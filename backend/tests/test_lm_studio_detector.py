"""LM Studio detector tests: confirmed detection, API failure,
false-positive rejection, loaded vs available distinction."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.detectors.base import CONFIDENCE_CONFIRMED, CONFIDENCE_HIGH
from app.detectors.lm_studio import LM_SEMANTIC_NODE, LmStudioDetector, model_node_id
from app.detectors.registry import DetectorContext
from app.services.topology import TopologyEngine

from conftest import make_conn, make_proc, make_snap

LM_EXE = r"C:\Users\xampos\AppData\Local\Programs\LM Studio\LM Studio.exe"


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeClient:
    """In-memory httpx client: /api/0/models (runtime) + /v1/models (served)."""

    def __init__(self, loaded=None, available=None, api_status=200, v1_status=200):
        self.loaded = loaded or []
        self.available = available or []
        self.api_status = api_status
        self.v1_status = v1_status
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        if "/api/0/models" in url:
            if self.api_status != 200:
                return FakeResponse(self.api_status)
            return FakeResponse(200, {"loaded_models": [
                {"id": m["id"], "model": m["id"]} for m in self.loaded
            ]})
        if "/v1/models" in url:
            if self.v1_status != 200:
                return FakeResponse(self.v1_status)
            return FakeResponse(200, {"data": [{"id": m["id"], "object": "model"} for m in self.available]})
        return FakeResponse(404)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fake_http(monkeypatch):
    def _install(client):
        monkeypatch.setattr("app.detectors.lm_studio.httpx.Client", lambda *a, **k: client)
        return client

    return _install


def _lm_snap(with_process=True):
    procs = []
    if with_process:
        procs.append(make_proc(4001, name="LM Studio.exe", exe=LM_EXE,
                               cmdline=[LM_EXE]))
    snap = make_snap(procs=procs)
    if with_process:
        sid = next(p for s, p in snap.processes.items() if p.pid == 4001).stable_id
        snap.connections = {
            make_conn(4001, lip="127.0.0.1", lport=1234).key: make_conn(
                4001, lip="127.0.0.1", lport=1234
            ),
        }
        snap.owner_map = {k: sid for k in snap.connections}
    return snap


def _run(detector, snap):
    topo = TopologyEngine(Settings()).build(snap)
    ctx = DetectorContext(snap=snap, topo=topo)
    return detector.detect(ctx)


def test_confirmed_detection_with_api(fake_http):
    client = fake_http(FakeClient(
        loaded=[{"id": "qwen3-4b"}],
        available=[{"id": "qwen3-4b"}, {"id": "llama-3.1-8b"}],
    ))
    det = LmStudioDetector(Settings())
    detections, rels = _run(det, _lm_snap())
    assert len(detections) == 1
    d = detections[0]
    assert d.semantic_type == "LM_STUDIO"
    assert d.confidence == CONFIDENCE_CONFIRMED
    assert d.metadata["endpoint"] == "http://127.0.0.1:1234"
    states = {m["id"]: m["state"] for m in d.metadata["models"]}
    assert states["qwen3-4b"] == "LOADED"      # runtime-proven
    assert states["llama-3.1-8b"] == "AVAILABLE"  # only servable
    # LOCAL_API + SERVES_MODEL edges
    kinds = {r.kind for r in rels}
    assert "LOCAL_API" in kinds
    assert "SERVES_MODEL" in kinds
    assert any(r.kind == "SERVES_MODEL" and r.target == model_node_id("qwen3-4b")
               for r in rels)
    # probes were loopback-only
    assert all("127.0.0.1" in u for u in client.calls)


def test_api_failure_keeps_high_confidence(fake_http):
    fake_http(FakeClient(api_status=500, v1_status=500))
    det = LmStudioDetector(Settings())
    detections, _ = _run(det, _lm_snap())
    assert len(detections) == 1
    d = detections[0]
    # process identity (path) holds; API unreachable degrades only the API part
    assert d.confidence == CONFIDENCE_CONFIRMED
    assert d.metadata["api_available"] is False
    assert "endpoint" in d.metadata


def test_api_timeout_does_not_crash(fake_http):
    class BoomClient:
        def get(self, url):
            raise TimeoutError("connect timed out")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_http(BoomClient())
    det = LmStudioDetector(Settings())
    detections, _ = _run(det, _lm_snap())
    assert len(detections) == 1
    assert detections[0].metadata["api_available"] is False


def test_false_positive_rejected():
    """A random python process with no LM Studio evidence is NOT detected —
    even if it happens to listen on the hint port."""
    snap = make_snap(procs=[
        make_proc(7001, name="python.exe", exe=r"C:\bin\python.exe",
                  cmdline=["python.exe", "-m", "flask", "run"]),
    ])
    snap.connections = {
        make_conn(7001, lip="127.0.0.1", lport=1234).key: make_conn(
            7001, lip="127.0.0.1", lport=1234
        ),
    }
    snap.owner_map = {k: next(p for s, p in snap.processes.items() if p.pid == 7001).stable_id
                      for k in snap.connections}
    det = LmStudioDetector(Settings())
    detections, _ = _run(det, snap)
    assert detections == []


def test_loaded_vs_available_distinction(fake_http):
    """/v1/models alone can NEVER claim LOADED."""
    fake_http(FakeClient(loaded=[], available=[{"id": "mistral-7b"}]))
    det = LmStudioDetector(Settings())
    detections, _ = _run(det, _lm_snap())
    models = detections[0].metadata["models"]
    assert models == [{"id": "mistral-7b", "state": "AVAILABLE"}]


def test_no_running_process_no_probe(fake_http):
    """No LM Studio process -> no detection AND no API probing at all."""
    client = fake_http(FakeClient(loaded=[], available=[]))
    det = LmStudioDetector(Settings())
    detections, _ = _run(det, _lm_snap(with_process=False))
    assert detections == []
    assert client.calls == []
