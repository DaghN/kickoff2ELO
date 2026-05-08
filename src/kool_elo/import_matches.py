"""
Load retro_results.json into SQLite (players + games).

Usage (from project root, with PYTHONPATH including ./src):

    pip install -r requirements.txt
    python -m kool_elo.import_matches

Or:

    pip install -r requirements.txt
    $env:PYTHONPATH='src'; python -m kool_elo.import_matches
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from kool_elo.config import DEFAULT_DB_PATH, DEFAULT_JSON_PATH


def _project_sqlite_statements(schema_path: Path) -> list[str]:
    """Split schema.sql into executable statements (skip PRAGMA in DDL file for apply_schema)."""
    raw = schema_path.read_text(encoding="utf-8")
    parts: list[str] = []
    for chunk in raw.split(";"):
        stmt = chunk.strip()
        if not stmt or stmt.upper().startswith("PRAGMA"):
            continue
        parts.append(stmt + ";")
    return parts


def apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.execute("PRAGMA foreign_keys = ON;")
    for stmt in _project_sqlite_statements(schema_path):
        conn.execute(stmt)


def load_and_prepare(
    json_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """
    Read JSON, validate, sort chronologically, derive players (last seen name wins).

    Returns (games_df, players_df, stats) where games_df rows are DB-ready.
    """
    df = pd.read_json(json_path)
    required = [
        "GameID",
        "StartTime",
        "PlayerA",
        "PlayerB",
        "NameA",
        "NameB",
        "ScoreA",
        "ScoreB",
        "Duration",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"JSON missing columns: {missing}")

    stats: dict[str, int] = {
        "rows_read": int(len(df)),
        "duplicate_game_ids": int(df["GameID"].duplicated().sum()),
    }
    if stats["duplicate_game_ids"]:
        raise ValueError("GameID values must be unique.")

    # Self-matches are invalid for head-to-head ELO; keep them out of `games`.
    self_mask = df["PlayerA"] == df["PlayerB"]
    stats["self_matches_skipped"] = int(self_mask.sum())
    df = df.loc[~self_mask].copy()

    # Parse time for ordering; keep SQLite-friendly string in output.
    start = pd.to_datetime(df["StartTime"], errors="coerce")
    if start.isna().any():
        bad = int(start.isna().sum())
        raise ValueError(f"Invalid StartTime on {bad} row(s).")

    df["_start_ts"] = start
    df.sort_values(["_start_ts", "GameID"], kind="mergesort", inplace=True)
    df.drop(columns=["_start_ts"], inplace=True)

    # Integer fields (source stores many as strings).
    for col in ("ScoreA", "ScoreB", "Duration"):
        df[f"_{col}_int"] = pd.to_numeric(df[col], errors="coerce")

    bad_scores = df["_ScoreA_int"].isna() | df["_ScoreB_int"].isna()
    if bad_scores.any():
        raise ValueError(f"Non-numeric scores on {int(bad_scores.sum())} row(s).")

    bad_dur = df["_Duration_int"].isna()
    if bad_dur.any():
        raise ValueError(f"Non-numeric Duration on {int(bad_dur.sum())} row(s).")

    if ((df["_ScoreA_int"] < 0) | (df["_ScoreB_int"] < 0)).any():
        raise ValueError("Negative scores are not allowed.")
    if (df["_Duration_int"] < 0).any():
        raise ValueError("Negative duration is not allowed.")

    # Build players with stable "last name in chronological order" rule.
    seq = pd.RangeIndex(len(df))
    side_a = pd.DataFrame(
        {
            "player_id": df["PlayerA"].astype(str),
            "display_name": df["NameA"].astype(str),
            "_seq": seq,
        }
    )
    side_b = pd.DataFrame(
        {
            "player_id": df["PlayerB"].astype(str),
            "display_name": df["NameB"].astype(str),
            "_seq": seq,
        }
    )
    long_players = pd.concat([side_a, side_b], ignore_index=True)
    long_players.sort_values("_seq", kind="mergesort", inplace=True)
    players = (
        long_players.groupby("player_id", sort=False, as_index=False)
        .last()[["player_id", "display_name"]]
    )

    games = pd.DataFrame(
        {
            "game_id": df["GameID"].astype(str),
            "start_time": start.loc[df.index].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "player_a_id": df["PlayerA"].astype(str),
            "player_b_id": df["PlayerB"].astype(str),
            "score_a": df["_ScoreA_int"].astype("int64"),
            "score_b": df["_ScoreB_int"].astype("int64"),
            "duration_secs": df["_Duration_int"].astype("int64"),
        }
    )

    stats["games_imported"] = int(len(games))
    stats["players_distinct"] = int(len(players))

    return games, players, stats


def import_to_sqlite(
    json_path: Path,
    db_path: Path,
    *,
    overwrite: bool,
    schema_path: Path | None = None,
) -> dict[str, int]:
    """Create (or replace) the database and load tables. Returns summary stats."""
    if schema_path is None:
        schema_path = Path(__file__).resolve().parent / "schema.sql"

    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"{db_path} already exists. Pass --overwrite to rebuild, or choose another --db."
            )
        db_path.unlink()

    games, players, stats = load_and_prepare(json_path)

    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn, schema_path)
        players.to_sql("players", conn, if_exists="append", index=False)
        games.to_sql("games", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import retro_results.json into SQLite.")
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help=f"Path to retro_results.json (default: {DEFAULT_JSON_PATH})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Output SQLite path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete existing DB file before import.",
    )
    args = parser.parse_args(argv)

    if not args.json.is_file():
        print(f"Input JSON not found: {args.json}", file=sys.stderr)
        return 1

    try:
        stats = import_to_sqlite(args.json, args.db, overwrite=args.overwrite)
    except Exception as exc:  # noqa: BLE001 — CLI prints clean message
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print("Import complete.")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"Database: {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
