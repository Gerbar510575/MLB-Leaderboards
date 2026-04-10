"""
Shared pytest fixtures for MLB Leaderboards tests.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Sample data reused across test modules
# ---------------------------------------------------------------------------
SAMPLE_PLAYERS: dict[str, dict] = {
    "111": {"player_id": "111", "player_name": "Aaron Judge",      "team": "NYY", "position": "OF"},
    "222": {"player_id": "222", "player_name": "Shohei Ohtani",    "team": "LAD", "position": "DH"},
    "333": {"player_id": "333", "player_name": "Yordan Alvarez",   "team": "HOU", "position": "DH"},
    "444": {"player_id": "444", "player_name": "Gerrit Cole",      "team": "NYY", "position": "SP"},
    "555": {"player_id": "555", "player_name": "Shane McClanahan", "team": "TB",  "position": "SP"},
}

SAMPLE_AGGREGATES: list[dict] = [
    # Batter — exit_velocity (descending, higher = better)
    {"player_id": "111", "metric_name": "exit_velocity", "avg_value": 95.5, "sample_size": 100, "sample_type": "BBE"},
    {"player_id": "222", "metric_name": "exit_velocity", "avg_value": 93.0, "sample_size": 80,  "sample_type": "BBE"},
    {"player_id": "333", "metric_name": "exit_velocity", "avg_value": 91.0, "sample_size": 60,  "sample_type": "BBE"},
    # Pitcher — p_xera (ascending, lower = better)
    {"player_id": "444", "metric_name": "p_xera", "avg_value": 2.50, "sample_size": 200, "sample_type": "PA"},
    {"player_id": "555", "metric_name": "p_xera", "avg_value": 3.50, "sample_size": 150, "sample_type": "PA"},
]


@pytest.fixture(scope="session")
def tmp_db(tmp_path_factory):
    """Temporary SQLite DB path shared for the whole test session."""
    return str(tmp_path_factory.mktemp("db") / "test_history.db")


@pytest.fixture(scope="module")
def client(tmp_db):
    """
    FastAPI TestClient with:
      - DB_PATH pointed at a temp file (prevents touching real mlb_history.db)
      - REAL_DATA_PATH set to a nonexistent path so tests start with no data
    """
    import backend.db as db_mod
    import backend.fetcher as fetcher_mod
    from backend.main import app
    from fastapi.testclient import TestClient

    with patch.object(db_mod, "DB_PATH", tmp_db):
        with patch.object(fetcher_mod, "REAL_DATA_PATH", "/nonexistent/real_data.json"):
            with TestClient(app) as c:
                yield c
