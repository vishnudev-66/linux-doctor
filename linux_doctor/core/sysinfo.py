from __future__ import annotations

import os
import platform
import socket
import time
from pathlib import Path

import psutil


def _os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip().strip('"')
    except OSError:
        pass
    return out


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def collect(include_hostname: bool = False) -> dict:
    osr = _os_release()
    vm = psutil.virtual_memory()
    boot = psutil.boot_time()
    info = {
        "distribution": osr.get("PRETTY_NAME", platform.system()),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", "unknown"),
        "python": platform.python_version(),
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
        "uptime_hours": round((time.time() - boot) / 3600, 1),
        "cpu_model": _cpu_model(),
        "cpu_logical_cores": psutil.cpu_count(logical=True),
        "cpu_physical_cores": psutil.cpu_count(logical=False),
        "ram_total_gb": round(vm.total / 1024**3, 2),
    }
    if include_hostname:
        info["hostname"] = socket.gethostname()
    try:
        b = psutil.sensors_battery()
        if b:
            info["battery"] = f"{b.percent:.0f}% ({'charging' if b.power_plugged else 'on battery'})"
    except Exception:  # noqa: BLE001
        pass
    return info
