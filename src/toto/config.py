"""Project-wide configuration and constants."""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
MOCK_DIR = DATA_DIR / "mock"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Ensure directories exist
for d in (INTERMEDIATE_DIR, MOCK_DIR, CACHE_DIR, REPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Logging ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- HTTP ---
RATE_LIMIT_SECONDS: float = 2.0
HTTP_TIMEOUT: float = 30.0
MAX_RETRIES: int = 3

# --- Cache ---
CACHE_TTL_HOURS: int = 6

# --- Model Parameters ---
DIXON_COLES_XI: float = 0.001  # Time-decay parameter
LOOKBACK_SEASONS: int = 4
ELO_INITIAL: float = 1500.0
ELO_K_FACTOR: float = 20.0
ELO_HOME_ADVANTAGE: float = 100.0

# --- Prediction Weights ---
WEIGHT_BASE_MODEL: float = 0.40
WEIGHT_CONDITION: float = 0.20
WEIGHT_MARKET: float = 0.15
WEIGHT_UPSET: float = 0.25

# --- Upset Detection Thresholds (toto-roid Daisy reference) ---
UPSET_VOTE_FLOOR: float = 0.50  # Min vote share to be considered "honmei"
UPSET_VOTE_CEILING: float = 0.80  # Max vote share (too dominant = skip)
UPSET_PROBABILITY_THRESHOLD: float = 0.70  # Min upset probability to flag

# --- J-League Teams (2025 J1) ---
J1_TEAMS: list[str] = [
    "北海道コンサドーレ札幌",
    "鹿島アントラーズ",
    "浦和レッズ",
    "大宮アルディージャ",
    "FC東京",
    "東京ヴェルディ",
    "町田ゼルビア",
    "川崎フロンターレ",
    "横浜F・マリノス",
    "横浜FC",
    "湘南ベルマーレ",
    "アルビレックス新潟",
    "清水エスパルス",
    "ジュビロ磐田",
    "名古屋グランパス",
    "京都サンガF.C.",
    "ガンバ大阪",
    "セレッソ大阪",
    "ヴィッセル神戸",
    "サンフレッチェ広島",
    "アビスパ福岡",
]

# --- Stadium Locations (lat, lon) for distance calculation ---
STADIUM_LOCATIONS: dict[str, tuple[float, float]] = {
    "北海道コンサドーレ札幌": (43.015, 141.410),
    "鹿島アントラーズ": (35.960, 140.620),
    "浦和レッズ": (35.865, 139.638),
    "大宮アルディージャ": (35.920, 139.623),
    "FC東京": (35.665, 139.527),
    "東京ヴェルディ": (35.665, 139.527),
    "町田ゼルビア": (35.548, 139.407),
    "川崎フロンターレ": (35.529, 139.716),
    "横浜F・マリノス": (35.510, 139.606),
    "横浜FC": (35.510, 139.606),
    "湘南ベルマーレ": (35.346, 139.350),
    "アルビレックス新潟": (37.878, 139.037),
    "清水エスパルス": (35.014, 138.453),
    "ジュビロ磐田": (34.773, 137.870),
    "名古屋グランパス": (35.115, 136.972),
    "京都サンガF.C.": (34.935, 135.742),
    "ガンバ大阪": (34.802, 135.538),
    "セレッソ大阪": (34.614, 135.518),
    "ヴィッセル神戸": (34.660, 135.166),
    "サンフレッチェ広島": (34.390, 132.455),
    "アビスパ福岡": (33.585, 130.411),
}

# --- URLs ---
JLEAGUE_DATA_URL = "https://data.j-league.or.jp"
TOTO_OFFICIAL_URL = "https://sp.toto-dream.com"
TOTOMO_URL = "https://toto.cam"
FOOTBALL_LAB_URL = "https://www.football-lab.jp"
