"""Replay matches in chronological order and persist provisional dual-K Elo."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from kool_elo.config import BASE_RATING, DEFAULT_DB_PATH, K_FACTOR, PROVISIONAL_GAMES_FULL_THRESHOLD
from kool_elo.elo_core import update_pair_dual_k
from kool_elo.schema_migrations import ensure_elo_schema

_BATCH = 4096


def rebuild_elo(
    conn: sqlite3.Connection,
    *,
    base_rating: float,
    k_factor: float,
    reset_snapshots: bool = True,
) -> tuple[int, dict[str, float], dict[str, int]]:
    """
    Full recompute stored in SQLite.

    Returns ``(games_processed, final_ratings, final_games_played)``.
    """

    keys = [str(row[0]) for row in conn.execute("SELECT player_id FROM players").fetchall()]

    ratings: dict[str, float] = {pid: float(base_rating) for pid in keys}
    games_played: dict[str, int] = {pid: 0 for pid in keys}

    snapshots: list[tuple[float, float, float, float, float, float, str]] = []
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
                       elo_b_after  = NULL,
                       k_used_a     = NULL,
                       k_used_b     = NULL;
                """
            )

        conn.execute(
            """
            UPDATE players
               SET rating = ?,
                   games_played = 0;
            """,
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
                  elo_b_after = ?,
                  k_used_a = ?,
                  k_used_b = ?
                WHERE game_id = ?;
                """,
                snapshots,
            )
            snapshots = []

        for gid, pid_a, pid_b, score_a, score_b in rows:
            pid_a = str(pid_a)
            pid_b = str(pid_b)
            gid = str(gid)

            na = games_played[pid_a]
            nb = games_played[pid_b]
            ra = ratings[pid_a]
            rb = ratings[pid_b]

            ra_new, rb_new, ka, kb = update_pair_dual_k(
                ra,
                rb,
                base_k=k_factor,
                score_a=int(score_a),
                score_b=int(score_b),
                prematch_games_a=na,
                prematch_games_b=nb,
            )

            snapshots.append((ra, rb, ra_new, rb_new, ka, kb, gid))
            ratings[pid_a], ratings[pid_b] = ra_new, rb_new
            games_played[pid_a] = na + 1
            games_played[pid_b] = nb + 1
            processed += 1

            if len(snapshots) >= _BATCH:
                flush_snapshots()

        flush_snapshots()

        conn.executemany(
            "UPDATE players SET rating = ?, games_played = ? WHERE player_id = ?;",
            [(ratings[pid], games_played[pid], pid) for pid in ratings],
        )

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return processed, ratings, games_played


def _print_leaderboards(
    conn: sqlite3.Connection,
    ratings: dict[str, float],
    games_played: dict[str, int],
    limit: int,
    provisional_threshold: int,
) -> None:
    names = {
        str(pid): nm
        for pid, nm in conn.execute("SELECT player_id, display_name FROM players").fetchall()
    }

    def fmt_rating(pid: str, elo: float) -> str:
        provisional = games_played.get(pid, 0) < provisional_threshold
        suffix = "?" if provisional else ""
        return f"{elo:.1f}{suffix}"

    ordered = sorted(
        ratings.items(),
        key=lambda item: (-item[1], names.get(item[0], item[0])),
    )

    rank_width = len(str(limit))
    print(f"\nTop {limit} (highest ratings):")
    for idx, (pid, elo) in enumerate(ordered[:limit], start=1):
        nm = names.get(pid, "?")
        print(f"  {idx:>{rank_width}}. {nm} ({pid}) — {fmt_rating(pid, elo)}")

    print(f"\nBottom {limit} (lowest ratings):")
    weakest = sorted(
        ratings.items(),
        key=lambda item: (item[1], names.get(item[0], item[0])),
    )
    for idx, (pid, elo) in enumerate(weakest[:limit], start=1):
        nm = names.get(pid, "?")
        print(f"  {idx:>{rank_width}}. {nm} ({pid}) — {fmt_rating(pid, elo)}")


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
        help="Baseline K-factor scaling provisional math (default matches config).",
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
        ensure_elo_schema(conn)
        processed, ratings, games_counts = rebuild_elo(
            conn,
            base_rating=args.base_rating,
            k_factor=args.k_factor,
        )

        print(
            f"Processed {processed} games with BASE={args.base_rating}, "
            f"K_BASE={args.k_factor}, provisional cutoff=<{PROVISIONAL_GAMES_FULL_THRESHOLD} games."
        )

        if not args.quiet:
            _print_leaderboards(
                conn,
                ratings,
                games_counts,
                limit=15,
                provisional_threshold=PROVISIONAL_GAMES_FULL_THRESHOLD,
            )
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"Elo rebuild failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
