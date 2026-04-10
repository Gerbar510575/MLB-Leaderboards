# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend

```bash
uv sync                                              # install dependencies
uv run uvicorn backend.main:app --reload --port 8000 # start dev server
uv run python main.py                                # alternative startup
uv run pytest tests/ -v                              # run all tests (131 tests)
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
curl "http://localhost:8000/api/v1/seasons"
curl "http://localhost:8000/api/v1/leaderboard?metric_name=exit_velocity&limit=50&min_requirement=5"
curl "http://localhost:8000/api/v1/leaderboard?metric_name=exit_velocity&year=2024&limit=50"
curl -si "http://localhost:8000/api/v1/leaderboard?metric_name=exit_velocity&limit=50&min_requirement=5" | grep X-Cache-Hit

# Cache
curl "http://localhost:8000/api/v1/cache/stats"
curl -X DELETE "http://localhost:8000/api/v1/cache"

# Real data refresh (requires network; runs in background ~30-45 s with all 6 sources)
curl -X POST "http://localhost:8000/api/v1/data/refresh?year=2026"
curl "http://localhost:8000/api/v1/data/status"   # includes scheduler.next_run

# Fantasy roster sync (requires Yahoo OAuth credentials in .env)
curl -X POST "http://localhost:8000/api/v1/fantasy/sync"   # returns events count
curl "http://localhost:8000/api/v1/fantasy/status"

# SQLite history (inspect directly)
sqlite3 backend/data/mlb_history.db "SELECT COUNT(*) FROM stat_snapshots;"
sqlite3 backend/data/mlb_history.db "SELECT * FROM fantasy_events ORDER BY event_at DESC LIMIT 10;"
```

## Architecture

### Backend modules

The backend is split into focused modules under `backend/`:

| Module | Responsibility |
|--------|---------------|
| `main.py` | FastAPI app + route definitions only |
| `config.py` | `pydantic-settings` — all tuneable values, reads `.env` |
| `cache.py` | `CACHE_TTL`, `_cache` dict, `@ttl_cache` decorator |
| `fetcher.py` | `_blocking_fetch()`, `_compute_leaderboard()`, `load_data()` |
| `adapters.py` | `DataSourceAdapter` Protocol + `PybaseballAdapter` concrete impl |
| `scheduler.py` | `_run_refresh()`, APScheduler job functions, `_refresh_job` state |
| `db.py` | SQLite init + all read/write functions |
| `fantasy.py` | `_do_fantasy_sync()`, `_detect_fantasy_events()`, `normalize_name()` |

### `backend/config.py`

All tuneable values live here as a `pydantic-settings` `Settings` class. Override any value via `.env`:

```python
cache_ttl: int = 300                 # TTL cache seconds
stats_refresh_hour: int = 10         # daily refresh hour (ET)
stats_refresh_minute: int = 0
stats_refresh_months: str = "3-10"   # Mar–Oct only
stats_refresh_tz: str = "America/New_York"
fantasy_sync_interval_hours: int = 1
# default_refresh_year is a @property: datetime.now(UTC).year
```

### `backend/fetcher.py` — key design decisions

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

### `backend/adapters.py` — DataSourceAdapter

`DataSourceAdapter` is a `@runtime_checkable Protocol` with six methods (one per data source). `PybaseballAdapter` is the production impl. Pass a custom adapter to `_blocking_fetch(year, adapter=MyAdapter())` for testing or alternative data sources.

### `backend/db.py` — SQLite history

Three tables:
- `stat_snapshots`: every metric record from every refresh. Written by `_write_stat_snapshot(aggregates, snapshot_at)` after each successful `_run_refresh()`. Indexed on `(player_id, metric_name, snapshot_at)`.
- `fantasy_events`: ownership change events only (pickup / drop / trade). Written by `_write_fantasy_events(events)` when `_detect_fantasy_events()` finds diffs. First-ever sync is skipped (no old index to compare). Indexed on `(match_key, event_at)`.
- `players`: player metadata (name / team / position) upserted on every refresh via `_upsert_players()`. Used by `_query_historical_snapshot()` to enrich historical rows.

Key read functions:
- `_get_available_seasons() -> list[int]` — distinct years from `stat_snapshots`, used by `GET /api/v1/seasons`
- `_query_historical_snapshot(year, metric_name, min_requirement) -> list[dict]` — finds the most-recent snapshot in `year`, JOINs `players`, returns a ranked + percentile-scored list

`_init_db()` runs at startup via `lifespan` using `CREATE TABLE IF NOT EXISTS` — safe to call repeatedly. All DB writes are non-fatal: wrapped in `try/except`, log `warning` on failure, never interrupt the main refresh/sync flow. `DB_PATH = backend/data/mlb_history.db` (gitignored; `backend/data/.gitkeep` ensures the directory is tracked).

**`_ASCENDING_METRICS` in `db.py`** — a `frozenset` copy of `fetcher._ASCENDING_METRICS`. Must stay in sync when adding new pitcher metrics where lower = better. Kept separate to avoid importing `fetcher` into `db`.

### `backend/main.py` — routes only

Routes and their key behaviours:

- `GET /api/v1/leaderboard?metric_name=&limit=&min_requirement=&year=` — if `year` is provided and differs from the current year, queries `db._query_historical_snapshot()`; otherwise uses the TTL-cached live path. Both paths return `"year"` in the response body.
- `GET /api/v1/seasons` — returns distinct years available in `stat_snapshots` via `db._get_available_seasons()`.
- `POST /api/v1/data/refresh?year=` — starts background refresh; defaults to `settings.default_refresh_year`.

**Fantasy roster** — `_fantasy_index` is a plain global dict in `backend/fantasy.py`. `POST /api/v1/fantasy/sync` dynamically imports `player_list.py` from `yahoo-fantasy-agent` via `sys.path.insert` (zero modifications to that project). Both Fantasy sync and data refresh call `_cache.clear()` after completion.

**`normalize_name()`** — verbatim copy of `yahoo-fantasy-agent/player_list.py::normalize_name`. Used exclusively for the Fantasy ownership JOIN. Must stay in sync with the original.

### Frontend (`frontend/src/`)

Components are split:

| File | Responsibility |
|------|---------------|
| `App.jsx` | Root — wraps `<Leaderboard>` in `<ErrorBoundary>` |
| `components/ErrorBoundary.jsx` | Class component; catches render errors, shows friendly UI |
| `components/Leaderboard.jsx` | State + data fetch (metric, limit, minReq, year, availableSeasons) |
| `components/FilterBar.jsx` | Metric / Season / Limit / MinReq selectors + 3 action buttons |
| `components/LeaderboardTable.jsx` | Table rendering (rank, percentile, player info, fantasy badge) |
| `components/PercentileLegend.jsx` | Colour key legend |
| `components/SchedulerStatus.jsx` | Last-fetched / next-run status bar |
| `utils/format.js` | `FORMAT_CONFIG`, `METRIC_LABELS`, `formatValue()`, `getPercentileStyle()`, `LEGEND` |

**`FORMAT_CONFIG`** — the canonical place to add or modify metric display (suffix, decimals, `stripLeadingZero` for xBA-style metrics, `showSign` for diff metrics). Adding a new metric requires: `FORMAT_CONFIG` entry + `METRIC_LABELS` entry + backend `_METRIC_NAMES` + the appropriate source dict.

**`getPercentileStyle(pct)`** in `utils/format.js` — implements the Baseball Savant "Red Hot" colour convention: red = elite (90–100), blue = low (0–19). Text colour is `white` for all tiers except 40–69 (light gray background), which uses `#1f2937` for contrast.

**Season selector** — `Leaderboard.jsx` fetches `/api/v1/seasons` on mount. If historical years are available, `FilterBar` shows a Season dropdown. Selecting a year passes `?year=YYYY` to the leaderboard API and shows a `Historical YYYY` badge in the header.

**Three action buttons in Filter Bar:**
- Blue `Refresh` — re-fetches current leaderboard from backend cache
- Amber `Refresh Stats` — triggers `POST /api/v1/data/refresh`, polls `/data/status` every 2 s (max 60 s), then reloads leaderboard
- Purple `Sync Fantasy` — triggers `POST /api/v1/fantasy/sync`, then reloads leaderboard

The frontend dev server proxies `/api/*` to `http://localhost:8000` via `vite.config.js`, so no CORS handling is needed during development.

### Tests (`tests/`)

```
tests/
├── conftest.py          # SAMPLE_PLAYERS, SAMPLE_AGGREGATES, client fixture (patches DB_PATH + REAL_DATA_PATH)
├── test_api.py          # All API endpoints via TestClient (leaderboard, metrics, seasons, cache, refresh, fantasy)
├── test_leaderboard.py  # _compute_leaderboard() — sort order, min_requirement, rank/percentile, TTL cache, fantasy
├── test_fetcher.py      # _blocking_fetch() — all 6 sources, field mapping, graceful degradation, FakeAdapter
├── test_db.py           # _init_db, _upsert_players, _write_stat_snapshot, _get_available_seasons, _query_historical_snapshot
└── test_utils.py        # normalize_name(), _detect_fantasy_events()
```

Run with: `uv run pytest tests/ -v`

### Data Flow

```
POST /api/v1/data/refresh
  → _run_refresh() [BackgroundTask]
    → _blocking_fetch(year, PybaseballAdapter()) [run_in_executor]
      → Source 1: fetch_batter_ev()            ← Savant (EV/LA/HH/Brl)
      → Source 2: fetch_batter_expected()      ← Savant (xBA/xSLG/xwOBA)
      → Source 3: fetch_sprint_speed()         ← Savant (Sprint Speed + team/pos)
      → Source 4: fetch_pitcher_expected()     ← Savant (xERA/ERA-xERA/xwOBA-against)
      → Source 5: fetch_pitcher_ev()           ← Savant (HH%/Brl%/EV against)
      → Source 6: fetch_pitcher_bref()         ← Baseball Reference (K/9/BB/9/K-BB%)
      → join batters by player_id; join pitchers by player_id / mlbID (all MLBAM ID)
    → os.replace(real_data.json.tmp → real_data.json)  ← atomic write
    → _cache.clear()
    → _write_stat_snapshot(aggregates, fetched_at)      ← SQLite append (non-fatal)
    → _upsert_players(players, fetched_at)              ← SQLite upsert (non-fatal)

GET /api/v1/leaderboard?year=YYYY          ← historical path
  → db._query_historical_snapshot(year, metric, minReq)
    → MAX(snapshot_at) WHERE year=YYYY → LEFT JOIN players → sorted + ranked

GET /api/v1/leaderboard                    ← current-season path
  → load_data()                            ← real_data.json (or {} / [] if absent → 404)
  → _compute_leaderboard(metric, minReq)   [TTL cached 5 min]
    → sorted(reverse=(metric not in _ASCENDING_METRICS))
    → inject _fantasy_index
  → result[:limit]
```
