"""Tests for service_guard.py."""

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

class TestServiceGuardEdges:

    def test_entity_id_as_a_single_string(self):
        from custom_components.roomba_plus.service_guard import _entity_ids

        assert _entity_ids(SimpleNamespace(data={"entity_id": "vacuum.r"})) == ["vacuum.r"]
        assert _entity_ids(SimpleNamespace(data={"entity_id": 5})) == []

    def test_unknown_and_foreign_entities_are_left_to_the_handler(self, monkeypatch):
        """The guard only refuses Roomba+ entries that are not loaded; an
        unknown entity or another integration's entity is the handler's
        business, with its own message."""
        from custom_components.roomba_plus import service_guard

        foreign = MagicMock(config_entry_id="other")
        unknown = None
        regs = {"vacuum.unknown": unknown, "light.kitchen": foreign}
        monkeypatch.setattr(service_guard.er, "async_get",
                            lambda _h: MagicMock(async_get=lambda e: regs.get(e)))
        hass = MagicMock()
        hass.config_entries.async_get_entry.return_value = MagicMock(domain="hue")
        call = SimpleNamespace(data={"entity_id": ["vacuum.unknown", "light.kitchen"]},
                               service="clean_room")
        service_guard.raise_if_target_not_loaded(hass, call)   # must not raise
