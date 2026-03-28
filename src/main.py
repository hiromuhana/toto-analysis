"""toto AI prediction system entry point.

Usage:
    conda activate toto-ai
    python src/main.py --round 1619 --budget 4000 --type toto
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from toto.config import INTERMEDIATE_DIR, LOG_LEVEL
from toto.models.schemas import (
    CollectedData,
    ConditionAnalysis,
    MatchData,
    MatchResult,
    OddsAnalysis,
    RecentMatch,
    SeasonStats,
    TotoType,
    UpsetAnalysis,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("toto")


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------

async def run_phase_a(toto_round: int, toto_type: TotoType) -> tuple[
    CollectedData, ConditionAnalysis, OddsAnalysis
]:
    """Phase A: collect data, train model, analyze condition & odds."""
    from toto.analyzers.condition import ConditionAnalyzer
    from toto.analyzers.odds import OddsAnalyzer
    from toto.collectors.mock import MockCollector

    logger.info("=== Phase A: Data Collection & Model Training ===")

    # --- Step 1: match list + voting (async) ---
    match_list, vote_map = await _collect_matches_and_votes(toto_round)

    # --- Step 2: team stats (async) ---
    from toto.collectors.jleague import load_team_stats
    team_stats = await load_team_stats(year=2025)

    # --- Step 3: historical match results + Dixon-Coles (sync, Selenium) ---
    dc_predictions = _train_and_predict(match_list)

    # --- Step 4: build CollectedData with recent/H2H ---
    collected = _build_collected_data(
        toto_round, toto_type, match_list, team_stats, dc_predictions,
    )

    if not collected.matches:
        logger.info("No real matches. Falling back to mock data.")
        mock = MockCollector()
        collected = await mock.collect(toto_round, toto_type=toto_type)

    _save_json(collected, "collected_data.json")
    logger.info("Collected: %d matches, %d with DC predictions",
                len(collected.matches),
                sum(1 for m in collected.matches if m.home_attack_rating != 1.0))

    # --- Step 5: condition & odds analysis ---
    condition = ConditionAnalyzer().analyze(collected)
    _save_json(condition, "condition_analysis.json")

    odds = OddsAnalyzer().analyze(collected, vote_map, dc_predictions)
    _save_json(odds, "odds_analysis.json")

    logger.info("Phase A complete")
    return collected, condition, odds


async def _collect_matches_and_votes(
    toto_round: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, float]]]:
    """Fetch match list and voting data from toto sites."""
    from toto.collectors.toto_official import TotoOfficialCollector
    from toto.collectors.totomo import TotomoCollector

    try:
        toto_result, totomo_result = await asyncio.gather(
            TotoOfficialCollector().collect(toto_round),
            TotomoCollector().collect(toto_round),
        )
    except Exception as e:
        logger.warning("Collectors failed: %s", e)
        return [], {}

    match_list: list[dict[str, Any]] = toto_result.get("matches", [])

    # Build vote_map: {match_number: {"home": %, "draw": %, "away": %}}
    raw_votes = totomo_result.get("votes", []) or toto_result.get("votes", [])
    vote_map: dict[int, dict[str, float]] = {}
    for v in raw_votes:
        mn = v.get("match_number", 0)
        if mn > 0:
            vote_map[mn] = {
                "home": v.get("home_vote_pct", 33.33),
                "draw": v.get("draw_vote_pct", 33.33),
                "away": v.get("away_vote_pct", 33.33),
            }

    logger.info("Match list: %d, Voting data: %d matches",
                len(match_list), len(vote_map))
    return match_list, vote_map


def _train_and_predict(
    match_list: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Train Dixon-Coles on historical data and predict current matches.

    Returns: {"home_vs_away": {"home": prob, "draw": prob, "away": prob}}
    """
    try:
        from toto.collectors.jleague_selenium import (
            build_training_dataframe,
            fetch_match_results,
        )
        import penaltyblog as pb
        from toto.collectors.jleague import TOTO_TO_OFFICIAL
    except ImportError as e:
        logger.warning("Selenium/penaltyblog not available: %s", e)
        return {}

    logger.info("--- Training Dixon-Coles model (Selenium) ---")

    # Fetch J1+J2+J3 match results
    results = fetch_match_results(year=2025, leagues=["J1", "J2", "J3"])
    df = build_training_dataframe(results)
    if df.empty:
        logger.warning("No match data for training")
        return {}

    logger.info("Training data: %d matches, %d teams",
                len(df), df["team_home"].nunique())

    # Train Dixon-Coles
    weights = pb.models.dixon_coles_weights(df["date"], xi=0.001)
    model = pb.models.DixonColesGoalModel(
        df["goals_home"], df["goals_away"],
        df["team_home"], df["team_away"],
        weights,
    )
    model.fit()
    logger.info("Dixon-Coles model trained")

    # Predict each match
    known_teams = set(df["team_home"].unique()) | set(df["team_away"].unique())
    predictions: dict[str, dict[str, float]] = {}

    for m in match_list:
        h_toto = m["home_team"]
        a_toto = m["away_team"]
        h = _find_team(h_toto, known_teams, TOTO_TO_OFFICIAL)
        a = _find_team(a_toto, known_teams, TOTO_TO_OFFICIAL)

        if h and a:
            try:
                pred = model.predict(h, a)
                p = pred.home_draw_away
                key = f"{h_toto}_vs_{a_toto}"
                predictions[key] = {"home": p[0], "draw": p[1], "away": p[2]}
                logger.info("  DC: %s vs %s → H=%.1f%% D=%.1f%% A=%.1f%%",
                            h_toto, a_toto, p[0]*100, p[1]*100, p[2]*100)
            except Exception as e:
                logger.warning("  DC predict failed for %s vs %s: %s", h_toto, a_toto, e)
        else:
            logger.warning("  DC: team not found: %s(%s) vs %s(%s)",
                           h_toto, h, a_toto, a)

    return predictions


def _find_team(
    toto_name: str,
    known_teams: set[str],
    mapping: dict[str, str],
) -> str | None:
    """Resolve toto short name to data.j-league.or.jp name."""
    if toto_name in known_teams:
        return toto_name
    official = mapping.get(toto_name, "")
    if official in known_teams:
        return official
    for k in known_teams:
        if official and (official in k or k in official):
            return k
        if toto_name in k or k in toto_name:
            return k
    return None


def _build_collected_data(
    toto_round: int,
    toto_type: TotoType,
    match_list: list[dict[str, Any]],
    team_stats: dict[str, dict[str, Any]],
    dc_predictions: dict[str, dict[str, float]],
) -> CollectedData:
    """Build CollectedData from match list, stats, and DC predictions."""
    matches: list[MatchData] = []

    # Try to load recent match data from Selenium cache
    recent_data = _load_recent_data()

    for m in match_list:
        home = m["home_team"]
        away = m["away_team"]
        h_stats = team_stats.get(home)
        a_stats = team_stats.get(away)

        home_season = _build_season_stats(h_stats)
        away_season = _build_season_stats(a_stats)

        # Elo from stats rank
        h_elo = 1500 + (10 - h_stats["rank"]) * 15 if h_stats else 1500.0
        a_elo = 1500 + (10 - a_stats["rank"]) * 15 if a_stats else 1500.0

        # Attack/defense from DC or stats
        dc_key = f"{home}_vs_{away}"
        dc = dc_predictions.get(dc_key)
        if h_stats and h_stats.get("played", 0) > 0:
            h_atk = round(h_stats["goals_for"] / h_stats["played"] / 1.3, 3)
            h_def = round(h_stats["goals_against"] / h_stats["played"] / 1.3, 3)
        else:
            h_atk, h_def = 1.0, 1.0
        if a_stats and a_stats.get("played", 0) > 0:
            a_atk = round(a_stats["goals_for"] / a_stats["played"] / 1.3, 3)
            a_def = round(a_stats["goals_against"] / a_stats["played"] / 1.3, 3)
        else:
            a_atk, a_def = 1.0, 1.0

        # Recent matches and H2H
        home_recent = _get_recent(recent_data, home)
        away_recent = _get_recent(recent_data, away)
        h2h = _get_h2h(recent_data, home, away)

        matches.append(MatchData(
            match_number=m["match_number"],
            home_team=home,
            away_team=away,
            stadium=m.get("stadium", ""),
            match_date=m.get("match_date", ""),
            home_season_stats=home_season,
            away_season_stats=away_season,
            home_elo=h_elo, away_elo=a_elo,
            home_attack_rating=h_atk, away_attack_rating=a_atk,
            home_defense_rating=h_def, away_defense_rating=a_def,
            home_recent=home_recent, away_recent=away_recent,
            h2h=h2h,
        ))

    sources = ["toto-dream.com", "data.j-league.or.jp"]
    if dc_predictions:
        sources.append("Dixon-Coles (penaltyblog)")
    if recent_data:
        sources.append("Selenium (recent matches)")

    return CollectedData(
        toto_round=toto_round, toto_type=toto_type,
        matches=matches, data_sources=sources,
    )


def _build_season_stats(stats: dict[str, Any] | None) -> SeasonStats:
    if not stats:
        return SeasonStats(played=0, wins=0, draws=0, losses=0,
                           goals_for=0, goals_against=0, points=0, rank=0)
    return SeasonStats(
        played=stats["played"], wins=stats["wins"],
        draws=stats["draws"], losses=stats["losses"],
        goals_for=stats["goals_for"], goals_against=stats["goals_against"],
        points=stats["points"], rank=stats["rank"],
    )


def _load_recent_data() -> list[dict[str, Any]]:
    """Load all match results from Selenium cache for recent/H2H lookups."""
    from toto.utils import cache as cache_util
    cached = cache_util.get("match_results_2025_J1_J2_J3")
    if not cached:
        return []
    all_matches: list[dict[str, Any]] = []
    for league_matches in cached.values():
        if isinstance(league_matches, list):
            all_matches.extend(league_matches)
    return all_matches


def _get_recent(
    all_matches: list[dict[str, Any]], team: str, n: int = 5
) -> list[RecentMatch]:
    """Get recent N matches for a team from cached data."""
    if not all_matches:
        return []
    from toto.collectors.jleague import TOTO_TO_OFFICIAL
    from toto.collectors.jleague_selenium import get_team_recent_matches

    # Try both toto name and official name
    names_to_try = [team]
    if team in TOTO_TO_OFFICIAL:
        names_to_try.append(TOTO_TO_OFFICIAL[team])

    for name in names_to_try:
        recent_raw = get_team_recent_matches(all_matches, name, n)
        if recent_raw:
            return [
                RecentMatch(
                    date=r["date"], opponent=r["opponent"],
                    home_or_away=r["home_or_away"],
                    goals_for=r["goals_for"], goals_against=r["goals_against"],
                    result=MatchResult(r["result"]),
                )
                for r in recent_raw
            ]
    return []


def _get_h2h(
    all_matches: list[dict[str, Any]], home: str, away: str, n: int = 5
) -> list[RecentMatch]:
    """Get H2H matches between two teams."""
    if not all_matches:
        return []
    from toto.collectors.jleague import TOTO_TO_OFFICIAL
    from toto.collectors.jleague_selenium import get_h2h_matches

    h_names = [home] + ([TOTO_TO_OFFICIAL[home]] if home in TOTO_TO_OFFICIAL else [])
    a_names = [away] + ([TOTO_TO_OFFICIAL[away]] if away in TOTO_TO_OFFICIAL else [])

    for h in h_names:
        for a in a_names:
            h2h_raw = get_h2h_matches(all_matches, h, a, n)
            if h2h_raw:
                return [
                    RecentMatch(
                        date=r["date"], opponent=r["opponent"],
                        home_or_away=r["home_or_away"],
                        goals_for=r["goals_for"], goals_against=r["goals_against"],
                        result=MatchResult(r["result"]),
                    )
                    for r in h2h_raw
                ]
    return []


def _save_json(obj: Any, filename: str) -> None:
    path = INTERMEDIATE_DIR / filename
    path.write_text(obj.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase B & C (unchanged logic, cleaner code)
# ---------------------------------------------------------------------------

def run_phase_b(
    collected: CollectedData,
    condition: ConditionAnalysis,
    odds: OddsAnalysis,
) -> UpsetAnalysis:
    """Phase B: Upset detection."""
    from toto.analyzers.upset import UpsetDetector

    logger.info("=== Phase B: Upset Detection ===")
    upset = UpsetDetector().analyze(collected, condition, odds)
    _save_json(upset, "upset_analysis.json")

    alerts = sum(1 for u in upset.upsets if u.is_upset_alert)
    logger.info("Phase B complete: %d upset alerts out of %d matches",
                alerts, len(upset.upsets))
    return upset


def run_phase_c(
    collected: CollectedData,
    condition: ConditionAnalysis,
    odds: OddsAnalysis,
    upset: UpsetAnalysis,
    budget: int,
) -> Path:
    """Phase C: Strategy synthesis & report."""
    from toto.output.report import generate_report
    from toto.strategy.synthesizer import StrategySynthesizer

    logger.info("=== Phase C: Strategy Synthesis ===")
    strategy = StrategySynthesizer().synthesize(
        collected, condition, odds, upset, budget,
    )
    report_path = generate_report(strategy)
    logger.info("Phase C complete: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(toto_round: int, budget: int, toto_type: TotoType) -> None:
    """Run the full prediction pipeline."""
    logger.info("toto AI prediction system v2")
    logger.info("Round: %d | Budget: %d yen | Type: %s",
                toto_round, budget, toto_type.value)

    collected, condition, odds = await run_phase_a(toto_round, toto_type)
    upset = run_phase_b(collected, condition, odds)
    report_path = run_phase_c(collected, condition, odds, upset, budget)

    print(f"\n{'='*60}")
    print(f"toto 第{toto_round}回 予測完了")
    print(f"レポート: {report_path}")
    print(f"中間データ: {INTERMEDIATE_DIR}")
    print(f"{'='*60}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="toto AI prediction system")
    parser.add_argument("--round", "-r", type=int, required=True)
    parser.add_argument("--budget", "-b", type=int, default=1000)
    parser.add_argument("--type", "-t", choices=["toto", "minitoto"], default="toto")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    toto_type = TotoType.TOTO if args.type == "toto" else TotoType.MINI_TOTO
    asyncio.run(main(args.round, args.budget, toto_type))
