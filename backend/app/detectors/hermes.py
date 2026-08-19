"""Hermes semantic detector (Phase 15).

Never relies on a single fragile process-name rule. Combines:

  - process name (Hermes.exe) AND executable path (…hermes-agent\…)
  - command line ``hermes_cli.main … serve`` (gateway) with the
    hermes-agent venv path
  - parent/child process relationships (Electron children, gateway
    spawned by the desktop app)
  - owned localhost listeners (LOCAL_API / HOSTS relationships)
  - explicit detector hints from config/detectors.json

CONFIRMED requires an exact known-path identity match (desktop app or
gateway). A bare process name is only MEDIUM; hint-only matches are LOW.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..models.entities import Snapshot, TopologyResult
from .base import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Detection,
    DetectionEvidence,
    SemanticRelationship,
    evidence,
)
from .redact import safe_cmdline
from .base import DetectorContext

HERMES_SEMANTIC_NODE = "sem:hermes"
HERMES_SEMANTIC_NAME = "HERMES AGENT"
_BUILTIN_PROCESS_PATTERNS = ["hermes.exe"]  # normalized; anything else = config hint


def _norm(s: str) -> str:
    return s.lower().replace("/", "\\")


class HermesDetector:
    name = "hermes"

    def __init__(self, cfg, hints: Optional[dict[str, Any]] = None) -> None:
        self.cfg = cfg
        self.hints = hints or {}
        self._process_patterns = [
            _norm(p) for p in self.hints.get("process_patterns", ["Hermes.exe"])
        ]
        self._path_patterns = [_norm(p) for p in self.hints.get("path_patterns", ["hermes-agent"])]
        self._cmdline_patterns = [
            p.lower() for p in self.hints.get("cmdline_patterns", ["hermes_cli.main"])
        ]

    # ------------------------------------------------------------- detection

    def detect(self, ctx: DetectorContext) -> tuple[list[Detection], list[SemanticRelationship]]:
        snaps = ctx.snap
        topo = ctx.topo
        family: list[str] = []          # stable ids of every Hermes process
        desktop_main: Optional[str] = None
        desktop_evidence: list[DetectionEvidence] = []
        gateway_evidence: list[DetectionEvidence] = []
        gateway_sids: list[str] = []

        pid_to_sid = ctx.pid_to_sid
        procs_by_sid = snaps.processes
        sid_to_ppid = {
            sid: (pid_to_sid.get(p.ppid) if p.ppid is not None else None)
            for sid, p in procs_by_sid.items()
        }

        for sid, p in procs_by_sid.items():
            name = _norm(p.name or "")
            exe = _norm(p.exe or "")
            cmd = " ".join(safe_cmdline(p.cmdline)).lower()

            is_desktop = any(pat in name for pat in self._process_patterns)
            path_matches = any(pat in exe for pat in self._path_patterns)
            is_gateway = any(pat in cmd for pat in self._cmdline_patterns) and "serve" in cmd

            if is_desktop and path_matches:
                family.append(sid)
                if desktop_main is None and "--type=" not in cmd:
                    desktop_main = sid
                if "--type=" in cmd:
                    desktop_evidence.append(evidence(
                        "child_relationship",
                        f"{p.name} Electron child of Hermes desktop (--type=…)",
                    ))
                else:
                    desktop_evidence.append(evidence(
                        "executable_path",
                        f"{p.name} at known Hermes path {p.exe}",
                    ))
            elif is_desktop:
                family.append(sid)
                name_hits = [pat for pat in self._process_patterns if pat in name]
                hint_only = all(pat not in _BUILTIN_PROCESS_PATTERNS for pat in name_hits)
                if hint_only:
                    # matched only through config hints: weakest possible signal
                    desktop_evidence.append(evidence(
                        "config_hint",
                        f"process name {p.name} matched a detector hint only",
                    ))
                else:
                    desktop_evidence.append(evidence(
                        "process_name",
                        f"process named {p.name} (path not a known Hermes path)",
                    ))
            elif is_gateway:
                family.append(sid)
                gateway_sids.append(sid)
                gateway_evidence.append(evidence(
                    "cmdline",
                    f"hermes gateway command line (hermes_cli.main serve, PID {p.pid})",
                ))
                if path_matches:
                    gateway_evidence.append(evidence(
                        "executable_path",
                        f"gateway python inside hermes-agent venv ({p.exe})",
                    ))

        # ancestry: processes whose parent chain reaches a confirmed Hermes
        # process (e.g. gateway uv-shim children). A descendant joins the
        # family ONLY when it carries its own Hermes identity signal —
        # otherwise every app spawned from this session tree (bash, the
        # monitor backend, curl, ...) would be misclassified as Hermes.
        def _has_identity(sid: str) -> bool:
            p = procs_by_sid[sid]
            name = _norm(p.name or "")
            exe = _norm(p.exe or "")
            cmd = " ".join(safe_cmdline(p.cmdline)).lower()
            return (
                any(pat in name for pat in self._process_patterns)
                or any(pat in exe for pat in self._path_patterns)
                or (
                    any(pat in cmd for pat in self._cmdline_patterns)
                    and "serve" in cmd
                )
            )

        confirmed = set(family)
        changed = True
        while changed:
            changed = False
            for sid, ppid in sid_to_ppid.items():
                if sid in family:
                    continue
                if ppid in confirmed and _has_identity(sid):
                    family.append(sid)
                    confirmed.add(sid)
                    changed = True

        if not family:
            return [], []

        ev_list = list(desktop_evidence) + list(gateway_evidence)
        if not ev_list:
            ev_list = [evidence("config_hint", "detector hints matched a Hermes pattern")]
        confidence = self._confidence(desktop_evidence, gateway_evidence, len(family))

        det = Detection(
            semantic_type="HERMES",
            semantic_name=HERMES_SEMANTIC_NAME,
            confidence=confidence,
            node_id=HERMES_SEMANTIC_NODE,
            process_ids=family,
            evidence=ev_list,
            metadata={
                "pids": [procs_by_sid[s].pid for s in family],
                "desktop_main_sid": desktop_main,
                "gateway_sids": gateway_sids,
                "state": "RUNNING",
            },
        )

        rels: list[SemanticRelationship] = []
        # membership: every underlying process belongs to the semantic node
        for sid in family:
            rels.append(SemanticRelationship(
                source=sid, target=HERMES_SEMANTIC_NODE, kind="MEMBER_OF",
                evidence=[evidence("process_name", "Hermes family process")],
            ))
        # real parent/child relationships inside the family
        seen: set[tuple[str, str]] = set()
        for sid, ppid in sid_to_ppid.items():
            if sid in family and ppid in family and (ppid, sid) not in seen:
                seen.add((ppid, sid))
                rels.append(SemanticRelationship(
                    source=ppid, target=sid, kind="PROCESS_PARENT",
                    evidence=[evidence(
                        "parent_relationship",
                        f"{procs_by_sid[ppid].name} (PID {procs_by_sid[ppid].pid}) spawned "
                        f"{procs_by_sid[sid].name} (PID {procs_by_sid[sid].pid})",
                    )],
                ))
        # owned localhost listeners (gateway API socket)
        for sid in gateway_sids:
            for e in topo.edges.values():
                if e.kind == "LISTEN" and e.source == sid:
                    rels.append(SemanticRelationship(
                        source=HERMES_SEMANTIC_NODE, target=e.target, kind="HOSTS",
                        evidence=[evidence(
                            "owned_listener",
                            f"gateway listens on localhost:{e.ports[0] if e.ports else '?'}",
                        )],
                    ))

        return [det], rels

    @staticmethod
    def _confidence(
        desktop_evidence: list[DetectionEvidence],
        gateway_evidence: list[DetectionEvidence],
        family_size: int,
    ) -> str:
        # hint-only name matches are the weakest possible signal: LOW
        if (
            desktop_evidence
            and not gateway_evidence
            and all(e.source == "config_hint" for e in desktop_evidence)
        ):
            return CONFIDENCE_LOW
        if desktop_evidence and any(e.source == "executable_path" for e in desktop_evidence):
            return CONFIDENCE_CONFIRMED
        if gateway_evidence and any(e.source == "executable_path" for e in gateway_evidence):
            return CONFIDENCE_CONFIRMED
        if desktop_evidence and gateway_evidence:
            return CONFIDENCE_HIGH
        if family_size >= 3 and desktop_evidence:
            return CONFIDENCE_HIGH
        if desktop_evidence or gateway_evidence:
            return CONFIDENCE_MEDIUM
        return CONFIDENCE_LOW
