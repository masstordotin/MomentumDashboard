"""
Refresh momentum-picks.json from public NSE/Yahoo daily data.

The screening rules are intentionally aligned with SCREENER_REFERENCE.md.
This script uses only the Python standard library so it can run in a compact
local/GitHub Pages workflow without dependency setup.
"""

import csv
import http.cookiejar
import json
import math
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Dict, List, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor


NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
# NOTE: deliberately no "events=history" param, and range is a placeholder -
# see the "freshness" comment above merge_raw_candles() for why.
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval=1d"
OUTPUT_FILE = "momentum-picks.json"
NUM_PICKS = 15
MAX_WORKERS = 12

# NSE's own end-of-day settlement file (bhavcopy) - the actual source of
# truth for a session's close, published directly by the exchange rather
# than aggregated/cached by a third party like Yahoo. Used to override the
# freshest day of the Yahoo-derived series (see fetch_bhavcopy() below).
NSE_BASE_URL = "https://www.nseindia.com"
NSE_ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com"
NSE_LEGACY_ARCHIVE_BASE_URL = "https://archives.nseindia.com"
SECURITY_BHAVCOPY_PATH_TEMPLATE = "/products/content/sec_bhavdata_full_{date}.csv"
# NSE fronts its data endpoints with bot detection that rejects requests
# lacking cookies from a prior "normal" page visit. Walking these paths
# first (discarding their bodies, keeping only the cookies) makes the
# bhavcopy request below look like it came from a browser that actually
# browsed the site, rather than a bare script.
NSE_WARM_UP_PATHS = (
    "/get-quotes-equity-historical-data",
    "/get-quotes/equity?symbol=RELIANCE",
    "/market-data/live-market-indices",
    "/products-services/indices-nifty500-index",
    "/market-data/live-equity-market?symbol=NIFTY%20500",
    "/",
)

# NSE trades in India Standard Time; "asOf" is anchored to IST midnight so the
# label can't drift by a calendar day depending on the viewer's timezone.
IST_OFFSET = timedelta(hours=5, minutes=30)


def epoch_to_ist_asof(epoch_seconds: float) -> str:
    ist_date = (datetime.fromtimestamp(epoch_seconds, tz=timezone.utc) + IST_OFFSET).date()
    return f"{ist_date.isoformat()}T00:00:00+05:30"


HEADERS = {
    "User-Agent": "Mozilla/5.0 MomentumDashboard/1.0",
    "Accept": "text/csv,application/json,text/plain,*/*",
    # Yahoo's long-range chart endpoint (range=1y) appears to sit behind a
    # more aggressive CDN/edge cache than its short-range endpoint - that's
    # what was causing the screener to qualify stocks off a session-old
    # close even after the asOf-labeling fix made the staleness visible.
    # These headers ask any caching layer in between to not serve a stale hit.
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# NSE's site is picky about looking like a real browser (User-Agent alone
# isn't enough - full browser-shaped headers matter for getting past its
# bot detection during warm-up and the bhavcopy fetch itself).
NSE_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
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
        timestamps = result.get("timestamp") or []
        quote_data = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None

    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    volumes: List[float] = []
    kept_timestamps: List[float] = []

    for ts, close, high, low, volume in zip(
        timestamps,
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
        kept_timestamps.append(ts)

    if len(closes) < 210:
        return None

    return {"close": closes, "high": highs, "low": lows, "volume": volumes, "timestamp": kept_timestamps}


def merge_raw_candles(base_raw: Dict[str, Any], overlay_raw: Dict[str, Any], override: bool = False) -> Dict[str, Any]:
    """Combines two Yahoo-chart-shaped raw responses into one, keyed by
    calendar day (IST) so overlapping/duplicate trading days can't sneak in.

    override=False (the default): a day from `overlay_raw` is only added if
    `base_raw` doesn't already have it. This is what fixed the original
    staleness bug - Yahoo's long-range daily chart endpoint (range=1y) has
    been observed returning a stale tail (e.g. missing yesterday's already-
    closed session) even when a short-range request (range=5d) for the exact
    same symbol/interval already has it. Both sides are the same underlying
    source, so we only ever expect to fill gaps, not disagreements.

    override=True: a day from `overlay_raw` REPLACES base's version of that
    day, in addition to filling gaps. Used when `overlay_raw` comes from NSE's
    own bhavcopy - the exchange's official settlement file, and explicitly
    the more authoritative source when it disagrees with a third-party
    aggregator like Yahoo for the same day.
    """

    def extract(raw: Dict[str, Any]):
        try:
            result = raw["chart"]["result"][0]
            timestamps = result.get("timestamp") or []
            quote = result["indicators"]["quote"][0]
            return timestamps, quote
        except (KeyError, IndexError, TypeError):
            return [], {}

    base_ts, base_quote = extract(base_raw)
    overlay_ts, overlay_quote = extract(overlay_raw)

    day_index: Dict[str, int] = {}
    merged_ts: List[float] = []
    merged_close: List[float] = []
    merged_high: List[float] = []
    merged_low: List[float] = []
    merged_volume: List[float] = []

    def add_or_replace(day: str, ts, close, high, low, volume, allow_replace: bool) -> None:
        if day in day_index:
            if allow_replace:
                idx = day_index[day]
                merged_ts[idx] = ts
                merged_close[idx] = close
                merged_high[idx] = high
                merged_low[idx] = low
                merged_volume[idx] = volume
            return
        day_index[day] = len(merged_ts)
        merged_ts.append(ts)
        merged_close.append(close)
        merged_high.append(high)
        merged_low.append(low)
        merged_volume.append(volume)

    for ts, close, high, low, volume in zip(
        base_ts,
        base_quote.get("close", []),
        base_quote.get("high", []),
        base_quote.get("low", []),
        base_quote.get("volume", []),
    ):
        add_or_replace(epoch_to_ist_asof(ts)[:10], ts, close, high, low, volume, allow_replace=False)

    for i, ts in enumerate(overlay_ts):
        try:
            add_or_replace(
                epoch_to_ist_asof(ts)[:10],
                ts,
                overlay_quote["close"][i],
                overlay_quote["high"][i],
                overlay_quote["low"][i],
                overlay_quote["volume"][i],
                allow_replace=override,
            )
        except (KeyError, IndexError):
            continue

    # Splicing/replacing can leave things out of chronological order -
    # re-sort so clean_candles()'s downstream "most recent is last"
    # assumption holds.
    order = sorted(range(len(merged_ts)), key=lambda i: merged_ts[i])
    return {
        "chart": {
            "result": [{
                "timestamp": [merged_ts[i] for i in order],
                "indicators": {"quote": [{
                    "close": [merged_close[i] for i in order],
                    "high": [merged_high[i] for i in order],
                    "low": [merged_low[i] for i in order],
                    "volume": [merged_volume[i] for i in order],
                }]},
            }]
        }
    }


def _bhav_row_to_raw(bhav_row: Dict[str, Any]) -> Dict[str, Any]:
    """Wraps a single bhavcopy row ({close, high, low, volume, asOf}) in the
    same Yahoo-chart-shaped structure merge_raw_candles() expects, so the two
    sources can be combined with the exact same merge logic. The synthetic
    timestamp is set to 09:15 IST (NSE's market-open time) on the bhavcopy's
    date - matching the convention Yahoo's own daily bars use - purely so
    day-bucketing and chronological sorting behave consistently."""
    day = datetime.strptime(bhav_row["asOf"][:10], "%Y-%m-%d")
    market_open_ist = day.replace(hour=9, minute=15)
    epoch = int((market_open_ist - IST_OFFSET).replace(tzinfo=timezone.utc).timestamp())
    return {
        "chart": {
            "result": [{
                "timestamp": [epoch],
                "indicators": {"quote": [{
                    "close": [bhav_row["close"]],
                    "high": [bhav_row["high"]],
                    "low": [bhav_row["low"]],
                    "volume": [bhav_row["volume"]],
                }]},
            }]
        }
    }


def _drop_unconfirmed_today(raw: Dict[str, Any], today_str: str, bhav_day: Optional[str]) -> Dict[str, Any]:
    """Yahoo's short-range daily fetch (added to merge_raw_candles's base
    series) can include TODAY's still-forming intraday bar whenever this
    script runs during live NSE market hours - its "close" is just the
    latest traded price (still changing) and its "volume" is only however
    much has traded so far that session, nowhere near a full day's volume.
    Treating that as a genuine completed candle is exactly what caused the
    screener to qualify zero candidates: partial-day volume divided into a
    30-day average of FULL-day volumes fails the volRatio>=1.5 gate almost
    universally, and which stocks happen to clear it becomes a function of
    what time of day the script happened to run - explaining why two runs
    on "the same criteria" produced wildly different results.

    Drops any candle dated today (IST) UNLESS NSE's own bhavcopy has already
    published that exact date - bhavcopy is only ever generated after the
    session settles, so its presence is proof the day is genuinely complete,
    not a live snapshot.
    """
    if bhav_day == today_str:
        return raw  # today's session is confirmed complete/published - safe to keep as-is

    try:
        result = raw["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return raw

    keep_idx = [i for i, ts in enumerate(timestamps) if epoch_to_ist_asof(ts)[:10] != today_str]
    if len(keep_idx) == len(timestamps):
        return raw  # nothing dated today to drop

    def pick(values: List[Any]) -> List[Any]:
        return [values[i] for i in keep_idx if i < len(values)]

    return {
        "chart": {
            "result": [{
                "timestamp": pick(timestamps),
                "indicators": {"quote": [{
                    "close": pick(quote.get("close", [])),
                    "high": pick(quote.get("high", [])),
                    "low": pick(quote.get("low", [])),
                    "volume": pick(quote.get("volume", [])),
                }]},
            }]
        }
    }


def _warm_up_nse_session(opener, retries: int = 2, backoff_base: float = 1.5) -> None:
    """Visits a short sequence of real NSE pages (discarding their bodies -
    only the cookies collected by `opener`'s cookie jar matter) so the
    bhavcopy request below looks like it came from a browser that actually
    browsed the site, not a bare script. NSE's data endpoints reject requests
    lacking this. Failures on individual warm-up pages are swallowed - we
    only give up on the whole warm-up if every path fails every retry."""
    any_success = False
    for path in NSE_WARM_UP_PATHS:
        for attempt in range(retries):
            try:
                req = Request(NSE_BASE_URL + path, headers=NSE_BROWSER_HEADERS)
                with opener.open(req, timeout=15) as resp:
                    resp.read(2048)
                any_success = True
                break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(backoff_base * (attempt + 1))
    if not any_success:
        raise RuntimeError("NSE session warm-up failed on every path")


def fetch_bhavcopy(max_lookback_days: int = 7, retries: int = 3, backoff_base: float = 2.0) -> Optional[Dict[str, Dict[str, Any]]]:
    """Fetches NSE's official end-of-day bhavcopy - the exchange's own
    settlement file with every listed security's OHLC/volume for one trading
    day - and returns {SYMBOL: {"close", "high", "low", "volume", "asOf"}}
    for EQ-series rows. This is the actual source of truth for a session's
    close, sidestepping whatever caching/lag a third-party aggregator
    (Yahoo) might have.

    Tries today's IST date first, then steps backward to cover weekends/
    holidays/not-yet-published files, up to max_lookback_days. Every network
    call is retried with backoff. Returns None - never raises - on total
    failure, so a run can fall back to Yahoo-only data instead of losing the
    whole screen: NSE is known to intermittently block datacenter/CI IPs
    even with a correct session warm-up, and this must not be fatal.
    """
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))

    warmed_up = False
    for attempt in range(retries):
        try:
            _warm_up_nse_session(opener)
            warmed_up = True
            break
        except Exception:
            if attempt < retries - 1:
                time.sleep(backoff_base * (2 ** attempt))
    if not warmed_up:
        print("bhavcopy: could not establish an NSE session after retries - falling back to Yahoo-only data")
        return None

    today_ist = (datetime.now(timezone.utc) + IST_OFFSET).date()

    for offset in range(max_lookback_days):
        day = today_ist - timedelta(days=offset)
        date_str = day.strftime("%d%m%Y")
        url = NSE_ARCHIVE_BASE_URL + SECURITY_BHAVCOPY_PATH_TEMPLATE.format(date=date_str)

        content: Optional[str] = None
        for attempt in range(retries):
            try:
                req = Request(url, headers=NSE_BROWSER_HEADERS)
                with opener.open(req, timeout=20) as resp:
                    content = resp.read().decode("utf-8", errors="replace")
                break
            except Exception:
                if attempt < retries - 1:
                    time.sleep(backoff_base * (2 ** attempt))

        if not content:
            continue  # this date's file isn't available (weekend/holiday/blocked) - try an earlier date

        try:
            rows = list(csv.DictReader(StringIO(content)))
        except Exception:
            continue

        as_of = f"{day.isoformat()}T00:00:00+05:30"
        result: Dict[str, Dict[str, Any]] = {}
        for raw_row in rows:
            row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items()}
            if row.get("SERIES") != "EQ":
                continue
            symbol = row.get("SYMBOL", "")
            if not symbol:
                continue
            try:
                close = float(row.get("CLOSE_PRICE", "0") or 0)
                high = float(row.get("HIGH_PRICE", "0") or 0)
                low = float(row.get("LOW_PRICE", "0") or 0)
                volume = float(row.get("TTL_TRD_QNTY", "0") or 0)
            except ValueError:
                continue
            if close <= 0 or volume <= 0:
                continue
            result[symbol] = {"close": close, "high": high, "low": low, "volume": volume, "asOf": as_of}

        if result:
            print(f"bhavcopy: fetched {len(result)} EQ symbols for {day.isoformat()}")
            return result
        # empty/unparseable file for this date - try an earlier one

    print(f"bhavcopy: no usable file found in the last {max_lookback_days} days - falling back to Yahoo-only data")
    return None


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

    timestamps = candles.get("timestamp") or []
    as_of = epoch_to_ist_asof(timestamps[-1]) if timestamps else None

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
        "asOf": as_of,
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
        "asOf": metrics.get("asOf"),
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


def screen_one(meta: Dict[str, str], bhavcopy_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    symbol = yahoo_symbol(meta["symbol"])
    long_raw = fetch_json(YAHOO_CHART_URL.format(symbol=symbol, range_="1y"), timeout=20)
    # A short-range request is fetched alongside the 1y history request and
    # merged in - see merge_raw_candles() for why this is necessary (the
    # long-range endpoint can lag a session or more behind). If this second
    # call fails for any reason we still proceed on the long-range data alone
    # rather than dropping the symbol entirely.
    try:
        short_raw = fetch_json(YAHOO_CHART_URL.format(symbol=symbol, range_="5d"), timeout=20)
    except Exception:
        short_raw = {}
    raw = merge_raw_candles(long_raw, short_raw)

    bhav_row = (bhavcopy_map or {}).get(meta["symbol"])
    bhav_day = bhav_row["asOf"][:10] if bhav_row else None

    # See _drop_unconfirmed_today() - the short-range fetch above can include
    # today's still-forming intraday bar during live market hours, which is
    # not a real completed session and must never be scored as one.
    today_ist_str = (datetime.now(timezone.utc) + IST_OFFSET).date().isoformat()
    raw = _drop_unconfirmed_today(raw, today_ist_str, bhav_day)

    # NSE's own bhavcopy, when available, is the actual source of truth for
    # the latest close - override whatever Yahoo has for that day (or add it
    # if Yahoo is missing it entirely).
    if bhav_row:
        raw = merge_raw_candles(raw, _bhav_row_to_raw(bhav_row), override=True)

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

    # Fetched once for the whole universe (bhavcopy is a single file covering
    # every listed security), not per-symbol. None if NSE couldn't be reached
    # - screen_one() falls back to Yahoo-only data per-symbol in that case.
    bhavcopy_map = fetch_bhavcopy()

    picks: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(screen_one, meta, bhavcopy_map): meta for meta in universe}
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
