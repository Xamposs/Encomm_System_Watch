"""Virtual machine observability — READ-ONLY (Phase 21).

Generic VM detector framework supporting the common local hypervisors when
they are ACTUALLY installed: Hyper-V, VMware Workstation/Player, VirtualBox.
WSL2 is recognized separately by the WSL collector — never duplicated here.

Strict rules:
  - READ-ONLY discovery only: CIM queries, process/command-line evidence,
    ``VBoxManage list ...``, ``vmrun list``. NO control commands ever
    (no Start/Stop/Restart-VM, no VBoxManage controlvm/startvm/modifyvm,
    no vmrun start/stop).
  - Identity is evidence-backed. When only a generic hypervisor process is
    visible and VM identity cannot be proven, the result is a
    ``VIRTUALIZATION PROCESS`` entry with ``name=None`` and LOW/MEDIUM
    confidence — never a made-up VM name.
  - Command lines are sanitized (VM paths redacted) before serialization.
  - Every provider degrades independently; missing providers are simply
    absent from the results.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("esw")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

VBOX_PATHS = [
    r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
    r"C:\Program Files (x86)\Oracle\VirtualBox\VBoxManage.exe",
]
VMRUN_PATHS = [
    r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe",
    r"C:\Program Files\VMware\VMware Workstation\vmrun.exe",
    r"C:\Program Files (x86)\VMware\VMware Player\vmrun.exe",
]
VMWARE_EXE_DIRS = [
    Path(r"C:\Program Files (x86)\VMware\VMware Workstation"),
    Path(r"C:\Program Files\VMware\VMware Workstation"),
    Path(r"C:\Program Files (x86)\VMware\VMware Player"),
    Path(r"C:\Program Files\VMware\VMware Player"),
]
# generic hypervisor worker processes (identity may not be provable)
HYPERVISOR_EXES = {
    "vmwp.exe": "HYPER-V WORKER",
    "vmware-vmx.exe": "VMWARE VM",
    "VBoxHeadless.exe": "VIRTUALBOX HEADLESS",
    "VirtualBox.exe": "VIRTUALBOX UI",
    "qemu-system-x86_64.exe": "QEMU",
}

_HV_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$vms = Get-VM | Select-Object Name,Id,State,Generation,MemoryAssigned,"
    "Uptime,ProcessorCount,@{N='NetAdapters';E={($_.NetworkAdapters | ForEach-Object Name) -join ','}};"
    "if ($vms) { $vms | ConvertTo-Json -Compress -Depth 4 } else { Write-Output '[]' };"
    "Write-Output '---VMWP---';"
    "Get-CimInstance Win32_Process -Filter \"Name = 'vmwp.exe'\" | ForEach-Object {"
    "[PSCustomObject]@{Pid=$_.ProcessId; Cmd=$_.CommandLine} } | ConvertTo-Json -Compress"
)

_VMWP_GUID_RE = re.compile(r"-G\s+([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})")


@dataclass
class VmInfo:
    provider: str                  # HYPER_V | VMWARE | VIRTUALBOX | OTHER
    name: Optional[str]            # None => generic VIRTUALIZATION PROCESS
    vm_id: Optional[str] = None
    state: str = "UNKNOWN"         # RUNNING | OFF | PAUSED | ...
    confidence: str = "LOW"        # CONFIRMED | HIGH | MEDIUM | LOW
    evidence: str = ""
    host_pid: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "provider": self.provider,
            "name": self.name,
            "state": self.state,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "metadata": self.metadata,
        }
        if self.vm_id:
            d["vm_id"] = self.vm_id
        if self.host_pid is not None:
            d["host_pid"] = self.host_pid
        return d

    @property
    def identity(self) -> str:
        """Node identity: real name when proven, else the process marker."""
        return self.name or "VIRTUALIZATION PROCESS"


@dataclass
class VmState:
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    vms: list[VmInfo] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": self.providers,
            "vms": [v.to_dict() for v in self.vms],
        }


def _decode(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-8"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _sanitize_path(path: str) -> str:
    """Redact a VM file path: keep the file name, drop the directory."""
    if not path:
        return ""
    try:
        name = Path(path).name
        return name or "<unknown>"
    except Exception:  # noqa: BLE001
        return "<unknown>"


def _uptime_to_s(raw: str) -> Optional[int]:
    """Parse TimeSpan-ish "1.02:03:04" (days.hh:mm:ss) -> seconds."""
    if not raw:
        return None
    raw = raw.strip()
    m = re.match(r"^(?:(\d+)\.)?(\d+):(\d+):(\d+)$", raw)
    if not m:
        return None
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h) * 3600 + int(mi) * 60 + int(s)


class VmCollector:
    """Read-only VM detection across Hyper-V / VMware / VirtualBox."""

    def __init__(self) -> None:
        self._psutil = None

    # ------------------------------------------------------------- internals

    def _procs(self) -> list[dict[str, Any]]:
        """Batched psutil process snapshot (one as_dict call — psutil has no
        thread parallelism on Windows)."""
        import psutil

        procs: list[dict[str, Any]] = []
        for p in psutil.process_iter():
            try:
                info = p.as_dict(attrs=["pid", "name", "exe", "cmdline"])
                procs.append(info)
            except Exception:  # noqa: BLE001 — per-process isolation
                continue
        return procs

    def _ps(self, script: str, timeout: float = 15.0):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )

    # ---------------------------------------------------------- hyper-v path

    def _hyperv(self, procs: list[dict[str, Any]]) -> tuple[dict, list[VmInfo]]:
        info: dict[str, Any] = {"installed": False, "count": 0, "running": 0}
        try:
            r = self._ps(_HV_SCRIPT)
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"PowerShell unavailable: {exc}"
            return info, []
        text = _decode(r.stdout)
        if r.returncode != 0 or "Get-VM" in text and "not recognized" in text:
            snippet = text.strip()[:150] or _decode(r.stderr).strip()[:150]
            info["error"] = f"Hyper-V module not available: {snippet}"
            return info, []
        vms_json, _, vmwp_json = text.partition("---VMWP---")
        vms: list[VmInfo] = []
        # vmwp.exe guid -> pid mapping (host process evidence)
        vmwp_pids: dict[str, int] = {}
        vmwp_raw = vmwp_json.strip()
        if vmwp_raw:
            try:
                parsed = json.loads(vmwp_raw)
                if isinstance(parsed, dict):
                    parsed = [parsed]
                for w in parsed or []:
                    cmd = str(w.get("Cmd", "") or "")
                    m = _VMWP_GUID_RE.search(cmd)
                    if m:
                        vmwp_pids[m.group(1).lower()] = int(w.get("Pid") or 0)
            except (ValueError, AttributeError):
                pass
        try:
            parsed = json.loads(vms_json.strip() or "[]")
            if isinstance(parsed, dict):
                parsed = [parsed]
            for v in parsed or []:
                name = str(v.get("Name", "") or "")
                if not name:
                    continue
                vid = str(v.get("Id", "") or "")
                state_raw = str(v.get("State", "") or "").upper()
                state = "RUNNING" if state_raw == "RUNNING" else (
                    "OFF" if state_raw in ("OFF", "STOPPED", "SAVED") else state_raw)
                meta: dict[str, Any] = {}
                if v.get("Generation"):
                    meta["generation"] = int(v["Generation"])
                if v.get("MemoryAssigned"):
                    meta["memory_mb"] = round(int(v["MemoryAssigned"]) / 1024 / 1024)
                if v.get("Uptime"):
                    upt = _uptime_to_s(str(v["Uptime"]))
                    if upt is not None:
                        meta["uptime_s"] = upt
                if v.get("ProcessorCount"):
                    meta["cpu_count"] = int(v["ProcessorCount"])
                if v.get("NetAdapters"):
                    nets = v["NetAdapters"]
                    meta["network_adapters"] = (
                        nets if isinstance(nets, list) else str(nets).split(",")
                    )
                host_pid = vmwp_pids.get(vid.lower()) if vid else None
                vms.append(VmInfo(
                    provider="HYPER_V", name=name, vm_id=vid or None,
                    state=state, confidence="CONFIRMED" if state == "RUNNING" else "HIGH",
                    evidence="Get-VM (CIM)" + (f" + vmwp PID {host_pid}" if host_pid else ""),
                    host_pid=host_pid, metadata=meta,
                ))
        except ValueError:
            info["error"] = "Get-VM output unparseable"
        info["installed"] = True
        info["count"] = len(vms)
        info["running"] = sum(1 for v in vms if v.state == "RUNNING")
        return info, vms

    # ----------------------------------------------------------- vmware path

    def _vmware(self, procs: list[dict[str, Any]]) -> tuple[dict, list[VmInfo]]:
        installed = any(d.is_dir() for d in VMWARE_EXE_DIRS)
        vmx_procs = [p for p in procs if (p.get("name") or "").lower() == "vmware-vmx.exe"]
        info: dict[str, Any] = {"installed": installed, "count": 0, "running": 0}
        vms: list[VmInfo] = []
        for p in vmx_procs:
            pid = int(p.get("pid") or 0)
            cmdline = [str(c) for c in (p.get("cmdline") or []) if c]
            vmx_path = next((c for c in cmdline if c.lower().endswith(".vmx")), None)
            if vmx_path:
                vms.append(VmInfo(
                    provider="VMWARE",
                    name=Path(vmx_path).stem or None,
                    state="RUNNING", confidence="HIGH",
                    evidence="vmware-vmx.exe command line (.vmx path)",
                    host_pid=pid,
                    metadata={"host_process": "vmware-vmx.exe",
                              "vmx_file": _sanitize_path(vmx_path)},
                ))
            else:
                vms.append(VmInfo(
                    provider="VMWARE", name=None, state="RUNNING",
                    confidence="MEDIUM",
                    evidence="vmware-vmx.exe running without readable .vmx path",
                    host_pid=pid,
                    metadata={"host_process": "vmware-vmx.exe"},
                ))
        # vmrun list as secondary evidence (read-only LIST only) when installed
        if vmx_procs:
            vmrun = next((str(x) for x in VMRUN_PATHS if Path(x).is_file()), None)
            if vmrun:
                try:
                    r = subprocess.run([vmrun, "list"], capture_output=True,
                                       timeout=10, creationflags=CREATE_NO_WINDOW)
                    if r.returncode == 0:
                        lines = [ln.strip() for ln in _decode(r.stdout).splitlines()
                                 if ln.strip()]
                        info["vmrun"] = lines
                except Exception:  # noqa: BLE001
                    pass
        info["count"] = len(vms)
        info["running"] = len(vms)
        return info, vms

    # ------------------------------------------------------- virtualbox path

    def _virtualbox(self, procs: list[dict[str, Any]]) -> tuple[dict, list[VmInfo]]:
        vbox = next((x for x in VBOX_PATHS if Path(x).is_file()), None)
        info: dict[str, Any] = {"installed": vbox is not None, "count": 0, "running": 0}
        if not vbox:
            return info, []
        vms: list[VmInfo] = []
        # read-only LIST operations only
        try:
            r = subprocess.run([vbox, "list", "runningvms"], capture_output=True,
                               timeout=10, creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                info["error"] = _decode(r.stderr).strip()[:200]
                return info, []
            running: dict[str, str] = {}
            for line in _decode(r.stdout).splitlines():
                m = re.match(r'^"(.+)"\s+\{([0-9a-f-]+)\}', line.strip())
                if m:
                    running[m.group(1)] = m.group(2)
            for name, vid in running.items():
                vms.append(VmInfo(
                    provider="VIRTUALBOX", name=name, vm_id=vid,
                    state="RUNNING", confidence="CONFIRMED",
                    evidence="VBoxManage list runningvms",
                    metadata={"host_process": "VBoxHeadless/VirtualBox"},
                ))
            info["count"] = len(vms)
            info["running"] = len(vms)
            info["defined"] = None
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"VBoxManage failed: {exc}"
        # host process evidence for running guests (when names are known the
        # runningvms list is authoritative; processes add host PID evidence)
        if vms:
            for p in procs:
                pname = (p.get("name") or "").lower()
                if pname in ("vboxheadless.exe", "virtualbox.exe") and vms:
                    # attach to the first VM without a host pid (best effort,
                    # names are the authoritative mapping)
                    for v in vms:
                        if v.host_pid is None:
                            v.host_pid = int(p.get("pid") or 0)
                            v.evidence += f" + {pname} PID {v.host_pid}"
                            break
        return info, vms

    # -------------------------------------------------------------- public

    def collect(self) -> VmState:
        state = VmState()
        try:
            procs = self._procs()
        except Exception as exc:  # noqa: BLE001 — process scan must never kill
            log.warning("vm process scan failed: %s", exc)
            procs = []

        # Hyper-V — only when the feature is actually present
        hv_info, hv_vms = self._hyperv(procs)
        state.providers["HYPER_V"] = hv_info
        state.vms.extend(hv_vms)

        vmw_info, vmw_vms = self._vmware(procs)
        state.providers["VMWARE"] = vmw_info
        state.vms.extend(vmw_vms)

        vb_info, vb_vms = self._virtualbox(procs)
        state.providers["VIRTUALBOX"] = vb_info
        state.vms.extend(vb_vms)

        # generic hypervisor processes with NO proven VM identity
        known_host_pids = {v.host_pid for v in state.vms if v.host_pid}
        for p in procs:
            pname = (p.get("name") or "")
            label = HYPERVISOR_EXES.get(pname.lower())
            if not label:
                continue
            pid = int(p.get("pid") or 0)
            if pid in known_host_pids:
                continue  # already attributed to a proven VM
            state.vms.append(VmInfo(
                provider="OTHER", name=None, state="RUNNING",
                confidence="LOW",
                evidence=f"running hypervisor process ({label}) without proven VM identity",
                host_pid=pid,
                metadata={"host_process": pname},
            ))
        return state
