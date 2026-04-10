"""
Unit tests for _compute_leaderboard:
  - Sort direction (descending for batters, ascending for pitcher metrics)
  - min_requirement filtering
  - Rank and percentile assignment
  - Fantasy ownership injection
  - TTL cache hit/miss behaviour
"""
import pytest
from unittest.mock import patch

import backend.cache as cache_mod
import backend.fantasy as fantasy_mod
import backend.fetcher as fetcher_mod
from tests.conftest import SAMPLE_PLAYERS, SAMPLE_AGGREGATES


@pytest.fixture(autouse=True)
def clear_cache():
    """Wipe the TTL cache before and after every test for isolation."""
    cache_mod._cache.clear()
    yield
    cache_mod._cache.clear()


@pytest.fixture(autouse=True)
def reset_fantasy_index():
    """Restore _fantasy_index to empty after every test."""
    original = dict(fantasy_mod._fantasy_index)
    yield
    fantasy_mod._fantasy_index.clear()
    fantasy_mod._fantasy_index.update(original)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSortOrder:
    def test_batter_metric_descending(self):
        """exit_velocity: highest value → rank 1."""
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert result[0]["player_name"] == "Aaron Judge"
        assert result[0]["avg_value"] == 95.5
        assert result[1]["avg_value"] == 93.0
        assert result[2]["avg_value"] == 91.0

    def test_pitcher_ascending_metric(self):
        """p_xera is in _ASCENDING_METRICS: lowest value → rank 1."""
        assert "p_xera" in fetcher_mod._ASCENDING_METRICS
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("p_xera", 1)
        assert result[0]["avg_value"] == 2.50
        assert result[1]["avg_value"] == 3.50


# ---------------------------------------------------------------------------
# min_requirement filtering
# ---------------------------------------------------------------------------

class TestMinRequirement:
    def test_filters_below_threshold(self):
        """Only players with sample_size >= min_requirement are included."""
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 90)
        assert len(result) == 1
        assert result[0]["player_name"] == "Aaron Judge"

    def test_all_pass_when_threshold_is_one(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert len(result) == 3

    def test_returns_empty_when_none_qualify(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 9999)
        assert result == []


# ---------------------------------------------------------------------------
# Rank and percentile
# ---------------------------------------------------------------------------

class TestRankAndPercentile:
    def test_rank_starts_at_one(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert result[0]["rank"] == 1
        assert result[1]["rank"] == 2
        assert result[2]["rank"] == 3

    def test_first_place_is_100th_percentile(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert result[0]["percentile"] == 100

    def test_last_place_is_0th_percentile(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert result[-1]["percentile"] == 0

    def test_single_player_is_100th_percentile(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 100)
        assert len(result) == 1
        assert result[0]["percentile"] == 100

    def test_ascending_metric_percentile(self):
        """For p_xera (lower=better), rank-1 player still gets percentile=100."""
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("p_xera", 1)
        assert result[0]["percentile"] == 100
        assert result[-1]["percentile"] == 0


# ---------------------------------------------------------------------------
# Fantasy ownership injection
# ---------------------------------------------------------------------------

class TestFantasyOwnership:
    def test_owned_player_flagged(self):
        fantasy_mod._fantasy_index["aaron judge"] = "Team A"
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        judge = next(p for p in result if p["player_name"] == "Aaron Judge")
        assert judge["is_owned"] is True
        assert judge["fantasy_team"] == "Team A"

    def test_unowned_player_not_flagged(self):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        ohtani = next(p for p in result if p["player_name"] == "Shohei Ohtani")
        assert ohtani["is_owned"] is False
        assert ohtani["fantasy_team"] is None

    def test_empty_fantasy_index_all_unowned(self):
        fantasy_mod._fantasy_index.clear()
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            result = fetcher_mod._compute_leaderboard("exit_velocity", 1)
        assert all(not p["is_owned"] for p in result)


# ---------------------------------------------------------------------------
# TTL cache behaviour
# ---------------------------------------------------------------------------

class TestTTLCache:
    def test_cache_miss_then_hit(self):
        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1
            return (SAMPLE_PLAYERS, SAMPLE_AGGREGATES)

        with patch.object(fetcher_mod, "load_data", side_effect=counting_load):
            fetcher_mod._compute_leaderboard("exit_velocity", 1)
            fetcher_mod._compute_leaderboard("exit_velocity", 1)

        assert call_count == 1

    def test_different_min_requirement_is_separate_cache_entry(self):
        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1
            return (SAMPLE_PLAYERS, SAMPLE_AGGREGATES)

        with patch.object(fetcher_mod, "load_data", side_effect=counting_load):
            fetcher_mod._compute_leaderboard("exit_velocity", 1)
            fetcher_mod._compute_leaderboard("exit_velocity", 10)

        assert call_count == 2

    def test_different_metric_is_separate_cache_entry(self):
        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1
            return (SAMPLE_PLAYERS, SAMPLE_AGGREGATES)

        with patch.object(fetcher_mod, "load_data", side_effect=counting_load):
            fetcher_mod._compute_leaderboard("exit_velocity", 1)
            fetcher_mod._compute_leaderboard("p_xera", 1)

        assert call_count == 2

    def test_cache_cleared_recomputes(self):
        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1
            return (SAMPLE_PLAYERS, SAMPLE_AGGREGATES)

        with patch.object(fetcher_mod, "load_data", side_effect=counting_load):
            fetcher_mod._compute_leaderboard("exit_velocity", 1)
            cache_mod._cache.clear()
            fetcher_mod._compute_leaderboard("exit_velocity", 1)

        assert call_count == 2
