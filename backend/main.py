"""
MLB Leaderboards API — FastAPI application and route handlers.

All business logic lives in the sub-modules:
  backend.cache     — TTL cache
  backend.db        — SQLite history
  backend.fetcher   — data loading, leaderboard computation, pybaseball fetch
  backend.fantasy   — Fantasy roster sync and name normalisation
  backend.scheduler — APScheduler jobs and refresh tracking
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

import backend.cache as cache_mod
import backend.db as db_mod
import backend.fantasy as fantasy_mod
import backend.fetcher as fetcher_mod
import backend.scheduler as scheduler_mod
from backend.config import settings

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    db_mod._init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        fantasy_mod._do_fantasy_sync,
        IntervalTrigger(hours=settings.fantasy_sync_interval_hours),
        id="fantasy_sync",
        name="Yahoo Fantasy Roster Sync",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduler_mod._scheduled_stats_refresh,
        CronTrigger(
            hour=settings.stats_refresh_hour,
            minute=settings.stats_refresh_minute,
            month=settings.stats_refresh_months,
            timezone=settings.stats_refresh_tz,
        ),
        id="stats_refresh",
        name="Baseball Savant Stats Refresh",
        replace_existing=True,
    )
    scheduler.start()
    scheduler_mod._scheduler = scheduler
    logger.info("Scheduler started. Jobs: %s", [j.id for j in scheduler.get_jobs()])

    yield

    scheduler.shutdown(wait=False)
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
# Routes — leaderboard
# ---------------------------------------------------------------------------
@app.get("/api/v1/leaderboard")
def get_leaderboard(
    metric_name: str = Query(..., description="指標名稱，例如 exit_velocity"),
    limit: int = Query(500, ge=1, le=2000, description="回傳筆數上限"),
    min_requirement: int = Query(5, ge=1, description="最低樣本數（類似最低打席數）"),
    year: int | None = Query(None, ge=2015, le=2030, description="歷史年份；不填預設當季實時數據"),
    response: Response = None,
):
    current_year = settings.default_refresh_year

    # ── Historical path: query SQLite stat_snapshots ───────────────────────
    if year is not None and year != current_year:
        full = db_mod._query_historical_snapshot(year, metric_name, min_requirement)
        if not full:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No historical data for year={year}, metric='{metric_name}'. "
                    "Try a different year or refresh current-season data first."
                ),
            )
        response.headers["X-Cache-Hit"] = "false"
        sliced = full[:limit]
        return {
            "metric_name":     metric_name,
            "limit":           limit,
            "min_requirement": min_requirement,
            "year":            year,
            "count":           len(sliced),
            "data":            sliced,
        }

    # ── Current path: TTL-cached real_data.json ────────────────────────────
    cache_key = ("_compute_leaderboard", (metric_name, min_requirement), ())
    hit_before = cache_key in cache_mod._cache and (
        time.monotonic() - cache_mod._cache[cache_key][1] < cache_mod.CACHE_TTL
    )

    full = fetcher_mod._compute_leaderboard(metric_name, min_requirement)
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
        "year":            current_year,
        "count":           len(sliced),
        "data":            sliced,
    }


# ---------------------------------------------------------------------------
# Routes — metrics
# ---------------------------------------------------------------------------
@app.get("/api/v1/metrics")
def get_metrics():
    """回傳所有可用的指標名稱。"""
    return {"metrics": sorted(fetcher_mod._METRIC_NAMES)}


@app.get("/api/v1/seasons")
def get_seasons():
    """回傳 SQLite stat_snapshots 中有數據的年份（降冪排列）。"""
    return {"seasons": db_mod._get_available_seasons()}


# ---------------------------------------------------------------------------
# Routes — cache
# ---------------------------------------------------------------------------
@app.get("/api/v1/cache/stats")
def cache_stats():
    """回傳目前快取狀態，方便 debug。"""
    now = time.monotonic()
    return {
        "ttl_seconds":    cache_mod.CACHE_TTL,
        "cached_entries": len(cache_mod._cache),
        "entries": [
            {
                "key":        str(k),
                "age_s":      round(now - v[1], 1),
                "expires_in": round(max(0.0, cache_mod.CACHE_TTL - (now - v[1])), 1),
            }
            for k, v in cache_mod._cache.items()
        ],
    }


@app.delete("/api/v1/cache")
def clear_cache():
    """手動清空快取（適合開發期間使用）。"""
    cache_mod._cache.clear()
    return {"message": "Cache cleared."}


# ---------------------------------------------------------------------------
# Routes — data refresh
# ---------------------------------------------------------------------------
@app.post("/api/v1/data/refresh")
async def refresh_data(
    background_tasks: BackgroundTasks,
    year: int = Query(default=None, ge=2015, le=2030, description="球季年份"),
):
    """
    Fetches real MLB Statcast data from pybaseball (Baseball Savant + BRef)
    and stores as real_data.json. Returns immediately; fetch runs in background.

    Poll GET /api/v1/data/status to track progress (refresh_job.status).
    Returns HTTP 409 if a refresh is already in progress.
    """
    if scheduler_mod._refresh_job.get("status") == "processing":
        raise HTTPException(status_code=409, detail="A refresh is already in progress.")
    target_year = year if year is not None else settings.default_refresh_year
    background_tasks.add_task(scheduler_mod._run_refresh, target_year)
    return {"status": "processing", "year": target_year}


@app.get("/api/v1/data/status")
def data_status():
    """回傳資料來源狀態及背景 refresh job 進度。"""
    def _next_run(job_id: str) -> str | None:
        sched = scheduler_mod._scheduler
        if sched is None:
            return None
        job = sched.get_job(job_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return None

    return {
        **fetcher_mod._data_source_meta,
        "refresh_job": scheduler_mod._refresh_job,
        "scheduler": {
            "stats_refresh": {
                "next_run": _next_run("stats_refresh"),
                "schedule": (
                    f"Daily {settings.stats_refresh_tz} "
                    f"{settings.stats_refresh_hour:02d}:{settings.stats_refresh_minute:02d}, "
                    f"months {settings.stats_refresh_months}"
                ),
            },
            "fantasy_sync": {
                "next_run": _next_run("fantasy_sync"),
                "schedule": f"Every {settings.fantasy_sync_interval_hours}h",
            },
        },
    }


# ---------------------------------------------------------------------------
# Routes — fantasy
# ---------------------------------------------------------------------------
@app.post("/api/v1/fantasy/sync")
def fantasy_sync():
    """
    Fetches the current Fantasy roster from Yahoo, updates _fantasy_index,
    persists to fantasy_roster.json, and clears the leaderboard cache.

    Credentials are read from environment variables:
      YAHOO_LEAGUE_ID   — e.g. 469.l.118983
      YAHOO_OAUTH2_PATH — absolute path to oauth2.json
    """
    result = fantasy_mod._do_fantasy_sync()
    if isinstance(result, str):
        status = 500 if "must be set" in result else 502
        raise HTTPException(status_code=status, detail=result)
    return result


@app.get("/api/v1/fantasy/status")
def fantasy_status():
    """Fantasy roster sync status."""
    return {
        "synced":       bool(fantasy_mod._fantasy_index),
        "synced_at":    fantasy_mod._fantasy_synced_at,
        "player_count": len(fantasy_mod._fantasy_index),
    }
