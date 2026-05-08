"""
Kool Elo — Streamlit dashboard (Stage 4).

Run from project root:

    pip install -r requirements.txt
    streamlit run dashboard.py
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kool_elo.config import BASE_RATING, DEFAULT_DB_PATH, K_FACTOR, PROJECT_ROOT


@st.cache_data(show_spinner=False)
def fetch_players(db_path_str: str) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT player_id, display_name, rating
            FROM players
            ORDER BY rating DESC, display_name COLLATE NOCASE;
            """,
            conn,
        )
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def fetch_games_agg(db_path_str: str) -> pd.Series:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS games,
              MIN(start_time) AS first_ts,
              MAX(start_time) AS last_ts,
              AVG(CAST(duration_secs AS REAL)) AS avg_duration_secs
            FROM games;
            """
        ).fetchone()
        cols = ("games", "first_ts", "last_ts", "avg_duration_secs")
        if row is None:
            return pd.Series({name: None for name in cols})
        return pd.Series(dict(zip(cols, row)))
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def rating_history_for_player(db_path_str: str, player_id: str) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
              start_time,
              game_id,
              CASE
                WHEN player_a_id = :pid THEN elo_a_after
                WHEN player_b_id = :pid THEN elo_b_after
              END AS rating_after,
              CASE
                WHEN player_a_id = :pid THEN score_a
                ELSE score_b
              END AS goals_for,
              CASE
                WHEN player_a_id = :pid THEN score_b
                ELSE score_a
              END AS goals_against
            FROM games
            WHERE player_a_id = :pid OR player_b_id = :pid
            ORDER BY start_time, game_id;
            """,
            conn,
            params={"pid": player_id},
        )
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def recent_games(db_path_str: str, limit: int = 200) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT
              start_time,
              game_id,
              player_a_id,
              player_b_id,
              score_a,
              score_b,
              elo_a_after,
              elo_b_after
            FROM games
            ORDER BY start_time DESC, game_id DESC
            LIMIT ?;
            """,
            conn,
            params=(int(limit),),
        )
    finally:
        conn.close()


def run_compute_elo(db_path: Path, base: float, k: float) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    cmd = [
        sys.executable,
        "-m",
        "kool_elo.compute_elo",
        "--db",
        str(db_path),
        "--base",
        str(base),
        "--k",
        str(k),
        "--quiet",
    ]
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=True)


def main() -> None:
    st.set_page_config(page_title="Kool Elo", layout="wide")
    st.title("Kool Elo — Kick Off 2 ratings")

    st.sidebar.header("Data")
    db_default = str(DEFAULT_DB_PATH.resolve())
    db_input = st.sidebar.text_input("SQLite database", value=db_default)
    db_path = Path(db_input).expanduser().resolve()

    if not db_path.is_file():
        st.error(
            f"Database file not found: `{db_path}`. "
            "Run `python -m kool_elo.import_matches --overwrite` first, then `compute_elo`."
        )
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Recompute Elo")
    st.sidebar.caption(
        "Replays all games in chronological order and refreshes `players.rating` "
        "and per-game `elo_*` columns."
    )
    base = float(st.sidebar.number_input("Base rating", value=float(BASE_RATING), step=1.0))
    k_factor = float(st.sidebar.number_input("K-factor", value=float(K_FACTOR), step=1.0))
    if st.sidebar.button("Run full replay", type="primary"):
        with st.spinner("Replaying every game — this can take a few seconds…"):
            try:
                run_compute_elo(db_path, base, k_factor)
            except subprocess.CalledProcessError as exc:
                st.sidebar.error(f"`compute_elo` failed (exit {exc.returncode}).")
                st.stop()
            else:
                st.cache_data.clear()
                st.sidebar.success("Replay finished — refreshing views.")
                st.rerun()

    db_key = str(db_path)

    players_df = fetch_players(db_key)
    games_agg = fetch_games_agg(db_key)

    overview_tab, board_tab, history_tab, recent_tab = st.tabs(
        ["Overview", "Leaderboard", "Rating history", "Recent games"]
    )

    with overview_tab:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Players", f"{len(players_df):,}")
        c2.metric("Stored games", f"{int(games_agg['games']):,}")
        c3.metric("First match", games_agg["first_ts"] or "—")
        c4.metric("Latest match", games_agg["last_ts"] or "—")

        rating_min = float(players_df["rating"].min()) if len(players_df) else float("nan")
        rating_max = float(players_df["rating"].max()) if len(players_df) else float("nan")
        avg_dur = games_agg["avg_duration_secs"]
        lines = [
            (
                f"- **Rating range:** {rating_min:.1f} → {rating_max:.1f} "
                "(snapshot after latest `compute_elo` replay)"
            )
        ]
        if pd.notna(avg_dur):
            lines.append(
                f"- **Mean match duration:** {float(avg_dur):,.1f}s "
                "(raw `duration_secs` from dump; includes pauses/menu time)"
            )
        st.markdown("\n".join(lines))

    with board_tab:
        st.subheader("Rankings")
        query = st.text_input("Filter by name (substring, case-insensitive)", value="")

        filtered = players_df
        if query.strip():
            needle = query.strip().lower()
            mask = filtered["display_name"].str.lower().str.contains(needle, na=False)
            filtered = filtered.loc[mask]

        st.dataframe(
            filtered.reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "player_id": st.column_config.TextColumn("player_id"),
                "display_name": st.column_config.TextColumn("display_name"),
                "rating": st.column_config.NumberColumn("rating", format="%.2f"),
            },
        )

    with history_tab:
        if players_df.empty:
            st.warning("No players.")
        else:
            player_options = (
                players_df.assign(
                    label=lambda df: df["display_name"].astype(str)
                    + " ("
                    + df["player_id"].astype(str)
                    + ")"
                )[["label", "player_id"]]
            )
            label_choice = st.selectbox(
                "Pick a player",
                options=player_options["label"].tolist(),
            )
            chosen_row = player_options.loc[player_options["label"] == label_choice].iloc[0]
            player_id = str(chosen_row["player_id"])

            hist = rating_history_for_player(db_key, player_id)
            if hist.empty:
                st.info("No games for this player in the DB.")
            else:
                hist = hist.assign(parsed_time=pd.to_datetime(hist["start_time"]))
                nan_elo = hist["rating_after"].isna().sum()
                if nan_elo:
                    st.warning(
                        f"{int(nan_elo)} row(s) still lack `rating_after`. "
                        "Run **Recompute Elo** in the sidebar."
                    )
                chart_df = hist.dropna(subset=["rating_after"])
                if not chart_df.empty:
                    chart_df = chart_df.set_index("parsed_time")[["rating_after"]]
                    st.line_chart(chart_df, height=360)
                    st.dataframe(
                        hist[
                            ["start_time", "game_id", "goals_for", "goals_against", "rating_after"]
                        ],
                        hide_index=True,
                        use_container_width=True,
                        height=420,
                        column_config={
                            "rating_after": st.column_config.NumberColumn(
                                "rating_after", format="%.2f"
                            )
                        },
                    )

    with recent_tab:
        st.subheader(f"Latest {200} matches (reverse chronological)")
        recent = recent_games(db_key, limit=200)
        st.dataframe(recent, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
