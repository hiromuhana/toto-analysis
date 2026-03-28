"""Pydantic data schemas for inter-agent communication.

All intermediate JSON files conform to these schemas.
Each schema includes agent name, timestamp, and toto_round for traceability.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# --- Enums ---

class MatchResult(str, Enum):
    """toto match result codes."""

    HOME_WIN = "1"
    DRAW = "0"
    AWAY_WIN = "2"


class PlanType(str, Enum):
    """Purchase plan types (Rakuten toto reference)."""

    CONSERVATIVE = "conservative"  # しっかりゾウ
    BALANCED = "balanced"          # バランスバード
    AGGRESSIVE = "aggressive"      # ハンターライオン


class TotoType(str, Enum):
    """toto lottery type."""

    TOTO = "toto"          # 13 matches
    MINI_TOTO = "minitoto"  # 5 matches


# --- Base ---

class AgentOutput(BaseModel):
    """Base class for all agent outputs."""

    agent: str
    timestamp: datetime = Field(default_factory=datetime.now)
    toto_round: int
    toto_type: TotoType = TotoType.TOTO


# --- Match-level primitives ---

class RecentMatch(BaseModel):
    """A single past match result."""

    date: str
    opponent: str
    home_or_away: str  # "home" | "away"
    goals_for: int
    goals_against: int
    result: MatchResult


class SeasonStats(BaseModel):
    """Aggregated season statistics for a team."""

    played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    points: int
    rank: int
    xg: float | None = None
    xga: float | None = None


class MatchData(BaseModel):
    """Collected data for a single toto match."""

    match_number: int
    home_team: str
    away_team: str
    stadium: str | None = None
    match_date: str | None = None
    home_season_stats: SeasonStats
    away_season_stats: SeasonStats
    home_recent: list[RecentMatch] = Field(default_factory=list)
    away_recent: list[RecentMatch] = Field(default_factory=list)
    h2h: list[RecentMatch] = Field(default_factory=list)
    home_elo: float = 1500.0
    away_elo: float = 1500.0
    home_attack_rating: float = 1.0
    away_attack_rating: float = 1.0
    home_defense_rating: float = 1.0
    away_defense_rating: float = 1.0


# --- Agent Output: data-collector ---

class CollectedData(AgentOutput):
    """Output of the data-collector agent."""

    agent: str = "data-collector"
    matches: list[MatchData]
    data_sources: list[str] = Field(default_factory=list)


# --- Agent Output: condition-analyzer ---

class MatchCondition(BaseModel):
    """Condition analysis for a single match."""

    match_number: int
    home_team: str
    away_team: str
    fatigue_home: float = Field(ge=-1.0, le=1.0)
    fatigue_away: float = Field(ge=-1.0, le=1.0)
    momentum_home: float = Field(ge=-1.0, le=1.0)
    momentum_away: float = Field(ge=-1.0, le=1.0)
    venue_advantage: float = Field(ge=-1.0, le=1.0)
    h2h_affinity: float = Field(ge=-1.0, le=1.0)
    weather_impact: float = Field(ge=-1.0, le=1.0, default=0.0)
    travel_distance_km: float = 0.0
    days_rest_home: int | None = None
    days_rest_away: int | None = None
    total_home_adjustment: float = 0.0
    total_away_adjustment: float = 0.0


class ConditionAnalysis(AgentOutput):
    """Output of the condition-analyzer agent."""

    agent: str = "condition-analyzer"
    conditions: list[MatchCondition]


# --- Agent Output: odds-analyzer ---

class MatchOdds(BaseModel):
    """Odds and voting analysis for a single match."""

    match_number: int
    home_team: str
    away_team: str
    # toto voting percentages
    home_vote_pct: float = 0.0
    draw_vote_pct: float = 0.0
    away_vote_pct: float = 0.0
    # Implied probabilities (vote pct corrected for overround)
    implied_home_prob: float = 0.0
    implied_draw_prob: float = 0.0
    implied_away_prob: float = 0.0
    # Model probabilities (from Dixon-Coles / ensemble)
    model_home_prob: float = 0.0
    model_draw_prob: float = 0.0
    model_away_prob: float = 0.0
    # Value = model_prob - implied_prob (positive = value bet)
    value_home: float = 0.0
    value_draw: float = 0.0
    value_away: float = 0.0
    # Detected biases
    biases: list[str] = Field(default_factory=list)


class OddsAnalysis(AgentOutput):
    """Output of the odds-analyzer agent."""

    agent: str = "odds-analyzer"
    odds: list[MatchOdds]


# --- Agent Output: upset-detector ---

class UpsetPattern(BaseModel):
    """A detected upset pattern."""

    category: str  # fatigue_gap | momentum_reversal | h2h_mismatch | ...
    description: str
    severity: float = Field(ge=0.0, le=1.0)


class MatchUpset(BaseModel):
    """Upset analysis for a single match."""

    match_number: int
    home_team: str
    away_team: str
    upset_score: int = Field(ge=0, le=100)
    patterns: list[UpsetPattern] = Field(default_factory=list)
    adjusted_home_prob: float = 0.0
    adjusted_draw_prob: float = 0.0
    adjusted_away_prob: float = 0.0
    is_upset_alert: bool = False
    explanation: str = ""


class UpsetAnalysis(AgentOutput):
    """Output of the upset-detector agent."""

    agent: str = "upset-detector"
    upsets: list[MatchUpset]


# --- Agent Output: strategy-synthesizer ---

class MatchPrediction(BaseModel):
    """Final prediction for a single match."""

    match_number: int
    home_team: str
    away_team: str
    final_home_prob: float
    final_draw_prob: float
    final_away_prob: float
    recommended_pick: MatchResult
    confidence: float = Field(ge=0.0, le=1.0)
    upset_alert: bool = False
    reasoning: str = ""


class PurchasePick(BaseModel):
    """A single pick in a purchase plan."""

    match_number: int
    picks: list[MatchResult]  # 1 pick = single, 2 = double, 3 = triple


class PurchasePlan(BaseModel):
    """A toto purchase plan."""

    name: PlanType
    display_name: str  # e.g., "しっかりゾウ（コンサバ）"
    picks: list[PurchasePick]
    total_combinations: int
    cost_yen: int
    estimated_hit_rate: float
    description: str = ""


class Strategy(AgentOutput):
    """Output of the strategy-synthesizer agent."""

    agent: str = "strategy-synthesizer"
    predictions: list[MatchPrediction]
    plans: list[PurchasePlan]
    report_path: str = ""
    disclaimer: str = (
        "本予測はエンターテインメント・研究目的であり、"
        "投資助言ではありません。くじの購入は自己責任で行ってください。"
    )
