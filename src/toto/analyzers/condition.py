"""Condition analyzer for toto match prediction.

Evaluates team condition factors (fatigue, momentum, venue advantage,
head-to-head affinity, weather) and produces a ConditionAnalysis output.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime

import numpy as np
from scipy import stats

from toto.config import INTERMEDIATE_DIR, STADIUM_LOCATIONS
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    MatchCondition,
    MatchData,
    RecentMatch,
)

logger = logging.getLogger(__name__)


class ConditionAnalyzer:
    """Analyzes team condition factors for each toto match.

    Produces per-match condition scores covering fatigue, momentum,
    venue advantage, head-to-head affinity, and weather impact.
    """

    def analyze(self, collected_data: CollectedData) -> ConditionAnalysis:
        """Run condition analysis on all matches in the collected data.

        Args:
            collected_data: Output from the data-collector agent.

        Returns:
            ConditionAnalysis with per-match condition breakdowns.
        """
        conditions: list[MatchCondition] = []

        for match in collected_data.matches:
            fatigue_home = self._calc_fatigue(match.home_recent, match.home_team)
            fatigue_away = self._calc_fatigue(match.away_recent, match.away_team)
            momentum_home = self._calc_momentum(match.home_recent)
            momentum_away = self._calc_momentum(match.away_recent)
            venue_advantage = self._calc_venue_advantage(
                match.home_team, match.away_team
            )
            h2h_affinity = self._calc_h2h_affinity(match.h2h, match.home_team)
            weather_impact = self._calc_weather_impact()
            travel_distance = self._haversine_distance(
                match.home_team, match.away_team
            )
            days_rest_home = self._days_since_last_match(match.home_recent)
            days_rest_away = self._days_since_last_match(match.away_recent)

            total_home = (
                fatigue_home * 0.25
                + momentum_home * 0.30
                + venue_advantage * 0.20
                + h2h_affinity * 0.15
                + weather_impact * 0.10
            )
            total_away = (
                fatigue_away * 0.25
                + momentum_away * 0.30
                + (-venue_advantage) * 0.20
                + (-h2h_affinity) * 0.15
                + weather_impact * 0.10
            )

            condition = MatchCondition(
                match_number=match.match_number,
                home_team=match.home_team,
                away_team=match.away_team,
                fatigue_home=round(max(-1.0, min(1.0, fatigue_home)), 4),
                fatigue_away=round(max(-1.0, min(1.0, fatigue_away)), 4),
                momentum_home=round(max(-1.0, min(1.0, momentum_home)), 4),
                momentum_away=round(max(-1.0, min(1.0, momentum_away)), 4),
                venue_advantage=round(max(-1.0, min(1.0, venue_advantage)), 4),
                h2h_affinity=round(max(-1.0, min(1.0, h2h_affinity)), 4),
                weather_impact=round(max(-1.0, min(1.0, weather_impact)), 4),
                travel_distance_km=round(travel_distance, 1),
                days_rest_home=days_rest_home,
                days_rest_away=days_rest_away,
                total_home_adjustment=round(total_home, 4),
                total_away_adjustment=round(total_away, 4),
            )
            conditions.append(condition)
            logger.info(
                "Match %d: %s vs %s | home_adj=%.3f away_adj=%.3f",
                match.match_number,
                match.home_team,
                match.away_team,
                total_home,
                total_away,
            )

        result = ConditionAnalysis(
            toto_round=collected_data.toto_round,
            toto_type=collected_data.toto_type,
            conditions=conditions,
        )

        self._save(result)
        return result

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _calc_fatigue(
        self, recent_matches: list[RecentMatch], team: str
    ) -> float:
        """Calculate fatigue score for a team.

        Args:
            recent_matches: Recent match history for the team.
            team: Team name (used for logging).

        Returns:
            Float in [-1, 1]. Negative = fatigued, positive = well-rested.
        """
        if not recent_matches:
            return 0.0

        # Days since last match component
        days = self._days_since_last_match(recent_matches)
        if days is None:
            days_score = 0.0
        elif days <= 3:
            days_score = -1.0
        elif days >= 7:
            days_score = 1.0
        else:
            # Linear interpolation between 3 and 7 days
            days_score = (days - 3) / (7 - 3) * 2.0 - 1.0

        # Match density: number of matches in the last 30 days
        now = datetime.now()
        matches_in_30d = 0
        for m in recent_matches:
            try:
                match_date = datetime.strptime(m.date, "%Y-%m-%d")
                delta = (now - match_date).days
                if 0 <= delta <= 30:
                    matches_in_30d += 1
            except (ValueError, TypeError):
                continue

        # Typical: 3-4 matches/month is normal, 6+ is heavy
        if matches_in_30d <= 2:
            density_score = 0.5
        elif matches_in_30d <= 4:
            density_score = 0.0
        elif matches_in_30d <= 6:
            density_score = -0.5
        else:
            density_score = -1.0

        # Weighted combination
        fatigue = days_score * 0.6 + density_score * 0.4
        return max(-1.0, min(1.0, fatigue))

    def _calc_momentum(self, recent_matches: list[RecentMatch]) -> float:
        """Calculate momentum score based on recent results and goal trends.

        Args:
            recent_matches: Recent match history (most recent first expected).

        Returns:
            Float in [-1, 1]. Positive = improving form.
        """
        if not recent_matches:
            return 0.0

        last_5 = recent_matches[:5]

        # Win/draw/loss pattern score
        result_points: list[float] = []
        for m in last_5:
            if m.result.value == "1" and m.home_or_away == "home":
                result_points.append(1.0)
            elif m.result.value == "2" and m.home_or_away == "away":
                result_points.append(1.0)
            elif m.result.value == "0":
                result_points.append(0.0)
            else:
                result_points.append(-1.0)

        if result_points:
            result_score = sum(result_points) / len(result_points)
        else:
            result_score = 0.0

        # Goal difference trend via linear regression
        goal_diffs = [m.goals_for - m.goals_against for m in last_5]

        if len(goal_diffs) >= 2:
            x = np.arange(len(goal_diffs), dtype=np.float64)
            y = np.array(goal_diffs, dtype=np.float64)
            slope, _, _, _, _ = stats.linregress(x, y)
            # Normalize slope: typical range is about [-2, 2]
            trend_score = float(np.clip(slope / 2.0, -1.0, 1.0))
        else:
            trend_score = 0.0

        momentum = result_score * 0.6 + trend_score * 0.4
        return max(-1.0, min(1.0, momentum))

    def _calc_venue_advantage(
        self, home_team: str, away_team: str
    ) -> float:
        """Calculate venue advantage for the home team.

        Args:
            home_team: Home team name.
            away_team: Away team name.

        Returns:
            Float representing home advantage. Base +0.3, with travel
            distance correction.
        """
        base_advantage = 0.3
        distance = self._haversine_distance(home_team, away_team)

        # Long-distance travel penalty for away team (benefit for home)
        if distance > 800.0:
            base_advantage += 0.1
        elif distance > 500.0:
            base_advantage += 0.05

        return min(1.0, base_advantage)

    def _calc_h2h_affinity(
        self, h2h_matches: list[RecentMatch], home_team: str
    ) -> float:
        """Calculate head-to-head affinity from the home team's perspective.

        Args:
            h2h_matches: Past head-to-head match records.
            home_team: The home team in the current fixture.

        Returns:
            Float in [-1, 1]. Positive = home team historically dominant.
            Returns 0.0 if fewer than 3 H2H matches are available.
        """
        if len(h2h_matches) < 3:
            return 0.0

        wins = 0
        losses = 0
        for m in h2h_matches:
            is_home_perspective = m.home_or_away == "home"
            if m.result.value == "1" and is_home_perspective:
                wins += 1
            elif m.result.value == "2" and is_home_perspective:
                losses += 1
            elif m.result.value == "2" and not is_home_perspective:
                wins += 1
            elif m.result.value == "1" and not is_home_perspective:
                losses += 1
            # Draws don't count toward wins or losses

        total = len(h2h_matches)
        win_rate = wins / total
        loss_rate = losses / total

        # Map win rate to [-1, 1]: 0% wins -> -1, 50% -> 0, 100% -> +1
        affinity = (win_rate - loss_rate)
        return max(-1.0, min(1.0, affinity))

    def _calc_weather_impact(self) -> float:
        """Calculate weather impact on match conditions.

        Returns:
            Always 0.0. Weather API integration is TBD.
        """
        # TODO: Integrate weather API (OpenWeatherMap or JMA).
        # Heavy rain / extreme heat would penalize technically-oriented teams.
        return 0.0

    def _haversine_distance(self, team_a: str, team_b: str) -> float:
        """Calculate great-circle distance between two teams' stadiums.

        Args:
            team_a: First team name.
            team_b: Second team name.

        Returns:
            Distance in kilometers. Returns 0.0 if either team is not
            found in STADIUM_LOCATIONS.
        """
        loc_a = STADIUM_LOCATIONS.get(team_a)
        loc_b = STADIUM_LOCATIONS.get(team_b)

        if loc_a is None or loc_b is None:
            logger.warning(
                "Stadium location not found for %s or %s", team_a, team_b
            )
            return 0.0

        lat1, lon1 = math.radians(loc_a[0]), math.radians(loc_a[1])
        lat2, lon2 = math.radians(loc_b[0]), math.radians(loc_b[1])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.asin(math.sqrt(a))

        # Earth's mean radius in km
        earth_radius_km = 6371.0
        return earth_radius_km * c

    def _days_since_last_match(
        self, recent_matches: list[RecentMatch]
    ) -> int | None:
        """Calculate days since the team's most recent match.

        Args:
            recent_matches: Recent match history.

        Returns:
            Number of days since last match, or None if no valid dates.
        """
        if not recent_matches:
            return None

        now = datetime.now()
        for m in recent_matches:
            try:
                match_date = datetime.strptime(m.date, "%Y-%m-%d")
                return (now - match_date).days
            except (ValueError, TypeError):
                continue

        return None

    def _save(self, result: ConditionAnalysis) -> None:
        """Save condition analysis to intermediate JSON file.

        Args:
            result: The ConditionAnalysis to persist.
        """
        output_path = INTERMEDIATE_DIR / "condition_analysis.json"
        output_path.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info("Condition analysis saved to %s", output_path)
