#!/usr/bin/env python3
"""
Watch official Cenacolo Vinciano (Last Supper) inventory for Milan trip dates.

Checks ONLY (user request):
  1) Admission tickets (€15) — 普通剩票
  2) Set-time guided tour English — 英文導覽

Modes:
  --once          single scan, print table, exit 0 if any target seats
  --loop          every --interval seconds (default 60); stdout ONLY on hits
                  (so Grok monitor wakes only when buyable)

Target days default: 2026-08-27 and 2026-08-28 (also reports 25–30 in --once).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

PRODUCTS = [
    {
        "id": "151991",
        "key": "admission",
        "name": "ADMISSION TICKETS (普通入場 €15)",
        "url": "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991",
    },
    {
        "id": "238363",
        "key": "guide_en",
        "name": "GUIDED TOUR ENGLISH (英文導覽)",
        "url": "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-visite-guidate-a-orario-fisso-in-inglese/238363",
    },
]

# Prefer 27–28; also watch 25–30 for salvage options
TARGET_DAYS = {27, 28}
WINDOW_DAYS = {25, 26, 27, 28, 29, 30}

DATE_RE = re.compile(
    r"new Date \(2026, \((\d+)-1\), (\d+)\), '(\d+)', (\d+), '(\d+)'"
)


def dump_dom(url: str, timeout: int = 90) -> str:
    if not os.path.isfile(CHROME):
        raise RuntimeError(f"Chrome not found: {CHROME}")
    cmd = [
        CHROME,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        f"--user-agent={USER_AGENT}",
        "--dump-dom",
        url,
    ]
    r = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        text=True,
        errors="replace",
    )
    return r.stdout or ""


def parse_seats(html: str) -> dict[tuple[int, int], int]:
    """Return {(month, day): seats} for 2026."""
    out: dict[tuple[int, int], int] = {}
    for m, d, a, _dow, _a2 in DATE_RE.findall(html):
        key = (int(m), int(d))
        seats = int(a)
        # keep max if duplicate rows
        out[key] = max(out.get(key, 0), seats)
    return out


def scan_all(window_only: bool = False) -> list[dict]:
    results = []
    for p in PRODUCTS:
        try:
            html = dump_dom(p["url"])
        except Exception as e:
            results.append(
                {
                    **p,
                    "ok": False,
                    "error": str(e),
                    "seats": {},
                }
            )
            continue
        if "new Date" not in html and p["id"] not in html:
            results.append(
                {
                    **p,
                    "ok": False,
                    "error": "blocked_or_empty",
                    "seats": {},
                }
            )
            continue
        seats = parse_seats(html)
        if window_only:
            seats = {
                k: v
                for k, v in seats.items()
                if k[0] == 8 and k[1] in WINDOW_DAYS
            }
        results.append({**p, "ok": True, "error": None, "seats": seats})
        time.sleep(1.5)  # gentle gap between product pages
    return results


def hits_for_targets(results: list[dict]) -> list[dict]:
    hits = []
    for r in results:
        if not r.get("ok"):
            continue
        for (mo, day), n in r["seats"].items():
            if mo == 8 and day in TARGET_DAYS and n > 0:
                hits.append(
                    {
                        "key": r["key"],
                        "name": r["name"],
                        "url": r["url"],
                        "date": f"2026-08-{day:02d}",
                        "seats": n,
                    }
                )
    return hits


def print_once_table(results: list[dict]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== Last Supper 官方全線檢查 @ {now} ===")
    print("目標：08-27 / 08-28（亦顯示 25–30）\n")
    for r in results:
        print(f"【{r['name']}】")
        print(f"  {r['url']}")
        if not r["ok"]:
            print(f"  ⚠️ 讀取失敗: {r.get('error')}")
            print()
            continue
        # show window
        any_row = False
        for day in sorted(WINDOW_DAYS):
            n = r["seats"].get((8, day), None)
            if n is None:
                # not in calendar payload
                continue
            any_row = True
            flag = "🟢" if n > 0 else "🔴"
            star = " ← 你嘅日子" if day in TARGET_DAYS else ""
            print(f"  {flag} 08-{day:02d}: {n} seats{star}")
        if not any_row:
            print("  （日曆無 8 月底資料 / 全日未開放）")
        print()
    hits = hits_for_targets(results)
    if hits:
        print("🚨 有位！立刻去買：")
        for h in hits:
            print(f"  - {h['date']} · {h['name']} · {h['seats']} seats")
            print(f"    {h['url']}")
    else:
        print("08-27 / 08-28 全部產品暫時 0 位。")
        print("週三 12:00 米蘭（港約 18:00）會加放下一週。")


def append_log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


GUIDE_HTML = Path(__file__).resolve().parent / "last-supper-buy-guide.html"
EMAIL_CFG = Path(__file__).resolve().parent / "last_supper_email.json"


def load_email_cfg():
    if not EMAIL_CFG.exists():
        return None
    try:
        cfg = json.loads(EMAIL_CFG.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not cfg.get("enabled", True):
        return None
    if not cfg.get("to") or not cfg.get("password"):
        return None
    return cfg


def send_alert_email(hits: list[dict], subject_prefix: str = "🚨 Last Supper 有位") -> str:
    """Send Gmail SMTP alert. Returns 'ok' or error string."""
    cfg = load_email_cfg()
    if not cfg:
        return "no_email_cfg"

    lines = [
        "官方《最後的晚餐》有位！請立刻上網購買（記名、唔退、唔改期）。",
        "",
        f"檢查時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "目標日：2026-08-27 / 2026-08-28（優先 08-28）",
        "",
        "—— 有位項目 ——",
    ]
    for h in hits:
        lines.append(f"• {h['date']} · {h['name']} · {h['seats']} seats")
        lines.append(f"  購買：{h['url']}")
        lines.append("")
    lines.extend(
        [
            "步驟：",
            "1) 開上面連結（或已自動開 Chrome）",
            "2) 揀 08-28 或 08-27 → 時段 → Full",
            "3) 填護照英文名 → 付款",
            "4) 提早 30 分鐘到現場取票",
            "",
            f"填表指南：{GUIDE_HTML}",
            "",
            "（此信由 last_supper_watch.py 自動發送）",
        ]
    )
    body = "\n".join(lines)
    subj_bits = ", ".join(f"{h['date']} {h['key']}×{h['seats']}" for h in hits)
    subject = f"{subject_prefix}: {subj_bits}"

    msg = MIMEMultipart()
    msg["From"] = cfg.get("from") or cfg.get("username")
    msg["To"] = cfg["to"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("username") or cfg.get("from")
    password = str(cfg["password"]).replace(" ", "")

    try:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, password)
            s.sendmail(msg["From"], [cfg["to"]], msg.as_string())
        return "ok"
    except Exception as e:
        return f"email_error: {e}"


def open_for_user(urls: list[str]) -> None:
    """Pop Chrome (or default browser) so user can fill details immediately."""
    # Prefer: buy pages first, then fill-in guide
    ordered: list[str] = []
    for u in urls:
        if u not in ordered:
            ordered.append(u)
    if GUIDE_HTML.exists():
        ordered.append(GUIDE_HTML.as_uri())

    for u in ordered:
        try:
            # macOS: open in Chrome if available, else default browser
            if os.path.isfile(CHROME):
                subprocess.Popen(
                    ["open", "-a", "Google Chrome", u],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["open", u],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(0.4)
        except Exception:
            pass

    # macOS notification + sound
    try:
        title = "Last Supper 有位！"
        body = "官方票有位 — Chrome 已打開，快去填名付款"
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{body}" with title "{title}" sound name "Glass"',
            ],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="seconds between scans")
    ap.add_argument(
        "--log",
        default=str(
            Path(__file__).resolve().parent / "last_supper_watch.log"
        ),
    )
    ap.add_argument(
        "--state",
        default=str(
            Path(__file__).resolve().parent / "last_supper_watch_state.json"
        ),
    )
    ap.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email using last_supper_email.json and exit",
    )
    args = ap.parse_args()
    if not args.once and not args.loop and not args.test_email:
        args.once = True

    log_path = Path(args.log)
    state_path = Path(args.state)

    if args.test_email:
        fake_hits = [
            {
                "key": "admission",
                "name": "ADMISSION TICKETS (普通入場 €15) [測試]",
                "url": "https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991",
                "date": "2026-08-28",
                "seats": 0,
            }
        ]
        r = send_alert_email(
            fake_hits, subject_prefix="✅ 測試：Last Supper 通知已接通"
        )
        print("test_email:", r)
        return 0 if r == "ok" else 1

    def load_state() -> dict:
        if state_path.exists():
            try:
                return json.loads(state_path.read_text())
            except Exception:
                return {}
        return {}

    def save_state(s: dict) -> None:
        state_path.write_text(json.dumps(s, ensure_ascii=False, indent=2))

    if args.once:
        results = scan_all(window_only=False)
        print_once_table(results)
        return 0 if hits_for_targets(results) else 1

    # loop mode: quiet unless hit (stdout line wakes Grok monitor)
    state = load_state()
    last_alert_key = state.get("last_alert_key")
    scan_n = 0
    append_log(
        log_path,
        f"{datetime.now().isoformat()} START loop interval={args.interval}s products={len(PRODUCTS)} targets=08-27,08-28",
    )

    while True:
        t0 = time.time()
        scan_n += 1
        try:
            results = scan_all(window_only=False)
            hits = hits_for_targets(results)
            # compact log every scan
            parts = []
            for r in results:
                if not r["ok"]:
                    parts.append(f"{r['key']}=ERR")
                    continue
                s27 = r["seats"].get((8, 27), 0)
                s28 = r["seats"].get((8, 28), 0)
                parts.append(f"{r['key']}:27={s27},28={s28}")
            line = f"{datetime.now().isoformat()} scan#{scan_n} " + " | ".join(parts)
            append_log(log_path, line)

            if hits:
                # dedupe alert if same snapshot
                key = "|".join(
                    f"{h['key']}:{h['date']}:{h['seats']}" for h in hits
                )
                if key != last_alert_key:
                    last_alert_key = key
                    state["last_alert_key"] = key
                    state["last_hit_at"] = datetime.now().isoformat()
                    save_state(state)
                    # Prefer admission first, then English guide
                    hit_urls = []
                    for prefer in ("admission", "guide_en"):
                        for h in hits:
                            if h["key"] == prefer and h["url"] not in hit_urls:
                                hit_urls.append(h["url"])
                    for h in hits:
                        if h["url"] not in hit_urls:
                            hit_urls.append(h["url"])
                    open_for_user(hit_urls)
                    mail_result = send_alert_email(hits)
                    append_log(
                        log_path,
                        f"{datetime.now().isoformat()} EMAIL {mail_result}",
                    )
                    # ONE stdout line per product-date for monitor / Grok
                    for h in hits:
                        msg = (
                            f"FOUND Last Supper seats: {h['date']} | {h['name']} | "
                            f"{h['seats']} seats | BUY NOW: {h['url']} | "
                            f"GUIDE: {GUIDE_HTML} | EMAIL:{mail_result}"
                        )
                        print(msg, flush=True)
                        append_log(log_path, "ALERT " + msg)
            else:
                state["last_scan_at"] = datetime.now().isoformat()
                state["last_scan_n"] = scan_n
                save_state(state)
        except Exception as e:
            append_log(log_path, f"{datetime.now().isoformat()} ERROR {e}")
            # do not spam stdout

        elapsed = time.time() - t0
        sleep_for = max(1.0, args.interval - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    sys.exit(main())
