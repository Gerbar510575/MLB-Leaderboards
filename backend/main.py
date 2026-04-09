from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler (module-level so routes can read next_run_time)
# ---------------------------------------------------------------------------
_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    _init_db()
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scheduled_fantasy_sync,
        IntervalTrigger(hours=1),
        id="fantasy_sync",
        name="Yahoo Fantasy Roster Sync",
        replace_existing=True,
    )
    _scheduler.add_job(
        _scheduled_stats_refresh,
        CronTrigger(hour=10, minute=0, month="3-10", timezone="America/New_York"),
        id="stats_refresh",
        name="Baseball Savant Stats Refresh",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started. Jobs: %s", [j.id for j in _scheduler.get_jobs()])
    yield
    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="MLB Leaderboards API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Cache-Hit"],
)

# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------
_cache: dict[tuple, tuple[Any, float]] = {}
CACHE_TTL = 300  # 5 minutes


def ttl_cache(ttl: int = CACHE_TTL):
    """
    Decorator that caches a function's return value for `ttl` seconds.

    Cache strategy:
      - key = (func_name, positional_args, sorted_kwargs)
      - On HIT  → return cached result directly (O(1), no I/O)
      - On MISS → compute, store with monotonic timestamp, return result
      - TTL expiry is lazy: stale entries are evicted on next access
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = (func.__name__, args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            if key in _cache:
                cached_result, cached_at = _cache[key]
                if now - cached_at < ttl:
                    return cached_result          # Cache HIT
            result = func(*args, **kwargs)        # Cache MISS → recompute
            _cache[key] = (result, now)
            return result
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Name normalisation — identical copy of player_list.py::normalize_name
# Must stay in sync with yahoo-fantasy-agent/player_list.py
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """
    Canonical name normalisation used for Fantasy roster JOIN.
    Examples: "Ronald Acuña Jr." → "ronald acuna", "Tyler O'Neill" → "tyler oneill"
    """
    if not name:
        return ""
    name = name.lower().strip()
    name = "".join(c for c in unicodedata.normalize('NFD', name)
                   if unicodedata.category(c) != 'Mn')
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    name = re.sub(r"['.]", "", name)
    return " ".join(name.split())


# ---------------------------------------------------------------------------
# Fantasy Index — plain global dict, NOT in TTL cache
# ---------------------------------------------------------------------------
_fantasy_index: dict[str, str] = {}   # match_key → fantasy_team_name
_fantasy_synced_at: str | None = None

FANTASY_PATH = os.path.join(os.path.dirname(__file__), "data", "fantasy_roster.json")


def _load_fantasy_index() -> None:
    """Load persisted fantasy roster into memory on startup."""
    global _fantasy_index, _fantasy_synced_at
    if not os.path.exists(FANTASY_PATH):
        return
    with open(FANTASY_PATH) as f:
        data = json.load(f)
    _fantasy_index = {p["match_key"]: p["fantasy_team"] for p in data.get("players", [])}
    _fantasy_synced_at = data.get("synced_at")


_load_fantasy_index()


# ---------------------------------------------------------------------------
# Data Source Metadata & Real Data Path
# ---------------------------------------------------------------------------
_data_source_meta: dict = {"source": "mock", "season": None, "fetched_at": None}
_refresh_job: dict = {"status": "idle", "started_at": None, "error": None}

REAL_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "real_data.json")
DB_PATH        = os.path.join(os.path.dirname(__file__), "data", "mlb_history.db")


def _init_db() -> None:
    """Create SQLite tables if they don't exist. Safe to call on every startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stat_snapshots (
                id          INTEGER PRIMARY KEY,
                snapshot_at TEXT NOT NULL,
                player_id   TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                avg_value   REAL NOT NULL,
                sample_size INTEGER,
                sample_type TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_stat
                ON stat_snapshots(player_id, metric_name, snapshot_at);

            CREATE TABLE IF NOT EXISTS fantasy_events (
                id          INTEGER PRIMARY KEY,
                event_at    TEXT NOT NULL,
                player_name TEXT NOT NULL,
                match_key   TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                from_team   TEXT,
                to_team     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fantasy
                ON fantasy_events(match_key, event_at);
        """)
    logger.info("DB initialised: %s", DB_PATH)


# Available metrics (used by /api/v1/metrics endpoint)
_METRIC_NAMES: list[str] = [
    "barrel_rate", "exit_velocity", "hard_hit_rate", "launch_angle",
    "p_avg_ev", "p_barrel_rate", "p_bb9", "p_era_diff",
    "p_hard_hit_rate", "p_k9", "p_k_bb_diff",
    "p_xera", "p_xwoba_against",
    "sprint_speed", "xba", "xslg", "xwoba", "xwoba_diff",
]

# Pitcher metrics where lower value = better (sorted ascending)
_ASCENDING_METRICS: set[str] = {
    "p_xera", "p_xwoba_against",
    "p_hard_hit_rate", "p_barrel_rate", "p_avg_ev",
    "p_bb9",
}


def load_data() -> tuple[dict[str, dict], list[dict]]:
    """
    Returns (players_dict, aggregates) from real_data.json.
    Returns ({}, []) if real_data.json does not exist yet.
    Updates _data_source_meta as a side effect.
    """
    global _data_source_meta

    if not os.path.exists(REAL_DATA_PATH):
        _data_source_meta = {"source": "none", "season": None, "fetched_at": None}
        return {}, []

    with open(REAL_DATA_PATH) as f:
        raw = json.load(f)
    players_dict = {p["player_id"]: p for p in raw["players"]}
    _data_source_meta = {
        "source":     raw.get("source", "real"),
        "season":     raw.get("season"),
        "fetched_at": raw.get("fetched_at"),
    }
    return players_dict, raw["aggregates"]


# ---------------------------------------------------------------------------
# Leaderboard computation — cache key excludes `limit`
# ---------------------------------------------------------------------------
@ttl_cache(ttl=CACHE_TTL)
def _compute_leaderboard(metric_name: str, min_requirement: int) -> list[dict]:
    """
    Computes a full leaderboard (up to 100 entries) for the given metric.

    The `limit` parameter is intentionally excluded from the cache key.
    All `limit` variants (e.g. 10, 20, 50) share this single cached result;
    slicing happens at the API layer, maximising cache hit rate.

    Supports both real data (aggregates) and mock data (all_records).
    Fantasy ownership is injected here from _fantasy_index (plain global dict).
    """
    players_dict, aggregates = load_data()

    aggregated: list[dict] = []
    for rec in aggregates:
        if rec["metric_name"] != metric_name:
            continue
        if rec["sample_size"] < min_requirement:
            continue
        player = players_dict.get(rec["player_id"], {})
        match_key = normalize_name(player.get("player_name", ""))
        fantasy_team = _fantasy_index.get(match_key)
        aggregated.append({
            "player_id":   rec["player_id"],
            "player_name": player.get("player_name", rec["player_id"]),
            "team":        player.get("team", ""),
            "position":    player.get("position", ""),
            "avg_value":   rec["avg_value"],
            "sample_size": rec["sample_size"],
            "sample_type": rec.get("sample_type", ""),
            "fantasy_team": fantasy_team,
            "is_owned":    fantasy_team is not None,
        })

    # Sort: ascending for pitcher "lower is better" metrics, descending otherwise
    ranked = sorted(aggregated, key=lambda x: x["avg_value"],
                    reverse=(metric_name not in _ASCENDING_METRICS))

    # Assign rank and percentile (rank=1 → 100th percentile)
    n = len(ranked)
    for i, entry in enumerate(ranked):
        entry["rank"] = i + 1
        entry["percentile"] = round((n - 1 - i) / max(n - 1, 1) * 100) if n > 1 else 100

    return ranked


# ---------------------------------------------------------------------------
# Real Data Fetch (pybaseball)
# ---------------------------------------------------------------------------

# Column mapping: metric_name → actual Baseball Savant CSV column name (EV leaderboard)
_SAVANT_METRIC_MAP: dict[str, str] = {
    "exit_velocity": "avg_hit_speed",
    "launch_angle":  "avg_hit_angle",
    "hard_hit_rate": "ev95percent",
    "barrel_rate":   "brl_percent",
}

# Column mapping: metric_name → expected stats CSV column name
_XBA_COLS: dict[str, str] = {
    "xba":        "est_ba",
    "xslg":       "est_slg",
    "xwoba":      "est_woba",
    "xwoba_diff": "est_woba_minus_woba_diff",
}

# Pitcher column mappings (Baseball Savant)
_PITCHER_EXP_COLS: dict[str, str] = {
    "p_xera":          "xera",
    "p_era_diff":      "era_minus_xera_diff",
    "p_xwoba_against": "est_woba",
}

_PITCHER_EV_COLS: dict[str, str] = {
    "p_hard_hit_rate": "ev95percent",
    "p_barrel_rate":   "brl_percent",
    "p_avg_ev":        "avg_hit_speed",
}


def _blocking_fetch(year: int) -> dict:
    """
    Runs blocking pybaseball I/O. Called via run_in_executor.
    Raises ValueError with a human-readable message on empty/bad data.
    Does NOT write any files — caller handles persistence.
    """
    import pybaseball  # deferred — avoid ImportError at startup

    # ── Source 1: Baseball Savant exit velocity / barrel leaderboard ──────
    ev_df = pybaseball.statcast_batter_exitvelo_barrels(year, minBBE=1)
    if ev_df is None or ev_df.empty:
        raise ValueError(
            f"No Statcast batted-ball data for {year} — "
            "the season may not have started yet or Baseball Savant is unavailable."
        )

    # Name column is "last_name, first_name" (single col, value like "Judge, Aaron")
    ev_df = ev_df.copy()
    def _reverse_name(raw: str) -> str:
        parts = str(raw).split(", ", 1)
        return (parts[1].strip() + " " + parts[0].strip()) if len(parts) == 2 else raw.strip()
    ev_df["full_name"] = ev_df["last_name, first_name"].apply(_reverse_name)
    ev_df["player_id_str"] = ev_df["player_id"].astype(str)
    logger.info("ev_df columns: %s", ev_df.columns.tolist())

    # ── Source 2: Baseball Savant expected stats for xBA ─────────────────
    xba_df = pybaseball.statcast_batter_expected_stats(year, minPA=1)
    if xba_df is None or xba_df.empty:
        raise ValueError(
            f"No Baseball Savant expected stats for {year} — "
            "the season may not have started yet or Baseball Savant is unavailable."
        )

    # player_id is shared with ev_df — direct join, no name matching needed
    xba_df = xba_df.copy()
    xba_df["player_id_str"] = xba_df["player_id"].astype(str)
    logger.info("xba_df columns: %s", xba_df.columns.tolist())

    xba_by_id: dict[str, dict] = {}
    for _, row in xba_df.iterrows():
        pid = str(row["player_id_str"])
        entry: dict = {"pa": int(row.get("pa", 0) or 0)}
        for stat, col in _XBA_COLS.items():
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                entry[stat] = float(val)
        xba_by_id[pid] = entry

    # ── Source 3: Sprint Speed (also provides team / position) ───────────
    speed_df = None
    team_pos_by_id: dict[str, dict] = {}
    speed_by_id: dict[str, dict] = {}
    try:
        speed_df = pybaseball.statcast_sprint_speed(year, min_opp=1)
        if speed_df is not None and not speed_df.empty:
            logger.info("speed_df columns: %s", speed_df.columns.tolist())
            speed_df = speed_df.copy()
            for _, row in speed_df.iterrows():
                raw_pid = row.get("player_id")
                if raw_pid is None:
                    continue
                try:
                    pid = str(int(float(raw_pid)))
                except (ValueError, TypeError):
                    continue
                team_pos_by_id[pid] = {
                    "team":     str(row.get("team", "") or ""),
                    "position": str(row.get("position", "") or ""),
                }
                val = row.get("sprint_speed")
                if val is not None and not (isinstance(val, float) and math.isnan(val)):
                    speed_by_id[pid] = {
                        "speed":   float(val),
                        "runs":    int(row.get("competitive_runs", 0) or 0),
                    }
    except Exception as exc:
        logger.warning("Sprint speed fetch failed (non-fatal): %s", exc)

    # ── Source 4: Pitcher Expected Stats (xERA / ERA-xERA / xwOBA against) ─
    pitcher_exp_by_id: dict[str, dict] = {}
    pitcher_names: dict[str, str] = {}
    try:
        p_exp_df = pybaseball.statcast_pitcher_expected_stats(year, minPA=1)
        if p_exp_df is not None and not p_exp_df.empty:
            p_exp_df = p_exp_df.copy()
            p_exp_df["full_name"] = p_exp_df["last_name, first_name"].apply(_reverse_name)
            p_exp_df["player_id_str"] = p_exp_df["player_id"].astype(str)
            for _, row in p_exp_df.iterrows():
                pid = row["player_id_str"]
                pitcher_names[pid] = row["full_name"]
                entry: dict = {"pa": int(row.get("pa", 0) or 0)}
                for stat, col in _PITCHER_EXP_COLS.items():
                    val = row.get(col)
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        entry[stat] = float(val)
                pitcher_exp_by_id[pid] = entry
    except Exception as exc:
        logger.warning("Pitcher expected stats fetch failed (non-fatal): %s", exc)

    # ── Source 5: Pitcher EV/Barrels (contact quality against) ───────────
    pitcher_ev_by_id: dict[str, dict] = {}
    try:
        p_ev_df = pybaseball.statcast_pitcher_exitvelo_barrels(year, minBBE=1)
        if p_ev_df is not None and not p_ev_df.empty:
            p_ev_df = p_ev_df.copy()
            p_ev_df["full_name"] = p_ev_df["last_name, first_name"].apply(_reverse_name)
            p_ev_df["player_id_str"] = p_ev_df["player_id"].astype(str)
            for _, row in p_ev_df.iterrows():
                pid = row["player_id_str"]
                if pid not in pitcher_names:
                    pitcher_names[pid] = row["full_name"]
                bbe = int(row.get("attempts", 0) or 0)
                ev_entry: dict = {"bbe": bbe}
                for stat, col in _PITCHER_EV_COLS.items():
                    val = row.get(col)
                    if val is not None and not (isinstance(val, float) and math.isnan(val)):
                        ev_entry[stat] = float(val)
                pitcher_ev_by_id[pid] = ev_entry
    except Exception as exc:
        logger.warning("Pitcher EV/barrels fetch failed (non-fatal): %s", exc)

    # ── Source 6: Baseball Reference pitching stats (K/9, BB/9, K-BB%) ───
    def _safe_float(v) -> float | None:
        try:
            f = float(v)
            return None if math.isnan(f) else f
        except (TypeError, ValueError):
            return None

    pitcher_bref_by_id: dict[str, dict] = {}
    try:
        bref_df = pybaseball.pitching_stats_bref(year)
        if bref_df is not None and not bref_df.empty:
            bref_df = bref_df.copy()
            for _, row in bref_df.iterrows():
                raw_id = row.get("mlbID")
                if raw_id is None or (isinstance(raw_id, float) and math.isnan(float(raw_id))):
                    continue
                try:
                    pid = str(int(float(raw_id)))
                except (ValueError, TypeError):
                    continue
                so9_f = _safe_float(row.get("SO9"))
                bb_f  = _safe_float(row.get("BB"))
                ip_f  = _safe_float(row.get("IP"))
                so_f  = _safe_float(row.get("SO"))
                bf_f  = _safe_float(row.get("BF"))
                bref_entry: dict = {"bf": int(bf_f) if bf_f is not None else 0}
                if so9_f is not None:
                    bref_entry["p_k9"] = round(so9_f, 2)
                if bb_f is not None and ip_f and ip_f > 0:
                    bref_entry["p_bb9"] = round(bb_f / ip_f * 9, 2)
                if so_f is not None and bb_f is not None and bf_f and bf_f > 0:
                    bref_entry["p_k_bb_diff"] = round((so_f - bb_f) / bf_f * 100, 2)
                pitcher_bref_by_id[pid] = bref_entry
    except Exception as exc:
        logger.warning("Baseball Reference pitching stats fetch failed (non-fatal): %s", exc)

    # ── Build players table ───────────────────────────────────────────────
    players: list[dict] = []
    seen_ids: set[str] = set()
    for _, row in ev_df.iterrows():
        pid = row["player_id_str"]
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        tp = team_pos_by_id.get(pid, {})
        players.append({
            "player_id":   pid,
            "player_name": row["full_name"],
            "team":        tp.get("team", ""),
            "position":    tp.get("position", ""),
        })

    # Add pitchers not already in the batter list
    for pid, name in pitcher_names.items():
        if pid in seen_ids:
            continue
        seen_ids.add(pid)
        players.append({
            "player_id":   pid,
            "player_name": name,
            "team":        team_pos_by_id.get(pid, {}).get("team", ""),
            "position":    "P",
        })

    # ── Build aggregates ──────────────────────────────────────────────────
    aggregates: list[dict] = []

    for _, row in ev_df.iterrows():
        pid = row["player_id_str"]
        bbe = int(row.get("attempts", 0) or 0)
        for metric_name, col in _SAVANT_METRIC_MAP.items():
            raw_val = row.get(col)
            if raw_val is None or (isinstance(raw_val, float) and math.isnan(raw_val)):
                continue
            aggregates.append({
                "player_id":   pid,
                "metric_name": metric_name,
                "avg_value":   round(float(raw_val), 3),
                "sample_size": bbe,
                "sample_type": "BBE",
            })

    # xBA / xSLG / xwOBA / xwOBA-diff: direct MLBAM ID join (same source)
    for p in players:
        pid = p["player_id"]
        xba_entry = xba_by_id.get(pid)
        if xba_entry is None:
            continue
        pa = xba_entry["pa"]
        for stat in ("xba", "xslg", "xwoba", "xwoba_diff"):
            val = xba_entry.get(stat)
            if val is None:
                continue
            aggregates.append({
                "player_id":   pid,
                "metric_name": stat,
                "avg_value":   round(val, 3),
                "sample_size": pa,
                "sample_type": "PA",
            })

    # Sprint speed aggregates (speed_by_id built above with team/pos)
    for p in players:
        pid = p["player_id"]
        entry = speed_by_id.get(pid)
        if entry is None:
            continue
        aggregates.append({
            "player_id":   pid,
            "metric_name": "sprint_speed",
            "avg_value":   round(entry["speed"], 1),
            "sample_size": entry["runs"],
            "sample_type": "sprints",
        })

    # Pitcher aggregates
    all_pitcher_ids = set(pitcher_exp_by_id) | set(pitcher_ev_by_id) | set(pitcher_bref_by_id)
    for pid in all_pitcher_ids:
        exp = pitcher_exp_by_id.get(pid)
        if exp:
            pa = exp["pa"]
            for stat in ("p_xera", "p_era_diff", "p_xwoba_against"):
                val = exp.get(stat)
                if val is None:
                    continue
                aggregates.append({
                    "player_id":   pid,
                    "metric_name": stat,
                    "avg_value":   round(val, 3),
                    "sample_size": pa,
                    "sample_type": "PA",
                })
        ev = pitcher_ev_by_id.get(pid)
        if ev:
            bbe = ev["bbe"]
            for stat in ("p_hard_hit_rate", "p_barrel_rate", "p_avg_ev"):
                val = ev.get(stat)
                if val is None:
                    continue
                aggregates.append({
                    "player_id":   pid,
                    "metric_name": stat,
                    "avg_value":   round(val, 3),
                    "sample_size": bbe,
                    "sample_type": "BBE",
                })
        bref = pitcher_bref_by_id.get(pid)
        if bref:
            bf = bref["bf"]
            for stat in ("p_k9", "p_bb9", "p_k_bb_diff"):
                val = bref.get(stat)
                if val is None:
                    continue
                aggregates.append({
                    "player_id":   pid,
                    "metric_name": stat,
                    "avg_value":   val,
                    "sample_size": bf,
                    "sample_type": "PA",
                })

    return {
        "source":     "real",
        "season":     year,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "players":    players,
        "aggregates": aggregates,
    }


def _write_stat_snapshot(aggregates: list[dict], snapshot_at: str) -> None:
    """Bulk-insert all metric records for this refresh into stat_snapshots. Non-fatal."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO stat_snapshots "
                "(snapshot_at, player_id, metric_name, avg_value, sample_size, sample_type) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (snapshot_at, r["player_id"], r["metric_name"],
                     r["avg_value"], r["sample_size"], r["sample_type"])
                    for r in aggregates
                ],
            )
        logger.info("stat_snapshots: wrote %d records at %s", len(aggregates), snapshot_at)
    except Exception as exc:
        logger.warning("stat_snapshots write failed (non-fatal): %s", exc)


async def _run_refresh(year: int) -> None:
    """Background task: fetch real data, write atomically, clear cache."""
    global _refresh_job
    _refresh_job = {
        "status":     "processing",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error":      None,
    }
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _blocking_fetch, year)

        # Atomic write: .tmp → os.replace() guarantees no half-written reads
        os.makedirs(os.path.dirname(REAL_DATA_PATH), exist_ok=True)
        tmp_path = REAL_DATA_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, REAL_DATA_PATH)

        _cache.clear()  # Force leaderboard recomputation with new data
        _refresh_job["status"] = "done"
        logger.info("Real data refresh complete: %d players, %d records",
                    len(data["players"]), len(data["aggregates"]))
        _write_stat_snapshot(data["aggregates"], data["fetched_at"])
    except Exception as exc:
        logger.exception("Real data refresh failed")
        _refresh_job = {
            "status":     "error",
            "started_at": _refresh_job.get("started_at"),
            "error":      str(exc),
        }


# ---------------------------------------------------------------------------
# Scheduler job functions
# ---------------------------------------------------------------------------
async def _scheduled_stats_refresh() -> None:
    """Scheduler job: refresh real data. Skips if a refresh is already running."""
    if _refresh_job.get("status") == "processing":
        logger.info("Scheduled stats refresh skipped: already processing")
        return
    year = datetime.now(timezone.utc).year
    logger.info("Scheduled stats refresh triggered for %d", year)
    await _run_refresh(year)


async def _scheduled_fantasy_sync() -> None:
    """Scheduler job: sync Yahoo Fantasy roster. Logs on failure, never raises."""
    logger.info("Scheduled Fantasy sync triggered")
    try:
        result = _do_fantasy_sync()
        if isinstance(result, str):
            logger.warning("Scheduled Fantasy sync failed: %s", result)
        else:
            logger.info("Scheduled Fantasy sync done: %d players", result["player_count"])
    except Exception as exc:
        logger.warning("Scheduled Fantasy sync exception: %s", exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/api/v1/leaderboard")
def get_leaderboard(
    metric_name: str = Query(..., description="指標名稱，例如 exit_velocity"),
    limit: int = Query(500, ge=1, le=2000, description="回傳筆數上限"),
    min_requirement: int = Query(5, ge=1, description="最低樣本數（類似最低打席數）"),
    response: Response = None,
):
    # Peek into cache before calling (to set X-Cache-Hit accurately)
    cache_key = ("_compute_leaderboard", (metric_name, min_requirement), ())
    hit_before = cache_key in _cache and (
        time.monotonic() - _cache[cache_key][1] < CACHE_TTL
    )

    full = _compute_leaderboard(metric_name, min_requirement)
    if not full:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No data for metric='{metric_name}' with min_requirement={min_requirement}. "
                "Click 'Refresh Stats' to fetch real Statcast data first."
            ),
        )

    response.headers["X-Cache-Hit"] = "true" if hit_before else "false"
    sliced = full[:limit]
    return {
        "metric_name":     metric_name,
        "limit":           limit,
        "min_requirement": min_requirement,
        "count":           len(sliced),
        "data":            sliced,
    }


@app.get("/api/v1/metrics")
def get_metrics():
    """回傳所有可用的指標名稱。"""
    return {"metrics": sorted(_METRIC_NAMES)}


@app.get("/api/v1/cache/stats")
def cache_stats():
    """回傳目前快取狀態，方便 debug。"""
    now = time.monotonic()
    return {
        "ttl_seconds":    CACHE_TTL,
        "cached_entries": len(_cache),
        "entries": [
            {
                "key":        str(k),
                "age_s":      round(now - v[1], 1),
                "expires_in": round(max(0.0, CACHE_TTL - (now - v[1])), 1),
            }
            for k, v in _cache.items()
        ],
    }


@app.delete("/api/v1/cache")
def clear_cache():
    """手動清空快取（適合開發期間使用）。"""
    _cache.clear()
    return {"message": "Cache cleared."}


# ---------------------------------------------------------------------------
# Data Refresh (pybaseball)
# ---------------------------------------------------------------------------
@app.post("/api/v1/data/refresh")
async def refresh_data(
    background_tasks: BackgroundTasks,
    year: int = Query(default=2026, ge=2015, le=2030, description="球季年份"),
):
    """
    Fetches real MLB Statcast data from pybaseball (Baseball Savant + FanGraphs)
    and stores as real_data.json. Returns immediately; fetch runs in background.

    Poll GET /api/v1/data/status to track progress (refresh_job.status).
    Returns HTTP 409 if a refresh is already in progress.
    """
    if _refresh_job.get("status") == "processing":
        raise HTTPException(status_code=409, detail="A refresh is already in progress.")
    background_tasks.add_task(_run_refresh, year)
    return {"status": "processing", "year": year}


@app.get("/api/v1/data/status")
def data_status():
    """回傳資料來源狀態及背景 refresh job 進度。"""
    def _next_run(job_id: str) -> str | None:
        if _scheduler is None:
            return None
        job = _scheduler.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return None

    return {
        **_data_source_meta,       # source, season, fetched_at
        "refresh_job": _refresh_job,
        "scheduler": {
            "stats_refresh": {
                "next_run": _next_run("stats_refresh"),
                "schedule": "Daily ET 10:00, Mar–Oct",
            },
            "fantasy_sync": {
                "next_run": _next_run("fantasy_sync"),
                "schedule": "Every 1 hour",
            },
        },
    }


# ---------------------------------------------------------------------------
# Fantasy Roster Sync
# ---------------------------------------------------------------------------
def _detect_fantasy_events(
    old_index: dict[str, str],
    new_index: dict[str, str],
    name_map:  dict[str, str],
    event_at:  str,
) -> list[tuple]:
    """
    Compare old vs new Fantasy index and return a list of change events.
    Skips comparison if old_index is empty (first-ever sync).
    event_type: 'pickup' (FA→team) | 'drop' (team→FA) | 'trade' (team→team)
    """
    if not old_index:
        return []
    events = []
    for key in set(old_index) | set(new_index):
        old_team = old_index.get(key)
        new_team = new_index.get(key)
        if old_team == new_team:
            continue
        name = name_map.get(key, key)
        if old_team is None:
            event_type = "pickup"
        elif new_team is None:
            event_type = "drop"
        else:
            event_type = "trade"
        events.append((event_at, name, key, event_type, old_team, new_team))
    return events


def _write_fantasy_events(events: list[tuple]) -> None:
    """Insert fantasy ownership change events into fantasy_events table. Non-fatal."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO fantasy_events "
                "(event_at, player_name, match_key, event_type, from_team, to_team) "
                "VALUES (?,?,?,?,?,?)",
                events,
            )
        logger.info("fantasy_events: wrote %d events", len(events))
    except Exception as exc:
        logger.warning("fantasy_events write failed (non-fatal): %s", exc)


def _do_fantasy_sync() -> dict | str:
    """
    Core Fantasy sync logic. Returns a result dict on success, or an error
    string on failure. Does NOT raise HTTPException — safe to call from
    both the route handler and the background scheduler.
    """
    global _fantasy_index, _fantasy_synced_at

    league_id = os.environ.get("YAHOO_LEAGUE_ID")
    oauth2_path = os.environ.get("YAHOO_OAUTH2_PATH")
    if not league_id or not oauth2_path:
        return "YAHOO_LEAGUE_ID and YAHOO_OAUTH2_PATH must be set in .env"

    # Dynamically import from yahoo-fantasy-agent (zero modifications to that project)
    yahoo_agent_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "yahoo-fantasy-agent")
    )
    if yahoo_agent_dir not in sys.path:
        sys.path.insert(0, yahoo_agent_dir)

    try:
        # oauth2.json path must be set before importing, as login() resolves relative to CWD
        os.chdir(os.path.dirname(oauth2_path))
        from player_list import get_all_rosters, login as yahoo_login  # type: ignore
        session = yahoo_login()
        roster_list = get_all_rosters(session, league_id)
    except Exception as exc:
        return f"Yahoo API error: {exc}"

    # Snapshot old index before overwriting (for event detection)
    old_index = dict(_fantasy_index)

    # Build index using the same normalize_name function
    new_index: dict[str, str] = {}
    players_out: list[dict] = []
    name_map: dict[str, str] = {}
    for p in roster_list:
        key = normalize_name(p["Player_Name"])
        new_index[key] = p["Team_Name"]
        name_map[key] = p["Player_Name"]
        players_out.append({
            "match_key":    key,
            "fantasy_team": p["Team_Name"],
            "player_name":  p["Player_Name"],
        })

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Persist to disk for restart recovery
    os.makedirs(os.path.dirname(FANTASY_PATH), exist_ok=True)
    with open(FANTASY_PATH, "w") as f:
        json.dump({"synced_at": synced_at, "players": players_out}, f, indent=2)

    # Update in-memory index and clear leaderboard cache
    _fantasy_index = new_index
    _fantasy_synced_at = synced_at
    _cache.clear()

    # Detect and persist ownership change events (non-fatal)
    events = _detect_fantasy_events(old_index, new_index, name_map, synced_at)
    if events:
        _write_fantasy_events(events)

    return {"synced_at": synced_at, "player_count": len(new_index), "events": len(events)}


@app.post("/api/v1/fantasy/sync")
def fantasy_sync():
    """
    Fetches the current Fantasy roster from Yahoo, updates _fantasy_index,
    persists to fantasy_roster.json, and clears the leaderboard cache so
    the next request reflects updated ownership data.

    Credentials are read from environment variables:
      YAHOO_LEAGUE_ID   — e.g. 469.l.118983
      YAHOO_OAUTH2_PATH — absolute path to oauth2.json
    """
    result = _do_fantasy_sync()
    if isinstance(result, str):
        status = 500 if "must be set" in result else 502
        raise HTTPException(status_code=status, detail=result)
    return result


@app.get("/api/v1/fantasy/status")
def fantasy_status():
    """Fantasy roster sync status."""
    return {
        "synced":       bool(_fantasy_index),
        "synced_at":    _fantasy_synced_at,
        "player_count": len(_fantasy_index),
    }
