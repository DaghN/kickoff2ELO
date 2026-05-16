"""
Report player counts and average *current* rating vs. last game time.

- Default: cumulative inactive tiers (1+ / 2+ / … years since last game).
- ``--min-games N``: restrict to ``players.games_played >= N`` (e.g. 20 drops provisional histories).

Last game times come from ``games`` (ground truth); ``players.last_game_at`` is
refreshed when you run this script (and after each Elo replay).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from kool_elo.config import DEFAULT_DB_PATH
from kool_elo.schema_migrations import ensure_elo_schema, refresh_last_game_at


def _subtract_calendar_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


def _run_report(
    conn: sqlite3.Connection,
    *,
    as_of: date,
    max_years: int,
    min_games_played: int,
) -> None:
    """
    For N = 1 .. max_years: players with at least one game whose last game
    (calendar date) is on or before (as_of minus N years).

    Also prints “all players” using everyone in ``players`` (current ratings).
    """

    cutoffs = {n: _subtract_calendar_years(as_of, n) for n in range(1, max_years + 1)}

    # Dynamic CASE columns for each year tier
    case_parts = []
    for n in range(1, max_years + 1):
        case_parts.append(
            f"SUM(CASE WHEN p.last_start IS NOT NULL AND "
            f"date(p.last_start) <= date(?) THEN 1 ELSE 0 END) AS n_ge_{n}y"
        )
        case_parts.append(
            f"AVG(CASE WHEN p.last_start IS NOT NULL AND "
            f"date(p.last_start) <= date(?) THEN p.rating END) AS avg_ge_{n}y"
        )

    case_sql = ",\n       ".join(case_parts)

    params: list[object] = [min_games_played]
    for n in range(1, max_years + 1):
        c = cutoffs[n].isoformat()
        params.extend([c, c])

    sql = f"""
WITH last_games AS (
  SELECT u.player_id, MAX(u.start_time) AS last_start
    FROM (
          SELECT player_a_id AS player_id, start_time FROM games
          UNION ALL
          SELECT player_b_id, start_time FROM games
         ) AS u
   GROUP BY u.player_id
),
base AS (
  SELECT pl.player_id, pl.rating, lg.last_start
    FROM players pl
    LEFT JOIN last_games lg ON lg.player_id = pl.player_id
   WHERE COALESCE(pl.games_played, 0) >= ?
)
SELECT COUNT(*) AS n_all,
       AVG(p.rating) AS avg_all,
       {case_sql}
  FROM base p;
"""
    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    if row is None:
        print("No data.", file=sys.stderr)
        return

    names = [d[0] for d in cur.description]

    data = dict(zip(names, row, strict=True))

    print(f"As-of date (local): {as_of.isoformat()}")
    if min_games_played > 0:
        print(f"Included only players with games_played >= {min_games_played}.")
    print()
    w = 54
    print(f"{'Cohort':<{w}} {'Count':>8}  {'Avg rating':>12}")
    print("-" * (w + 8 + 12 + 2))
    n_all = int(data["n_all"])
    avg_all = data["avg_all"]
    avg_all_txt = f"{float(avg_all):.2f}" if avg_all is not None else "-"
    print(f"{'All players (current list)':<{w}} {n_all:>8}  {avg_all_txt:>12}")

    for n in range(1, max_years + 1):
        cn = int(data[f"n_ge_{n}y"])
        av = data[f"avg_ge_{n}y"]
        av_txt = f"{float(av):.2f}" if av is not None else "-"
        label = f"{n}+ year(s) inactive (last game date <= {cutoffs[n].isoformat()})"
        print(f"{label:<{w}} {cn:>8}  {av_txt:>12}")

    print()
    print(
        "Notes: inactive tiers include only players with >=1 game in `games`. "
        "Rating is `players.rating` (current list). "
        "Last game uses the calendar date of `MAX(start_time)` per player."
        + (
            f" Filter: games_played >= {min_games_played}."
            if min_games_played > 0
            else ""
        )
    )


def _run_bucket_report(
    conn: sqlite3.Connection,
    *,
    as_of: date,
    min_games_played: int,
) -> None:
    """
    Buckets k = 0..9: last game calendar date ``d`` with
    ``(as_of - (k+1) years) < d <= (as_of - k years)`` (lower bound exclusive).

    Players with last game on/before ``as_of - 10 years`` are omitted from all
    buckets (and listed as excluded). Players with no games are omitted.
    """

    bounds: list[tuple[date, date]] = []
    for k in range(10):
        hi = as_of if k == 0 else _subtract_calendar_years(as_of, k)
        lo = _subtract_calendar_years(as_of, k + 1)
        bounds.append((lo, hi))

    case_parts: list[str] = []
    params: list[object] = [min_games_played]
    for k, (lo, hi) in enumerate(bounds):
        case_parts.append(
            "SUM(CASE WHEN p.last_start IS NOT NULL AND "
            "date(p.last_start) > date(?) AND date(p.last_start) <= date(?) "
            f"THEN 1 ELSE 0 END) AS n_b{k}",
        )
        case_parts.append(
            "AVG(CASE WHEN p.last_start IS NOT NULL AND "
            "date(p.last_start) > date(?) AND date(p.last_start) <= date(?) "
            f"THEN p.rating END) AS avg_b{k}",
        )
        lo_s, hi_s = lo.isoformat(), hi.isoformat()
        params.extend([lo_s, hi_s, lo_s, hi_s])

    boundary_10 = _subtract_calendar_years(as_of, 10).isoformat()

    sql = f"""
WITH last_games AS (
  SELECT u.player_id, MAX(u.start_time) AS last_start
    FROM (
          SELECT player_a_id AS player_id, start_time FROM games
          UNION ALL
          SELECT player_b_id, start_time FROM games
         ) AS u
   GROUP BY u.player_id
),
base AS (
  SELECT pl.player_id, pl.rating, lg.last_start
    FROM players pl
    LEFT JOIN last_games lg ON lg.player_id = pl.player_id
   WHERE COALESCE(pl.games_played, 0) >= ?
)
SELECT {", ".join(case_parts)},
       SUM(CASE WHEN p.last_start IS NOT NULL
                 AND date(p.last_start) <= date(?) THEN 1 ELSE 0 END) AS n_excl_old
  FROM base p;
"""
    params.append(boundary_10)

    cur = conn.execute(sql, tuple(params))
    row = cur.fetchone()
    if row is None:
        print("No data.", file=sys.stderr)
        return

    names = [d[0] for d in cur.description]
    data = dict(zip(names, row, strict=True))

    print(f"As-of date (local): {as_of.isoformat()}")
    if min_games_played > 0:
        print(f"Included only players with games_played >= {min_games_played}.")
    print()
    w = 58
    print(f"{'Last game age (years)':<{w}} {'Count':>8}  {'Avg rating':>12}")
    print("-" * (w + 8 + 12 + 2))

    for k in range(10):
        lo, hi = bounds[k]
        cn = int(data[f"n_b{k}"])
        av = data[f"avg_b{k}"]
        av_txt = f"{float(av):.2f}" if av is not None else "-"
        if k == 0:
            label = f"0-1  ( {lo.isoformat()}  <  last <= {hi.isoformat()} )"
        else:
            label = f"{k}-{k+1}  ( {lo.isoformat()}  <  last <= {hi.isoformat()} )"
        print(f"{label:<{w}} {cn:>8}  {av_txt:>12}")

    excl = int(data["n_excl_old"])
    print("-" * (w + 8 + 12 + 2))
    print(
        f"{'Excluded (last on/before ' + boundary_10 + ', not in table above)':<{w}} "
        f"{excl:>8}  {'':>12}"
    )

    print()
    print(
        "Notes: bands use calendar dates of last game vs. as-of. "
        "Rating is `players.rating`. "
        "Players with no games are not counted above or in Excluded."
        + (
            f" Filter: games_played >= {min_games_played}."
            if min_games_played > 0
            else ""
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count players and average rating by years since last game."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite path")
    parser.add_argument(
        "--as-of",
        type=str,
        default="",
        help="Reference date YYYY-MM-DD (default: today's local date).",
    )
    parser.add_argument(
        "--max-years",
        type=int,
        default=9,
        help="(Default mode) Report inactive cohorts for 1 .. N years (default: 9).",
    )
    parser.add_argument(
        "--buckets",
        action="store_true",
        help="Report 0-1, 1-2, …, 9-10 year bands; ignore last game older than 10 years.",
    )
    parser.add_argument(
        "--min-games",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Require players.games_played >= N (default: 0). "
            "Use 20 to exclude provisional-sized histories."
        ),
    )
    args = parser.parse_args(argv)

    if args.min_games < 0:
        print("--min-games must be >= 0.", file=sys.stderr)
        return 1

    db_path = args.db.resolve()
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        print("Create it with: python -m kool_elo.import_matches --overwrite", file=sys.stderr)
        return 1

    if not args.buckets and args.max_years < 1:
        print("--max-years must be at least 1.", file=sys.stderr)
        return 1

    as_of = date.today()
    if args.as_of:
        as_of = date.fromisoformat(args.as_of)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_elo_schema(conn)
        refresh_last_game_at(conn)
        conn.commit()
        if args.buckets:
            _run_bucket_report(conn, as_of=as_of, min_games_played=args.min_games)
        else:
            _run_report(
                conn,
                as_of=as_of,
                max_years=args.max_years,
                min_games_played=args.min_games,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"Report failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
