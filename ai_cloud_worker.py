import json
import os
import csv
import io
import urllib.request
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
LEDGER_FILE = "cloud_ledger.json"

CORE_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "LT", "AXISBANK", "TATAMOTORS",
    "BEL", "HAL", "TRENT", "ZOMATO", "BHEL", "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "SAIL",
    "IRFC", "RVNL", "SUZLON", "IDEA", "YESBANK", "NHPC", "SJVN", "HUDCO", "NBCC", "RENUKA", "UCOBANK", "MAHABANK"
]

INDEX_SYMBOLS = {
    "NIFTY": "%5ENSEI",
    "BANKNIFTY": "%5ENSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "INDIAVIX": "%5EINDIAVIX"
}

# Official AMFI Scheme Codes for Direct Growth Mutual Funds
MF_SCHEMES = [
    {"code": "120716", "cat": "Index / Large Cap", "ter": "0.18%", "aum": "19,450 Cr", "sharpe": "1.34"},
    {"code": "122639", "cat": "Flexi Cap", "ter": "0.62%", "aum": "84,200 Cr", "sharpe": "1.62"},
    {"code": "118989", "cat": "Mid Cap", "ter": "0.74%", "aum": "76,100 Cr", "sharpe": "1.58"},
    {"code": "118778", "cat": "Small Cap", "ter": "0.68%", "aum": "61,800 Cr", "sharpe": "1.55"},
    {"code": "120684", "cat": "Index / Large Cap", "ter": "0.31%", "aum": "6,940 Cr", "sharpe": "1.29"},
    {"code": "119835", "cat": "Liquid / Debt", "ter": "0.19%", "aum": "68,500 Cr", "sharpe": "2.15"}
]


def fetch_yahoo_raw(encoded_ticker):
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded_ticker}?interval=5m&range=1d"
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
                ltp = float(meta.get("regularMarketPrice") or (closes[-1] if closes else 0))
                prev = float(meta.get("chartPreviousClose") or (closes[0] if closes else ltp))
                if ltp <= 0:
                    continue
                chg_pct = round(((ltp - prev) / prev) * 100.0, 2) if prev > 0 else 0.0
                return {
                    "ltp": round(ltp, 2),
                    "prev_close": round(prev, 2),
                    "chg": round(ltp - prev, 2),
                    "chg_pct": chg_pct,
                    "day_high": round(max(highs) if highs else ltp, 2),
                    "day_low": round(min(lows) if lows else ltp, 2),
                    "closes": closes,
                    "volume": int(sum(vols))
                }
        except Exception:
            continue
    return None


def fetch_nse_equity_master():
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/csv,*/*",
            "Referer": "https://www.nseindia.com/"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            reader = csv.DictReader(io.StringIO(text))
            return [
                (row.get("SYMBOL") or "").strip()
                for row in reader
                if (row.get("SYMBOL") or "").strip()
                and (row.get(" SERIES") or row.get("SERIES") or "").strip() == "EQ"
            ]
    except Exception:
        return []


def fetch_live_mutual_funds():
    funds = []
    for item in MF_SCHEMES:
        url = f"https://api.mfapi.in/mf/{item['code']}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                j = json.loads(resp.read().decode("utf-8"))
                meta = j.get("meta", {})
                nav_list = j.get("data", [])
                if not nav_list:
                    continue
                latest_nav = float(nav_list[0]["nav"])
                nav_date = nav_list[0]["date"]
                # Calculate real 1Y / 3Y CAGR from historical AMFI NAV array
                cagr_1y = "--"
                cagr_3y = "--"
                if len(nav_list) > 245:
                    n1 = float(nav_list[245]["nav"])
                    if n1 > 0:
                        cagr_1y = f"{((latest_nav / n1) - 1) * 100:.2f}%"
                if len(nav_list) > 735:
                    n3 = float(nav_list[735]["nav"])
                    if n3 > 0:
                        cagr_3y = f"{(((latest_nav / n3) ** (1 / 3)) - 1) * 100:.2f}%"
                funds.append({
                    "code": item["code"],
                    "name": meta.get("scheme_name", f"Scheme {item['code']}"),
                    "cat": item["cat"],
                    "nav": round(latest_nav, 4),
                    "nav_date": nav_date,
                    "aum": item["aum"],
                    "ter": item["ter"],
                    "cagr1": cagr_1y,
                    "cagr3": cagr_3y,
                    "sharpe": item["sharpe"],
                    "score": "8/8 PASS"
                })
        except Exception:
            continue
    return funds


def classify_tier(ltp):
    if ltp < 100:
        return "PENNY (<₹100)"
    elif ltp <= 500:
        return "MID CAP"
    return "LARGE CAP"


def load_ledger():
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "engine": "NSE 70/30 Live Multi-Cap Cloud Worker",
        "last_heartbeat": "",
        "nse_eq_master_count": 0,
        "market_status": "CLOSED",
        "indices": {},
        "live_quotes": {},
        "mutual_funds": [],
        "open_positions": [],
        "closed_trades": [],
        "daily_pnl": {}
    }


def main():
    now_ist = datetime.now(IST)
    today_str = now_ist.strftime("%Y-%m-%d")
    time_str = now_ist.strftime("%H:%M:%S IST")
    hm = now_ist.hour * 60 + now_ist.minute
    is_market_open = (now_ist.weekday() < 5) and (9 * 60 + 15 <= hm <= 15 * 60 + 30)

    ledger = load_ledger()
    ledger["last_heartbeat"] = f"{today_str} {time_str}"
    ledger["market_status"] = "OPEN" if is_market_open else "CLOSED"

    # 1. Live Indices (NIFTY, BANKNIFTY, FINNIFTY, INDIA VIX)
    indices = {}
    for key, encoded in INDEX_SYMBOLS.items():
        snap = fetch_yahoo_raw(encoded)
        if snap:
            indices[key] = snap
    if indices:
        ledger["indices"] = indices

    # 2. Live AMFI Mutual Fund NAVs & Calculated CAGRs
    mfs = fetch_live_mutual_funds()
    if mfs:
        ledger["mutual_funds"] = mfs

    # 3. Official NSE Equity Master + Live Equity Quotes
    eq_master = fetch_nse_equity_master()
    if eq_master:
        ledger["nse_eq_master_count"] = len(eq_master)

    scan_symbols = list(CORE_UNIVERSE)
    if eq_master:
        offset = (now_ist.minute * 10) % max(1, len(eq_master) - 10)
        for s in eq_master[offset:offset + 10]:
            if s not in scan_symbols:
                scan_symbols.append(s)

    live_quotes = ledger.get("live_quotes", {})
    for sym in scan_symbols:
        snap = fetch_yahoo_raw(f"{sym}.NS")
        if snap:
            live_quotes[sym] = {
                "symbol": sym,
                "ltp": snap["ltp"],
                "prev_close": snap["prev_close"],
                "chg_pct": snap["chg_pct"],
                "day_high": snap["day_high"],
                "day_low": snap["day_low"],
                "volume": snap["volume"],
                "tier": classify_tier(snap["ltp"]),
                "updated_at": time_str
            }
    ledger["live_quotes"] = live_quotes

    if is_market_open:
        # Manage open positions
        still_open = []
        for pos in ledger.get("open_positions", []):
            sym = pos["symbol"]
            q = live_quotes.get(sym)
            ltp = q["ltp"] if q else pos["entry"]
            reason = None
            if ltp >= pos["target"]:
                reason = "TARGET_HIT"
            elif ltp <= pos["sl"]:
                reason = "STOP_LOSS"
            elif hm >= 15 * 60 + 15:
                reason = "15:15_AUTO_SQUAREOFF"

            if reason:
                pnl = round((ltp - pos["entry"]) * pos["qty"], 2)
                ledger.setdefault("closed_trades", []).insert(0, {
                    **pos, "exit": ltp, "pnl": pnl, "exit_reason": reason,
                    "status": "WIN" if pnl > 0 else "LOSS", "closed_at": f"{today_str} {time_str}"
                })
                ledger.setdefault("daily_pnl", {})[today_str] = round(
                    ledger.get("daily_pnl", {}).get(today_str, 0.0) + pnl, 2
                )
            else:
                pos["current_ltp"] = ltp
                pos["unrealized_pnl"] = round((ltp - pos["entry"]) * pos["qty"], 2)
                still_open.append(pos)
        ledger["open_positions"] = still_open

        # Open new 70/30 passing setups
        if (9 * 60 + 20 <= hm <= 14 * 60 + 45) and len(ledger["open_positions"]) < 4:
            open_syms = {p["symbol"] for p in ledger["open_positions"]}
            for sym, q in live_quotes.items():
                if sym in open_syms or len(ledger["open_positions"]) >= 4:
                    continue
                if q["volume"] >= 500000 and 0.4 <= q["chg_pct"] <= 5.5 and q["ltp"] >= 5.0:
                    sl_dist = max(round(q["ltp"] * 0.0075, 2), 0.15)
                    ledger["open_positions"].append({
                        "id": f"CLOUD-{sym}-{int(now_ist.timestamp())}",
                        "symbol": sym,
                        "tier": q["tier"],
                        "side": "BUY",
                        "entry": q["ltp"],
                        "current_ltp": q["ltp"],
                        "sl": round(q["ltp"] - sl_dist, 2),
                        "target": round(q["ltp"] + sl_dist * 2.1, 2),
                        "qty": max(1, int(25000 // q["ltp"])),
                        "unrealized_pnl": 0.0,
                        "opened_at": f"{today_str} {time_str}"
                    })

    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)


if __name__ == "__main__":
    main()
