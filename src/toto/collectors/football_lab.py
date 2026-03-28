"""Football LAB (football-lab.jp) team statistics collector."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import FOOTBALL_LAB_URL, J1_TEAMS

logger = logging.getLogger(__name__)

# Mapping from Japanese team names to football-lab.jp URL slugs.
# Football LAB uses English slug-based URLs like /teams/2025/T001/.
# This mapping covers J1 2025 teams.
_TEAM_SLUG_MAP: dict[str, str] = {
    "北海道コンサドーレ札幌": "T001",
    "鹿島アントラーズ": "T002",
    "浦和レッズ": "T004",
    "大宮アルディージャ": "T006",
    "FC東京": "T007",
    "東京ヴェルディ": "T008",
    "町田ゼルビア": "T032",
    "川崎フロンターレ": "T009",
    "横浜F・マリノス": "T010",
    "横浜FC": "T011",
    "湘南ベルマーレ": "T012",
    "アルビレックス新潟": "T013",
    "清水エスパルス": "T015",
    "ジュビロ磐田": "T016",
    "名古屋グランパス": "T017",
    "京都サンガF.C.": "T018",
    "ガンバ大阪": "T019",
    "セレッソ大阪": "T020",
    "ヴィッセル神戸": "T021",
    "サンフレッチェ広島": "T022",
    "アビスパ福岡": "T027",
}


class FootballLabCollector(BaseCollector):
    """Scrapes football-lab.jp for detailed J-League team statistics.

    Collects attack/defense ratings, expected goals (xG), and other
    advanced metrics from Football LAB team pages.

    Reference: dovicie/jleague-result-prediction used Football LAB data
    for feature engineering.
    """

    # URL pattern: /teams/{season}/{team_slug}/
    TEAM_PATH_TEMPLATE = "/teams/{season}/{slug}/"

    def __init__(self) -> None:
        super().__init__(name="football_lab")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect team statistics from Football LAB for all J1 teams.

        Args:
            toto_round: The toto round number (used for cache keying).
            **kwargs: Optional 'season' (int, defaults to 2026),
                      'teams' (list of team names to collect).

        Returns:
            Dictionary with per-team stats keyed by team name.
        """
        season: int = kwargs.get("season", 2026)
        target_teams: list[str] = kwargs.get("teams", J1_TEAMS)

        result: dict[str, Any] = {
            "source": "football-lab.jp",
            "toto_round": toto_round,
            "season": season,
            "team_stats": {},
        }

        for team_name in target_teams:
            stats = await self._fetch_team_stats(team_name, season)
            if stats:
                result["team_stats"][team_name] = stats

        logger.info(
            "[%s] Collected stats for %d / %d teams.",
            self.name,
            len(result["team_stats"]),
            len(target_teams),
        )
        return result

    async def _fetch_team_stats(
        self, team_name: str, season: int
    ) -> dict[str, Any] | None:
        """Fetch and parse stats for a single team.

        Args:
            team_name: Japanese team name (must be in _TEAM_SLUG_MAP).
            season: Season year.

        Returns:
            Dict of team statistics, or None if fetch/parse fails.
        """
        slug = _TEAM_SLUG_MAP.get(team_name)
        if slug is None:
            logger.warning(
                "[%s] No URL slug mapping for team '%s'. Skipping.",
                self.name,
                team_name,
            )
            return None

        path = self.TEAM_PATH_TEMPLATE.format(season=season, slug=slug)
        url = f"{FOOTBALL_LAB_URL}{path}"
        cache_key = f"flab_{slug}_{season}"

        try:
            html = await self.fetch(url, cache_key=cache_key)
            return self._parse_team_page(html, team_name)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch stats for %s (%s).",
                self.name,
                team_name,
                url,
                exc_info=True,
            )
            return None

    def _parse_team_page(
        self, html: str, team_name: str
    ) -> dict[str, Any]:
        """Parse a Football LAB team page for key statistics.

        Args:
            html: Raw HTML of the team page.
            team_name: The team's Japanese name (for logging).

        Returns:
            Dict containing attack_rating, defense_rating, xg, xga,
            and other available metrics.
        """
        soup = BeautifulSoup(html, "lxml")
        stats: dict[str, Any] = {"team": team_name}

        # Extract attack / defense ratings from rating sections.
        stats.update(self._extract_ratings(soup))

        # Extract season summary stats (wins, draws, losses, goals).
        stats.update(self._extract_season_summary(soup))

        # Extract expected goals if available.
        stats.update(self._extract_xg(soup))

        return stats

    def _extract_ratings(self, soup: BeautifulSoup) -> dict[str, float]:
        """Extract attack and defense rating values.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Dict with 'attack_rating' and 'defense_rating' keys.
        """
        ratings: dict[str, float] = {
            "attack_rating": 1.0,
            "defense_rating": 1.0,
        }

        # Football LAB shows ratings in various formats.
        # Common patterns: "攻撃力 XX.X" or dedicated divs/spans.
        for label, key in [("攻撃", "attack_rating"), ("守備", "defense_rating")]:
            elements = soup.find_all(
                string=re.compile(label)
            )
            for el in elements:
                parent = el.parent if el.parent else el
                siblings_text = parent.get_text(separator=" ", strip=True)
                value = self._extract_first_float(siblings_text)
                if value is not None and 0.0 < value < 200.0:
                    ratings[key] = value
                    break

        return ratings

    def _extract_season_summary(
        self, soup: BeautifulSoup
    ) -> dict[str, Any]:
        """Extract season W/D/L and goals summary.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Dict with wins, draws, losses, goals_for, goals_against.
        """
        summary: dict[str, Any] = {}

        # Look for a summary table or stat block.
        stat_patterns = {
            "wins": re.compile(r"(\d+)\s*勝"),
            "draws": re.compile(r"(\d+)\s*分"),
            "losses": re.compile(r"(\d+)\s*敗"),
            "goals_for": re.compile(r"得点\s*(\d+)|(\d+)\s*得点"),
            "goals_against": re.compile(r"失点\s*(\d+)|(\d+)\s*失点"),
        }

        full_text = soup.get_text(separator=" ")
        for key, pattern in stat_patterns.items():
            match = pattern.search(full_text)
            if match:
                value = match.group(1) or match.group(2) if match.lastindex and match.lastindex > 1 else match.group(1)
                if value:
                    summary[key] = int(value)

        return summary

    def _extract_xg(self, soup: BeautifulSoup) -> dict[str, float | None]:
        """Extract expected goals (xG / xGA) if available.

        Args:
            soup: Parsed BeautifulSoup document.

        Returns:
            Dict with 'xg' and 'xga' (None if not found).
        """
        xg_data: dict[str, float | None] = {"xg": None, "xga": None}

        for label, key in [("期待得点", "xg"), ("期待失点", "xga"), ("xG", "xg"), ("xGA", "xga")]:
            elements = soup.find_all(string=re.compile(re.escape(label)))
            for el in elements:
                parent = el.parent if el.parent else el
                text = parent.get_text(separator=" ", strip=True)
                value = self._extract_first_float(text)
                if value is not None and 0.0 <= value < 200.0:
                    xg_data[key] = value
                    break

        return xg_data

    @staticmethod
    def _extract_first_float(text: str) -> float | None:
        """Extract the first floating-point number from text.

        Args:
            text: Input string.

        Returns:
            First float found, or None.
        """
        match = re.search(r"(\d+\.\d+)", text)
        if match:
            return float(match.group(1))
        # Try integer fallback.
        match = re.search(r"(\d+)", text)
        if match:
            return float(match.group(1))
        return None
