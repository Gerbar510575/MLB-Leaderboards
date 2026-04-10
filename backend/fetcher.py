"""
Data fetching, leaderboard computation, and related metadata.

Responsibilities
----------------
- load_data()            : reads real_data.json → (players_dict, aggregates)
- _blocking_fetch(year)  : pybaseball I/O for all 6 sources (runs in executor)
- _compute_leaderboard() : TTL-cached sorting, ranking, percentile + fantasy join
- Column-name maps       : _SAVANT_METRIC_MAP, _XBA_COLS, _PITCHER_EXP_COLS, _PITCHER_EV_COLS
- Module-level helpers   : _reverse_name(), _safe_float()
"""
from __future__ import annotations

import logging
import math
import os

from backend.cache import ttl_cache, CACHE_TTL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths & runtime state
# ---------------------------------------------------------------------------
REAL_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "real_data.json")

_data_source_meta: dict = {"source": "mock", "season": None, "fetched_at": None}

# ---------------------------------------------------------------------------
# Available metrics
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Column maps: metric_name → Baseball Savant CSV column
# ---------------------------------------------------------------------------
_SAVANT_METRIC_MAP: dict[str, str] = {
    "exit_velocity": "avg_hit_speed",
    "launch_angle":  "avg_hit_angle",
    "hard_hit_rate": "ev95percent",
    "barrel_rate":   "brl_percent",
}

_XBA_COLS: dict[str, str] = {
    "xba":        "est_ba",
    "xslg":       "est_slg",
    "xwoba":      "est_woba",
    "xwoba_diff": "est_woba_minus_woba_diff",
}

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

# ---------------------------------------------------------------------------
# Module-level helpers (extracted from _blocking_fetch for testability)
# ---------------------------------------------------------------------------

def _reverse_name(raw: str) -> str:
    """
    Convert Baseball Savant 'Last, First' format → 'First Last'.
    e.g. "Judge, Aaron" → "Aaron Judge"
    """
    s = str(raw)
    parts = s.split(", ", 1)
    return (parts[1].strip() + " " + parts[0].strip()) if len(parts) == 2 else s.strip()


def _safe_float(v) -> float | None:
    """Convert to float, returning None for NaN / None / non-numeric values."""
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data() -> tuple[dict[str, dict], list[dict]]:
    """
    Returns (players_dict, aggregates) from real_data.json.
    Returns ({}, []) if real_data.json does not exist yet.
    Updates _data_source_meta as a side effect.
    """
    import json
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
    Computes a full leaderboard for the given metric.

    `limit` is intentionally excluded from the cache key — all limit variants
    share this cached result; slicing happens at the API layer.

    Fantasy ownership is injected from backend.fantasy._fantasy_index.
    """
    import backend.fantasy as _fantasy_mod  # late import to avoid circular at module load

    players_dict, aggregates = load_data()

    aggregated: list[dict] = []
    for rec in aggregates:
        if rec["metric_name"] != metric_name:
            continue
        if rec["sample_size"] < min_requirement:
            continue
        player = players_dict.get(rec["player_id"], {})
        match_key = _fantasy_mod.normalize_name(player.get("player_name", ""))
        fantasy_team = _fantasy_mod._fantasy_index.get(match_key)
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

    ranked = sorted(aggregated, key=lambda x: x["avg_value"],
                    reverse=(metric_name not in _ASCENDING_METRICS))

    n = len(ranked)
    for i, entry in enumerate(ranked):
        entry["rank"] = i + 1
        entry["percentile"] = round((n - 1 - i) / max(n - 1, 1) * 100) if n > 1 else 100

    return ranked


# ---------------------------------------------------------------------------
# Real data fetch — runs in executor, returns raw dict
# ---------------------------------------------------------------------------

def _blocking_fetch(year: int, adapter=None) -> dict:
    """
    Runs blocking I/O via the supplied DataSourceAdapter.
    Defaults to PybaseballAdapter() when adapter is None.
    Called via run_in_executor; does NOT write any files.
    Raises ValueError with a human-readable message on empty/bad data.
    """
    from datetime import datetime, timezone
    from backend.adapters import PybaseballAdapter

    if adapter is None:
        adapter = PybaseballAdapter()

    # ── Source 1: Baseball Savant exit velocity / barrel leaderboard ──────
    ev_df = adapter.fetch_batter_ev(year)
    if ev_df is None or ev_df.empty:
        raise ValueError(
            f"No Statcast batted-ball data for {year} — "
            "the season may not have started yet or Baseball Savant is unavailable."
        )

    ev_df = ev_df.copy()
    ev_df["full_name"] = ev_df["last_name, first_name"].apply(_reverse_name)
    ev_df["player_id_str"] = ev_df["player_id"].astype(str)
    logger.info("ev_df columns: %s", ev_df.columns.tolist())

    # ── Source 2: Baseball Savant expected stats for xBA ─────────────────
    xba_df = adapter.fetch_batter_expected(year)
    if xba_df is None or xba_df.empty:
        raise ValueError(
            f"No Baseball Savant expected stats for {year} — "
            "the season may not have started yet or Baseball Savant is unavailable."
        )

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
    team_pos_by_id: dict[str, dict] = {}
    speed_by_id: dict[str, dict] = {}
    try:
        speed_df = adapter.fetch_sprint_speed(year)
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
                        "speed": float(val),
                        "runs":  int(row.get("competitive_runs", 0) or 0),
                    }
    except Exception as exc:
        logger.warning("Sprint speed fetch failed (non-fatal): %s", exc)

    # ── Source 4: Pitcher Expected Stats (xERA / ERA-xERA / xwOBA against) ─
    pitcher_exp_by_id: dict[str, dict] = {}
    pitcher_names: dict[str, str] = {}
    try:
        p_exp_df = adapter.fetch_pitcher_expected(year)
        if p_exp_df is not None and not p_exp_df.empty:
            p_exp_df = p_exp_df.copy()
            p_exp_df["full_name"] = p_exp_df["last_name, first_name"].apply(_reverse_name)
            p_exp_df["player_id_str"] = p_exp_df["player_id"].astype(str)
            for _, row in p_exp_df.iterrows():
                pid = row["player_id_str"]
                pitcher_names[pid] = row["full_name"]
                entry = {"pa": int(row.get("pa", 0) or 0)}
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
        p_ev_df = adapter.fetch_pitcher_ev(year)
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
    pitcher_bref_by_id: dict[str, dict] = {}
    try:
        bref_df = adapter.fetch_pitcher_bref(year)
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
