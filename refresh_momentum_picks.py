"""
Refresh momentum-picks.json from public NSE/Yahoo daily data.

The screening rules are intentionally aligned with SCREENER_REFERENCE.md.
This script uses only the Python standard library so it can run in a compact
local/GitHub Pages workflow without dependency setup.
"""

import csv
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen


NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&events=history"
OUTPUT_FILE = "momentum-picks.json"
NUM_PICKS = 15
MAX_WORKERS = 12


HEADERS = {
    "User-Agent": "Mozilla/5.0 MomentumDashboard/1.0",
    "Accept": "text/csv,application/json,text/plain,*/*",
}


def fetch_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def ema(values: List[float], period: int) -> List[Optional[float]]:
    if len(values) < period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * len(values)
    multiplier = 2 / (period + 1)
    current = sum(values[:period]) / period
    result[period - 1] = current

    for i in range(period, len(values)):
        current = (values[i] - current) * multiplier + current
        result[i] = current

    return result


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    if len(values) <= period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * len(values)
    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0)
        loss = abs(min(change, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[i] = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

    return result


def pct_return(values: List[float], sessions: int) -> Optional[float]:
    if len(values) <= sessions or values[-sessions - 1] <= 0:
        return None
    return ((values[-1] / values[-sessions - 1]) - 1) * 100


def clean_candles(raw: Dict[str, Any]) -> Optional[Dict[str, List[float]]]:
    try:
        result = raw["chart"]["result"][0]
        quote_data = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None

    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    volumes: List[float] = []

    for close, high, low, volume in zip(
        quote_data.get("close", []),
        quote_data.get("high", []),
        quote_data.get("low", []),
        quote_data.get("volume", []),
    ):
        if close is None or high is None or low is None or volume is None:
            continue
        if close <= 0 or volume <= 0:
            continue
        closes.append(float(close))
        highs.append(float(high))
        lows.append(float(low))
        volumes.append(float(volume))

    if len(closes) < 210:
        return None

    return {"close": closes, "high": highs, "low": lows, "volume": volumes}


def infer_bucket(symbol: str, rank_index: int) -> str:
    if rank_index < 100:
        return "Large"
    if rank_index < 250:
        return "Mid"
    return "Small"


def setup_type(price: float, highs: List[float], ema50_value: float, vol_ratio: float, ret_1m: float) -> str:
    recent_high = max(highs[-21:-1])
    distance_to_high = (price / recent_high - 1) * 100
    distance_to_ema = (price / ema50_value - 1) * 100

    if vol_ratio >= 2.0 and ret_1m >= 8 and distance_to_high >= -1.5:
        return "Momentum expansion"
    if distance_to_high >= -2.0:
        return "Breakout continuation"
    if 0 <= distance_to_ema <= 8:
        return "Pullback continuation"
    return "Breakout continuation"


def score_candidate(candidate: Dict[str, Any]) -> float:
    rsi_score = max(0, 20 - abs(candidate["rsi"] - 64) * 1.6)
    trend_score = min(18, ((candidate["price"] / candidate["ema50"]) - 1) * 100 * 1.8)
    volume_score = min(18, candidate["volRatio"] * 7)
    ret_score = min(28, candidate["ret1m"] * 0.7 + candidate["ret3m"] * 0.45 + candidate["ret6m"] * 0.25)
    structure_score = 16 if candidate["higherHigh"] and candidate["higherLow"] else 8
    volatility_penalty = max(0, candidate["volatility"] - 3.5) * 2
    return round(max(0, min(99, rsi_score + trend_score + volume_score + ret_score + structure_score - volatility_penalty)), 0)


def qualifies(candles: Dict[str, List[float]]) -> Optional[Dict[str, Any]]:
    close = candles["close"]
    high = candles["high"]
    low = candles["low"]
    volume = candles["volume"]

    ema50 = ema(close, 50)[-1]
    ema200 = ema(close, 200)[-1]
    rsi_series = rsi(close)
    current_rsi = rsi_series[-1]
    rsi_4_sessions_ago = rsi_series[-4]

    if ema50 is None or ema200 is None or current_rsi is None or rsi_4_sessions_ago is None:
        return None

    price = close[-1]
    avg_volume_30 = statistics.mean(volume[-31:-1])
    vol_ratio = volume[-1] / avg_volume_30 if avg_volume_30 > 0 else 0

    ret1m = pct_return(close, 21)
    ret3m = pct_return(close, 63)
    ret6m = pct_return(close, 126)
    if ret1m is None or ret3m is None or ret6m is None:
        return None

    current_high = max(high[-21:])
    previous_high = max(high[-42:-21])
    current_low = min(low[-21:])
    previous_low = min(low[-42:-21])
    higher_high = current_high > previous_high
    higher_low = current_low > previous_low

    daily_returns = [
        abs((close[i] / close[i - 1] - 1) * 100)
        for i in range(len(close) - 20, len(close))
        if close[i - 1] > 0
    ]
    volatility = statistics.mean(daily_returns) if daily_returns else 0

    if not (price > ema50 > ema200):
        return None
    if not (55 <= current_rsi <= 75):
        return None
    if current_rsi < rsi_4_sessions_ago - 4:
        return None
    if vol_ratio < 1.5:
        return None
    if not (higher_high and higher_low):
        return None
    if ret1m < 0 or ret3m < 0 or ret6m < 0:
        return None
    if price / ema50 > 1.22 or ret1m > 35:
        return None

    return {
        "price": price,
        "rsi": current_rsi,
        "ema50": ema50,
        "ema200": ema200,
        "volRatio": vol_ratio,
        "ret1m": ret1m,
        "ret3m": ret3m,
        "ret6m": ret6m,
        "higherHigh": higher_high,
        "higherLow": higher_low,
        "volatility": volatility,
        "setup": setup_type(price, high, ema50, vol_ratio, ret1m),
    }


def get_universe() -> List[Dict[str, str]]:
    content = fetch_text(NSE_500_URL)
    rows = list(csv.DictReader(StringIO(content)))
    return [
        {
            "name": row["Company Name"].replace(" Ltd.", "").replace(" Limited", ""),
            "sector": row["Industry"],
            "symbol": row["Symbol"],
            "bucket": infer_bucket(row["Symbol"], index),
        }
        for index, row in enumerate(rows)
        if row.get("Series") == "EQ" and not row.get("Symbol", "").startswith("DUMMY")
    ]


def yahoo_symbol(nse_symbol: str) -> str:
    return quote(f"{nse_symbol}.NS", safe="")


def build_pick(meta: Dict[str, str], metrics: Dict[str, Any]) -> Dict[str, Any]:
    price = metrics["price"]
    ema50 = metrics["ema50"]
    recent_support = max(ema50, price * 0.94)
    buy_low = price * 0.985 if metrics["setup"] != "Pullback continuation" else max(ema50, price * 0.96)
    buy_high = price * 1.005
    invalid = recent_support * 0.985

    return {
        "rank": 0,
        "name": meta["name"],
        "symbol": meta["symbol"],
        "sector": meta["sector"],
        "bucket": meta["bucket"],
        "score": int(metrics["score"]),
        "price": round(price, 2),
        "rsi": round(metrics["rsi"], 2),
        "ema50": round(metrics["ema50"], 2),
        "ema200": round(metrics["ema200"], 2),
        "volRatio": round(metrics["volRatio"], 2),
        "ret1m": round(metrics["ret1m"], 1),
        "ret3m": round(metrics["ret3m"], 1),
        "ret6m": round(metrics["ret6m"], 1),
        "setup": metrics["setup"],
        "buyZone": f"Rs {buy_low:.0f} - Rs {buy_high:.0f} near trigger/base hold",
        "invalid": f"Daily close below Rs {invalid:.0f}",
        "thesis": (
            f"Qualifies on price > 50 EMA > 200 EMA, RSI stability, {metrics['volRatio']:.2f}x volume, "
            f"and a recent higher-high/higher-low structure in a strong {meta['sector']} pocket."
        ),
        "indicator": (
            f"RSI {metrics['rsi']:.2f} stable, EMA50 Rs {metrics['ema50']:.0f} > EMA200 Rs {metrics['ema200']:.0f}, "
            f"volume {metrics['volRatio']:.2f}x 30-day avg, returns {metrics['ret1m']:.1f}%/{metrics['ret3m']:.1f}%/{metrics['ret6m']:.1f}%."
        ),
    }


def screen_one(meta: Dict[str, str]) -> Optional[Dict[str, Any]]:
    symbol = yahoo_symbol(meta["symbol"])
    raw = fetch_json(YAHOO_CHART_URL.format(symbol=symbol), timeout=20)
    candles = clean_candles(raw)
    if not candles:
        return None
    metrics = qualifies(candles)
    if not metrics:
        return None
    metrics["score"] = score_candidate(metrics)
    return build_pick(meta, metrics)


def main() -> None:
    universe = get_universe()
    picks: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(screen_one, meta): meta for meta in universe}
        for index, future in enumerate(as_completed(futures), start=1):
            meta = futures[future]
            try:
                pick = future.result()
                if pick:
                    picks.append(pick)
            except Exception as exc:
                print(f"skip {meta['symbol']}: {exc}")

            if index % 50 == 0:
                print(f"screened {index}/{len(universe)}; candidates={len(picks)}")

    # If strict structure filtering produces too few candidates on a quieter day,
    # rerun with the same criteria except the weekly HH/HL requirement relaxed.
    if len(picks) < NUM_PICKS:
        print(f"strict screen found {len(picks)} candidates; keeping strict results only")

    picks.sort(key=lambda item: (item["score"], item["ret1m"], item["ret3m"], item["volRatio"]), reverse=True)
    picks = picks[:NUM_PICKS]
    for rank, pick in enumerate(picks, start=1):
        pick["rank"] = rank

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(picks, file, indent=2, ensure_ascii=False)
        file.write("\n")

    print(f"wrote {len(picks)} picks to {OUTPUT_FILE} at {datetime.now(timezone.utc).isoformat()}")
    for pick in picks:
        print(f"{pick['rank']:>2}. {pick['symbol']:<14} score={pick['score']} setup={pick['setup']}")


if __name__ == "__main__":
    main()
