"""Markdown report generator for toto predictions."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from toto.config import REPORTS_DIR
from toto.models.schemas import (
    MatchResult,
    PlanType,
    Strategy,
)

logger = logging.getLogger(__name__)

RESULT_LABELS: dict[str, str] = {
    "1": "ホーム勝ち",
    "0": "引き分け",
    "2": "アウェイ勝ち",
}


def generate_report(strategy: Strategy) -> Path:
    """Generate a Markdown report from a strategy.

    Args:
        strategy: Complete strategy with predictions and plans.

    Returns:
        Path to the generated report file.
    """
    rnd = strategy.toto_round
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report_path = REPORTS_DIR / f"round_{rnd:04d}_report.md"

    lines: list[str] = []
    _add = lines.append

    _add(f"# toto 第{rnd}回 AI予測レポート")
    _add("")
    _add(f"**生成日時:** {now}")
    _add(f"**くじ種別:** {strategy.toto_type.value}")
    _add(f"**対象試合数:** {len(strategy.predictions)}")
    _add("")

    # --- Summary ---
    _add("## サマリー")
    _add("")
    high_conf = [p for p in strategy.predictions if p.confidence >= 0.5]
    upset_alerts = [p for p in strategy.predictions if p.upset_alert]
    _add(f"- 高信頼度試合（50%以上）: **{len(high_conf)}/{len(strategy.predictions)}**")
    _add(f"- 波乱警戒試合: **{len(upset_alerts)}**")
    _add("")

    # --- Predictions Table ---
    _add("## 全試合予測一覧")
    _add("")
    _add("| No. | ホーム | アウェイ | 予測 | 信頼度 | H% | D% | A% | 警戒 |")
    _add("|-----|--------|---------|------|--------|-----|-----|-----|------|")
    for p in strategy.predictions:
        alert = "⚠" if p.upset_alert else ""
        pick_label = RESULT_LABELS.get(p.recommended_pick.value, "?")
        _add(
            f"| {p.match_number} "
            f"| {p.home_team} "
            f"| {p.away_team} "
            f"| **{pick_label}** "
            f"| {p.confidence:.0%} "
            f"| {p.final_home_prob:.0%} "
            f"| {p.final_draw_prob:.0%} "
            f"| {p.final_away_prob:.0%} "
            f"| {alert} |"
        )
    _add("")

    # --- Upset Highlights ---
    if upset_alerts:
        _add("## 波乱注意試合")
        _add("")
        for p in upset_alerts:
            _add(f"### No.{p.match_number}: {p.home_team} vs {p.away_team}")
            _add("")
            if p.reasoning:
                _add(f"**根拠:** {p.reasoning}")
            _add("")

    # --- Purchase Plans ---
    _add("## 購入プラン")
    _add("")

    plan_icons: dict[PlanType, str] = {
        PlanType.CONSERVATIVE: "[コンサバ]",
        PlanType.BALANCED: "[バランス]",
        PlanType.AGGRESSIVE: "[アグレッシブ]",
    }

    for plan in strategy.plans:
        icon = plan_icons.get(plan.name, "")
        _add(f"### {icon} {plan.display_name}")
        _add("")
        _add(f"- **口数:** {plan.total_combinations}口")
        _add(f"- **費用:** {plan.cost_yen:,}円")
        _add(f"- **推定的中率:** {plan.estimated_hit_rate:.6%}")
        if plan.description:
            _add(f"- **戦略:** {plan.description}")
        _add("")

        # Picks table
        _add("| No. | ピック |")
        _add("|-----|--------|")
        for pick in plan.picks:
            labels = [RESULT_LABELS.get(r.value, r.value) for r in pick.picks]
            picks_str = " / ".join(labels)
            _add(f"| {pick.match_number} | {picks_str} |")
        _add("")

        # Copy-paste format
        _add("**コピペ用:**")
        _add("```")
        pick_codes = []
        for pick in plan.picks:
            codes = [r.value for r in pick.picks]
            pick_codes.append("-".join(codes))
        _add(" ".join(pick_codes))
        _add("```")
        _add("")

    # --- Disclaimer ---
    _add("---")
    _add("")
    _add(f"**免責事項:** {strategy.disclaimer}")
    _add("")

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report generated: %s", report_path)

    strategy.report_path = str(report_path)

    return report_path
