# Kick Off 2 ELO ratings

GitHub: **[DaghN/kickoff2ELO](https://github.com/DaghN/kickoff2ELO)** — Python + SQLite ELO ratings tooling and a **[Streamlit](https://streamlit.io/)** explorer for Kick Off 2: **online** ladder ratings (JSON dump) and **Amiga 500** event ratings (KOATD CSV → SQLite).

---

## Repo layout

| Piece | Role |
|--------|------|
| `dashboard.py` | Streamlit UI (deploy this entry point on Streamlit Community Cloud). |
| `requirements.txt` | Runtime dependencies (`pandas`, `streamlit`). |
| `src/kool_elo/` | CLI modules: import JSON → SQLite, Elo replay, remote sync helpers. |
| `data/` | Local SQLite (online: `retro_elo.sqlite3`; Amiga 500: `offline_koatd.sqlite3`) plus sync manifest — **heavy files stay untracked** (see `.gitignore`). |

---

## Local usage

Create a virtual environment from the repo root (Windows/macOS/Linux).

```bash
pip install -r requirements.txt
streamlit run dashboard.py
```

Typical CLI loop (alternative to clicking **Sync now** / **Bootstrap** inside Streamlit):

```bash
python -m kool_elo.import_matches --overwrite
python -m kool_elo.compute_elo
```

The online ladder JSON is merged at `retro_results.json` (ignored + usually downloaded by `sync_remote_results` / the dashboard).

---

## Streamlit Community Cloud (handhold checklist)

These steps mirror what you configure in **[share.streamlit.io](https://share.streamlit.io/)** → **Create app**.

1. **Connect GitHub** and pick **this repo** + **`main`** (or your deployment branch).

2. **Main file**: `dashboard.py` (runs from repo root).

3. **Python version**: anything **3.10+** (the codebase uses postponed annotation evaluation everywhere).

### Secrets (**Settings → Secrets**)

Streamlit merges them into **`st.secrets`**; `dashboard.py` mirrors the keys listed below into `os.environ` so the same variables work locally and inside the subprocess pipelines.

Example `secrets.toml` (swap the URL string for your validated dump):

```toml
KOOL_REMOTE_RESULTS_URL = "https://example.org/your/online/AllResultsDump.php"
# Optional single guarded auto bootstrap on empty Cloud sandbox (see README)
KOOL_CLOUD_AUTO_BOOTSTRAP = "false"

# Uncomment if upstream is slow/unstable — seconds string
# KOOL_RESULTS_FETCH_TIMEOUT = "300"
```

Important:

- **`KOOL_REMOTE_RESULTS_URL`** — full HTTP(S) endpoint that returns **one JSON array** of rows with `GameID`, `StartTime`, `PlayerA`, `PlayerB`, `NameA`, `NameB`, `ScoreA`, `ScoreB`, `Duration`, etc.
- The built-in default in [`src/kool_elo/config.py`](src/kool_elo/config.py) targets the **authorised online ladder dump endpoint** for this project (including the `Q=` scope you use locally). Override with **`KOOL_REMOTE_RESULTS_URL`** in Secrets or env if Joshua issues a different URL or you intentionally switch accounts.

### First bootstrap on Cloud

On a fresh dyno **`data/` is empty**, so **`retro_elo.sqlite3` doesn't exist**:

1. Open the deployed URL.
2. Use **Bootstrap SQLite from online JSON** (downloads JSON + replay).
3. Optionally set **`KOOL_CLOUD_AUTO_BOOTSTRAP`** to `true`; `dashboard.py` writes `data/.cloud_auto_bootstrap_attempted` so every new browser visitor does **not** re-download immediately. Delete that file if you interrupted a failed bootstrap and need automation to rerun on the **same** Cloud instance.

Rebuilds mirror your local **`import_matches → compute_elo`** pipeline (`apply_import_and_elo` subprocess wrapper).

---

## Ephemeral storage caveats on Community Cloud

The Community Cloud filesystem is **not persistent long-term**:

- SQLite + manifests exist only for the lifetime of your running service / sandbox.
- **Every cold start** may repeat bootstrap unless Streamlit restores something from caches (do not rely on that for production KPIs).

For a hardened long-running PoC you'd typically host on a VPS or wire object storage—but for “public mirror I can poke from Discord” flows, rebuilding from the authoritative JSON occasionally is acceptable.

---

## Environment variables mirrored from Secrets (`dashboard.py`)

| Variable | Meaning |
|-----------|---------|
| `KOOL_REMOTE_RESULTS_URL` | Overrides default JSON dump everywhere (sidebar + pipelines). |
| `KOOL_AUTO_SYNC_ON_START` | `true` ⇒ download-hash check once at boot. |
| `KOOL_AUTO_SYNC_APPLY` | Paired flag ⇒ run import+Elo rebuild when hashes differ. |
| `KOOL_CLOUD_AUTO_BOOTSTRAP` | When SQLite is missing + flag is `true`, attempt once per dyno/`data/` folder guarded by **`data/.cloud_auto_bootstrap_attempted`**. Delete that file manually to retry automation after fixing upstream timeouts. |
| `KOOL_RESULTS_FETCH_TIMEOUT` | Pass-through to downloader seconds (string). |

---

## Iterating Cursor → GitHub → Cloud

1. Commit + push GitHub (**you** authenticate `git push`; token/SSH stays on your machines).
2. Streamlit reconnects hooks on **push** (`Redeploy` button if webhook missed).
3. Keep **Secrets** authoritative for URLs/throttles (`dashboard.py` does not mutate Git history).

Happy `vibe-coding`; keep experimentation on feature branches until you validate heavy sync jobs.

---

See also `memory.md` for richer design breadcrumbs (sandbox personal notes—not required reading for deploy).
