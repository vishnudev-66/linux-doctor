"""Command-line interface (also the headless / server-friendly entry point)."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import load_settings
from .core import sysinfo
from .core.models import Severity
from .db.store import DatabaseError, Store
from .diagnostics.engine import WORKFLOWS, run_workflow
from .learn.commands import explain_command
from .reports.export import write_report


def _run_to_dict(store: Store, run_id: int) -> dict:
    run = next((r for r in store.list_runs(1000) if r["id"] == run_id), None)
    if not run:
        raise SystemExit(f"no such run: {run_id}")
    fs = []
    for f in store.get_findings(run_id):
        f["evidence"] = json.loads(f.pop("evidence_json") or "{}")
        f["learn"] = ""
        fs.append(f)
    run["findings"], run["errors"] = fs, []
    return run


def _print_result(res) -> None:
    print(f"\n== {res.title} - {res.summary} ==")
    for f in sorted(res.findings, key=lambda x: -x.severity):
        print(f" [{f.severity.name:9}] {f.title}  (confidence: {f.confidence.value})")
        if f.recommendation and f.severity >= Severity.ATTENTION:
            print(f"             -> {f.recommendation}")
    for e in res.errors:
        print(f" ! {e}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="linux-doctor", description="Local-first Linux diagnostics (read-only).")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="launch the desktop app")
    sub.add_parser("info", help="show system information")
    sub.add_parser("workflows", help="list diagnostic workflows")
    sc = sub.add_parser("scan", help="run a diagnostic workflow")
    sc.add_argument("workflow", choices=sorted(WORKFLOWS))
    sc.add_argument("--json", action="store_true")
    sc.add_argument("--no-save", action="store_true", help="do not store the result in history")
    sub.add_parser("history", help="list saved scans")
    ex = sub.add_parser("export", help="export a saved scan")
    ex.add_argument("run_id", type=int)
    ex.add_argument("--format", choices=["html", "json", "csv"], default="html")
    ex.add_argument("--no-redact", action="store_true")
    ec = sub.add_parser("explain", help="explain a command offline (never executes it)")
    ec.add_argument("command", help="quote the whole command, e.g. \"sudo apt autoremove\"")
    sub.add_parser("backup", help="create a database backup")
    pg = sub.add_parser("purge", help="delete history")
    pg.add_argument("--older-than-days", type=int)
    pg.add_argument("--all", action="store_true")
    sub.add_parser("selfcheck", help="verify installation and database health")
    a = p.parse_args(argv)
    s = load_settings()

    if a.cmd in (None, "gui"):
        try:
            from .ui.app import run_gui
        except ImportError as exc:
            print(f"GUI unavailable ({exc}). Install with: pip install 'linux-doctor[gui]'. CLI still works: linux-doctor --help",
                  file=sys.stderr)
            return 2
        return run_gui()
    if a.cmd == "info":
        for k, v in sysinfo.collect().items():
            print(f"{k:20} {v}")
        return 0
    if a.cmd == "workflows":
        for k, (t, _) in WORKFLOWS.items():
            print(f"{k:16} {t}")
        return 0
    if a.cmd == "scan":
        res = run_workflow(a.workflow, s)
        if s.history_enabled and not a.no_save:
            try:
                st = Store()
                st.save_run(res)
                st.purge_older_than(s.retention_days)
                st.close()
            except DatabaseError as exc:
                print(f"warning: history not saved: {exc}", file=sys.stderr)
        if a.json:
            print(json.dumps(res.to_dict(), indent=2, default=str))
        else:
            _print_result(res)
        return 1 if res.worst >= Severity.ACTION else 0
    if a.cmd == "explain":
        r = explain_command(a.command)
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1
    try:
        st = Store()
    except DatabaseError as exc:
        print(f"database error: {exc}", file=sys.stderr)
        return 3
    try:
        if a.cmd == "history":
            for r in st.list_runs(50):
                print(f"{r['id']:5} {r['title']:28} {r['status']:10} {r['summary']}")
        elif a.cmd == "export":
            print(write_report(_run_to_dict(st, a.run_id), a.format, redact=not a.no_redact))
        elif a.cmd == "backup":
            print(st.backup())
        elif a.cmd == "purge":
            if a.all:
                st.clear_all(); print("All history deleted.")
            elif a.older_than_days:
                print(f"Deleted {st.purge_older_than(a.older_than_days)} run(s).")
            else:
                print("Specify --all or --older-than-days N", file=sys.stderr); return 2
        elif a.cmd == "selfcheck":
            print(f"linux-doctor {__version__}\ndatabase: OK ({st.path})")
            print(f"workflows: {len(WORKFLOWS)}")
    finally:
        st.close()
    return 0
