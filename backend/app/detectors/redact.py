"""Command-line secret redaction.

Credential-looking values must never reach the frontend, the WebSocket
stream, or the logs. Detectors may consume sanitized command metadata only.

Redaction is applied at the serialization boundary (topology node data,
diff-event metadata) and inside detectors via :func:`safe_cmdline`, so the
raw process collector keeps its untouched snapshot (tests rely on that)
while every outward-facing path is scrubbed.

Only value *shapes* are matched — the flag names themselves are not secret.
A value is replaced by ``***`` when it appears:
  - as the argument of a known secret-bearing flag (``--api-key X``,
    ``--access-token=Y``, ``-p password``, ...)
  - inside a key=value token whose key looks like a secret
  - as the payload of an ``Authorization: Bearer ...`` style token
"""
from __future__ import annotations

import re

_SECRET_FLAGS = re.compile(
    r"(?i)^-{1,2}(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|"
    r"passwd|secret|client[_-]?secret|bearer|auth(?:orization)?|session[_-]?key)$"
)
_SECRET_KEY_VALUE = re.compile(
    r"(?i)(^|[=&;\s])(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|"
    r"passwd|secret|client[_-]?secret|bearer|auth(?:orization)?|session[_-]?key)=[^&\s]+"
)
_BEARER_PREFIX = re.compile(r"(?i)\bbearer\s+")
_BEARER_TOKEN = re.compile(r"(?i)(\bbearer\s+)\S+")
_REDACTED = "***"


def _redact_token(arg: str) -> str:
    """Redact a single argv token (flag, value, or key=value form)."""
    # "Authorization: Bearer xyz" / "Bearer xyz" style tokens — the token
    # itself is consumed, not just annotated
    if _BEARER_PREFIX.search(arg):
        return _BEARER_TOKEN.sub(lambda m: m.group(1) + _REDACTED, arg)
    # inline key=value form (--flag handled by the caller)
    return _SECRET_KEY_VALUE.sub(lambda m: m.group(1) + _REDACTED, arg)


def redact_cmdline(args: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return a redacted copy of a command line (never mutates the input)."""
    if not args:
        return list(args or [])
    out: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        arg = args[i]
        # flag with a separate value: --api-key supersecret
        if _SECRET_FLAGS.match(arg) and i + 1 < n:
            out.append(arg)
            out.append(_REDACTED)
            i += 2
            continue
        # --flag=value  /  key=value  inline form
        if "=" in arg:
            key, _, rest = arg.partition("=")
            if _SECRET_FLAGS.match(key) and rest:
                out.append(f"{key}={_REDACTED}")
                i += 1
                continue
        out.append(_redact_token(arg))
        i += 1
    return out


def safe_cmdline(args: list[str] | tuple[str, ...] | None) -> list[str]:
    """Alias used by detectors — same behaviour, intent-revealing name."""
    return redact_cmdline(args)
