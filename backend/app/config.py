"""Central configuration. All values are environment-overridable (ESW_*)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    poll_interval: float = 1.0            # collector tick (s)
    demo_mode: bool = False               # synthetic data ONLY when explicitly enabled
    max_external_nodes: int = 120         # cap on distinct external IP nodes
    max_listen_nodes: int = 40            # cap on distinct listening-port nodes
    max_loc_nodes: int = 40               # cap on unpaired localhost endpoint nodes
    max_process_events_per_tick: int = 60
    metrics_cpu_delta: float = 2.0        # % change that triggers a metrics event
    metrics_mem_delta_mb: float = 16.0
    metrics_force_interval_s: float = 10.0
    ws_heartbeat_s: float = 15.0
    max_event_batch: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.environ.get("ESW_HOST", "127.0.0.1"),
            port=int(os.environ.get("ESW_PORT", "8765")),
            poll_interval=float(os.environ.get("ESW_POLL_INTERVAL", "1.0")),
            demo_mode=_env_bool("ESW_DEMO_MODE", False),
            max_external_nodes=int(os.environ.get("ESW_MAX_EXTERNAL_NODES", "120")),
            max_listen_nodes=int(os.environ.get("ESW_MAX_LISTEN_NODES", "40")),
            max_loc_nodes=int(os.environ.get("ESW_MAX_LOC_NODES", "40")),
        )
