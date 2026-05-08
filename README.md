# Momentum Dashboard – GitHub Pages Starter

This folder gives you a fast testing setup for your NSE momentum dashboard.

## Files
- `momentum-dashboard.html` — your dashboard UI
- `momentum-picks.json` — daily data payload from Perplexity or your screener
- `update_dashboard.py` — injects JSON picks into the HTML for static deployment

## Quick local workflow
1. Replace `momentum-picks.json` with the latest Perplexity output.
2. Run:
   ```bash
   python update_dashboard.py
   ```
3. Open `momentum-dashboard.html` in a browser.

## GitHub Pages deployment
1. Create a new GitHub repo.
2. Upload these files to the repo root.
3. Rename `momentum-dashboard.html` to `index.html` if you want the site to open directly on the repo URL.
4. In GitHub:
   - Go to **Settings > Pages**
   - Under **Build and deployment**, choose **Deploy from a branch**
   - Select `main` branch and `/root`
5. Your dashboard will publish at:
   `https://YOUR-USERNAME.github.io/YOUR-REPO/`

## Daily update workflow
1. Open the Perplexity daily screening task.
2. Copy the JSON response into `momentum-picks.json`.
3. Run:
   ```bash
   python update_dashboard.py
   ```
4. Commit and push the updated files:
   ```bash
   git add .
   git commit -m "Update daily momentum picks"
   git push
   ```
5. GitHub Pages will refresh the site.

## Optional improvement
You can later modify the dashboard to fetch `momentum-picks.json` directly, which removes the injection step and makes the workflow even faster.
