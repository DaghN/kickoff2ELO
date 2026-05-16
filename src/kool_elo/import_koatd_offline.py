"""Build standalone offline SQLite (`players`/`games`) from KOATD CSV exports.

Uses the same ``schema.sql`` layout as online retro imports. Tournament dates come from
the ``Tournament players`` CSV; fixtures from ``Scores``.

Example::

    PYTHONPATH=src python -m kool_elo.import_koatd_offline --overwrite

Then::

    PYTHONPATH=src python -m kool_elo.compute_elo --db data/offline_koatd.sqlite3

Ordering heuristic: tournament ``Date`` gives the calendar day; games inside a tournament
use ``Scores.ID`` for stable ordering inside the pseudo-timestamps (+0,+1,+2… seconds).

``duration_secs`` is ``0`` (offline dump has no durations).
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from kool_elo.import_matches import apply_schema
from kool_elo.schema_migrations import ensure_elo_schema, refresh_last_game_at

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCORES_CSV = PROJECT_ROOT / "data" / "koatd_scores_export.csv"
DEFAULT_TOURNAMENTS_CSV = PROJECT_ROOT / "data" / "koatd_tournament_players_export.csv"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "offline_koatd.sqlite3"


def _player_id_stub(display_name: str) -> str:
    """Stable surrogate id from normalised spelling (offline-only identifiers)."""

    norm = " ".join(str(display_name).strip().split()).lower().encode("utf-8")
    return "oko2_" + hashlib.sha256(norm).hexdigest()[:22]


def _resolve_tournament(
    raw: str,
    date_by_name: dict[str, pd.Timestamp],
) -> tuple[str | None, str | None]:
    """Exact match or longest canonical prefix boundary (Roman-numeral safe-ish)."""

    raw = raw.strip()
    if raw in date_by_name:
        dt = date_by_name[raw]
        return raw, dt

    names = sorted(date_by_name.keys(), key=len, reverse=True)
    for base in names:
        if raw == base:
            return base, date_by_name[base]
        if not raw.startswith(base):
            continue
        if len(raw) > len(base):
            next_ch = raw[len(base)]
            if next_ch.isalnum():
                continue
        return base, date_by_name[base]

    return None, None


def load_koatd_csvs(
    scores_csv: Path,
    tournaments_csv: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    scores = pd.read_csv(scores_csv)
    tournaments = pd.read_csv(tournaments_csv)

    need_s = {"ID", "Team A", "Team B", "A", "B", "Tournament"}
    miss_s = need_s.difference(scores.columns)
    if miss_s:
        raise ValueError(f"Scores CSV missing columns: {sorted(miss_s)}")

    need_t = {"Tournament", "Date"}
    miss_t = need_t.difference(tournaments.columns)
    if miss_t:
        raise ValueError(f"Tournament players CSV missing columns: {sorted(miss_t)}")

    if tournaments["Tournament"].duplicated().any():
        raise ValueError("Tournament CSV has duplicated Tournament rows — dedupe exports.")

    dt = pd.to_datetime(tournaments["Date"], errors="coerce")

    invalid_tm = dt.isna()
    if invalid_tm.any():
        bad_rows = tournaments.loc[invalid_tm, "Tournament"].tolist()[:15]
        raise ValueError(
            "Invalid Tournament `Date` on "
            f"{int(invalid_tm.sum())} row(s). Examples: {bad_rows}"
        )

    date_by_name: dict[str, pd.Timestamp] = {}
    for nm, ts in zip(
        tournaments["Tournament"].astype(str).map(str.strip), dt, strict=True
    ):
        date_by_name[nm] = ts

    self_mask = (
        scores["Team A"].astype(str).map(str.strip)
        == scores["Team B"].astype(str).map(str.strip)
    )
    skipped_self = int(self_mask.sum())
    sc = scores.loc[~self_mask].copy()

    canon_rows: list[str] = []
    ts_rows: list[pd.Timestamp] = []
    bad_rows = 0
    for raw_tm in sc["Tournament"].astype(str):
        c, event_ts = _resolve_tournament(raw_tm, date_by_name)
        if c is None or event_ts is None:
            canon_rows.append("")
            ts_rows.append(pd.NaT)
            bad_rows += 1
        else:
            canon_rows.append(c)
            ts_rows.append(event_ts)

    sc["_tournament_canon"] = canon_rows
    sc["_event_day"] = ts_rows

    if bad_rows:
        sc_good = sc.loc[sc["_event_day"].notna()].copy()
    else:
        sc_good = sc

    anchor = sc_good["_event_day"].dt.normalize()
    sc_good["_pseudo_ts"] = anchor + pd.to_timedelta(
        sc_good.groupby("Tournament").cumcount().astype("int64"),
        unit="s",
    )
    sc_good.sort_values(["_pseudo_ts", "ID"], kind="mergesort", inplace=True)

    seq = pd.RangeIndex(len(sc_good))

    side_a = pd.DataFrame(
        {
            "player_id_stub": [_player_id_stub(x) for x in sc_good["Team A"].astype(str)],
            "display_name": sc_good["Team A"].astype(str).map(lambda s: str(s).strip()),
            "_seq": seq,
        }
    )
    side_b = pd.DataFrame(
        {
            "player_id_stub": [_player_id_stub(x) for x in sc_good["Team B"].astype(str)],
            "display_name": sc_good["Team B"].astype(str).map(lambda s: str(s).strip()),
            "_seq": seq,
        }
    )

    long_p = pd.concat([side_a, side_b], ignore_index=True)
    long_p.sort_values("_seq", kind="mergesort", inplace=True)
    players = (
        long_p.groupby("player_id_stub", sort=False, as_index=False)
        .last()[["player_id_stub", "display_name"]]
        .rename(columns={"player_id_stub": "player_id"})
    )

    sa = pd.to_numeric(sc_good["A"], errors="coerce").astype("Int64")
    sb = pd.to_numeric(sc_good["B"], errors="coerce").astype("Int64")
    bad_nums = sa.isna() | sb.isna()
    if bad_nums.any():
        raise ValueError(f"Non-numeric offline scores on {int(bad_nums.sum())} row(s).")

    if ((sa < 0) | (sb < 0)).any():
        raise ValueError("Negative offline scores.")

    pid_a = [_player_id_stub(x) for x in sc_good["Team A"].astype(str)]
    pid_b = [_player_id_stub(x) for x in sc_good["Team B"].astype(str)]

    games = pd.DataFrame(
        {
            "game_id": sc_good["ID"].astype(str),
            "start_time": sc_good["_pseudo_ts"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "player_a_id": pid_a,
            "player_b_id": pid_b,
            "score_a": sa.astype("int64"),
            "score_b": sb.astype("int64"),
            "duration_secs": 0,
        }
    )

    stats = {
        "scores_rows_read": len(scores),
        "scores_rows_kept_after_self_skip": len(sc),
        "skipped_self_matches": skipped_self,
        "skipped_unresolved_games_no_tournament_match": bad_rows,
        "games_imported": len(games),
        "players_distinct": len(players),
    }

    return games, players, stats


def import_koatd_to_sqlite(
    scores_csv: Path,
    tournaments_csv: Path,
    db_path: Path,
    *,
    overwrite: bool,
    schema_path: Path | None = None,
) -> dict[str, int]:
    """Create offline SQLite identical shape to retro import pipeline."""

    if schema_path is None:
        schema_path = Path(__file__).resolve().parent / "schema.sql"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"{db_path} exists; pass --overwrite or choose another --db."
            )
        db_path.unlink()

    games, players, stats = load_koatd_csvs(scores_csv, tournaments_csv)

    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn, schema_path)
        players.to_sql("players", conn, if_exists="append", index=False)
        games.to_sql("games", conn, if_exists="append", index=False)
        ensure_elo_schema(conn)
        refresh_last_game_at(conn)
        conn.commit()
    finally:
        conn.close()

    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import KOATD CSV exports into offline SQLite (retro-compatible schema)."
    )
    parser.add_argument(
        "--scores",
        type=Path,
        default=DEFAULT_SCORES_CSV,
        help=f"Scores CSV path (default: {DEFAULT_SCORES_CSV})",
    )
    parser.add_argument(
        "--tournaments",
        type=Path,
        default=DEFAULT_TOURNAMENTS_CSV,
        help=f"Tournament CSV path (default: {DEFAULT_TOURNAMENTS_CSV})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Output SQLite path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument("--overwrite", action="store_true", help="Rebuild DB.")
    args = parser.parse_args(argv)

    if not args.scores.is_file():
        print(f"Scores CSV missing: {args.scores}", file=sys.stderr)
        return 1
    if not args.tournaments.is_file():
        print(f"Tournament CSV missing: {args.tournaments}", file=sys.stderr)
        return 1

    try:
        stats = import_koatd_to_sqlite(
            args.scores,
            args.tournaments,
            args.db,
            overwrite=args.overwrite,
        )
    except Exception as exc:  # noqa: BLE001 — CLI
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1

    print("Offline KOATD import complete.")
    for key, val in sorted(stats.items()):
        print(f"  {key}: {val}")
    print(f"Database: {args.db}")
    print("Next (optional): python -m kool_elo.compute_elo --db " + str(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
