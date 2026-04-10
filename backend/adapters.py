"""
Data source abstraction layer.

DataSourceAdapter  — Protocol defining the 6 fetch operations
PybaseballAdapter  — Concrete implementation wrapping pybaseball

Benefits
--------
- _blocking_fetch() depends on the Protocol, not on pybaseball directly
- Tests inject a FakeAdapter: no network, no pybaseball install required
- Future sources (e.g. a local CSV cache, a different stats API) swap in
  by implementing the same Protocol
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataSourceAdapter(Protocol):
    """All six data sources that _blocking_fetch consumes."""

    def fetch_batter_ev(self, year: int) -> pd.DataFrame:
        """
        Baseball Savant exit-velocity / barrel leaderboard.
        Columns used: last_name, first_name | player_id | attempts |
                      avg_hit_speed | avg_hit_angle | ev95percent | brl_percent
        """
        ...

    def fetch_batter_expected(self, year: int) -> pd.DataFrame:
        """
        Baseball Savant expected stats (xBA / xSLG / xwOBA).
        Columns used: player_id | pa | est_ba | est_slg | est_woba |
                      est_woba_minus_woba_diff
        """
        ...

    def fetch_sprint_speed(self, year: int) -> pd.DataFrame:
        """
        Baseball Savant sprint speed + team / position metadata.
        Columns used: player_id | sprint_speed | competitive_runs |
                      team | position
        """
        ...

    def fetch_pitcher_expected(self, year: int) -> pd.DataFrame:
        """
        Baseball Savant pitcher expected stats.
        Columns used: last_name, first_name | player_id | pa |
                      xera | era_minus_xera_diff | est_woba
        """
        ...

    def fetch_pitcher_ev(self, year: int) -> pd.DataFrame:
        """
        Baseball Savant pitcher exit-velocity / barrel leaderboard.
        Columns used: last_name, first_name | player_id | attempts |
                      ev95percent | brl_percent | avg_hit_speed
        """
        ...

    def fetch_pitcher_bref(self, year: int) -> pd.DataFrame:
        """
        Baseball Reference pitching stats (K/9, BB/9, K-BB%).
        Columns used: mlbID | BF | SO9 | BB | IP | SO
        """
        ...


class PybaseballAdapter:
    """
    Production adapter — thin wrappers around pybaseball functions.
    pybaseball is imported lazily so startup is not blocked if it is slow.
    """

    def fetch_batter_ev(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.statcast_batter_exitvelo_barrels(year, minBBE=1)

    def fetch_batter_expected(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.statcast_batter_expected_stats(year, minPA=1)

    def fetch_sprint_speed(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.statcast_sprint_speed(year, min_opp=1)

    def fetch_pitcher_expected(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.statcast_pitcher_expected_stats(year, minPA=1)

    def fetch_pitcher_ev(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.statcast_pitcher_exitvelo_barrels(year, minBBE=1)

    def fetch_pitcher_bref(self, year: int) -> pd.DataFrame:
        import pybaseball
        return pybaseball.pitching_stats_bref(year)
