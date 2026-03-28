"""Toto.cam (totomo) analysis data collector."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import TOTOMO_URL

logger = logging.getLogger(__name__)


class TotomoCollector(BaseCollector):
    """Scrapes toto.cam for community voting distributions and upset analysis.

    Data sources:
        - /totodata/toto_voterate.php : Community voting rates per match.
        - /totodata/analyzed/toto_analyzed1-01.php : Upset / analysis data.
    """

    VOTERATE_PATH = "/totodata/toto_voterate.php"
    ANALYZED_PATH = "/totodata/analyzed/toto_analyzed1-01.php"

    def __init__(self) -> None:
        super().__init__(name="totomo")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect voting rate and analysis data from toto.cam.

        Args:
            toto_round: The toto round number.

        Returns:
            Dictionary with 'vote_rates' and 'analysis' data.
        """
        result: dict[str, Any] = {
            "source": "toto.cam",
            "toto_round": toto_round,
            "vote_rates": [],
            "analysis": [],
        }

        vote_rates, analysis = await self._fetch_voterate(toto_round), await self._fetch_analysis(toto_round)
        result["vote_rates"] = vote_rates
        result["analysis"] = analysis

        return result

    async def _fetch_voterate(self, toto_round: int) -> list[dict[str, Any]]:
        """Fetch community voting rate data.

        Args:
            toto_round: The toto round number.

        Returns:
            List of per-match voting rate records.
        """
        url = f"{TOTOMO_URL}{self.VOTERATE_PATH}"
        params = {"kai": str(toto_round)}
        cache_key = f"totomo_voterate_{toto_round}"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            return self._parse_voterate(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch vote rates for round %d.",
                self.name,
                toto_round,
                exc_info=True,
            )
            return []

    async def _fetch_analysis(self, toto_round: int) -> list[dict[str, Any]]:
        """Fetch upset / pattern analysis data.

        Args:
            toto_round: The toto round number.

        Returns:
            List of analysis records per match.
        """
        url = f"{TOTOMO_URL}{self.ANALYZED_PATH}"
        params = {"kai": str(toto_round)}
        cache_key = f"totomo_analysis_{toto_round}"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            return self._parse_analysis(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch analysis for round %d.",
                self.name,
                toto_round,
                exc_info=True,
            )
            return []

    def _parse_voterate(self, html: str) -> list[dict[str, Any]]:
        """Parse community voting rate HTML.

        Args:
            html: Raw HTML from the vote rate page.

        Returns:
            List of dicts with match info and voting percentages.
        """
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []

        # toto.cam tables typically have class or id markers.
        table = soup.select_one("table.vote_table, table.data-table, table")
        if table is None:
            logger.warning("[%s] No vote rate table found.", self.name)
            return records

        rows = table.find_all("tr")
        match_number = 0

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            record = self._extract_voterate_row(cells)
            if record:
                match_number += 1
                record["match_number"] = match_number
                records.append(record)

        logger.info(
            "[%s] Parsed vote rates for %d matches.", self.name, len(records)
        )
        return records

    def _extract_voterate_row(
        self, cells: list[Any]
    ) -> dict[str, Any] | None:
        """Extract voting data from a table row.

        Args:
            cells: List of <td> elements.

        Returns:
            Dict with team names and vote rates, or None.
        """
        try:
            texts = [c.get_text(strip=True) for c in cells]

            # Find cells containing percentage values.
            pct_values: list[float] = []
            team_names: list[str] = []
            for text in texts:
                pct_match = re.search(r"(\d{1,3}(?:\.\d{1,2})?)%?", text)
                if pct_match and float(pct_match.group(1)) <= 100.0:
                    pct_values.append(float(pct_match.group(1)))
                elif not re.match(r"^\d+$", text) and len(text) > 1:
                    team_names.append(text)

            if len(pct_values) < 3 or len(team_names) < 2:
                return None

            return {
                "home_team": team_names[0],
                "away_team": team_names[1],
                "home_vote_pct": pct_values[0],
                "draw_vote_pct": pct_values[1],
                "away_vote_pct": pct_values[2],
            }
        except (IndexError, ValueError):
            return None

    def _parse_analysis(self, html: str) -> list[dict[str, Any]]:
        """Parse upset/analysis HTML page.

        Args:
            html: Raw HTML from the analysis page.

        Returns:
            List of analysis records with upset indicators.
        """
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []

        # Look for analysis sections (often divs or table rows).
        sections = soup.select(
            "table tr, div.match-analysis, div.analyzed-item"
        )
        match_number = 0

        for section in sections:
            record = self._extract_analysis_record(section)
            if record:
                match_number += 1
                record["match_number"] = match_number
                records.append(record)

        logger.info(
            "[%s] Parsed analysis for %d matches.", self.name, len(records)
        )
        return records

    def _extract_analysis_record(
        self, element: Any,
    ) -> dict[str, Any] | None:
        """Extract analysis data from an element.

        Args:
            element: A BeautifulSoup element.

        Returns:
            Dict with analysis data or None.
        """
        text = element.get_text(separator=" ", strip=True)
        if not text or len(text) < 5:
            return None

        # Look for team name pairs and prediction indicators.
        cells = element.find_all("td")
        if len(cells) < 3:
            return None

        texts = [c.get_text(strip=True) for c in cells]
        team_names = [
            t for t in texts
            if t and not re.match(r"^[\d.%\-]+$", t) and len(t) > 1
        ]
        if len(team_names) < 2:
            return None

        # Detect prediction markers (common: "1", "0", "2" or icons).
        prediction = None
        for t in texts:
            if t in ("1", "0", "2"):
                prediction = t
                break

        # Check for upset flags (keywords in Japanese).
        upset_keywords = ["波乱", "番狂", "注意", "穴"]
        has_upset_flag = any(kw in text for kw in upset_keywords)

        return {
            "home_team": team_names[0],
            "away_team": team_names[1],
            "prediction": prediction,
            "has_upset_flag": has_upset_flag,
            "raw_text": text[:200],
        }
