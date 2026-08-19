"""TCB correlation tests for the MODERN Windows TCPIP ETW schema.

These protect the empirical Windows 11 manifest contract discovered via
probe_etw*.py: TcpDataTransferSend/Receive carry ONLY (Tcb, bytes) while
connection identity arrives in separate lifecycle events:

    TcpConnectionRundown / TcpConnectTcbComplete / TcpAcceptListenerComplete
        -> Tcb -> (pid, local, remote)
    TcpDataTransferSend / TcpDataTransferReceive
        -> Tcb lookup -> NetworkActivityEvent

Removal: TcpDisconnectTcbComplete / TcpCloseTcbRequest / TcpAbortTcbComplete
/ TcpConnectTcbFailure / TcpConnectTcbFailedRcvRst /
TcpConnectionTerminatedRcvRst (by Tcb), TcpRstSend (by sockaddr tuple),
TcpTcbStateChange(CLOSED), plus a 5-minute TTL and an entry cap.
"""
import time

from app.telemetry.base import Capability, NetworkActivityEvent
from app.telemetry.windows_network import (
    TCB_MAP_MAX_ENTRIES,
    EtwTcpipProvider,
    _normalize_tcb,
    _parse_endpoint,
)


def _tcb(hex_n=0xFFFF8E0F2627B8A0):
    return f"0x{hex_n:016X}"


# ------------------------------------------------------------- normalization

def test_normalize_tcb_formats():
    assert _normalize_tcb("0xFFFF8E0F2627B8A0") == 0xFFFF8E0F2627B8A0
    assert _normalize_tcb("0xffff8e0f2627b8a0") == 0xFFFF8E0F2627B8A0
    assert _normalize_tcb("123456") == 123456
    assert _normalize_tcb(0xFFFF8E0F2627B8A0) == 0xFFFF8E0F2627B8A0
    assert _normalize_tcb(b"0xFFFF8E0F2627B8A0") == 0xFFFF8E0F2627B8A0


def test_normalize_tcb_invalid():
    assert _normalize_tcb(None) is None
    assert _normalize_tcb("0x0") is None          # null TCB (e.g. TcpRstSend)
    assert _normalize_tcb(0) is None
    assert _normalize_tcb(-5) is None
    assert _normalize_tcb("garbage") is None
    assert _normalize_tcb("") is None
    assert _normalize_tcb(b"\x00\xff") is None
    assert _normalize_tcb(12.5) is None


def test_parse_endpoint_v4_and_v6():
    assert _parse_endpoint("127.0.0.1:62960") == ("127.0.0.1", 62960)
    assert _parse_endpoint("[2606:4700:110:85fa:61e5:7be2:6ab6:c760]:63690") == (
        "2606:4700:110:85fa:61e5:7be2:6ab6:c760", 63690)
    assert _parse_endpoint("[::1]:19735") == ("::1", 19735)


def test_parse_endpoint_invalid():
    assert _parse_endpoint(None) is None
    assert _parse_endpoint("") is None
    assert _parse_endpoint("0.0.0.0:0") is None     # port-0 wildcard
    assert _parse_endpoint("127.0.0.1") is None     # no port
    assert _parse_endpoint("::1") is None           # bare v6, no port
    assert _parse_endpoint("junk") is None
    assert _parse_endpoint("127.0.0.1:notaport") is None
    assert _parse_endpoint(1234) is None


# ------------------------------------------------------------- event helpers

def _conn(task, tcb, pid, lip, lport, rip, rport, pid_field="ProcessId"):
    return (0, {
        "Task Name": task, "Tcb": _tcb(tcb),
        pid_field: str(pid),
        "LocalAddress": f"{lip}:{lport}", "RemoteAddress": f"{rip}:{rport}",
    })


def _xfer(task, tcb, size):
    field = "BytesSent" if task == "TCPDATATRANSFERSEND" else "NumBytes"
    return (0, {"Task Name": task, "Tcb": _tcb(tcb), field: str(size)})


def _setup_map(*events):
    prov = EtwTcpipProvider()
    for ev in events:
        prov._on_event(ev)
    return prov


# ----------------------------------------------- mapping creation (identity)

def test_rundown_creates_mapping_send_resolves_out():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0xAA, 1234, "192.168.1.5", 51000,
              "104.18.22.44", 443, pid_field="Pid"),
        _xfer("TCPDATATRANSFERSEND", 0xAA, 4096),
    )
    evs = prov.drain()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.pid == 1234
    assert ev.direction == "OUT"
    assert (ev.local_ip, ev.local_port) == ("192.168.1.5", 51000)
    assert (ev.remote_ip, ev.remote_port) == ("104.18.22.44", 443)
    assert ev.size == 4096
    assert ev.protocol == "tcp"


def test_connect_complete_mapping_receive_resolves_in():
    prov = _setup_map(
        _conn("TCPCONNECTTCBCOMPLETE", 0xBB, 777, "10.0.0.2", 33000,
              "10.0.0.1", 5432),
        _xfer("TCPDATATRANSFERRECEIVE", 0xBB, 2048),
    )
    evs = prov.drain()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.direction == "IN"
    assert ev.pid == 777
    assert (ev.local_ip, ev.local_port) == ("10.0.0.2", 33000)
    assert (ev.remote_ip, ev.remote_port) == ("10.0.0.1", 5432)
    assert ev.size == 2048


def test_accept_listener_complete_maps_server_side():
    prov = _setup_map(
        _conn("TCPACCEPTLISTENERCOMPLETE", 0xCC, 200, "127.0.0.1", 19735,
              "127.0.0.1", 53121),
        _xfer("TCPDATATRANSFERSEND", 0xCC, 8192),   # server echoes -> OUT
        _xfer("TCPDATATRANSFERRECEIVE", 0xCC, 4096),  # server receives -> IN
    )
    evs = prov.drain()
    assert len(evs) == 2
    assert [(e.direction, e.size) for e in evs] == [("OUT", 8192), ("IN", 4096)]
    assert all(e.pid == 200 for e in evs)


def test_proceeding_with_pid_zero_skipped_complete_creates():
    prov = _setup_map(
        _conn("TCPCONNECTTCBPROCEEDING", 0xDD, 0, "127.0.0.1", 1111,
              "127.0.0.1", 2222),
    )
    assert prov.drain() == []
    assert prov.counters()["tcb_map_size"] == 0
    prov._on_event(_conn("TCPCONNECTTCBCOMPLETE", 0xDD, 999, "127.0.0.1", 1111,
                         "127.0.0.1", 2222))
    prov._on_event(_xfer("TCPDATATRANSFERSEND", 0xDD, 100))
    evs = prov.drain()
    assert len(evs) == 1 and evs[0].pid == 999


def test_udp_events_attributed_directly():
    prov = EtwTcpipProvider()
    prov._on_event((0, {
        "Task Name": "UDPENDPOINTSENDMESSAGES", "Pid": "42",
        "LocalSockAddr": "0.0.0.0:65106", "RemoteSockAddr": "127.0.0.1:19740",
        "NumBytes": "500", "NumMessages": "1",
    }))
    prov._on_event((0, {
        "Task Name": "UDPENDPOINTRECEIVEMESSAGES", "Pid": "42",
        "LocalSockAddr": "127.0.0.1:19740", "RemoteSockAddr": "192.168.194.1:137",
        "NumBytes": "50", "NumMessages": "1",
    }))
    evs = prov.drain()
    assert len(evs) == 2
    assert evs[0].direction == "OUT" and evs[0].protocol == "udp"
    assert evs[1].direction == "IN" and evs[1].size == 50
    assert evs[1].local_port == 19740


# --------------------------------------------------------- misses and safety

def test_unknown_tcb_is_miss_not_event():
    prov = _setup_map(_xfer("TCPDATATRANSFERSEND", 0xEE, 4096))
    assert prov.drain() == []
    cnt = prov.counters()
    assert cnt["tcb_lookup_misses"] == 1
    assert cnt["tcb_lookup_hits"] == 0


def test_zero_size_transfer_hit_but_no_event():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0xFF, 1, "127.0.0.1", 1, "127.0.0.1", 2,
              pid_field="Pid"),
        _xfer("TCPDATATRANSFERSEND", 0xFF, 0),   # ACK-only / zero bytes
    )
    assert prov.drain() == []
    assert prov.counters()["tcb_lookup_hits"] == 1


def test_disconnect_removes_mapping_prevents_reuse():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0x11, 100, "127.0.0.1", 5000,
              "127.0.0.1", 6000, pid_field="Pid"),
        _conn("TCPDISCONNECTTCBCOMPLETE", 0x11, 0, "127.0.0.1", 5000,
              "127.0.0.1", 6000),
        _xfer("TCPDATATRANSFERSEND", 0x11, 512),  # stale: must NOT attribute
    )
    assert prov.drain() == []
    cnt = prov.counters()
    assert cnt["tcb_mappings_created"] == 1
    assert cnt["tcb_mappings_removed"] == 1
    assert cnt["tcb_map_size"] == 0
    assert cnt["tcb_lookup_misses"] == 1


def test_close_request_and_abort_remove():
    for task in ("TCPCLOSETCBREQUEST", "TCPABORTTCBCOMPLETE"):
        prov = _setup_map(
            _conn("TCPCONNECTIONRUNDOWN", 0x22, 100, "127.0.0.1", 5000,
                  "127.0.0.1", 6000, pid_field="Pid"),
            _conn(task, 0x22, 0, "127.0.0.1", 5000, "127.0.0.1", 6000),
        )
        assert prov.drain() == []
        assert prov.counters()["tcb_map_size"] == 0


def test_connect_failure_events_remove_mapping():
    for task in ("TCPCONNECTTCBFAILURE", "TCPCONNECTTCBFAILEDRCVDRST",
                 "TCPCONNECTIONTERMINATEDRCVDRST"):
        prov = _setup_map(
            _conn("TCPCONNECTTCBPROCEEDING", 0x33, 100, "127.0.0.1", 5000,
                  "127.0.0.1", 6000),
            _conn(task, 0x33, 100, "127.0.0.1", 5000, "127.0.0.1", 6000),
        )
        assert prov.drain() == []
        assert prov.counters()["tcb_map_size"] == 0


def test_rst_send_removes_by_tuple():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0x44, 100, "127.0.0.1", 5000,
              "127.0.0.1", 6000, pid_field="Pid"),
    )
    prov._on_event((0, {
        "Task Name": "TCPRSTSEND", "Tcb": "0x0",
        "LocalSockAddr": "127.0.0.1:5000", "RemoteSockAddr": "127.0.0.1:6000",
    }))
    assert prov.counters()["tcb_map_size"] == 0
    assert prov.counters()["tcb_mappings_removed"] == 1


def test_state_change_closed_removes_other_states_keep():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0x55, 100, "127.0.0.1", 5000,
              "127.0.0.1", 6000, pid_field="Pid"),
    )
    prov._on_event((0, {"Task Name": "TCPTCBSTATECHANGE", "Tcb": _tcb(0x55),
                        "OldState": "EstablishedState", "NewState": "FinWait1State "}))
    assert prov.counters()["tcb_map_size"] == 1   # transient state: keep
    prov._on_event((0, {"Task Name": "TCPTCBSTATECHANGE", "Tcb": _tcb(0x55),
                        "OldState": "FinWait1State", "NewState": "ClosedState "}))
    assert prov.counters()["tcb_map_size"] == 0   # final state: removed


def test_tcb_reuse_new_identity_wins():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0x66, 100, "127.0.0.1", 5000,
              "127.0.0.1", 6000, pid_field="Pid"),
    )
    # same Tcb re-learned for a NEW connection (different tuple/pid)
    prov._on_event(_conn("TCPACCEPTLISTENERCOMPLETE", 0x66, 200,
                         "127.0.0.1", 7000, "127.0.0.1", 8000))
    prov._on_event(_xfer("TCPDATATRANSFERSEND", 0x66, 999))
    evs = prov.drain()
    assert len(evs) == 1
    assert evs[0].pid == 200
    assert (evs[0].local_ip, evs[0].local_port) == ("127.0.0.1", 7000)
    assert prov.counters()["tcb_mappings_created"] == 1  # replaced, not re-created


def test_ttl_cleanup_drops_stale_mappings():
    prov = EtwTcpipProvider()
    prov._on_event(_conn("TCPCONNECTIONRUNDOWN", 0x77, 100, "127.0.0.1", 5000,
                         "127.0.0.1", 6000, pid_field="Pid"))
    # backdate the entry past the TTL
    with prov._lock:
        prov._tcb_map[next(iter(prov._tcb_map))].last_seen -= 301.0
    prov.drain()
    cnt = prov.counters()
    assert cnt["tcb_map_size"] == 0
    assert cnt["tcb_mappings_removed"] == 1


def test_map_cap_evicts_oldest(monkeypatch):
    monkeypatch.setattr("app.telemetry.windows_network.TCB_MAP_MAX_ENTRIES", 50)
    prov = EtwTcpipProvider()
    for i in range(60):
        prov._on_event(_conn("TCPCONNECTIONRUNDOWN", 0x1000 + i, 100,
                             "127.0.0.1", 5000 + i, "127.0.0.1", 6000,
                             pid_field="Pid"))
    cnt = prov.counters()
    assert cnt["tcb_map_size"] == 50          # hard cap enforced
    assert cnt["tcb_mappings_created"] == 60
    assert cnt["tcb_mappings_removed"] == 10  # oldest evicted
    prov._on_event(_xfer("TCPDATATRANSFERSEND", 0x1000, 10))  # evicted tcb
    assert prov.counters()["tcb_lookup_misses"] == 1


def test_malformed_events_never_crash():
    prov = EtwTcpipProvider()
    bad = [
        None,
        (0, None),
        (0, {"Task Name": "TCPDATATRANSFERSEND"}),                       # no Tcb
        (0, {"Task Name": "TCPDATATRANSFERSEND", "Tcb": "0x0"}),         # null Tcb
        (0, {"Task Name": "TCPDATATRANSFERSEND", "Tcb": "junk"}),        # bad Tcb
        (0, {"Task Name": "TCPDATATRANSFERSEND", "Tcb": _tcb(0x88),
             "BytesSent": "garbage"}),                                   # bad size
        (0, {"Task Name": "TCPCONNECTIONRUNDOWN", "Tcb": _tcb(0x88),
             "Pid": "abc"}),                                             # bad pid
        (0, {"Task Name": "TCPCONNECTIONRUNDOWN", "Tcb": _tcb(0x88),
             "Pid": "100", "LocalAddress": "nonsense"}),                 # bad addr
        (0, {"Task Name": "TCPCONNECTIONRUNDOWN", "Tcb": _tcb(0x88),
             "Pid": "100", "LocalAddress": "127.0.0.1:0",
             "RemoteAddress": "127.0.0.1:6000"}),                        # port 0
        (0, {"Task Name": "TCPDISCONNECTTCBCOMPLETE"}),                  # no Tcb
        (0, {"Task Name": "UDPENDPOINTSENDMESSAGES", "Pid": "0"}),       # pid 0
        (0, {"Task Name": "TCPTCBSTATECHANGE", "Tcb": "0x0"}),
        (0, {"Task Name": "TCPRSTSEND"}),                                # no addrs
        (0, {"Task Name": "UNKNOWNTASK", "Tcb": _tcb(0x88)}),            # ignored
        "garbage",
        (0, 42),
    ]
    for ev in bad:
        prov._on_event(ev)
    assert prov.drain() == []
    assert prov.counters()["events_dropped"] == 0


# ---------------------------------------------------------------- counters

def test_tcb_counters_exposed():
    prov = _setup_map(
        _conn("TCPCONNECTIONRUNDOWN", 0x99, 100, "127.0.0.1", 5000,
              "127.0.0.1", 6000, pid_field="Pid"),
        _xfer("TCPDATATRANSFERSEND", 0x99, 100),
        _xfer("TCPDATATRANSFERRECEIVE", 0xAA, 100),   # unknown TCB
    )
    prov.drain()
    cnt = prov.counters()
    assert cnt["events_received"] == 3
    assert cnt["tcb_mappings_created"] == 1
    assert cnt["tcb_map_size"] == 1
    assert cnt["tcb_lookup_hits"] == 1
    assert cnt["tcb_lookup_misses"] == 1
    assert cnt["events_drained"] == 1


def test_readiness_transitions():
    prov = EtwTcpipProvider()
    assert prov.capability().readiness == "NONE"
    prov._on_event(_xfer("TCPDATATRANSFERSEND", 0xAA, 100))
    # data without identity events stays INITIALIZING-capable but produces
    # nothing; readiness only becomes ACTIVE on an ATTRIBUTED data event
    assert prov.capability().readiness == "NONE"
    prov._on_event(_conn("TCPCONNECTTCBCOMPLETE", 0xAB, 100,
                         "127.0.0.1", 5000, "127.0.0.1", 6000))
    prov._on_event(_xfer("TCPDATATRANSFERSEND", 0xAB, 100))
    assert prov.capability().readiness == "ACTIVE"
    prov.mark_degraded()
    cap = prov.capability()
    assert cap.level == "TIER0" and cap.readiness == "DEGRADED"
    assert "ETW session ended" in cap.detail
