"""
Lightweight checks that Stage 1 import produced a consistent database.

Run from project root with PYTHONPATH=src:

    python -m kool_elo.verify_stage1
"""

from __future__ import annotations

import json
import sqlite3
import sys

from kool_elo.config import DEFAULT_DB_PATH, DEFAULT_JSON_PATH


def _self_match_row(r: dict) -> bool:
    return str(r["PlayerA"]) == str(r["PlayerB"])


def main() -> int:
    db_path = DEFAULT_DB_PATH
    json_path = DEFAULT_JSON_PATH

    if not db_path.is_file():
        print(f"Missing database: {db_path} (run import_matches first)", file=sys.stderr)
        return 1
    if not json_path.is_file():
        print(f"Missing JSON: {json_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            print(f"integrity_check failed: {integrity}", file=sys.stderr)
            return 1

        try:
            fk_rows = list(conn.execute("PRAGMA foreign_key_check"))
        except sqlite3.OperationalError as exc:
            fk_rows = None
            print(f"WARN: foreign_key_check unavailable ({exc})")

        if fk_rows:
            print(f"Foreign key violations: {fk_rows[:5]}", file=sys.stderr)
            return 1

        ng, ndistinct = conn.execute(
            "select count(*), count(distinct game_id) from games"
        ).fetchone()
        if ng != ndistinct:
            print("Duplicate game_id rows detected", file=sys.stderr)
            return 1
        pp = conn.execute("select count(*) from players").fetchone()[0]

        orphan = conn.execute(
            """
            select count(*) from games g
            left join players pa on pa.player_id = g.player_a_id
            left join players pb on pb.player_id = g.player_b_id
            where pa.player_id is null or pb.player_id is null
            """
        ).fetchone()[0]
        if orphan:
            print(f"Orphan game rows referencing missing players: {orphan}", file=sys.stderr)
            return 1

        bad_scores = conn.execute(
            "select count(*) from games where score_a < 0 or score_b < 0"
        ).fetchone()[0]
        if bad_scores:
            print(f"Negative scores: {bad_scores}", file=sys.stderr)
            return 1

        bad_dur = conn.execute(
            "select count(*) from games where duration_secs < 0"
        ).fetchone()[0]
        if bad_dur:
            print(f"Negative duration: {bad_dur}", file=sys.stderr)
            return 1

        # Insert order from pandas is rowid order; should match (start_time, game_id) sort.
        order_viol = conn.execute(
            """
            with o as (
              select rowid, start_time, game_id,
                     lag(start_time || char(31) || game_id) over (order by rowid) as prev_key
              from games
            )
            select count(*) from o
            where prev_key is not null
              and (start_time || char(31) || game_id) < prev_key
            """
        ).fetchone()[0]

    finally:
        conn.close()

    with json_path.open(encoding="utf-8") as f:
        raw = json.load(f)

    json_total = len(raw)
    json_ex_self = sum(1 for r in raw if not _self_match_row(r))

    need_ids: set[str] = set()
    for r in raw:
        if _self_match_row(r):
            continue
        need_ids.add(str(r["PlayerA"]))
        need_ids.add(str(r["PlayerB"]))

    conn = sqlite3.connect(db_path)
    try:
        db_ids = {row[0] for row in conn.execute("select player_id from players")}
    finally:
        conn.close()

    ok = True
    if ng != json_ex_self:
        print(f"Game count mismatch: db={ng} json_ex_self={json_ex_self}", file=sys.stderr)
        ok = False
    if db_ids != need_ids:
        print(
            f"Player id set mismatch (db vs json-derived): "
            f"{len(db_ids)} ids vs expected {len(need_ids)}; "
            f"symmetric diff size {len(db_ids ^ need_ids)}",
            file=sys.stderr,
        )
        ok = False
    if order_viol != 0:
        print(f"WARN: Row insert order differs from lexical (time,game) order in {order_viol} step(s)")
        ok = False

    if fk_rows is None:
        fk_report = "n/a (unsupported)"
    else:
        fk_report = str(len(fk_rows))

    print(f"Stage 1 checks: OK={'yes' if ok else 'NO'}")
    print(f"  integrity_check: {integrity}")
    print(f"  games: {ng}")
    print(f"  players: {pp}")
    print(f"  json rows (total / excl_self): {json_total} / {json_ex_self}")
    print(f"  fk violations: {fk_report}")
    print(f"  insert_order vs lexical order violations: {order_viol}")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
