"""Tests for position_room.py: where a position lies during a clean."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.roomba_plus.position_room import room_at_position
from custom_components.roomba_plus.umf_aligner import UmfAligner


def _square(prefix: str, x0: float, y0: float, size: float) -> list[dict]:
    corners = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    return [{"id": f"{prefix}{i}", "coordinates": [x, y]} for i, (x, y) in enumerate(corners)]


def _outline(prefix: str) -> dict:
    return {"type": "polygon", "ids": [[f"{prefix}{i}" for i in range(4)]]}


def _living_room() -> SimpleNamespace:
    """@cwar36's case (#252): two clean zones inside one room."""
    gs = MagicMock()
    gs.door_markers = []
    aligner = UmfAligner(
        _square("r", 0, 0, 5) + _square("e", 1, 1, 1) + _square("c", 3, 3, 1),
        [{"id": "1", "name": "Living Room", "geometry": _outline("r")}],
        gs,
        zones=[
            {"id": "3", "name": "Entertainment Center", "geometry": _outline("e")},
            {"id": "5", "name": "Coffee Table", "geometry": _outline("c")},
        ],
    )
    aligner.align()  # resolves the outlines; no door evidence, so not aligned
    return SimpleNamespace(umf_aligner=aligner)


def test_a_zone_clean_is_followed_zone_by_zone():
    """#252: with the run's zones, a position in a zone names the zone,
    so two zones in one room are told apart."""
    data = _living_room()
    planned = ["Entertainment Center", "Coffee Table"]

    assert room_at_position(data, 1500, 1500, frame="umf", zones=planned) == "Entertainment Center"
    assert room_at_position(data, 3500, 3500, frame="umf", zones=planned) == "Coffee Table"
    # Between the zones the robot is in the room.
    assert room_at_position(data, 2500, 500, frame="umf", zones=planned) == "Living Room"


def test_a_room_clean_names_the_room():
    data = _living_room()
    assert room_at_position(data, 1500, 1500, frame="umf", zones=["Living Room"]) == "Living Room"
    assert room_at_position(data, 1500, 1500, frame="umf") == "Living Room"


class TestThePathTurnLearnerFlushes:
    """4.6.13: the counts are saved five minutes delayed; `async_flush`
    writes them now and drops the pending write, for the unload."""

    async def test_flush_writes_the_counts_and_leaves_nothing_pending(self, hass):
        from homeassistant.helpers.storage import Store

        from custom_components.roomba_plus.position_room import (
            PathTurnLearner,
            path_turn_store_key,
        )

        learner = PathTurnLearner()
        await learner.async_load(hass, "e1")
        learner.add("map|0", {"0": True}, "0")
        assert learner._store._delay_handle is not None
        await learner.async_flush()
        assert learner._store._delay_handle is None
        stored = await Store(hass, 1, path_turn_store_key("e1")).async_load()
        assert stored["maps"]["map|0"]["positions"] == 1

    async def test_flush_without_a_store_does_nothing(self):
        from custom_components.roomba_plus.position_room import PathTurnLearner

        await PathTurnLearner().async_flush()
