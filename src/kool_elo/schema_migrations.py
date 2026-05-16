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


def ensure_provisional_columns(conn: sqlite3.Connection) -> None:
    """Adds `games_played` on players plus per-match K audit columns."""

    cols = _table_columns(conn, "players")
    if "games_played" not in cols:
        conn.execute(
            "ALTER TABLE players ADD COLUMN games_played INTEGER NOT NULL DEFAULT 0;"
        )

    alterations = (
        ("k_used_a", "ALTER TABLE games ADD COLUMN k_used_a REAL;"),
        ("k_used_b", "ALTER TABLE games ADD COLUMN k_used_b REAL;"),
    )
    for column, ddl in alterations:
        cols = _table_columns(conn, "games")
        if column not in cols:
            conn.execute(ddl)


def ensure_peak_tracking_columns(conn: sqlite3.Connection) -> None:
    """Peak rating bookkeeping (replay recomputes these fields)."""

    cols = _table_columns(conn, "players")
    if "peak_rating" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN peak_rating REAL;")
    if "peak_rating_at" not in cols:
        conn.execute(
            "ALTER TABLE players ADD COLUMN peak_rating_at TEXT;"
        )


def ensure_last_game_at_column(conn: sqlite3.Connection) -> None:
    """Last game timestamp — always derivable from ``games`` (see ``refresh_last_game_at``)."""

    cols = _table_columns(conn, "players")
    if "last_game_at" not in cols:
        conn.execute("ALTER TABLE players ADD COLUMN last_game_at TEXT;")


def refresh_last_game_at(conn: sqlite3.Connection) -> None:
    """
    Set ``players.last_game_at`` to each player's latest ``games.start_time``.

    ``games`` is the source of truth; call after imports or Elo replay.
    """

    conn.execute(
        """
        UPDATE players
           SET last_game_at = (
                 SELECT MAX(g.start_time)
                   FROM games g
                  WHERE g.player_a_id = players.player_id
                     OR g.player_b_id = players.player_id
               );
        """
    )


def ensure_elo_schema(conn: sqlite3.Connection) -> None:
    """Convenience rollup for tools that mutate ratings."""

    ensure_stage2_rating_columns(conn)
    ensure_provisional_columns(conn)
    ensure_peak_tracking_columns(conn)
    ensure_last_game_at_column(conn)
