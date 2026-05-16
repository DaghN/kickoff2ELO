"""Cached SQLite reads shared by dashboard.py and multipage player routes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


@st.cache_data(show_spinner=False)
def fetch_players(db_path_str: str) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT player_id,
                   display_name,
                   rating,
                   COALESCE(games_played, 0) AS games_played,
                   peak_rating,
                   peak_rating_at
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
        return pd.read_sql_query(
            """
            SELECT
              g.start_time,
              g.game_id,
              g.player_a_id,
              g.player_b_id,
              pa.display_name AS name_a,
              pb.display_name AS name_b,
              g.score_a,
              g.score_b,
              CASE
                WHEN g.player_a_id = :pid THEN g.elo_a_after
                WHEN g.player_b_id = :pid THEN g.elo_b_after
              END AS rating_after
            FROM games g
            JOIN players pa ON pa.player_id = g.player_a_id
            JOIN players pb ON pb.player_id = g.player_b_id
            WHERE g.player_a_id = :pid OR g.player_b_id = :pid
            ORDER BY g.start_time, g.game_id;
            """,
            conn,
            params={"pid": player_id},
        )
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
              g.start_time,
              g.game_id,
              g.player_a_id,
              g.player_b_id,
              pa.display_name AS name_a,
              pb.display_name AS name_b,
              g.score_a,
              g.score_b,
              g.elo_a_after,
              g.elo_b_after
            FROM games g
            JOIN players pa ON pa.player_id = g.player_a_id
            JOIN players pb ON pb.player_id = g.player_b_id
            ORDER BY g.start_time DESC, g.game_id DESC
            LIMIT ?;
            """,
            conn,
            params=(int(limit),),
        )
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def fetch_player_row(db_path_str: str, player_id: str) -> pd.Series | None:
    players = fetch_players(db_path_str)
    if players.empty:
        return None
    mask = players["player_id"].astype(str) == str(player_id)
    if not mask.any():
        return None
    return players.loc[mask].iloc[0]
