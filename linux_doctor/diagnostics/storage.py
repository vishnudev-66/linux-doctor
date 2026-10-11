from __future__ import annotations

import heapq
import os
import threading
import time
from pathlib import Path

import psutil

from ..config import Settings
from ..core.models import Confidence, Finding, Severity

_SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "cgroup2", "ramfs", "iso9660"}


def filesystems() -> list[dict]:
    rows = []
    for part in psutil.disk_partitions(all=False):
        if part.fstype in _SKIP_FS or part.mountpoint.startswith(("/snap", "/boot/efi")):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        rows.append({"mount": part.mountpoint, "fstype": part.fstype, "total_gb": round(u.total / 1024**3, 1),
                     "free_gb": round(u.free / 1024**3, 1), "used_pct": u.percent})
    return rows


def check_filesystems(s: Settings, **_) -> list[Finding]:
    out = []
    for fs in filesystems():
        sev = Severity.OK
        if fs["used_pct"] >= s.disk_crit_pct or fs["free_gb"] < s.disk_min_free_gb / 2:
            sev = Severity.ACTION
        elif fs["used_pct"] >= s.disk_warn_pct or fs["free_gb"] < s.disk_min_free_gb:
            sev = Severity.ATTENTION
        out.append(Finding(f"STOR_{'LOW' if sev else 'OK'}", f"Filesystem {fs['mount']}: {fs['used_pct']}% used, {fs['free_gb']} GB free",
                           sev, "Checked both percentage and absolute free space." if sev else "",
                           fs, "Run the directory analyzer to find what uses space." if sev else "",
                           Confidence.HIGH, "df"))
    return out or [Finding("STOR_NONE", "No regular filesystems could be inspected", Severity.INFO)]


def dir_sizes(root: str | Path, cancel: threading.Event | None = None, max_entries: int = 200_000) -> list[tuple[str, int]]:
    """Sizes of the immediate children of root. Never follows symlinks, stays on one device."""
    root = Path(root)
    try:
        dev = root.stat().st_dev
        children = list(os.scandir(root))
    except OSError:
        return []
    results, seen = [], 0
    for ch in children:
        if cancel and cancel.is_set():
            break
        total = 0
        try:
            if ch.is_symlink():
                continue
            if ch.is_file(follow_symlinks=False):
                total = ch.stat(follow_symlinks=False).st_size
            elif ch.is_dir(follow_symlinks=False):
                for dp, dn, fn in os.walk(ch.path, followlinks=False, onerror=lambda e: None):
                    if cancel and cancel.is_set():
                        break
                    try:
                        if os.stat(dp).st_dev != dev:
                            dn[:] = []
                            continue
                    except OSError:
                        continue
                    for f in fn:
                        seen += 1
                        if seen > max_entries:
                            break
                        try:
                            total += os.lstat(os.path.join(dp, f)).st_size
                        except OSError:
                            pass
        except OSError:
            continue
        results.append((ch.name, total))
    return sorted(results, key=lambda r: r[1], reverse=True)


def large_files(root: str | Path, min_mb: int = 500, limit: int = 20,
                cancel: threading.Event | None = None) -> list[tuple[str, int]]:
    heap: list[tuple[int, str]] = []
    root = Path(root)
    try:
        dev = root.stat().st_dev
    except OSError:
        return []
    for dp, dn, fn in os.walk(root, followlinks=False, onerror=lambda e: None):
        if cancel and cancel.is_set():
            break
        try:
            if os.stat(dp).st_dev != dev:
                dn[:] = []
                continue
        except OSError:
            continue
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if st.st_size >= min_mb * 1024**2 and not os.path.islink(p):
                heapq.heappush(heap, (st.st_size, p))
                if len(heap) > limit:
                    heapq.heappop(heap)
    return [(p, sz) for sz, p in sorted(heap, reverse=True)]


def check_cleanup(s: Settings, **_) -> list[Finding]:
    """ADVISORY ONLY - this tool never deletes anything."""
    home = Path.home()
    cands = {"User cache (~/.cache)": home / ".cache", "Trash": home / ".local/share/Trash",
             "APT package cache": Path("/var/cache/apt/archives")}
    advice = {}
    for label, p in cands.items():
        if p.is_dir():
            sz = sum(v for _, v in dir_sizes(p)) if os.access(p, os.R_OK | os.X_OK) else 0
            if sz > 200 * 1024**2:
                advice[label] = f"{sz / 1024**2:.0f} MB"
    dl = home / "Downloads"
    old = 0
    if dl.is_dir():
        cutoff = time.time() - 180 * 86400
        for e in os.scandir(dl):
            try:
                if e.is_file(follow_symlinks=False) and e.stat().st_mtime < cutoff:
                    old += e.stat().st_size
            except OSError:
                pass
        if old > 200 * 1024**2:
            advice["Downloads older than 180 days"] = f"{old / 1024**2:.0f} MB"
    if not advice:
        return [Finding("STOR_CLEAN_NONE", "No large cleanup candidates found", Severity.OK)]
    return [Finding("STOR_CLEAN_CANDIDATES", "Possible cleanup opportunities (review manually)", Severity.INFO,
                    "Sizes only. Caches are not always disposable and Linux Doctor will not delete anything for you.",
                    advice, "Review each location yourself, e.g. with 'du -sh' and your file manager.",
                    Confidence.MEDIUM, "du")]
