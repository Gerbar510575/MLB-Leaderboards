"""
Background job management.

Jobs
----
_run_refresh(year)           : fetch real data, write atomically, clear cache
_scheduled_stats_refresh()   : cron job — daily ET 10:00, baseball season only (Mar–Oct)
_scheduled_fantasy_sync()    : interval job — every 1 hour

_scheduler and _refresh_job are module-level so routes can inspect them.
_scheduler is set by the lifespan in main.py after the scheduler is created.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.cache import _cache
from backend.db import _upsert_players, _write_stat_snapshot
import backend.fetcher as fetcher_mod
import backend.fantasy as fantasy_mod

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

_refresh_job: dict = {"status": "idle", "started_at": None, "error": None}


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
        data = await loop.run_in_executor(None, fetcher_mod._blocking_fetch, year)

        # Atomic write: .tmp → os.replace() guarantees no half-written reads
        os.makedirs(os.path.dirname(fetcher_mod.REAL_DATA_PATH), exist_ok=True)
        tmp_path = fetcher_mod.REAL_DATA_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, fetcher_mod.REAL_DATA_PATH)

        _cache.clear()
        _refresh_job["status"] = "done"
        logger.info("Real data refresh complete: %d players, %d records",
                    len(data["players"]), len(data["aggregates"]))
        _write_stat_snapshot(data["aggregates"], data["fetched_at"])
        _upsert_players(data["players"], data["fetched_at"])
    except Exception as exc:
        logger.exception("Real data refresh failed")
        _refresh_job = {
            "status":     "error",
            "started_at": _refresh_job.get("started_at"),
            "error":      str(exc),
        }


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
        result = fantasy_mod._do_fantasy_sync()
        if isinstance(result, str):
            logger.warning("Scheduled Fantasy sync failed: %s", result)
        else:
            logger.info("Scheduled Fantasy sync done: %d players", result["player_count"])
    except Exception as exc:
        logger.warning("Scheduled Fantasy sync exception: %s", exc)
