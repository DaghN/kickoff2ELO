"""Replay matches in chronological order and persist symmetric Elo ratings."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from kool_elo.config import BASE_RATING, DEFAULT_DB_PATH, K_FACTOR
from kool_elo.elo_core import update_pair_after_game
from kool_elo.schema_migrations import ensure_stage2_rating_columns

_BATCH = 4096


def rebuild_elo(
    conn: sqlite3.Connection,
    *,
    base_rating: float,
    k_factor: float,
    reset_snapshots: bool = True,
) -> tuple[int, dict[str, float]]:
    """
    Full recompute stored in SQLite.

    Returns `(games_processed, final_ratings_snapshot)`.

    Preconditions: migrations applied (`ensure_stage2_rating_columns`).
    """

    ratings: dict[str, float] = {
        str(pid): float(base_rating)
        for (pid,) in conn.execute("SELECT player_id FROM players").fetchall()
    }

    snapshots: list[tuple[float, float, float, float, str]] = []
    processed = 0

    conn.execute("BEGIN")
    try:
        if reset_snapshots:
            conn.execute(
                """
                UPDATE games
                   SET elo_a_before = NULL,
                       elo_b_before = NULL,
                       elo_a_after  = NULL,
                       elo_b_after  = NULL;
                """
            )

        conn.execute(
            "UPDATE players SET rating = ?;",
            (float(base_rating),),
        )

        rows = conn.execute(
            """
            SELECT game_id, player_a_id, player_b_id, score_a, score_b
            FROM games
            ORDER BY start_time, game_id;
            """
        ).fetchall()

        def flush_snapshots() -> None:
            nonlocal snapshots
            if not snapshots:
                return
            conn.executemany(
                """
                UPDATE games SET
                  elo_a_before = ?,
                  elo_b_before = ?,
                  elo_a_after = ?,
                  elo_b_after = ?
                WHERE game_id = ?;
                """,
                snapshots,
            )
            snapshots = []

        for gid, pid_a, pid_b, score_a, score_b in rows:
            pid_a = str(pid_a)
            pid_b = str(pid_b)
            gid = str(gid)

            ra = ratings[pid_a]
            rb = ratings[pid_b]

            ra_new, rb_new = update_pair_after_game(
                ra,
                rb,
                k=k_factor,
                score_a=int(score_a),
                score_b=int(score_b),
            )

            snapshots.append((ra, rb, ra_new, rb_new, gid))
            ratings[pid_a], ratings[pid_b] = ra_new, rb_new
            processed += 1

            if len(snapshots) >= _BATCH:
                flush_snapshots()

        flush_snapshots()

        conn.executemany(
            "UPDATE players SET rating = ? WHERE player_id = ?;",
            [(rating, pid) for pid, rating in ratings.items()],
        )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return processed, ratings


def _print_leaderboards(
    conn: sqlite3.Connection, ratings: dict[str, float], limit: int
) -> None:
    names = {
        str(pid): nm
        for pid, nm in conn.execute("SELECT player_id, display_name FROM players").fetchall()
    }

    ordered = sorted(
        ratings.items(),
        key=lambda item: (-item[1], names.get(item[0], item[0])),
    )

    rank_width = len(str(limit))
    print(f"\nTop {limit} (highest ratings):")
    for idx, (pid, elo) in enumerate(ordered[:limit], start=1):
        nm = names.get(pid, "?")
        print(f"  {idx:>{rank_width}}. {nm} ({pid}) — {elo:.1f}")

    print(f"\nBottom {limit} (lowest ratings):")
    weakest = sorted(
        ratings.items(),
        key=lambda item: (item[1], names.get(item[0], item[0])),
    )
    for idx, (pid, elo) in enumerate(weakest[:limit], start=1):
        nm = names.get(pid, "?")
        print(f"  {idx:>{rank_width}}. {nm} ({pid}) — {elo:.1f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild Elo ratings from games table.")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--base",
        dest="base_rating",
        type=float,
        default=BASE_RATING,
        help="Starting rating before replay (default matches config).",
    )
    parser.add_argument(
        "--k",
        dest="k_factor",
        type=float,
        default=K_FACTOR,
        help="Symmetric K-factor (default matches config).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print totals (skip leaderboard preview).",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()

    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_stage2_rating_columns(conn)
        processed, ratings = rebuild_elo(
            conn,
            base_rating=args.base_rating,
            k_factor=args.k_factor,
        )

        print(
            f"Processed {processed} games with BASE={args.base_rating} "
            f"K={args.k_factor}."
        )

        if not args.quiet:
            _print_leaderboards(conn, ratings, limit=15)
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"Elo rebuild failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
