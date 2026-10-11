"""Best-effort redaction of sensitive data before storage, export, or AI use."""
from __future__ import annotations

import getpass
import json
import re
import socket
from typing import Any

_RULES = [
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret|passwd|password|pwd)\b(\s*[=:]\s*|\s+)\S+"), r"\1\2<redacted>"),
    (re.compile(r"\b[A-Za-z0-9+/_\-]{40,}={0,2}\b"), "<redacted-secret>"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "<email>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ipv4>"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"), "<ipv6>"),
    (re.compile(r"/home/[^/\s]+"), "/home/<user>"),
]


def redact(text: str, keep_ips: bool = False) -> str:
    out = str(text)
    try:
        user, host = getpass.getuser(), socket.gethostname()
    except Exception:  # noqa: BLE001
        user = host = ""
    for lit, repl in ((user, "<user>"), (host, "<host>")):
        if len(lit) >= 3:
            out = re.sub(rf"\b{re.escape(lit)}\b", repl, out)
    for pat, repl in _RULES:
        if keep_ips and repl in ("<ipv4>", "<ipv6>"):
            continue
        out = pat.sub(repl, out)
    return out


def redact_obj(obj: Any, keep_ips: bool = False) -> Any:
    """Redact every string inside nested JSON-like data."""
    if isinstance(obj, str):
        return redact(obj, keep_ips)
    if isinstance(obj, dict):
        return {str(k): redact_obj(v, keep_ips) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, keep_ips) for v in obj]
    return obj


def redact_json(obj: Any) -> str:
    return json.dumps(redact_obj(obj), default=str)
