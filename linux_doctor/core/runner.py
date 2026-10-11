"""Allow-listed command runner.

There is NO function here that accepts an arbitrary command string. Callers pick a
registered key; the only variable parts are validated placeholders. No shell is used.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
MAX_OUTPUT = 512 * 1024

_UNIT = re.compile(r"^[A-Za-z0-9@_.:\-]{1,100}\.(service|socket|timer|target|mount|path)$")

COMMANDS: dict[str, list[str]] = {
    "ip_addr": ["ip", "-brief", "address"],
    "ip_route": ["ip", "route"],
    "ss_listen": ["ss", "-H", "-tuln"],
    "lsblk": ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"],
    "failed_units": ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
    "unit_status": ["systemctl", "status", "{unit}", "--no-pager", "-n", "0"],
    "unit_journal": ["journalctl", "-u", "{unit}", "-b", "--no-pager", "-n", "{n}"],
    "journal_errors": ["journalctl", "-p", "err", "-b", "--no-pager", "-n", "{n}"],
    "ssh_active": ["systemctl", "is-active", "ssh"],
    "ufw_status": ["ufw", "status"],
    "apt_upgradable": ["apt", "list", "--upgradable"],
    "gpu_pci": ["lspci"],
    "uname": ["uname", "-a"],
}


@dataclass
class CmdResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    missing: bool = False
    timed_out: bool = False


def _validate(token: str, value: str) -> str:
    if token == "{unit}":
        if not _UNIT.match(value):
            raise ValueError("invalid unit name")
        return value
    if token == "{n}":
        n = int(value)
        if not 1 <= n <= 1000:
            raise ValueError("n out of range")
        return str(n)
    raise ValueError("unknown placeholder")


def run(key: str, *, timeout: float = 10, **params: str | int) -> CmdResult:
    """Run a registered command. Unknown keys / invalid params raise ValueError."""
    if key not in COMMANDS:
        raise ValueError(f"command not allow-listed: {key!r}")
    argv: list[str] = []
    used = set()
    for tok in COMMANDS[key]:
        if tok.startswith("{") and tok.endswith("}"):
            name = tok[1:-1]
            if name not in params:
                raise ValueError(f"missing parameter {name}")
            argv.append(_validate(tok, str(params[name])))
            used.add(name)
        else:
            argv.append(tok)
    if set(params) - used:
        raise ValueError("unexpected parameters")
    exe = shutil.which(argv[0], path=SAFE_PATH)
    if not exe:
        return CmdResult(False, missing=True, stderr=f"{argv[0]} not installed")
    argv[0] = exe
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                           timeout=timeout, check=False, shell=False, stdin=subprocess.DEVNULL,
                           env={"PATH": SAFE_PATH, "LC_ALL": "C", "LANG": "C"})
    except subprocess.TimeoutExpired:
        return CmdResult(False, timed_out=True, stderr="timed out")
    except OSError as exc:
        return CmdResult(False, stderr=str(exc))
    return CmdResult(p.returncode == 0, p.stdout[:MAX_OUTPUT], p.stderr[:MAX_OUTPUT], p.returncode)
