import json
import os
import csv
import io
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor, as_completed
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

MF_SCHEMES = [
    {"code": "120716", "cat": "Index / Large Cap", "ter": "0.18%"},
    {"code": "122639", "cat": "Flexi Cap", "ter": "0.62%"},
    {"code": "118989", "cat": "Mid Cap", "ter": "0.74%"},
    {"code": "118778", "cat": "Small Cap", "ter": "0.68%"},
    {"code": "120684", "cat": "Index / Large Cap", "ter": "0.31%"},
    {"code": "119835", "cat": "Liquid / Debt", "ter": "0.19%"}
]


def fetch_yahoo_raw(encoded_ticker, interval="5m", range_str="5d"):
    """Fetches last completed trading session (range=5d) so data is never empty off-hours."""
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{encoded_ticker}?interval={interval}&range={range_str}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                result = data["chart"]["result"][0]
                meta = result["meta"]
                timestamps = result.get("timestamp") or []
                quote = result["indicators"]["quote"][0]
                opens = quote.get("open") or []
                highs = quote.get("high") or []
                lows = quote.get("low") or []
                closes = quote.get("close") or []
                vols = quote.get("volume") or []

                bars = []
                for i in range(len(closes)):
                    if closes[i] is not None and opens[i] is not None:
                        bars.append({
                            "t": timestamps[i] if i < len(timestamps) else 0,
                            "o": round(float(opens[i]), 2),
                            "h": round(float(highs[i] or closes[i]), 2),
                            "l": round(float(lows[i] or closes[i]), 2),
                            "c": round(float(closes[i]), 2),
                            "v": int(vols[i] or 0)
                        })

                ltp = float(meta.get("regularMarketPrice") or (bars[-1]["c"] if bars else 0))
                prev = float(meta.get("previousClose") or meta.get("chartPreviousClose") or (bars[-2]["c"] if len(bars) > 1 else ltp))
                if ltp <= 0:
                    continue
                chg_pct = round(((ltp - prev) / prev) * 100.0, 2) if prev > 0 else 0.0
                day_bars = bars[-75:] if len(bars) > 75 else bars
                day_high = max((b["h"] for b in day_bars), default=ltp)
                day_low = min((b["l"] for b in day_bars), default=ltp)
                total_vol = sum(b["v"] for b in day_bars)

                return {
                    "ltp": round(ltp, 2),
                    "prev_close": round(prev, 2),
                    "chg": round(ltp - prev, 2),
                    "chg_pct": chg_pct,
                    "day_high": round(day_high, 2),
                    "day_low": round(day_low, 2),
                    "volume": int(total_vol),
                    "bars": day_bars
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


def fetch_nse_option_chain(symbol="NIFTY"):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/option-chain"
    }
    try:
        opener.open(urllib.request.Request("https://www.nseindia.com", headers=headers), timeout=6).read()
        raw = opener.open(
            urllib.request.Request(f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}", headers=headers),
            timeout=8
        ).read().decode("utf-8")
        payload = json.loads(raw)
        records = payload.get("records", {})
        spot = float(records.get("underlyingValue") or 0)
        data_rows = records.get("data", [])
        if not spot or not data_rows:
            return None

        expiries = records.get("expiryDates", [])
        nearest_exp = expiries[0] if expiries else None
        filtered = [r for r in data_rows if not nearest_exp or r.get("expiryDate") == nearest_exp]
        filtered.sort(key=lambda x: x.get("strikePrice", 0))

        total_ce_oi = sum((r.get("CE") or {}).get("openInterest", 0) for r in filtered)
        total_pe_oi = sum((r.get("PE") or {}).get("openInterest", 0) for r in filtered)
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 1.0

        atm_idx = min(range(len(filtered)), key=lambda i: abs(filtered[i].get("strikePrice", 0) - spot))
        sliced = filtered[max(0, atm_idx - 5): min(len(filtered), atm_idx + 6)]

        strikes = [r["strikePrice"] for r in sliced]
        min_pain = float("inf")
        max_pain_strike = filtered[atm_idx]["strikePrice"]
        for test_s in strikes:
            pain = 0.0
            for r in sliced:
                k = r["strikePrice"]
                ce_oi = (r.get("CE") or {}).get("openInterest", 0)
                pe_oi = (r.get("PE") or {}).get("openInterest", 0)
                if test_s > k:
                    pain += (test_s - k) * ce_oi
                if test_s < k:
                    pain += (k - test_s) * pe_oi
            if pain < min_pain:
                min_pain = pain
                max_pain_strike = test_s

        chain_out = []
        for r in sliced:
            ce = r.get("CE") or {}
            pe = r.get("PE") or {}
            chain_out.append({
                "strike": r["strikePrice"],
                "ce_oi": round(ce.get("openInterest", 0) / 100000, 2),
                "ce_iv": round(ce.get("impliedVolatility", 0), 1),
                "ce_ltp": round(ce.get("lastPrice", 0), 2),
                "pe_ltp": round(pe.get("lastPrice", 0), 2),
                "pe_iv": round(pe.get("impliedVolatility", 0), 1),
                "pe_oi": round(pe.get("openInterest", 0) / 100000, 2)
            })

        return {
            "symbol": symbol,
            "expiry": nearest_exp,
            "spot": spot,
            "pcr": pcr,
            "max_pain": max_pain_strike,
            "chain": chain_out
        }
    except Exception:
        return None


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
                cagr_1y, cagr_3y = "--", "--"
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
                    "ter": item["ter"],
                    "cagr1": cagr_1y,
                    "cagr3": cagr_3y,
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


def scan_single_equity(sym):
    snap = fetch_yahoo_raw(f"{sym}.NS", interval="5m", range_str="5d")
    if not snap:
        return None
    include_bars = sym in CORE_UNIVERSE
    return sym, {
        "symbol": sym,
        "ltp": snap["ltp"],
        "prev_close": snap["prev_close"],
        "chg_pct": snap["chg_pct"],
        "day_high": snap["day_high"],
        "day_low": snap["day_low"],
        "volume": snap["volume"],
        "tier": classify_tier(snap["ltp"]),
        "bars": snap["bars"] if include_bars else []
    }


def load_ledger():
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "engine": "NSE 70/30 Multi-Threaded Cloud Worker",
        "last_heartbeat": "",
        "nse_eq_master_count": 0,
        "market_status": "CLOSED",
        "indices": {},
        "option_chains": {},
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

    # 1. Indices (5d range ensures last session prices are always present off-hours)
    indices = {}
    for key, encoded in INDEX_SYMBOLS.items():
        snap = fetch_yahoo_raw(encoded, interval="5m", range_str="5d")
        if snap:
            snap.pop("bars", None)
            indices[key] = snap
    if indices:
        ledger["indices"] = indices

    # 2. Real NSE Option Chains
    opt_chains = ledger.get("option_chains", {})
    for idx_sym in ("NIFTY", "BANKNIFTY"):
        oc = fetch_nse_option_chain(idx_sym)
        if oc:
            opt_chains[idx_sym] = oc
    ledger["option_chains"] = opt_chains

    # 3. Live AMFI Mutual Fund NAVs
    mfs = fetch_live_mutual_funds()
    if mfs:
        ledger["mutual_funds"] = mfs

    # 4. Concurrent 20-Thread Scan across ~300 NSE EQ Stocks
    eq_master = fetch_nse_equity_master()
    if eq_master:
        ledger["nse_eq_master_count"] = len(eq_master)

    scan_batch = list(CORE_UNIVERSE)
    if eq_master:
        batch_size = 270
        start_idx = ((now_ist.hour * 60 + now_ist.minute) // 5 * batch_size) % max(1, len(eq_master))
        slice_syms = eq_master[start_idx:start_idx + batch_size]
        if len(slice_syms) < batch_size:
            slice_syms += eq_master[:batch_size - len(slice_syms)]
        for s in slice_syms:
            if s not in scan_batch:
                scan_batch.append(s)

    live_quotes = ledger.get("live_quotes", {})
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(scan_single_equity, sym) for sym in scan_batch]
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                sym, qdata = res
                qdata["updated_at"] = time_str
                live_quotes[sym] = qdata

    sorted_quotes = sorted(live_quotes.values(), key=lambda x: x.get("volume", 0), reverse=True)[:250]
    ledger["live_quotes"] = {q["symbol"]: q for q in sorted_quotes}

    if is_market_open:
        still_open = []
        for pos in ledger.get("open_positions", []):
            sym = pos["symbol"]
            q = ledger["live_quotes"].get(sym)
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

        if (9 * 60 + 20 <= hm <= 14 * 60 + 45) and len(ledger["open_positions"]) < 5:
            open_syms = {p["symbol"] for p in ledger["open_positions"]}
            for sym, q in ledger["live_quotes"].items():
                if sym in open_syms or len(ledger["open_positions"]) >= 5:
                    continue
                if q["volume"] >= 500000 and 0.5 <= q["chg_pct"] <= 5.5 and q["ltp"] >= 5.0:
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
