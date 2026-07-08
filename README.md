# Momentum Dashboard - GitHub Pages Starter

This folder gives you a fast testing setup for your NSE momentum dashboard.

## Files

- `index.html` - your dashboard UI.
- `momentum-picks.json` - daily data payload from Perplexity or your screener.
- `portfolio-data.json` - your watchlist, paper trades and real holdings (Portfolio tab).
- `nse-sectors.json` - symbol -> {name, sector} lookup covering the full NSE 500 list, auto-refreshed by GitHub Actions.
- `portfolio-prices.json` - symbol -> {price, asOf} for everything in your watchlist/holdings/open paper trades, auto-refreshed roughly hourly during market hours by GitHub Actions.
- `update_dashboard.py` - refreshes the JSON picks through the Perplexity API and can push updates to GitHub.
- `refresh_momentum_picks.py` - standard-library alternative that screens directly from NSE/Yahoo data (no Perplexity dependency).
- `refresh_nse_sectors.py` - builds `nse-sectors.json` from the NSE 500 constituent list.
- `refresh_portfolio_prices.py` - builds `portfolio-prices.json` for your tracked symbols from Yahoo Finance.
- `.github/workflows/refresh-portfolio.yml` - runs the two scripts above automatically, roughly hourly during NSE market hours, and commits the results.
- `SCREENER_REFERENCE.md` - canonical NSE momentum screener rules for the project.

## Portfolio Tab

The **Portfolio** view has three sub-tabs:

- **Watchlist** - names you're tracking, with added price/date and live change %.
- **Paper Trading** - a virtual cash account (default ₹10,00,000) for simulated buys/sells, with open positions, closed trades, and realized/unrealized P&L.
- **Holdings** - your real positions, valuation, P&L and sector allocation.

**Current price** resolves in this order: (1) a live match in today's `momentum-picks.json`, marked <code>live</code>; (2) `portfolio-prices.json`, auto-refreshed roughly hourly during market hours, marked <code>auto</code>; (3) whatever you last typed in via **✎ Update**, marked with an "upd DD Mon" note. **Sector** resolves from a stored value, then a live picks match, then the full NSE 500 lookup in `nse-sectors.json`.

The automated refresh (see `.github/workflows/refresh-portfolio.yml`) needs no API key or external account - it runs on GitHub's own infrastructure and commits `nse-sectors.json`/`portfolio-prices.json` back to the repo on a schedule. One caveat: NSE's archives site occasionally blocks requests from cloud/datacenter IPs (including CI runners), so the sector-refresh step is allowed to fail without blocking the price refresh - if you notice sectors going stale, check the Actions tab for that step's logs.

Editing anything in the Portfolio tab (adding/removing/updating watchlist items, trades, or holdings) only changes your browser session. Click **⬇ Export portfolio-data.json** and commit the downloaded file to persist those specific edits and see them on other devices - separate from the automated price/sector refresh, which commits on its own.

## Quick Local Workflow

1. Replace `momentum-picks.json` with the latest Perplexity output.
2. Run:
   ```bash
   python update_dashboard.py
   ```
3. Open `index.html` in a browser.

## GitHub Pages Deployment

1. Create a new GitHub repo.
2. Upload these files to the repo root.
3. Keep `index.html` in the repo root so the site opens directly on the repo URL.
4. In GitHub:
   - Go to **Settings > Pages**.
   - Under **Build and deployment**, choose **Deploy from a branch**.
   - Select `main` branch and `/root`.
5. Your dashboard will publish at:
   `https://YOUR-USERNAME.github.io/YOUR-REPO/`

## Daily Update Workflow

1. Open the Perplexity daily screening task and use `SCREENER_REFERENCE.md` as the project reference.
2. Copy the JSON response into `momentum-picks.json`, or run:
   ```bash
   python update_dashboard.py
   ```
3. Commit and push the updated files:
   ```bash
   git add .
   git commit -m "Update daily momentum picks"
   git push
   ```
4. GitHub Pages will refresh the site.

## Optional Improvement

You can later modify the dashboard to fetch `momentum-picks.json` directly, which removes the injection step and makes the workflow even faster.
