#!/usr/bin/env python3
"""
ai_cloud_worker.py - 24/7 Headless NSE 70/30 AI Auto-Pilot Worker for GitHub Actions.
Runs on GitHub servers every 5 minutes during NSE market hours (09:15 - 15:30 IST, Mon-Fri).
Fetches live Yahoo/NSE 5-minute candles, manages open paper positions (SL, Target, 15:15 IST square-off),
scans Opening Range Breakouts (09:15-09:30 IST), validates risk rules, and saves results to cloud_ledger.json.
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
LEDGER_FILE = "cloud_ledger.json"

UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN",
    "BHARTIARTL", "LT", "AXISBANK", "KOTAKBANK", "BAJFINANCE", "TITAN",
    "SUNPHARMA", "MARUTI", "TATAMOTORS", "HCLTECH", "NTPC", "TATASTEEL"
]

CONFIG = {
    "budget": 100000.0,
    "max_loss_trade": 1500.0,
    "max_daily_loss": 4500.0,
    "max_trades": 3,
    "min_rr": 1.0,
    "stop_rule": "or-low",      # Calibrated for 70/30 high win-rate
    "target_mult": 0.75,        # 0.75R target for 70/30 consistency
    "no_before": 9 * 60 + 30,   # 09:30 IST
    "no_after": 14 * 60 + 15,   # 14:15 IST
    "flat_by": 15 * 60 + 15,    # 15:15 IST mandatory intraday square-off
    "brok_flat": 20.0,
    "brok_pct": 0.03,
    "stt_pct": 0.025,
    "exch_pct": 0.00297,
    "stamp_pct": 0.003,
    "gst_pct": 18.0
}


def rtick(val, tick=0.05):
    return round(round(val / tick) * tick, 2)


def now_ist():
    return datetime.now(IST)


def compute_mis_charges(qty, entry, exit_px):
    buy_val = qty * entry
    sell_val = qty * exit_px
    brok = min(CONFIG["brok_flat"], buy_val * CONFIG["brok_pct"] / 100.0) + \
           min(CONFIG["brok_flat"], sell_val * CONFIG["brok_pct"] / 100.0)
    stt = sell_val * CONFIG["stt_pct"] / 100.0
    exch = (buy_val + sell_val) * CONFIG["exch_pct"] / 100.0
    sebi = (buy_val + sell_val) * 0.0001
    stamp = buy_val * CONFIG["stamp_pct"] / 100.0
    gst = (brok + exch + sebi) * CONFIG["gst_pct"] / 100.0
    return round(brok + stt + exch + sebi + stamp + gst, 2)


def fetch_live_5m_bars(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}.NS?interval=5m&range=2d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NSE-Cloud-Worker/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.load(resp)
        res = payload["chart"]["result"][0]
        ts_list = res["timestamp"]
        q = res["indicators"]["quote"][0]
        by_date = {}
        for ts, o, h, l, c, v in zip(ts_list, q["open"], q["high"], q["low"], q["close"], q["volume"]):
            if None in (o, h, l, c):
                continue
            dt = datetime.fromtimestamp(ts, IST)
            mins = dt.hour * 60 + dt.minute
            if mins < 555 or mins >= 930:
                continue
            d_str = dt.date().isoformat()
            by_date.setdefault(d_str, []).append({
                "m": mins,
                "time": f"{dt.hour:02d}:{dt.minute:02d}",
                "o": rtick(o),
                "h": rtick(h),
                "l": rtick(l),
                "c": rtick(c),
                "v": v or 10000
            })
        if not by_date:
            return None, []
        latest_date = sorted(by_date.keys())[-1]
        return latest_date, by_date[latest_date]
    except Exception as exc:
        print(f"[WARN] Fetch failed for {symbol}: {exc}")
        return None, []


def load_cloud_state():
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "started_since": now_ist().date().isoformat(),
        "last_heartbeat_ist": "",
        "status": "INITIALIZED",
        "trades": [],
        "logs": []
    }


def save_cloud_state(state):
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def add_log(state, msg):
    ts = now_ist().strftime("%Y-%m-%d %H:%M:%S IST")
    print(f"[{ts}] {msg}")
    state["logs"].insert(0, f"[{ts}] {msg}")
    state["logs"] = state["logs"][:80]


def main():
    dt_ist = now_ist()
    today_str = dt_ist.date().isoformat()
    cur_mins = dt_ist.hour * 60 + dt_ist.minute
    is_weekday = dt_ist.weekday() < 5
    is_market_hours = is_weekday and (555 <= cur_mins <= 930)

    state = load_cloud_state()
    state["last_heartbeat_ist"] = dt_ist.strftime("%Y-%m-%d %H:%M:%S IST")

    if not is_market_hours:
        state["status"] = "STANDBY (NSE CLOSED)"
        add_log(state, "Heartbeat check: NSE is currently closed. Standing by for 09:15 IST.")
        save_cloud_state(state)
        return

    state["status"] = "ACTIVE (LIVE NSE POLLING)"

    # 1. Monitor & settle OPEN trades
    for tr in state["trades"]:
        if tr.get("status") != "OPEN":
            continue
        _, bars = fetch_live_5m_bars(tr["symbol"])
        if not bars:
            continue
        last_bar = bars[-1]
        exit_px = None
        reason = ""

        if last_bar["l"] <= tr["stop"]:
            exit_px = tr["stop"]
            reason = "LIVE STOP LOSS HIT"
        elif last_bar["h"] >= tr["target"]:
            exit_px = tr["target"]
            reason = "LIVE TARGET HIT"
        elif cur_mins >= CONFIG["flat_by"]:
            exit_px = last_bar["c"]
            reason = "15:15 IST AUTO SQUARE-OFF"

        if exit_px is not None:
            charges = compute_mis_charges(tr["qty"], tr["entry"], exit_px)
            pnl = round((exit_px - tr["entry"]) * tr["qty"] - charges, 2)
            tr["exit"] = exit_px
            tr["pnl"] = pnl
            tr["status"] = "CLOSED"
            tr["note"] = f"Cloud AI Auto-Pilot ({reason})"
            add_log(state, f"{reason}: {tr['symbol']} closed @ {exit_px} | Net P&L: {pnl:+.2f}")
        time.sleep(0.25)

    # 2. Check daily risk limits before opening new trades
    today_trades = [t for t in state["trades"] if t.get("date") == today_str]
    realized_loss = -sum(
        float(t["pnl"]) for t in today_trades
        if t.get("status") == "CLOSED" and float(t.get("pnl", 0)) < 0
    )

    if len(today_trades) >= CONFIG["max_trades"]:
        add_log(state, f"Daily trade cap reached ({len(today_trades)}/{CONFIG['max_trades']}). Monitoring open positions only.")
        save_cloud_state(state)
        return

    if realized_loss >= CONFIG["max_daily_loss"]:
        add_log(state, f"Daily loss limit reached ({realized_loss:.2f}/{CONFIG['max_daily_loss']:.2f}). Halting new entries.")
        save_cloud_state(state)
        return

    if not (CONFIG["no_before"] <= cur_mins <= CONFIG["no_after"]):
        add_log(state, f"Outside entry window ({dt_ist.strftime('%H:%M')} IST). Waiting for valid window.")
        save_cloud_state(state)
        return

    # 3. Scan ORB setups across UNIVERSE
    open_symbols = {t["symbol"] for t in state["trades"] if t.get("status") == "OPEN"}
    for sym in UNIVERSE:
        if sym in open_symbols:
            continue
        sess_date, bars = fetch_live_5m_bars(sym)
        if sess_date != today_str or len(bars) < 4:
            continue

        orb_bars = [b for b in bars if 555 <= b["m"] < 570]
        if len(orb_bars) < 2:
            continue
        or_high = max(b["h"] for b in orb_bars)
        or_low = min(b["l"] for b in orb_bars)

        latest = bars[-1]
        if latest["c"] > or_high:
            entry = rtick(latest["c"] * 1.0002)
            raw_stop = or_low if CONFIG["stop_rule"] == "or-low" else (or_high + or_low) / 2.0
            stop = rtick(min(raw_stop, entry - max(0.5, entry * 0.004)))
            risk_per_share = max(0.05, rtick(entry - stop))
            target = rtick(entry + CONFIG["target_mult"] * risk_per_share)

            qty = min(
                int(CONFIG["max_loss_trade"] // risk_per_share),
                int(CONFIG["budget"] // entry)
            )
            if qty < 1:
                continue

            charges_tgt = compute_mis_charges(qty, entry, target)
            charges_sl = compute_mis_charges(qty, entry, stop)
            net_gain = (target - entry) * qty - charges_tgt
            net_loss = (entry - stop) * qty + charges_sl
            net_rr = net_gain / max(1.0, net_loss)

            if net_rr >= CONFIG["min_rr"] and (realized_loss + net_loss) <= CONFIG["max_daily_loss"]:
                trade_id = f"CLOUD_{int(time.time())}_{sym}"
                state["trades"].insert(0, {
                    "id": trade_id,
                    "date": today_str,
                    "segment": "MIS",
                    "symbol": sym,
                    "side": "BUY",
                    "qty": qty,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "isShort": False,
                    "exit": None,
                    "pnl": None,
                    "status": "OPEN",
                    "source": "AI_AUTOPILOT",
                    "note": "Cloud AI Auto-Pilot (GitHub 24/7 Worker)"
                })
                add_log(state, f"NEW CLOUD PAPER FILL: BUY {qty} {sym} @ {entry} | SL {stop} | Tgt {target} (Net R:R {net_rr:.2f}R)")
                break
        time.sleep(0.25)

    save_cloud_state(state)


if __name__ == "__main__":
    main()
