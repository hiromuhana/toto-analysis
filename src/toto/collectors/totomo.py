"""Toto.cam (totomo) voting rate collector.

Fetches real-time voting percentages from toto.cam.
HTML structure: table.voterate with rows containing
[No, HomeTeam, VoteCount(pct%), VoteCount(pct%), VoteCount(pct%), AwayTeam]
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from toto.collectors.base import BaseCollector
from toto.config import TOTOMO_URL

logger = logging.getLogger(__name__)


class TotomoCollector(BaseCollector):
    """Scrapes toto.cam for voting rate data.

    Verified working URL pattern:
        /totodata/voterates.php?type=toto&totoid={round}
    """

    def __init__(self) -> None:
        super().__init__(name="totomo")

    async def collect(self, toto_round: int, **kwargs: Any) -> dict[str, Any]:
        """Collect voting rate data from toto.cam.

        Args:
            toto_round: The toto round number.

        Returns:
            Dictionary with 'votes' list containing per-match voting data.
        """
        toto_type = kwargs.get("toto_type_str", "toto")
        url = f"{TOTOMO_URL}/totodata/voterates.php"
        params = {"type": toto_type, "totoid": str(toto_round)}
        cache_key = f"totomo_votes_{toto_round}_{toto_type}"

        try:
            html = await self.fetch(url, cache_key=cache_key, params=params)
            votes = self._parse_voterate(html)
            logger.info("[%s] Parsed %d matches for round %d.", self.name, len(votes), toto_round)
            return {
                "source": "toto.cam",
                "toto_round": toto_round,
                "votes": votes,
            }
        except Exception:
            logger.warning(
                "[%s] Failed to fetch voting data for round %d.",
                self.name,
                toto_round,
                exc_info=True,
            )
            return {"source": "toto.cam", "toto_round": toto_round, "votes": []}

    def _parse_voterate(self, html: str) -> list[dict[str, Any]]:
        """Parse the voterate table from toto.cam HTML.

        Expected table structure (class='voterate'):
            Row: [No, HomeTeam, Count(H%), Count(D%), Count(A%), AwayTeam]
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="voterate")
        if table is None:
            logger.warning("[%s] No table.voterate found.", self.name)
            return []

        records: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            texts = [c.get_text(strip=True) for c in cells]

            # First cell should be match number
            try:
                match_no = int(texts[0])
            except (ValueError, IndexError):
                continue

            home_team = texts[1]
            away_team = texts[-1]

            # Extract percentages from middle cells: "234,558（34.58%）"
            pcts: list[float] = []
            for t in texts[2:-1]:
                m = re.search(r"([\d.]+)%", t)
                if m:
                    pcts.append(float(m.group(1)))

            if len(pcts) < 3:
                continue

            records.append({
                "match_number": match_no,
                "home_team": home_team,
                "away_team": away_team,
                "home_vote_pct": pcts[0],
                "draw_vote_pct": pcts[1],
                "away_vote_pct": pcts[2],
            })

        return records
