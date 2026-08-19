"""Security tests: command-line secret redaction.

Credential values must never reach the frontend, the event stream, or the
logs. Redaction preserves the flag names (they are not secrets) and only
replaces the value shapes.
"""
from __future__ import annotations

import pytest

from app.detectors.redact import redact_cmdline, safe_cmdline


def test_flag_with_separate_value():
    out = redact_cmdline(["python.exe", "--api-key", "sk-abc123", "run.py"])
    assert out == ["python.exe", "--api-key", "***", "run.py"]


def test_flag_with_equals_value():
    out = redact_cmdline(["server", "--token=SECRETVALUE", "--port", "8000"])
    assert out == ["server", "--token=***", "--port", "8000"]


def test_variants_redacted():
    cases = [
        ["app", "--api_key", "sk-live-1"],
        ["app", "--access-token", "sk-live-2"],
        ["app", "--access_token=sk-live-3"],
        ["app", "--client-secret", "sk-live-4"],
        ["app", "--password", "hunter2-secret"],
        ["app", "--passwd", "pwd-x-value"],
        ["app", "--auth-token", "tok-y-value"],
        ["app", "--session-key", "tok-z-value"],
    ]
    for args in cases:
        out = redact_cmdline(args)
        joined = " ".join(out)
        assert "sk-live-" not in joined
        assert "hunter2-secret" not in joined
        assert "pwd-x-value" not in joined
        assert "tok-y-value" not in joined
        assert "tok-z-value" not in joined
        assert "***" in joined


def test_inline_key_value_redacted():
    out = redact_cmdline(["tool", "--config=token=abc123&user=bob"])
    joined = " ".join(out)
    assert "abc123" not in joined
    assert "***" in joined
    assert "bob" in joined  # non-secret values stay


def test_authorization_bearer_redacted():
    out = redact_cmdline(["curl", "-H", "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", "url"])
    joined = " ".join(out)
    assert "eyJhbGciOiJIUzI1NiJ9" not in joined
    assert "Bearer ***" in joined


def test_normal_args_untouched():
    args = ["node", "server.js", "--port", "8080", "--host", "127.0.0.1",
            "model.gguf", "--ngl", "24"]
    assert redact_cmdline(args) == args


def test_input_never_mutated():
    args = ["app", "--api-key", "secret", "--keep", "value"]
    redact_cmdline(args)
    assert args == ["app", "--api-key", "secret", "--keep", "value"]


def test_empty_and_none():
    assert redact_cmdline([]) == []
    assert redact_cmdline(None) == []


def test_safe_cmdline_alias():
    assert safe_cmdline(["a", "--token", "t"]) == ["a", "--token", "***"]


def test_topology_serializes_redacted_cmdline():
    """The raw collector keeps cmdline; the outward node data is redacted."""
    from app.config import Settings
    from app.services.topology import TopologyEngine
    from conftest import make_proc, make_snap

    snap = make_snap(procs=[
        make_proc(1, name="agent.exe", cmdline=["agent.exe", "--api-key", "sk-live"]),
    ])
    topo = TopologyEngine(Settings()).build(snap)
    node = next(n for n in topo.nodes.values() if n.kind == "PROCESS")
    assert node.data["cmdline"] == ["agent.exe", "--api-key", "***"]
    assert "sk-live" not in str(node.to_dict())
