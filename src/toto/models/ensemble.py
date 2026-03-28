"""Ensemble predictor combining Dixon-Coles, Elo, and gradient boosting.

Produces final match-outcome probabilities by blending three
independent sub-models via a configurable weighted average.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from toto.config import (
    DIXON_COLES_XI,
    ELO_HOME_ADVANTAGE,
    ELO_INITIAL,
    ELO_K_FACTOR,
    WEIGHT_BASE_MODEL,
)
from toto.models.dixon_coles import DixonColesPredictor
from toto.models.elo import EloRating

logger = logging.getLogger(__name__)

# Minimum number of samples required to train the gradient-boosting
# classifier.  Below this threshold the ensemble falls back to a
# simple average of Dixon-Coles and Elo predictions.
_MIN_SAMPLES_FOR_GB: int = 50


class EnsemblePredictor:
    """Ensemble model combining Dixon-Coles, Elo, and CatBoost.

    The three components contribute via a weighted average:

    * **Dixon-Coles** and **Elo** each receive ``(1 - WEIGHT_BASE_MODEL) / 2``
      of the total weight.
    * **CatBoost** (gradient boosting) receives ``WEIGHT_BASE_MODEL``.

    When insufficient data is available for the ML component, the
    ensemble degrades gracefully to a 50/50 blend of Dixon-Coles and
    Elo.

    Attributes:
        dc: Dixon-Coles sub-model.
        elo: Elo rating sub-model.
    """

    def __init__(self) -> None:
        """Initialise the ensemble and its sub-models."""
        self.dc = DixonColesPredictor(xi=DIXON_COLES_XI)
        self.elo = EloRating(
            k=ELO_K_FACTOR,
            home_advantage=ELO_HOME_ADVANTAGE,
            initial=ELO_INITIAL,
        )
        self._gb_model: Any | None = None
        self._gb_fitted: bool = False
        self._matches_df: pd.DataFrame | None = None
        self._feature_columns: list[str] = [
            "elo_diff",
            "attack_diff",
            "defense_diff",
            "home_form_5",
            "away_form_5",
            "h2h_home_rate",
            "goals_scored_avg",
            "goals_conceded_avg",
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(
        self,
        matches_df: pd.DataFrame,
        features_df: pd.DataFrame | None = None,
    ) -> None:
        """Fit all sub-models on historical match data.

        Args:
            matches_df: DataFrame with columns ``date``, ``team_home``,
                ``team_away``, ``goals_home``, ``goals_away``.
            features_df: Optional pre-computed feature DataFrame.  If
                *None*, features are built automatically via
                ``_build_features``.
        """
        required = {"date", "team_home", "team_away", "goals_home", "goals_away"}
        missing = required - set(matches_df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        df = matches_df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        self._matches_df = df

        # 1. Fit Dixon-Coles.
        self.dc.fit(df)
        logger.info("Dixon-Coles sub-model fitted.")

        # 2. Process matches through Elo.
        self.elo.process_season(df)
        logger.info("Elo sub-model fitted.")

        # 3. Gradient boosting.
        if features_df is not None:
            feat = features_df
        else:
            feat = self._build_features(df)

        if feat is not None and len(feat) >= _MIN_SAMPLES_FOR_GB:
            target = self._build_target(df, len(feat))
            self._fit_gradient_boosting(feat, target)
        else:
            logger.warning(
                "Not enough data for gradient boosting (%d samples, need %d). "
                "Falling back to DC + Elo average.",
                0 if feat is None else len(feat),
                _MIN_SAMPLES_FOR_GB,
            )

    def predict(
        self,
        home_team: str,
        away_team: str,
        features: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Predict match-outcome probabilities.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.
            features: Optional dictionary of pre-computed features for
                the gradient-boosting model.

        Returns:
            Dictionary containing:
                * ``home_prob``, ``draw_prob``, ``away_prob`` -- final
                  blended probabilities.
                * ``components`` -- per-model raw probabilities.
        """
        dc_probs = self.dc.predict(home_team, away_team)
        elo_probs = self.elo.predict(home_team, away_team)

        gb_probs: dict[str, float] | None = None
        if self._gb_fitted and self._gb_model is not None:
            gb_probs = self._gb_predict(home_team, away_team, features)

        combined = self._combine(dc_probs, elo_probs, gb_probs)

        return {
            "home_prob": combined["home_prob"],
            "draw_prob": combined["draw_prob"],
            "away_prob": combined["away_prob"],
            "components": {
                "dixon_coles": dc_probs,
                "elo": elo_probs,
                "gradient_boosting": gb_probs,
            },
        }

    # ------------------------------------------------------------------
    # Feature engineering
    # ------------------------------------------------------------------

    def _build_features(self, matches_df: pd.DataFrame) -> pd.DataFrame | None:
        """Build a feature DataFrame for the gradient-boosting model.

        Features (per match row):
            * ``elo_diff`` -- home Elo minus away Elo.
            * ``attack_diff`` -- home attack rating minus away.
            * ``defense_diff`` -- home defense rating minus away.
            * ``home_form_5`` -- home team's points from last 5 matches.
            * ``away_form_5`` -- away team's points from last 5 matches.
            * ``h2h_home_rate`` -- historical home-win rate in H2H.
            * ``goals_scored_avg`` -- home team's rolling goals scored
              average (last 5).
            * ``goals_conceded_avg`` -- home team's rolling goals
              conceded average (last 5).

        Args:
            matches_df: Chronologically sorted match DataFrame.

        Returns:
            Feature DataFrame aligned with *matches_df* rows, or
            *None* if feature construction fails.
        """
        df = matches_df.copy().reset_index(drop=True)
        n = len(df)
        if n < _MIN_SAMPLES_FOR_GB:
            return None

        # Temporary Elo tracker for feature generation (separate from
        # self.elo to avoid double-counting).
        temp_elo = EloRating(
            k=self.elo.k,
            home_advantage=self.elo.home_advantage,
            initial=self.elo.initial,
        )

        # Rolling result history per team.
        team_history: dict[str, list[dict[str, Any]]] = {}

        # Head-to-head records: (home, away) -> list of results.
        h2h_records: dict[tuple[str, str], list[int]] = {}

        records: list[dict[str, float]] = []

        for idx in range(n):
            row = df.iloc[idx]
            home = str(row["team_home"])
            away = str(row["team_away"])
            hg = int(row["goals_home"])
            ag = int(row["goals_away"])

            # --- Elo features (before update) ---
            home_r = temp_elo.get_rating(home)
            away_r = temp_elo.get_rating(away)
            elo_diff = home_r["overall"] - away_r["overall"]
            attack_diff = home_r["attack"] - away_r["attack"]
            defense_diff = home_r["defense"] - away_r["defense"]

            # --- Form (last 5) ---
            home_form = self._form_points(team_history.get(home, []), last_n=5)
            away_form = self._form_points(team_history.get(away, []), last_n=5)

            # --- H2H ---
            h2h_key = (home, away)
            h2h_list = h2h_records.get(h2h_key, [])
            h2h_home_rate = (
                sum(h2h_list) / len(h2h_list) if h2h_list else 0.5
            )

            # --- Rolling goals ---
            home_hist = team_history.get(home, [])
            goals_scored_avg = (
                np.mean([m["gf"] for m in home_hist[-5:]]) if home_hist else 1.3
            )
            goals_conceded_avg = (
                np.mean([m["ga"] for m in home_hist[-5:]]) if home_hist else 1.3
            )

            records.append(
                {
                    "elo_diff": elo_diff,
                    "attack_diff": attack_diff,
                    "defense_diff": defense_diff,
                    "home_form_5": home_form,
                    "away_form_5": away_form,
                    "h2h_home_rate": h2h_home_rate,
                    "goals_scored_avg": float(goals_scored_avg),
                    "goals_conceded_avg": float(goals_conceded_avg),
                }
            )

            # --- Post-match updates ---
            temp_elo.update(home, away, hg, ag)

            # Result code: 1 = home win, 0 = draw, 2 = away win.
            if hg > ag:
                home_pts, away_pts = 3, 0
            elif hg == ag:
                home_pts, away_pts = 1, 1
            else:
                home_pts, away_pts = 0, 3

            team_history.setdefault(home, []).append(
                {"gf": hg, "ga": ag, "pts": home_pts}
            )
            team_history.setdefault(away, []).append(
                {"gf": ag, "ga": hg, "pts": away_pts}
            )

            h2h_records.setdefault(h2h_key, []).append(1 if hg > ag else 0)

        return pd.DataFrame(records, columns=self._feature_columns)

    @staticmethod
    def _form_points(history: list[dict[str, Any]], last_n: int = 5) -> float:
        """Compute normalised form points from recent matches.

        Args:
            history: List of match dicts with a ``pts`` key.
            last_n: Number of recent matches to consider.

        Returns:
            Normalised form in [0, 1] (1.0 = all wins).
        """
        if not history:
            return 0.5  # neutral prior
        recent = history[-last_n:]
        max_points = 3 * len(recent)
        return sum(m["pts"] for m in recent) / max(max_points, 1)

    # ------------------------------------------------------------------
    # Gradient boosting
    # ------------------------------------------------------------------

    def _fit_gradient_boosting(
        self, X: pd.DataFrame, y: pd.Series | np.ndarray
    ) -> None:
        """Fit a CatBoost classifier for 3-class prediction.

        Classes: 0 = home win, 1 = draw, 2 = away win.

        Args:
            X: Feature matrix.
            y: Target labels (0, 1, 2).
        """
        try:
            from catboost import CatBoostClassifier
        except ImportError:
            logger.warning("catboost is not installed; skipping GB model.")
            return

        model = CatBoostClassifier(
            iterations=300,
            depth=6,
            learning_rate=0.05,
            loss_function="MultiClass",
            verbose=0,
            random_seed=42,
            classes_count=3,
        )

        try:
            model.fit(X, y)
            self._gb_model = model
            self._gb_fitted = True
            logger.info(
                "CatBoost classifier fitted on %d samples with %d features.",
                len(X),
                X.shape[1],
            )
        except Exception:
            logger.warning("CatBoost fitting failed.", exc_info=True)
            self._gb_model = None
            self._gb_fitted = False

    def _gb_predict(
        self,
        home_team: str,
        away_team: str,
        features: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Obtain gradient-boosting probabilities for a single match.

        Args:
            home_team: Name of the home team.
            away_team: Name of the away team.
            features: Optional pre-computed feature dict.

        Returns:
            Probability dictionary with ``home_prob``, ``draw_prob``,
            ``away_prob``.
        """
        if features is not None:
            row = pd.DataFrame([features])[self._feature_columns]
        else:
            # Build features from current Elo state.
            home_r = self.elo.get_rating(home_team)
            away_r = self.elo.get_rating(away_team)
            row = pd.DataFrame(
                [
                    {
                        "elo_diff": home_r["overall"] - away_r["overall"],
                        "attack_diff": home_r["attack"] - away_r["attack"],
                        "defense_diff": home_r["defense"] - away_r["defense"],
                        "home_form_5": 0.5,
                        "away_form_5": 0.5,
                        "h2h_home_rate": 0.5,
                        "goals_scored_avg": 1.3,
                        "goals_conceded_avg": 1.3,
                    }
                ]
            )[self._feature_columns]

        proba = self._gb_model.predict_proba(row)[0]
        return {
            "home_prob": round(float(proba[0]), 4),
            "draw_prob": round(float(proba[1]), 4),
            "away_prob": round(float(proba[2]), 4),
        }

    # ------------------------------------------------------------------
    # Combination
    # ------------------------------------------------------------------

    @staticmethod
    def _combine(
        dc_probs: dict[str, float],
        elo_probs: dict[str, float],
        gb_probs: dict[str, float] | None,
    ) -> dict[str, float]:
        """Combine sub-model predictions via weighted average.

        When the gradient-boosting model is available:
            * GB weight = ``WEIGHT_BASE_MODEL``
            * DC weight = ``(1 - WEIGHT_BASE_MODEL) / 2``
            * Elo weight = ``(1 - WEIGHT_BASE_MODEL) / 2``

        Without the GB model, Dixon-Coles and Elo are averaged equally.

        Args:
            dc_probs: Dixon-Coles probabilities.
            elo_probs: Elo probabilities.
            gb_probs: Gradient-boosting probabilities, or *None*.

        Returns:
            Blended probability dictionary.
        """
        if gb_probs is not None:
            w_gb = WEIGHT_BASE_MODEL
            w_dc = (1.0 - WEIGHT_BASE_MODEL) / 2.0
            w_elo = (1.0 - WEIGHT_BASE_MODEL) / 2.0

            home = w_dc * dc_probs["home_prob"] + w_elo * elo_probs["home_prob"] + w_gb * gb_probs["home_prob"]
            draw = w_dc * dc_probs["draw_prob"] + w_elo * elo_probs["draw_prob"] + w_gb * gb_probs["draw_prob"]
            away = w_dc * dc_probs["away_prob"] + w_elo * elo_probs["away_prob"] + w_gb * gb_probs["away_prob"]
        else:
            home = 0.5 * dc_probs["home_prob"] + 0.5 * elo_probs["home_prob"]
            draw = 0.5 * dc_probs["draw_prob"] + 0.5 * elo_probs["draw_prob"]
            away = 0.5 * dc_probs["away_prob"] + 0.5 * elo_probs["away_prob"]

        # Normalise to guard against floating-point drift.
        total = home + draw + away
        if total > 0:
            home /= total
            draw /= total
            away /= total
        else:
            home, draw, away = 1 / 3, 1 / 3, 1 / 3

        return {
            "home_prob": round(home, 4),
            "draw_prob": round(draw, 4),
            "away_prob": round(away, 4),
        }

    # ------------------------------------------------------------------
    # Target construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_target(
        matches_df: pd.DataFrame, n_features: int
    ) -> np.ndarray:
        """Build classification target from match results.

        Labels: 0 = home win, 1 = draw, 2 = away win.

        Args:
            matches_df: Match DataFrame.
            n_features: Number of feature rows (must match).

        Returns:
            Numpy array of integer labels.
        """
        df = matches_df.head(n_features)
        conditions = [
            df["goals_home"] > df["goals_away"],
            df["goals_home"] == df["goals_away"],
            df["goals_home"] < df["goals_away"],
        ]
        choices = [0, 1, 2]
        return np.select(conditions, choices).astype(int)
