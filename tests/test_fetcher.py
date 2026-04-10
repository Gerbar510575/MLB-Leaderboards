"""
Unit tests for _blocking_fetch via FakeAdapter.

Previously untestable because _blocking_fetch called pybaseball directly.
The DataSourceAdapter abstraction allows injecting controlled DataFrames
with no network I/O.

Coverage
--------
- Output structure (keys, types)
- Batter EV column mapping (_SAVANT_METRIC_MAP)
- xBA expected-stats join (by player_id)
- Sprint speed + team/position metadata
- Pitcher expected-stats column mapping (_PITCHER_EXP_COLS)
- Pitcher EV column mapping (_PITCHER_EV_COLS)
- BRef K/9, BB/9, K-BB% calculation
- Graceful no-op when pitcher / sprint sources return empty DataFrames
- ValueError raised when primary batter sources are empty
- _reverse_name and _safe_float helpers
"""
from __future__ import annotations


import pandas as pd
import pytest

from backend.fetcher import _blocking_fetch, _reverse_name, _safe_float

# ---------------------------------------------------------------------------
# FakeAdapter — controlled DataFrames, no network
# ---------------------------------------------------------------------------

JUDGE_ID   = 592450
OHTANI_ID  = 660271
COLE_ID    = 543037

BATTER_EV = pd.DataFrame({
    "last_name, first_name": ["Judge, Aaron", "Ohtani, Shohei"],
    "player_id":             [JUDGE_ID,        OHTANI_ID],
    "attempts":              [250,              220],
    "avg_hit_speed":         [95.5,             93.2],
    "avg_hit_angle":         [17.1,             14.3],
    "ev95percent":           [52.4,             48.1],
    "brl_percent":           [12.4,              9.8],
})

BATTER_EXPECTED = pd.DataFrame({
    "player_id": [JUDGE_ID,  OHTANI_ID],
    "pa":        [550,        500],
    "est_ba":    [0.310,      0.295],
    "est_slg":   [0.580,      0.560],
    "est_woba":  [0.420,      0.400],
    "est_woba_minus_woba_diff": [0.015, -0.010],
})

SPRINT_SPEED = pd.DataFrame({
    "player_id":       [float(JUDGE_ID), float(OHTANI_ID)],
    "sprint_speed":    [27.5,             28.1],
    "competitive_runs":[55,               60],
    "team":            ["NYY",            "LAD"],
    "position":        ["RF",             "DH"],
})

PITCHER_EXPECTED = pd.DataFrame({
    "last_name, first_name":  ["Cole, Gerrit"],
    "player_id":              [COLE_ID],
    "pa":                     [750],
    "xera":                   [2.85],
    "era_minus_xera_diff":    [-0.20],
    "est_woba":               [0.290],
})

PITCHER_EV = pd.DataFrame({
    "last_name, first_name": ["Cole, Gerrit"],
    "player_id":             [COLE_ID],
    "attempts":              [300],
    "ev95percent":           [38.0],
    "brl_percent":           [6.5],
    "avg_hit_speed":         [87.5],
})

PITCHER_BREF = pd.DataFrame({
    "mlbID": [float(COLE_ID)],
    "BF":    [750],
    "SO9":   [11.5],
    "BB":    [40.0],
    "IP":    [200.0],
    "SO":    [255.0],
})


class FakeAdapter:
    """Returns the module-level DataFrames — copies to prevent mutation."""

    def fetch_batter_ev(self, year):       return BATTER_EV.copy()
    def fetch_batter_expected(self, year): return BATTER_EXPECTED.copy()
    def fetch_sprint_speed(self, year):    return SPRINT_SPEED.copy()
    def fetch_pitcher_expected(self, year):return PITCHER_EXPECTED.copy()
    def fetch_pitcher_ev(self, year):      return PITCHER_EV.copy()
    def fetch_pitcher_bref(self, year):    return PITCHER_BREF.copy()


class EmptyPitcherAdapter(FakeAdapter):
    """Pitcher sources return empty DataFrames — batter data still works."""
    def fetch_pitcher_expected(self, year): return pd.DataFrame()
    def fetch_pitcher_ev(self, year):       return pd.DataFrame()
    def fetch_pitcher_bref(self, year):     return pd.DataFrame()


class EmptySpeedAdapter(FakeAdapter):
    """Sprint speed returns empty — team/pos fall back to empty strings."""
    def fetch_sprint_speed(self, year): return pd.DataFrame()


class EmptyPrimaryAdapter(FakeAdapter):
    """Source 1 (batter EV) returns empty — should raise ValueError."""
    def fetch_batter_ev(self, year): return pd.DataFrame()


class EmptyExpectedAdapter(FakeAdapter):
    """Source 2 (batter expected) returns empty — should raise ValueError."""
    def fetch_batter_expected(self, year): return pd.DataFrame()


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestReverseNameHelper:
    def test_standard_format(self):
        assert _reverse_name("Judge, Aaron") == "Aaron Judge"

    def test_single_part(self):
        assert _reverse_name("Madonna") == "Madonna"

    def test_non_string_coerced(self):
        result = _reverse_name(123)
        assert isinstance(result, str)


class TestSafeFloatHelper:
    def test_valid_number(self):
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_nan_returns_none(self):
        assert _safe_float(float("nan")) is None

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_string_number(self):
        assert _safe_float("2.5") == pytest.approx(2.5)

    def test_non_numeric_string_returns_none(self):
        assert _safe_float("N/A") is None

    def test_int_coerced(self):
        assert _safe_float(10) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# _blocking_fetch — output structure
# ---------------------------------------------------------------------------

class TestBlockingFetchStructure:
    def setup_method(self):
        self.result = _blocking_fetch(2026, adapter=FakeAdapter())

    def test_top_level_keys(self):
        for key in ("source", "season", "fetched_at", "players", "aggregates"):
            assert key in self.result, f"Missing key: {key}"

    def test_source_is_real(self):
        assert self.result["source"] == "real"

    def test_season_matches_year(self):
        assert self.result["season"] == 2026

    def test_fetched_at_is_iso_string(self):
        assert "T" in self.result["fetched_at"]
        assert "Z" in self.result["fetched_at"]

    def test_players_is_list(self):
        assert isinstance(self.result["players"], list)

    def test_aggregates_is_list(self):
        assert isinstance(self.result["aggregates"], list)


# ---------------------------------------------------------------------------
# Players table
# ---------------------------------------------------------------------------

class TestPlayersTable:
    def setup_method(self):
        self.result = _blocking_fetch(2026, adapter=FakeAdapter())
        self.players = {p["player_id"]: p for p in self.result["players"]}

    def test_batter_names_reversed(self):
        assert self.players[str(JUDGE_ID)]["player_name"] == "Aaron Judge"
        assert self.players[str(OHTANI_ID)]["player_name"] == "Shohei Ohtani"

    def test_pitcher_included(self):
        assert str(COLE_ID) in self.players
        assert self.players[str(COLE_ID)]["player_name"] == "Gerrit Cole"

    def test_team_from_sprint_speed(self):
        assert self.players[str(JUDGE_ID)]["team"] == "NYY"
        assert self.players[str(OHTANI_ID)]["team"] == "LAD"

    def test_position_from_sprint_speed(self):
        assert self.players[str(JUDGE_ID)]["position"] == "RF"

    def test_pitcher_position_defaults_to_p(self):
        assert self.players[str(COLE_ID)]["position"] == "P"

    def test_no_duplicate_players(self):
        ids = [p["player_id"] for p in self.result["players"]]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Batter EV aggregates (_SAVANT_METRIC_MAP)
# ---------------------------------------------------------------------------

class TestBatterEVAggregates:
    def setup_method(self):
        result = _blocking_fetch(2026, adapter=FakeAdapter())
        self.agg = {
            (r["player_id"], r["metric_name"]): r
            for r in result["aggregates"]
        }

    def test_exit_velocity_mapped(self):
        rec = self.agg[(str(JUDGE_ID), "exit_velocity")]
        assert rec["avg_value"] == pytest.approx(95.5, abs=0.01)
        assert rec["sample_type"] == "BBE"
        assert rec["sample_size"] == 250

    def test_hard_hit_rate_mapped(self):
        rec = self.agg[(str(JUDGE_ID), "hard_hit_rate")]
        assert rec["avg_value"] == pytest.approx(52.4, abs=0.01)

    def test_barrel_rate_mapped(self):
        rec = self.agg[(str(JUDGE_ID), "barrel_rate")]
        assert rec["avg_value"] == pytest.approx(12.4, abs=0.01)

    def test_launch_angle_mapped(self):
        rec = self.agg[(str(JUDGE_ID), "launch_angle")]
        assert rec["avg_value"] == pytest.approx(17.1, abs=0.01)

    def test_both_batters_have_ev(self):
        assert (str(OHTANI_ID), "exit_velocity") in self.agg


# ---------------------------------------------------------------------------
# xBA expected-stats join
# ---------------------------------------------------------------------------

class TestXbaAggregates:
    def setup_method(self):
        result = _blocking_fetch(2026, adapter=FakeAdapter())
        self.agg = {
            (r["player_id"], r["metric_name"]): r
            for r in result["aggregates"]
        }

    def test_xba_joined_by_player_id(self):
        rec = self.agg[(str(JUDGE_ID), "xba")]
        assert rec["avg_value"] == pytest.approx(0.310, abs=0.001)
        assert rec["sample_type"] == "PA"
        assert rec["sample_size"] == 550

    def test_xslg_present(self):
        assert (str(JUDGE_ID), "xslg") in self.agg

    def test_xwoba_present(self):
        assert (str(JUDGE_ID), "xwoba") in self.agg

    def test_xwoba_diff_present(self):
        assert (str(JUDGE_ID), "xwoba_diff") in self.agg


# ---------------------------------------------------------------------------
# Sprint speed aggregates
# ---------------------------------------------------------------------------

class TestSprintSpeedAggregates:
    def setup_method(self):
        result = _blocking_fetch(2026, adapter=FakeAdapter())
        self.agg = {
            (r["player_id"], r["metric_name"]): r
            for r in result["aggregates"]
        }

    def test_sprint_speed_present(self):
        rec = self.agg[(str(JUDGE_ID), "sprint_speed")]
        assert rec["avg_value"] == pytest.approx(27.5, abs=0.1)
        assert rec["sample_type"] == "sprints"
        assert rec["sample_size"] == 55


# ---------------------------------------------------------------------------
# Pitcher aggregates
# ---------------------------------------------------------------------------

class TestPitcherAggregates:
    def setup_method(self):
        result = _blocking_fetch(2026, adapter=FakeAdapter())
        self.agg = {
            (r["player_id"], r["metric_name"]): r
            for r in result["aggregates"]
        }

    def test_xera_mapped(self):
        rec = self.agg[(str(COLE_ID), "p_xera")]
        assert rec["avg_value"] == pytest.approx(2.85, abs=0.01)
        assert rec["sample_type"] == "PA"

    def test_era_diff_mapped(self):
        assert (str(COLE_ID), "p_era_diff") in self.agg

    def test_xwoba_against_mapped(self):
        rec = self.agg[(str(COLE_ID), "p_xwoba_against")]
        assert rec["avg_value"] == pytest.approx(0.290, abs=0.001)

    def test_pitcher_ev_hard_hit_rate(self):
        rec = self.agg[(str(COLE_ID), "p_hard_hit_rate")]
        assert rec["avg_value"] == pytest.approx(38.0, abs=0.1)
        assert rec["sample_type"] == "BBE"

    def test_pitcher_ev_barrel_rate(self):
        assert (str(COLE_ID), "p_barrel_rate") in self.agg

    def test_pitcher_ev_avg_ev(self):
        rec = self.agg[(str(COLE_ID), "p_avg_ev")]
        assert rec["avg_value"] == pytest.approx(87.5, abs=0.1)

    def test_k9_from_bref(self):
        rec = self.agg[(str(COLE_ID), "p_k9")]
        assert rec["avg_value"] == pytest.approx(11.5, abs=0.01)
        assert rec["sample_type"] == "PA"

    def test_bb9_calculated_from_bref(self):
        # BB/9 = BB / IP * 9 = 40 / 200 * 9 = 1.80
        rec = self.agg[(str(COLE_ID), "p_bb9")]
        assert rec["avg_value"] == pytest.approx(1.80, abs=0.01)

    def test_k_bb_diff_calculated(self):
        # K-BB% = (SO - BB) / BF * 100 = (255 - 40) / 750 * 100 ≈ 28.67
        rec = self.agg[(str(COLE_ID), "p_k_bb_diff")]
        assert rec["avg_value"] == pytest.approx(28.67, abs=0.01)


# ---------------------------------------------------------------------------
# Graceful degradation — empty pitcher / sprint sources
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_empty_pitcher_sources_still_returns_batters(self):
        result = _blocking_fetch(2026, adapter=EmptyPitcherAdapter())
        player_ids = {p["player_id"] for p in result["players"]}
        assert str(JUDGE_ID) in player_ids
        assert str(OHTANI_ID) in player_ids
        # No pitcher aggregates
        pitcher_agg = [r for r in result["aggregates"] if r["metric_name"].startswith("p_")]
        assert pitcher_agg == []

    def test_empty_sprint_speed_falls_back_to_empty_team(self):
        result = _blocking_fetch(2026, adapter=EmptySpeedAdapter())
        players = {p["player_id"]: p for p in result["players"]}
        assert players[str(JUDGE_ID)]["team"] == ""
        assert players[str(JUDGE_ID)]["position"] == ""

    def test_empty_sprint_speed_no_sprint_aggregates(self):
        result = _blocking_fetch(2026, adapter=EmptySpeedAdapter())
        speed_agg = [r for r in result["aggregates"] if r["metric_name"] == "sprint_speed"]
        assert speed_agg == []


# ---------------------------------------------------------------------------
# ValueError on missing primary data
# ---------------------------------------------------------------------------

class TestValueErrorOnMissingData:
    def test_empty_batter_ev_raises(self):
        with pytest.raises(ValueError, match="No Statcast batted-ball data"):
            _blocking_fetch(2026, adapter=EmptyPrimaryAdapter())

    def test_empty_batter_expected_raises(self):
        with pytest.raises(ValueError, match="No Baseball Savant expected stats"):
            _blocking_fetch(2026, adapter=EmptyExpectedAdapter())
