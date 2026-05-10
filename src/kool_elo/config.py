"""Project paths and shared constants.

ELO tuning parameters live here so experiments stay one-file edits.
"""

import os
from pathlib import Path

# src/kool_elo/config.py -> parents: kool_elo, src, project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_JSON_PATH = PROJECT_ROOT / "retro_results.json"
DEFAULT_DB_PATH = DATA_DIR / "retro_elo.sqlite3"

# KO2 community JSON dump. `?Q=` limits what the server returns — change or clear via env.
DEFAULT_REMOTE_RESULTS_URL = "https://joshua.kickoff2.net/db/AllResultsDump.php?Q=Dagh"
REMOTE_RESULTS_MANIFEST_PATH = DATA_DIR / "results_sync_manifest.json"


def resolved_remote_results_url() -> str:
    return os.environ.get("KOOL_REMOTE_RESULTS_URL", DEFAULT_REMOTE_RESULTS_URL).strip()

# --- ELO (used from Stage 2 onward) ---
BASE_RATING = 1600
K_FACTOR = 32

# Provisional-player K scaling (forum spec — prematch game counters during replay).
PROVISIONAL_GAMES_FULL_THRESHOLD = 20
PROVISIONAL_K_CEILING_ABOVE_RATING = (
    1900.0  # strict `>`; cap applies only while still provisional.
)
