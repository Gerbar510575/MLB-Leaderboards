"""
SQLite history database — append-only.

Tables
------
stat_snapshots  : every metric record from every data refresh
fantasy_events  : ownership change events (pickup / drop / trade)

All writes are non-fatal: wrapped in try/except, log warning on failure,
never interrupt the main refresh or sync flow.
"""
from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "mlb_history.db")


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

            CREATE TABLE IF NOT EXISTS players (
                player_id   TEXT PRIMARY KEY,
                player_name TEXT NOT NULL,
                team        TEXT DEFAULT '',
                position    TEXT DEFAULT '',
                updated_at  TEXT NOT NULL
            );
        """)
        conn.commit()
    logger.info("DB initialised: %s", DB_PATH)


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


def _upsert_players(players: list[dict], updated_at: str) -> None:
    """Upsert player metadata (name / team / position) keyed by player_id. Non-fatal."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                "INSERT INTO players (player_id, player_name, team, position, updated_at) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(player_id) DO UPDATE SET "
                "  player_name=excluded.player_name, "
                "  team=excluded.team, "
                "  position=excluded.position, "
                "  updated_at=excluded.updated_at",
                [
                    (p["player_id"], p["player_name"],
                     p.get("team", ""), p.get("position", ""), updated_at)
                    for p in players
                ],
            )
        logger.info("players: upserted %d rows at %s", len(players), updated_at)
    except Exception as exc:
        logger.warning("players upsert failed (non-fatal): %s", exc)


def _get_available_seasons() -> list[int]:
    """Return distinct years that have data in stat_snapshots, sorted descending."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT DISTINCT CAST(strftime('%Y', snapshot_at) AS INTEGER) "
                "FROM stat_snapshots ORDER BY 1 DESC"
            ).fetchall()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.warning("get_available_seasons failed: %s", exc)
        return []


# Pitcher metrics where lower = better — kept in sync with fetcher._ASCENDING_METRICS
_ASCENDING_METRICS: frozenset[str] = frozenset({
    "p_xera", "p_xwoba_against", "p_hard_hit_rate", "p_barrel_rate", "p_avg_ev", "p_bb9",
})


def _query_historical_snapshot(
    year: int, metric_name: str, min_requirement: int
) -> list[dict]:
    """
    Return a ranked leaderboard for a historical year from stat_snapshots.

    Strategy: use the most-recent snapshot whose year matches `year`.
    Fantasy ownership is unavailable for historical data (is_owned=False).
    Returns [] when no data exists for the requested year / metric.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT MAX(snapshot_at) FROM stat_snapshots "
                "WHERE CAST(strftime('%Y', snapshot_at) AS INTEGER) = ?",
                (year,),
            ).fetchone()
            if not row or not row[0]:
                return []
            latest = row[0]

            rows = conn.execute(
                "SELECT s.player_id, p.player_name, p.team, p.position, "
                "       s.avg_value, s.sample_size, s.sample_type "
                "FROM stat_snapshots s "
                "LEFT JOIN players p ON s.player_id = p.player_id "
                "WHERE s.snapshot_at = ? AND s.metric_name = ? AND s.sample_size >= ?",
                (latest, metric_name, min_requirement),
            ).fetchall()
    except Exception as exc:
        logger.warning("historical snapshot query failed: %s", exc)
        return []

    entries = [
        {
            "player_id":    r[0],
            "player_name":  r[1] or r[0],
            "team":         r[2] or "",
            "position":     r[3] or "",
            "avg_value":    r[4],
            "sample_size":  r[5],
            "sample_type":  r[6] or "",
            "fantasy_team": None,
            "is_owned":     False,
        }
        for r in rows
    ]

    ranked = sorted(
        entries,
        key=lambda x: x["avg_value"],
        reverse=(metric_name not in _ASCENDING_METRICS),
    )
    n = len(ranked)
    for i, entry in enumerate(ranked):
        entry["rank"] = i + 1
        entry["percentile"] = round((n - 1 - i) / max(n - 1, 1) * 100) if n > 1 else 100
    return ranked


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
