"""Apply additive SQLite schema tweaks for tooling beyond the bootstrap DDL."""

from __future__ import annotations

import sqlite3


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})")}


def ensure_stage2_rating_columns(conn: sqlite3.Connection) -> None:
    """
    Guarantee `players.rating` plus per-game snapshot columns on `games`.

    Older databases created before Stage 2 only need ALTERs — idempotent.
    """

    cols = _table_columns(conn, "players")
    if "rating" not in cols:
        conn.execute(
            "ALTER TABLE players ADD COLUMN rating REAL NOT NULL DEFAULT 1600;"
        )

    cols = _table_columns(conn, "games")
    alterations = (
        ("elo_a_before", "ALTER TABLE games ADD COLUMN elo_a_before REAL;"),
        ("elo_b_before", "ALTER TABLE games ADD COLUMN elo_b_before REAL;"),
        ("elo_a_after", "ALTER TABLE games ADD COLUMN elo_a_after REAL;"),
        ("elo_b_after", "ALTER TABLE games ADD COLUMN elo_b_after REAL;"),
    )
    for column, ddl in alterations:
        cols = _table_columns(conn, "games")
        if column not in cols:
            conn.execute(ddl)

