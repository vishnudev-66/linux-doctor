# Linux Doctor 1.0

Local-first Linux diagnostics, explainable findings and safe troubleshooting for Ubuntu/Debian desktops.
Read-only by design: it **inspects and explains; it never repairs, deletes or runs AI-suggested commands.**

## Features
| Area | What you get |
|---|---|
| Dashboard | Live CPU / memory / disk / uptime cards; one-click full health check |
| System info | OS, kernel, CPU, RAM, battery, desktop |
| Processes | Top CPU/memory processes (inspection only) |
| Diagnostics | Workflows: slow computer, low storage, internet, failed services & logs, basic security hygiene, full health check |
| Storage | Filesystem checks (percent **and** absolute free space), directory sizes, large-file finder, advisory cleanup report (no deletion) |
| Network | Interface -> IP -> route -> TCP -> DNS ladder that tells DNS failures from routing failures; listening-port review |
| Security | UFW state, pending updates, SSH exposure, key / shadow permissions. Never claims the system is "secure" |
| AI (optional) | Local Ollama by default; redacted context; strict JSON validation; commands are display-only and labelled read-only vs review |
| History | SQLite, private permissions, redacted evidence, retention, backup, delete-all, export saved scans |
| Reports | HTML / JSON / CSV, redacted by default, escaped output, CSV-injection safe |
| Learn | Offline command library + explainer that warns about dangerous commands |
| Desktop UI | Guided page descriptions, full settings, light/dark appearance, keyboard navigation (`Ctrl+1`–`Ctrl+8`) |
| CLI | Everything also works headless: `linux-doctor scan full_health` |

## Quick start
```bash
sudo apt install -y python3 python3-venv libegl1 libxcb-cursor0   # one-time system packages
./scripts/install.sh                                              # per-user, no root
linux-doctor gui          # desktop app
linux-doctor scan full_health
```
See **DEPLOY.md** (install, .deb build, upgrade, uninstall, verification) and **SECURITY.md** (threat model, database safety).

## CLI
```
linux-doctor info | workflows | history | selfcheck | backup
linux-doctor scan <workflow> [--json] [--no-save]
linux-doctor export <run_id> --format html|json|csv [--no-redact]
linux-doctor explain "sudo apt autoremove"        # offline, never executes
linux-doctor purge --older-than-days 30 | --all
```
Exit code of `scan` is 1 if any "action recommended" finding exists (usable in scripts/monitoring).

## Layout
```
linux_doctor/  core/ (runner, redact, sysinfo, models)  diagnostics/  db/  ai/  reports/  learn/  ui/  cli.py
tests/  scripts/  packaging/  docs via README, DEPLOY.md, SECURITY.md
```

## Development
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[gui,dev]"
python -m pytest -q          # or: python -m unittest discover -s tests
```

## Known limitations
- Tested core logic on Ubuntu-like systems; the PySide6 GUI is compile-checked but needs a manual visual test on your desktop before release.
- Reading UFW state / some logs needs privileges; the app reports "could not inspect" instead of escalating.
- PDF export, repair actions, and non-systemd distributions are not implemented in 1.0.
- Database is protected by permissions, redaction and integrity checks - not encrypted at rest (use full-disk encryption).
