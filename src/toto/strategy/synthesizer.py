"""Strategy synthesizer: integrates all analysis outputs into final predictions.

References:
- note連載「ChatGPTでtoto予想は当たるのか？」の多段パイプライン方式
- 楽天toto WINNERの3タイプ戦略（しっかりゾウ/バランスバード/ハンターライオン）
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path

from toto.config import (
    INTERMEDIATE_DIR,
    WEIGHT_BASE_MODEL,
    WEIGHT_CONDITION,
    WEIGHT_MARKET,
    WEIGHT_UPSET,
)
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    MatchPrediction,
    MatchResult,
    OddsAnalysis,
    PlanType,
    PurchasePick,
    PurchasePlan,
    Strategy,
    TotoType,
    UpsetAnalysis,
)

logger = logging.getLogger(__name__)


class StrategySynthesizer:
    """Combines all analysis outputs into final predictions and purchase plans.

    The synthesis follows a multi-stage pipeline:
    1. Load all intermediate analysis results
    2. Compute weighted final probabilities per match
    3. Generate 3 purchase plans (conservative/balanced/aggressive)
    """

    def synthesize(
        self,
        collected: CollectedData,
        condition: ConditionAnalysis,
        odds: OddsAnalysis,
        upset: UpsetAnalysis,
        budget: int = 1000,
    ) -> Strategy:
        """Synthesize all analyses into a final strategy.

        Args:
            collected: Raw match data.
            condition: Condition analysis results.
            odds: Odds and voting analysis results.
            upset: Upset detection results.
            budget: Budget in yen for purchase plans.

        Returns:
            Complete strategy with predictions and purchase plans.
        """
        predictions = self._compute_predictions(collected, condition, odds, upset)
        plans = self._generate_plans(predictions, budget)

        strategy = Strategy(
            toto_round=collected.toto_round,
            toto_type=collected.toto_type,
            predictions=predictions,
            plans=plans,
        )

        output_path = INTERMEDIATE_DIR / "strategy.json"
        output_path.write_text(
            strategy.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("Strategy saved to %s", output_path)

        return strategy

    def _compute_predictions(
        self,
        collected: CollectedData,
        condition: ConditionAnalysis,
        odds: OddsAnalysis,
        upset: UpsetAnalysis,
    ) -> list[MatchPrediction]:
        """Compute weighted final probabilities for each match."""
        predictions: list[MatchPrediction] = []

        condition_map = {c.match_number: c for c in condition.conditions}
        odds_map = {o.match_number: o for o in odds.odds}
        upset_map = {u.match_number: u for u in upset.upsets}

        for match in collected.matches:
            mn = match.match_number
            cond = condition_map.get(mn)
            odd = odds_map.get(mn)
            upst = upset_map.get(mn)

            base_probs = self._get_base_probs(odd)
            cond_adj = self._get_condition_adjustment(cond)
            market_probs = self._get_market_probs(odd)
            upset_probs = self._get_upset_probs(upst)

            final_h = (
                WEIGHT_BASE_MODEL * base_probs[0]
                + WEIGHT_CONDITION * (base_probs[0] + cond_adj[0])
                + WEIGHT_MARKET * market_probs[0]
                + WEIGHT_UPSET * upset_probs[0]
            )
            final_d = (
                WEIGHT_BASE_MODEL * base_probs[1]
                + WEIGHT_CONDITION * (base_probs[1] + cond_adj[1])
                + WEIGHT_MARKET * market_probs[1]
                + WEIGHT_UPSET * upset_probs[1]
            )
            final_a = (
                WEIGHT_BASE_MODEL * base_probs[2]
                + WEIGHT_CONDITION * (base_probs[2] + cond_adj[2])
                + WEIGHT_MARKET * market_probs[2]
                + WEIGHT_UPSET * upset_probs[2]
            )

            # Normalize to sum to 1.0
            total = final_h + final_d + final_a
            if total > 0:
                final_h /= total
                final_d /= total
                final_a /= total
            else:
                final_h, final_d, final_a = 1 / 3, 1 / 3, 1 / 3

            probs = {"1": final_h, "0": final_d, "2": final_a}
            best_pick = max(probs, key=probs.get)  # type: ignore[arg-type]
            confidence = probs[best_pick]

            is_upset = upst.is_upset_alert if upst else False

            reasoning_parts = []
            if confidence > 0.5:
                reasoning_parts.append(f"高信頼度({confidence:.0%})")
            if is_upset:
                reasoning_parts.append(f"波乱警戒(スコア={upst.upset_score})")  # type: ignore[union-attr]
            if odd and max(odd.value_home, odd.value_draw, odd.value_away) > 0.1:
                reasoning_parts.append("バリューベット検出")

            predictions.append(
                MatchPrediction(
                    match_number=mn,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    final_home_prob=round(final_h, 4),
                    final_draw_prob=round(final_d, 4),
                    final_away_prob=round(final_a, 4),
                    recommended_pick=MatchResult(best_pick),
                    confidence=round(confidence, 4),
                    upset_alert=is_upset,
                    reasoning="、".join(reasoning_parts) if reasoning_parts else "",
                )
            )

        return predictions

    def _get_base_probs(self, odd: object | None) -> tuple[float, float, float]:
        """Extract model probabilities from odds analysis."""
        if odd and hasattr(odd, "model_home_prob") and odd.model_home_prob > 0:
            return (odd.model_home_prob, odd.model_draw_prob, odd.model_away_prob)
        return (1 / 3, 1 / 3, 1 / 3)

    def _get_condition_adjustment(
        self, cond: object | None
    ) -> tuple[float, float, float]:
        """Convert condition factors to probability adjustments."""
        if not cond or not hasattr(cond, "total_home_adjustment"):
            return (0.0, 0.0, 0.0)
        home_adj = cond.total_home_adjustment * 0.1
        away_adj = cond.total_away_adjustment * 0.1
        return (home_adj, -(home_adj + away_adj) / 2, away_adj)

    def _get_market_probs(self, odd: object | None) -> tuple[float, float, float]:
        """Get implied probabilities from market/voting data."""
        if odd and hasattr(odd, "implied_home_prob") and odd.implied_home_prob > 0:
            return (odd.implied_home_prob, odd.implied_draw_prob, odd.implied_away_prob)
        return (1 / 3, 1 / 3, 1 / 3)

    def _get_upset_probs(self, upst: object | None) -> tuple[float, float, float]:
        """Get upset-adjusted probabilities."""
        if upst and hasattr(upst, "adjusted_home_prob") and upst.adjusted_home_prob > 0:
            return (
                upst.adjusted_home_prob,
                upst.adjusted_draw_prob,
                upst.adjusted_away_prob,
            )
        return (1 / 3, 1 / 3, 1 / 3)

    def _generate_plans(
        self, predictions: list[MatchPrediction], budget: int
    ) -> list[PurchasePlan]:
        """Generate 3 purchase plans based on predictions.

        Conservative: single picks for all, double only on high upset scores.
        Balanced: double on medium+ upset scores.
        Aggressive: double on low+ upset, triple on high upset.
        """
        cost_per_unit = 100  # 1口100円

        return [
            self._conservative_plan(predictions, budget, cost_per_unit),
            self._balanced_plan(predictions, budget, cost_per_unit),
            self._aggressive_plan(predictions, budget, cost_per_unit),
        ]

    def _conservative_plan(
        self,
        predictions: list[MatchPrediction],
        budget: int,
        unit_cost: int,
    ) -> PurchasePlan:
        """しっかりゾウ: prioritize hit rate over payout."""
        picks: list[PurchasePick] = []
        for pred in predictions:
            if pred.upset_alert and pred.confidence < 0.5:
                # Double: best pick + second best
                sorted_probs = sorted(
                    [
                        (pred.final_home_prob, MatchResult.HOME_WIN),
                        (pred.final_draw_prob, MatchResult.DRAW),
                        (pred.final_away_prob, MatchResult.AWAY_WIN),
                    ],
                    reverse=True,
                )
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[sorted_probs[0][1], sorted_probs[1][1]],
                    )
                )
            else:
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[pred.recommended_pick],
                    )
                )

        total_combos = math.prod(len(p.picks) for p in picks)
        cost = total_combos * unit_cost

        return PurchasePlan(
            name=PlanType.CONSERVATIVE,
            display_name="しっかりゾウ（コンサバ）",
            picks=picks,
            total_combinations=total_combos,
            cost_yen=cost,
            estimated_hit_rate=self._estimate_hit_rate(predictions, picks),
            description="的中確率を最優先。波乱警戒試合のみダブル選択。",
        )

    def _balanced_plan(
        self,
        predictions: list[MatchPrediction],
        budget: int,
        unit_cost: int,
    ) -> PurchasePlan:
        """バランスバード: balance hit rate and payout."""
        picks: list[PurchasePick] = []
        for pred in predictions:
            if pred.confidence < 0.45:
                sorted_probs = sorted(
                    [
                        (pred.final_home_prob, MatchResult.HOME_WIN),
                        (pred.final_draw_prob, MatchResult.DRAW),
                        (pred.final_away_prob, MatchResult.AWAY_WIN),
                    ],
                    reverse=True,
                )
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[sorted_probs[0][1], sorted_probs[1][1]],
                    )
                )
            else:
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[pred.recommended_pick],
                    )
                )

        total_combos = math.prod(len(p.picks) for p in picks)
        cost = total_combos * unit_cost

        return PurchasePlan(
            name=PlanType.BALANCED,
            display_name="バランスバード（バランス）",
            picks=picks,
            total_combinations=total_combos,
            cost_yen=cost,
            estimated_hit_rate=self._estimate_hit_rate(predictions, picks),
            description="的中確率と配当のバランスを最適化。信頼度45%未満でダブル。",
        )

    def _aggressive_plan(
        self,
        predictions: list[MatchPrediction],
        budget: int,
        unit_cost: int,
    ) -> PurchasePlan:
        """ハンターライオン: chase high payouts."""
        picks: list[PurchasePick] = []
        for pred in predictions:
            if pred.upset_alert and pred.confidence < 0.4:
                # Triple: all three outcomes
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[
                            MatchResult.HOME_WIN,
                            MatchResult.DRAW,
                            MatchResult.AWAY_WIN,
                        ],
                    )
                )
            elif pred.confidence < 0.5:
                sorted_probs = sorted(
                    [
                        (pred.final_home_prob, MatchResult.HOME_WIN),
                        (pred.final_draw_prob, MatchResult.DRAW),
                        (pred.final_away_prob, MatchResult.AWAY_WIN),
                    ],
                    reverse=True,
                )
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[sorted_probs[0][1], sorted_probs[1][1]],
                    )
                )
            else:
                picks.append(
                    PurchasePick(
                        match_number=pred.match_number,
                        picks=[pred.recommended_pick],
                    )
                )

        total_combos = math.prod(len(p.picks) for p in picks)
        cost = total_combos * unit_cost

        return PurchasePlan(
            name=PlanType.AGGRESSIVE,
            display_name="ハンターライオン（アグレッシブ）",
            picks=picks,
            total_combinations=total_combos,
            cost_yen=cost,
            estimated_hit_rate=self._estimate_hit_rate(predictions, picks),
            description="高配当を積極的に狙う。波乱警戒試合でトリプル、不確実試合でダブル。",
        )

    def _estimate_hit_rate(
        self,
        predictions: list[MatchPrediction],
        picks: list[PurchasePick],
    ) -> float:
        """Estimate the probability of hitting all matches in a plan."""
        prob = 1.0
        pred_map = {p.match_number: p for p in predictions}
        for pick in picks:
            pred = pred_map.get(pick.match_number)
            if not pred:
                prob *= 1 / 3
                continue
            match_prob = 0.0
            for result in pick.picks:
                if result == MatchResult.HOME_WIN:
                    match_prob += pred.final_home_prob
                elif result == MatchResult.DRAW:
                    match_prob += pred.final_draw_prob
                elif result == MatchResult.AWAY_WIN:
                    match_prob += pred.final_away_prob
            prob *= match_prob
        return round(prob, 8)

    @classmethod
    def from_intermediate_files(cls, toto_round: int, budget: int = 1000) -> Strategy:
        """Load all intermediate files and synthesize.

        Args:
            toto_round: The toto round number.
            budget: Budget in yen.

        Returns:
            Complete strategy.
        """
        collected = CollectedData.model_validate_json(
            (INTERMEDIATE_DIR / "collected_data.json").read_text(encoding="utf-8")
        )
        condition = ConditionAnalysis.model_validate_json(
            (INTERMEDIATE_DIR / "condition_analysis.json").read_text(encoding="utf-8")
        )
        odds_data = OddsAnalysis.model_validate_json(
            (INTERMEDIATE_DIR / "odds_analysis.json").read_text(encoding="utf-8")
        )
        upset_data = UpsetAnalysis.model_validate_json(
            (INTERMEDIATE_DIR / "upset_analysis.json").read_text(encoding="utf-8")
        )

        synthesizer = cls()
        return synthesizer.synthesize(collected, condition, odds_data, upset_data, budget)
