# Momentum Dashboard - GitHub Pages Starter

This folder gives you a fast testing setup for your NSE momentum dashboard.

## Files

- `index.html` - your dashboard UI.
- `momentum-picks.json` - daily data payload from Perplexity or your screener.
- `portfolio-data.json` - your watchlist, paper trades and real holdings (Portfolio tab).
- `update_dashboard.py` - refreshes the JSON picks through the Perplexity API and can push updates to GitHub.
- `refresh_momentum_picks.py` - standard-library alternative that screens directly from NSE/Yahoo data (no Perplexity dependency).
- `SCREENER_REFERENCE.md` - canonical NSE momentum screener rules for the project.

## Portfolio Tab

The **Portfolio** view has three sub-tabs:

- **Watchlist** - names you're tracking, with added price/date and live change %.
- **Paper Trading** - a virtual cash account (default ₹10,00,000) for simulated buys/sells, with open positions, closed trades, and realized/unrealized P&L.
- **Holdings** - your real positions, valuation, P&L and sector allocation.

Current price for any tracked symbol is pulled automatically from `momentum-picks.json` when that symbol appears in today's screen; otherwise you enter a price manually.

All edits happen in your browser only. Click **⬇ Export portfolio-data.json** on the Portfolio page, then commit the downloaded file to the repo the same way you commit `momentum-picks.json`, to persist changes and see them on other devices.

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
