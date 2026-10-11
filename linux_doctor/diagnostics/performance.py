from __future__ import annotations

import time

import psutil

from ..config import Settings
from ..core.models import Confidence, Finding, Severity


def top_processes(limit: int = 10, sample: float = 0.6) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "username"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(sample)
    rows = []
    for p in procs:
        try:
            rows.append({"pid": p.pid, "name": p.info["name"] or "?", "user": p.info["username"] or "?",
                         "cpu": p.cpu_percent(None), "mem": round(p.memory_percent(), 1)})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return rows


def check_cpu(s: Settings, **_) -> list[Finding]:
    samples = [psutil.cpu_percent(interval=0.5) for _ in range(3)]
    avg = sum(samples) / len(samples)
    top = sorted(top_processes(), key=lambda r: r["cpu"], reverse=True)[:5]
    ev = {"samples_pct": samples, "average_pct": round(avg, 1), "top_cpu_processes": top}
    if avg >= s.cpu_warn_pct:
        return [Finding("PERF_CPU_HIGH", "CPU usage is high over the sample window", Severity.ATTENTION,
                        "CPU was high during a ~1.5 s measurement. A short spike is normal; repeat the check to see if it is sustained.",
                        ev, "Repeat the scan, then inspect the top processes listed in the evidence.",
                        Confidence.LOW, "top")]
    return [Finding("PERF_CPU_OK", "CPU usage is normal", Severity.OK, evidence=ev, confidence=Confidence.MEDIUM)]


def check_memory(s: Settings, **_) -> list[Finding]:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    avail_pct = vm.available * 100 / vm.total
    ev = {"total_gb": round(vm.total / 1024**3, 2), "available_pct": round(avail_pct, 1),
          "swap_used_pct": round(sw.percent, 1), "swap_total_gb": round(sw.total / 1024**3, 2)}
    out = []
    if avail_pct < s.mem_avail_warn_pct:
        top = sorted(top_processes(sample=0.1), key=lambda r: r["mem"], reverse=True)[:5]
        ev["top_memory_processes"] = top
        out.append(Finding("PERF_MEM_LOW", "Available memory is low", Severity.ATTENTION,
                           "Linux uses spare RAM for caching, so 'used' RAM alone is not a problem. Low *available* memory is.",
                           ev, "Close memory-heavy applications listed in the evidence.", Confidence.MEDIUM, "free"))
    if sw.total and sw.percent >= s.swap_warn_pct:
        out.append(Finding("PERF_SWAP_HEAVY", "Swap usage is heavy", Severity.ATTENTION,
                           "Heavy swapping can make the computer feel slow. It does not by itself prove a hardware fault.",
                           ev, "Check memory consumers and consider closing applications.", Confidence.MEDIUM, "free"))
    if not out:
        out.append(Finding("PERF_MEM_OK", "Memory availability is normal", Severity.OK, evidence=ev))
    return out
