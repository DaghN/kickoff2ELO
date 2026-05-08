# Kool Elo — project memory

Working log for decisions, parameters, and next steps. Updated as the project evolves.

## Working style

- **Hands-off on implementation:** Prefer *vibecoding* — describe goals and constraints in chat; avoid expecting the maintainer to edit files, run commands, or manage project wiring by hand unless they explicitly choose to.
- **Technical depth is welcome:** Explanations may use math, stats, and software terms; clarity matters more than simplifying vocabulary.

## Requirements (agreed)

- **Domain:** ELO-style ratings for ~80k Kick Off 2 (Amiga) head-to-head matches from `retro_results.json` (project root).
- **Stack:** Python, SQLite, pandas; later Streamlit for exploration.
- **Data model:** Relational SQLite schema — `players` and `games` (see **Database schema** below).
- **ELO (later):** Simple win/draw/loss only; base rating **1600**, K-factor **32**; no goal-difference bonus in v1. New-player handling deferred.
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

## Current parameters (for when ELO lands)

| Parameter    | Value | Notes                    |
|-------------|-------|--------------------------|
| Base rating | 1600  | Starting rating        |
| K-factor    | 32    | Standard single value v1 |

*Implemented in `src/kool_elo/config.py` (`BASE_RATING`, `K_FACTOR`) for easy tuning in later stages.*

## Repository layout

```
├── data/              # Local SQLite DB files (gitignored patterns)
├── src/kool_elo/      # Python package (config, schema.sql, import_matches)
├── retro_results.json # Raw dump (project root; large — consider LFS or gitignore if sharing)
├── memory.md
├── requirements.txt
└── .gitignore
```

## Staging plan (recommended)

1. **Stage 1 — Import & schema:** DONE — DDL in `schema.sql`; loader `import_matches.py`. Games sorted by `StartTime`, then `game_id`; invalid self-matches (`PlayerA == PlayerB`) are skipped and counted in the import summary (`self_matches_skipped`).
2. **Stage 2 — ELO core:** Chronological pass, update ratings in memory + `players` table; optional `game_elo` or rating snapshot columns for audit/rebuild.
3. **Stage 3 — CLI / notebooks (optional):** Thin script to recompute, export CSV, sanity checks.
4. **Stage 4 — Streamlit:** Rankings, player lookup, filters, parameter sidebar for future K/R0 experiments.

## Status

- [x] Project skeleton, git, `.gitignore`, `memory.md`, staging plan documented.
- [x] Stage 1: JSON → SQLite import (`data/retro_elo.sqlite3`).
- [ ] Stage 2: Chronological ELO pass and persistence.

## Database schema (SQLite)

File: `data/retro_elo.sqlite3` (gitignored). Built from `src/kool_elo/schema.sql`.

**`players`**

| Column        | Type | Notes |
|---------------|------|--------|
| `player_id`   | TEXT | Primary key; same string ids as in JSON |
| `display_name`| TEXT | One row per id; **last name seen in chronological order** wins |

**`games`**

| Column          | Type    | Notes |
|-----------------|---------|--------|
| `game_id`       | TEXT    | Primary key |
| `start_time`    | TEXT    | `YYYY-MM-DD HH:MM:SS` (lexicographic = chronological) |
| `player_a_id`   | TEXT    | FK → `players` |
| `player_b_id`   | TEXT    | FK → `players` |
| `score_a`       | INTEGER | ≥ 0 |
| `score_b`       | INTEGER | ≥ 0 |
| `duration_secs` | INTEGER | ≥ 0 |

Indexes: `start_time`, `player_a_id`, `player_b_id`. Foreign keys enforced with `PRAGMA foreign_keys = ON` on import connection.

**Import stats (local run, 2026-05-09):** 77,589 rows read; **2 self-matches skipped** (same id in A and B); **77,587** games loaded; **280** distinct players. Chronological span in DB: first game `2016-07-10 23:14:56`, latest `2026-05-08 21:14:07`.

## How to run the import

From project root (PowerShell example):

```powershell
pip install -r requirements.txt
$env:PYTHONPATH = "src"
python -m kool_elo.import_matches --overwrite
```

- `--json PATH` — override input file (default: `retro_results.json` in project root).
- `--db PATH` — override output DB (default: `data/retro_elo.sqlite3`).
- `--overwrite` — delete existing DB file before writing (required if the file already exists).

**Sanity check (recommended after import or DB changes)**

```powershell
$env:PYTHONPATH = "src"
python -m kool_elo.verify_stage1
```

This checks SQLite `integrity_check`, foreign keys (when supported), orphaned rows, duplicate `game_id` values, non-negative constraints, lexical insert order versus `(start_time, game_id)`, and counts / player ids versus `retro_results.json` with the same self-match rule as the importer.

## Decisions log

| Date       | Decision |
|------------|----------|
| 2026-05-09 | Use `src/kool_elo` package; store generated DB under `data/` with gitignore on `*.sqlite` etc. |
| 2026-05-09 | ELO and Streamlit deferred until after clean import. |
| 2026-05-09 | Exclude self-matches (`PlayerA == PlayerB`) from `games`; log count in import summary. |
| 2026-05-09 | `display_name` resolution: last appearance in time-sorted match list. |
| 2026-05-09 | Document *vibecoding* preference: user steers outcomes in chat without doing implementation grunt work; still OK with rigorous technical explanation. |

## Next steps (Stage 2)

1. Add rating column(s) on `players` (and optional per-game rating snapshot table or columns for reproducibility).
2. Implement chronological ELO using `config.BASE_RATING` and `config.K_FACTOR` (simple W/D/L).
3. Script to recompute from DB (idempotent) for parameter experiments.
