"""Fetch Kick Off JSON result dumps from the community site.

The upstream server (as of testing) omits ``ETag`` / ``Last-Modified`` headers, so
each sync still **downloads the full JSON body** to detect changes. We **skip**
merging, JSON rewrites, and (optionally) DB rebuilds when the payload SHA-256
matches the last run; otherwise we merge unseen ``GameID`` rows with local data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kool_elo.config import (
    DATA_DIR,
    DEFAULT_JSON_PATH,
    PROJECT_ROOT,
    REMOTE_RESULTS_MANIFEST_PATH,
    resolved_remote_results_url,
)

_FETCH_TIMEOUT_SEC = float(os.environ.get("KOOL_RESULTS_FETCH_TIMEOUT", "300"))
_USER_AGENT = "KoolElo-results-sync/1.0 (local dev; +https://github.com/)"


@dataclass
class SyncStats:
    remote_rows: int
    local_rows_before: int
    added_ids: int
    updated_rows: int
    merged_rows: int
    payload_unchanged: bool
    wrote_file: bool


def _read_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return [row for row in data if isinstance(row, dict)]


def _atomic_write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(path)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_manifest() -> dict[str, Any]:
    if not REMOTE_RESULTS_MANIFEST_PATH.is_file():
        return {}
    try:
        return json.loads(REMOTE_RESULTS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_manifest(blob: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REMOTE_RESULTS_MANIFEST_PATH.write_text(
        json.dumps(blob, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _http_get(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SEC) as response:
        return response.read()


def _merge_rows(
    local_rows: list[dict[str, Any]],
    remote_rows: list[dict[str, Any]],
    *,
    replace_local: bool,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Merge by ``GameID``, preferring the row with the lexicographically latest
    ``StartTime`` when duplicates disagree.
    """

    if replace_local:
        bucket: dict[str, dict[str, Any]] = {}
        for row in remote_rows:
            bucket[str(row["GameID"])] = row
        merged = list(bucket.values())
        return merged, len(merged), 0  # second value = rows in remote snapshot

    bucket = {str(row["GameID"]): dict(row) for row in local_rows}
    added = updated = 0

    for row in remote_rows:
        gid = str(row["GameID"])
        if gid not in bucket:
            bucket[gid] = dict(row)
            added += 1
            continue
        cur = bucket[gid]
        cur_time = str(cur.get("StartTime", ""))
        new_time = str(row.get("StartTime", ""))
        if new_time > cur_time:
            bucket[gid] = dict(row)
            updated += 1

    merged = sorted(
        bucket.values(),
        key=lambda r: (str(r.get("StartTime", "")), str(r.get("GameID", ""))),
    )
    return merged, added, updated


def sync_remote_results(
    *,
    url: str,
    out_path: Path,
    replace_local: bool,
    force_fetch: bool,
) -> SyncStats:
    manifest = _load_manifest()
    if manifest.get("remote_url") != url:
        manifest = {"remote_url": url}

    raw = _http_get(url)
    digest = _sha256_hex(raw)
    if (
        not force_fetch
        and digest == manifest.get("last_body_sha256")
        and out_path.is_file()
    ):
        return SyncStats(
            remote_rows=0,
            local_rows_before=len(_read_json_array(out_path)),
            added_ids=0,
            updated_rows=0,
            merged_rows=len(_read_json_array(out_path)),
            payload_unchanged=True,
            wrote_file=False,
        )

    remote = json.loads(raw.decode("utf-8"))
    if not isinstance(remote, list):
        raise ValueError("Remote payload is not a JSON array.")
    remote_rows = [row for row in remote if isinstance(row, dict)]
    for row in remote_rows:
        if "GameID" not in row or "StartTime" not in row:
            raise ValueError("Remote rows must include GameID and StartTime.")

    local_rows = [] if replace_local else _read_json_array(out_path)
    merged, added, updated = _merge_rows(local_rows, remote_rows, replace_local=replace_local)

    wrote_file = True
    if not replace_local and added == 0 and updated == 0 and len(merged) == len(local_rows):
        # Nothing structurally new; still refresh manifest hash for bookkeeping.
        wrote_file = False

    if wrote_file:
        _atomic_write_json(out_path, merged)

    manifest.update(
        {
            "remote_url": url,
            "last_body_sha256": digest,
            "last_sync_utc": datetime.now(timezone.utc).isoformat(),
            "last_remote_count": len(remote_rows),
            "last_merged_count": len(merged),
            "last_added": added,
            "last_updated": updated,
        }
    )
    _save_manifest(manifest)

    return SyncStats(
        remote_rows=len(remote_rows),
        local_rows_before=len(local_rows),
        added_ids=added,
        updated_rows=updated,
        merged_rows=len(merged),
        payload_unchanged=False,
        wrote_file=wrote_file,
    )


def apply_import_and_elo() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    subprocess.run(
        [sys.executable, "-m", "kool_elo.import_matches", "--overwrite"],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "kool_elo.compute_elo", "--quiet"],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download community JSON results, merge into retro_results.json, optionally rebuild DB."
    )
    parser.add_argument(
        "--url",
        default=resolved_remote_results_url(),
        help="Remote JSON endpoint (default: config / env KOOL_REMOTE_RESULTS_URL).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help="Local JSON path to write (default: project retro_results.json).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Ignore existing local rows; remote snapshot becomes the entire file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Even if the remote payload hash matches the last sync, re-merge.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="After a successful write, run import_matches --overwrite + compute_elo.",
    )
    args = parser.parse_args(argv)

    try:
        stats = sync_remote_results(
            url=args.url.strip(),
            out_path=args.out,
            replace_local=args.replace,
            force_fetch=args.force,
        )
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1

    if stats.payload_unchanged:
        print("Remote payload identical to last sync (sha256). Nothing to merge.")
        return 0

    print(
        "Sync summary:",
        f"remote_rows={stats.remote_rows}",
        f"local_before={stats.local_rows_before}",
        f"added={stats.added_ids}",
        f"updates={stats.updated_rows}",
        f"merged={stats.merged_rows}",
        f"wrote_json={stats.wrote_file}",
        sep="\n  ",
    )
    print(f"Wrote manifest: {REMOTE_RESULTS_MANIFEST_PATH}")

    if args.apply:
        if not stats.wrote_file and stats.added_ids == 0 and stats.updated_rows == 0:
            print("`--apply` skipped (no filesystem changes).")
            return 0
        print("Running import_matches + compute_elo …")
        try:
            apply_import_and_elo()
        except subprocess.CalledProcessError as exc:
            print(f"Pipeline failed: {exc}", file=sys.stderr)
            return exc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
