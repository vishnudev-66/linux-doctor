"""Read-only security hygiene checks. Not an audit; never claims the system is secure."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from ..config import Settings
from ..core import runner
from ..core.models import Confidence, Finding, Severity


def check_security(s: Settings, **_) -> list[Finding]:
    out: list[Finding] = []
    # Firewall
    r = runner.run("ufw_status", timeout=5)
    if r.missing:
        out.append(Finding("SEC_UFW_MISSING", "UFW is not installed", Severity.INFO,
                           "Another firewall (nftables/firewalld) may be in use; not inspected.", confidence=Confidence.LOW))
    elif "inactive" in r.stdout.lower():
        out.append(Finding("SEC_UFW_INACTIVE", "UFW firewall is inactive", Severity.ATTENTION,
                           "Inactive firewall is common on desktops behind a router but increases exposure on public networks.",
                           {"status": "inactive"}, "Consider: sudo ufw enable (review rules first).", Confidence.HIGH, "ufw"))
    elif "active" in r.stdout.lower():
        out.append(Finding("SEC_UFW_ACTIVE", "UFW firewall is active", Severity.OK, confidence=Confidence.HIGH))
    else:
        out.append(Finding("SEC_UFW_UNKNOWN", "Firewall state could not be read", Severity.INFO,
                           "Reading UFW status needs root; Linux Doctor does not run as root.", {"detail": r.stderr[:150]},
                           confidence=Confidence.LOW))
    # Pending updates
    r = runner.run("apt_upgradable", timeout=s.scan_timeout)
    if r.ok:
        n = len([l for l in r.stdout.splitlines() if "/" in l])
        if n:
            out.append(Finding("SEC_UPDATES", f"{n} package update(s) available", Severity.ATTENTION if n > 20 else Severity.INFO,
                               "Updates may include security fixes. This list reflects the last 'apt update'.",
                               {"count": n}, "Review with 'apt list --upgradable', then update.", Confidence.MEDIUM, "apt"))
    # SSH server
    r = runner.run("ssh_active", timeout=5)
    if r.ok and r.stdout.strip() == "active":
        cfg = Path("/etc/ssh/sshd_config")
        root_login = None
        try:
            for line in cfg.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0].lower() == "permitrootlogin":
                    root_login = parts[1]
        except OSError:
            pass
        sev = Severity.ATTENTION if root_login == "yes" else Severity.INFO
        out.append(Finding("SEC_SSH_ACTIVE", "SSH server is running", sev,
                           "Fine if you use it. Make sure password logins and root login are restricted.",
                           {"PermitRootLogin": root_login or "not readable / default"}, "", Confidence.MEDIUM, "ss"))
    # File permissions
    probes = []
    for p, bad in ((Path("/etc/shadow"), stat.S_IROTH | stat.S_IWOTH),):
        try:
            if p.stat().st_mode & bad:
                probes.append(f"{p} is accessible to everyone")
        except OSError:
            pass
    ssh = Path.home() / ".ssh"
    if ssh.is_dir():
        try:
            for f in ssh.iterdir():
                if f.name.startswith("id_") and not f.name.endswith(".pub") and f.is_file() \
                        and f.stat().st_mode & 0o077:
                    probes.append(f"private key {f.name} has loose permissions")
        except OSError:
            pass
    if probes:
        out.append(Finding("SEC_PERMS", "Risky file permissions found", Severity.ACTION,
                           "Private keys and /etc/shadow must not be readable by others.",
                           {"items": probes}, "For keys: chmod 600 ~/.ssh/<keyfile>", Confidence.HIGH, "chmod"))
    else:
        out.append(Finding("SEC_PERMS_OK", "Checked file permissions look fine", Severity.OK,
                           "Only a few well-known files were checked."))
    return out
