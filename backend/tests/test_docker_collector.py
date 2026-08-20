"""Docker collector tests (Phase 09).

Fixture-driven via the injectable CLI runner. Covers: no engine, engine
available, container lists, port parsing, and the hard rule that container
ENV is never collected or serialized.
"""
import types

import pytest

from app.collectors.docker import DockerCollector, DockerState, _parse_ports


def _res(out, code=0, err=""):
    return types.SimpleNamespace(returncode=code, stdout=out.encode(), stderr=err.encode())


ENGINE_VERSION = '{"Client":{"Version":"29.5.3"},"Server":{"Version":"29.5.3"}}'
CLIENT_ONLY = ('{"Client":{"Version":"29.5.3","Context":"desktop-linux"},"Server":null}',
               0, "failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; "
                  "check if the path is correct and if the daemon is running: open "
                  "//./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.")


def _ps_line(cid="abc123def456", name="/postgres", image="postgres:16",
             state="running", status="Up 2 hours",
             ports="0.0.0.0:5433->5432/tcp, 127.0.0.1:8080->80/tcp",
             nets="bridge", created="2026-08-19 12:34:56 +0300 EEST"):
    return ('{"ID":"%s","Names":"%s","Image":"%s","Command":"postgres","CreatedAt":"%s",'
            '"Ports":"%s","Labels":{"com.example.token":"secret-label"},"State":"%s",'
            '"Status":"%s","Networks":"%s"}'
            % (cid, name, image, created, ports, state, status, nets))


class RecordingRunner:
    def __init__(self, responses):
        self.responses = responses  # list of (predicate, result) or callable
        self.calls = []

    def __call__(self, args, timeout=10.0):
        self.calls.append(list(args))
        for pred, result in self.responses:
            if callable(pred):
                if pred(args):
                    return result
            elif pred == args[0]:
                return result
        return _res("", 1, "unexpected call: " + " ".join(args))


def _engine_up_runner():
    return RecordingRunner([
        ("version", _res(ENGINE_VERSION)),
        ("ps", _res("\n".join([
            _ps_line(),
            _ps_line(cid="feed00000000", name="/redis", image="redis:7",
                     state="exited", status="Exited (0) 3 hours ago",
                     ports="6379/tcp", nets="bridge,esw-net",
                     created="2026-08-18 01:02:03 +0300 EEST"),
        ]))),
        ("inspect", _res("1234")),
    ])


def test_engine_not_running():
    r = RecordingRunner([
        ("version", _res(*CLIENT_ONLY)),
        ("ps", _res("", 1, "cannot connect to the Docker daemon")),
    ])
    st = DockerCollector(runner=r).collect()
    assert st.available is True
    assert st.engine_status == "NOT_RUNNING"
    assert st.containers == []
    assert st.version is None
    assert "engine not reachable" in st.error or "daemon" in st.error
    # no container query attempted when the engine is down
    assert all(a[0] != "ps" for a in r.calls)


def test_cli_missing():
    def boom(args, timeout=10.0):
        raise FileNotFoundError("docker")
    st = DockerCollector(runner=boom).collect()
    assert st.available is False
    assert st.engine_status == "UNKNOWN"
    assert "not found" in st.error


def test_engine_available_and_containers():
    r = _engine_up_runner()
    st = DockerCollector(runner=r).collect()
    assert st.engine_status == "RUNNING"
    assert st.version == "29.5.3"
    assert len(st.containers) == 2
    pg = next(c for c in st.containers if c.name == "postgres")
    assert pg.state == "running"
    assert pg.image == "postgres:16"
    assert pg.pid == 1234
    assert pg.ports == [
        {"host_ip": "0.0.0.0", "host_port": 5433, "container_port": 5432, "proto": "tcp"},
        {"host_ip": "127.0.0.1", "host_port": 8080, "container_port": 80, "proto": "tcp"},
    ]
    assert pg.networks == ["bridge"]
    assert pg.created is not None
    redis = next(c for c in st.containers if c.name == "redis")
    assert redis.state == "exited"
    assert redis.pid is None  # non-running -> no inspect
    assert redis.ports == [{"host_ip": None, "host_port": None,
                            "container_port": 6379, "proto": "tcp"}]
    assert redis.networks == ["bridge", "esw-net"]
    # one inspect call only, and it MUST be the targeted format (never full)
    inspects = [c for c in r.calls if c[0] == "inspect"]
    assert len(inspects) == 1
    assert "--format" in inspects[0]
    assert "{{.State.Pid}}" in inspects[0]


def test_no_environment_leakage():
    """ENV must never be collected or serialized — even when the CLI JSON
    would contain it (labels with token-like values are also skipped)."""
    malicious = ('{"ID":"badc0ffee000","Names":"/leaky","Image":"img",'
                 '"Config":{"Env":["POSTGRES_PASSWORD=hunter2","API_KEY=sk-123"]},'
                 '"Ports":"","Labels":{"com.example.token":"secret-label"},'
                 '"State":"running","Status":"Up","Networks":"bridge"}')
    r = RecordingRunner([
        ("version", _res(ENGINE_VERSION)),
        ("ps", _res(malicious)),
        ("inspect", _res("55")),
    ])
    st = DockerCollector(runner=r).collect()
    assert len(st.containers) == 1
    c = st.containers[0]
    d = c.to_dict()
    blob = repr(d).lower()
    assert "password" not in blob and "api_key" not in blob and "hunter2" not in blob
    assert "sk-123" not in blob and "secret-label" not in blob
    assert "env" not in d and "labels" not in d
    # full-inspect JSON (with Config.Env) is never requested
    assert all("--format" in call for call in r.calls if call[0] == "inspect")


def test_port_parser():
    assert _parse_ports("0.0.0.0:8080->80/tcp") == [
        {"host_ip": "0.0.0.0", "host_port": 8080, "container_port": 80, "proto": "tcp"}]
    assert _parse_ports("5432/tcp") == [
        {"host_ip": None, "host_port": None, "container_port": 5432, "proto": "tcp"}]
    assert _parse_ports("") == []
    assert _parse_ports("garbage") == []
    both = _parse_ports("127.0.0.1:5433->5432/tcp, 80/udp")
    assert len(both) == 2
    assert both[1]["proto"] == "udp"
    v6 = _parse_ports("[::1]:8080->80/tcp")
    assert v6[0]["host_ip"] == "::1"


def test_state_dict_shape():
    st = DockerState()
    d = st.to_dict()
    assert set(d) == {"available", "engine_status", "version", "containers",
                      "source", "error"}
