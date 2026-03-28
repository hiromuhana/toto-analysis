"""Odds analyzer for toto match prediction.

Analyzes toto voting percentages, calculates implied probabilities,
detects public biases, and identifies value bet opportunities.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from toto.config import INTERMEDIATE_DIR
from toto.models.schemas import (
    CollectedData,
    MatchData,
    MatchOdds,
    OddsAnalysis,
)

logger = logging.getLogger(__name__)

# toto's approximate payout ratio (take rate ~50%)
_TOTO_PAYOUT_RATIO = 0.50


class OddsAnalyzer:
    """Analyzes toto voting percentages and detects market biases.

    Compares public vote distribution against model probabilities
    (Dixon-Coles or ensemble) to find value and detect systematic biases.
    """

    def analyze(
        self,
        collected_data: CollectedData,
        vote_data: dict[int, dict[str, float]] | None = None,
    ) -> OddsAnalysis:
        """Run odds analysis on all matches.

        Args:
            collected_data: Output from the data-collector agent.
            vote_data: Optional voting data keyed by match_number.
                Each value is a dict with keys "home", "draw", "away"
                containing vote percentages (0-100 scale).
                If None, equal distribution (33/33/33) is assumed.

        Returns:
            OddsAnalysis with per-match odds breakdowns.
        """
        odds_list: list[MatchOdds] = []

        for match in collected_data.matches:
            # Extract vote percentages
            if vote_data and match.match_number in vote_data:
                votes = vote_data[match.match_number]
                home_pct = votes.get("home", 33.33)
                draw_pct = votes.get("draw", 33.33)
                away_pct = votes.get("away", 33.33)
            else:
                home_pct = 33.33
                draw_pct = 33.33
                away_pct = 33.33
                logger.debug(
                    "Match %d: No vote data, using equal distribution",
                    match.match_number,
                )

            # Calculate implied probabilities from vote percentages
            impl_home, impl_draw, impl_away = self._calc_implied_probs(
                home_pct, draw_pct, away_pct
            )

            # Get model probabilities from Dixon-Coles ratings
            model_home, model_draw, model_away = self._get_model_probs(match)

            # Calculate value (model_prob - implied_prob)
            value_home = self._calc_value(model_home, impl_home)
            value_draw = self._calc_value(model_draw, impl_draw)
            value_away = self._calc_value(model_away, impl_away)

            # Detect biases
            model_probs = (model_home, model_draw, model_away)
            implied_probs = (impl_home, impl_draw, impl_away)
            biases = self._detect_biases(match, implied_probs, model_probs)

            match_odds = MatchOdds(
                match_number=match.match_number,
                home_team=match.home_team,
                away_team=match.away_team,
                home_vote_pct=round(home_pct, 2),
                draw_vote_pct=round(draw_pct, 2),
                away_vote_pct=round(away_pct, 2),
                implied_home_prob=round(impl_home, 4),
                implied_draw_prob=round(impl_draw, 4),
                implied_away_prob=round(impl_away, 4),
                model_home_prob=round(model_home, 4),
                model_draw_prob=round(model_draw, 4),
                model_away_prob=round(model_away, 4),
                value_home=round(value_home, 4),
                value_draw=round(value_draw, 4),
                value_away=round(value_away, 4),
                biases=biases,
            )
            odds_list.append(match_odds)
            logger.info(
                "Match %d: %s vs %s | value_h=%.3f value_d=%.3f "
                "value_a=%.3f biases=%s",
                match.match_number,
                match.home_team,
                match.away_team,
                value_home,
                value_draw,
                value_away,
                biases,
            )

        result = OddsAnalysis(
            toto_round=collected_data.toto_round,
            toto_type=collected_data.toto_type,
            odds=odds_list,
        )

        self._save(result)
        return result

    # ------------------------------------------------------------------
    # Private methods
    # ------------------------------------------------------------------

    def _calc_implied_probs(
        self, home_pct: float, draw_pct: float, away_pct: float
    ) -> tuple[float, float, float]:
        """Convert toto vote percentages to true implied probabilities.

        Removes the overround (toto takes approximately 50% as commission)
        to derive fair probability estimates from public voting patterns.

        Args:
            home_pct: Home win vote percentage (0-100 scale).
            draw_pct: Draw vote percentage (0-100 scale).
            away_pct: Away win vote percentage (0-100 scale).

        Returns:
            Tuple of (home_prob, draw_prob, away_prob) summing to ~1.0.
        """
        total_pct = home_pct + draw_pct + away_pct

        if total_pct <= 0:
            return (1.0 / 3, 1.0 / 3, 1.0 / 3)

        # Normalize to raw probabilities
        raw_home = home_pct / total_pct
        raw_draw = draw_pct / total_pct
        raw_away = away_pct / total_pct

        # In toto, the payout ratio is approximately 50%, meaning the
        # "overround" embedded in public votes is about 1/0.50 = 2.0.
        # The raw vote shares already sum to 1.0 after normalization,
        # so they ARE the implied probabilities from the crowd's
        # perspective. No further overround correction is needed on
        # the probability side; the overround affects payout, not the
        # probability interpretation.
        #
        # However, we know the crowd tends to be biased. We return the
        # raw normalized shares as the "implied" probabilities that
        # reflect the crowd's consensus view.
        return (raw_home, raw_draw, raw_away)

    def _detect_biases(
        self,
        match_data: MatchData,
        implied_probs: tuple[float, float, float],
        model_probs: tuple[float, float, float],
    ) -> list[str]:
        """Detect systematic biases in public voting patterns.

        Args:
            match_data: Match data with team stats.
            implied_probs: Implied probabilities from vote distribution.
            model_probs: Model-derived probabilities.

        Returns:
            List of detected bias labels.
        """
        biases: list[str] = []
        impl_home, impl_draw, impl_away = implied_probs
        model_home, model_draw, model_away = model_probs

        # 1. Popularity bias: strong team (higher Elo / higher rank)
        #    is over-voted relative to model assessment.
        home_rank = match_data.home_season_stats.rank
        away_rank = match_data.away_season_stats.rank
        elo_diff = match_data.home_elo - match_data.away_elo

        if home_rank < away_rank and impl_home > model_home + 0.10:
            biases.append("popularity_bias")
        elif away_rank < home_rank and impl_away > model_away + 0.10:
            biases.append("popularity_bias")
        elif abs(elo_diff) > 100:
            # Higher-Elo team is being over-voted
            if elo_diff > 0 and impl_home > model_home + 0.10:
                biases.append("popularity_bias")
            elif elo_diff < 0 and impl_away > model_away + 0.10:
                biases.append("popularity_bias")

        # 2. Draw neglect: public votes draw < 20% but model says > 25%.
        if impl_draw < 0.20 and model_draw > 0.25:
            biases.append("draw_neglect")

        # 3. Recency bias: recent form is over-weighted in votes.
        #    Detected when vote distribution aligns too closely with
        #    recent form but diverges from season-long stats.
        home_recent_wr = self._recent_win_rate(match_data.home_recent)
        away_recent_wr = self._recent_win_rate(match_data.away_recent)
        home_season_wr = self._season_win_rate(match_data.home_season_stats)
        away_season_wr = self._season_win_rate(match_data.away_season_stats)

        # If recent form diverges significantly from season form,
        # and votes follow recent form, flag recency bias.
        home_form_gap = abs(home_recent_wr - home_season_wr)
        away_form_gap = abs(away_recent_wr - away_season_wr)

        if home_form_gap > 0.20 or away_form_gap > 0.20:
            # Check if implied probs lean toward recent form direction
            if (
                home_recent_wr > home_season_wr
                and impl_home > model_home + 0.08
            ):
                biases.append("recency_bias")
            elif (
                away_recent_wr > away_season_wr
                and impl_away > model_away + 0.08
            ):
                biases.append("recency_bias")

        return biases

    def _calc_value(self, model_prob: float, implied_prob: float) -> float:
        """Calculate value of a bet outcome.

        Args:
            model_prob: Model's estimated probability.
            implied_prob: Market/vote implied probability.

        Returns:
            Positive value indicates a value bet opportunity.
        """
        return model_prob - implied_prob

    def _get_model_probs(
        self, match: MatchData
    ) -> tuple[float, float, float]:
        """Derive model probabilities from Dixon-Coles ratings.

        Uses the attack/defense ratings and Elo stored in MatchData
        to produce outcome probabilities. This is a simplified proxy;
        the full Dixon-Coles engine runs separately.

        Args:
            match: Match data with ratings.

        Returns:
            Tuple of (home_prob, draw_prob, away_prob).
        """
        # Elo-based probability as a proxy
        elo_diff = match.home_elo - match.away_elo
        # Add home advantage (~100 Elo points)
        elo_diff += 100.0

        # Expected score from Elo formula
        expected_home = 1.0 / (1.0 + 10.0 ** (-elo_diff / 400.0))

        # Distribute into 3-way probabilities
        # Approximate draw probability from league average (~25%)
        # adjusted by how close the teams are in strength.
        strength_gap = abs(elo_diff) / 400.0
        draw_base = 0.26
        draw_prob = max(0.10, draw_base - strength_gap * 0.08)

        remaining = 1.0 - draw_prob
        home_prob = remaining * expected_home
        away_prob = remaining * (1.0 - expected_home)

        # Incorporate attack/defense ratings as correction
        home_strength = (
            match.home_attack_rating / match.away_defense_rating
        )
        away_strength = (
            match.away_attack_rating / match.home_defense_rating
        )

        total_strength = home_strength + away_strength
        if total_strength > 0:
            rating_home = home_strength / total_strength
            rating_away = away_strength / total_strength
        else:
            rating_home = 0.5
            rating_away = 0.5

        # Blend Elo-based and rating-based probabilities (70/30)
        final_home = home_prob * 0.7 + (rating_home * remaining) * 0.3
        final_away = away_prob * 0.7 + (rating_away * remaining) * 0.3
        final_draw = 1.0 - final_home - final_away

        # Ensure valid probability distribution
        final_draw = max(0.05, final_draw)
        total = final_home + final_draw + final_away
        return (final_home / total, final_draw / total, final_away / total)

    def _recent_win_rate(self, recent_matches: list[Any]) -> float:
        """Calculate win rate from recent matches.

        Args:
            recent_matches: List of RecentMatch objects.

        Returns:
            Win rate as a float in [0, 1].
        """
        if not recent_matches:
            return 0.5

        last_5 = recent_matches[:5]
        wins = 0
        for m in last_5:
            if m.result.value == "1" and m.home_or_away == "home":
                wins += 1
            elif m.result.value == "2" and m.home_or_away == "away":
                wins += 1
        return wins / len(last_5)

    def _season_win_rate(self, season_stats: Any) -> float:
        """Calculate win rate from season statistics.

        Args:
            season_stats: SeasonStats object.

        Returns:
            Win rate as a float in [0, 1].
        """
        if season_stats.played == 0:
            return 0.5
        return season_stats.wins / season_stats.played

    def _save(self, result: OddsAnalysis) -> None:
        """Save odds analysis to intermediate JSON file.

        Args:
            result: The OddsAnalysis to persist.
        """
        output_path = INTERMEDIATE_DIR / "odds_analysis.json"
        output_path.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        logger.info("Odds analysis saved to %s", output_path)
