"""Offline command library + local explainer (works with no AI)."""
from __future__ import annotations

import shlex

LIBRARY: dict[str, dict[str, str]] = {
    "df": {"purpose": "Show filesystem space usage", "syntax": "df -h", "example": "df -h /",
           "notes": "-h = human-readable sizes. Read-only."},
    "du": {"purpose": "Show how much space directories use", "syntax": "du -sh <path>", "example": "du -sh ~/Downloads",
           "notes": "Read-only. On huge trees it can take a while."},
    "free": {"purpose": "Show memory and swap", "syntax": "free -h", "example": "free -h",
             "notes": "Look at 'available', not 'free': Linux caches in spare RAM."},
    "top": {"purpose": "Live view of processes", "syntax": "top", "example": "top", "notes": "Press q to quit."},
    "ps": {"purpose": "List processes", "syntax": "ps aux --sort=-%cpu | head", "example": "ps aux --sort=-%mem | head",
           "notes": "Read-only."},
    "ip": {"purpose": "Inspect network interfaces and routes", "syntax": "ip addr | ip route", "example": "ip -brief address",
           "notes": "Read-only when used with addr/route/link."},
    "ss": {"purpose": "List sockets and listening ports", "syntax": "ss -tuln", "example": "ss -tuln",
           "notes": "Read-only. -p shows owning processes (needs privileges for others')."},
    "systemctl": {"purpose": "Inspect/control services", "syntax": "systemctl status <unit>", "example": "systemctl --failed",
                  "notes": "status/--failed are read-only; start/stop/restart change the system."},
    "journalctl": {"purpose": "Read system logs", "syntax": "journalctl -p err -b -n 100", "example": "journalctl -u ssh.service -n 50",
                   "notes": "-b = this boot, -n = last N lines, -p err = errors."},
    "chmod": {"purpose": "Change file permissions", "syntax": "chmod 600 <file>", "example": "chmod 600 ~/.ssh/id_ed25519",
              "notes": "CAUTION: recursive (-R) or on system paths can break your system."},
    "apt": {"purpose": "Package manager", "syntax": "apt list --upgradable", "example": "apt list --upgradable",
            "notes": "'list' is read-only; install/remove/upgrade change the system and need sudo."},
    "uname": {"purpose": "Kernel/system information", "syntax": "uname -a", "example": "uname -r", "notes": "Read-only."},
    "ufw": {"purpose": "Firewall front-end", "syntax": "sudo ufw status", "example": "sudo ufw status verbose",
            "notes": "'enable'/'allow'/'deny' alter network access - review first."},
    "lsblk": {"purpose": "List block devices", "syntax": "lsblk", "example": "lsblk -f", "notes": "Read-only."},
}

_DANGEROUS = {
    "rm": "deletes files permanently - double-check the path, especially with -r/-f",
    "dd": "writes raw data to devices and can destroy disks",
    "mkfs": "formats a filesystem and erases its data",
    "chmod": "changes permissions; recursive use on system paths can break the OS",
    "chown": "changes ownership; recursive use on system paths can break the OS",
    "kill": "terminates processes; unsaved work can be lost",
    "shutdown": "powers off or reboots", "reboot": "reboots the machine",
}


def explain_command(text: str) -> dict:
    """Local, deterministic explanation. Never executes the command."""
    try:
        parts = shlex.split(text)
    except ValueError:
        return {"ok": False, "message": "Could not parse this command (unbalanced quotes)."}
    if not parts:
        return {"ok": False, "message": "Enter a command."}
    notes, i = [], 0
    if parts[0] == "sudo":
        notes.append("'sudo' runs the rest with administrator rights - mistakes are more costly.")
        i = 1
    if i >= len(parts):
        return {"ok": False, "message": "sudo needs a command after it."}
    cmd = parts[i]
    entry = LIBRARY.get(cmd)
    warn = _DANGEROUS.get(cmd)
    if any(c in text for c in ("|", ";", "&&", "$(", "`", ">")):
        notes.append("Contains pipes/redirection/chaining - each part runs separately; redirection (>) can overwrite files.")
    if cmd in ("rm", "dd", "mkfs") or (cmd == "chmod" and "-R" in parts):
        notes.append("Review this carefully before running; Linux Doctor will never run it for you.")
    return {"ok": True, "command": cmd, "known": bool(entry), "library": entry, "warning": warn, "notes": notes}
