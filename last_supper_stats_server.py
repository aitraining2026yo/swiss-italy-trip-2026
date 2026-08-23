#!/usr/bin/env python3
"""
LAN dashboard for Last Supper ticket watch stats.
Bind 0.0.0.0 so other devices on the same network can open it.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "last_supper_watch.log"
STATE = ROOT / "last_supper_watch_state.json"
PORT = int(os.environ.get("LAST_SUPPER_STATS_PORT", "8787"))
HOST = os.environ.get("LAST_SUPPER_STATS_HOST", "0.0.0.0")

SCAN_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[^\s]+)\s+scan#(?P<n>\d+)\s+(?P<body>.+)$"
)
PART_RE = re.compile(
    r"(?P<key>admission|guide_en|guide_it|workshop):27=(?P<s27>\d+),28=(?P<s28>\d+)"
)
ERR_RE = re.compile(r"(?P<key>admission|guide_en|guide_it|workshop)=ERR")
START_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T[^\s]+)\s+START loop")
ALERT_RE = re.compile(r"ALERT|FOUND")


def process_running() -> dict:
    try:
        out = subprocess.check_output(
            ["pgrep", "-fl", "last_supper_watch.py --loop"],
            text=True,
            errors="replace",
        ).strip()
    except subprocess.CalledProcessError:
        return {"running": False, "pids": [], "cmd": None}
    lines = [ln for ln in out.splitlines() if "last_supper_watch.py" in ln]
    pids = []
    cmd = None
    for ln in lines:
        parts = ln.split(None, 1)
        if parts and parts[0].isdigit():
            pids.append(int(parts[0]))
            if "Python" in ln or "python" in ln:
                cmd = parts[1] if len(parts) > 1 else ln
    return {
        "running": bool(pids),
        "pids": pids,
        "cmd": cmd,
    }


def parse_log(max_lines: int = 400) -> dict:
    scans = []
    starts = []
    alerts = []
    if LOG.exists():
        try:
            raw = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            raw = []
        for line in raw[-max_lines:]:
            m = SCAN_RE.match(line.strip())
            if m:
                products = {}
                for pm in PART_RE.finditer(m.group("body")):
                    products[pm.group("key")] = {
                        "d27": int(pm.group("s27")),
                        "d28": int(pm.group("s28")),
                    }
                for em in ERR_RE.finditer(m.group("body")):
                    products[em.group("key")] = {"error": True}
                scans.append(
                    {
                        "ts": m.group("ts"),
                        "n": int(m.group("n")),
                        "products": products,
                        "raw": line.strip(),
                    }
                )
                continue
            sm = START_RE.match(line.strip())
            if sm:
                starts.append(sm.group("ts"))
            if ALERT_RE.search(line):
                alerts.append(line.strip())

    last = scans[-1] if scans else None
    # seats summary from last scan
    seats = {"admission": None, "guide_en": None}
    if last:
        for k in seats:
            seats[k] = last["products"].get(k)

    # simple history: last 30 scans as compact rows
    history = []
    for s in scans[-30:]:
        a27 = s["products"].get("admission", {}).get("d27")
        a28 = s["products"].get("admission", {}).get("d28")
        e27 = s["products"].get("guide_en", {}).get("d27")
        e28 = s["products"].get("guide_en", {}).get("d28")
        history.append(
            {
                "ts": s["ts"],
                "n": s["n"],
                "adm": f"{a27}/{a28}" if a27 is not None else "—",
                "en": f"{e27}/{e28}" if e27 is not None else "—",
            }
        )

    state = {}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "process": process_running(),
        "last_scan": last,
        "seats": seats,
        "scan_count_in_log_tail": len(scans),
        "last_start": starts[-1] if starts else None,
        "alerts": alerts[-10:],
        "history": list(reversed(history)),
        "state": state,
        "log_path": str(LOG),
        "targets": ["2026-08-27", "2026-08-28"],
        "products": [
            "ADMISSION (€15)",
            "GUIDED ENGLISH (~€25)",
        ],
    }


def lan_urls(port: int) -> list[str]:
    urls = [f"http://127.0.0.1:{port}/"]
    try:
        # primary interfaces
        for iface_cmd in (
            ["ipconfig", "getifaddr", "en0"],
            ["ipconfig", "getifaddr", "en1"],
        ):
            try:
                ip = subprocess.check_output(iface_cmd, text=True).strip()
                if ip:
                    u = f"http://{ip}:{port}/"
                    if u not in urls:
                        urls.append(u)
            except Exception:
                pass
        # fallback: UDP trick for outbound IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            u = f"http://{ip}:{port}/"
            if u not in urls:
                urls.append(u)
        finally:
            s.close()
    except Exception:
        pass
    return urls


HTML = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Last Supper 搶票 Stats · 瑞士意大利</title>
  <style>
    :root {
      --bg: #f4f1eb;
      --paper: #fffdf9;
      --ink: #1c2430;
      --muted: #5b6573;
      --line: #e0d9ce;
      --blue: #1d4e89;
      --blue-mid: #457b9d;
      --blue-soft: #e8f0fa;
      --blue-line: #cddff3;
      --green: #2d6a4f;
      --green-soft: #eaf4ef;
      --red: #c1121f;
      --red-soft: #fceeee;
      --gold: #b8860b;
      --shadow: 0 8px 24px rgba(28, 36, 48, 0.07);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", system-ui, sans-serif;
      background:
        radial-gradient(circle at top left, #e8f0fa 0, transparent 42%),
        radial-gradient(circle at top right, #f0f6fc 0, transparent 38%),
        var(--bg);
      color: var(--ink);
      min-height: 100vh;
      line-height: 1.5;
      font-size: 15px;
    }
    .wrap { max-width: 920px; margin: 0 auto; padding: 20px 16px 48px; }
    .hero {
      background: linear-gradient(135deg, #1d3557 0%, #457b9d 55%, #a8dadc 100%);
      color: #fff;
      border-radius: 20px;
      padding: 22px 18px;
      margin-bottom: 14px;
      box-shadow: var(--shadow);
    }
    .hero h1 { font-size: 1.35rem; margin: 0 0 6px; font-weight: 800; }
    .hero .sub { color: rgba(255,255,255,.92); font-size: 13.5px; margin: 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }
    .card {
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: var(--shadow);
    }
    .card .k {
      font-size: 11px; color: var(--muted);
      text-transform: uppercase; letter-spacing: .04em; font-weight: 700;
    }
    .card .v {
      font-size: 1.3rem; font-weight: 800; margin-top: 4px;
      font-variant-numeric: tabular-nums; color: var(--blue);
    }
    .card .s { font-size: 12px; color: var(--muted); margin-top: 4px; }
    .ok { color: var(--green) !important; }
    .bad { color: var(--red) !important; }
    .warn { color: var(--gold) !important; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 4px; }
    th, td {
      text-align: left; padding: 8px 8px;
      border-bottom: 1px solid var(--line);
    }
    th {
      color: var(--blue); font-weight: 700; font-size: 11px;
      background: var(--blue-soft); border-radius: 0;
    }
    tr:nth-child(even) td { background: #fcfaf7; }
    h2 {
      font-size: 1.05rem; margin: 20px 0 10px;
      color: var(--ink);
      border-left: 4px solid var(--blue-mid);
      padding-left: 10px;
    }
    .mono {
      font-family: ui-monospace, Menlo, monospace;
      font-size: 12px; word-break: break-all;
      color: var(--muted);
    }
    footer { margin-top: 20px; font-size: 12px; color: var(--muted); text-align: center; }
    .live { animation: pulse 1.5s infinite; }
    @keyframes pulse { 50% { opacity: .55; } }
    .badge {
      display: inline-block; font-size: 11px; font-weight: 700;
      background: rgba(255,255,255,.2); border: 1px solid rgba(255,255,255,.35);
      border-radius: 999px; padding: 3px 10px; margin-bottom: 8px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <div class="badge">🇨🇭 🇮🇹 瑞士＋意大利 · 搶票監控</div>
      <h1>Last Supper 搶票 Stats</h1>
      <p class="sub">目標 2026-08-27 / 08-28 · 普通票 + 英文導覽 · 每 10 秒自動更新</p>
    </header>


    <div class="grid" id="cards"></div>

    <h2>最近掃描（admission 27/28 · EN 27/28）</h2>
    <div class="card">
      <table>
        <thead><tr><th>#</th><th>時間</th><th>普通票</th><th>英文導覽</th></tr></thead>
        <tbody id="hist"></tbody>
      </table>
    </div>

    <h2>Alerts</h2>
    <div class="card mono" id="alerts">—</div>

    <footer id="foot"></footer>
  </div>
  <script>
    async function load() {
      try {
        const r = await fetch('/api/stats?_=' + Date.now());
        const d = await r.json();
        render(d);
      } catch (e) {
        document.getElementById('foot').textContent = '載入失敗: ' + e;
      }
    }
    function seatCell(p) {
      if (!p) return '—';
      if (p.error) return '<span class="bad">ERR</span>';
      const a = p.d27, b = p.d28;
      const cls = (a > 0 || b > 0) ? 'ok' : 'bad';
      return `<span class="${cls}">${a} / ${b}</span>`;
    }
    function render(d) {
      const run = d.process && d.process.running;
      const last = d.last_scan;
      const cards = [
        { k: '監控狀態', v: run ? '● 運行中' : '○ 已停', cls: run ? 'ok live' : 'bad', s: run ? 'pids: ' + (d.process.pids||[]).join(',') : '請重開 last_supper_watch' },
        { k: '最後掃描', v: last ? '#' + last.n : '—', s: last ? last.ts.replace('T',' ') : '無 log' },
        { k: '普通票 27/28', v: last ? seatText(last.products.admission) : '—', cls: seatCls(last && last.products.admission), s: 'seats' },
        { k: '英文導覽 27/28', v: last ? seatText(last.products.guide_en) : '—', cls: seatCls(last && last.products.guide_en), s: 'seats' },
        { k: '本頁更新', v: d.generated_at.replace('T',' '), s: 'server now' },
        { k: 'Log 內 scan 數', v: String(d.scan_count_in_log_tail), s: 'tail window' },
      ];
      document.getElementById('cards').innerHTML = cards.map(c => `
        <div class="card">
          <div class="k">${c.k}</div>
          <div class="v ${c.cls||''}">${c.v}</div>
          <div class="s">${c.s||''}</div>
        </div>`).join('');

      document.getElementById('hist').innerHTML = (d.history||[]).map(h => `
        <tr>
          <td>${h.n}</td>
          <td class="mono">${h.ts.replace('T',' ')}</td>
          <td>${h.adm}</td>
          <td>${h.en}</td>
        </tr>`).join('') || '<tr><td colspan="4">暫無</td></tr>';

      const al = d.alerts && d.alerts.length ? d.alerts.join('\\n') : '（未有 FOUND / ALERT）';
      document.getElementById('alerts').textContent = al;
      document.getElementById('foot').textContent =
        'API /api/stats · log: ' + d.log_path + ' · 同 Wi‑Fi / 區網先開到';
    }
    function seatText(p) {
      if (!p) return '—';
      if (p.error) return 'ERR';
      return p.d27 + ' / ' + p.d28;
    }
    function seatCls(p) {
      if (!p || p.error) return 'bad';
      return (p.d27 > 0 || p.d28 > 0) ? 'ok' : 'bad';
    }
    load();
    setInterval(load, 10000);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # quieter
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/stats":
            data = parse_log()
            data["urls"] = lan_urls(PORT)
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/health":
            self._send(200, b"ok\n", "text/plain; charset=utf-8")
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")


def main() -> None:
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    urls = lan_urls(PORT)
    print(f"Last Supper stats server on {HOST}:{PORT}", flush=True)
    for u in urls:
        print(f"  open: {u}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
