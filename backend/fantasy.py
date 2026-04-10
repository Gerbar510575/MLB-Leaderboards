"""
Yahoo Fantasy roster integration.

Responsibilities
----------------
- normalize_name()        : canonical name normalisation for Fantasy JOIN
                            (verbatim copy of yahoo-fantasy-agent/player_list.py::normalize_name —
                             must stay in sync)
- _fantasy_index          : in-memory match_key → fantasy_team dict (NOT in TTL cache)
- _load_fantasy_index()   : load persisted roster from fantasy_roster.json on startup
- _detect_fantasy_events(): pure diff of old vs new index → list of change event tuples
- _do_fantasy_sync()      : full sync: fetch Yahoo → update index → persist → detect events
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

from backend.cache import _cache
from backend.db import _write_fantasy_events

logger = logging.getLogger(__name__)

FANTASY_PATH = os.path.join(os.path.dirname(__file__), "data", "fantasy_roster.json")

_fantasy_index: dict[str, str] = {}   # match_key → fantasy_team_name
_fantasy_synced_at: str | None = None


# ---------------------------------------------------------------------------
# Name normalisation — identical copy of player_list.py::normalize_name
# Must stay in sync with yahoo-fantasy-agent/player_list.py
# ---------------------------------------------------------------------------
def normalize_name(name: str) -> str:
    """
    Canonical name normalisation used for Fantasy roster JOIN.
    Examples: "Ronald Acuña Jr." → "ronald acuna", "Tyler O'Neill" → "tyler oneill"
    """
    if not name:
        return ""
    name = name.lower().strip()
    name = "".join(c for c in unicodedata.normalize('NFD', name)
                   if unicodedata.category(c) != 'Mn')
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    name = re.sub(r"['.]", "", name)
    return " ".join(name.split())


# ---------------------------------------------------------------------------
# Startup: load persisted roster
# ---------------------------------------------------------------------------
def _load_fantasy_index() -> None:
    """Load persisted fantasy roster into memory on startup."""
    global _fantasy_index, _fantasy_synced_at
    if not os.path.exists(FANTASY_PATH):
        return
    with open(FANTASY_PATH) as f:
        data = json.load(f)
    _fantasy_index = {p["match_key"]: p["fantasy_team"] for p in data.get("players", [])}
    _fantasy_synced_at = data.get("synced_at")


_load_fantasy_index()


# ---------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------
def _detect_fantasy_events(
    old_index: dict[str, str],
    new_index: dict[str, str],
    name_map:  dict[str, str],
    event_at:  str,
) -> list[tuple]:
    """
    Compare old vs new Fantasy index and return a list of change events.
    Skips comparison if old_index is empty (first-ever sync).
    event_type: 'pickup' (FA→team) | 'drop' (team→FA) | 'trade' (team→team)
    """
    if not old_index:
        return []
    events = []
    for key in set(old_index) | set(new_index):
        old_team = old_index.get(key)
        new_team = new_index.get(key)
        if old_team == new_team:
            continue
        name = name_map.get(key, key)
        if old_team is None:
            event_type = "pickup"
        elif new_team is None:
            event_type = "drop"
        else:
            event_type = "trade"
        events.append((event_at, name, key, event_type, old_team, new_team))
    return events


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------
def _do_fantasy_sync() -> dict | str:
    """
    Core Fantasy sync logic. Returns a result dict on success, or an error
    string on failure. Does NOT raise HTTPException — safe to call from
    both the route handler and the background scheduler.
    """
    global _fantasy_index, _fantasy_synced_at

    league_id   = os.environ.get("YAHOO_LEAGUE_ID")
    oauth2_path = os.environ.get("YAHOO_OAUTH2_PATH")
    if not league_id or not oauth2_path:
        return "YAHOO_LEAGUE_ID and YAHOO_OAUTH2_PATH must be set in .env"

    # Dynamically import from yahoo-fantasy-agent (zero modifications to that project)
    yahoo_agent_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "yahoo-fantasy-agent")
    )
    if yahoo_agent_dir not in sys.path:
        sys.path.insert(0, yahoo_agent_dir)

    try:
        os.chdir(os.path.dirname(oauth2_path))
        from player_list import get_all_rosters, login as yahoo_login  # type: ignore
        session     = yahoo_login()
        roster_list = get_all_rosters(session, league_id)
    except Exception as exc:
        return f"Yahoo API error: {exc}"

    old_index = dict(_fantasy_index)

    new_index: dict[str, str] = {}
    players_out: list[dict] = []
    name_map: dict[str, str] = {}
    for p in roster_list:
        key              = normalize_name(p["Player_Name"])
        new_index[key]   = p["Team_Name"]
        name_map[key]    = p["Player_Name"]
        players_out.append({
            "match_key":    key,
            "fantasy_team": p["Team_Name"],
            "player_name":  p["Player_Name"],
        })

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    os.makedirs(os.path.dirname(FANTASY_PATH), exist_ok=True)
    with open(FANTASY_PATH, "w") as f:
        json.dump({"synced_at": synced_at, "players": players_out}, f, indent=2)

    _fantasy_index   = new_index
    _fantasy_synced_at = synced_at
    _cache.clear()

    events = _detect_fantasy_events(old_index, new_index, name_map, synced_at)
    if events:
        _write_fantasy_events(events)

    return {"synced_at": synced_at, "player_count": len(new_index), "events": len(events)}
