from __future__ import annotations

from ..config import Settings
from ..core import runner
from ..core.models import Confidence, Finding, Severity


def check_failed_units(s: Settings, **_) -> list[Finding]:
    r = runner.run("failed_units", timeout=s.scan_timeout)
    if r.missing:
        return [Finding("SVC_NO_SYSTEMD", "systemd tools not available", Severity.INFO,
                        "Service inspection requires systemd; this feature is unsupported here.")]
    if not r.ok:
        return [Finding("SVC_ERR", "Could not query services", Severity.INFO, r.stderr[:200])]
    units = [l.split()[0] for l in r.stdout.splitlines() if l.strip()]
    if not units:
        return [Finding("SVC_OK", "No failed systemd units", Severity.OK, confidence=Confidence.HIGH)]
    return [Finding("SVC_FAILED", f"{len(units)} failed unit(s)", Severity.ATTENTION,
                    "A failed unit did not start or exited abnormally. Logs usually say why.",
                    {"units": units[:30]}, "Inspect each unit with: systemctl status <unit>", Confidence.HIGH, "systemctl")]


def check_journal_errors(s: Settings, **_) -> list[Finding]:
    r = runner.run("journal_errors", n=s.max_log_lines, timeout=s.scan_timeout)
    if r.missing or not r.ok:
        return [Finding("LOG_UNAVAILABLE", "Journal not readable", Severity.INFO,
                        "Not in the 'systemd-journal' or 'adm' group, or journald is absent.", {"detail": r.stderr[:200]})]
    lines = [l for l in r.stdout.splitlines() if l and not l.startswith("--")]
    if not lines:
        return [Finding("LOG_OK", "No error-priority messages this boot", Severity.OK)]
    return [Finding("LOG_ERRORS", f"{len(lines)} error-priority journal entries this boot", Severity.INFO,
                    "Errors are common and often harmless. Look for repeated messages.",
                    {"latest": lines[-15:]}, "Use 'journalctl -p err -b' for the full list.", Confidence.LOW, "journalctl")]
