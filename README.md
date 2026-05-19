# Momentum Dashboard - GitHub Pages Starter

This folder gives you a fast testing setup for your NSE momentum dashboard.

## Files

- `index.html` - your dashboard UI.
- `momentum-picks.json` - daily data payload from Perplexity or your screener.
- `update_dashboard.py` - refreshes the JSON picks through the Perplexity API and can push updates to GitHub.
- `SCREENER_REFERENCE.md` - canonical NSE momentum screener rules for the project.

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
