# Deployment guide (Ubuntu 22.04 / 24.04 / 26.04, Debian 12+)

## 0. Verify the download
```bash
sha256sum -c linux-doctor-1.0.0.zip.sha256     # must print: OK
unzip linux-doctor-1.0.0.zip && cd linux-doctor
```

## 1. System prerequisites (only step that needs sudo)
```bash
sudo apt update
sudo apt install -y python3 python3-venv libegl1 libxcb-cursor0
```
`libxcb-cursor0` is required by Qt 6.5+ on Ubuntu; without it the GUI will not start.

## 2. Test before installing (optional but recommended)
```bash
python3 -m venv /tmp/ld-test && . /tmp/ld-test/bin/activate
pip install -e ".[dev]" && python -m pytest -q ; deactivate
```

## 3A. Per-user install (recommended, no root)
```bash
./scripts/install.sh              # CLI + GUI
LD_EXTRAS=none ./scripts/install.sh   # CLI only (servers / headless)
linux-doctor selfcheck
linux-doctor gui
```

## 3B. System-wide .deb (for several users / fleet rollout)
```bash
./scripts/build_deb.sh                       # creates dist/linux-doctor_1.0.0_all.deb + .sha256
sudo apt install ./dist/linux-doctor_1.0.0_all.deb
```
The package's post-install step builds an isolated virtualenv in `/opt/linux-doctor` (internet needed during install).
The app still runs as the *logged-in user*; each user gets their own private data directory.

## 4. Optional local AI (stays on your machine)
```bash
curl -fsSL https://ollama.com/install.sh -o ollama-install.sh   # read the script first
less ollama-install.sh && sh ollama-install.sh
ollama pull llama3.2
```
Then Settings -> AI provider = `ollama` (endpoint stays `http://127.0.0.1:11434`). Non-loopback endpoints are blocked unless you explicitly allow them.

## 5. Upgrade / uninstall
```bash
./scripts/install.sh                 # re-run from the new version's folder; settings/history are preserved
./scripts/uninstall.sh               # remove app, keep data
./scripts/uninstall.sh --purge-data  # remove app and all data
sudo apt remove linux-doctor         # for the .deb
```

## 6. Routine safe commands
```bash
linux-doctor scan full_health --json > scan.json   # read-only scan
linux-doctor history                               # list saved scans
linux-doctor export 12 --format html               # redacted report in ~/.local/share/linux-doctor/reports
linux-doctor backup                                # private DB backup
linux-doctor purge --older-than-days 30            # retention
linux-doctor explain "sudo apt autoremove"         # explain without running
ls -l ~/.local/share/linux-doctor                  # expect drwx------ and -rw------- (700/600)
```
Scheduled scan (user cron, no root): `crontab -e` -> `0 9 * * 1 /home/<you>/.local/bin/linux-doctor scan full_health >/dev/null`

## 7. Troubleshooting
| Symptom | Fix |
|---|---|
| `Could not load the Qt platform plugin "xcb"` | `sudo apt install libxcb-cursor0 libegl1` |
| `GUI unavailable` | Install with GUI extra: `./scripts/install.sh` (default) |
| `linux-doctor: command not found` | Add `~/.local/bin` to PATH, log out/in |
| `database error: integrity check failed` | Move `~/.local/share/linux-doctor/history.db*` aside; a fresh DB is created. Restore from `backups/` if needed |
| Journal / UFW "not readable" | Expected without privileges. Add yourself to `systemd-journal`: `sudo usermod -aG systemd-journal $USER` (log out/in) |
