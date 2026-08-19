"""MCP server semantic detector (Phase 16).

MCP servers run over stdio, local HTTP, or SSE — so networking alone can
never prove one. Evidence used:

  - command line (mcp markers, server flags)
  - script / package path (node_modules/@modelcontextprotocol/…,
    mcp-server, mcp_server)
  - parent/child process relationship (MCP is spawned by its client —
    on this machine, typically the Hermes gateway)
  - local listeners (HTTP/SSE transports)
  - explicit detector hints

Identification truthfulness: when the evidence proves the specific server
(e.g. a ``server-filesystem`` package path) the semantic name is that
server; when it proves MCP but not which, the name is ``unknown``.

stdio servers have NO socket relationship. Their link to the parent is a
real ancestry edge (PROCESS_PARENT) and a semantic SPAWNED edge — never a
fake network edge, and never DATA particles.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..models.entities import Snapshot, TopologyResult
from .base import (
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
from .hermes import HERMES_SEMANTIC_NODE

_MCP_TOKEN = re.compile(r"(?i)(^|[\s\\/_.-])mcp([\s\\/_.-]|$)")
_SERVER_NAME_HINTS = [
    "filesystem", "github", "memory", "fetch", "sequential", "time",
    "brave-search", "firecrawl", "context7", "git", "sqlite", "postgres",
    "playwright", "puppeteer", "notion", "slack", "linear",
]


def _norm(s: str) -> str:
    return s.lower().replace("/", "\\")


def _has_mcp_token(text: str) -> bool:
    return bool(_MCP_TOKEN.search(text.lower()))


class McpDetector:
    name = "mcp"

    def __init__(self, cfg, hints: Optional[dict[str, Any]] = None) -> None:
        self.cfg = cfg
        self.hints = hints or {}
        self._cmdline_patterns = [
            p.lower() for p in self.hints.get("cmdline_patterns", ["mcp"])
        ]
        self._path_patterns = [
            _norm(p)
            for p in self.hints.get(
                "path_patterns",
                ["node_modules\\@modelcontextprotocol", "mcp-server", "mcp_server"],
            )
        ]
        self._launcher_patterns = [
            p.lower() for p in self.hints.get("launcher_patterns", ["hermes_cli.main", "hermes"])
        ]
        self._package_patterns = [
            p.lower()
            for p in self.hints.get("package_patterns", ["@modelcontextprotocol", "mcp-"])
        ]

    def detect(self, ctx: DetectorContext) -> tuple[list[Detection], list[SemanticRelationship]]:
        snaps: Snapshot = ctx.snap
        topo: TopologyResult = ctx.topo
        detections: list[Detection] = []
        rels: list[SemanticRelationship] = []

        pid_to_sid = ctx.pid_to_sid
        for sid, p in snaps.processes.items():
            cmd = " ".join(safe_cmdline(p.cmdline))
            exe = _norm(p.exe or "")
            cmd_l = cmd.lower()
            ppid = p.ppid
            parent_sid = pid_to_sid.get(ppid) if ppid is not None else None
            parent = snaps.processes.get(parent_sid) if parent_sid else None
            parent_cmd = " ".join(safe_cmdline(parent.cmdline)).lower() if parent else ""

            path_hit = any(pat in exe or pat in cmd_l for pat in self._path_patterns)
            package_hit = any(pat in cmd_l for pat in self._package_patterns)
            # cmdline marker: anchored token only — a bare "mcp" substring
            # ("mcproxy", "tcpdump", ...) is NOT an MCP marker
            token_hit = _has_mcp_token(cmd)
            launcher_hit = any(pat in parent_cmd for pat in self._launcher_patterns)

            # stdio servers look like plain child processes: indicator must
            # be strong, and context (launcher or package path) required —
            # a random "mcp" substring in any command line is NOT a server.
            if not (token_hit or path_hit):
                continue
            if not (launcher_hit or path_hit or package_hit or parent_sid is None):
                continue

            ev_list: list[DetectionEvidence] = []
            if path_hit:
                ev_list.append(evidence(
                    "executable_path",
                    f"MCP package path in command line ({p.exe or p.name})",
                ))
            if token_hit:
                ev_list.append(evidence("cmdline", "MCP marker in command line"))
            if launcher_hit and parent:
                ev_list.append(evidence(
                    "parent_relationship",
                    f"spawned by {parent.name} (PID {parent.pid}) — known MCP client launcher",
                ))

            server_name = self._identify_server(cmd)
            confidence = CONFIDENCE_HIGH if (package_hit or path_hit) else CONFIDENCE_MEDIUM
            if launcher_hit and not (package_hit or path_hit):
                confidence = CONFIDENCE_MEDIUM
            node_id = f"sem:mcp:{p.pid}"

            det = Detection(
                semantic_type="MCP_SERVER",
                semantic_name=server_name,
                confidence=confidence,
                node_id=node_id,
                process_ids=[sid],
                evidence=ev_list,
                metadata={
                    "pids": [p.pid],
                    "transport": self._transport(cmd, topo, sid),
                    "state": "RUNNING",
                },
            )
            detections.append(det)

            rels.append(SemanticRelationship(
                source=sid, target=node_id, kind="MEMBER_OF",
                evidence=[evidence("cmdline", "MCP server process")],
            ))
            if parent_sid and parent:
                rels.append(SemanticRelationship(
                    source=parent_sid, target=sid, kind="PROCESS_PARENT",
                    evidence=[evidence(
                        "parent_relationship",
                        f"{parent.name} (PID {parent.pid}) spawned {p.name} (PID {p.pid})",
                    )],
                ))
                rels.append(SemanticRelationship(
                    source=parent_sid, target=node_id, kind="SPAWNED",
                    evidence=[evidence(
                        "parent_relationship",
                        f"MCP server {server_name} spawned by {parent.name} (PID {parent.pid})",
                    )],
                ))
            # HTTP/SSE transport: the server owns a listener
            for e in topo.edges.values():
                if e.kind == "LISTEN" and e.source == sid:
                    rels.append(SemanticRelationship(
                        source=node_id, target=e.target, kind="HOSTS",
                        evidence=[evidence(
                            "owned_listener",
                            f"MCP server listens on localhost:{e.ports[0] if e.ports else '?'}",
                        )],
                    ))

        return detections, rels

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _identify_server(cmd: str) -> str:
        """Server identity from package/path/cmdline evidence; else 'unknown'."""
        cmd_l = cmd.lower()
        for name in _SERVER_NAME_HINTS:
            if name in cmd_l:
                return name
        m = re.search(r"(?i)server[-_]([a-z0-9-]+)", cmd_l)
        if m:
            return m.group(1)
        m = re.search(r"(?i)@modelcontextprotocol[/\\](server-[a-z0-9-]+)", cmd_l)
        if m:
            return m.group(1).removeprefix("server-")
        return "unknown"

    @staticmethod
    def _transport(cmd: str, topo: TopologyResult, sid: str) -> str:
        """stdio by default; HTTP/SSE only when the process owns a listener."""
        for e in topo.edges.values():
            if e.kind == "LISTEN" and e.source == sid:
                return "HTTP/SSE"
        return "stdio"
