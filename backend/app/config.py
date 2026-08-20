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
    # ---- network telemetry -------------------------------------------------
    telemetry_enabled: bool = True
    telemetry_flush_ms: float = 200.0      # activity aggregation window
    telemetry_burst_bytes: int = 200_000   # per-edge per-window burst threshold
    telemetry_burst_cooldown_s: float = 10.0
    # ---- semantic detection (GPU + AI observability, v0.3.0) ---------------
    detectors_config_path: str = ""        # config/detectors.json (hints only)
    detector_interval_s: float = 3.0       # process classification cadence
    lm_studio_api_interval_s: float = 8.0  # local API probe cadence
    gpu_metrics_interval_s: float = 1.0    # overall GPU metrics
    gpu_pid_interval_s: float = 2.0        # GPU PID attribution
    gpu_enabled: bool = True
    # ---- infrastructure observability (v0.4.0) ----------------------------
    infra_services_interval_s: float = 4.0   # Windows services poll
    infra_wsl_interval_s: float = 5.0        # WSL distro state poll
    infra_docker_interval_s: float = 3.0     # Docker engine poll
    infra_vm_interval_s: float = 4.0         # VM poll

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
            telemetry_enabled=_env_bool("ESW_TELEMETRY_ENABLED", True),
            telemetry_flush_ms=float(os.environ.get("ESW_TELEMETRY_FLUSH_MS", "200")),
            telemetry_burst_bytes=int(os.environ.get("ESW_TELEMETRY_BURST_BYTES", "200000")),
            telemetry_burst_cooldown_s=float(os.environ.get("ESW_TELEMETRY_BURST_COOLDOWN_S", "10")),
            detectors_config_path=os.environ.get("ESW_DETECTORS_CONFIG", ""),
            detector_interval_s=float(os.environ.get("ESW_DETECTOR_INTERVAL_S", "3.0")),
            lm_studio_api_interval_s=float(os.environ.get("ESW_LM_STUDIO_API_INTERVAL_S", "8.0")),
            gpu_metrics_interval_s=float(os.environ.get("ESW_GPU_METRICS_INTERVAL_S", "1.0")),
            gpu_pid_interval_s=float(os.environ.get("ESW_GPU_PID_INTERVAL_S", "2.0")),
            gpu_enabled=_env_bool("ESW_GPU_ENABLED", True),
            infra_services_interval_s=float(os.environ.get("ESW_INFRA_SERVICES_INTERVAL_S", "4.0")),
            infra_wsl_interval_s=float(os.environ.get("ESW_INFRA_WSL_INTERVAL_S", "5.0")),
            infra_docker_interval_s=float(os.environ.get("ESW_INFRA_DOCKER_INTERVAL_S", "3.0")),
            infra_vm_interval_s=float(os.environ.get("ESW_INFRA_VM_INTERVAL_S", "4.0")),
        )
