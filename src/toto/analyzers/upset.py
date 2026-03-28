"""Upset detector for toto match prediction.

Detects upset (haran / wave) patterns by analyzing condition gaps,
momentum reversals, H2H mismatches, environmental disadvantages,
season context, and vote overconfidence. Inspired by toto-roid.com's
Daisy logic.

This is the most critical analyzer in the pipeline: correctly
identifying upsets is the key differentiator for toto profitability.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from toto.config import (
    INTERMEDIATE_DIR,
    STADIUM_LOCATIONS,
    UPSET_PROBABILITY_THRESHOLD,
    UPSET_VOTE_CEILING,
    UPSET_VOTE_FLOOR,
)
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    MatchCondition,
    MatchData,
    MatchOdds,
    MatchUpset,
    OddsAnalysis,
    UpsetAnalysis,
    UpsetPattern,
)

logger = logging.getLogger(__name__)

# Pattern severity weights for final upset score calculation
_PATTERN_WEIGHTS: dict[str, float] = {
    "fatigue_gap": 0.20,
    "momentum_reversal": 0.20,
    "h2h_mismatch": 0.15,
    "environment_disadvantage": 0.10,
    "season_context": 0.15,
    "vote_overconfidence": 0.20,
}


class UpsetDetector:
    """Detects upset patterns and adjusts match probabilities.

    Implements the Daisy logic: when the public votes a favorite at
    50-80% but multiple structural patterns suggest the favorite is
    vulnerable, flag the match as an upset candidate and shift
    probabilities accordingly.
    """

    def analyze(
        self,
        collected: CollectedData,
        condition: ConditionAnalysis,
        odds: OddsAnalysis,
    ) -> UpsetAnalysis:
        """Run upset detection on all matches.

        Args:
            collected: Output from the data-collector agent.
            condition: Output from the condition-analyzer agent.
            odds: Output from the odds-analyzer agent.

        Returns:
            UpsetAnalysis with per-match upset assessments.
        """
        # Build lookup maps for quick access
        condition_map: dict[int, MatchCondition] = {
            c.match_number: c for c in condition.conditions
        }
        odds_map: dict[int, MatchOdds] = {
            o.match_number: o for o in odds.odds
        }

        upsets: list[MatchUpset] = []

        for match in collected.matches:
            match_num = match.match_number
            mc = condition_map.get(match_num)
            mo = odds_map.get(match_num)

            if mc is None or mo is None:
                logger.warning(
                    "Match %d missing condition or odds data, skipping",
                    match_num,
                )
                continue

            # Determine who the "favorite" is based on model probability
            favorite_is_home = mo.model_home_prob >= mo.model_away_prob

            # Detect individual upset patterns
            patterns: list[UpsetPattern] = []

            p = self._detect_fatigue_gap(mc, favorite_is_home)
            if p is not None:
                patterns.append(p)

            p = self._detect_momentum_reversal(mc, match, favorite_is_home)
            if p is not None:
                patterns.append(p)

            p = self._detect_h2h_mismatch(mc, match, favorite_is_home)
            if p is not None:
                patterns.append(p)

            p = self._detect_environment_disadvantage(mc, favorite_is_home)
            if p is not None:
                patterns.append(p)

            p = self._detect_season_context(match)
            if p is not None:
                patterns.append(p)

            p = self._detect_vote_overconfidence(mo, favorite_is_home)
            if p is not None:
                patterns.append(p)

            # Calculate upset score
            upset_score = self._calc_upset_score(patterns)

            # Adjust probabilities
            original_probs = (
                mo.model_home_prob,
                mo.model_draw_prob,
                mo.model_away_prob,
            )
            adj_home, adj_draw, adj_away = self._adjust_probabilities(
                original_probs, upset_score, favorite_is_home
            )

            # Determine if this triggers an upset alert (Daisy rule)
            is_alert = self._is_upset_alert(mo, upset_score, favorite_is_home)

            explanation = self._build_explanation(
                match, patterns, upset_score, is_alert
            )

            match_upset = MatchUpset(
                match_number=match_num,
                home_team=match.home_team,
                away_team=match.away_team,
                upset_score=upset_score,
                patterns=patterns,
                adjusted_home_prob=round(adj_home, 4),
                adjusted_draw_prob=round(adj_draw, 4),
                adjusted_away_prob=round(adj_away, 4),
                is_upset_alert=is_alert,
                explanation=explanation,
            )
            upsets.append(match_upset)

            if is_alert:
                logger.warning(
                    "UPSET ALERT Match %d: %s vs %s (score=%d, patterns=%d)",
                    match_num,
                    match.home_team,
                    match.away_team,
                    upset_score,
                    len(patterns),
                )
            else:
                logger.info(
                    "Match %d: %s vs %s | upset_score=%d patterns=%d",
                    match_num,
                    match.home_team,
                    match.away_team,
                    upset_score,
                    len(patterns),
                )

        result = UpsetAnalysis(
            toto_round=collected.toto_round,
            toto_type=collected.toto_type,
            upsets=upsets,
        )

        self._save(result)
        return result

    # ------------------------------------------------------------------
    # Pattern detectors
    # ------------------------------------------------------------------

    def _detect_fatigue_gap(
        self, mc: MatchCondition, favorite_is_home: bool
    ) -> UpsetPattern | None:
        """Detect fatigue gap between favorite and underdog.

        Flags when the favorite is fatigued (< -0.3) while the
        underdog is well-rested (> 0.3).

        Args:
            mc: Match condition data.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        if favorite_is_home:
            fav_fatigue = mc.fatigue_home
            und_fatigue = mc.fatigue_away
        else:
            fav_fatigue = mc.fatigue_away
            und_fatigue = mc.fatigue_home

        if fav_fatigue < -0.3 and und_fatigue > 0.3:
            gap = und_fatigue - fav_fatigue
            severity = min(1.0, gap / 2.0)

            fav_label = mc.home_team if favorite_is_home else mc.away_team
            und_label = mc.away_team if favorite_is_home else mc.home_team

            return UpsetPattern(
                category="fatigue_gap",
                description=(
                    f"{fav_label} is fatigued ({fav_fatigue:.2f}) while "
                    f"{und_label} is well-rested ({und_fatigue:.2f}). "
                    f"Fatigue gap: {gap:.2f}"
                ),
                severity=round(severity, 3),
            )

        return None

    def _detect_momentum_reversal(
        self,
        mc: MatchCondition,
        match: MatchData,
        favorite_is_home: bool,
    ) -> UpsetPattern | None:
        """Detect momentum reversal between favorite and underdog.

        Flags when the favorite's momentum is negative (declining)
        while the underdog's momentum is positive (rising).

        Args:
            mc: Match condition data.
            match: Match data for additional context.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        if favorite_is_home:
            fav_momentum = mc.momentum_home
            und_momentum = mc.momentum_away
        else:
            fav_momentum = mc.momentum_away
            und_momentum = mc.momentum_home

        if fav_momentum < 0.0 and und_momentum > 0.0:
            gap = und_momentum - fav_momentum
            severity = min(1.0, gap / 2.0)

            fav_label = mc.home_team if favorite_is_home else mc.away_team
            und_label = mc.away_team if favorite_is_home else mc.home_team

            return UpsetPattern(
                category="momentum_reversal",
                description=(
                    f"{fav_label} momentum declining ({fav_momentum:.2f}) "
                    f"while {und_label} is rising ({und_momentum:.2f}). "
                    f"Momentum gap: {gap:.2f}"
                ),
                severity=round(severity, 3),
            )

        return None

    def _detect_h2h_mismatch(
        self,
        mc: MatchCondition,
        match: MatchData,
        favorite_is_home: bool,
    ) -> UpsetPattern | None:
        """Detect head-to-head mismatch unfavorable to the favorite.

        Flags when the favorite has a poor H2H record against this
        specific opponent (historical "bogey team" effect).

        Args:
            mc: Match condition data.
            match: Match data with H2H records.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        if len(match.h2h) < 3:
            return None

        h2h_affinity = mc.h2h_affinity

        # If favorite is home and H2H affinity is negative,
        # or favorite is away and H2H affinity is positive (favors home),
        # then the favorite has a poor H2H record.
        if favorite_is_home and h2h_affinity < -0.2:
            severity = min(1.0, abs(h2h_affinity))
            return UpsetPattern(
                category="h2h_mismatch",
                description=(
                    f"Home favorite {mc.home_team} has poor H2H record "
                    f"against {mc.away_team} (affinity: {h2h_affinity:.2f})"
                ),
                severity=round(severity, 3),
            )

        if not favorite_is_home and h2h_affinity > 0.2:
            severity = min(1.0, abs(h2h_affinity))
            return UpsetPattern(
                category="h2h_mismatch",
                description=(
                    f"Away favorite {mc.away_team} has poor H2H record "
                    f"against {mc.home_team} (affinity: {h2h_affinity:.2f}, "
                    f"favors home)"
                ),
                severity=round(severity, 3),
            )

        return None

    def _detect_environment_disadvantage(
        self, mc: MatchCondition, favorite_is_home: bool
    ) -> UpsetPattern | None:
        """Detect environmental disadvantage for the favorite.

        Flags when the favorite is playing a long-distance away game
        (>800km travel).

        Args:
            mc: Match condition data.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        # Only relevant when the favorite is away
        if favorite_is_home:
            return None

        if mc.travel_distance_km > 800.0:
            # Scale severity: 800km = 0.4, 1200km+ = 1.0
            severity = min(1.0, 0.4 + (mc.travel_distance_km - 800.0) / 1000.0)

            return UpsetPattern(
                category="environment_disadvantage",
                description=(
                    f"Away favorite {mc.away_team} traveling "
                    f"{mc.travel_distance_km:.0f}km to {mc.home_team}'s "
                    f"ground. Long-distance travel fatigue risk."
                ),
                severity=round(severity, 3),
            )

        return None

    def _detect_season_context(
        self, match: MatchData
    ) -> UpsetPattern | None:
        """Detect abnormal motivation from season context.

        - Relegation battle teams (rank >= 16 in J1) show abnormal
          strength ("must-win" desperation).
        - Post-championship / already-safe teams may show motivation drop.

        Args:
            match: Match data with season stats.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        home_stats = match.home_season_stats
        away_stats = match.away_season_stats

        # Determine favorite/underdog by points (simple heuristic)
        home_is_stronger = home_stats.points >= away_stats.points

        # Relegation battle: underdog is in relegation zone and may
        # fight harder than expected
        relegation_zone_rank = 16  # Bottom 3 in J1 (18 teams -> rank 16+)

        if home_is_stronger:
            underdog_stats = away_stats
            underdog_name = match.away_team
            fav_stats = home_stats
            fav_name = match.home_team
        else:
            underdog_stats = home_stats
            underdog_name = match.home_team
            fav_stats = away_stats
            fav_name = match.away_team

        # Underdog in relegation battle -> extra motivation
        if underdog_stats.rank >= relegation_zone_rank and underdog_stats.played >= 10:
            severity = min(1.0, (underdog_stats.rank - relegation_zone_rank + 1) * 0.25)
            return UpsetPattern(
                category="season_context",
                description=(
                    f"{underdog_name} is in relegation battle "
                    f"(rank {underdog_stats.rank}). Desperation boost expected."
                ),
                severity=round(severity, 3),
            )

        # Favorite is comfortably top -> possible motivation drop
        if (
            fav_stats.rank <= 3
            and fav_stats.played >= 25
            and (fav_stats.points - underdog_stats.points) > 15
        ):
            return UpsetPattern(
                category="season_context",
                description=(
                    f"{fav_name} (rank {fav_stats.rank}) may have reduced "
                    f"motivation with a comfortable lead. "
                    f"Point gap: {fav_stats.points - underdog_stats.points}"
                ),
                severity=0.4,
            )

        return None

    def _detect_vote_overconfidence(
        self, mo: MatchOdds, favorite_is_home: bool
    ) -> UpsetPattern | None:
        """Detect vote overconfidence (core Daisy logic).

        Flags when the favorite's vote share is between UPSET_VOTE_FLOOR
        (50%) and UPSET_VOTE_CEILING (80%), but the model probability
        diverges significantly (>15% gap).

        Args:
            mo: Match odds data.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            UpsetPattern if detected, None otherwise.
        """
        if favorite_is_home:
            fav_vote = mo.home_vote_pct / 100.0
            fav_model_prob = mo.model_home_prob
            fav_name = mo.home_team
        else:
            fav_vote = mo.away_vote_pct / 100.0
            fav_model_prob = mo.model_away_prob
            fav_name = mo.away_team

        # Daisy rule: only consider matches where favorite vote share
        # is in the critical zone
        if not (UPSET_VOTE_FLOOR <= fav_vote <= UPSET_VOTE_CEILING):
            return None

        # Check for significant divergence between votes and model
        divergence = fav_vote - fav_model_prob

        if divergence > 0.15:
            severity = min(1.0, divergence / 0.30)
            return UpsetPattern(
                category="vote_overconfidence",
                description=(
                    f"{fav_name} receives {fav_vote:.0%} of votes but model "
                    f"assigns only {fav_model_prob:.0%} win probability. "
                    f"Divergence: {divergence:.0%}. Public may be overconfident."
                ),
                severity=round(severity, 3),
            )

        return None

    # ------------------------------------------------------------------
    # Scoring and adjustment
    # ------------------------------------------------------------------

    def _calc_upset_score(self, patterns: list[UpsetPattern]) -> int:
        """Calculate overall upset score from detected patterns.

        Uses a weighted sum of pattern severities, normalized to 0-100.

        Args:
            patterns: List of detected upset patterns.

        Returns:
            Integer score in [0, 100].
        """
        if not patterns:
            return 0

        weighted_sum = 0.0
        total_weight = 0.0

        for pattern in patterns:
            weight = _PATTERN_WEIGHTS.get(pattern.category, 0.10)
            weighted_sum += pattern.severity * weight
            total_weight += weight

        if total_weight == 0.0:
            return 0

        # Normalize to 0-1 range, then scale to 0-100
        # The maximum possible weighted_sum equals total_weight (all severity=1.0)
        normalized = weighted_sum / sum(_PATTERN_WEIGHTS.values())

        # Bonus for having multiple patterns (compound risk)
        pattern_count_bonus = min(0.15, len(patterns) * 0.03)
        normalized = min(1.0, normalized + pattern_count_bonus)

        return int(round(normalized * 100))

    def _adjust_probabilities(
        self,
        original_probs: tuple[float, float, float],
        upset_score: int,
        favorite_is_home: bool,
    ) -> tuple[float, float, float]:
        """Adjust match probabilities based on upset score.

        Shifts probability away from the favorite proportional to the
        upset score. The shifted probability is distributed to draw
        and the underdog.

        Args:
            original_probs: (home_prob, draw_prob, away_prob).
            upset_score: Upset score in [0, 100].
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            Adjusted (home_prob, draw_prob, away_prob).
        """
        home_prob, draw_prob, away_prob = original_probs

        if upset_score == 0:
            return (home_prob, draw_prob, away_prob)

        # Maximum shift is 20% of favorite's probability
        shift_factor = (upset_score / 100.0) * 0.20

        if favorite_is_home:
            shift_amount = home_prob * shift_factor
            home_prob -= shift_amount
            # Distribute: 40% to draw, 60% to underdog
            draw_prob += shift_amount * 0.40
            away_prob += shift_amount * 0.60
        else:
            shift_amount = away_prob * shift_factor
            away_prob -= shift_amount
            draw_prob += shift_amount * 0.40
            home_prob += shift_amount * 0.60

        # Ensure valid probability distribution
        total = home_prob + draw_prob + away_prob
        if total > 0:
            home_prob /= total
            draw_prob /= total
            away_prob /= total

        return (
            round(home_prob, 4),
            round(draw_prob, 4),
            round(away_prob, 4),
        )

    def _is_upset_alert(
        self,
        mo: MatchOdds,
        upset_score: int,
        favorite_is_home: bool,
    ) -> bool:
        """Determine whether to raise an upset alert.

        Implements the Daisy rule: only flag when:
        1. Favorite vote share is between UPSET_VOTE_FLOOR and
           UPSET_VOTE_CEILING.
        2. Upset probability (score/100) exceeds UPSET_PROBABILITY_THRESHOLD.

        Args:
            mo: Match odds data.
            upset_score: Calculated upset score.
            favorite_is_home: Whether the favorite is the home team.

        Returns:
            True if upset alert should be raised.
        """
        if favorite_is_home:
            fav_vote_share = mo.home_vote_pct / 100.0
        else:
            fav_vote_share = mo.away_vote_pct / 100.0

        in_daisy_zone = (
            UPSET_VOTE_FLOOR <= fav_vote_share <= UPSET_VOTE_CEILING
        )
        above_threshold = (upset_score / 100.0) >= UPSET_PROBABILITY_THRESHOLD

        return in_daisy_zone and above_threshold

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_explanation(
        self,
        match: MatchData,
        patterns: list[UpsetPattern],
        upset_score: int,
        is_alert: bool,
    ) -> str:
        """Build a human-readable explanation of the upset analysis.

        Args:
            match: Match data.
            patterns: Detected upset patterns.
            upset_score: Overall upset score.
            is_alert: Whether an upset alert was triggered.

        Returns:
            Explanation string.
        """
        if not patterns:
            return (
                f"{match.home_team} vs {match.away_team}: "
                f"No significant upset patterns detected."
            )

        parts = [
            f"{match.home_team} vs {match.away_team}: "
            f"Upset score {upset_score}/100."
        ]

        if is_alert:
            parts.append("*** UPSET ALERT ***")

        parts.append(f"Detected {len(patterns)} pattern(s):")
        for i, p in enumerate(patterns, 1):
            parts.append(f"  {i}. [{p.category}] {p.description}")

        return " ".join(parts)

    def _save(self, result: UpsetAnalysis) -> None:
        """Save upset analysis to intermediate JSON file.

        Args:
            result: The UpsetAnalysis to persist.
        """
        output_path = INTERMEDIATE_DIR / "upset_analysis.json"
        output_path.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info("Upset analysis saved to %s", output_path)
