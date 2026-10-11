from __future__ import annotations

import threading
import time
from collections.abc import Callable

from ..config import Settings, load_settings
from ..core.models import Finding, RunResult, Severity
from . import network, performance, security, services, storage

Check = Callable[..., list[Finding]]

WORKFLOWS: dict[str, tuple[str, list[Check]]] = {
    "slow_computer": ("Slow computer", [performance.check_cpu, performance.check_memory]),
    "low_storage": ("Low storage", [storage.check_filesystems, storage.check_cleanup]),
    "internet": ("Internet not working", [network.check_connectivity]),
    "failed_services": ("Failed services & logs", [services.check_failed_units, services.check_journal_errors]),
    "security_basic": ("Basic security hygiene", [security.check_security, network.check_listening]),
    "full_health": ("Full health check", [performance.check_cpu, performance.check_memory,
                                           storage.check_filesystems, network.check_connectivity,
                                           services.check_failed_units, security.check_security]),
}


def run_workflow(name: str, settings: Settings | None = None,
                 cancel: threading.Event | None = None,
                 progress: Callable[[str], None] | None = None) -> RunResult:
    if name not in WORKFLOWS:
        raise ValueError(f"unknown workflow: {name}")
    s = settings or load_settings()
    title, checks = WORKFLOWS[name]
    res = RunResult(name, title)
    for chk in checks:
        if cancel and cancel.is_set():
            res.status = "cancelled"
            break
        if progress:
            progress(chk.__name__)
        try:
            res.findings.extend(chk(s))
        except Exception as exc:  # noqa: BLE001 - isolate failed checks and report them in the result
            res.errors.append(f"{chk.__name__}: {type(exc).__name__}: {exc}")
            res.findings.append(Finding("CHECK_FAILED", f"Check '{chk.__name__}' could not complete", Severity.INFO,
                                        "The check raised an error; other checks still ran.", {"error": str(exc)[:200]}))
    res.finished_at = time.time()
    return res
