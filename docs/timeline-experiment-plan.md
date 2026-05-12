# Timeline divergence experiment — implementation plan

Pre-experiment checkpoint: mirror production (single SQLite path), two JSON ground truths, Logos rating traces, diff after split. Parameter: `--games-back` (e.g. 50 vs 100).

## Goals

- Treat **two JSON ground truths** as two full production runs: **`import_matches --overwrite`** then **`compute_elo`** (same ordering, dual-K, constants as in `kool_elo.config`).
- Use **`DEFAULT_DB_PATH`** only; the second import **overwrites** the first intentionally; intended workflow afterward is remote **resync** and rebuild from canonical data.
- Persist **Logos’s rating after each of his games** (post-update) into **two trace files** (one per timeline), saved **after each complete pipeline** so traces survive DB wipes.
- Deliver **rating differences after the split**: paired deltas / absolute gap along Logos’s post-split sequence.
- **`games_back`** is configurable (default e.g. 50).

## Spec lock-in

- **Global order:** `ORDER BY start_time, game_id` (matches `rebuild_elo`).
- **Ground-zero rule:** Among Logos’s games in that order, pick the match such that **exactly `games_back` Logos games occur after it** (exact index semantics documented in implementation code); if that row is a draw, walk **backward** along Logos’s timeline until decisive or fail clearly.
- **Fork:** Swap **only** `ScoreA`/`ScoreB` on that row (seats unchanged).
- **Logging moment:** Logos’s rating **immediately after** applying that game’s update.

## Stage 1 — Fork preparation

**Inputs:** Canonical `retro_results.json` (default path).

1. Load JSON array (same schema as `import_matches`).
2. Resolve **Logos** → `player_id`.
3. Build canonical global order (same sort as `load_and_prepare`).
4. Select **`ground_zero_game_id`** per spec above.
5. Write **`retro_results_fork.json`** (or similar path): copy of array with **one** row’s scores swapped.

**Workflow:** Prefer keeping **`retro_results.json` untouched** and passing **`--json`** to import for canonical vs fork paths.

## Stage 2 — Timeline A (canonical)

1. `import_matches --overwrite` with default `--db`, **`--json`** canonical path.
2. Instrumented **`compute_elo`** / shared replay: append to **`logos_trace_real`** whenever Logos plays (post-update rating).
3. Close trace file completely on disk before Stage 3.

**Optional:** Verify final Logos rating in DB matches last trace row.

## Stage 3 — Timeline B (fork)

1. `import_matches --overwrite` — **same `--db`**, **`--json`** = fork file.
2. Instrumented rebuild → **`logos_trace_alt`**.
3. SQLite now reflects **fork** world only — expected.

## Stage 4 — Diff after split

1. Align traces by **`game_id`** on Logos samples.
2. Locate split at **`ground_zero_game_id`** (post-update divergence).
3. Emit series from that row onward: `abs(r_alt - r_real)` (optional signed delta).
4. Record first index where difference `< 1` (document comparison rule).

## Stage 5 — Unified CLI

Suggested: `python -m kool_elo.experiment_timeline_convergence --player Logos --games-back 50 [--json …] [--fork-out …] [--trace-real …] [--trace-alt …]`

Runs fork build → Stage 2 → Stage 3 → diff output.

## Stage 6 — Validation

- Same inputs → identical traces (deterministic).
- Timeline A final Logos rating matches plain `compute_elo` after canonical import (instrumentation must not change math).
- Fork differs from canonical by exactly one decisive row.

## Stage 7 — Repo hygiene

New module(s) under `src/kool_elo/` for the experiment; avoid changing default `compute_elo` / `import_matches` behavior unless extracting a shared replay helper.

## Operational checklist (single DB)

| Step              | DB state after step      | Must persist   |
|-------------------|--------------------------|----------------|
| Import canonical  | Real games + players     | —              |
| Rebuild + log     | Real ratings             | **`trace_real`** |
| Import fork       | DB wiped; fork games     | —              |
| Rebuild + log alt | Fork ratings             | **`trace_alt`** |
| Resync (later)    | Fresh canonical pipeline | —              |

## Non-goals

- Separate SQLite files per timeline (not required).
- Replacing symmetric K=32 globally — production uses dual-K provisional rules unless explicitly overridden for a separate experiment.
