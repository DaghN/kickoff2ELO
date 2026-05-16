"""Replay matches in chronological order and persist provisional dual-K Elo."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from kool_elo.config import (
    BASE_RATING,
    DEFAULT_DB_PATH,
    ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES,
    K_FACTOR,
    PROVISIONAL_DUAL_K_ENABLED,
    PROVISIONAL_GAMES_FULL_THRESHOLD,
)
from kool_elo.elo_core import update_pair_after_game, update_pair_dual_k
from kool_elo.schema_migrations import ensure_elo_schema, refresh_last_game_at

_BATCH = 4096


def _calendar_year_iso_ts(start_ts: str) -> int:
    """First four chars are ``YYYY`` for SQLite ``games.start_time`` format."""

    s = start_ts.strip()
    if len(s) < 4:
        raise ValueError(f"start_time too short for calendar year: {start_ts!r}")
    return int(s[:4])


def apply_established_mean_recalibration(
    ratings: dict[str, float],
    games_played: dict[str, int],
    peak_rating: dict[str, float | None],
    *,
    establishment_floor: int,
    target_mean: float,
) -> float | None:
    """
    Add the same delta to every player with ``games_played >= establishment_floor``
    so the cohort's arithmetic mean equals ``target_mean``. Also shifts stored
    ``peak_rating`` by delta when set. Returns ``delta``, or ``None`` if nobody
    is established yet.
    """

    estab = [pid for pid in ratings if games_played[pid] >= establishment_floor]
    if not estab:
        return None
    cur_mean = sum(ratings[p] for p in estab) / len(estab)
    delta = target_mean - cur_mean
    for pid in estab:
        ratings[pid] += delta
        pk = peak_rating[pid]
        if pk is not None:
            peak_rating[pid] = pk + delta
    return float(delta)


def rebuild_elo(
    conn: sqlite3.Connection,
    *,
    base_rating: float,
    k_factor: float,
    reset_snapshots: bool = True,
    report_annual_active_established: bool = False,
    establishment_games_floor: int = PROVISIONAL_GAMES_FULL_THRESHOLD,
    established_mass_recalibrate_every: int = 0,
    provisional_dual_k: bool | None = None,
) -> tuple[
    int,
    dict[str, float],
    dict[str, int],
    list[
        tuple[
            int,
            int,
            float | None,
            int,
            float | None,
            int,
            float | None,
        ]
    ],
    tuple[int, int],
]:
    """
    Full recompute stored in SQLite.

    Returns ``(games_processed, final_ratings, final_games_played, annual_rows, mass_stats)``.
    When ``report_annual_active_established`` is true, ``annual_rows`` has one row
    per closed calendar year after its last game:

    ``(year, n_played, avg_played, n_ge_floor, avg_ge_floor, n_active_est, avg_active_est)``

    - ``avg_played``: players with cumulative games ``> 0`` through that instant.
    - ``avg_ge_floor``: players with cumulative games ``>= establishment_games_floor``.
    - ``avg_active_est``: played at least once in ``year`` and same floor (original slice).

    Peak ladders ignore each player's outcomes until *that* player's cumulative
    games reach ``PROVISIONAL_GAMES_FULL_THRESHOLD``—only afterward do we accumulate
    ``peak_rating`` / ``peak_rating_at`` for them.

    When ``established_mass_recalibrate_every > 0``, after games ``N``, ``2N``, …
    processed, every player with ``games_played >= establishment_games_floor`` gets
    the same additive shift so their mean matches ``base_rating``; provisionals are
    untouched. Set ``established_mass_recalibrate_every`` to ``0`` to disable.
    ``mass_stats`` is ``(applied_count, milestones_with_zero_established)``.

    ``provisional_dual_k``: ``True`` = per-player forum K; ``False`` = both seats use
    ``k_factor``. ``None`` uses ``config.PROVISIONAL_DUAL_K_ENABLED``.
    """

    use_dual_k = (
        PROVISIONAL_DUAL_K_ENABLED if provisional_dual_k is None else provisional_dual_k
    )

    keys = [str(row[0]) for row in conn.execute("SELECT player_id FROM players").fetchall()]

    ratings: dict[str, float] = {pid: float(base_rating) for pid in keys}
    games_played: dict[str, int] = {pid: 0 for pid in keys}
    peak_rating: dict[str, float | None] = {pid: None for pid in keys}
    peak_when: dict[str, str | None] = {pid: None for pid in keys}

    snapshots: list[tuple[float, float, float, float, float, float, str]] = []
    processed = 0

    annual_rows: list[
        tuple[int, int, float | None, int, float | None, int, float | None]
    ] = []
    active_in_year: dict[int, set[str]] = defaultdict(set)

    mass_applied = 0
    mass_idle_milestones = 0

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
                   games_played = 0,
                   peak_rating = NULL,
                   peak_rating_at = NULL;
            """,
            (float(base_rating),),
        )

        rows = conn.execute(
            """
            SELECT game_id, player_a_id, player_b_id, score_a, score_b, start_time
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

        def update_peak(pid: str, rating_value: float, ts: str) -> None:
            prior = peak_rating[pid]
            if prior is None or rating_value > prior:
                peak_rating[pid] = rating_value
                peak_when[pid] = ts

        def append_year_close_if_needed(
            *, closed_year: int, row_index: int, start_ts: str
        ) -> None:
            if not report_annual_active_established:
                return
            next_y: int | None = None
            if row_index + 1 < len(rows):
                next_y = _calendar_year_iso_ts(str(rows[row_index + 1][5]))
            cur_y = _calendar_year_iso_ts(start_ts)
            if next_y is not None and next_y <= cur_y:
                return
            rs_played = [ratings[pid] for pid in keys if games_played[pid] > 0]
            n_played = len(rs_played)
            avg_played = sum(rs_played) / n_played if n_played else None

            rs_est = [
                ratings[pid]
                for pid in keys
                if games_played[pid] >= establishment_games_floor
            ]
            n_ge = len(rs_est)
            avg_ge = sum(rs_est) / n_ge if n_ge else None

            rs_act_est: list[float] = []
            for pid in active_in_year[closed_year]:
                if games_played[pid] >= establishment_games_floor:
                    rs_act_est.append(ratings[pid])
            n_ae = len(rs_act_est)
            avg_ae = sum(rs_act_est) / n_ae if n_ae else None

            annual_rows.append(
                (closed_year, n_played, avg_played, n_ge, avg_ge, n_ae, avg_ae)
            )

        for idx, (gid, pid_a, pid_b, score_a, score_b, start_ts) in enumerate(rows):
            pid_a = str(pid_a)
            pid_b = str(pid_b)
            gid = str(gid)
            start_ts = str(start_ts)

            y = _calendar_year_iso_ts(start_ts)
            active_in_year[y].add(pid_a)
            active_in_year[y].add(pid_b)

            na = games_played[pid_a]
            nb = games_played[pid_b]
            ra = ratings[pid_a]
            rb = ratings[pid_b]

            if use_dual_k:
                ra_new, rb_new, ka, kb = update_pair_dual_k(
                    ra,
                    rb,
                    base_k=k_factor,
                    score_a=int(score_a),
                    score_b=int(score_b),
                    prematch_games_a=na,
                    prematch_games_b=nb,
                )
            else:
                ra_new, rb_new = update_pair_after_game(
                    ra,
                    rb,
                    k=float(k_factor),
                    score_a=int(score_a),
                    score_b=int(score_b),
                )
                ka = kb = float(k_factor)

            snapshots.append((ra, rb, ra_new, rb_new, ka, kb, gid))
            ratings[pid_a], ratings[pid_b] = ra_new, rb_new
            games_played[pid_a] = na + 1
            games_played[pid_b] = nb + 1

            if games_played[pid_a] >= PROVISIONAL_GAMES_FULL_THRESHOLD:
                update_peak(pid_a, ra_new, start_ts)
            if games_played[pid_b] >= PROVISIONAL_GAMES_FULL_THRESHOLD:
                update_peak(pid_b, rb_new, start_ts)

            processed += 1

            if (
                established_mass_recalibrate_every > 0
                and processed % established_mass_recalibrate_every == 0
            ):
                delta_m = apply_established_mean_recalibration(
                    ratings,
                    games_played,
                    peak_rating,
                    establishment_floor=establishment_games_floor,
                    target_mean=float(base_rating),
                )
                if delta_m is None:
                    mass_idle_milestones += 1
                else:
                    mass_applied += 1

            if len(snapshots) >= _BATCH:
                flush_snapshots()

            append_year_close_if_needed(closed_year=y, row_index=idx, start_ts=start_ts)

        flush_snapshots()

        payload = []
        for pid in ratings:
            games_total = games_played[pid]
            pr = peak_rating[pid]
            pw = peak_when[pid]
            if games_total < PROVISIONAL_GAMES_FULL_THRESHOLD:
                pr = None
                pw = None
            payload.append(
                (
                    ratings[pid],
                    games_total,
                    pr,
                    pw,
                    pid,
                )
            )

        conn.executemany(
            """
            UPDATE players
               SET rating = ?,
                   games_played = ?,
                   peak_rating = ?,
                   peak_rating_at = ?
             WHERE player_id = ?;
            """,
            payload,
        )

        refresh_last_game_at(conn)

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return processed, ratings, games_played, annual_rows, (mass_applied, mass_idle_milestones)


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

    peak_rows = conn.execute(
        "SELECT player_id, peak_rating, peak_rating_at FROM players"
    ).fetchall()
    peaks = {str(pid): (peak_val, peak_time) for pid, peak_val, peak_time in peak_rows}

    rank_width = len(str(limit))
    print(f"\nTop {limit} (highest ratings):")
    for idx, (pid, elo) in enumerate(ordered[:limit], start=1):
        nm = names.get(pid, "?")
        pk = peaks.get(pid, (None, None))
        peak_txt = ""
        if pk[0] is not None:
            peak_txt = f"; peak {float(pk[0]):.1f}"
            if pk[1]:
                peak_txt += f" @ {pk[1]}"
        print(f"  {idx:>{rank_width}}. {nm} — {fmt_rating(pid, elo)}{peak_txt}")

    print(f"\nBottom {limit} (lowest ratings):")
    weakest = sorted(
        ratings.items(),
        key=lambda item: (item[1], names.get(item[0], item[0])),
    )
    for idx, (pid, elo) in enumerate(weakest[:limit], start=1):
        nm = names.get(pid, "?")
        pk = peaks.get(pid, (None, None))
        peak_txt = ""
        if pk[0] is not None:
            peak_txt = f"; peak {float(pk[0]):.1f}"
            if pk[1]:
                peak_txt += f" @ {pk[1]}"
        print(f"  {idx:>{rank_width}}. {nm} — {fmt_rating(pid, elo)}{peak_txt}")


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
    parser.add_argument(
        "--annual-active-established-avg",
        action="store_true",
        dest="annual_active_established_avg",
        help=(
            "After replay, print per calendar year: avg rating for (1) players who have "
            "played >=1 cumulative game through that year, "
            f"(2) players with >={PROVISIONAL_GAMES_FULL_THRESHOLD} cumulative games, "
            f"(3) players active that year with the same floor; snapshot after last game that year."
        ),
    )
    parser.add_argument(
        "--established-mass-recalibrate-every",
        type=int,
        default=None,
        metavar="N",
        help=(
            "After every N globally processed games, add the same delta to all players with "
            f">={PROVISIONAL_GAMES_FULL_THRESHOLD} games so their mean equals --base (0=off). "
            f"Default: config ({ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES})."
        ),
    )
    dual_grp = parser.add_mutually_exclusive_group()
    dual_grp.add_argument(
        "--symmetric-k",
        action="store_true",
        help="Both players use --k every game (disable provisional dual-K ramps).",
    )
    dual_grp.add_argument(
        "--provisional-dual-k",
        action="store_true",
        help=(
            "Use per-player provisional K (forum factors). "
            "Default follows PROVISIONAL_DUAL_K_ENABLED in config."
        ),
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()

    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    mass_interval = args.established_mass_recalibrate_every
    if mass_interval is None:
        mass_interval = ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES
    if mass_interval < 0:
        print("--established-mass-recalibrate-every must be >= 0.", file=sys.stderr)
        return 1

    if args.symmetric_k:
        pk_dual = False
    elif args.provisional_dual_k:
        pk_dual = True
    else:
        pk_dual = PROVISIONAL_DUAL_K_ENABLED

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_elo_schema(conn)
        processed, ratings, games_counts, annual_rows, mass_stats = rebuild_elo(
            conn,
            base_rating=args.base_rating,
            k_factor=args.k_factor,
            report_annual_active_established=args.annual_active_established_avg,
            establishment_games_floor=PROVISIONAL_GAMES_FULL_THRESHOLD,
            established_mass_recalibrate_every=mass_interval,
            provisional_dual_k=pk_dual,
        )

        extras = ""
        if mass_interval > 0:
            applied, idle_m = mass_stats
            extras = (
                f"; mass-recalibrate established every {mass_interval} games "
                f"({applied} shifts, {idle_m} milestones with no established players)"
            )
        k_extras = "; dual-K (provisional ramps)" if pk_dual else "; symmetric K=--k both sides"
        print(
            f"Processed {processed} games with BASE={args.base_rating}, "
            f"K_BASE={args.k_factor}, provisional cutoff=<{PROVISIONAL_GAMES_FULL_THRESHOLD} games{extras}{k_extras}."
        )

        if args.annual_active_established_avg and annual_rows:
            g = PROVISIONAL_GAMES_FULL_THRESHOLD
            print(
                "\nYear-end snapshots (after last listed game that year):\n"
                "  - played: cumulative games > 0 through that instant\n"
                f"  - est: >={g} cumulative games\n"
                "  - act+est: >=1 game that calendar year and est\n"
            )
            yw, nw, aw = 6, 5, 10
            hdr = (
                f"{'Year':>{yw}}  "
                f"{'N':>{nw}} {'Avg played':>{aw}}  "
                f"{'N':>{nw}} {'Avg est':>{aw}}  "
                f"{'N':>{nw}} {'Avg act+est':>{aw}}"
            )
            print(hdr)
            print("-" * len(hdr))
            for (
                yr,
                n_played,
                avg_played,
                n_ge,
                avg_ge,
                n_ae,
                avg_ae,
            ) in annual_rows:
                a1 = f"{avg_played:.2f}" if avg_played is not None else "-"
                a2 = f"{avg_ge:.2f}" if avg_ge is not None else "-"
                a3 = f"{avg_ae:.2f}" if avg_ae is not None else "-"
                print(
                    f"{yr:>{yw}}  "
                    f"{n_played:>{nw}} {a1:>{aw}}  "
                    f"{n_ge:>{nw}} {a2:>{aw}}  "
                    f"{n_ae:>{nw}} {a3:>{aw}}"
                )
            print(
                f"\n(act+est: played >=1 game in that calendar year and >={g} cumulative games.)"
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
