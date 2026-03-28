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
        """Convert a string to int, returning 0 for non-numeric values."""
        try:
            return int(value.replace(",", ""))
        except (ValueError, AttributeError):
            return 0


# --- Team name mapping: toto short name → official full name ---

TOTO_TO_OFFICIAL: dict[str, str] = {
    # J2
    "横浜FC": "横浜ＦＣ", "湘南": "湘南ベルマーレ",
    "甲府": "ヴァンフォーレ甲府", "大宮": "ＲＢ大宮アルディージャ",
    "秋田": "ブラウブリッツ秋田", "仙台": "ベガルタ仙台",
    "富山": "カターレ富山", "今治": "ＦＣ今治",
    "いわき": "いわきＦＣ", "磐田": "ジュビロ磐田",
    "藤枝": "藤枝ＭＹＦＣ", "札幌": "北海道コンサドーレ札幌",
    "山形": "モンテディオ山形", "栃木Ｃ": "栃木シティ",
    "徳島": "徳島ヴォルティス", "鳥栖": "サガン鳥栖",
    "水戸": "水戸ホーリーホック", "長崎": "Ｖ・ファーレン長崎",
    "千葉": "ジェフユナイテッド千葉", "山口": "レノファ山口ＦＣ",
    "大分": "大分トリニータ", "熊本": "ロアッソ熊本",
    "愛媛": "愛媛ＦＣ", "新潟": "アルビレックス新潟",
    # J3
    "八戸": "ヴァンラーレ八戸", "相模原": "ＳＣ相模原",
    "FC大阪": "ＦＣ大阪", "高知": "高知ユナイテッドＳＣ",
    "宮崎": "テゲバジャーロ宮崎", "滋賀": "ＭＩＯびわこ滋賀",
    "鳥取": "ガイナーレ鳥取", "栃木": "栃木ＳＣ",
    "北九州": "ギラヴァンツ北九州", "金沢": "ツエーゲン金沢",
    "奈良": "奈良クラブ", "福島": "福島ユナイテッドＦＣ",
    "岐阜": "ＦＣ岐阜", "群馬": "ザスパ群馬",
    "松本": "松本山雅ＦＣ", "琉球": "ＦＣ琉球",
    "讃岐": "カマタマーレ讃岐", "長野": "ＡＣ長野パルセイロ",
    "沼津": "アスルクラロ沼津", "鹿児島": "鹿児島ユナイテッドＦＣ",
    # J1
    "鹿島": "鹿島アントラーズ", "浦和": "浦和レッズ",
    "FC東京": "ＦＣ東京", "東京Ｖ": "東京ヴェルディ",
    "町田": "町田ゼルビア", "川崎Ｆ": "川崎フロンターレ",
    "横浜FM": "横浜Ｆ・マリノス", "名古屋": "名古屋グランパス",
    "京都": "京都サンガＦ.Ｃ.", "G大阪": "ガンバ大阪",
    "C大阪": "セレッソ大阪", "神戸": "ヴィッセル神戸",
    "広島": "サンフレッチェ広島", "福岡": "アビスパ福岡",
    "柏": "柏レイソル",
}


async def load_team_stats(year: int = 2025) -> dict[str, dict[str, Any]]:
    """Load team stats from cache or fetch from data.j-league.or.jp.

    Returns a dict mapping toto short names to stats dicts.
    """
    import json
    from toto.config import CACHE_DIR
    from toto.utils import cache as cache_util

    cache_key = f"team_stats_{year}"
    cached = cache_util.get(cache_key)
    if cached:
        # Build reverse mapping: official name → stats
        official_stats = cached if isinstance(cached, dict) else json.loads(cached)
        return _build_toto_name_map(official_stats)

    # Fetch from data.j-league.or.jp
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    all_stats: dict[str, dict[str, Any]] = {}

    # J1=651, J2=655, J3=657 (2025 season competition IDs)
    competition_ids = {"J1": 651, "J2": 655, "J3": 657}

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for league, comp_id in competition_ids.items():
            url = (
                f"https://data.j-league.or.jp/SFRT01/"
                f"?yearId={year}&competitionId={comp_id}"
                f"&competitionSectionId=0&search=search"
            )
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                for table in soup.find_all("table"):
                    rows = table.find_all("tr")
                    if len(rows) < 10:
                        continue
                    for row in rows[1:]:
                        cells = row.find_all(["th", "td"])
                        texts = [c.get_text(strip=True) for c in cells]
                        if len(texts) >= 11 and texts[1].isdigit():
                            team_name = texts[2]
                            all_stats[team_name] = {
                                "rank": int(texts[1]),
                                "team": team_name,
                                "points": int(texts[3]),
                                "played": int(texts[4]),
                                "wins": int(texts[5]),
                                "draws": int(texts[6]),
                                "losses": int(texts[7]),
                                "goals_for": int(texts[8]),
                                "goals_against": int(texts[9]),
                                "league": league,
                                "season": year,
                            }
            except Exception as e:
                logger.warning("Failed to fetch %s standings: %s", league, e)

    if all_stats:
        cache_util.set(cache_key, all_stats)
        logger.info("Loaded stats for %d teams from data.j-league.or.jp", len(all_stats))

    return _build_toto_name_map(all_stats)


def _build_toto_name_map(
    official_stats: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build a mapping from toto short names to stats.

    Tries exact match first, then fuzzy match via TOTO_TO_OFFICIAL mapping.
    """
    result: dict[str, dict[str, Any]] = {}

    # Direct mapping: official name → stats
    for official_name, stats in official_stats.items():
        result[official_name] = stats

    # Reverse mapping: toto short name → stats via TOTO_TO_OFFICIAL
    for short_name, official_name in TOTO_TO_OFFICIAL.items():
        if official_name in official_stats:
            result[short_name] = official_stats[official_name]

    return result
