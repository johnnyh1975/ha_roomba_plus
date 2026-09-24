"""Tests for calendar_rooms.py."""

from __future__ import annotations
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
import pytest


# ── formerly tests/test_coverage_small_gaps.py ──────────────────────────────────
#
# Small gaps in eight modules — quality scale, test-coverage (Silver).
#
# Mostly error branches and edge cases: a malformed input must not crash,
# must not return something wrong, and where the code logs, it must log.
# Each test pins what the branch is for, not only that it ran.

class TestCalendarRoomMatching:

    def test_empty_text_or_no_rooms_match_nothing(self):
        from custom_components.roomba_plus.calendar_rooms import match_rooms

        assert match_rooms("", {"1": "Kitchen"}) == []
        assert match_rooms("Kitchen", {}) == []
        assert match_rooms("!!!", {"1": "Kitchen"}) == []

    def test_a_room_with_an_empty_name_is_skipped(self):
        from custom_components.roomba_plus.calendar_rooms import match_rooms

        assert match_rooms("clean kitchen", {"1": "Kitchen", "2": "   "}) == ["1"]

    def test_two_rooms_with_the_same_name_are_ambiguous(self):
        """Refuse rather than guess which of two 'Bedroom' is meant."""
        from custom_components.roomba_plus.calendar_rooms import (
            AmbiguousRoomError,
            match_rooms,
        )

        with pytest.raises(AmbiguousRoomError) as exc:
            match_rooms("clean bedroom", {"1": "Bedroom", "2": "Bedroom"})
        assert exc.value.phrase == "bedroom"
        assert set(exc.value.candidates) == {"1", "2"}
