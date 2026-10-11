"""PySide6 desktop UI. All diagnostics run on worker threads; the UI never blocks."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QDoubleSpinBox, QFrame, QStyle, QListWidgetItem, QScrollArea)

from .. import __version__
from ..ai.assistant import explain as ai_explain
from ..ai.provider import AIUnavailable
from ..config import load_settings, save_settings
from ..core import sysinfo
from ..core.models import Severity
from ..db.store import DatabaseError, Store
from ..diagnostics import performance
from ..diagnostics.engine import WORKFLOWS, run_workflow
from ..learn.commands import LIBRARY, explain_command
from ..reports.export import write_report

COLORS = {Severity.OK: "#2e7d32", Severity.INFO: "#1565c0", Severity.ATTENTION: "#ef6c00", Severity.ACTION: "#c62828"}


class _Sig(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.sig = fn, _Sig()

    def run(self):
        try:
            self.sig.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001
            self.sig.failed.emit(f"{type(exc).__name__}: {exc}")


def _bg(fn, ok, err=None):
    j = Job(fn)
    j.sig.done.connect(ok)
    j.sig.failed.connect(err or (lambda m: QMessageBox.warning(None, "Linux Doctor", m)))
    j.setAutoDelete(False)
    _keep.append(j)
    QThreadPool.globalInstance().start(j)


_keep: list = []


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fill(t, rows):
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            t.setItem(i, j, QTableWidgetItem(str(v)))


def _page_heading(layout, title, description):
    heading = QLabel(title)
    heading.setObjectName("pageHeading")
    layout.addWidget(heading)
    help_text = QLabel(description)
    help_text.setObjectName("pageDescription")
    help_text.setWordWrap(True)
    layout.addWidget(help_text)


def _export_report(parent, result, redact, suggested_name):
    path, selected_filter = QFileDialog.getSaveFileName(
        parent, "Export report", suggested_name, "HTML (*.html);;JSON (*.json);;CSV (*.csv)"
    )
    if not path:
        return
    fmt = Path(path).suffix.lower().lstrip(".")
    if fmt not in ("html", "json", "csv"):
        fmt = {"HTML (*.html)": "html", "JSON (*.json)": "json", "CSV (*.csv)": "csv"}.get(
            selected_filter, "html"
        )
        path = f"{path}.{fmt}"
    try:
        saved = write_report(result, fmt, redact, path)
    except (OSError, ValueError) as exc:
        QMessageBox.warning(parent, "Export failed", f"Could not save the report:\n{exc}")
    else:
        QMessageBox.information(parent, "Report exported", f"Saved report to:\n{saved}")


class Dashboard(QWidget):
    def __init__(self, s, run_full_check):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(18)

        hero = QFrame()
        hero.setObjectName("dashboardHero")
        hero.setStyleSheet(
            "QFrame#dashboardHero { background-color: #12304a; border-radius: 14px; }"
        )
        hero_row = QHBoxLayout(hero)
        hero_row.setContentsMargins(24, 22, 24, 22)
        intro = QVBoxLayout()
        eyebrow = QLabel("LINUX DOCTOR  /  SYSTEM OVERVIEW")
        eyebrow.setStyleSheet("color: #8fc8ff; font-size: 10px; font-weight: bold;")
        title = QLabel("Your system, at a glance")
        title.setStyleSheet("color: white; font-size: 25px; font-weight: bold;")
        description = QLabel(
            "Clear, safe insights into your Linux system. Checks inspect and explain "
            "— they never make changes automatically."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #d6e4f0; font-size: 13px;")
        for widget in (eyebrow, title, description):
            intro.addWidget(widget)
        hero_row.addLayout(intro, 1)
        check_button = QPushButton("Run full health check")
        check_button.setMinimumHeight(44)
        check_button.setStyleSheet(
            "QPushButton { background: #65b5ff; color: #10283d; border: 0; "
            "border-radius: 8px; padding: 0 16px; font-weight: bold; }"
            "QPushButton:hover { background: #8ac8ff; }"
        )
        check_button.clicked.connect(run_full_check)
        hero_row.addWidget(check_button, 0, Qt.AlignVCenter)
        lay.addWidget(hero)

        self.cards = {}
        row = QHBoxLayout()
        row.setSpacing(12)
        for key, heading in (("CPU", "PROCESSOR"), ("Memory", "MEMORY"), ("Disk (/)", "STORAGE"),
                             ("Uptime", "UPTIME")):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setStyleSheet(
                "QFrame#metricCard { background: #ffffff; border: 1px solid #d8e2ec; "
                "border-radius: 11px; }"
            )
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(16, 14, 16, 14)
            card_lay.setSpacing(7)
            label = QLabel(heading)
            label.setStyleSheet("color: #60758a; font-size: 10px; font-weight: bold;")
            value = QLabel("--")
            value.setStyleSheet("color: #173b59; font-size: 23px; font-weight: bold;")
            detail = QLabel("Loading current reading…")
            detail.setStyleSheet("color: #60758a; font-size: 11px;")
            card_lay.addWidget(label)
            card_lay.addWidget(value)
            card_lay.addWidget(detail)
            card_lay.addStretch()
            self.cards[key] = (value, detail)
            row.addWidget(card, 1)
        lay.addLayout(row)
        reassurance = QLabel(
            "READ-ONLY BY DESIGN   ·   Run a health check for storage, network, services and security findings."
        )
        reassurance.setWordWrap(True)
        reassurance.setStyleSheet(
            "background: #edf5fc; color: #34536d; border-radius: 8px; padding: 12px; font-size: 12px;"
        )
        lay.addWidget(reassurance)
        lay.addStretch()
        self.timer = QTimer(self, timeout=self.refresh)
        self.timer.start(s.refresh_seconds * 1000)
        self.refresh()

    def refresh(self):
        vm = psutil.virtual_memory()
        du = psutil.disk_usage("/")
        values = {
            "CPU": (f"{psutil.cpu_percent():.0f}%", "Current processor usage"),
            "Memory": (f"{vm.percent:.0f}%", f"{vm.available / 1024**3:.1f} GB available"),
            "Disk (/)": (f"{du.percent:.0f}%", f"{du.free / 1024**3:.0f} GB free on /"),
            "Uptime": (f"{(__import__('time').time() - psutil.boot_time()) / 3600:.1f} h",
                       "Since the last boot"),
        }
        for key, (value, detail) in values.items():
            self.cards[key][0].setText(value)
            self.cards[key][1].setText(detail)


class SysInfoPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        _page_heading(lay, "System information", "A summary of your operating system and hardware.")
        self.t = _table(["Property", "Value"])
        lay.addWidget(self.t)
        _bg(sysinfo.collect, lambda d: _fill(self.t, list(d.items())))


class ProcessPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        _page_heading(lay, "Top processes", "Inspect CPU and memory use. Linux Doctor does not stop or control processes.")
        b = QPushButton("Refresh process list")
        b.setToolTip("Read the current top CPU and memory consumers.")
        b.clicked.connect(self.load)
        self.t = _table(["PID", "Name", "User", "CPU %", "Mem %"])
        lay.addWidget(b)
        lay.addWidget(self.t)
        self.load()

    def load(self):
        _bg(lambda: sorted(performance.top_processes(limit=50), key=lambda r: r["cpu"], reverse=True)[:50],
            lambda rows: _fill(self.t, [(r["pid"], r["name"], r["user"], r["cpu"], r["mem"]) for r in rows]))


class DiagPage(QWidget):
    def __init__(self, s, store):
        super().__init__()
        self.s, self.store, self.result, self.cancel = s, store, None, threading.Event()
        lay = QVBoxLayout(self)
        _page_heading(lay, "Diagnostics", "Choose a guided check. Results are advisory; the app never applies repairs.")
        top = QHBoxLayout()
        self.combo = QComboBox()
        for k, (t, _) in WORKFLOWS.items():
            self.combo.addItem(t, k)
        self.combo.setToolTip("Select a workflow to inspect a particular area of your system.")
        self.run_b, self.stop_b = QPushButton("Run selected check"), QPushButton("Cancel check")
        self.run_b.setToolTip("Start the selected read-only diagnostic workflow.")
        self.stop_b.setToolTip("Ask the current diagnostic workflow to stop.")
        self.stop_b.setEnabled(False)
        self.exp_b = QPushButton("Export this result…")
        self.exp_b.setToolTip("Save the latest diagnostic result as HTML, JSON, or CSV.")
        self.exp_b.setEnabled(False)
        self.ai_b = QPushButton("Explain finding with AI…")
        self.ai_b.setToolTip("Get an optional explanation of the selected finding. The AI response is untrusted.")
        self.ai_b.setEnabled(False)
        self.selection_note = QLabel("Select a finding below to read its explanation and evidence.")
        self.selection_note.setObjectName("pageDescription")
        self.prog = QProgressBar()
        self.prog.setRange(0, 0)
        self.prog.hide()
        for w in (self.combo, self.run_b, self.stop_b, self.exp_b, self.ai_b):
            top.addWidget(w)
        lay.addLayout(top)
        lay.addWidget(self.prog)
        sp = QSplitter(Qt.Vertical)
        self.t = _table(["Severity", "Finding", "Confidence"])
        self.detail = QPlainTextEdit(readOnly=True)
        self.detail.setPlaceholderText("Diagnostic details will appear here after a check completes.")
        sp.addWidget(self.t)
        sp.addWidget(self.detail)
        lay.addWidget(sp)
        lay.addWidget(self.selection_note)
        self.run_b.clicked.connect(self.start)
        self.stop_b.clicked.connect(self._cancel)
        self.exp_b.clicked.connect(self.export)
        self.ai_b.clicked.connect(self.ask_ai)
        self.t.itemSelectionChanged.connect(self.show_detail)

    def _cancel(self):
        self.cancel.set()
        self.stop_b.setEnabled(False)
        self.selection_note.setText("Stopping the check…")

    def start(self):
        self.cancel = threading.Event()
        self.prog.show()
        self.run_b.setEnabled(False)
        self.stop_b.setEnabled(True)
        self.exp_b.setEnabled(False)
        self.ai_b.setEnabled(False)
        self.t.setRowCount(0)
        self.detail.clear()
        self.selection_note.setText("Check running. You can cancel it at any time.")
        name, c = self.combo.currentData(), self.cancel
        _bg(lambda: run_workflow(name, self.s, c), self.finished, self._err)

    def _err(self, m):
        self.prog.hide(); self.run_b.setEnabled(True); self.stop_b.setEnabled(False)
        self.selection_note.setText("The check could not complete. See the error for details.")
        QMessageBox.warning(self, "Linux Doctor", m)

    def finished(self, res):
        self.prog.hide(); self.run_b.setEnabled(True); self.stop_b.setEnabled(False)
        self.result = res
        self.exp_b.setEnabled(True)
        if self.s.history_enabled and self.store:
            try:
                self.store.save_run(res)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "History not saved", f"The check completed, but history could not be saved:\n{exc}")
        fs = sorted(res.findings, key=lambda f: -f.severity)
        self._fs = fs
        _fill(self.t, [(f.severity.label, f.title, f.confidence.value) for f in fs])
        for i, f in enumerate(fs):
            self.t.item(i, 0).setForeground(QColor(COLORS[f.severity]))
        self.detail.setPlainText(f"{res.title}: {res.summary}" + ("  (cancelled)" if res.status == "cancelled" else ""))
        self.selection_note.setText(
            f"{len(fs)} finding(s) · {res.summary} Select a row for recommendations and evidence."
        )
        if res.errors:
            QMessageBox.warning(self, "Some checks could not complete", "\n".join(res.errors))

    def _sel(self):
        r = self.t.currentRow()
        return self._fs[r] if self.result and 0 <= r < len(self._fs) else None

    def show_detail(self):
        f = self._sel()
        self.ai_b.setEnabled(f is not None and self.s.ai_provider != "none")
        if f:
            self.detail.setPlainText(f"{f.title}\n\nWhat this means: {f.explanation or '-'}\n\nRecommendation: "
                                     f"{f.recommendation or '-'}\n\nEvidence:\n{json.dumps(f.evidence, indent=2, default=str)}")
            self.selection_note.setText(
                "AI explanations are optional and treated as untrusted. Suggested commands are never run automatically."
            )

    def export(self):
        if not self.result:
            return
        _export_report(self, self.result.to_dict(), self.s.redact_reports, "linux-doctor-report.html")

    def ask_ai(self):
        f = self._sel()
        if not f:
            return
        if self.s.ai_provider == "none":
            QMessageBox.information(self, "AI", "AI is disabled. Enable a provider in Settings (local Ollama recommended).")
            return
        if QMessageBox.question(self, "Send to AI",
                                f"A redacted summary of this finding will be sent to {self.s.ai_endpoint}. Continue?") != QMessageBox.Yes:
            return
        ctx = json.dumps(f.to_dict(), default=str)
        self.detail.appendPlainText("\n\nAsking AI...")
        _bg(lambda: ai_explain(self.s, "Explain this finding for a beginner.", ctx), self._show_ai,
            lambda m: self.detail.appendPlainText(f"\nAI unavailable: {m}\nBasic diagnostics are unaffected."))

    def _show_ai(self, e):
        txt = [f"\n--- AI explanation (untrusted; verify before acting) ---\n{e.summary}"]
        for title, items in (("Observations", e.observations), ("Possible causes (hypotheses)", e.possible_causes),
                             ("Limitations", e.limitations)):
            if items:
                txt.append(f"\n{title}:\n" + "\n".join(f" - {i}" for i in items))
        if e.next_steps:
            txt.append("\nSuggested commands (NOT run by Linux Doctor):")
            for st in e.next_steps:
                tag = "read-only" if st["risk"] == "read_only" else "REVIEW CAREFULLY"
                txt.append(f" - {st['description']}\n     {st['command']}   [{tag}]")
        self.detail.appendPlainText("\n".join(txt))


class HistoryPage(QWidget):
    def __init__(self, s, store):
        super().__init__()
        self.s, self.store = s, store
        lay = QVBoxLayout(self)
        _page_heading(lay, "Reports & history", "Review previous scans, export a report, or manage your private history.")
        row = QHBoxLayout()
        actions = (
            ("Refresh history", self.load, "Reload saved diagnostic scans."),
            ("Export selected report…", self.export, "Save the selected scan as HTML, JSON, or CSV."),
            ("Back up history", self.backup, "Create a private-permission copy of the history database."),
            ("Delete all history…", self.clear, "Permanently erase saved scans after confirmation."),
        )
        for label, fn, tooltip in actions:
            b = QPushButton(label); b.setToolTip(tooltip); b.clicked.connect(fn); row.addWidget(b)
        lay.addLayout(row)
        self.t = _table(["ID", "Workflow", "Status", "Summary"])
        self.t.setToolTip("Select a scan before exporting its report.")
        lay.addWidget(self.t)
        self.load()

    def load(self):
        if self.store:
            _fill(self.t, [(r["id"], r["title"], r["status"], r["summary"]) for r in self.store.list_runs(200)])
        else:
            self.t.setRowCount(0)

    def export(self):
        if not self.store or self.t.currentRow() < 0:
            QMessageBox.information(self, "Select a scan", "Select a saved scan before exporting its report.")
            return
        run_id = int(self.t.item(self.t.currentRow(), 0).text())
        run = next((r for r in self.store.list_runs(1000) if r["id"] == run_id), None)
        if run is None:
            QMessageBox.warning(self, "Scan not found", "That scan is no longer in history. Refresh the list and try again.")
            self.load()
            return
        findings = []
        for finding in self.store.get_findings(run_id):
            findings.append({
                "code": finding["code"],
                "severity": finding["severity"],
                "confidence": finding["confidence"],
                "title": finding["title"],
                "explanation": finding["explanation"],
                "recommendation": finding["recommendation"],
                "evidence": json.loads(finding["evidence_json"] or "{}"),
            })
        result = {
            "workflow": run["workflow"], "title": run["title"], "status": run["status"],
            "started_at": run["started_at"], "finished_at": run["finished_at"],
            "summary": run["summary"], "errors": [], "findings": findings,
        }
        _export_report(self, result, self.s.redact_reports, f"linux-doctor-{run_id}.html")

    def clear(self):
        if self.store and QMessageBox.question(self, "Delete", "Permanently delete ALL history?") == QMessageBox.Yes:
            self.store.clear_all(); self.load()
        elif not self.store:
            QMessageBox.warning(self, "History unavailable", "History storage is unavailable in this session.")

    def backup(self):
        if self.store:
            try:
                saved = self.store.backup()
            except OSError as exc:
                QMessageBox.warning(self, "Backup failed", f"Could not back up history:\n{exc}")
            else:
                QMessageBox.information(self, "Backup complete", f"Saved (private permissions):\n{saved}")
        else:
            QMessageBox.warning(self, "History unavailable", "History storage is unavailable in this session.")


class AssistantPage(QWidget):
    def __init__(self, s):
        super().__init__()
        self.s = s
        lay = QVBoxLayout(self)
        _page_heading(lay, "AI assistant & command explainer",
                      "Explain a Linux command offline, or optionally ask a configured AI provider about a finding. "
                      "Commands are never executed.")
        lay.addWidget(QLabel("Explain a command or terminal error"))
        self.inp = QLineEdit(placeholderText="e.g. sudo apt autoremove")
        self.inp.setClearButtonEnabled(True)
        self.inp.returnPressed.connect(self.go)
        b = QPushButton("Explain command offline")
        b.setToolTip("Use the built-in command library. This works without an AI provider.")
        b.clicked.connect(self.go)
        self.out = QPlainTextEdit(readOnly=True)
        for w in (self.inp, b, self.out):
            lay.addWidget(w)

    def go(self):
        r = explain_command(self.inp.text())
        if not r["ok"]:
            self.out.setPlainText(r["message"]); return
        lines = [f"Command: {r['command']}"]
        if r["library"]:
            lines += [f"Purpose: {r['library']['purpose']}", f"Syntax: {r['library']['syntax']}", f"Notes: {r['library']['notes']}"]
        else:
            lines.append("Not in the built-in library. Try: man " + r["command"])
        if r["warning"]:
            lines.append(f"WARNING: this command {r['warning']}")
        lines += [f"Note: {n}" for n in r["notes"]]
        self.out.setPlainText("\n".join(lines))


class LearnPage(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        _page_heading(outer, "Learn Linux", "Browse the offline command library and read safe, plain-language explanations.")
        lay = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.addItems(sorted(LIBRARY))
        self.lst.setToolTip("Choose a command to see its purpose, syntax, and cautions.")
        self.view = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.lst, 1)
        lay.addWidget(self.view, 3)
        outer.addLayout(lay)
        self.lst.currentTextChanged.connect(lambda n: self.view.setPlainText(
            "\n".join(f"{k.title()}: {v}" for k, v in LIBRARY.get(n, {}).items())))


class SettingsPage(QWidget):
    def __init__(self, s, apply):
        super().__init__()
        self.s, self.apply = s, apply
        outer = QVBoxLayout(self)
        _page_heading(outer, "Settings", "Adjust diagnostic thresholds, privacy, refresh timing, and optional AI. Changes are saved locally.")
        content = QWidget()
        f = QFormLayout(content)
        f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.w = {}
        specs = (
            ("disk_warn_pct", "Disk warning threshold", "f", 50, 99, "%", "Warn when disk use reaches this percentage."),
            ("disk_crit_pct", "Disk critical threshold", "f", 51, 100, "%", "Mark disk use critical at this percentage."),
            ("disk_min_free_gb", "Minimum free disk space", "f", 0, 10000, " GB", "Also warn when free space falls below this amount."),
            ("cpu_warn_pct", "CPU warning threshold", "f", 50, 100, "%", "Warn when sustained CPU use reaches this percentage."),
            ("mem_avail_warn_pct", "Low available memory threshold", "f", 1, 50, "%", "Warn when available memory falls below this percentage."),
            ("swap_warn_pct", "Swap warning threshold", "f", 10, 100, "%", "Warn when swap use reaches this percentage."),
            ("refresh_seconds", "Dashboard refresh interval", "i", 1, 60, " seconds", "How often the live dashboard refreshes."),
            ("retention_days", "History retention", "i", 1, 3650, " days", "Keep diagnostic history for this many days."),
            ("scan_timeout", "Diagnostic timeout", "i", 5, 300, " seconds", "Maximum time allowed for supported checks."),
            ("max_log_lines", "Maximum log lines", "i", 10, 1000, " lines", "Limit the number of recent log lines inspected."),
            ("network_test_port", "Connectivity test port", "i", 1, 65535, "", "TCP port used by the connectivity check."),
        )
        sections = {
            "disk_warn_pct": "Diagnostic thresholds",
            "refresh_seconds": "Dashboard, history, and log limits",
            "network_test_port": "Network checks",
        }
        for name, label, kind, lo, hi, suffix, tooltip in specs:
            if name in sections:
                section = QLabel(sections[name])
                section.setObjectName("settingsSection")
                f.addRow(section)
            w = QDoubleSpinBox() if kind == "f" else QSpinBox()
            w.setRange(lo, hi); w.setValue(getattr(s, name)); w.setSuffix(suffix); w.setToolTip(tooltip)
            if kind == "f":
                w.setDecimals(1)
            self.w[name] = w
            f.addRow(label, w)
        self.network_host = QLineEdit(s.network_test_host)
        self.network_host.setToolTip("Host used by the network reachability check.")
        f.addRow("Connectivity test host", self.network_host)
        self.dns_name = QLineEdit(s.dns_test_name)
        self.dns_name.setToolTip("Domain used to test DNS resolution.")
        f.addRow("DNS test domain", self.dns_name)
        privacy_heading = QLabel("Privacy and appearance")
        privacy_heading.setObjectName("settingsSection")
        f.addRow(privacy_heading)
        self.hist = QCheckBox("Save diagnostic history")
        self.hist.setChecked(s.history_enabled)
        self.hist.setToolTip("Store redacted scan results locally for review and export.")
        f.addRow("History", self.hist)
        self.red = QCheckBox("Redact reports by default")
        self.red.setChecked(s.redact_reports)
        self.red.setToolTip("Mask IP addresses, usernames, and secrets in exported reports.")
        f.addRow("Report privacy", self.red)
        self.theme = QComboBox()
        self.theme.addItems(["system", "light", "dark"])
        self.theme.setCurrentText(s.theme)
        self.theme.setToolTip("Choose the application appearance.")
        f.addRow("Appearance", self.theme)
        self.prov = QComboBox()
        self.prov.addItems(["none", "ollama"])
        self.prov.setCurrentText(s.ai_provider)
        self.prov.setToolTip("AI is optional. Ollama is local by default.")
        ai_heading = QLabel("Optional AI assistant")
        ai_heading.setObjectName("settingsSection")
        f.addRow(ai_heading)
        f.addRow("AI provider", self.prov)
        self.ep = QLineEdit(s.ai_endpoint)
        self.ep.setToolTip("AI service URL. Remote endpoints require explicit permission.")
        f.addRow("AI endpoint", self.ep)
        self.model = QLineEdit(s.ai_model)
        f.addRow("AI model", self.model)
        self.remote = QCheckBox("Allow sending redacted context to a remote AI endpoint")
        self.remote.setChecked(s.ai_allow_remote)
        self.remote.setToolTip("Only enable this if you trust the remote service.")
        f.addRow("Remote AI", self.remote)
        save_b = QPushButton("Save settings")
        save_b.setToolTip("Validate and save these preferences locally.")
        save_b.clicked.connect(self.save)
        reset_b = QPushButton("Restore defaults")
        reset_b.setToolTip("Reset all settings to their built-in defaults. Save to apply.")
        reset_b.clicked.connect(self.reset)
        buttons = QHBoxLayout()
        buttons.addWidget(save_b)
        buttons.addWidget(reset_b)
        f.addRow(buttons)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        outer.addStretch()

    def save(self):
        d = {k: w.value() for k, w in self.w.items()}
        d.update(history_enabled=self.hist.isChecked(), redact_reports=self.red.isChecked(), ai_provider=self.prov.currentText(),
                 ai_endpoint=self.ep.text().strip(), ai_model=self.model.text().strip(), ai_allow_remote=self.remote.isChecked(),
                 theme=self.theme.currentText(), network_test_host=self.network_host.text().strip(),
                 dns_test_name=self.dns_name.text().strip())
        from ..config import Settings
        new = Settings.from_dict(d)
        save_settings(new)
        self.apply(new)
        self.s = new
        QMessageBox.information(self, "Settings", "Saved.")

    def reset(self):
        from ..config import Settings
        defaults = Settings()
        for name, widget in self.w.items():
            widget.setValue(getattr(defaults, name))
        self.hist.setChecked(defaults.history_enabled)
        self.red.setChecked(defaults.redact_reports)
        self.theme.setCurrentText(defaults.theme)
        self.prov.setCurrentText(defaults.ai_provider)
        self.ep.setText(defaults.ai_endpoint)
        self.model.setText(defaults.ai_model)
        self.remote.setChecked(defaults.ai_allow_remote)
        self.network_host.setText(defaults.network_test_host)
        self.dns_name.setText(defaults.dns_test_name)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Linux Doctor {__version__}")
        self.resize(1100, 700)
        self.s = load_settings()
        try:
            self.store = Store()
        except DatabaseError as exc:
            self.store = None
            QMessageBox.warning(self, "Database", f"History is disabled: {exc}")
        self.stack, self.nav = QStackedWidget(), QListWidget()
        self.nav.setFixedWidth(220)
        self.nav.setAccessibleName("Application pages")
        self.nav.setToolTip("Choose a page. Keyboard shortcuts Ctrl+1 through Ctrl+8 also work.")
        self.nav.setSpacing(3)
        self.diag_page = DiagPage(self.s, self.store)
        self.dashboard = Dashboard(self.s, self.start_full_check)
        self.history_page = HistoryPage(self.s, self.store)
        self.settings_page = SettingsPage(self.s, self._apply)
        pages = [
            ("Dashboard", "Live system overview and full health check", QStyle.SP_ComputerIcon, self.dashboard),
            ("System information", "Operating system and hardware details", QStyle.SP_FileDialogInfoView, SysInfoPage()),
            ("Processes", "Inspect current CPU and memory consumers", QStyle.SP_ArrowRight, ProcessPage()),
            ("Diagnostics", "Run guided, read-only system checks", QStyle.SP_BrowserReload, self.diag_page),
            ("AI assistant & commands", "Explain commands and optional AI findings", QStyle.SP_MessageBoxInformation, AssistantPage(self.s)),
            ("Reports & history", "Review, export, back up, or clear scan history", QStyle.SP_DialogSaveButton, self.history_page),
            ("Learn Linux", "Browse the offline command library", QStyle.SP_DialogHelpButton, LearnPage()),
            ("Settings", "Preferences, privacy, thresholds, and AI", QStyle.SP_FileDialogDetailedView, self.settings_page),
        ]
        self.shortcuts = []
        for index, (name, description, icon, widget) in enumerate(pages):
            item = QListWidgetItem(self.style().standardIcon(icon), name)
            item.setToolTip(f"{description}\nKeyboard shortcut: Ctrl+{index + 1}")
            item.setStatusTip(description)
            self.nav.addItem(item)
            self.stack.addWidget(widget)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda page=index: self.nav.setCurrentRow(page))
            self.shortcuts.append(shortcut)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        c = QWidget()
        h = QHBoxLayout(c)
        h.setContentsMargins(12, 12, 12, 12)
        h.setSpacing(14)
        h.addWidget(self.nav)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(c)
        self.statusBar().showMessage("Read-only diagnostics · Your data stays on this device")
        self.statusBar().setToolTip("Linux Doctor inspects and explains; it never applies repairs.")

    def start_full_check(self):
        self.nav.setCurrentRow(3)
        self.diag_page.combo.setCurrentIndex(self.diag_page.combo.findData("full_health"))
        self.diag_page.start()

    def _apply(self, s):
        self.s.__dict__.update(s.__dict__)
        self.diag_page.s = self.s
        self.dashboard.timer.setInterval(self.s.refresh_seconds * 1000)
        app = QApplication.instance()
        if app:
            _apply_theme(app, self.s.theme)

    def closeEvent(self, e):
        if self.store:
            self.store.close()
        super().closeEvent(e)


_APP_STYLE = """
QMainWindow { background: #f3f6fa; }
QWidget { font-size: 12px; }
QLabel#pageHeading { color: #173b59; font-size: 22px; font-weight: 700; padding-top: 4px; }
QLabel#pageDescription { color: #5b6f82; font-size: 12px; padding-bottom: 6px; }
QLabel#settingsSection { color: #22618b; font-size: 14px; font-weight: 700; padding-top: 12px; }
QListWidget { background: #ffffff; border: 1px solid #d8e2ec; border-radius: 10px; padding: 6px; }
QListWidget::item { min-height: 38px; padding: 5px 8px; border-radius: 6px; }
QListWidget::item:selected { background: #dceeff; color: #123c5b; font-weight: 600; }
QListWidget::item:hover:!selected { background: #eef4f9; }
QPushButton { min-height: 34px; padding: 3px 12px; border: 1px solid #bdcbd8; border-radius: 6px;
              background: #ffffff; color: #173b59; }
QPushButton:hover { background: #eaf4fc; border-color: #76a9d1; }
QPushButton:pressed { background: #dceeff; }
QPushButton:disabled { color: #8999a8; background: #edf0f3; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QCheckBox:focus, QListWidget:focus, QTableWidget:focus, QPlainTextEdit:focus { border: 2px solid #3687c8; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 30px; padding: 2px 7px; border: 1px solid #c7d3de;
                                                 border-radius: 5px; background: #ffffff; }
QTableWidget, QPlainTextEdit { background: #ffffff; border: 1px solid #d8e2ec; border-radius: 6px; }
QHeaderView::section { background: #eaf0f6; color: #294b66; padding: 7px; border: 0; font-weight: 600; }
QStatusBar { background: #eaf0f6; color: #456176; }
QProgressBar { min-height: 12px; border: 0; border-radius: 5px; background: #e2eaf1; }
QProgressBar::chunk { border-radius: 5px; background: #3687c8; }
"""

_DARK_STYLE = """
QMainWindow { background: #17232e; }
QWidget { color: #e4edf5; font-size: 12px; }
QLabel#pageHeading { color: #eaf4fc; }
QLabel#pageDescription { color: #b6c6d4; }
QLabel#settingsSection { color: #8fc8ff; }
QListWidget { background: #202f3c; color: #e4edf5; border-color: #405262; }
QListWidget::item:selected { background: #284d69; color: #ffffff; }
QListWidget::item:hover:!selected { background: #2a3b49; }
QPushButton { background: #263744; color: #e4edf5; border-color: #526574; }
QPushButton:hover { background: #324b5d; border-color: #7eb8e3; }
QPushButton:pressed { background: #284d69; }
QPushButton:disabled { color: #91a0ad; background: #202a33; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { background: #202f3c; color: #e4edf5; border-color: #526574; }
QTableWidget, QPlainTextEdit { background: #202f3c; color: #e4edf5; border-color: #405262; }
QHeaderView::section { background: #293b49; color: #e4edf5; }
QStatusBar { background: #202f3c; color: #c1d0dc; }
QProgressBar { background: #293b49; }
"""


def _apply_theme(app, theme):
    default_style = getattr(app, "_linux_doctor_default_style", None)
    if default_style is None:
        default_style = app.style().objectName()
        app._linux_doctor_default_style = default_style
    if theme in ("light", "dark"):
        app.setStyle("Fusion")
    else:
        app.setStyle(default_style)
    app.setStyleSheet(_APP_STYLE + (_DARK_STYLE if theme == "dark" else ""))


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Linux Doctor")
    s = load_settings()
    _apply_theme(app, s.theme)
    w = MainWindow(); w.show()
    return app.exec()
