"""
API endpoint tests via FastAPI TestClient.
Covers all 8 routes: leaderboard, metrics, cache, refresh, status, fantasy.
"""
import os
import pytest
from unittest.mock import patch

import backend.cache as cache_mod
import backend.fetcher as fetcher_mod
import backend.scheduler as scheduler_mod
from tests.conftest import SAMPLE_PLAYERS, SAMPLE_AGGREGATES


@pytest.fixture(autouse=True)
def clear_cache_and_reset_job():
    """Isolate cache and refresh_job state between tests."""
    cache_mod._cache.clear()
    scheduler_mod._refresh_job = {"status": "idle", "started_at": None, "error": None}
    yield
    cache_mod._cache.clear()
    scheduler_mod._refresh_job = {"status": "idle", "started_at": None, "error": None}


# ---------------------------------------------------------------------------
# GET /api/v1/metrics
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 200

    def test_returns_all_18_metrics(self, client):
        data = client.get("/api/v1/metrics").json()
        assert len(data["metrics"]) == 18

    def test_metrics_sorted(self, client):
        metrics = client.get("/api/v1/metrics").json()["metrics"]
        assert metrics == sorted(metrics)

    def test_known_metrics_present(self, client):
        metrics = client.get("/api/v1/metrics").json()["metrics"]
        for expected in ("exit_velocity", "xba", "sprint_speed", "p_xera", "p_k9"):
            assert expected in metrics


# ---------------------------------------------------------------------------
# GET /api/v1/leaderboard
# ---------------------------------------------------------------------------

class TestLeaderboardEndpoint:
    def test_no_data_returns_404(self, client):
        """Without real_data.json and no mock data, expect 404."""
        resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity")
        assert resp.status_code == 404

    def test_with_data_returns_200(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            data = client.get(
                "/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1"
            ).json()
        assert "metric_name" in data
        assert "limit" in data
        assert "count" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_player_record_fields(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            data = client.get(
                "/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1"
            ).json()
        player = data["data"][0]
        for field in ("rank", "player_name", "team", "position", "avg_value",
                      "sample_size", "percentile", "is_owned"):
            assert field in player, f"Missing field: {field}"

    def test_limit_parameter(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            data = client.get(
                "/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1&limit=2"
            ).json()
        assert data["count"] == 2
        assert len(data["data"]) == 2

    def test_x_cache_hit_header_present(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
        assert "x-cache-hit" in resp.headers

    def test_first_request_is_cache_miss(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
        assert resp.headers["x-cache-hit"] == "false"

    def test_second_request_is_cache_hit(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
        assert resp.headers["x-cache-hit"] == "true"

    def test_different_limits_share_cache(self, client):
        """limit=1 and limit=50 hit the same cache entry (cache key excludes limit)."""
        call_count = 0

        def counting_load():
            nonlocal call_count
            call_count += 1
            return (SAMPLE_PLAYERS, SAMPLE_AGGREGATES)

        with patch.object(fetcher_mod, "load_data", side_effect=counting_load):
            client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1&limit=1")
            resp = client.get(
                "/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1&limit=50"
            )
        assert call_count == 1
        assert resp.headers["x-cache-hit"] == "true"

    def test_unknown_metric_returns_404(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            resp = client.get("/api/v1/leaderboard?metric_name=nonexistent_metric&min_requirement=1")
        assert resp.status_code == 404

    def test_historical_year_no_data_returns_404(self, client):
        """Querying a year with no SQLite data should return 404."""
        import backend.db as db_mod
        with patch.object(db_mod, "_query_historical_snapshot", return_value=[]):
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&year=2020")
        assert resp.status_code == 404

    def test_historical_year_with_data_returns_200(self, client):
        from tests.conftest import SAMPLE_AGGREGATES
        import backend.db as db_mod
        fake_history = [
            {**agg, "player_name": "Aaron Judge", "team": "NYY", "position": "OF",
             "rank": i + 1, "percentile": 100 - i * 20, "fantasy_team": None, "is_owned": False}
            for i, agg in enumerate(a for a in SAMPLE_AGGREGATES if a["metric_name"] == "exit_velocity")
        ]
        with patch.object(db_mod, "_query_historical_snapshot", return_value=fake_history):
            resp = client.get("/api/v1/leaderboard?metric_name=exit_velocity&year=2023&min_requirement=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["year"] == 2023
        assert len(data["data"]) == len(fake_history)

    def test_response_includes_year_field(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            data = client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1").json()
        assert "year" in data


# ---------------------------------------------------------------------------
# GET /api/v1/seasons
# ---------------------------------------------------------------------------

class TestSeasonsEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/seasons")
        assert resp.status_code == 200

    def test_shape(self, client):
        data = client.get("/api/v1/seasons").json()
        assert "seasons" in data
        assert isinstance(data["seasons"], list)

    def test_reflects_snapshot_data(self, client):
        import backend.db as db_mod
        with patch.object(db_mod, "_get_available_seasons", return_value=[2024, 2023]):
            data = client.get("/api/v1/seasons").json()
        assert data["seasons"] == [2024, 2023]


# ---------------------------------------------------------------------------
# GET /api/v1/cache/stats  +  DELETE /api/v1/cache
# ---------------------------------------------------------------------------

class TestCacheEndpoints:
    def test_cache_stats_returns_200(self, client):
        resp = client.get("/api/v1/cache/stats")
        assert resp.status_code == 200

    def test_cache_stats_shape(self, client):
        data = client.get("/api/v1/cache/stats").json()
        assert "ttl_seconds" in data
        assert "cached_entries" in data
        assert "entries" in data

    def test_clear_cache_returns_200(self, client):
        resp = client.delete("/api/v1/cache")
        assert resp.status_code == 200

    def test_clear_cache_empties_entries(self, client):
        with patch.object(fetcher_mod, "load_data", return_value=(SAMPLE_PLAYERS, SAMPLE_AGGREGATES)):
            client.get("/api/v1/leaderboard?metric_name=exit_velocity&min_requirement=1")
        client.delete("/api/v1/cache")
        data = client.get("/api/v1/cache/stats").json()
        assert data["cached_entries"] == 0


# ---------------------------------------------------------------------------
# POST /api/v1/data/refresh  +  GET /api/v1/data/status
# ---------------------------------------------------------------------------

class TestDataRefreshEndpoints:
    def test_refresh_returns_processing(self, client):
        resp = client.post("/api/v1/data/refresh?year=2026")
        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"

    def test_refresh_returns_year(self, client):
        resp = client.post("/api/v1/data/refresh?year=2025")
        assert resp.json()["year"] == 2025

    def test_refresh_409_when_already_running(self, client):
        scheduler_mod._refresh_job["status"] = "processing"
        resp = client.post("/api/v1/data/refresh?year=2026")
        assert resp.status_code == 409

    def test_data_status_returns_200(self, client):
        resp = client.get("/api/v1/data/status")
        assert resp.status_code == 200

    def test_data_status_shape(self, client):
        data = client.get("/api/v1/data/status").json()
        assert "refresh_job" in data
        assert "scheduler" in data
        assert "stats_refresh" in data["scheduler"]
        assert "fantasy_sync" in data["scheduler"]

    def test_data_status_refresh_job_fields(self, client):
        data = client.get("/api/v1/data/status").json()
        job = data["refresh_job"]
        assert "status" in job
        assert "started_at" in job
        assert "error" in job


# ---------------------------------------------------------------------------
# POST /api/v1/fantasy/sync  +  GET /api/v1/fantasy/status
# ---------------------------------------------------------------------------

class TestFantasyEndpoints:
    def test_fantasy_status_returns_200(self, client):
        resp = client.get("/api/v1/fantasy/status")
        assert resp.status_code == 200

    def test_fantasy_status_shape(self, client):
        data = client.get("/api/v1/fantasy/status").json()
        assert "synced" in data
        assert "synced_at" in data
        assert "player_count" in data

    def test_fantasy_sync_500_when_env_missing(self, client):
        """Without YAHOO_LEAGUE_ID / YAHOO_OAUTH2_PATH → 500."""
        with patch.dict(os.environ, {}, clear=True):
            resp = client.post("/api/v1/fantasy/sync")
        assert resp.status_code == 500

    def test_fantasy_sync_error_message(self, client):
        with patch.dict(os.environ, {}, clear=True):
            detail = client.post("/api/v1/fantasy/sync").json()["detail"]
        assert "YAHOO_LEAGUE_ID" in detail or "must be set" in detail
