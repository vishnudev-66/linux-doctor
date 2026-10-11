"""Paths and validated settings. Everything private (dir 0700, files 0600)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import APP_NAME


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or (Path.home() / default))


def ensure_private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def data_dir() -> Path:
    return ensure_private_dir(_xdg("XDG_DATA_HOME", ".local/share") / APP_NAME)


def config_dir() -> Path:
    return ensure_private_dir(_xdg("XDG_CONFIG_HOME", ".config") / APP_NAME)


def reports_dir() -> Path:
    return ensure_private_dir(data_dir() / "reports")


def backups_dir() -> Path:
    return ensure_private_dir(data_dir() / "backups")


# name: (min, max) for numeric fields
_LIMITS = {
    "refresh_seconds": (1, 60), "disk_warn_pct": (50, 99), "disk_crit_pct": (51, 100),
    "disk_min_free_gb": (0, 10000), "cpu_warn_pct": (50, 100), "mem_avail_warn_pct": (1, 50),
    "swap_warn_pct": (10, 100), "retention_days": (1, 3650), "scan_timeout": (5, 300),
    "max_log_lines": (10, 1000),
}


@dataclass
class Settings:
    refresh_seconds: int = 3
    disk_warn_pct: float = 80.0
    disk_crit_pct: float = 90.0
    disk_min_free_gb: float = 5.0
    cpu_warn_pct: float = 85.0
    mem_avail_warn_pct: float = 10.0
    swap_warn_pct: float = 50.0
    retention_days: int = 90
    history_enabled: bool = True
    scan_timeout: int = 20
    max_log_lines: int = 200
    theme: str = "system"            # system | light | dark
    ai_provider: str = "none"        # none | ollama
    ai_endpoint: str = "http://127.0.0.1:11434"
    ai_model: str = "llama3.2"
    ai_allow_remote: bool = False    # must be True to talk to a non-loopback host
    redact_reports: bool = True
    network_test_host: str = "1.1.1.1"
    network_test_port: int = 443
    dns_test_name: str = "example.com"

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        s = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            v, default = raw[f.name], getattr(s, f.name)
            try:
                if isinstance(default, bool):
                    v = bool(v) if isinstance(v, bool) else default
                elif isinstance(default, int):
                    v = int(v)
                elif isinstance(default, float):
                    v = float(v)
                else:
                    v = str(v)[:200]
            except (TypeError, ValueError):
                continue
            if f.name in _LIMITS:
                lo, hi = _LIMITS[f.name]
                v = max(lo, min(hi, v))
            setattr(s, f.name, v)
        if s.theme not in ("system", "light", "dark"):
            s.theme = "system"
        if s.ai_provider not in ("none", "ollama"):
            s.ai_provider = "none"
        if s.disk_crit_pct <= s.disk_warn_pct:
            s.disk_crit_pct = min(100.0, s.disk_warn_pct + 5)
        if not 1 <= s.network_test_port <= 65535:
            s.network_test_port = 443
        return s


def load_settings() -> Settings:
    path = config_dir() / "settings.json"
    try:
        return Settings.from_dict(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        return Settings()


def save_settings(s: Settings) -> None:
    d = config_dir()
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".settings-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(s), fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, d / "settings.json")
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
