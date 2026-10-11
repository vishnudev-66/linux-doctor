"""Command suggestion policy. AI output is untrusted: commands are only ever DISPLAYED,
and only read-only ones are labelled as safe to try."""
from __future__ import annotations

import re
import shlex

_META = re.compile(r"[;&|`$<>\\\n\r(){}*?!~]")
_READ_ONLY = {
    "ip": {"addr", "address", "route", "link", "-brief", "-br", "a", "r"},
    "df": None, "free": None, "uptime": None, "uname": None, "lsblk": None, "lspci": None, "lsusb": None,
    "ss": None, "ps": None, "top": None, "du": None, "id": None, "whoami": None, "hostnamectl": None,
    "systemctl": {"status", "is-active", "is-enabled", "--failed", "list-units", "--no-pager"},
    "journalctl": None, "dmesg": None, "resolvectl": {"status", "query"}, "ufw": {"status"},
    "apt": {"list", "policy", "show"}, "dpkg": {"-l", "-s", "--list", "--status"},
}


# Mutating verbs / flags that must never be labelled read-only, whatever the base command.
_DENY_WORDS = {"set", "add", "del", "delete", "flush", "change", "replace", "enable", "disable", "restart", "start",
               "stop", "reload", "kill", "mask", "unmask", "edit", "reset", "reboot", "poweroff", "install", "remove",
               "purge", "upgrade", "autoremove", "clean", "-k", "-c", "-C", "--kill", "--clear", "--read-clear"}
_DENY_PREFIXES = ("--vacuum", "--rotate", "--flush", "--sync", "--relinquish", "--smart-relinquish", "--setup-keys",
                  "--update-catalog", "--output=", "-o", "--file", "--root", "--directory")


def classify(cmd: str) -> str:
    """Return 'read_only' or 'review' (never runnable from the app)."""
    if not isinstance(cmd, str) or len(cmd) > 200 or _META.search(cmd):
        return "review"
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return "review"
    if not parts or parts[0] not in _READ_ONLY:
        return "review"
    allowed = _READ_ONLY[parts[0]]
    if allowed is not None:
        subs = [p for p in parts[1:] if not p.startswith("-n") and not p.isdigit()]
        if not subs or subs[0] not in allowed:
            return "review"
    if parts[0] in ("ss", "dmesg") and any(p[:1] == "-" and p[:2] != "--" and set(p) & set("KkcC") for p in parts[1:]):
        return "review"   # ss -K kills sockets; dmesg -c/-C clears the ring buffer (incl. clustered flags like -tK)
    for p in parts[1:]:
        if p in _DENY_WORDS or p.startswith(_DENY_PREFIXES):
            return "review"
    return "read_only"
