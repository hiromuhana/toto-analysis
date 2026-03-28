"""Toto official site (toto-dream.com) collector.

Fetches match list and voting data from the official toto site.
HTML structure:
  - Match list: div#detail02 > table, rows with match number in first td
  - Voting: li-based structure with tohyo_ class prefix
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import TOTO_OFFICIAL_URL

logger = logging.getLogger(__name__)


class TotoOfficialCollector(BaseCollector):
    """Scrapes sp.toto-dream.com for toto match list and voting data.

    Verified working URLs:
        Match info: /dcs/subos/screen/si01/ssin026/PGSSIN02601InittotoSP.form?holdCntId={round}
        Voting:     /dcs/subos/screen/si01/ssin025/PGSSIN02501ForwardVotetotoSP.form
                    ?holdCntId={round}&commodityId=01&gameAssortment=9&fromId=SSIN026
    """

    MATCH_INFO_PATH = (
        "/dcs/subos/screen/si01/ssin026/PGSSIN02601InittotoSP.form"
    )
    VOTING_PATH = (
        "/dcs/subos/screen/si01/ssin025/PGSSIN02501ForwardVotetotoSP.form"
    )

    def __init__(self) -> None:
        super().__init__(name="toto_official")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect match list and voting data from toto official site.

        Args:
            toto_round: The toto round number.

        Returns:
            Dictionary with 'matches' and 'votes' data.
        """
        matches = await self._fetch_matches(toto_round)
        votes = await self._fetch_voting(toto_round)

        logger.info(
            "[%s] Collected %d matches, %d vote records for round %d.",
            self.name, len(matches), len(votes), toto_round,
        )
        return {
            "source": "toto-dream.com",
            "toto_round": toto_round,
            "matches": matches,
            "votes": votes,
        }

    async def _fetch_matches(self, toto_round: int) -> list[dict[str, Any]]:
        """Fetch match list from the toto info page."""
        url = f"{TOTO_OFFICIAL_URL}{self.MATCH_INFO_PATH}"
        params = {"holdCntId": str(toto_round)}
        cache_key = f"toto_matches_{toto_round}"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            return self._parse_matches(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch matches for round %d.",
                self.name, toto_round, exc_info=True,
            )
            return []

    async def _fetch_voting(self, toto_round: int) -> list[dict[str, Any]]:
        """Fetch voting percentages from the toto voting page."""
        url = f"{TOTO_OFFICIAL_URL}{self.VOTING_PATH}"
        params = {
            "holdCntId": str(toto_round),
            "commodityId": "01",
            "gameAssortment": "9",
            "fromId": "SSIN026",
        }
        cache_key = f"toto_vote_{toto_round}_01"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            return self._parse_voting(html)
        except Exception:
            logger.warning(
                "[%s] Failed to fetch voting for round %d.",
                self.name, toto_round, exc_info=True,
            )
            return []

    def _parse_matches(self, html: str) -> list[dict[str, Any]]:
        """Parse match list from toto info page.

        Structure: div#detail02 > table > tr with [No, Date, Stadium, Home VS Away]
        """
        soup = BeautifulSoup(html, "lxml")
        detail = soup.find("div", id="detail02")
        if not detail:
            logger.warning("[%s] div#detail02 not found.", self.name)
            return []

        table = detail.find("table")
        if not table:
            logger.warning("[%s] No table in div#detail02.", self.name)
            return []

        records: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            texts = [c.get_text(strip=True) for c in cells]
            if not texts or not re.match(r"^\d+$", texts[0]):
                continue

            match_no = int(texts[0])
            full_text = row.get_text(" ", strip=True)

            date_match = re.search(r"(\d{2}/\d{2})\s*(\d{2}:\d{2})", full_text)
            match_date = date_match.group(1) if date_match else ""
            match_time = date_match.group(2) if date_match else ""

            vs_match = re.search(r"(\S+)\s*(?:VS|ＶＳ|vs)\s*(\S+)", full_text)
            if not vs_match:
                continue

            home_team = vs_match.group(1)
            away_team = vs_match.group(2)
            stadium = texts[2] if len(texts) > 2 else ""

            records.append({
                "match_number": match_no,
                "home_team": home_team,
                "away_team": away_team,
                "match_date": match_date,
                "match_time": match_time,
                "stadium": stadium,
            })

        logger.info("[%s] Parsed %d matches.", self.name, len(records))
        return records

    def _parse_voting(self, html: str) -> list[dict[str, Any]]:
        """Parse voting percentages from the toto voting page."""
        soup = BeautifulSoup(html, "lxml")
        records: list[dict[str, Any]] = []

        pct_elements = soup.find_all(string=re.compile(r"\d+\.\d+%"))
        if not pct_elements:
            logger.warning("[%s] No percentage data found in voting page.", self.name)
            return records

        pcts: list[float] = []
        for elem in pct_elements:
            for m in re.finditer(r"(\d+\.\d+)%", str(elem)):
                pcts.append(float(m.group(1)))

        match_count = len(pcts) // 3
        for i in range(match_count):
            records.append({
                "match_number": i + 1,
                "home_vote_pct": pcts[i * 3],
                "draw_vote_pct": pcts[i * 3 + 1],
                "away_vote_pct": pcts[i * 3 + 2],
            })

        logger.info("[%s] Parsed voting for %d matches.", self.name, len(records))
        return records
