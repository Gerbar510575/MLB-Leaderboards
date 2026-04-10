"""
Unit tests for pure utility functions:
  - normalize_name
  - _detect_fantasy_events
"""
from backend.fantasy import normalize_name, _detect_fantasy_events

# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

class TestNormalizeName:
    def test_basic_lowercase(self):
        assert normalize_name("Aaron Judge") == "aaron judge"

    def test_strips_accents(self):
        assert normalize_name("Ronald Acuña Jr.") == "ronald acuna"

    def test_strips_jr_suffix(self):
        assert normalize_name("Vladimir Guerrero Jr.") == "vladimir guerrero"

    def test_strips_sr_suffix(self):
        assert normalize_name("Ken Griffey Sr.") == "ken griffey"

    def test_strips_roman_numerals(self):
        assert normalize_name("Cal Ripken II") == "cal ripken"
        assert normalize_name("Cal Ripken III") == "cal ripken"
        assert normalize_name("Henry IV") == "henry"

    def test_strips_apostrophe(self):
        assert normalize_name("Tyler O'Neill") == "tyler oneill"

    def test_strips_period(self):
        assert normalize_name("J.D. Martinez") == "jd martinez"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_extra_whitespace(self):
        assert normalize_name("  Aaron   Judge  ") == "aaron judge"

    def test_unicode_tilde(self):
        # ñ → n
        assert normalize_name("Adolis García") == "adolis garcia"


# ---------------------------------------------------------------------------
# _detect_fantasy_events
# ---------------------------------------------------------------------------

class TestDetectFantasyEvents:
    AT = "2026-04-10T12:00:00Z"
    NAME_MAP = {
        "aaron judge":   "Aaron Judge",
        "gerrit cole":   "Gerrit Cole",
        "shohei ohtani": "Shohei Ohtani",
    }

    def test_first_sync_returns_empty(self):
        """old_index is empty → first-ever sync, skip comparison."""
        events = _detect_fantasy_events(
            old_index={},
            new_index={"aaron judge": "Team A"},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert events == []

    def test_pickup(self):
        """Player added to a team (FA → team)."""
        events = _detect_fantasy_events(
            old_index={"gerrit cole": "Team A"},
            new_index={"gerrit cole": "Team A", "aaron judge": "Team B"},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert len(events) == 1
        evt = events[0]
        assert evt[3] == "pickup"
        assert evt[2] == "aaron judge"
        assert evt[4] is None
        assert evt[5] == "Team B"

    def test_drop(self):
        """Player removed from a team (team → FA)."""
        events = _detect_fantasy_events(
            old_index={"aaron judge": "Team A"},
            new_index={},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert len(events) == 1
        evt = events[0]
        assert evt[3] == "drop"
        assert evt[4] == "Team A"
        assert evt[5] is None

    def test_trade(self):
        """Player moved between teams."""
        events = _detect_fantasy_events(
            old_index={"aaron judge": "Team A"},
            new_index={"aaron judge": "Team B"},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert len(events) == 1
        evt = events[0]
        assert evt[3] == "trade"
        assert evt[4] == "Team A"
        assert evt[5] == "Team B"

    def test_no_change_returns_empty(self):
        old = {"aaron judge": "Team A", "gerrit cole": "Team B"}
        events = _detect_fantasy_events(
            old_index=old,
            new_index=dict(old),
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert events == []

    def test_multiple_events(self):
        events = _detect_fantasy_events(
            old_index={"aaron judge": "Team A", "gerrit cole": "Team B"},
            new_index={"aaron judge": "Team C", "shohei ohtani": "Team A"},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert len(events) == 3
        types = {e[3] for e in events}
        assert types == {"trade", "drop", "pickup"}

    def test_event_at_timestamp_preserved(self):
        events = _detect_fantasy_events(
            old_index={"aaron judge": "Team A"},
            new_index={},
            name_map=self.NAME_MAP,
            event_at=self.AT,
        )
        assert events[0][0] == self.AT

    def test_name_map_fallback_to_key(self):
        """If a key is not in name_map, the key itself is used as player_name."""
        events = _detect_fantasy_events(
            old_index={"unknown player": "Team A"},
            new_index={},
            name_map={},
            event_at=self.AT,
        )
        assert events[0][1] == "unknown player"
