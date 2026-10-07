"""Tests for command_record.py."""

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

class TestCommandRecord:

    def test_a_non_dict_payload_is_summarised_as_empty(self):
        from custom_components.roomba_plus.command_record import _summarise

        assert _summarise(["not", "a", "dict"]) == {}

    def test_recording_never_raises(self):
        """A diagnostic aid must never be the reason a command fails."""
        from custom_components.roomba_plus.command_record import record_command

        class _Broken:
            def append(self, _x):
                raise RuntimeError("full")

        entry = SimpleNamespace(runtime_data=SimpleNamespace(sent_commands=_Broken()))
        record_command(entry, "start", {"command": "start"})   # must not raise


class TestEachRegionSaysWhatItWasSentAs:
    """A room and a zone can share a number; `["0"]` alone could not say
    which one went out (@Hardy-196)."""

    def test_types_are_kept_beside_the_ids(self):
        from custom_components.roomba_plus.command_record import _summarise

        out = _summarise({"regions": [
            {"region_id": "0", "type": "zid", "params": {"twoPass": False}},
            {"region_id": "3", "type": "rid"},
        ]})
        assert out["regions"] == ["0", "3"]
        assert out["region_types"] == ["zid", "rid"]

    def test_bare_ids_add_no_types(self):
        from custom_components.roomba_plus.command_record import _summarise

        assert "region_types" not in _summarise({"region_ids": ["1", "2"]})
