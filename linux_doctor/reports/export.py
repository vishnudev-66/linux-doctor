from __future__ import annotations

import csv
import html
import io
import json
import os
import time
from pathlib import Path

from ..config import reports_dir
from ..core.redact import redact_obj

DISCLAIMER = ("Automated, point-in-time checks. Not a security audit; a clean result does not prove the system is secure.")


def _prep(result: dict, redact: bool) -> dict:
    return redact_obj(result) if redact else result


def to_json(result: dict, redact: bool = True) -> str:
    return json.dumps({"disclaimer": DISCLAIMER, **_prep(result, redact)}, indent=2, default=str)


def to_csv(result: dict, redact: bool = True) -> str:
    r = _prep(result, redact)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "severity", "confidence", "title", "explanation", "recommendation"])
    for f in r["findings"]:
        # prefix cells that start with formula characters to prevent CSV injection
        w.writerow([("'" + str(v)) if str(v)[:1] in "=+-@" and str(v) else v for v in
                    (f["code"], f["severity"], f["confidence"], f["title"], f["explanation"], f["recommendation"])])
    return buf.getvalue()


def to_html(result: dict, redact: bool = True) -> str:
    r = _prep(result, redact)
    e = html.escape
    rows = "".join(
        f"<tr class='{e(f['severity'])}'><td>{e(f['severity'])}</td><td>{e(f['title'])}</td>"
        f"<td>{e(f['explanation'])}<pre>{e(json.dumps(f['evidence'], indent=1, default=str))}</pre></td>"
        f"<td>{e(f['recommendation'])}</td><td>{e(f['confidence'])}</td></tr>" for f in r["findings"])
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("started_at", 0)))
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' "
            f"content=\"default-src 'none'; style-src 'unsafe-inline'\"><title>Linux Doctor report</title><style>"
            "body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px;vertical-align:top}"
            "tr.action{background:#fdd}tr.attention{background:#ffe9c7}tr.ok{background:#e6f6e6}pre{white-space:pre-wrap;font-size:.8em}</style></head><body>"
            f"<h1>{e(r['title'])}</h1><p>{e(started)} &mdash; {e(r['summary'])}</p><p><em>{e(DISCLAIMER)}</em></p>"
            f"<table><tr><th>Severity</th><th>Finding</th><th>Explanation / evidence</th><th>Recommendation</th><th>Confidence</th></tr>{rows}</table></body></html>")


def write_report(result: dict, fmt: str, redact: bool = True, dest: str | Path | None = None) -> Path:
    fn = {"json": to_json, "csv": to_csv, "html": to_html}.get(fmt)
    if not fn:
        raise ValueError("format must be json, csv or html")
    path = Path(dest) if dest else reports_dir() / time.strftime(f"report-%Y%m%d-%H%M%S.{fmt}")
    old = os.umask(0o077)
    try:
        path.write_text(fn(result, redact), encoding="utf-8")
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return path
