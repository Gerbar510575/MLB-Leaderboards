"""
Centralised application settings.

All values have sensible defaults and can be overridden via environment
variables or the .env file (pydantic-settings reads it automatically).

Variable names map 1-to-1 with env var names (upper-cased):
  CACHE_TTL=600
  FANTASY_SYNC_INTERVAL_HOURS=2
  STATS_REFRESH_HOUR=9
  STATS_REFRESH_MINUTE=30
  STATS_REFRESH_MONTHS=3-10
  STATS_REFRESH_TZ=America/New_York
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",          # silently ignore unknown env vars (e.g. Yahoo keys)
    )

    # Cache
    cache_ttl: int = 300         # seconds — leaderboard TTL cache

    # Scheduler: daily Statcast stats refresh
    stats_refresh_hour: int    = 10
    stats_refresh_minute: int  = 0
    stats_refresh_months: str  = "3-10"
    stats_refresh_tz: str      = "America/New_York"

    # Scheduler: Yahoo Fantasy roster sync
    fantasy_sync_interval_hours: int = 1

    # Data refresh API: default season year
    @property
    def default_refresh_year(self) -> int:
        return datetime.now(timezone.utc).year


settings = Settings()
