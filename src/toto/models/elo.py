"""Elo rating system with attack/defense separation.

Implements a FiveThirtyEight SPI-style Elo system that tracks
separate attack and defense components, enabling richer feature
generation for downstream models.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import poisson

from toto.config import ELO_HOME_ADVANTAGE, ELO_INITIAL, ELO_K_FACTOR

logger = logging.getLogger(__name__)


class EloRating:
    """Elo rating system with attack/defense decomposition.

    Each team maintains three ratings:
        * **overall** -- classic Elo rating.
        * **attack**  -- adjusted when goals scored deviate from
          expectation.
        * **defense** -- adjusted when goals conceded deviate from
          expectation.

    The attack/defense split follows a simplified FiveThirtyEight SPI
    methodology: goals scored above expectation push attack up, and
    goals conceded below expectation push defense up (lower concession
    is better, reflected as a *higher* defense rating).

    Attributes:
        k: Base K-factor controlling rating volatility.
        home_advantage: Elo points added to the home side for
            expected-score calculation.
        initial: Starting rating for unseen teams.
    """

    _MAX_GOALS: int = 10

    def __init__(
        self,
        k: float = ELO_K_FACTOR,
        home_advantage: float = ELO_HOME_ADVANTAGE,
        initial: float = ELO_INITIAL,
    ) -> None:
        """Initialise the rating system.

        Args:
            k: Base K-factor.
            home_advantage: Home-field advantage in Elo points.
            initial: Initial rating for new teams.
        """
        self.k = k
        self.home_advantage = home_advantage
        self.initial = initial

        # {team: {"overall": float, "attack": float, "defense": float}}
        self._ratings: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "overall": self.initial,
                "attack": self.initial,
                "defense": self.initial,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
    ) -> None:
        """Update ratings after a single match result.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.
            home_goals: Goals scored by the home team.
            away_goals: Goals scored by the away team.
        """
        # Ensure teams exist in the ratings dictionary.
        _ = self._ratings[home_team]
        _ = self._ratings[away_team]

        home_overall = self._ratings[home_team]["overall"]
        away_overall = self._ratings[away_team]["overall"]

        # Expected score (logistic).
        home_expected = self._expected_score(home_overall, away_overall)
        away_expected = 1.0 - home_expected

        # Actual score: 1 for win, 0.5 for draw, 0 for loss.
        if home_goals > away_goals:
            home_actual, away_actual = 1.0, 0.0
        elif home_goals == away_goals:
            home_actual, away_actual = 0.5, 0.5
        else:
            home_actual, away_actual = 0.0, 1.0

        # Goal-difference multiplier (FiveThirtyEight method).
        goal_diff = abs(home_goals - away_goals)
        gd_multiplier = self._goal_diff_multiplier(goal_diff)

        k_eff = self.k * gd_multiplier

        # Update overall Elo.
        home_delta = k_eff * (home_actual - home_expected)
        away_delta = k_eff * (away_actual - away_expected)

        self._ratings[home_team]["overall"] += home_delta
        self._ratings[away_team]["overall"] += away_delta

        # --- Attack / Defense split ---
        # Expected goals derived from rating difference.
        home_exp_goals = self._expected_goals(home_overall, away_overall)
        away_exp_goals = self._expected_goals(away_overall, home_overall)

        attack_k = self.k * 0.5  # dampen component updates
        defense_k = self.k * 0.5

        # Home attack: scored more than expected -> attack goes up.
        home_attack_delta = attack_k * (home_goals - home_exp_goals)
        # Home defense: conceded less than expected -> defense goes up.
        home_defense_delta = defense_k * (away_exp_goals - away_goals)

        away_attack_delta = attack_k * (away_goals - away_exp_goals)
        away_defense_delta = defense_k * (home_exp_goals - home_goals)

        self._ratings[home_team]["attack"] += home_attack_delta
        self._ratings[away_team]["attack"] += away_attack_delta
        self._ratings[home_team]["defense"] += home_defense_delta
        self._ratings[away_team]["defense"] += away_defense_delta

    def get_rating(self, team: str) -> dict[str, float]:
        """Return the current ratings for a team.

        Args:
            team: Team name.

        Returns:
            Dictionary with ``overall``, ``attack``, ``defense``.
        """
        r = self._ratings[team]
        return {
            "overall": round(r["overall"], 2),
            "attack": round(r["attack"], 2),
            "defense": round(r["defense"], 2),
        }

    def predict(self, home_team: str, away_team: str) -> dict[str, float]:
        """Predict match outcome probabilities.

        Converts the Elo difference to a win expectancy via the
        logistic function, then uses Poisson modelling to estimate
        the draw probability separately from the residual.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.

        Returns:
            Dictionary with ``home_prob``, ``draw_prob``, ``away_prob``
            summing to 1.0.
        """
        home_overall = self._ratings[home_team]["overall"]
        away_overall = self._ratings[away_team]["overall"]

        # Expected goals for Poisson draw estimation.
        home_exp_goals = self._expected_goals(home_overall, away_overall)
        away_exp_goals = self._expected_goals(away_overall, home_overall)

        # Build Poisson score-line matrix.
        max_goals = self._MAX_GOALS
        home_pmf = np.array(
            [poisson.pmf(g, max(home_exp_goals, 0.1)) for g in range(max_goals + 1)]
        )
        away_pmf = np.array(
            [poisson.pmf(g, max(away_exp_goals, 0.1)) for g in range(max_goals + 1)]
        )
        score_matrix = np.outer(home_pmf, away_pmf)

        home_prob = float(np.tril(score_matrix, -1).sum())
        draw_prob = float(np.trace(score_matrix))
        away_prob = float(np.triu(score_matrix, 1).sum())

        total = home_prob + draw_prob + away_prob
        if total > 0:
            home_prob /= total
            draw_prob /= total
            away_prob /= total
        else:
            home_prob, draw_prob, away_prob = 1 / 3, 1 / 3, 1 / 3

        return {
            "home_prob": round(home_prob, 4),
            "draw_prob": round(draw_prob, 4),
            "away_prob": round(away_prob, 4),
        }

    def process_season(self, matches_df: pd.DataFrame) -> None:
        """Process all matches in a season chronologically.

        Args:
            matches_df: DataFrame with columns ``date``, ``team_home``,
                ``team_away``, ``goals_home``, ``goals_away``.

        Raises:
            ValueError: If required columns are missing.
        """
        required = {"date", "team_home", "team_away", "goals_home", "goals_away"}
        missing = required - set(matches_df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        df = matches_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        for _, row in df.iterrows():
            self.update(
                home_team=str(row["team_home"]),
                away_team=str(row["team_away"]),
                home_goals=int(row["goals_home"]),
                away_goals=int(row["goals_away"]),
            )

        logger.info("Processed %d matches for season Elo update.", len(df))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _expected_score(
        self, rating_a: float, rating_b: float
    ) -> float:
        """Logistic expected score with home advantage.

        Args:
            rating_a: Home team's overall Elo.
            rating_b: Away team's overall Elo.

        Returns:
            Expected score for the home team in [0, 1].
        """
        diff = rating_a + self.home_advantage - rating_b
        return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    def _expected_goals(
        self, rating_a: float, rating_b: float
    ) -> float:
        """Estimate expected goals from an Elo difference.

        Maps the Elo advantage to a goal expectation using a scaled
        logistic transform centred on 1.3 goals (J-League average).

        Args:
            rating_a: Attacking team's overall Elo.
            rating_b: Defending team's overall Elo.

        Returns:
            Expected goals for team A.
        """
        league_avg = 1.3
        diff = rating_a + self.home_advantage - rating_b
        # Scale: 400 Elo-point advantage roughly doubles goal expectation.
        return league_avg * (10.0 ** (diff / 800.0))

    @staticmethod
    def _goal_diff_multiplier(goal_diff: int) -> float:
        """FiveThirtyEight-style goal-difference multiplier.

        For goal differences > 1, applies ``log(goal_diff + 1)`` to
        amplify the K-factor, rewarding / penalising dominant results
        more heavily.

        Args:
            goal_diff: Absolute goal difference.

        Returns:
            Multiplier >= 1.0.
        """
        if goal_diff <= 1:
            return 1.0
        return math.log(goal_diff + 1)
