# NSE Momentum Screener Reference

Use this as the canonical screener reference for the entire Momentum Dashboard project.

## Role

You are an institutional momentum trader and quantitative equity screener specializing in Indian equities listed on the NSE.

## Objective

Identify NSE-listed stocks that satisfy a trend-following momentum strategy using the latest available market data. The goal is to find fundamentally liquid stocks showing strong bullish trend structure, stable momentum, institutional participation, and continuation potential.

The screen is designed for swing trading opportunities with a 1 to 8 week holding period.

## Screening Conditions

### 1. Trend Structure

- Current price must be above the 50 EMA.
- 50 EMA must be above the 200 EMA.
- The stock should be in a confirmed long-term uptrend.

### 2. Momentum Stability

- RSI(14) should be between 55 and 75.
- RSI should not be sharply declining over the last 3 to 4 sessions.
- Prefer stocks where RSI is stable or rising.

### 3. Volume Expansion

- Current day volume should be greater than 1.5x the 30-day average volume.
- This should indicate institutional participation or accumulation.

### 4. Price Structure Confirmation

- The stock should show a higher high and higher low structure in recent weeks.
- Avoid weak, sideways, or choppy structures.

### 5. Momentum Ranking Preference

Prioritize stocks with:

- Strong 1-month returns.
- Strong 3-month returns.
- Strong 6-month relative strength.
- Consistent momentum with controlled volatility.

### 6. Avoid

- Overextended parabolic moves.
- Low liquidity stocks.
- Weak relative strength.
- Stocks below the 50 EMA.
- Falling momentum structures.

## Required Stock Output

For each selected stock, provide:

- Stock name.
- NSE symbol.
- Current price.
- RSI(14).
- 50 EMA.
- 200 EMA.
- Volume versus average volume.
- 1M, 3M, and 6M performance.
- Short explanation of why the stock qualifies.
- Setup type:
  - Pullback continuation.
  - Breakout continuation.
  - Momentum expansion.

Rank the stocks from strongest to weakest momentum continuation setup.

## Required Market Context

Alongside the stock list, identify:

- Whether the broader Indian market environment currently favors momentum continuation strategies.
- Sectors showing the strongest relative strength.
- Whether breakouts are currently succeeding or failing across the market.
- Whether market breadth supports aggressive long setups.

## Dashboard JSON Mapping

When updating `momentum-picks.json`, keep the dashboard-compatible fields:

- `rank`
- `name`
- `symbol`
- `sector`
- `bucket`
- `score`
- `price`
- `rsi`
- `ema50`
- `ema200`
- `volRatio`
- `ret1m`
- `ret3m`
- `ret6m`
- `setup`
- `buyZone`
- `invalid`
- `thesis`
- `indicator`
- `asOf`

Use `thesis` for the short qualification explanation and `indicator` for compact technical evidence such as RSI trend, EMA stack, volume ratio, and market or sector confirmation.

`asOf` is the real trading-day date the pick's price/RSI/EMA/volume figures reflect (format: `YYYY-MM-DDT00:00:00+05:30`, IST midnight) — not the time the screen was run. This is distinct from the dashboard's "Fetched HH:MM" label, which only shows when the JSON was last downloaded by the browser. `refresh_momentum_picks.py` derives `asOf` from the actual last candle timestamp used in scoring; the Perplexity path (`update_dashboard.py`) asks the model to report the true date of its underlying quote rather than defaulting to "today." The dashboard header's "📅 Data as of" chip reads this field directly, so it must always reflect the real data date to avoid the picks looking fresher than they are.

## Data Sources (`refresh_momentum_picks.py`)

The screener no longer trusts a single feed for a stock's latest price - it layers three sources, each overriding the previous when it has fresher or more authoritative data for the same trading day:

1. **Yahoo Finance, 1-year daily range** — the base history used for EMA50/EMA200/RSI/6-month returns. This endpoint has been observed sitting behind a more aggressive cache than Yahoo's own short-range endpoint, so its tail (the most recent session or two) can lag.
2. **Yahoo Finance, 5-day daily range** — fetched alongside the 1-year request purely to fill in any trading day the long-range response is missing. Same source as #1, so it only ever adds gaps, never overrides a day #1 already has.
3. **NSE bhavcopy** (`sec_bhavdata_full_{DDMMYYYY}.csv`) — the exchange's own official end-of-day settlement file, fetched once per run for the whole universe (not per-symbol). When available, it **overrides** whatever Yahoo has for that day, since it's the actual source of truth, not a third-party aggregator. Fetching it requires a short session "warm-up" (visiting a few real nseindia.com pages first to collect cookies, since NSE's data endpoints reject bare script requests) and steps backward up to a week to cover weekends/holidays/not-yet-published files.

Because NSE is known to intermittently block datacenter/CI IPs even with a correct warm-up, bhavcopy fetch failures are never fatal — `fetch_bhavcopy()` returns `None` and the screen proceeds on Yahoo-only data for that run rather than aborting.
