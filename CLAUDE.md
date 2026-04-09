# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend

```bash
uv sync                                              # install dependencies
uvicorn backend.main:app --reload --port 8000        # start dev server
python main.py                                       # alternative startup
```

### Frontend

```bash
cd frontend
npm install
npm run dev      # dev server → http://localhost:5173
npm run build    # production build → frontend/dist/
```

### Verify API

```bash
# Core leaderboard
curl "http://localhost:8000/api/v1/metrics"
curl "http://localhost:8000/api/v1/leaderboard?metric_name=exit_velocity&limit=50&min_requirement=5"
curl -si "http://localhost:8000/api/v1/leaderboard?metric_name=exit_velocity&limit=50&min_requirement=5" | grep X-Cache-Hit

# Cache
curl "http://localhost:8000/api/v1/cache/stats"
curl -X DELETE "http://localhost:8000/api/v1/cache"

# Real data refresh (requires network; runs in background ~10-20 s)
curl -X POST "http://localhost:8000/api/v1/data/refresh?year=2026"
curl "http://localhost:8000/api/v1/data/status"

# Fantasy roster sync (requires Yahoo OAuth credentials in .env)
curl -X POST "http://localhost:8000/api/v1/fantasy/sync"
curl "http://localhost:8000/api/v1/fantasy/status"
```

## Architecture

### Backend (`backend/main.py`)

Single-file FastAPI app. Key design decisions:

**Cache key excludes `limit`** — `_compute_leaderboard(metric_name, min_requirement)` computes the full leaderboard and caches it. The API route slices by `limit` after the cache lookup. This means `limit=50` and `limit=200` share the same cache entry.

**`X-Cache-Hit` response header** — the route peeks at `_cache` before calling the cached function to determine whether the result will be a hit, then sets `X-Cache-Hit: true/false` on the response. The frontend displays this as a badge.

**Real data only** — `load_data()` returns `(players_dict, aggregates)`. It reads `real_data.json`; if absent it returns `({}, [])` and the leaderboard endpoint raises HTTP 404 with a message prompting the user to click "Refresh Stats". There is no mock fallback.

**Real data schema (`real_data.json`)** — top-level keys: `source`, `season`, `fetched_at`, `players`, `aggregates`. Each aggregate record includes `player_id`, `metric_name`, `avg_value`, `sample_size`, and `sample_type` (`"BBE"` for Statcast metrics, `"PA"` for xBA). `player_id` is the MLBAM integer stored as a string.

**Real data refresh** — `POST /api/v1/data/refresh` starts a FastAPI `BackgroundTask` and returns immediately (`{"status": "processing"}`). The background task calls `_blocking_fetch()` in a thread pool, which fetches from 6 sources in sequence, then writes via atomic `os.replace()`. All pitcher sources are wrapped in `try/except` so one failing source doesn't abort the refresh. Poll `GET /api/v1/data/status` (`refresh_job.status`: `idle` → `processing` → `done`/`error`). HTTP 409 is returned if a refresh is already running. A full refresh with all 6 sources takes ~30–45 s.

**Six data sources in `_blocking_fetch()`:**
1. `statcast_batter_exitvelo_barrels(year, minBBE=1)` → EV, Launch Angle, Hard Hit%, Barrel% (sample_type=`"BBE"`, count=`attempts`)
2. `statcast_batter_expected_stats(year, minPA=1)` → xBA, xSLG, xwOBA, xwOBA-diff (sample_type=`"PA"`, count=`pa`)
3. `statcast_sprint_speed(year, min_opp=1)` → Sprint Speed (sample_type=`"sprints"`, count=`competitive_runs`); also provides `team` and `position` for all hitters via `team_pos_by_id`
4. `statcast_pitcher_expected_stats(year, minPA=1)` → xERA, ERA−xERA, xwOBA Against (sample_type=`"PA"`)
5. `statcast_pitcher_exitvelo_barrels(year, minBBE=1)` → Hard Hit% Against, Barrel% Against, Avg EV Against (sample_type=`"BBE"`)
6. `pitching_stats_bref(year)` (Baseball Reference) → K/9, BB/9, K-BB% (sample_type=`"PA"`, count=`BF`); joined via `mlbID` = MLBAM ID. Note: `pybaseball.pitching_stats()` (FanGraphs) is blocked (HTTP 403) so BRef is used instead.

**Baseball Savant column names** — All Savant pitcher/batter endpoints return a single `'last_name, first_name'` column (e.g. `"Judge, Aaron"`), reversed by `_reverse_name()` to reconstruct full name. Batter EV columns: `avg_hit_speed`, `avg_hit_angle`, `ev95percent`, `brl_percent` (sample size = `attempts`). Batter xStats: `est_ba`, `est_slg`, `est_woba`, `woba_minus_xwoba_diff` (sample size = `pa`). Pitcher xStats: `xera`, `era_minus_xera_diff`, `est_woba` (sample size = `pa`). Pitcher EV: same columns as batter EV (sample size = `attempts`).

**`_SAVANT_METRIC_MAP`** — maps batter `metric_name` → Savant CSV column (source 1):
```python
{
    "exit_velocity": "avg_hit_speed",
    "launch_angle":  "avg_hit_angle",
    "hard_hit_rate": "ev95percent",
    "barrel_rate":   "brl_percent",
}
```

**`_PITCHER_EXP_COLS` / `_PITCHER_EV_COLS`** — analogous dicts for pitcher sources 4 and 5:
```python
_PITCHER_EXP_COLS = {"p_xera": "xera", "p_era_diff": "era_minus_xera_diff", "p_xwoba_against": "est_woba"}
_PITCHER_EV_COLS  = {"p_hard_hit_rate": "ev95percent", "p_barrel_rate": "brl_percent", "p_avg_ev": "avg_hit_speed"}
```

**`_ASCENDING_METRICS`** — pitcher metrics where lower value = better rank (sorted ascending, index-0 gets percentile 100):
```python
_ASCENDING_METRICS: set[str] = {"p_xera", "p_xwoba_against", "p_hard_hit_rate", "p_barrel_rate", "p_avg_ev", "p_bb9"}
```
`_compute_leaderboard()` uses `reverse=(metric_name not in _ASCENDING_METRICS)` in its `sorted()` call.

**`_safe_float(v)`** — helper that converts a value to `float` or `None`, swallowing `NaN`, `None`, and type errors. Used when iterating BRef rows where missing values can be `float('nan')`, `None`, or non-numeric strings.

**Fantasy roster** — `_fantasy_index` is a plain global dict (not in TTL cache). `POST /api/v1/fantasy/sync` dynamically imports `player_list.py` from `yahoo-fantasy-agent` via `sys.path.insert` (zero modifications to that project). Both Fantasy sync and data refresh call `_cache.clear()` after completion.

**`normalize_name()`** — verbatim copy of `yahoo-fantasy-agent/player_list.py::normalize_name`. Used exclusively for the Fantasy ownership JOIN (match Statcast player names to Yahoo roster names). Both Statcast data sources share `player_id` (MLBAM ID) so no name matching is needed for data joining. Must stay in sync with the original.

### Frontend (`frontend/src/components/Leaderboard.jsx`)

Single component. Key things to know:

**`FORMAT_CONFIG`** — the canonical place to add or modify metric display (suffix, decimals, `stripLeadingZero` for xBA-style metrics, `showSign` for diff metrics). Adding a new batter metric from Savant requires: `FORMAT_CONFIG` entry + `_METRIC_NAMES` + `_SAVANT_METRIC_MAP` (or `_XBA_COLS`). Adding a new pitcher metric requires: `FORMAT_CONFIG` entry + `_METRIC_NAMES` + the appropriate backend dict (`_PITCHER_EXP_COLS`, `_PITCHER_EV_COLS`, or BRef calculation) + `_ASCENDING_METRICS` if lower = better. Also add `METRIC_LABELS` entry in the frontend.

**`getPercentileStyle(pct)`** — implements the Baseball Savant "Red Hot" colour convention: red = elite (90–100), blue = low (0–19). Text colour is `white` for all tiers except 40–69 (light gray background), which uses `#1f2937` for contrast.

**Three action buttons in Filter Bar:**
- Blue `Refresh` — re-fetches current leaderboard from backend cache
- Amber `Refresh Stats` — triggers `POST /api/v1/data/refresh`, polls `/data/status` every 2 s (max 60 s), then reloads leaderboard
- Purple `Sync Fantasy` — triggers `POST /api/v1/fantasy/sync`, then reloads leaderboard

The frontend dev server proxies `/api/*` to `http://localhost:8000` via `vite.config.js`, so no CORS handling is needed during development.

### Data Flow

```
POST /api/v1/data/refresh
  → _run_refresh() [BackgroundTask]
    → _blocking_fetch() [run_in_executor]
      → Source 1: statcast_batter_exitvelo_barrels(year)     ← Savant (EV/LA/HH/Brl)
      → Source 2: statcast_batter_expected_stats(year)       ← Savant (xBA/xSLG/xwOBA)
      → Source 3: statcast_sprint_speed(year)                ← Savant (Sprint Speed + team/pos)
      → Source 4: statcast_pitcher_expected_stats(year)      ← Savant (xERA/ERA-xERA/xwOBA-against)
      → Source 5: statcast_pitcher_exitvelo_barrels(year)    ← Savant (HH%/Brl%/EV against)
      → Source 6: pitching_stats_bref(year)                  ← Baseball Reference (K/9/BB/9/K-BB%)
      → join batters by player_id; join pitchers by player_id / mlbID (all MLBAM ID)
    → os.replace(real_data.json.tmp → real_data.json)        ← atomic write
    → _cache.clear()

GET /api/v1/leaderboard
  → load_data()                                  ← real_data.json (or {} / [] if absent → 404)
  → _compute_leaderboard(metric, minReq)            [TTL cached 5 min]
    → sorted(reverse=(metric not in _ASCENDING_METRICS))
    → inject _fantasy_index
  → result[:limit]
```
