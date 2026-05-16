"""Amiga 500 (KOATD) player profile (Streamlit multipage)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import kool_elo.config as _kool_config

from kool_elo.config import PROVISIONAL_GAMES_FULL_THRESHOLD
from kool_elo.schema_migrations import ensure_elo_schema
from kool_elo.streamlit_queries import fetch_player_row, rating_history_for_player

AMIGA500_DB_PATH = getattr(
    _kool_config,
    "AMIGA500_DB_PATH",
    getattr(_kool_config, "OFFLINE_KOATD_DB_PATH", _kool_config.DATA_DIR / "offline_koatd.sqlite3"),
)


def _ensure_schema(sqlite_path: Path) -> None:
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_elo_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _query_player_id() -> str:
    raw = st.query_params.get("player_id")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    return str(raw).strip() if raw else ""


st.set_page_config(page_title="Kick Off 2 ELO — Amiga 500 player", layout="wide")

player_id = _query_player_id()
if not player_id:
    st.warning("Missing **player_id** in the URL. Open a player from the main **Leaderboard** or match lists.")
    st.stop()

db_str = st.session_state.get("_kool_amiga500_db_path") or str(AMIGA500_DB_PATH.resolve())
db_path = Path(db_str).expanduser().resolve()
if not db_path.is_file():
    st.error(f"Amiga 500 SQLite not found (`{db_path}`). Build it from the Home page or **Amiga 500** tools.")
    st.stop()

_ensure_schema(db_path)

row = fetch_player_row(str(db_path), player_id)
if row is None:
    st.error(f"No Amiga 500 player with id `{player_id}`.")
    st.stop()

title_name = str(row["display_name"])
st.title(title_name)
st.caption(f"Amiga 500 · `{player_id}`")

gp = int(row["games_played"])
prov = gp < PROVISIONAL_GAMES_FULL_THRESHOLD
c1, c2, c3 = st.columns(3)
c1.metric("Rating", f"{float(row['rating']):.1f}")
c2.metric("Games played", f"{gp:,}")
c3.metric("Provisional", "yes (?)" if prov else "no")

hist = rating_history_for_player(str(db_path), player_id)
if hist.empty:
    st.info("No games for this player.")
else:
    hist = hist.assign(parsed_time=pd.to_datetime(hist["start_time"]))
    nan_elo = hist["rating_after"].isna().sum()
    if nan_elo:
        st.warning(
            f"{int(nan_elo)} row(s) lack `rating_after`. Run **Recompute Elo** from the Home page sidebar."
        )
    chart_df = hist.dropna(subset=["rating_after"])
    if not chart_df.empty:
        chart_df = chart_df.set_index("parsed_time")[["rating_after"]]
        st.subheader("Rating history")
        st.line_chart(chart_df, height=320)
