"""J-League match results collector using Selenium.

Fetches individual match results (with scores) from data.j-league.or.jp
by submitting the search form via JavaScript. This data is essential for
training the Dixon-Coles model.

Requires: seleniumbase (with headless Chrome)
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from seleniumbase import SB

from toto.config import CACHE_DIR
from toto.utils import cache as cache_util

logger = logging.getLogger(__name__)

JLEAGUE_URL = "https://data.j-league.or.jp/SFMS01/"

# competition_frame_ids on data.j-league.or.jp
LEAGUE_FRAME_IDS: dict[str, str] = {
    "J1": "1",
    "J2": "2",
    "J3": "3",
}


def fetch_match_results(
    year: int = 2025,
    leagues: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch match results from data.j-league.or.jp using Selenium.

    Args:
        year: Season year.
        leagues: List of league codes (e.g., ["J1", "J2", "J3"]).
                 Defaults to all three.
        start_date: Start date in YYYY-MM-DD format. Defaults to Jan 1.
        end_date: End date in YYYY-MM-DD format. Defaults to Dec 31.

    Returns:
        Dict mapping league code to list of match result dicts.
        Each match dict has: date, home, away, goals_home, goals_away, score.
    """
    if leagues is None:
        leagues = ["J1", "J2", "J3"]

    if start_date is None:
        start_date = f"{year}-01-01"
    if end_date is None:
        end_date = f"{year}-12-31"

    cache_key = f"match_results_{year}_{'_'.join(leagues)}"
    cached = cache_util.get(cache_key)
    if cached is not None:
        logger.info("Match results cache hit for %s %s", year, leagues)
        return cached

    results: dict[str, list[dict[str, Any]]] = {}

    with SB(uc=True, headless=True) as sb:
        for league in leagues:
            frame_id = LEAGUE_FRAME_IDS.get(league)
            if frame_id is None:
                logger.warning("Unknown league: %s", league)
                continue

            matches = _fetch_league_matches(sb, year, frame_id, start_date, end_date)
            results[league] = matches
            logger.info("[selenium] %s %d: %d matches fetched", league, year, len(matches))

    if results:
        cache_util.set(cache_key, results)
        total = sum(len(v) for v in results.values())
        logger.info("[selenium] Total: %d matches saved to cache", total)

    return results


def _fetch_league_matches(
    sb: Any,
    year: int,
    frame_id: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Fetch matches for a single league via form submission."""
    sb.open(JLEAGUE_URL)
    sb.sleep(3)

    sb.execute_script(f"""
        document.querySelector('select[name="competition_years"]').value = '{year}';
        document.querySelector('select[name="competition_frame_ids"]').value = '{frame_id}';
        var startInput = document.querySelector('input[name="startDate"]');
        var endInput = document.querySelector('input[name="endDate"]');
        if (startInput) {{ startInput.value = '{start_date}'; }}
        if (endInput) {{ endInput.value = '{end_date}'; }}
        document.querySelector('form').submit();
    """)
    sb.sleep(5)

    html = sb.get_page_source()
    return _parse_match_table(html)


def _parse_match_table(html: str) -> list[dict[str, Any]]:
    """Parse the match results table.

    Expected columns: [シーズン, 大会, 節, 試合日, K/O時刻, ホーム, スコア, アウェイ]
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        return []

    matches: list[dict[str, Any]] = []
    for row in table.find_all("tr")[1:]:  # Skip header
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        texts = [c.get_text(strip=True) for c in cells]
        score_match = re.match(r"(\d+)-(\d+)", texts[6])
        if not score_match:
            continue

        matches.append({
            "season": texts[0],
            "competition": texts[1],
            "matchday": texts[2],
            "date": texts[3],
            "kickoff": texts[4],
            "home": texts[5],
            "score": texts[6],
            "away": texts[7],
            "goals_home": int(score_match.group(1)),
            "goals_away": int(score_match.group(2)),
        })

    return matches


def get_team_recent_matches(
    all_matches: list[dict[str, Any]],
    team_name: str,
    n: int = 5,
) -> list[dict[str, Any]]:
    """Extract the most recent N matches for a given team.

    Args:
        all_matches: List of all match dicts (sorted by date).
        team_name: Team name to filter by.
        n: Number of recent matches to return.

    Returns:
        List of recent match dicts with perspective from the given team.
    """
    team_matches = [
        m for m in all_matches
        if m["home"] == team_name or m["away"] == team_name
    ]
    # Sort by date descending (most recent first)
    team_matches.sort(key=lambda m: m["date"], reverse=True)

    recent: list[dict[str, Any]] = []
    for m in team_matches[:n]:
        is_home = m["home"] == team_name
        recent.append({
            "date": m["date"],
            "opponent": m["away"] if is_home else m["home"],
            "home_or_away": "home" if is_home else "away",
            "goals_for": m["goals_home"] if is_home else m["goals_away"],
            "goals_against": m["goals_away"] if is_home else m["goals_home"],
            "result": (
                "1" if (is_home and m["goals_home"] > m["goals_away"])
                or (not is_home and m["goals_away"] > m["goals_home"])
                else "0" if m["goals_home"] == m["goals_away"]
                else "2"
            ),
        })

    return recent


def get_h2h_matches(
    all_matches: list[dict[str, Any]],
    team_a: str,
    team_b: str,
    n: int = 5,
) -> list[dict[str, Any]]:
    """Extract head-to-head matches between two teams.

    Args:
        all_matches: List of all match dicts.
        team_a: First team name (perspective team).
        team_b: Second team name.
        n: Maximum number of H2H matches to return.

    Returns:
        List of H2H match dicts from team_a's perspective.
    """
    h2h = [
        m for m in all_matches
        if (m["home"] == team_a and m["away"] == team_b)
        or (m["home"] == team_b and m["away"] == team_a)
    ]
    h2h.sort(key=lambda m: m["date"], reverse=True)
    return get_team_recent_matches(h2h, team_a, n=n) if h2h else []


def build_training_dataframe(
    results: dict[str, list[dict[str, Any]]],
) -> Any:
    """Convert match results to a pandas DataFrame suitable for Dixon-Coles.

    Args:
        results: Dict mapping league to match list.

    Returns:
        pandas DataFrame with columns: date, team_home, team_away,
        goals_home, goals_away.
    """
    import pandas as pd

    rows = []
    for league, matches in results.items():
        for m in matches:
            # Parse date: "25/02/15(土)" → "2025-02-15"
            date_match = re.match(r"(\d{2})/(\d{2})/(\d{2})", m["date"])
            if date_match:
                y = 2000 + int(date_match.group(1))
                month = date_match.group(2)
                day = date_match.group(3)
                iso_date = f"{y}-{month}-{day}"
            else:
                iso_date = m["date"]

            rows.append({
                "date": iso_date,
                "team_home": m["home"],
                "team_away": m["away"],
                "goals_home": m["goals_home"],
                "goals_away": m["goals_away"],
                "league": league,
            })

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df
