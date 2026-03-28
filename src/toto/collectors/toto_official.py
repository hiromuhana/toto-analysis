"""Toto official (toto-dream.com) voting data collector."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import TOTO_OFFICIAL_URL

logger = logging.getLogger(__name__)


class TotoOfficialCollector(BaseCollector):
    """Scrapes sp.toto-dream.com for toto voting percentages and match info.

    The voting page shows how the public has voted on each of the 13 toto
    matches, broken down by home-win / draw / away-win percentages.
    """

    VOTE_PATH = (
        "/dcs/subos/screen/si01/ssin025/"
        "PGSSIN02501ForwardVotetotoSP.form"
    )

    def __init__(self) -> None:
        super().__init__(name="toto_official")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect toto voting percentage data for a round.

        Args:
            toto_round: The toto round (holdCntId).

        Returns:
            Dictionary containing voting data per match, with keys:
                - 'toto_round': The round number.
                - 'source': Data source identifier.
                - 'matches': List of per-match voting dicts.
        """
        result: dict[str, Any] = {
            "source": "toto-dream.com",
            "toto_round": toto_round,
            "matches": [],
        }

        commodity_id = kwargs.get("commodity_id", "01")
        matches = await self._fetch_voting(toto_round, commodity_id)
        result["matches"] = matches
        return result

    async def _fetch_voting(
        self, toto_round: int, commodity_id: str = "01"
    ) -> list[dict[str, Any]]:
        """Fetch and parse the voting percentage page.

        Args:
            toto_round: The toto round number.
            commodity_id: Product type ('01' = toto, '03' = minitoto-A, etc.).

        Returns:
            List of match voting dicts.
        """
        url = f"{TOTO_OFFICIAL_URL}{self.VOTE_PATH}"
        params = {
            "holdCntId": str(toto_round),
            "commodityId": commodity_id,
        }
        cache_key = f"toto_vote_{toto_round}_{commodity_id}"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            return self._parse_voting_table(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch voting data for round %d. Returning empty list.",
                self.name,
                toto_round,
                exc_info=True,
            )
            return []

    def _parse_voting_table(self, html: str) -> list[dict[str, Any]]:
        """Parse the voting HTML into per-match voting records.

        Args:
            html: Raw HTML from the voting page.

        Returns:
            List of dicts with match_number, teams, and vote percentages.
        """
        soup = BeautifulSoup(html, "lxml")
        matches: list[dict[str, Any]] = []

        # The voting page renders a table with rows per match.
        # Each row typically contains: match#, home team, vote bars, away team.
        vote_rows = soup.select("table tr, div.vote-row, li.match-item")
        if not vote_rows:
            logger.warning("[%s] No voting rows found in HTML.", self.name)
            return matches

        match_number = 0
        for row in vote_rows:
            record = self._extract_vote_record(row, match_number + 1)
            if record:
                match_number += 1
                record["match_number"] = match_number
                matches.append(record)

        logger.info(
            "[%s] Parsed voting data for %d matches.", self.name, len(matches)
        )
        return matches

    def _extract_vote_record(
        self, element: Any, fallback_number: int
    ) -> dict[str, Any] | None:
        """Extract voting percentages from a single row/element.

        Args:
            element: A BeautifulSoup element representing one match row.
            fallback_number: Default match number if not found in the element.

        Returns:
            Dict with team names and vote percentages, or None.
        """
        text = element.get_text(separator=" ", strip=True)
        if not text:
            return None

        # Look for percentage patterns like "45.2%" or "45.2"
        pct_matches = re.findall(r"(\d{1,3}(?:\.\d{1,2})?)\s*%?", text)
        if len(pct_matches) < 3:
            return None

        # Try to extract team names from links or text nodes.
        team_links = element.find_all("a")
        team_names = [a.get_text(strip=True) for a in team_links if a.get_text(strip=True)]

        # Fallback: extract from td cells.
        if len(team_names) < 2:
            cells = element.find_all("td")
            team_candidates = [
                c.get_text(strip=True)
                for c in cells
                if c.get_text(strip=True) and not re.match(r"^[\d.%]+$", c.get_text(strip=True))
            ]
            team_names = team_candidates[:2] if len(team_candidates) >= 2 else team_names

        if len(team_names) < 2:
            return None

        # The three percentages correspond to home-win, draw, away-win.
        try:
            percentages = [float(p) for p in pct_matches[:3]]
        except ValueError:
            return None

        # Sanity check: percentages should roughly sum to ~100.
        total = sum(percentages)
        if total < 10.0:
            return None

        # Normalize if they don't sum to 100.
        if abs(total - 100.0) > 5.0:
            if total > 0:
                percentages = [p / total * 100.0 for p in percentages]

        return {
            "match_number": fallback_number,
            "home_team": team_names[0],
            "away_team": team_names[1],
            "home_vote_pct": round(percentages[0], 1),
            "draw_vote_pct": round(percentages[1], 1),
            "away_vote_pct": round(percentages[2], 1),
        }
