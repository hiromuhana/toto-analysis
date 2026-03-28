"""Mock data collector for development and fallback scenarios."""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from toto.collectors.base import BaseCollector
from toto.config import J1_TEAMS, MOCK_DIR
from toto.models.schemas import (
    CollectedData,
    MatchData,
    MatchResult,
    RecentMatch,
    SeasonStats,
)

logger = logging.getLogger(__name__)


class MockCollector(BaseCollector):
    """Generates realistic mock data for development and testing.

    This collector serves as a fallback when real data sources are
    unavailable. It produces plausible J-League statistics based on
    real team names and typical performance distributions.

    If JSON files exist in data/mock/, they are loaded and used instead
    of generating random data.
    """

    def __init__(self) -> None:
        super().__init__(name="mock")

    async def collect(self, toto_round: int, **kwargs: Any) -> CollectedData:
        """Generate or load mock data for a toto round.

        Args:
            toto_round: The toto round number.
            **kwargs: Optional 'num_matches' (default 13),
                      'toto_type' ('toto' or 'minitoto').

        Returns:
            CollectedData with realistic mock match data.
        """
        toto_type: str = kwargs.get("toto_type", "toto")
        num_matches = 13 if toto_type == "toto" else 5

        # Try loading from file first.
        file_data = self._load_from_file(toto_round)
        if file_data is not None:
            logger.info(
                "[%s] Loaded mock data from file for round %d.",
                self.name,
                toto_round,
            )
            return file_data

        # Generate fresh mock data.
        logger.info(
            "[%s] Generating mock data for round %d (%d matches).",
            self.name,
            toto_round,
            num_matches,
        )
        matches = self._generate_matches(num_matches, toto_round)

        return CollectedData(
            toto_round=toto_round,
            matches=matches,
            data_sources=["mock"],
        )

    def _load_from_file(self, toto_round: int) -> CollectedData | None:
        """Attempt to load mock data from a JSON file.

        Args:
            toto_round: Round number used to locate the file.

        Returns:
            CollectedData if file exists and parses correctly, else None.
        """
        candidates = [
            MOCK_DIR / f"round_{toto_round}.json",
            MOCK_DIR / f"toto_{toto_round}.json",
            MOCK_DIR / "default.json",
        ]

        for path in candidates:
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    return CollectedData.model_validate(raw)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(
                        "[%s] Failed to parse mock file %s: %s",
                        self.name,
                        path,
                        e,
                    )

        return None

    def _generate_matches(
        self, num_matches: int, toto_round: int
    ) -> list[MatchData]:
        """Generate a list of realistic mock matches.

        Args:
            num_matches: Number of matches to generate (13 or 5).
            toto_round: Round number (used for date seeding).

        Returns:
            List of MatchData with plausible statistics.
        """
        # Use a seeded RNG for reproducibility per round.
        rng = random.Random(toto_round * 1000 + num_matches)

        # Shuffle teams and pair them up.
        available_teams = list(J1_TEAMS)
        rng.shuffle(available_teams)

        # Ensure we have enough teams for the required matches.
        if len(available_teams) < num_matches * 2:
            # Repeat teams if necessary (unlikely for J1 with 21 teams).
            available_teams = available_teams * 3

        matches: list[MatchData] = []
        base_date = datetime(2026, 3, 28) + timedelta(days=(toto_round % 52) * 7)

        for i in range(num_matches):
            home_team = available_teams[i * 2]
            away_team = available_teams[i * 2 + 1]

            match_date = (base_date + timedelta(days=rng.choice([0, 1]))).strftime(
                "%Y-%m-%d"
            )

            home_stats = self._generate_season_stats(rng)
            away_stats = self._generate_season_stats(rng)
            home_recent = self._generate_recent_matches(rng, home_team)
            away_recent = self._generate_recent_matches(rng, away_team)
            h2h = self._generate_h2h(rng, home_team, away_team)

            home_elo = 1500.0 + rng.gauss(0, 100)
            away_elo = 1500.0 + rng.gauss(0, 100)

            matches.append(
                MatchData(
                    match_number=i + 1,
                    home_team=home_team,
                    away_team=away_team,
                    stadium=f"{home_team}ホームスタジアム",
                    match_date=match_date,
                    home_season_stats=home_stats,
                    away_season_stats=away_stats,
                    home_recent=home_recent,
                    away_recent=away_recent,
                    h2h=h2h,
                    home_elo=round(home_elo, 1),
                    away_elo=round(away_elo, 1),
                    home_attack_rating=round(rng.uniform(0.6, 1.5), 2),
                    away_attack_rating=round(rng.uniform(0.6, 1.5), 2),
                    home_defense_rating=round(rng.uniform(0.6, 1.5), 2),
                    away_defense_rating=round(rng.uniform(0.6, 1.5), 2),
                )
            )

        return matches

    def _generate_season_stats(self, rng: random.Random) -> SeasonStats:
        """Generate plausible season statistics for a team.

        Generates stats resembling a mid-season J1 team: roughly 15 matches
        played with a realistic win/draw/loss distribution.

        Args:
            rng: Seeded random number generator.

        Returns:
            SeasonStats with realistic values.
        """
        played = rng.randint(10, 20)

        # Generate W/D/L that sum to played.
        win_rate = rng.uniform(0.25, 0.60)
        draw_rate = rng.uniform(0.10, 0.30)
        wins = max(0, round(played * win_rate))
        draws = max(0, round(played * draw_rate))
        losses = max(0, played - wins - draws)

        # Ensure they sum correctly.
        total = wins + draws + losses
        if total != played:
            losses = played - wins - draws
            if losses < 0:
                draws += losses
                losses = 0

        goals_for = max(0, round(played * rng.uniform(0.8, 2.0)))
        goals_against = max(0, round(played * rng.uniform(0.6, 1.8)))
        points = wins * 3 + draws

        rank = rng.randint(1, len(J1_TEAMS))

        xg = round(goals_for * rng.uniform(0.85, 1.15), 1)
        xga = round(goals_against * rng.uniform(0.85, 1.15), 1)

        return SeasonStats(
            played=played,
            wins=wins,
            draws=draws,
            losses=losses,
            goals_for=goals_for,
            goals_against=goals_against,
            points=points,
            rank=rank,
            xg=xg,
            xga=xga,
        )

    def _generate_recent_matches(
        self, rng: random.Random, team_name: str, count: int = 5
    ) -> list[RecentMatch]:
        """Generate recent match history for a team.

        Args:
            rng: Seeded random number generator.
            team_name: The team's name.
            count: Number of recent matches to generate.

        Returns:
            List of RecentMatch records.
        """
        opponents = [t for t in J1_TEAMS if t != team_name]
        recent: list[RecentMatch] = []

        for j in range(count):
            opponent = rng.choice(opponents)
            is_home = rng.choice([True, False])
            goals_for = rng.choices(range(5), weights=[25, 35, 20, 12, 8])[0]
            goals_against = rng.choices(range(5), weights=[25, 35, 20, 12, 8])[0]

            if goals_for > goals_against:
                result = MatchResult.HOME_WIN if is_home else MatchResult.AWAY_WIN
            elif goals_for == goals_against:
                result = MatchResult.DRAW
            else:
                result = MatchResult.AWAY_WIN if is_home else MatchResult.HOME_WIN

            match_date = (
                datetime(2026, 3, 28) - timedelta(days=(j + 1) * 7)
            ).strftime("%Y-%m-%d")

            recent.append(
                RecentMatch(
                    date=match_date,
                    opponent=opponent,
                    home_or_away="home" if is_home else "away",
                    goals_for=goals_for,
                    goals_against=goals_against,
                    result=result,
                )
            )

        return recent

    def _generate_h2h(
        self,
        rng: random.Random,
        home_team: str,
        away_team: str,
        count: int = 3,
    ) -> list[RecentMatch]:
        """Generate head-to-head history between two teams.

        Args:
            rng: Seeded random number generator.
            home_team: Home team name.
            away_team: Away team name.
            count: Number of H2H matches to generate.

        Returns:
            List of RecentMatch for past meetings.
        """
        h2h: list[RecentMatch] = []
        for j in range(count):
            is_home = j % 2 == 0
            goals_for = rng.choices(range(4), weights=[20, 35, 25, 20])[0]
            goals_against = rng.choices(range(4), weights=[20, 35, 25, 20])[0]

            if goals_for > goals_against:
                result = MatchResult.HOME_WIN
            elif goals_for == goals_against:
                result = MatchResult.DRAW
            else:
                result = MatchResult.AWAY_WIN

            match_date = (
                datetime(2026, 3, 28) - timedelta(days=(j + 1) * 90)
            ).strftime("%Y-%m-%d")

            h2h.append(
                RecentMatch(
                    date=match_date,
                    opponent=away_team if is_home else home_team,
                    home_or_away="home" if is_home else "away",
                    goals_for=goals_for,
                    goals_against=goals_against,
                    result=result,
                )
            )

        return h2h
