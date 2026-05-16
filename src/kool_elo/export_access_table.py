"""Export a Microsoft Access table to CSV using ODBC (Jet / ACE).

Requires Windows ODBC drivers (often from "Microsoft Access Database Engine"
redistributable). Python **bitness must match** the driver (64-bit Python →
64-bit ACE).

Examples (from project root, PYTHONPATH including src):

    pip install -r requirements-access.txt

    python -m kool_elo.export_access_table --list-tables --mdb data/offline.mdb
    python -m kool_elo.export_access_table --mdb data/offline.mdb --table Results \\
        --out data/offline_results_raw.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_CONN_DRIVERS_JET_ACE = (
    r"Driver={Microsoft Access Driver (*.mdb, *.accdb)};",
    r"Driver={Microsoft Access Driver (*.mdb)};",
)


def _connect_mdb(mdb_path: Path) -> object:
    """Return a pyodbc connection or raise with a helpful message."""

    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - env specific
        raise SystemExit(
            "Missing pyodbc. Install with: pip install -r requirements-access.txt"
        ) from exc

    if not mdb_path.is_file():
        raise SystemExit(f"File not found: {mdb_path.resolve()}")

    dbq = str(mdb_path.resolve())
    last_exc: Exception | None = None
    for drv in _CONN_DRIVERS_JET_ACE:
        conn_str = f"{drv}DBQ={dbq};"
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.Error as e:  # noqa: PERF203 — try fallback drivers
            last_exc = e
    raise SystemExit(f"Could not connect to Access file. Last ODBC error:\n  {last_exc}")


def _list_tables(conn: object) -> list[str]:
    names: list[str] = []
    for row in conn.cursor().tables(tableType="TABLE"):  # type: ignore[union-attr]
        name = getattr(row, "table_name", None) or row[2]
        if not name or str(name).startswith("MSys") or str(name).startswith("~"):
            continue
        names.append(str(name))
    return sorted(set(names))


def _export_csv(conn: object, table: str, out_path: Path) -> None:

    cur = conn.cursor()
    query = _quote_ident_sql_server_style(table)

    rows = cur.execute(f"SELECT * FROM {query}")  # noqa: S608 — constrained to user table arg
    colnames = [c[0] for c in cur.description] if cur.description else []

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(colnames)
        chunk = rows.fetchmany(5000)
        while chunk:
            writer.writerows(chunk)
            chunk = rows.fetchmany(5000)


def _quote_ident_sql_server_style(ident: str) -> str:
    """Quote table name [like this]; reject obvious injection-ish input."""

    bad_chars = set('[];\r\n"\'`')
    if any(c in ident for c in bad_chars):
        raise SystemExit("Table name contains disallowed characters.")
    return f"[{ident}]"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export one Access (.mdb/.accdb) table to CSV via ODBC.",
    )
    parser.add_argument(
        "--mdb",
        type=Path,
        required=True,
        help="Path to .mdb or .accdb file",
    )
    parser.add_argument(
        "--table",
        type=str,
        default="",
        help="Table name to export",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output CSV path (required unless --list-tables)",
    )
    parser.add_argument(
        "--list-tables",
        action="store_true",
        help="Print accessible user table names and exit",
    )
    args = parser.parse_args(argv)

    conn = _connect_mdb(args.mdb)

    try:
        if args.list_tables:
            for name in _list_tables(conn):
                print(name)
            return 0

        if not args.table.strip():
            print("Specify --table NAME or use --list-tables.", file=sys.stderr)
            return 2

        if args.out is None:
            print("Specify --out path/to/file.csv when exporting.", file=sys.stderr)
            return 2

        _export_csv(conn, args.table.strip(), args.out.resolve())
        print(f"Wrote {args.out.resolve()} ({args.table})", file=sys.stderr)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
