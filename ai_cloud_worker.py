import json
import os
import csv
import io
import urllib.request
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
LEDGER_FILE = "cloud_ledger.json"

# Multi-cap NSE basket covering Large Cap, Mid Cap, Small Cap & Liquid Penny Stocks (< Rs 100)
CORE_MULTI_CAP_UNIVERSE = [
    # Large Cap
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "LT", "AXISBANK", "TATAMOTORS",
    # Mid Cap & PSU
    "BEL", "HAL", "TRENT", "ZOMATO", "BHEL", "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "SAIL",
    # Small Cap & Liquid Penny (< Rs 100)
    "IRFC", "RVNL", "SUZLON", "IDEA", "YESBANK", "NHPC", "SJVN", "HUDCO", "NBCC", "RENUKA", "UCOBANK", "MAHABANK"
]


def fetch_nse_equity_master():
    """Fetches official NSE EQUITY_L.csv master list of all ~2,200+ listed equities."""
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/csv,*/*",
            "Referer": "https://www.nseindia.com/"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            eq_symbols = []
            for row in reader:
                sym = (row.get("SYMBOL") or "").strip()
                series = (row.get(" SERIES") or row.get("SERIES") or "").strip()
                # Only allow EQ series (intraday MIS eligible; excludes BE/T2T trade-to-trade penny traps)
                if sym and series == "EQ":
                    eq_symbols.append(sym)
            return eq_symbols
    except Exception:
        return []


def fetch_yahoo_intraday(symbol):
    """Fetches live 5m intraday candles from Yahoo Finance query1/query2 endpoints."""
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}.NS?interval=5m&range=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result = data["chart"]["result"][0]
                meta = result["meta"]
                quote = result["indicators"]["quote"][0]
                closes = [c for c in quote.get("close", []) if c is not None]
                highs = [h for h in quote.get("high", []) if h is not None]
                lows = [l for l in quote.get("low", []) if l is not None]
                vols = [v for v in quote.get("volume", []) if v is not None]
                if len(closes) < 5:
                    continue
                ltp = float(meta.get("regularMarketPrice") or closes[-1])
                prev_close = float(meta.get("chartPreviousClose") or closes[0])
                total_vol = int(sum(vols))
                return {
                    "symbol": symbol,
                    "ltp": round(ltp, 2),
                    "prev_close": round(prev_close, 2),
                    "day_high": round(max(highs), 2),
                    "day_low": round(min(lows), 2),
                    "closes": closes,
                    "volume": total_vol
                }
        except Exception:
            continue
    return None


def classify_cap_tier(ltp):
    if ltp < 100:
        return "PENNY / SMALL (<₹100)"
    elif ltp < 500:
        return "SMALL / MID CAP"
    return "LARGE / MID CAP"


def evaluate_70_30_setup(snap):
    """Evaluates momentum, VWAP proxy, liquidity gate (>=5L vol), and 1:2 R:R."""
    ltp = snap["ltp"]
    prev = snap["prev_close"]
    closes = snap["closes"]
    vol = snap["volume"]

    # Liquidity & circuit-lock guard (critical for penny/small cap intraday safety)
    if vol < 500000 or ltp < 5.0:
        return None

    chg_pct = ((ltp - prev) / prev) * 100.0 if prev > 0 else 0.0
    # Avoid stocks locked near +/- 9.5% or 19.5% circuit limits
    if abs(chg_pct) >= 9.2:
        return None

    ema_fast = sum(closes[-5:]) / 5.0
    ema_slow = sum(closes[-12:]) / min(len(closes), 12)
    vwap_proxy = (snap["day_high"] + snap["day_low"] + ltp) / 3.0

    # High-probability 70/30 LONG breakout alignment
    if ltp > vwap_proxy and ema_fast > ema_slow and 0.4 <= chg_pct <= 5.5:
        sl_dist = max(round(ltp * 0.0075, 2), 0.15)
        sl = round(ltp - sl_dist, 2)
        target = round(ltp + (sl_dist * 2.1), 2)
        qty = max(1, int(25000 // ltp))  # Rs 25,000 paper position sizing
        return {
            "side": "BUY",
            "entry": ltp,
            "sl": sl,
            "target": target,
            "qty": qty,
            "tier": classify_cap_tier(ltp),
            "confidence": min(92, round(72 + chg_pct * 2.5, 1))
        }
    return None


def load_ledger():
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "engine": "NSE 70/30 Multi-Cap Cloud AI Worker",
        "started_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
        "last_heartbeat": "",
        "nse_eq_master_count": 0,
        "market_status": "CLOSED",
        "open_positions": [],
        "closed_trades": [],
        "live_scanner_snapshot": [],
        "daily_pnl": {}
    }


def main():
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    time_str = now_ist.strftime("%H:%M:%S IST")
    hm = now_ist.hour * 60 + now_ist.minute
    is_weekday = now_ist.weekday() < 5
    is_market_open = is_weekday and (9 * 60 + 15 <= hm <= 15 * 60 + 30)

    ledger = load_ledger()
    ledger["last_heartbeat"] = f"{today_str} {time_str}"
    ledger["market_status"] = "OPEN" if is_market_open else "CLOSED"

    eq_master = fetch_nse_equity_master()
    if eq_master:
        ledger["nse_eq_master_count"] = len(eq_master)

    # Rotate 12 extra EQ symbols from the full ~2,200 NSE master each run alongside core multi-cap basket
    scan_symbols = list(CORE_MULTI_CAP_UNIVERSE)
    if eq_master:
        offset = (now_ist.minute * 12) % max(1, len(eq_master) - 12)
        for s in eq_master[offset:offset + 12]:
            if s not in scan_symbols:
                scan_symbols.append(s)

    snapshots = {}
    scanner_rows = []
    for sym in scan_symbols:
        snap = fetch_yahoo_intraday(sym)
        if snap:
            snapshots[sym] = snap
            chg = round(((snap["ltp"]
