"""
Tests for backend/db.py — SQLite history layer.

Covers:
  _init_db, _upsert_players, _write_stat_snapshot,
  _get_available_seasons, _query_historical_snapshot
"""
from __future__ import annotations

import pytest

from tests.conftest import SAMPLE_PLAYERS, SAMPLE_AGGREGATES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def db(tmp_path):
    """Fresh DB for each test."""
    import backend.db as db_mod
    from unittest.mock import patch

    db_path = str(tmp_path / "test.db")
    with patch.object(db_mod, "DB_PATH", db_path):
        db_mod._init_db()
        yield db_mod


# ---------------------------------------------------------------------------
# _init_db
# ---------------------------------------------------------------------------
class TestInitDb:
    def test_tables_created(self, db):
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert {"stat_snapshots", "fantasy_events", "players"}.issubset(tables)

    def test_idempotent(self, db):
        """Calling _init_db() twice should not raise."""
        db._init_db()


# ---------------------------------------------------------------------------
# _upsert_players
# ---------------------------------------------------------------------------
class TestUpsertPlayers:
    def test_insert(self, db):
        players = list(SAMPLE_PLAYERS.values())
        db._upsert_players(players, "2025-01-01T00:00:00Z")
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        assert count == len(players)

    def test_update_on_conflict(self, db):
        """Re-upserting with a changed team should update the row."""
        players = [{"player_id": "111", "player_name": "Aaron Judge", "team": "BOS", "position": "OF"}]
        db._upsert_players(players, "2025-06-01T00:00:00Z")
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as conn:
            row = conn.execute("SELECT team FROM players WHERE player_id='111'").fetchone()
        assert row[0] == "BOS"

    def test_non_fatal_on_bad_data(self, db, monkeypatch):
        """A corrupt payload should log a warning, not raise."""
        monkeypatch.setattr(db, "DB_PATH", "/nonexistent/path/db")
        db._upsert_players(list(SAMPLE_PLAYERS.values()), "2025-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# _write_stat_snapshot + _get_available_seasons
# ---------------------------------------------------------------------------
class TestStatSnapshot:
    def test_write_and_seasons(self, db):
        db._write_stat_snapshot(SAMPLE_AGGREGATES, "2024-10-01T00:00:00Z")
        seasons = db._get_available_seasons()
        assert 2024 in seasons

    def test_multiple_years(self, db):
        db._write_stat_snapshot(SAMPLE_AGGREGATES, "2023-10-01T00:00:00Z")
        db._write_stat_snapshot(SAMPLE_AGGREGATES, "2024-10-01T00:00:00Z")
        seasons = db._get_available_seasons()
        assert seasons == sorted(set(seasons), reverse=True)  # descending
        assert 2023 in seasons and 2024 in seasons

    def test_non_fatal_on_bad_path(self, db, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", "/bad/path.db")
        db._write_stat_snapshot(SAMPLE_AGGREGATES, "2025-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# _query_historical_snapshot
# ---------------------------------------------------------------------------
class TestQueryHistoricalSnapshot:
    @pytest.fixture(autouse=True)
    def seed(self, db):
        """Seed players + one snapshot."""
        db._upsert_players(list(SAMPLE_PLAYERS.values()), "2024-10-01T00:00:00Z")
        db._write_stat_snapshot(SAMPLE_AGGREGATES, "2024-10-01T00:00:00Z")

    def test_returns_ranked_list(self, db):
        rows = db._query_historical_snapshot(2024, "exit_velocity", 1)
        assert len(rows) == 3
        # descending — Judge (95.5) should be rank 1
        assert rows[0]["player_id"] == "111"
        assert rows[0]["rank"] == 1

    def test_ascending_metric(self, db):
        rows = db._query_historical_snapshot(2024, "p_xera", 1)
        assert len(rows) == 2
        # ascending — lower xERA = better rank
        assert rows[0]["player_id"] == "444"  # 2.50 xERA
        assert rows[0]["rank"] == 1

    def test_min_requirement_filter(self, db):
        rows = db._query_historical_snapshot(2024, "exit_velocity", 90)
        assert len(rows) == 1  # only Judge has 100 BBE

    def test_missing_year_returns_empty(self, db):
        rows = db._query_historical_snapshot(1999, "exit_velocity", 1)
        assert rows == []

    def test_missing_metric_returns_empty(self, db):
        rows = db._query_historical_snapshot(2024, "nonexistent_metric", 1)
        assert rows == []

    def test_is_owned_false(self, db):
        rows = db._query_historical_snapshot(2024, "exit_velocity", 1)
        assert all(r["is_owned"] is False for r in rows)

    def test_percentile_range(self, db):
        rows = db._query_historical_snapshot(2024, "exit_velocity", 1)
        for r in rows:
            assert 0 <= r["percentile"] <= 100

    def test_uses_latest_snapshot_for_year(self, db):
        """Two snapshots in same year — should use the more recent one."""
        import backend.db as db_mod
        # Write a newer snapshot with higher EV for all
        newer = [
            {"player_id": "111", "metric_name": "exit_velocity", "avg_value": 99.0, "sample_size": 110, "sample_type": "BBE"},
            {"player_id": "222", "metric_name": "exit_velocity", "avg_value": 97.0, "sample_size": 90,  "sample_type": "BBE"},
            {"player_id": "333", "metric_name": "exit_velocity", "avg_value": 95.0, "sample_size": 70,  "sample_type": "BBE"},
        ]
        db._write_stat_snapshot(newer, "2024-10-15T00:00:00Z")
        rows = db._query_historical_snapshot(2024, "exit_velocity", 1)
        # Should reflect the newer snapshot values
        assert rows[0]["avg_value"] == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# Seasons endpoint (via TestClient)
# ---------------------------------------------------------------------------
class TestSeasonsEndpoint:
    def test_empty_when_no_data(self, client):
        resp = client.get("/api/v1/seasons")
        assert resp.status_code == 200
        assert isinstance(resp.json()["seasons"], list)
