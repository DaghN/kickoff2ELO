"""
Kick Off 2 ELO ratings — Streamlit dashboard.

Run from project root:

    pip install -r requirements.txt
    streamlit run dashboard.py
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kool_elo.config import (
    BASE_RATING,
    DATA_DIR,
    DEFAULT_DB_PATH,
    DEFAULT_JSON_PATH,
    ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES,
    K_FACTOR,
    PROVISIONAL_DUAL_K_ENABLED,
    PROVISIONAL_GAMES_FULL_THRESHOLD,
    PROJECT_ROOT,
    resolved_remote_results_url,
)

# Support older `config.py` copies that predate `AMIGA500_DB_PATH` (avoids ImportError).
import kool_elo.config as _kool_config

AMIGA500_DB_PATH = getattr(
    _kool_config,
    "AMIGA500_DB_PATH",
    getattr(_kool_config, "OFFLINE_KOATD_DB_PATH", _kool_config.DATA_DIR / "offline_koatd.sqlite3"),
)
KOATD_SCORES_EXPORT_CSV = getattr(
    _kool_config,
    "KOATD_SCORES_EXPORT_CSV",
    _kool_config.DATA_DIR / "koatd_scores_export.csv",
)
KOATD_TOURNAMENTS_EXPORT_CSV = getattr(
    _kool_config,
    "KOATD_TOURNAMENTS_EXPORT_CSV",
    _kool_config.DATA_DIR / "koatd_tournament_players_export.csv",
)
from kool_elo.schema_migrations import ensure_elo_schema
from kool_elo.sync_remote_results import apply_import_and_elo, sync_remote_results


def _mirror_streamlit_secrets_to_environment() -> None:
    """Expose selected Streamlit Secrets as env vars so CLI subprocess pipelines see them."""

    mirrored = (
        "KOOL_REMOTE_RESULTS_URL",
        "KOOL_AUTO_SYNC_ON_START",
        "KOOL_AUTO_SYNC_APPLY",
        "KOOL_CLOUD_AUTO_BOOTSTRAP",
        "KOOL_RESULTS_FETCH_TIMEOUT",
    )
    try:
        sec = st.secrets
        for key in mirrored:
            if key not in os.environ and key in sec:
                value = sec[key]
                if value is None or str(value).strip() == "":
                    continue
                os.environ[key] = str(value).strip()
    except StreamlitSecretNotFoundError:
        # No secrets.toml locally or on stripped-down runs — defaults + plain env suffice.
        return
    except (AttributeError, FileNotFoundError, RuntimeError, OSError):
        return


def _bootstrap_db_from_remote(
    *,
    db_path: Path,
    json_out: Path,
    dump_url: str,
) -> None:
    sync_remote_results(
        url=dump_url.strip(),
        out_path=json_out,
        replace_local=False,
        force_fetch=True,
    )
    if not json_out.is_file():
        raise RuntimeError("Bootstrap finished but retro_results.json is still missing.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    apply_import_and_elo(db_path=db_path)
    _apply_elo_migrations(db_path)


def _apply_elo_migrations(sqlite_path: Path) -> None:
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_elo_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _peak_time_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    text = str(value).strip()
    return text if text else "—"


@st.cache_data(show_spinner=False)
def fetch_players(db_path_str: str) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT player_id,
                   display_name,
                   rating,
                   COALESCE(games_played, 0) AS games_played,
                   peak_rating,
                   peak_rating_at
            FROM players
            ORDER BY rating DESC, display_name COLLATE NOCASE;
            """,
            conn,
        )
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def fetch_games_agg(db_path_str: str) -> pd.Series:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS games,
              MIN(start_time) AS first_ts,
              MAX(start_time) AS last_ts,
              AVG(CAST(duration_secs AS REAL)) AS avg_duration_secs
            FROM games;
            """
        ).fetchone()
        cols = ("games", "first_ts", "last_ts", "avg_duration_secs")
        if row is None:
            return pd.Series({name: None for name in cols})
        return pd.Series(dict(zip(cols, row)))
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def rating_history_for_player(db_path_str: str, player_id: str) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
              g.start_time,
              g.game_id,
              pa.display_name AS name_a,
              pb.display_name AS name_b,
              g.score_a,
              g.score_b,
              CASE
                WHEN g.player_a_id = :pid THEN g.elo_a_after
                WHEN g.player_b_id = :pid THEN g.elo_b_after
              END AS rating_after
            FROM games g
            JOIN players pa ON pa.player_id = g.player_a_id
            JOIN players pb ON pb.player_id = g.player_b_id
            WHERE g.player_a_id = :pid OR g.player_b_id = :pid
            ORDER BY g.start_time, g.game_id;
            """,
            conn,
            params={"pid": player_id},
        )
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def recent_games(db_path_str: str, limit: int = 200) -> pd.DataFrame:
    path = Path(db_path_str)
    conn = sqlite3.connect(path)
    try:
        return pd.read_sql_query(
            """
            SELECT
              start_time,
              game_id,
              player_a_id,
              player_b_id,
              score_a,
              score_b,
              elo_a_after,
              elo_b_after
            FROM games
            ORDER BY start_time DESC, game_id DESC
            LIMIT ?;
            """,
            conn,
            params=(int(limit),),
        )
    finally:
        conn.close()


def run_import_koatd_offline_bundle(
    *,
    sqlite_path: Path,
    scores_csv: Path,
    tournaments_csv: Path,
    base: float,
    k: float,
    use_provisional_dual_k: bool,
) -> None:
    """Rebuild Amiga 500 SQLite from KOATD CSV dumps, then replay Elo (same K settings as sidebar)."""

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "kool_elo.import_koatd_offline",
            "--overwrite",
            "--scores",
            str(scores_csv.resolve()),
            "--tournaments",
            str(tournaments_csv.resolve()),
            "--db",
            str(sqlite_path.resolve()),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        check=True,
    )
    run_compute_elo(
        sqlite_path,
        base,
        k,
        use_provisional_dual_k=use_provisional_dual_k,
    )


def run_compute_elo(
    db_path: Path,
    base: float,
    k: float,
    *,
    use_provisional_dual_k: bool,
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    cmd = [
        sys.executable,
        "-m",
        "kool_elo.compute_elo",
        "--db",
        str(db_path),
        "--base",
        str(base),
        "--k",
        str(k),
        "--quiet",
        "--established-mass-recalibrate-every",
        str(ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES),
    ]
    if use_provisional_dual_k:
        cmd.append("--provisional-dual-k")
    else:
        cmd.append("--symmetric-k")
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env, check=True)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def main() -> None:
    st.set_page_config(page_title="Kick Off 2 ELO ratings", layout="wide")
    _mirror_streamlit_secrets_to_environment()
    st.title("Kick Off 2 ELO ratings")

    browse_amiga500 = st.radio(
        "Data source",
        options=["Online", "Amiga 500"],
        horizontal=True,
        key="_kool_browse_source",
    ) == "Amiga 500"

    st.sidebar.header("Data")
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    json_out = DEFAULT_JSON_PATH.resolve()

    db_default = str(DEFAULT_DB_PATH.resolve())
    db_input = st.sidebar.text_input(
        "SQLite database (online)",
        value=db_default,
        disabled=browse_amiga500,
        help="Path to `retro_elo.sqlite3` (ignored while browsing Amiga 500).",
    )
    db_path = Path(db_input).expanduser().resolve()
    amiga500_path = AMIGA500_DB_PATH.resolve()

    if browse_amiga500:
        if not amiga500_path.is_file():
            st.warning(
                f"Amiga 500 SQLite not found (`{amiga500_path}`). Build it from KOATD CSV bundles "
                "or import locally, then refresh."
            )
            bundled_scores = KOATD_SCORES_EXPORT_CSV.resolve()
            bundled_tours = KOATD_TOURNAMENTS_EXPORT_CSV.resolve()
            if bundled_scores.is_file() and bundled_tours.is_file():
                if st.button("Build Amiga 500 database from KOATD CSV exports", key="_kool_gate_build_amiga500"):
                    try:
                        with st.spinner("Importing KOATD CSV → SQLite → Elo replay …"):
                            run_import_koatd_offline_bundle(
                                sqlite_path=AMIGA500_DB_PATH,
                                scores_csv=bundled_scores,
                                tournaments_csv=bundled_tours,
                                base=float(BASE_RATING),
                                k=float(K_FACTOR),
                                use_provisional_dual_k=bool(
                                    st.session_state.get("_kool_provisional_dual_k", PROVISIONAL_DUAL_K_ENABLED)
                                ),
                            )
                    except subprocess.CalledProcessError as exc:
                        st.error(f"Import failed (exit {exc.returncode}).")
                        st.stop()
                    st.cache_data.clear()
                    st.success("Amiga 500 database built.")
                    st.rerun()
            else:
                st.markdown(
                    "**Streamlit Cloud** only sees files pushed to GitHub. "
                    "`offline_koatd.sqlite3` is gitignored locally, so the hosted app never receives it unless you rebuild on Cloud "
                    "(or force-add the DB)."
                )
                st.info(
                    "No bundled **`data/koatd_scores_export.csv`** "
                    "**+ `data/koatd_tournament_players_export.csv`** in this deployment "
                    "(commit them from your PC after `EXPORT_KOATD.bat`).\n\n"
                    "Alternatively, from the repo root locally:\n\n"
                    "```\nPYTHONPATH=src python -m kool_elo.import_koatd_offline --overwrite\n"
                    "PYTHONPATH=src python -m kool_elo.compute_elo --db data/offline_koatd.sqlite3\n```"
                )
            st.stop()
    elif not db_path.is_file():
        st.warning(
            f"SQLite not found yet (`{db_path}`). Streamlit Cloud starts without your local `data/*.sqlite3`; "
            "pull the online ladder JSON once and replay into SQLite below."
        )
        boot_input = st.text_input(
            "Online ladder JSON URL (bootstrap)",
            value=resolved_remote_results_url(),
            help="Override with env `KOOL_REMOTE_RESULTS_URL` or Streamlit secret of the same name.",
            key="_kool_cloud_bootstrap_dump_url",
        )
        tip = """
**First‑time bootstrap** downloads the online ladder JSON and replays imports + Elo (may take minutes and can hit hosted timeouts on very large payloads).

Suggested Cloud setup: paste the validated dump URL under **Secrets** as `KOOL_REMOTE_RESULTS_URL`.

If `KOOL_CLOUD_AUTO_BOOTSTRAP` is `true`, automation runs **once per Cloud sandbox** (`data/.cloud_auto_bootstrap_attempted` keeps every new browser tab from restarting the downloader). Delete that file if you need to rerun automation without pushing a redeploy—the Streamlit Cloud filesystem is short-lived anyway.
"""
        st.markdown(tip)

        boot_attempt_flag = DATA_DIR / ".cloud_auto_bootstrap_attempted"
        if _env_truthy("KOOL_CLOUD_AUTO_BOOTSTRAP") and not boot_attempt_flag.is_file():
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            boot_attempt_flag.write_text("", encoding="utf-8")
            try:
                with st.spinner("KOOL_CLOUD_AUTO_BOOTSTRAP: fetching JSON → SQLite …"):
                    _bootstrap_db_from_remote(
                        db_path=db_path,
                        json_out=json_out,
                        dump_url=boot_input,
                    )
            except Exception as exc:  # noqa: BLE001 — user-facing onboarding
                st.error(
                    f"Automatic bootstrap failed: {exc}. Use the manual button below, tweak Secrets/timeouts, "
                    "or delete `data/.cloud_auto_bootstrap_attempted` once upstream issues are cleared."
                )
            else:
                st.cache_data.clear()
                st.success("SQLite ready.")
                st.rerun()

        if st.button("Bootstrap SQLite from online JSON", type="primary"):
            try:
                with st.spinner("Downloading + import + replay — patience …"):
                    _bootstrap_db_from_remote(
                        db_path=db_path,
                        json_out=json_out,
                        dump_url=boot_input,
                    )
            except Exception as exc:  # noqa: BLE001 — surface full stack trace in expander optional
                st.exception(exc)
                st.stop()
            st.cache_data.clear()
            st.success("SQLite ready.")
            st.rerun()

        st.stop()

    active_db = amiga500_path if browse_amiga500 else db_path
    _apply_elo_migrations(active_db)

    if (
        not browse_amiga500
        and _env_truthy("KOOL_AUTO_SYNC_ON_START")
        and not st.session_state.get("_kool_remote_autosync_once")
    ):
        st.session_state["_kool_remote_autosync_once"] = True
        boot_url = resolved_remote_results_url()
        try:
            with st.spinner("Checking online ladder JSON (downloads full payload)…"):
                stats_boot = sync_remote_results(
                    url=boot_url,
                    out_path=json_out,
                    replace_local=False,
                    force_fetch=False,
                )
            if not stats_boot.payload_unchanged:
                if (
                    _env_truthy("KOOL_AUTO_SYNC_APPLY")
                    and (
                        stats_boot.wrote_file
                        or stats_boot.added_ids
                        or stats_boot.updated_rows
                    )
                ):
                    with st.spinner("import_matches + compute_elo …"):
                        apply_import_and_elo(db_path=db_path)
                elif not _env_truthy("KOOL_AUTO_SYNC_APPLY"):
                    st.session_state["_kool_json_refresh_pending"] = True
                st.cache_data.clear()
                st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.sidebar.warning(f"Automatic remote sync failed: {exc}")

    st.sidebar.markdown("---")
    if browse_amiga500:
        st.sidebar.info(
            "Browsing **Amiga 500** (`offline_koatd.sqlite3`). Online JSON sync is hidden."
        )
    else:
        st.sidebar.subheader("Online ladder JSON")
        st.sidebar.caption(
            "Joshua serves the full payload each visit (no etag). We SHA-256 the body "
            "and skip SQLite work when nothing changed. "
            "`KOOL_REMOTE_RESULTS_URL` overrides the default URL."
        )
        dump_url = st.sidebar.text_input(
            "Dump URL",
            value=resolved_remote_results_url(),
            help="Override with env `KOOL_REMOTE_RESULTS_URL` or Streamlit secret of the same name.",
        )
        replace_local = st.sidebar.checkbox(
            "Replace local JSON (remote only)",
            value=False,
            help="Deletes local-only rows until the next merge.",
        )
        force_fetch_sidebar = st.sidebar.checkbox(
            "Ignore hash / re-merge anyway",
            value=False,
        )
        rebuild_after_sync = st.sidebar.checkbox(
            "After sync: import_matches + compute_elo",
            value=True,
        )
        if st.session_state.get("_kool_json_refresh_pending"):
            st.sidebar.info(
                "JSON on disk may be newer than SQLite (auto-sync without "
                "`KOOL_AUTO_SYNC_APPLY`). Pull again with ✓ rebuild or toggle env."
            )
        if st.sidebar.button("Sync now"):
            with st.spinner("Fetching dump + merging locally…"):
                try:
                    stats = sync_remote_results(
                        url=dump_url.strip(),
                        out_path=json_out,
                        replace_local=replace_local,
                        force_fetch=force_fetch_sidebar,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.sidebar.error(f"{type(exc).__name__}: {exc}")
                else:
                    if stats.payload_unchanged:
                        st.sidebar.success(
                            "Remote body matches last SHA-256 — skipping JSON rewrite."
                        )
                    else:
                        parts = [
                            f"remote rows: {stats.remote_rows:,}",
                            f"merged: {stats.merged_rows:,}",
                            f"added GameIDs: +{stats.added_ids:,}",
                            f"updates: {stats.updated_rows:,}",
                            f"wrote JSON: {'yes' if stats.wrote_file else 'no'}",
                        ]
                        st.sidebar.success(" · ".join(parts))
                        changed_local = (
                            stats.wrote_file
                            or stats.added_ids
                            or stats.updated_rows
                            or replace_local
                        )
                        if rebuild_after_sync and changed_local:
                            with st.spinner("import_matches + compute_elo …"):
                                try:
                                    apply_import_and_elo(db_path=db_path)
                                except subprocess.CalledProcessError:
                                    st.sidebar.error("Rebuild pipeline failed.")
                                    st.stop()
                            st.session_state.pop("_kool_json_refresh_pending", None)
                        elif changed_local:
                            st.session_state["_kool_json_refresh_pending"] = True
                        st.cache_data.clear()
                        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("Recompute Elo")
    st.sidebar.caption(f"Target database: `{active_db.name}`")
    if "_kool_provisional_dual_k" not in st.session_state:
        st.session_state["_kool_provisional_dual_k"] = PROVISIONAL_DUAL_K_ENABLED
    use_dual_k = st.sidebar.checkbox(
        "Provisional dual‑K (forum per‑player ramps)",
        key="_kool_provisional_dual_k",
        help=(
            "If checked, each seat gets its own K from the provisional ladders in `elo_core.py`. "
            "If unchecked, both players share the sidebar K-factor every match."
        ),
    )
    if use_dual_k:
        st.sidebar.caption(
            "Next replay: **dual‑K** — automatic ramps + opponent damping (see `elo_core.py`). "
            f"`?` markers still indicate `games_played` < `{PROVISIONAL_GAMES_FULL_THRESHOLD}`."
        )
    else:
        st.sidebar.caption(
            "Next replay: **symmetric K** — same `K-factor` below for **both** players every game."
        )
    if ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES > 0:
        st.sidebar.caption(
            f"Established mean recalibration **on**: every **{ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES}** games "
            "(see `ESTABLISHED_MASS_RECALIBRATE_EVERY_N_GAMES` in `config.py`; set `0` to disable)."
        )
    base = float(st.sidebar.number_input("Base rating", value=float(BASE_RATING), step=1.0))
    k_factor = float(st.sidebar.number_input("K-factor", value=float(K_FACTOR), step=1.0))
    if st.sidebar.button("Run full replay", type="primary"):
        with st.spinner("Replaying every game — this can take a few seconds…"):
            try:
                run_compute_elo(
                    active_db,
                    base,
                    k_factor,
                    use_provisional_dual_k=use_dual_k,
                )
            except subprocess.CalledProcessError as exc:
                st.sidebar.error(f"`compute_elo` failed (exit {exc.returncode}).")
                st.stop()
            else:
                st.cache_data.clear()
                st.sidebar.success("Replay finished — refreshing views.")
                st.rerun()

    db_key = str(active_db)

    players_df = fetch_players(db_key)
    games_agg = fetch_games_agg(db_key)

    amiga500_tools_tab_label = "Amiga 500 tools" if browse_amiga500 else "Amiga 500"
    overview_tab, board_tab, history_tab, recent_tab, amiga500_tab = st.tabs(
        ["Overview", "Leaderboard", "Rating history", "Recent games", amiga500_tools_tab_label]
    )

    with overview_tab:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Players", f"{len(players_df):,}")
        c2.metric("Stored games", f"{int(games_agg['games']):,}")
        c3.metric("First match", games_agg["first_ts"] or "—")
        c4.metric("Latest match", games_agg["last_ts"] or "—")

        rating_min = float(players_df["rating"].min()) if len(players_df) else float("nan")
        rating_max = float(players_df["rating"].max()) if len(players_df) else float("nan")
        avg_dur = games_agg["avg_duration_secs"]
        lines = [
            (
                f"- **Rating range:** {rating_min:.1f} → {rating_max:.1f} "
                "(snapshot after latest `compute_elo` replay)"
            )
        ]
        if pd.notna(avg_dur):
            lines.append(
                f"- **Mean match duration:** {float(avg_dur):,.1f}s "
                "(raw `duration_secs` from dump; includes pauses/menu time)"
            )
        st.markdown("\n".join(lines))

    with board_tab:
        st.subheader("Rankings")
        st.caption(
            f"Peak stays **—** until **{PROVISIONAL_GAMES_FULL_THRESHOLD}** completed matches. "
            "The **?** column marks provisional estimates — **rating** and **peak rating** "
            "are numbers so sorting is correct."
        )
        query = st.text_input("Filter by name (substring, case-insensitive)", value="")

        filtered = players_df
        if query.strip():
            needle = query.strip().lower()
            mask = filtered["display_name"].str.lower().str.contains(needle, na=False)
            filtered = filtered.loc[mask]

        gp = filtered["games_played"].astype(int)
        provisional = gp < PROVISIONAL_GAMES_FULL_THRESHOLD
        peak_eligible = ~provisional

        peak_num = pd.to_numeric(filtered["peak_rating"], errors="coerce")
        peak_num = peak_num.where(peak_eligible)

        view = filtered.assign(
            games=gp,
            # Numeric columns so interactive sort is numeric, not lexicographic on "931?" vs "2335".
            prov_mark=[
                "?" if bool(prov) else ""
                for prov in provisional
            ],
            peak_rating=peak_num,
            peak_recorded=[
                _peak_time_text(pw) if elig else "—"
                for pw, elig in zip(filtered["peak_rating_at"], peak_eligible)
            ],
        )
        cols = ["display_name", "games", "rating", "prov_mark", "peak_rating", "peak_recorded"]
        st.dataframe(
            view.loc[:, cols].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "games": st.column_config.NumberColumn("games", format="%d"),
                "rating": st.column_config.NumberColumn("rating", format="%.1f"),
                "prov_mark": st.column_config.TextColumn(
                    "?",
                    help=(
                        "**?** marks a provisional estimate (fewer than "
                        f"{PROVISIONAL_GAMES_FULL_THRESHOLD} games played)."
                    ),
                ),
                "peak_rating": st.column_config.NumberColumn("peak rating", format="%.1f"),
                "peak_recorded": st.column_config.TextColumn("peak recorded"),
            },
        )

    with history_tab:
        if players_df.empty:
            st.warning("No players.")
        else:
            player_options = (
                players_df.assign(
                    label=lambda df: df["display_name"].astype(str)
                    + " ("
                    + df["player_id"].astype(str)
                    + ")"
                )[["label", "player_id"]]
            )
            label_choice = st.selectbox(
                "Pick a player",
                options=player_options["label"].tolist(),
            )
            chosen_row = player_options.loc[player_options["label"] == label_choice].iloc[0]
            player_id = str(chosen_row["player_id"])

            hist = rating_history_for_player(db_key, player_id)
            if hist.empty:
                st.info("No games for this player in the DB.")
            else:
                hist = hist.assign(parsed_time=pd.to_datetime(hist["start_time"]))
                nan_elo = hist["rating_after"].isna().sum()
                if nan_elo:
                    st.warning(
                        f"{int(nan_elo)} row(s) still lack `rating_after`. "
                        "Run **Recompute Elo** in the sidebar."
                    )
                chart_df = hist.dropna(subset=["rating_after"])
                if not chart_df.empty:
                    chart_df = chart_df.set_index("parsed_time")[["rating_after"]]
                    st.line_chart(chart_df, height=360)

                st.caption(
                    "**Score** is **left name’s goals – right name’s goals**, in the same order as the *players* column."
                )
                hist_view = hist.assign(
                    matchup=lambda df: df["name_a"].astype(str) + " vs " + df["name_b"].astype(str),
                    scoreline=lambda df: df["score_a"].astype(str) + "–" + df["score_b"].astype(str),
                )
                show_cols = [
                    "start_time",
                    "matchup",
                    "scoreline",
                    "rating_after",
                    "game_id",
                ]
                st.dataframe(
                    hist_view.loc[:, show_cols].reset_index(drop=True),
                    hide_index=True,
                    use_container_width=True,
                    height=420,
                    column_config={
                        "start_time": st.column_config.TextColumn("when"),
                        "matchup": st.column_config.TextColumn("players"),
                        "scoreline": st.column_config.TextColumn("goals (left – right)"),
                        "rating_after": st.column_config.NumberColumn("your rating after", format="%.2f"),
                        "game_id": st.column_config.TextColumn("game"),
                    },
                )

    with recent_tab:
        st.subheader(f"Latest {200} matches (reverse chronological)")
        recent = recent_games(db_key, limit=200)
        st.dataframe(recent, use_container_width=True, hide_index=True)

    with amiga500_tab:
        st.subheader("Amiga 500")
        st.caption(
            "SQLite from KOATD Access CSV exports (`Scores` + `Tournament players`). "
            "`start_time` is tournament calendar day + intra-event ordering (not precise kickoff)."
        )
        bundled_scores = KOATD_SCORES_EXPORT_CSV.resolve()
        bundled_tours = KOATD_TOURNAMENTS_EXPORT_CSV.resolve()

        if browse_amiga500:
            st.info(
                "You’re browsing this database everywhere (**Overview** through **Recent games**). "
                "Use **Run full replay** in the sidebar after changing base/K, or rebuild from CSV below."
            )
            st.markdown(f"**Path:** `{amiga500_path.resolve()}`")
            if bundled_scores.is_file() and bundled_tours.is_file():
                if st.button(
                    "Rebuild Amiga 500 SQLite from KOATD CSV exports",
                    key="_kool_amiga500_rebuild_while_global",
                ):
                    try:
                        with st.spinner("Re-import KOATD CSV → SQLite → Elo replay …"):
                            run_import_koatd_offline_bundle(
                                sqlite_path=AMIGA500_DB_PATH,
                                scores_csv=bundled_scores,
                                tournaments_csv=bundled_tours,
                                base=base,
                                k=k_factor,
                                use_provisional_dual_k=use_dual_k,
                            )
                    except subprocess.CalledProcessError as exc:
                        st.error(f"Rebuild failed (exit {exc.returncode}).")
                        st.stop()
                    st.cache_data.clear()
                    st.success("Amiga 500 database rebuilt.")
                    st.rerun()
            else:
                st.caption(
                    "No bundled `koatd_scores_export.csv` + `koatd_tournament_players_export.csv` next to this app — "
                    "re-import locally if you need a fresh Amiga 500 build."
                )
        elif not amiga500_path.is_file():
            st.markdown(
                "**Streamlit Cloud** only sees files pushed to GitHub. "
                "`offline_koatd.sqlite3` is gitignored locally, so the hosted app never receives it unless you rebuild on Cloud "
                "(or force-add the DB)."
            )
            if bundled_scores.is_file() and bundled_tours.is_file():
                st.warning(
                    "Bundled KOATD CSVs were found in the repo (`data/`). "
                    "You can build the SQLite database once inside this deployment."
                )
                if st.button("Build Amiga 500 database from KOATD CSV exports", key="_kool_cloud_build_amiga500"):
                    try:
                        with st.spinner("Importing KOATD CSV → SQLite → Elo replay (may take a minute on Cloud)…"):
                            run_import_koatd_offline_bundle(
                                sqlite_path=AMIGA500_DB_PATH,
                                scores_csv=bundled_scores,
                                tournaments_csv=bundled_tours,
                                base=base,
                                k=k_factor,
                                use_provisional_dual_k=use_dual_k,
                            )
                    except subprocess.CalledProcessError as exc:
                        st.error(f"Import failed (exit {exc.returncode}).")
                        st.stop()
                    st.cache_data.clear()
                    st.success("Amiga 500 database built.")
                    st.rerun()

            else:
                st.info(
                    "No Amiga 500 SQLite yet — and no **`data/koatd_scores_export.csv`** "
                    "**+ `data/koatd_tournament_players_export.csv`** in this deployment "
                    "(commit them from your PC after `EXPORT_KOATD.bat`).\n\n"
                    "Alternatively, from the repo root locally:\n\n"
                    "```\nPYTHONPATH=src python -m kool_elo.import_koatd_offline --overwrite\n"
                    "PYTHONPATH=src python -m kool_elo.compute_elo --db data/offline_koatd.sqlite3\n```"
                )
                st.caption(
                    "Switch **Data source** to **Amiga 500** after building to browse that dataset everywhere."
                )
        else:
            _apply_elo_migrations(amiga500_path)
            amiga500_key = str(amiga500_path)
            amiga500_players = fetch_players(amiga500_key)
            amiga500_games = fetch_games_agg(amiga500_key)
            st.caption(
                "Peek at Amiga 500 without switching the global source, or choose **Amiga 500** above to browse it everywhere."
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Players", f"{len(amiga500_players):,}")
            c2.metric("Games", f"{int(amiga500_games['games']):,}")
            c3.metric("First → last", (amiga500_games["first_ts"] or "—") + " → " + (amiga500_games["last_ts"] or "—"))

            fq = st.text_input("Filter name", value="", key="_kool_amiga500_name_filter")
            view_o = amiga500_players
            if fq.strip():
                nm = fq.strip().lower()
                view_o = view_o.loc[view_o["display_name"].str.lower().str.contains(nm, na=False)]

            gpo = view_o["games_played"].astype(int)
            prov_o = gpo < PROVISIONAL_GAMES_FULL_THRESHOLD

            peak_elig_o = ~prov_o

            pk_o = pd.to_numeric(view_o["peak_rating"], errors="coerce")
            pk_o = pk_o.where(peak_elig_o)

            tbl = view_o.assign(
                games=gpo,
                prov_mark=["?" if bool(p) else "" for p in prov_o],
                peak_rating=pk_o,
            )
            cols = ["display_name", "games", "rating", "prov_mark", "peak_rating"]
            show = tbl.loc[:, cols].reset_index(drop=True)
            st.dataframe(
                show,
                use_container_width=True,
                hide_index=True,
                height=min(620, max(420, len(show) * 35)),
                column_config={
                    "display_name": st.column_config.TextColumn("name"),
                    "games": st.column_config.NumberColumn("games", format="%d"),
                    "rating": st.column_config.NumberColumn("rating", format="%.1f"),
                    "prov_mark": st.column_config.TextColumn("?", width="small"),
                    "peak_rating": st.column_config.NumberColumn("peak", format="%.1f"),
                },
            )
            st.caption(
                "**?** provisional (under "
                f"{PROVISIONAL_GAMES_FULL_THRESHOLD} games). Replay with "
                "`python -m kool_elo.compute_elo --db data/offline_koatd.sqlite3` after re-import."
            )


if __name__ == "__main__":
    main()
