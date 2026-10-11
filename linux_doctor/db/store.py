"""SQLite history store.

Safety properties: private dir/file permissions (0700/0600), parameterized SQL only,
foreign keys + CHECK constraints, WAL + secure_delete, integrity check on open,
evidence redacted before it is written, retention purge, online backup API.
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import backups_dir, data_dir
from ..core.models import RunResult
from ..core.redact import redact, redact_json

SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diagnostic_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow TEXT NOT NULL, title TEXT NOT NULL,
  started_at REAL NOT NULL, finished_at REAL NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('completed','failed','cancelled')),
  summary TEXT NOT NULL DEFAULT '', reviewed INTEGER NOT NULL DEFAULT 0 CHECK(reviewed IN (0,1)));
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES diagnostic_runs(id) ON DELETE CASCADE,
  code TEXT NOT NULL, severity TEXT NOT NULL CHECK(severity IN ('ok','info','attention','action')),
  title TEXT NOT NULL, explanation TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}',
  recommendation TEXT NOT NULL DEFAULT '', confidence TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','reviewed','resolved','dismissed')));
CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id);
CREATE TABLE IF NOT EXISTS system_metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at REAL NOT NULL,
  metric_name TEXT NOT NULL, metric_value REAL NOT NULL, unit TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_metrics_time ON system_metrics(recorded_at);
CREATE TABLE IF NOT EXISTS action_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL,
  approved INTEGER NOT NULL CHECK(approved IN (0,1)), result TEXT NOT NULL DEFAULT '',
  verified TEXT NOT NULL DEFAULT '');
"""


class DatabaseError(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else data_dir() / "history.db"
        old = os.umask(0o077)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        finally:
            os.umask(old)
        self._conn.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA secure_delete=ON")
            self._conn.execute("PRAGMA trusted_schema=OFF")
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            if row[0] != "ok":
                raise DatabaseError(f"integrity check failed: {row[0]}")
            self._conn.executescript(_SCHEMA)
            self._conn.execute("INSERT OR IGNORE INTO schema_meta(key,value) VALUES('version',?)", (str(SCHEMA_VERSION),))
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(f"database unusable: {exc}") from exc
        for suffix in ("-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                os.chmod(p, 0o600)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    # ---- runs -----------------------------------------------------------
    def save_run(self, res: RunResult) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO diagnostic_runs(workflow,title,started_at,finished_at,status,summary) VALUES(?,?,?,?,?,?)",
                (res.workflow, res.title, res.started_at, res.finished_at or time.time(), res.status, res.summary))
            rid = cur.lastrowid
            for f in res.findings:
                c.execute(
                    "INSERT INTO findings(run_id,code,severity,title,explanation,evidence_json,recommendation,confidence,status)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (rid, f.code, f.severity.name.lower(), redact(f.title), redact(f.explanation),
                     redact_json(f.evidence), redact(f.recommendation), f.confidence.value, f.status))
        res.run_id = rid
        return rid

    def list_runs(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM diagnostic_runs ORDER BY started_at DESC LIMIT ?",
                                  (max(1, min(limit, 1000)),)).fetchall()
        return [dict(r) for r in rows]

    def get_findings(self, run_id: int) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM findings WHERE run_id=? ORDER BY id", (int(run_id),)).fetchall()
        return [dict(r) for r in rows]

    def mark_reviewed(self, run_id: int) -> None:
        self._conn.execute("UPDATE diagnostic_runs SET reviewed=1 WHERE id=?", (int(run_id),))

    def set_finding_status(self, finding_id: int, status: str) -> None:
        if status not in ("new", "reviewed", "resolved", "dismissed"):
            raise ValueError("invalid status")
        self._conn.execute("UPDATE findings SET status=? WHERE id=?", (status, int(finding_id)))

    # ---- metrics / actions ---------------------------------------------
    def record_metric(self, name: str, value: float, unit: str) -> None:
        self._conn.execute("INSERT INTO system_metrics(recorded_at,metric_name,metric_value,unit) VALUES(?,?,?,?)",
                           (time.time(), name, float(value), unit))

    def log_action(self, action: str, approved: bool, result: str = "", verified: str = "") -> None:
        self._conn.execute("INSERT INTO action_history(at,action,approved,result,verified) VALUES(?,?,?,?,?)",
                           (time.time(), redact(action), int(approved), redact(result)[:500], verified[:200]))

    # ---- retention / privacy -------------------------------------------
    def purge_older_than(self, days: int) -> int:
        cutoff = time.time() - max(1, int(days)) * 86400
        with self._tx() as c:
            n = c.execute("DELETE FROM diagnostic_runs WHERE started_at < ?", (cutoff,)).rowcount
            c.execute("DELETE FROM system_metrics WHERE recorded_at < ?", (cutoff,))
            c.execute("DELETE FROM action_history WHERE at < ?", (cutoff,))
        return n

    def clear_all(self) -> None:
        with self._tx() as c:
            for t in ("findings", "diagnostic_runs", "system_metrics", "action_history"):
                c.execute(f"DELETE FROM {t}")  # fixed table names, no user input
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._conn.execute("VACUUM")

    def backup(self, dest: str | Path | None = None) -> Path:
        dest = Path(dest) if dest else backups_dir() / time.strftime("history-%Y%m%d-%H%M%S.db")
        old = os.umask(0o077)
        try:
            out = sqlite3.connect(dest)
            with out:
                self._conn.backup(out)
            out.close()
        finally:
            os.umask(old)
        os.chmod(dest, 0o600)
        return dest
