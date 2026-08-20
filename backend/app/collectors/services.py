"""Windows Service discovery — READ-ONLY (Phase 08).

Source: psutil ``win_service_iter`` / ``win_service_get`` (no control APIs are
ever called — no start/stop/restart). Every service degrades independently:
a service that cannot be inspected (AccessDenied, vanished mid-enumeration)
is reported as ``inaccessible`` with its name only — never fabricated.

Field availability is truthful: ``pid`` is exposed only when the service is
running AND Windows/psutil actually returns one; ``binpath``/``account`` are
omitted when access is denied. Metadata (display name, start type, account,
binary path, description) is expensive to fetch per service, so it is
TTL-cached; status + PID refresh on every poll (3-5 s).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("esw")

METADATA_TTL_S = 30.0

# psutil start_type values -> compact label used in the UI
START_TYPE_LABELS = {
    "automatic": "Auto",
    "automatic delayed": "AutoDelay",
    "manual": "Manual",
    "disabled": "Disabled",
    "boot": "Boot",
    "system": "System",
    "unknown": "Unknown",
}


@dataclass
class ServiceInfo:
    name: str
    display_name: str = ""
    status: str = "unknown"          # running | stopped | paused | unknown
    start_type: str = ""             # compact label (Auto / Manual / ...)
    account: str = ""
    binpath: str = ""
    description: str = ""
    pid: Optional[int] = None        # None = not running / not exposed
    inaccessible: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "display_name": self.display_name,
            "status": self.status,
            "start_type": self.start_type,
            "inaccessible": self.inaccessible,
        }
        if self.account:
            d["account"] = self.account
        if self.binpath:
            d["binpath"] = self.binpath
        if self.description:
            d["description"] = self.description
        if self.pid is not None:
            d["pid"] = self.pid
        if self.error:
            d["error"] = self.error
        return d


class ServicesCollector:
    """Enumerate Windows services via psutil (read-only).

    ``collect()`` returns ``(services: list[ServiceInfo], skipped: int)``.
    """

    def __init__(self, metadata_ttl_s: float = METADATA_TTL_S) -> None:
        self._metadata_ttl_s = metadata_ttl_s
        # name -> (fetch_time, metadata fields) — metadata cache
        self._meta_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._psutil_available = hasattr(__import__("psutil"), "win_service_iter")

    # ------------------------------------------------------------- internals

    def _metadata(self, svc) -> dict[str, str]:
        """TTL-cached heavy metadata for one psutil service object.

        Per-field try/except: an AccessDenied on binpath must not lose the
        display name. Unavailable fields are simply omitted.
        """
        name = svc.name()
        now = time.time()
        hit = self._meta_cache.get(name)
        if hit and now - hit[0] < self._metadata_ttl_s:
            return hit[1]

        out: dict[str, str] = {}
        for key, attr in (
            ("display_name", "display_name"),
            ("start_type", "start_type"),
            ("account", "username"),
            ("binpath", "binpath"),
            ("description", "description"),
        ):
            try:
                v = getattr(svc, attr)()
                if v:
                    out[key] = str(v)
            except Exception:  # noqa: BLE001 — per-field degradation
                continue
        self._meta_cache[name] = (now, out)
        return out

    def _one(self, svc) -> ServiceInfo:
        name = svc.name()
        meta = self._metadata(svc)
        info = ServiceInfo(
            name=name,
            display_name=meta.get("display_name", ""),
            start_type=START_TYPE_LABELS.get(
                str(meta.get("start_type", "")).lower(), meta.get("start_type", "")
            ),
            account=meta.get("account", ""),
            binpath=meta.get("binpath", ""),
            description=meta.get("description", ""),
        )
        try:
            status = svc.status()
            info.status = status if status in ("running", "stopped", "paused") else str(status)
        except Exception:  # noqa: BLE001 — a service whose state cannot be
            # read is genuinely inaccessible: mark it, keep the name
            info.status = "unknown"
            info.inaccessible = True
            info.error = "status access denied"
        try:
            pid = svc.pid()
            info.pid = pid if pid and pid > 0 else None
        except Exception:  # noqa: BLE001 — pid may be denied
            info.pid = None
        return info

    # -------------------------------------------------------------- public

    def collect(self) -> tuple[list[ServiceInfo], int]:
        """Enumerate all services. Returns (services, skipped).

        ``skipped`` counts services that could not be enumerated at all
        (AccessDenied on iteration, vanished mid-run). Never raises.
        """
        if not self._psutil_available:
            return [], 0
        import psutil

        services: list[ServiceInfo] = []
        skipped = 0
        try:
            it = psutil.win_service_iter()
        except Exception as exc:  # noqa: BLE001 — total failure degrades to empty
            log.warning("win_service_iter failed: %s", exc)
            return [], 1
        for svc in it:
            try:
                services.append(self._one(svc))
            except psutil.AccessDenied:
                skipped += 1
                services.append(ServiceInfo(
                    name=svc.name(), inaccessible=True, error="access denied",
                ))
            except psutil.NoSuchProcess:
                skipped += 1
            except Exception:  # noqa: BLE001 — per-service isolation
                skipped += 1
                try:
                    services.append(ServiceInfo(
                        name=svc.name(), inaccessible=True, error="inspect failed",
                    ))
                except Exception:  # noqa: BLE001
                    pass
        return services, skipped
