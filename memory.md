# Kool Elo — project memory

Working log for decisions, parameters, and next steps. Updated as the project evolves.

## Working style

- **Hands-off on implementation:** Prefer *vibecoding* — describe goals and constraints in chat; avoid expecting the maintainer to edit files, run commands, or manage project wiring by hand unless they explicitly choose to.
- **Technical depth is welcome:** Explanations may use math, stats, and software terms; clarity matters more than simplifying vocabulary.

## Requirements (agreed)

- **Domain:** ELO-style ratings for ~80k Kick Off 2 (Amiga) head-to-head matches from `retro_results.json` (project root).
- **Stack:** Python, SQLite, pandas; later Streamlit for exploration.
- **Data model:** Relational SQLite schema — `players` and `games` (see **Database schema** below).
- **ELO (implemented):** Simple win/draw/loss only (“classic” fractional scores 1 / ½ / 0); base rating **1600**, symmetric **K = 32**; no goal-difference modifier in v1. New-player / provisional handling intentionally deferred (“everyone starts at `BASE_RATING` until games move them”).
- **Engineering:** Clear layout, git from day one, maintain this file, keep code readable and easy to tune parameters later.

## Source file schema (`retro_results.json`)

Single JSON array. Each object (verified on sample):

| Field      | Example type | Notes                          |
|------------|--------------|--------------------------------|
| GameID     | string       | Unique match id                |
| StartTime  | string       | e.g. `2026-05-08 21:14:07`     |
| PlayerA/B  | string       | Player ids (numeric as string) |
| NameA/B    | string       | Display names                  |
| ScoreA/B   | string       | Goals (cast in DB/import)      |
| Duration   | string       | e.g. seconds as string         |

Record count checked locally: **77,589** games (slightly under 80k; file may grow).

## Current parameters

| Parameter    | Value | Notes                    |
|-------------|-------|--------------------------|
| Base rating | 1600  | Starting rating (`BASE_RATING` in `config.py`) |
| K-factor    | 32    | Same K for both players (`K_FACTOR`) |

CLI overrides (`compute_elo --base`, `--k`) replay the ladder without touching `config.py` when trialling ideas quickly.

## Repository layout

```
├── data/              # Local SQLite DB files (gitignored patterns)
├── src/kool_elo/      # Python package
│                       # - schema.sql (+ schema_migrations for older files)
│                       # - config, import_matches, verify_stage1
│                       # - elo_core (pure formulas)
│                       # - compute_elo (replay + persistence)
├── retro_results.json # Raw dump (project root)
├── memory.md
├── requirements.txt
└── .gitignore
```

## Staging plan (recommended)

1. **Stage 1 — Import & schema:** DONE — DDL in `schema.sql`; loader `import_matches.py`. Games sorted by `StartTime`, then `game_id`; invalid self-matches (`PlayerA == PlayerB`) are skipped (`self_matches_skipped` in summary).
2. **Stage 2 — ELO core:** DONE — `compute_elo` replays chronologically (`ORDER BY start_time, game_id`); persists `players.rating` plus per-game snapshots on `games` (`elo_*` columns); `elo_core.py` isolates maths; migrations patch legacy DB files created before these columns existed.
3. **Stage 3 — CLI / exports (optional):** CSV exports, richer audit helpers, notebooks.
4. **Stage 4 — Streamlit:** Rankings explorer, filters, sliders bound to `--base/--k`-style experimentation.

## Status

- [x] Project skeleton, git, `.gitignore`, `memory.md`, staging plan documented.
- [x] Stage 1: JSON → SQLite import (`data/retro_elo.sqlite3`).
- [x] Stage 2: Elo recomputation (`python -m kool_elo.compute_elo`).
- [ ] Stage 3+: exports / dashboards as needed.

## Database schema (SQLite)

File: `data/retro_elo.sqlite3` (gitignored). Created by `schema.sql`; older DBs pick up deltas via `schema_migrations.ensure_stage2_rating_columns`.

**`players`**

| Column         | Type | Notes |
|----------------|------|-------|
| `player_id`    | TEXT | Primary key; same string ids as in JSON |
| `display_name` | TEXT | **Last chronological name appearance** wins during import |
| `rating`       | REAL | Current Elo (default seed `1600`; recomputed replay resets everyone to `--base`) |

**`games`**

| Column          | Type    | Notes |
|-----------------|---------|--------|
| `game_id`       | TEXT    | Primary key |
| `start_time`    | TEXT    | Lexicographically sortable timestamps |
| `player_a_id`   | TEXT    | FK → `players` |
| `player_b_id`   | TEXT    | FK → `players` |
| `score_a/b`     | INTEGER | Goals (determine W/D/L only) |
| `duration_secs` | INTEGER | Not used by Elo v1 |
| `elo_*`         | REAL    | Stored **before → after** ratings for both seats; `NULL` only before first successful compute |

Indexes: `(start_time)`, `(player_a_id)`, `(player_b_id)`. Always enable `PRAGMA foreign_keys = ON` when opening connections.

### Elo recap (implemented)

Expected score for A vs B: \(E_A = \frac{1}{1 + 10^{(R_B - R_A)/400}}\). Actual score \(S_A \in \{1, \tfrac12, 0\}\) from goals; symmetric update \(R_A \leftarrow R_A + K(S_A - E_A)\), \(R_B\) analogously (\(S_B = 1 - S_A\) in wins/losses, both ½ draws). Goal differential ignored by design—roadmap item if ever desired.

**Import statistics (fixture on this workstation):** 77,587 stored games spanning `2016-07-10`→`2026-05-08`; post-compute illustrative spread roughly **2475 → 1062** rating points (purely illustrative; rerun after data refresh).

## How to run tooling

### 1 · Import (`import_matches`)

```powershell
pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m kool_elo.import_matches --overwrite
```

Flags: `--json`, `--db`, `--overwrite` (required if DB already exists).

### 2 · Stage 1 verifier

```powershell
$env:PYTHONPATH = "src"
python -m kool_elo.verify_stage1
```

Validates FK integrity, chronological insert order parity with JSON, excludes self-rows consistently, etc.

### 3 · Elo replay (`compute_elo`)

```powershell
$env:PYTHONPATH = "src"
python -m kool_elo.compute_elo          # leaderboard preview + summary
python -m kool_elo.compute_elo --quiet # summary only
```

Flags:

- `--db PATH` — override SQLite target (defaults to `data/retro_elo.sqlite3`).
- `--base FLOAT` — starting rating for replay (defaults to `BASE_RATING`).
- `--k FLOAT` — symmetric K-factor (defaults to `K_FACTOR`).

Replay is deterministic and **idempotent**: running it twice with identical inputs reproduces identical ratings and snapshots (verified programmatically).

**Workflow caveat:** rerun import with `--overwrite` whenever the JSON snapshot changes materially; rerun `compute_elo` afterward to refresh ratings.

## Decisions log

| Date       | Decision |
|------------|----------|
| 2026-05-09 | Package lives under `src/kool_elo`; artefacts under `data/`. |
| 2026-05-09 | Skip self-match rows at import—they are undefined for pairwise Elo. |
| 2026-05-09 | Display names follow last chronological occurrence (import sort). |
| 2026-05-09 | Document *vibecoding*: user drives requirements via chat—not manual repo ops. |
| 2026-05-09 | Stage 2: store per-game snapshots directly on `games` for reproducibility (`elo_*`). |
| 2026-05-09 | Stage 2: `schema_migrations.py` preserves compatibility with SQLite files minted during Stage‑1‑only DDL. |

## Next steps

1. Stage 3 niceties — export ranked CSV/HTML, matchup history drill-downs without Streamlit overhead.
2. Stage 4 Streamlit UX — leaderboard tab, filters, plotting rating evolution using stored `elo_*` columns / joins.
3. Optional future mechanics — provisional ratings, separate home/away, goal-sensitive K/FIDE-style caps, richer new-player onboarding.
