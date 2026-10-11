"""PySide6 desktop UI. All diagnostics run on worker threads; the UI never blocks."""
from __future__ import annotations

import json
import sys
import threading

import psutil
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QDoubleSpinBox)

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


class Dashboard(QWidget):
    def __init__(self, s):
        super().__init__()
        self.s = s
        lay = QVBoxLayout(self)
        self.cards = {k: QLabel() for k in ("CPU", "Memory", "Disk (/)", "Uptime")}
        row = QHBoxLayout()
        for k, lab in self.cards.items():
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet("border:1px solid #888;border-radius:8px;padding:18px;font-size:16px")
            row.addWidget(lab)
        lay.addLayout(row)
        self.note = QLabel("Normal readings do not mean the system is secure - run a Full health check for more.")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        lay.addStretch()
        self.timer = QTimer(self, timeout=self.refresh)
        self.timer.start(s.refresh_seconds * 1000)
        self.refresh()

    def refresh(self):
        vm = psutil.virtual_memory()
        du = psutil.disk_usage("/")
        self.cards["CPU"].setText(f"CPU\n{psutil.cpu_percent():.0f}%")
        self.cards["Memory"].setText(f"Memory\n{vm.percent:.0f}% used\n{vm.available / 1024**3:.1f} GB available")
        self.cards["Disk (/)"].setText(f"Disk (/)\n{du.percent:.0f}% used\n{du.free / 1024**3:.0f} GB free")
        self.cards["Uptime"].setText(f"Uptime\n{(__import__('time').time() - psutil.boot_time()) / 3600:.1f} h")


class SysInfoPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        self.t = _table(["Property", "Value"])
        lay.addWidget(self.t)
        _bg(sysinfo.collect, lambda d: _fill(self.t, list(d.items())))


class ProcessPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        b = QPushButton("Refresh (inspection only - no process control)")
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
        top = QHBoxLayout()
        self.combo = QComboBox()
        for k, (t, _) in WORKFLOWS.items():
            self.combo.addItem(t, k)
        self.run_b, self.stop_b = QPushButton("Run"), QPushButton("Cancel")
        self.exp_b = QPushButton("Export report")
        self.ai_b = QPushButton("Explain selected with AI")
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
        sp.addWidget(self.t)
        sp.addWidget(self.detail)
        lay.addWidget(sp)
        self.run_b.clicked.connect(self.start)
        self.stop_b.clicked.connect(self.cancel.set)
        self.exp_b.clicked.connect(self.export)
        self.ai_b.clicked.connect(self.ask_ai)
        self.t.itemSelectionChanged.connect(self.show_detail)

    def start(self):
        self.cancel = threading.Event()
        self.prog.show()
        self.run_b.setEnabled(False)
        name, c = self.combo.currentData(), self.cancel
        _bg(lambda: run_workflow(name, self.s, c), self.finished, self._err)

    def _err(self, m):
        self.prog.hide(); self.run_b.setEnabled(True)
        QMessageBox.warning(self, "Linux Doctor", m)

    def finished(self, res):
        self.prog.hide(); self.run_b.setEnabled(True)
        self.result = res
        if self.s.history_enabled and self.store:
            try:
                self.store.save_run(res)
            except Exception as exc:  # noqa: BLE001
                self.detail.setPlainText(f"History not saved: {exc}")
        fs = sorted(res.findings, key=lambda f: -f.severity)
        self._fs = fs
        _fill(self.t, [(f.severity.label, f.title, f.confidence.value) for f in fs])
        for i, f in enumerate(fs):
            self.t.item(i, 0).setForeground(__import__("PySide6.QtGui", fromlist=["QColor"]).QColor(COLORS[f.severity]))
        self.detail.setPlainText(f"{res.title}: {res.summary}" + ("  (cancelled)" if res.status == "cancelled" else ""))

    def _sel(self):
        r = self.t.currentRow()
        return self._fs[r] if self.result and 0 <= r < len(self._fs) else None

    def show_detail(self):
        f = self._sel()
        if f:
            self.detail.setPlainText(f"{f.title}\n\nWhat this means: {f.explanation or '-'}\n\nRecommendation: "
                                     f"{f.recommendation or '-'}\n\nEvidence:\n{json.dumps(f.evidence, indent=2, default=str)}")

    def export(self):
        if not self.result:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export", "report.html", "HTML (*.html);;JSON (*.json);;CSV (*.csv)")
        if path:
            fmt = path.rsplit(".", 1)[-1].lower()
            if fmt in ("html", "json", "csv"):
                write_report(self.result.to_dict(), fmt, self.s.redact_reports, path)

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
        row = QHBoxLayout()
        for label, fn in (("Refresh", self.load), ("Delete all history", self.clear), ("Backup database", self.backup)):
            b = QPushButton(label); b.clicked.connect(fn); row.addWidget(b)
        lay.addLayout(row)
        self.t = _table(["ID", "Workflow", "Status", "Summary"])
        lay.addWidget(self.t)
        self.load()

    def load(self):
        if self.store:
            _fill(self.t, [(r["id"], r["title"], r["status"], r["summary"]) for r in self.store.list_runs(200)])

    def clear(self):
        if self.store and QMessageBox.question(self, "Delete", "Permanently delete ALL history?") == QMessageBox.Yes:
            self.store.clear_all(); self.load()

    def backup(self):
        if self.store:
            QMessageBox.information(self, "Backup", f"Saved (private permissions):\n{self.store.backup()}")


class AssistantPage(QWidget):
    def __init__(self, s):
        super().__init__()
        self.s = s
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Paste a command or terminal error. Commands are explained, never executed."))
        self.inp = QLineEdit(placeholderText="e.g. sudo apt autoremove")
        b = QPushButton("Explain (offline)")
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
        lay = QHBoxLayout(self)
        self.lst = QListWidget()
        self.lst.addItems(sorted(LIBRARY))
        self.view = QPlainTextEdit(readOnly=True)
        lay.addWidget(self.lst, 1)
        lay.addWidget(self.view, 3)
        self.lst.currentTextChanged.connect(lambda n: self.view.setPlainText(
            "\n".join(f"{k.title()}: {v}" for k, v in LIBRARY.get(n, {}).items())))


class SettingsPage(QWidget):
    def __init__(self, s, apply):
        super().__init__()
        self.s, self.apply = s, apply
        f = QFormLayout(self)
        self.w = {}
        for name, kind, lo, hi in (("disk_warn_pct", "f", 50, 99), ("disk_crit_pct", "f", 51, 100), ("disk_min_free_gb", "f", 0, 10000),
                                   ("cpu_warn_pct", "f", 50, 100), ("refresh_seconds", "i", 1, 60), ("retention_days", "i", 1, 3650),
                                   ("scan_timeout", "i", 5, 300)):
            w = QDoubleSpinBox() if kind == "f" else QSpinBox()
            w.setRange(lo, hi); w.setValue(getattr(s, name)); self.w[name] = w; f.addRow(name.replace("_", " "), w)
        self.hist = QCheckBox("Keep diagnostic history"); self.hist.setChecked(s.history_enabled); f.addRow(self.hist)
        self.red = QCheckBox("Redact reports (IPs, usernames, secrets)"); self.red.setChecked(s.redact_reports); f.addRow(self.red)
        self.prov = QComboBox(); self.prov.addItems(["none", "ollama"]); self.prov.setCurrentText(s.ai_provider); f.addRow("AI provider", self.prov)
        self.ep = QLineEdit(s.ai_endpoint); f.addRow("AI endpoint (localhost by default)", self.ep)
        self.model = QLineEdit(s.ai_model); f.addRow("AI model", self.model)
        b = QPushButton("Save"); b.clicked.connect(self.save); f.addRow(b)

    def save(self):
        d = {k: w.value() for k, w in self.w.items()}
        d.update(history_enabled=self.hist.isChecked(), redact_reports=self.red.isChecked(), ai_provider=self.prov.currentText(),
                 ai_endpoint=self.ep.text().strip(), ai_model=self.model.text().strip(), ai_allow_remote=self.s.ai_allow_remote)
        from ..config import Settings
        new = Settings.from_dict(d)
        save_settings(new)
        self.apply(new)
        QMessageBox.information(self, "Settings", "Saved.")


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
        self.nav.setFixedWidth(190)
        pages = [("Dashboard", Dashboard(self.s)), ("System Information", SysInfoPage()), ("Processes", ProcessPage()),
                 ("Diagnostics", DiagPage(self.s, self.store)), ("AI Assistant & Commands", AssistantPage(self.s)),
                 ("Reports & History", HistoryPage(self.s, self.store)), ("Learn Linux", LearnPage()),
                 ("Settings", SettingsPage(self.s, self._apply))]
        for n, w in pages:
            self.nav.addItem(n); self.stack.addWidget(w)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        c = QWidget(); h = QHBoxLayout(c); h.addWidget(self.nav); h.addWidget(self.stack, 1)
        self.setCentralWidget(c)

    def _apply(self, s):
        self.s.__dict__.update(s.__dict__)

    def closeEvent(self, e):
        if self.store:
            self.store.close()
        super().closeEvent(e)


def run_gui() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Linux Doctor")
    s = load_settings()
    if s.theme == "dark":
        app.setStyle("Fusion")
    w = MainWindow(); w.show()
    return app.exec()
