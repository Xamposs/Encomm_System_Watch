"""TEST-ONLY benchmark graph fixture tests (v0.3.1).

The fixture itself is synthetic validation data — these tests verify it is
deterministic, structurally valid, explicitly labeled TEST/BENCHMARK, and
that benchmark mode can never leak into the normal (real) data path.
"""
from app.services.benchmark_graph import (
    BenchmarkMode,
    generate_benchmark_graph,
    validate_graph,
)

SIZES = [500, 1000, 1500, 2000]


def test_generates_at_least_requested_node_count():
    for n in SIZES:
        g = generate_benchmark_graph(n)
        assert len(g["nodes"]) >= n, f"{n} requested, {len(g['nodes'])} got"
        assert g["meta"]["label"] == "TEST/BENCHMARK (synthetic)"
        assert g["meta"]["test_only"] is True


def test_deterministic_same_seed():
    a = generate_benchmark_graph(1000, seed=42)
    b = generate_benchmark_graph(1000, seed=42)
    assert a["nodes"] == b["nodes"]
    assert a["edges"] == b["edges"]


def test_default_seed_derived_from_node_count():
    a = generate_benchmark_graph(1000)
    b = generate_benchmark_graph(1000)
    assert a["nodes"] == b["nodes"]
    assert a["meta"]["seed"] == 1000 * 7919


def test_all_elements_labeled_test_only():
    for n in [500, 1500]:
        g = generate_benchmark_graph(n)
        assert all(x["data"].get("test_only") for x in g["nodes"])
        assert all(x["data"].get("benchmark") for x in g["nodes"])
        assert all(x.get("test_only") for x in g["edges"])


def test_no_real_identity_data():
    g = generate_benchmark_graph(1000)
    for n in g["nodes"]:
        d = n["data"]
        if d.get("pid") is not None:
            assert d["pid"] >= 400_000, f"pid {d['pid']} looks real"
        # endpoints/semantic nodes carry no username; process nodes must be
        # clearly synthetic
        assert d.get("username") in (None, "SYNTHETIC\\bench")
        if n["kind"] == "PROCESS":
            assert "Bench" in str(d.get("exe") or "") or "--benchmark-test-only" in " ".join(d.get("cmdline") or [])


def test_realistic_kind_mix():
    g = generate_benchmark_graph(1000)
    kinds = {n["kind"] for n in g["nodes"]}
    for expected in ("PROCESS", "EXTERNAL_ENDPOINT", "LISTENING_PORT",
                     "SEMANTIC", "GPU", "LOCAL_LLM"):
        assert expected in kinds, f"missing kind {expected}"
    proc = sum(1 for n in g["nodes"] if n["kind"] == "PROCESS")
    assert proc > len(g["nodes"]) * 0.5
    ekind = {e["kind"] for e in g["edges"]}
    for expected in ("LOCALHOST", "EXTERNAL", "PROCESS_PARENT", "USES_GPU",
                     "MEMBER_OF", "SERVES_MODEL", "SPAWNED", "HOSTS", "LISTEN"):
        assert expected in ekind, f"missing edge kind {expected}"


def test_edges_reference_existing_nodes_and_unique_ids():
    for n in [500, 2000]:
        g = generate_benchmark_graph(n)
        problems = validate_graph(g["nodes"], g["edges"])
        assert problems == [], f"validation problems: {problems[:5]}"


def test_edge_count_proportionate():
    for n in [500, 1000, 1500]:
        g = generate_benchmark_graph(n)
        ratio = len(g["edges"]) / len(g["nodes"])
        assert 0.5 <= ratio <= 3.0, f"edge ratio {ratio:.2f} at {n} nodes"


def test_families_present():
    """At least one process family where parent + >=2 children share the
    same name (the exact contract the frontend familyOf() uses)."""
    g = generate_benchmark_graph(1500)
    by_parent: dict[str, list[str]] = {}
    name_of: dict[str, str] = {}
    for n in g["nodes"]:
        name_of[n["id"]] = n["data"].get("name") or ""
        p = n["data"].get("parent_sid")
        if p:
            by_parent.setdefault(p, []).append(n["id"])
    fam = [
        k for k, v in by_parent.items()
        if len(v) >= 2 and all(name_of.get(c) == name_of.get(k) for c in v)
    ]
    assert len(fam) >= 1, "expected at least one name-matched process family"


def test_benchmark_mode_inactive_by_default():
    bm = BenchmarkMode()
    assert bm.active is False
    assert bm.snapshot() is None
    st = bm.status()
    assert st["active"] is False and st["node_count"] == 0


def test_benchmark_mode_activate_deactivate():
    bm = BenchmarkMode()
    st = bm.activate(500, seed=7, now=100.0)
    assert bm.active is True
    assert st["active"] is True
    assert st["node_count"] >= 500
    assert st["label"] == "TEST/BENCHMARK (synthetic)"
    snap = bm.snapshot()
    assert len(snap["nodes"]) >= 500
    assert all(n["data"]["test_only"] for n in snap["nodes"])
    # deactivate returns the POST-deactivation status (inactive)
    deact = bm.deactivate()
    assert deact["active"] is False and deact["node_count"] == 0
    assert bm.active is False
    assert bm.snapshot() is None


def test_benchmark_mode_rejects_bad_sizes():
    bm = BenchmarkMode()
    try:
        bm.activate(5)
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        bm.activate(6000)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_benchmark_snapshot_matches_generated_fixture():
    bm = BenchmarkMode()
    bm.activate(1000, seed=11, now=1.0)
    snap = bm.snapshot()
    assert snap["meta"]["node_count"] == len(snap["nodes"])
    assert snap["meta"]["edge_count"] == len(snap["edges"])
    assert snap["meta"]["seed"] == 11
    assert validate_graph(snap["nodes"], snap["edges"]) == []


def test_benchmark_api_gate_and_flow(client):
    """The API: header-gated activation, labeled snapshot, clean return."""
    # without the header: refused
    r = client.post("/api/benchmark/activate", json={"nodes": 500})
    assert r.status_code == 200
    body = r.json()
    assert "error" in body and "header" in body["error"]
    # status: inactive by default
    st = client.get("/api/benchmark/status").json()
    assert st["active"] is False
    # with the header: active, snapshot is synthetic
    r = client.post(
        "/api/benchmark/activate", json={"nodes": 500, "seed": 3},
        headers={"X-ESW-Benchmark": "test-only"},
    )
    assert r.json()["active"] is True
    snap = client.get("/api/state").json()
    assert snap["mode"] == "benchmark"
    assert len(snap["nodes"]) >= 500
    assert all(n["data"].get("test_only") for n in snap["nodes"])
    assert all(e.get("test_only") for e in snap["edges"])
    # deactivate: real mode restored
    client.post("/api/benchmark/deactivate")
    snap2 = client.get("/api/state").json()
    assert snap2["mode"] != "benchmark"
