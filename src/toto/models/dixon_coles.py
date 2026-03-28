"""Dixon-Coles prediction model wrapper.

Wraps penaltyblog's Dixon-Coles implementation with a fallback
to basic Poisson regression when the primary model fails.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import poisson

from toto.config import DIXON_COLES_XI

logger = logging.getLogger(__name__)


class DixonColesPredictor:
    """Match outcome predictor based on the Dixon-Coles (1997) model.

    Uses penaltyblog's implementation as the primary engine and falls
    back to independent Poisson when penaltyblog cannot produce a
    prediction (e.g., unseen teams).

    Attributes:
        xi: Time-decay parameter controlling how quickly older
            matches lose influence.
    """

    _MAX_GOALS: int = 10

    def __init__(self, xi: float = DIXON_COLES_XI) -> None:
        """Initialise the predictor.

        Args:
            xi: Time-decay parameter. Larger values discount older
                results more aggressively.
        """
        self.xi = xi
        self._model: Any | None = None
        self._matches_df: pd.DataFrame | None = None
        self._team_goals: dict[str, dict[str, float]] = {}
        self._league_avg_goals: float = 1.3
        self._is_fitted: bool = False

    def fit(self, matches_df: pd.DataFrame) -> None:
        """Fit the Dixon-Coles model on historical match data.

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
        self._matches_df = df

        # Pre-compute per-team goal stats for Poisson fallback.
        self._compute_team_stats(df)

        # Attempt penaltyblog Dixon-Coles fit.
        try:
            import penaltyblog as pb

            self._model = pb.models.DixonColesModel(
                df["goals_home"],
                df["goals_away"],
                df["team_home"],
                df["team_away"],
                df["date"],
                xi=self.xi,
            )
            self._model.fit()
            self._is_fitted = True
            logger.info(
                "Dixon-Coles model fitted on %d matches (xi=%.4f).",
                len(df),
                self.xi,
            )
        except Exception:
            logger.warning(
                "penaltyblog Dixon-Coles fit failed; Poisson fallback is active.",
                exc_info=True,
            )
            self._model = None
            self._is_fitted = True

    def predict(self, home_team: str, away_team: str) -> dict[str, float]:
        """Predict match outcome probabilities.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.

        Returns:
            Dictionary with keys ``home_prob``, ``draw_prob``,
            ``away_prob`` summing to 1.0.

        Raises:
            RuntimeError: If the model has not been fitted yet.
        """
        if not self._is_fitted:
            raise RuntimeError("Model has not been fitted. Call fit() first.")

        # Try penaltyblog prediction first.
        if self._model is not None:
            try:
                probs_matrix = self._model.predict(home_team, away_team)
                return self._extract_probabilities(probs_matrix)
            except Exception:
                logger.warning(
                    "penaltyblog predict failed for %s vs %s; using Poisson fallback.",
                    home_team,
                    away_team,
                )

        # Poisson fallback.
        return self._poisson_predict(home_team, away_team)

    def get_team_params(self) -> dict[str, dict[str, float]]:
        """Return attack and defense ratings for every team.

        If the penaltyblog model is available, parameters are extracted
        from it.  Otherwise, simplified ratings derived from average
        goals scored / conceded are returned.

        Returns:
            Mapping of team name to ``{"attack": float, "defense": float}``.
        """
        if self._model is not None:
            try:
                params = self._model.get_params()
                team_params: dict[str, dict[str, float]] = {}
                for team in self._team_goals:
                    attack_key = f"attack_{team}"
                    defense_key = f"defense_{team}"
                    team_params[team] = {
                        "attack": float(params.get(attack_key, 1.0)),
                        "defense": float(params.get(defense_key, 1.0)),
                    }
                return team_params
            except Exception:
                logger.warning(
                    "Failed to extract penaltyblog params; returning fallback.",
                    exc_info=True,
                )

        # Fallback: normalised goals scored / conceded.
        result: dict[str, dict[str, float]] = {}
        for team, stats in self._team_goals.items():
            result[team] = {
                "attack": stats["avg_scored"] / max(self._league_avg_goals, 0.01),
                "defense": stats["avg_conceded"] / max(self._league_avg_goals, 0.01),
            }
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_team_stats(self, df: pd.DataFrame) -> None:
        """Build per-team goal averages from the training data.

        Args:
            df: Cleaned match DataFrame.
        """
        teams: set[str] = set(df["team_home"]) | set(df["team_away"])
        self._league_avg_goals = float(
            (df["goals_home"].sum() + df["goals_away"].sum()) / (2 * max(len(df), 1))
        )

        for team in teams:
            home_mask = df["team_home"] == team
            away_mask = df["team_away"] == team

            goals_scored = (
                df.loc[home_mask, "goals_home"].sum()
                + df.loc[away_mask, "goals_away"].sum()
            )
            goals_conceded = (
                df.loc[home_mask, "goals_away"].sum()
                + df.loc[away_mask, "goals_home"].sum()
            )
            n_matches = int(home_mask.sum() + away_mask.sum())
            n_matches = max(n_matches, 1)

            self._team_goals[team] = {
                "avg_scored": goals_scored / n_matches,
                "avg_conceded": goals_conceded / n_matches,
            }

    def _poisson_predict(
        self, home_team: str, away_team: str
    ) -> dict[str, float]:
        """Predict outcome probabilities using independent Poisson.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.

        Returns:
            Probability dictionary.
        """
        home_stats = self._team_goals.get(home_team)
        away_stats = self._team_goals.get(away_team)

        if home_stats is not None and away_stats is not None:
            home_attack = home_stats["avg_scored"] / max(self._league_avg_goals, 0.01)
            away_defense = away_stats["avg_conceded"] / max(
                self._league_avg_goals, 0.01
            )
            away_attack = away_stats["avg_scored"] / max(self._league_avg_goals, 0.01)
            home_defense = home_stats["avg_conceded"] / max(
                self._league_avg_goals, 0.01
            )

            home_expected = home_attack * away_defense * self._league_avg_goals
            away_expected = away_attack * home_defense * self._league_avg_goals
        else:
            # Completely unknown teams: use league average.
            logger.warning(
                "Unknown team(s): %s / %s. Using league-average Poisson.",
                home_team,
                away_team,
            )
            home_expected = self._league_avg_goals * 1.1  # slight home advantage
            away_expected = self._league_avg_goals * 0.9

        return self._poisson_matrix_probabilities(home_expected, away_expected)

    def _poisson_matrix_probabilities(
        self, home_expected: float, away_expected: float
    ) -> dict[str, float]:
        """Compute H/D/A probabilities from a Poisson score matrix.

        Args:
            home_expected: Expected goals for the home side.
            away_expected: Expected goals for the away side.

        Returns:
            Probability dictionary.
        """
        max_goals = self._MAX_GOALS
        home_pmf = np.array(
            [poisson.pmf(g, home_expected) for g in range(max_goals + 1)]
        )
        away_pmf = np.array(
            [poisson.pmf(g, away_expected) for g in range(max_goals + 1)]
        )

        # Outer product gives the full score-line matrix.
        score_matrix = np.outer(home_pmf, away_pmf)

        home_prob = float(np.tril(score_matrix, -1).sum())
        draw_prob = float(np.trace(score_matrix))
        away_prob = float(np.triu(score_matrix, 1).sum())

        # Normalise to account for truncation.
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

    @staticmethod
    def _extract_probabilities(probs_matrix: Any) -> dict[str, float]:
        """Extract H/D/A probabilities from a penaltyblog prediction.

        Args:
            probs_matrix: Object returned by ``model.predict()``.

        Returns:
            Probability dictionary.
        """
        # penaltyblog returns a FootballProbabilityGrid with
        # home_win, draw, away_win attributes.
        home_prob = float(probs_matrix.home_win)
        draw_prob = float(probs_matrix.draw)
        away_prob = float(probs_matrix.away_win)

        total = home_prob + draw_prob + away_prob
        if total > 0:
            home_prob /= total
            draw_prob /= total
            away_prob /= total

        return {
            "home_prob": round(home_prob, 4),
            "draw_prob": round(draw_prob, 4),
            "away_prob": round(away_prob, 4),
        }
