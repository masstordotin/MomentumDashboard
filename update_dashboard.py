"""
update_dashboard.py
Daily NSE Momentum Screen → Perplexity API → GitHub Push → Live Dashboard

Usage:
    python update_dashboard.py

Requirements:
    pip install requests PyGithub schedule pytz
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
import schedule
import pytz
from github import Github, InputGitAuthor  # PyGithub package

# ============================================================
# CONFIGURATION — Change these values
# ============================================================

# Your Perplexity API key (get from https://www.perplexity.ai/api)
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "YOUR_PPLX_API_KEY_HERE")

# Your GitHub token (with repo scope) — create at https://github.com/settings/tokens
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "YOUR_GITHUB_TOKEN_HERE")

# Your GitHub username and repo name (the one serving the dashboard)
GITHUB_USER = "masstordotin"
GITHUB_REPO = "MomentumDashboard"

# The branch that GitHub Pages serves from — must match the repo's actual
# branch name exactly (git refs are case-sensitive). This repo's branch is "Main".
GITHUB_BRANCH = "Main"

# The path inside your repo where momentum-picks.json lives
# If index.html is in a subfolder like 'docs/', put it there too
JSON_PATH_IN_REPO = "momentum-picks.json"

# How many picks to request (10–20 recommended)
NUM_PICKS = 15

# ============================================================
# Logging Setup
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ============================================================
# 1. Perplexity API Screening
# ============================================================

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"

# The JSON schema that matches your dashboard's expected fields
SONAR_JSON_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "rank": {"type": "number"},
            "name": {"type": "string"},
            "symbol": {"type": "string"},
            "sector": {"type": "string"},
            "bucket": {
                "type": "string",
                "enum": ["Large", "Mid", "Small"]
            },
            "score": {"type": "number"},
            "price": {"type": "number"},
            "rsi": {"type": "number"},
            "ema50": {"type": "number"},
            "ema200": {"type": "number"},
            "volRatio": {"type": "number"},
            "ret1m": {"type": "number"},
            "ret3m": {"type": "number"},
            "ret6m": {"type": "number"},
            "setup": {
                "type": "string",
                "enum": [
                    "Pullback continuation",
                    "Breakout continuation",
                    "Momentum expansion"
                ]
            },
            "buyZone": {"type": "string"},
            "invalid": {"type": "string"},
            "thesis": {"type": "string"},
            "indicator": {"type": "string"},
            "asOf": {"type": "string"}
        },
        "required": [
            "rank", "name", "symbol", "sector", "bucket", "score",
            "price", "rsi", "ema50", "ema200", "volRatio",
            "ret1m", "ret3m", "ret6m", "setup", "buyZone",
            "invalid", "thesis", "indicator", "asOf"
        ],
        "additionalProperties": False
    }
}

# The screening prompt — tells sonar-pro exactly what to search for.
# Keep this aligned with SCREENER_REFERENCE.md.
SCREENING_PROMPT = f"""You are an institutional momentum trader and quantitative equity screener specializing in Indian equities listed on the NSE.

Your task is to identify NSE-listed stocks that satisfy this trend-following momentum strategy using the latest available market data.

Strategy objective:
Find fundamentally liquid stocks showing strong bullish trend structure, stable momentum, institutional participation, and continuation potential for 1 to 8 week swing trading opportunities.

Screen for currently LIVE NSE stocks that meet ALL of these criteria:

**TREND STRUCTURE:**
- Current price > 50 EMA > 200 EMA
- Stock is in a confirmed long-term uptrend

**MOMENTUM STABILITY:**
- RSI(14) between 55 and 75
- RSI should not be sharply declining in the last 3 to 4 sessions
- Prefer stocks where RSI is stable or rising

**VOLUME EXPANSION:**
- Current day volume > 1.5x the 30-day average volume
- This should indicate institutional participation and accumulation

**PRICE STRUCTURE CONFIRMATION:**
- Higher high + higher low structure in recent weeks
- Avoid choppy, sideways, or weak structures

**MOMENTUM RANKING PREFERENCE:**
- Prioritize strong 1-month returns
- Prioritize strong 3-month returns
- Prioritize strong 6-month relative strength
- Favor consistent momentum with controlled volatility

**AVOID:**
- Overextended parabolic moves
- Low liquidity stocks
- Weak relative strength
- Stocks below 50 EMA
- Falling momentum structures

**WHAT TO RETURN:**
Return exactly {NUM_PICKS} top candidates as a raw JSON array (no markdown, no explanation, no code blocks).
Each object must match this schema:

{json.dumps(SONAR_JSON_SCHEMA, indent=2)}

**FOCUS AREAS FOR TODAY:**
- Prioritize stocks in sectors currently showing the strongest relative strength
- Include a mix of large-cap, mid-cap, and small-cap names
- Avoid low-liquidity stocks, penny stocks, and overextended parabolic moves
- For each stock, provide a specific buy zone (price range for entry), invalidation level (stop-loss zone), and a concise 1-sentence thesis
- Set the indicator field to a brief technical note with RSI trend, EMA stack, volume ratio, and sector or market confirmation
- Rank the stocks from strongest to weakest momentum continuation setup
- Classify each setup as Pullback continuation, Breakout continuation, or Momentum expansion
- While the JSON schema does not include separate market-regime fields, use sector, thesis, and indicator to reflect whether the broader Indian market environment favors momentum continuation, whether breakouts are succeeding or failing, and whether market breadth supports aggressive long setups
- Set the "asOf" field to the exact trading-day date that the price, RSI, EMA, and volume figures actually reflect (the date of the underlying candle/quote you found, NOT necessarily today), formatted exactly as "YYYY-MM-DDT00:00:00+05:30" (IST midnight for that date). If your source data is from the prior trading session (e.g. markets haven't closed yet, or your search tool returned slightly stale quotes), asOf must reflect that earlier date, not today's date — do not default to today just because that's when this request was made.

**Date Context:** {datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%A, %B %d, %Y")} IST market close. This is when the request is being made — it is NOT automatically the asOf date; only use it as asOf if your actual price data is confirmed current as of this session's close.
Search for the latest NSE price data, RSI, EMA, and volume information from financial websites."""


def call_perplexity_api(max_retries: int = 3) -> Optional[List[Dict[str, Any]]]:
    """
    Calls the Perplexity sonar-pro API with the screening prompt and JSON schema.
    Returns the parsed list of stock picks, or None on failure.
    """
    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "sonar-pro",
        "messages": [
            {
                "role": "system",
                "content": "You are a momentum trader. Return ONLY raw JSON matching the requested schema. No markdown, no explanation."
            },
            {
                "role": "user",
                "content": SCREENING_PROMPT
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "nse_momentum_picks",
                "schema": SONAR_JSON_SCHEMA
            }
        },
        "max_tokens": 4096,
        "temperature": 0.2,  # Low temperature for consistent structured output
        "stream": False
    }
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Calling Perplexity API (attempt {attempt + 1}/{max_retries})...")
            response = requests.post(
                PERPLEXITY_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            if response.status_code != 200:
                logger.error(f"API error: {response.status_code} - {response.text}")
                time.sleep(5)
                continue
            
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            
            # Try to parse as JSON directly
            picks = parse_json_response(content)
            if picks:
                logger.info(f"Successfully retrieved {len(picks)} stock picks")
                return picks
            
            logger.warning("Failed to parse JSON, retrying...")
            time.sleep(5)
            
        except requests.RequestException as e:
            logger.error(f"Request error on attempt {attempt + 1}: {e}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error on attempt {attempt + 1}: {e}")
            time.sleep(5)
    
    logger.error("All API attempts failed. Returning None.")
    return None


def parse_json_response(content: str) -> Optional[List[Dict[str, Any]]]:
    """
    Extracts and parses JSON from the API response.
    Handles cases where the model wraps JSON in markdown code blocks.
    """
    try:
        # First try direct parse
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    
    try:
        # Remove markdown code blocks if present
        match = re.search(r'\[\s*\{[\s\S]*\}\s*\]', content)
        if match:
            return json.loads(match.group())
    except json.JSONDecodeError:
        pass
    
    try:
        # Try to find any JSON array starting with [ and ending with ]
        # This handles cases where there's extra text before/after
        start = content.find('[')
        end = content.rfind(']')
        if start != -1 and end != -1 and end > start:
            json_str = content[start:end + 1]
            return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    logger.error("Could not extract JSON array from response")
    logger.debug(f"Response content: {content[:500]}...")
    return None


# ============================================================
# 2. Local File Write (backup + for local testing)
# ============================================================

def write_local_json(picks: List[Dict[str, Any]], filename: str = "momentum-picks.json") -> None:
    """Writes picks to a local JSON file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(picks, f, indent=2, ensure_ascii=False)
    logger.info(f"Wrote {len(picks)} picks to {filename}")


# ============================================================
# 3. GitHub Push
# ============================================================

def push_to_github(picks: List[Dict[str, Any]]) -> bool:
    """
    Pushes the momentum-picks.json to the GitHub repo.
    Uses the GitHub API via PyGithub.
    Returns True on success, False on failure.
    """
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(f"{GITHUB_USER}/{GITHUB_REPO}")

        content = json.dumps(picks, indent=2, ensure_ascii=False)
        commit_message = f"Update momentum picks {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC"

        try:
            repo_file = repo.get_contents(JSON_PATH_IN_REPO, ref=GITHUB_BRANCH)
            if isinstance(repo_file, list):
                repo_file = repo_file[0]
            repo.update_file(
                path=repo_file.path,
                message=commit_message,
                content=content,
                sha=repo_file.sha,
                branch=GITHUB_BRANCH
            )
            logger.info("Updated %s on GitHub branch %s", JSON_PATH_IN_REPO, GITHUB_BRANCH)
        except Exception as e:
            if getattr(e, "status", None) == 404 or "404" in str(e):
                repo.create_file(
                    path=JSON_PATH_IN_REPO,
                    message=commit_message,
                    content=content,
                    branch=GITHUB_BRANCH
                )
                logger.info("Created %s on GitHub branch %s", JSON_PATH_IN_REPO, GITHUB_BRANCH)
            else:
                raise

        return True
    except Exception as e:
        logger.error("Failed to push picks to GitHub: %s", e)
        return False


# ============================================================
# 4. Orchestration / Entry Point
# ============================================================

def run_once() -> bool:
    """Runs a single screen -> write -> push cycle. Returns True on success."""
    picks = call_perplexity_api()
    if not picks:
        logger.error("No picks returned from Perplexity — skipping this cycle.")
        return False

    write_local_json(picks, filename=JSON_PATH_IN_REPO)

    if GITHUB_TOKEN == "YOUR_GITHUB_TOKEN_HERE":
        logger.warning("GITHUB_TOKEN not set — wrote local JSON only, skipped GitHub push.")
        return True

    return push_to_github(picks)


def run_daemon(run_at: str = "08:00", timezone_name: str = "Asia/Kolkata") -> None:
    """Runs run_once() every day at run_at (local time in timezone_name), forever."""
    tz = pytz.timezone(timezone_name)

    def job():
        logger.info("Scheduled run starting at %s", datetime.now(tz).isoformat())
        run_once()

    schedule.every().day.at(run_at).do(job)
    logger.info("Daemon started — will run daily at %s %s. Ctrl+C to stop.", run_at, timezone_name)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    import sys
    if "--daemon" in sys.argv:
        run_daemon()
    else:
        success = run_once()
        logger.info("Run finished: %s", "success" if success else "failed")
        sys.exit(0 if success else 1)
