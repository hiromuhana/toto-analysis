"""Integration tests for the toto prediction pipeline."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from toto.analyzers.condition import ConditionAnalyzer
from toto.analyzers.odds import OddsAnalyzer
from toto.analyzers.upset import UpsetDetector
from toto.collectors.mock import MockCollector
from toto.config import INTERMEDIATE_DIR
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    MatchResult,
    OddsAnalysis,
    PlanType,
    Strategy,
    TotoType,
    UpsetAnalysis,
)
from toto.output.report import generate_report
from toto.strategy.synthesizer import StrategySynthesizer


@pytest.fixture
def collected_data() -> CollectedData:
    """Generate mock collected data for testing."""
    collector = MockCollector()
    return asyncio.get_event_loop().run_until_complete(
        collector.collect(1620, toto_type=TotoType.TOTO)
    )


@pytest.fixture
def condition_analysis(collected_data: CollectedData) -> ConditionAnalysis:
    analyzer = ConditionAnalyzer()
    return analyzer.analyze(collected_data)


@pytest.fixture
def odds_analysis(collected_data: CollectedData) -> OddsAnalysis:
    analyzer = OddsAnalyzer()
    return analyzer.analyze(collected_data)


@pytest.fixture
def upset_analysis(
    collected_data: CollectedData,
    condition_analysis: ConditionAnalysis,
    odds_analysis: OddsAnalysis,
) -> UpsetAnalysis:
    detector = UpsetDetector()
    return detector.analyze(collected_data, condition_analysis, odds_analysis)


class TestMockCollector:
    def test_generates_13_matches_for_toto(self, collected_data: CollectedData) -> None:
        assert len(collected_data.matches) == 13

    def test_uses_real_team_names(self, collected_data: CollectedData) -> None:
        for match in collected_data.matches:
            assert match.home_team != ""
            assert match.away_team != ""
            assert match.home_team != match.away_team

    def test_season_stats_are_plausible(self, collected_data: CollectedData) -> None:
        for match in collected_data.matches:
            stats = match.home_season_stats
            assert stats.played > 0
            assert stats.wins + stats.draws + stats.losses == stats.played
            assert stats.points == stats.wins * 3 + stats.draws

    def test_elo_ratings_in_range(self, collected_data: CollectedData) -> None:
        for match in collected_data.matches:
            assert 1200.0 <= match.home_elo <= 1800.0
            assert 1200.0 <= match.away_elo <= 1800.0

    def test_minitoto_generates_5_matches(self) -> None:
        collector = MockCollector()
        data = asyncio.get_event_loop().run_until_complete(
            collector.collect(1620, toto_type=TotoType.MINI_TOTO)
        )
        assert len(data.matches) == 5


class TestConditionAnalyzer:
    def test_produces_conditions_for_all_matches(
        self, collected_data: CollectedData, condition_analysis: ConditionAnalysis
    ) -> None:
        assert len(condition_analysis.conditions) == len(collected_data.matches)

    def test_factors_in_range(self, condition_analysis: ConditionAnalysis) -> None:
        for cond in condition_analysis.conditions:
            assert -1.0 <= cond.fatigue_home <= 1.0
            assert -1.0 <= cond.fatigue_away <= 1.0
            assert -1.0 <= cond.momentum_home <= 1.0
            assert -1.0 <= cond.momentum_away <= 1.0
            assert -1.0 <= cond.venue_advantage <= 1.0

    def test_home_has_positive_venue_advantage(
        self, condition_analysis: ConditionAnalysis
    ) -> None:
        for cond in condition_analysis.conditions:
            assert cond.venue_advantage > 0.0


class TestOddsAnalyzer:
    def test_produces_odds_for_all_matches(
        self, collected_data: CollectedData, odds_analysis: OddsAnalysis
    ) -> None:
        assert len(odds_analysis.odds) == len(collected_data.matches)

    def test_model_probs_sum_to_one(self, odds_analysis: OddsAnalysis) -> None:
        for odd in odds_analysis.odds:
            total = odd.model_home_prob + odd.model_draw_prob + odd.model_away_prob
            assert abs(total - 1.0) < 0.01


class TestUpsetDetector:
    def test_produces_upsets_for_all_matches(
        self, collected_data: CollectedData, upset_analysis: UpsetAnalysis
    ) -> None:
        assert len(upset_analysis.upsets) == len(collected_data.matches)

    def test_upset_score_in_range(self, upset_analysis: UpsetAnalysis) -> None:
        for upset in upset_analysis.upsets:
            assert 0 <= upset.upset_score <= 100

    def test_adjusted_probs_sum_to_one(self, upset_analysis: UpsetAnalysis) -> None:
        for upset in upset_analysis.upsets:
            total = (
                upset.adjusted_home_prob
                + upset.adjusted_draw_prob
                + upset.adjusted_away_prob
            )
            assert abs(total - 1.0) < 0.01


class TestStrategySynthesizer:
    def test_produces_predictions_and_plans(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        assert len(strategy.predictions) == len(collected_data.matches)
        assert len(strategy.plans) == 3

    def test_plan_types(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        plan_names = {p.name for p in strategy.plans}
        assert PlanType.CONSERVATIVE in plan_names
        assert PlanType.BALANCED in plan_names
        assert PlanType.AGGRESSIVE in plan_names

    def test_predictions_probs_sum_to_one(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        for pred in strategy.predictions:
            total = pred.final_home_prob + pred.final_draw_prob + pred.final_away_prob
            assert abs(total - 1.0) < 0.01

    def test_recommended_pick_is_valid(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        for pred in strategy.predictions:
            assert pred.recommended_pick in (
                MatchResult.HOME_WIN,
                MatchResult.DRAW,
                MatchResult.AWAY_WIN,
            )


class TestReportGeneration:
    def test_generates_report_file(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        report_path = generate_report(strategy)
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "第1620回" in content
        assert "免責事項" in content
        assert "コピペ用" in content


class TestSchemas:
    def test_collected_data_serialization(self, collected_data: CollectedData) -> None:
        json_str = collected_data.model_dump_json()
        restored = CollectedData.model_validate_json(json_str)
        assert len(restored.matches) == len(collected_data.matches)

    def test_strategy_serialization(
        self,
        collected_data: CollectedData,
        condition_analysis: ConditionAnalysis,
        odds_analysis: OddsAnalysis,
        upset_analysis: UpsetAnalysis,
    ) -> None:
        synthesizer = StrategySynthesizer()
        strategy = synthesizer.synthesize(
            collected_data, condition_analysis, odds_analysis, upset_analysis
        )
        json_str = strategy.model_dump_json()
        restored = Strategy.model_validate_json(json_str)
        assert len(restored.predictions) == len(strategy.predictions)
        assert len(restored.plans) == len(strategy.plans)
