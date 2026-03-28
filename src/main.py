"""toto AI prediction system entry point.

Usage:
    conda activate toto-ai
    python src/main.py --round 1620 --budget 1000 --type toto
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from toto.config import INTERMEDIATE_DIR, LOG_LEVEL
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    OddsAnalysis,
    TotoType,
    UpsetAnalysis,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("toto")


async def run_phase_a(toto_round: int, toto_type: TotoType) -> tuple[
    CollectedData, ConditionAnalysis, OddsAnalysis
]:
    """Phase A: Run data-collector, condition-analyzer, odds-analyzer in parallel."""
    from toto.analyzers.condition import ConditionAnalyzer
    from toto.analyzers.odds import OddsAnalyzer
    from toto.collectors.mock import MockCollector

    logger.info("=== Phase A: Data Collection & Analysis (parallel) ===")

    # --- Step 1: Get match list + voting from toto official & totomo ---
    from toto.collectors.toto_official import TotoOfficialCollector
    from toto.collectors.totomo import TotomoCollector
    from toto.models.schemas import MatchData, SeasonStats

    collected: CollectedData | None = None
    vote_data: dict | None = None

    # Fetch match list and voting data in parallel
    toto_collector = TotoOfficialCollector()
    totomo_collector = TotomoCollector()

    try:
        toto_result, totomo_result = await asyncio.gather(
            toto_collector.collect(toto_round),
            totomo_collector.collect(toto_round),
        )
    except Exception as e:
        logger.warning("Collectors failed (%s)", e)
        toto_result = {"matches": [], "votes": []}
        totomo_result = {"votes": []}

    # Use toto official for match list
    official_matches = toto_result.get("matches", [])

    # Prefer totomo for voting data (more reliable), fallback to toto official
    totomo_votes = totomo_result.get("votes", [])
    official_votes = toto_result.get("votes", [])
    vote_list = totomo_votes if totomo_votes else official_votes
    if vote_list:
        vote_data = {"votes": vote_list, "toto_round": toto_round}
        logger.info("Voting data: %d matches from %s",
                     len(vote_list), "toto.cam" if totomo_votes else "toto-dream.com")

    # --- Step 2: Build CollectedData from match list ---
    if official_matches:
        match_data_list = []
        for m in official_matches:
            # Create basic MatchData with placeholder stats
            # (Real stats would come from football-lab.jp or data.j-league.or.jp)
            default_stats = SeasonStats(
                played=0, wins=0, draws=0, losses=0,
                goals_for=0, goals_against=0, points=0, rank=0,
            )
            match_data_list.append(MatchData(
                match_number=m["match_number"],
                home_team=m["home_team"],
                away_team=m["away_team"],
                stadium=m.get("stadium", ""),
                match_date=m.get("match_date", ""),
                home_season_stats=default_stats,
                away_season_stats=default_stats,
            ))

        collected = CollectedData(
            toto_round=toto_round,
            toto_type=toto_type,
            matches=match_data_list,
            data_sources=["toto-dream.com"],
        )
        logger.info("[Real] Got %d matches from toto-dream.com", len(match_data_list))

    # --- Step 3: Fall back to mock if no real matches ---
    if collected is None or not collected.matches:
        logger.info("Using mock data (real collectors returned no matches)")
        mock = MockCollector()
        collected = await mock.collect(toto_round, toto_type=toto_type)
        vote_data = None

    # Save collected data
    (INTERMEDIATE_DIR / "collected_data.json").write_text(
        collected.model_dump_json(indent=2), encoding="utf-8"
    )
    logger.info("Collected data: %d matches", len(collected.matches))

    # Run condition and odds analysis in parallel
    condition_analyzer = ConditionAnalyzer()
    odds_analyzer = OddsAnalyzer()

    condition = condition_analyzer.analyze(collected)
    odds = odds_analyzer.analyze(collected, vote_data)

    # Save intermediate results
    (INTERMEDIATE_DIR / "condition_analysis.json").write_text(
        condition.model_dump_json(indent=2), encoding="utf-8"
    )
    (INTERMEDIATE_DIR / "odds_analysis.json").write_text(
        odds.model_dump_json(indent=2), encoding="utf-8"
    )

    logger.info("Phase A complete")
    return collected, condition, odds


def run_phase_b(
    collected: CollectedData,
    condition: ConditionAnalysis,
    odds: OddsAnalysis,
) -> UpsetAnalysis:
    """Phase B: Run upset-detector (sequential, depends on Phase A)."""
    from toto.analyzers.upset import UpsetDetector

    logger.info("=== Phase B: Upset Detection (sequential) ===")

    detector = UpsetDetector()
    upset = detector.analyze(collected, condition, odds)

    (INTERMEDIATE_DIR / "upset_analysis.json").write_text(
        upset.model_dump_json(indent=2), encoding="utf-8"
    )

    upset_count = sum(1 for u in upset.upsets if u.is_upset_alert)
    logger.info("Phase B complete: %d upset alerts", upset_count)
    return upset


def run_phase_c(
    collected: CollectedData,
    condition: ConditionAnalysis,
    odds: OddsAnalysis,
    upset: UpsetAnalysis,
    budget: int,
) -> Path:
    """Phase C: Run strategy-synthesizer (sequential, depends on Phase B)."""
    from toto.output.report import generate_report
    from toto.strategy.synthesizer import StrategySynthesizer

    logger.info("=== Phase C: Strategy Synthesis (sequential) ===")

    synthesizer = StrategySynthesizer()
    strategy = synthesizer.synthesize(collected, condition, odds, upset, budget)

    report_path = generate_report(strategy)
    logger.info("Phase C complete: report at %s", report_path)

    return report_path


async def main(toto_round: int, budget: int, toto_type: TotoType) -> None:
    """Run the full prediction pipeline."""
    logger.info("toto AI prediction system starting")
    logger.info("Round: %d, Budget: %d yen, Type: %s", toto_round, budget, toto_type.value)

    # Phase A (parallel)
    collected, condition, odds = await run_phase_a(toto_round, toto_type)

    # Phase B (sequential)
    upset = run_phase_b(collected, condition, odds)

    # Phase C (sequential)
    report_path = run_phase_c(collected, condition, odds, upset, budget)

    logger.info("Pipeline complete!")
    logger.info("Report: %s", report_path)

    # Print summary to stdout
    print(f"\n{'='*60}")
    print(f"toto 第{toto_round}回 予測完了")
    print(f"レポート: {report_path}")
    print(f"中間データ: {INTERMEDIATE_DIR}")
    print(f"{'='*60}\n")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="toto AI prediction system",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--round", "-r",
        type=int,
        required=True,
        help="toto round number (e.g., 1620)",
    )
    parser.add_argument(
        "--budget", "-b",
        type=int,
        default=1000,
        help="Budget in yen",
    )
    parser.add_argument(
        "--type", "-t",
        choices=["toto", "minitoto"],
        default="toto",
        help="Lottery type",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    toto_type = TotoType.TOTO if args.type == "toto" else TotoType.MINI_TOTO
    asyncio.run(main(args.round, args.budget, toto_type))
