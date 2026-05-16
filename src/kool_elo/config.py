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
# Offline KOATD tournaments (`import_koatd_offline`); unrelated to Joshua remote dump.
OFFLINE_KOATD_DB_PATH = DATA_DIR / "offline_koatd.sqlite3"
# Bundled CSV exports (commit these for Streamlit Cloud if you want the Offline KOATD tab there).
KOATD_SCORES_EXPORT_CSV = DATA_DIR / "koatd_scores_export.csv"
KOATD_TOURNAMENTS_EXPORT_CSV = DATA_DIR / "koatd_tournament_players_export.csv"

# KO2 community JSON dump. Joshua scopes rows via `Q=`; defaults here match the known-working local URL (`Q=Dagh`).
# Override any time with `KOOL_REMOTE_RESULTS_URL` (shell or Streamlit Secrets) if you deploy elsewhere.
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
# True: forum-style per-player K (provisional A/B ramps in ``elo_core``).
# False: both players use ``K_FACTOR`` (``--k``) every game.
PROVISIONAL_DUAL_K_ENABLED = False

# 0 = off. When N > 0, after every N **globally** processed games in replay, apply the
# same additive delta to every player with games >= PROVISIONAL_GAMES_FULL_THRESHOLD
# so their mean rating equals ``BASE_RATING``. Provisional players unchanged.
# **Streamlit / sync** pass this through to ``compute_elo``; ``0`` disables shifts.
ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES = 0
