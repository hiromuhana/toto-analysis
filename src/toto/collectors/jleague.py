"""J-League data collector from data.j-league.or.jp."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import JLEAGUE_DATA_URL

logger = logging.getLogger(__name__)


class JLeagueCollector(BaseCollector):
    """Scrapes data.j-league.or.jp for match results, standings, and team stats.

    Data sources:
        - /SFMS01/ : Match fixtures and results.
        - /SFRT01/ : League standings table.
    """

    FIXTURES_PATH = "/SFMS01/SFMS0101.html"
    STANDINGS_PATH = "/SFRT01/SFRT0101.html"

    def __init__(self) -> None:
        super().__init__(name="jleague")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect J-League match data and standings.

        Args:
            toto_round: The toto round number.

        Returns:
            Dictionary containing 'fixtures', 'standings', and metadata.
            Returns empty data with a warning if scraping fails.
        """
        result: dict[str, Any] = {
            "source": "data.j-league.or.jp",
            "toto_round": toto_round,
            "fixtures": [],
            "standings": [],
        }

        result["fixtures"] = await self._fetch_fixtures()
        result["standings"] = await self._fetch_standings()

        return result

    async def _fetch_fixtures(self) -> list[dict[str, Any]]:
        """Fetch and parse match fixture data.

        Returns:
            List of fixture dictionaries with match details.
        """
        url = f"{JLEAGUE_DATA_URL}{self.FIXTURES_PATH}"
        try:
            html = await self.fetch(url, cache_key="jleague_fixtures")
            return self._parse_fixtures(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch fixtures from %s. Returning empty list.",
                self.name,
                url,
                exc_info=True,
            )
            return []

    async def _fetch_standings(self) -> list[dict[str, Any]]:
        """Fetch and parse league standings.

        Returns:
            List of team standing dictionaries.
        """
        url = f"{JLEAGUE_DATA_URL}{self.STANDINGS_PATH}"
        try:
            html = await self.fetch(url, cache_key="jleague_standings")
            return self._parse_standings(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch standings from %s. Returning empty list.",
                self.name,
                url,
                exc_info=True,
            )
            return []

    def _parse_fixtures(self, html: str) -> list[dict[str, Any]]:
        """Parse fixtures HTML table into structured data.

        Args:
            html: Raw HTML content from the fixtures page.

        Returns:
            List of fixture records.
        """
        soup = BeautifulSoup(html, "lxml")
        fixtures: list[dict[str, Any]] = []

        # J-League data site uses table-based layouts for fixture lists.
        # Look for common table structures containing match data.
        table = soup.select_one("table.search-table, table.game-table, table#search_result")
        if table is None:
            # Fallback: try the first substantial table on the page.
            tables = soup.find_all("table")
            for t in tables:
                rows = t.find_all("tr")
                if len(rows) > 3:
                    table = t
                    break

        if table is None:
            logger.warning("[%s] No fixture table found in HTML.", self.name)
            return fixtures

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            fixture = self._extract_fixture_from_row(cells)
            if fixture:
                fixtures.append(fixture)

        logger.info("[%s] Parsed %d fixtures.", self.name, len(fixtures))
        return fixtures

    def _extract_fixture_from_row(
        self, cells: list[Any]
    ) -> dict[str, Any] | None:
        """Extract a single fixture record from a table row.

        Args:
            cells: List of <td> elements from a table row.

        Returns:
            Fixture dict or None if the row cannot be parsed.
        """
        try:
            texts = [c.get_text(strip=True) for c in cells]

            # Typical J-League data table columns:
            # [date, kickoff, home, score, away, stadium, ...]
            # We attempt flexible extraction.
            date_text = texts[0] if len(texts) > 0 else ""
            home_team = texts[2] if len(texts) > 2 else ""
            score_text = texts[3] if len(texts) > 3 else ""
            away_team = texts[4] if len(texts) > 4 else ""
            stadium = texts[5] if len(texts) > 5 else ""

            if not home_team or not away_team:
                return None

            # Parse score (e.g., "2-1" or "vs")
            goals_home: int | None = None
            goals_away: int | None = None
            score_match = re.match(r"(\d+)\s*[-\u2013]\s*(\d+)", score_text)
            if score_match:
                goals_home = int(score_match.group(1))
                goals_away = int(score_match.group(2))

            return {
                "date": date_text,
                "home_team": home_team,
                "away_team": away_team,
                "goals_home": goals_home,
                "goals_away": goals_away,
                "stadium": stadium,
                "score_raw": score_text,
            }
        except (IndexError, ValueError):
            return None

    def _parse_standings(self, html: str) -> list[dict[str, Any]]:
        """Parse standings HTML table into structured data.

        Args:
            html: Raw HTML content from the standings page.

        Returns:
            List of team standing records sorted by rank.
        """
        soup = BeautifulSoup(html, "lxml")
        standings: list[dict[str, Any]] = []

        table = soup.select_one(
            "table.search-table, table.standing-table, table#ranking_table"
        )
        if table is None:
            tables = soup.find_all("table")
            for t in tables:
                rows = t.find_all("tr")
                if len(rows) > 5:
                    table = t
                    break

        if table is None:
            logger.warning("[%s] No standings table found in HTML.", self.name)
            return standings

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 7:
                continue

            record = self._extract_standing_from_row(cells)
            if record:
                standings.append(record)

        logger.info("[%s] Parsed %d standings entries.", self.name, len(standings))
        return standings

    def _extract_standing_from_row(
        self, cells: list[Any]
    ) -> dict[str, Any] | None:
        """Extract a single standings record from a table row.

        Args:
            cells: List of <td> elements from a table row.

        Returns:
            Standing dict or None if the row cannot be parsed.
        """
        try:
            texts = [c.get_text(strip=True) for c in cells]
            # Typical: [rank, team, played, W, D, L, GF, GA, GD, points]
            rank = self._safe_int(texts[0])
            team = texts[1]
            played = self._safe_int(texts[2])
            wins = self._safe_int(texts[3])
            draws = self._safe_int(texts[4])
            losses = self._safe_int(texts[5])
            goals_for = self._safe_int(texts[6])
            goals_against = self._safe_int(texts[7])
            points = self._safe_int(texts[9]) if len(texts) > 9 else self._safe_int(texts[8])

            if not team:
                return None

            return {
                "rank": rank,
                "team": team,
                "played": played,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "points": points,
            }
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: str) -> int:
        """Convert a string to int, returning 0 for non-numeric values.

        Args:
            value: String to convert.

        Returns:
            Parsed integer or 0.
        """
        try:
            return int(value.replace(",", ""))
        except (ValueError, AttributeError):
            return 0
