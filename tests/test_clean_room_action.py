"""Unit tests for the roomba_plus.clean_room action helpers.

Tests the three pure-Python helpers in __init__.py:
  - _resolve_pmapv_id   live pmap freshness lookup
  - _resolve_rooms      name→region_id resolver with pmap_id fallback and
                        cross-floor guard (updated in v1.4.4)
  - Command params      shape built by _async_handle_clean_room logic

v1.4.4 additions:
  - _resolve_rooms now accepts zones with empty pmap_id (Alt 1 manual entry)
  - Empty pmap_id is resolved from live state.pmaps at call time
  - Zones with empty pmap_id AND empty state.pmaps raise ServiceValidationError
  - Mixed empty/stored pmap_ids on the same floor resolve consistently

No HA or roombapy installation required — uses the stubs from conftest.py.
"""
import sys
import os
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

# Constants mirroring const.py exactly — avoids importing the full package.
CONF_SMART_ZONE_DATA = "smart_zone_data"
SERVICE_CLEAN_ROOM   = "clean_room"
ATTR_ROOM_NAME       = "room_name"
ATTR_ORDERED         = "ordered"


# ── Local reference implementations ──────────────────────────────────────────
# These mirror the production helpers so we can test logic in isolation.
# The TestResolveRoomsProduction class tests the real implementation directly.

class ServiceValidationError(Exception):
    def __init__(self, message="", translation_domain=None,
                 translation_key=None, translation_placeholders=None):
        super().__init__(message)
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders or {}


def _resolve_pmapv_id_ref(state: dict, pmap_id: str):
    for pmap in state.get("pmaps", []):
        if pmap_id in pmap:
            return pmap[pmap_id]
    return None


def _resolve_rooms_ref(zone_data: dict, room_names: list, state: dict = None):
    """Reference implementation matching __init__.py as of v1.4.4."""
    state = state or {}
    index = {
        meta["name"].casefold(): (rid, meta.get("pmap_id", ""))
        for rid, meta in zone_data.items()
        if meta.get("name")
    }
    resolved = []
    unknown = []
    for name in room_names:
        match = index.get(name.casefold())
        if match is None:
            unknown.append(name)
        else:
            resolved.append(match)
    if unknown:
        raise ServiceValidationError(
            f"Unknown room(s): {', '.join(unknown)}",
            translation_key="rooms_not_found",
            translation_placeholders={"names": ", ".join(unknown)},
        )
    live_pmap_id = next(
        (next(iter(p)) for p in state.get("pmaps", []) if p), ""
    )
    resolved = [
        (rid, pmap_id if pmap_id else live_pmap_id)
        for rid, pmap_id in resolved
    ]
    pmap_ids = {p for _, p in resolved}
    if "" in pmap_ids:
        raise ServiceValidationError(
            "Could not resolve pmap_id",
            translation_key="pmap_not_resolved",
        )
    if len(pmap_ids) > 1:
        raise ServiceValidationError(
            "Rooms span multiple floors",
            translation_key="rooms_different_floors",
            translation_placeholders={"pmap_ids": ", ".join(pmap_ids)},
        )
    return resolved


# ── Shared fixtures ───────────────────────────────────────────────────────────

ZONE_DATA_SINGLE_FLOOR = {
    "3": {"name": "Kitchen",  "pmap_id": "map_ground_floor"},
    "5": {"name": "Hallway",  "pmap_id": "map_ground_floor"},
    "7": {"name": "Office",   "pmap_id": "map_ground_floor"},
}

ZONE_DATA_MULTI_FLOOR = {
    "3": {"name": "Kitchen",  "pmap_id": "map_ground_floor"},
    "9": {"name": "Bedroom",  "pmap_id": "map_first_floor"},
}

STATE_WITH_PMAP = {"pmaps": [{"map_ground_floor": "220101T120000"}]}


# ─────────────────────────────────────────────────────────────────────────────
# 1. _resolve_pmapv_id
# ─────────────────────────────────────────────────────────────────────────────

class TestResolvePmapvId:
    """Tests for the live pmap freshness resolver."""

    def test_found_single_pmap(self):
        state = {"pmaps": [{"abc123": "220101T120000"}]}
        assert _resolve_pmapv_id_ref(state, "abc123") == "220101T120000"

    def test_found_among_multiple_pmaps(self):
        state = {"pmaps": [{"floor1": "ts1"}, {"floor2": "ts2"}]}
        assert _resolve_pmapv_id_ref(state, "floor2") == "ts2"

    def test_not_found_returns_none(self):
        state = {"pmaps": [{"abc123": "ts1"}]}
        assert _resolve_pmapv_id_ref(state, "does_not_exist") is None

    def test_empty_pmaps_returns_none(self):
        assert _resolve_pmapv_id_ref({"pmaps": []}, "abc123") is None

    def test_missing_pmaps_key_returns_none(self):
        assert _resolve_pmapv_id_ref({}, "abc123") is None

    def test_always_reads_latest_value(self):
        """Simulates a map retrain: pmapv_id changes, resolver reflects it."""
        state_v1 = {"pmaps": [{"abc123": "220101T120000"}]}
        state_v2 = {"pmaps": [{"abc123": "230601T090000"}]}
        assert _resolve_pmapv_id_ref(state_v1, "abc123") == "220101T120000"
        assert _resolve_pmapv_id_ref(state_v2, "abc123") == "230601T090000"


# ─────────────────────────────────────────────────────────────────────────────
# 2. _resolve_rooms — reference implementation (pre-v1.4.4 behaviour)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRooms:
    """Core resolver tests — zones with stored pmap_id (normal path)."""

    def test_single_room(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor")]

    def test_multi_room_same_floor(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Hallway"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor"), ("5", "map_ground_floor")]

    def test_order_preserved(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Office", "Kitchen"], STATE_WITH_PMAP)
        assert result == [("7", "map_ground_floor"), ("3", "map_ground_floor")]

    def test_case_insensitive(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["kitchen"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor")]

    def test_mixed_case(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["KITCHEN", "halLWAY"], STATE_WITH_PMAP)
        assert result == [("3", "map_ground_floor"), ("5", "map_ground_floor")]

    def test_unknown_room_raises(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Bathroom"], STATE_WITH_PMAP)
        assert exc_info.value.translation_key == "rooms_not_found"
        assert "Bathroom" in exc_info.value.translation_placeholders["names"]

    def test_partial_unknown_raises(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Nonexistent"], STATE_WITH_PMAP)
        assert "Nonexistent" in exc_info.value.translation_placeholders["names"]

    def test_multiple_unknowns_all_reported(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["X", "Y"], STATE_WITH_PMAP)
        names = exc_info.value.translation_placeholders["names"]
        assert "X" in names and "Y" in names

    def test_cross_floor_raises(self):
        state = {"pmaps": [{"map_ground_floor": "ts1"}, {"map_first_floor": "ts2"}]}
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(ZONE_DATA_MULTI_FLOOR, ["Kitchen", "Bedroom"], state)
        assert exc_info.value.translation_key == "rooms_different_floors"
        placeholders = exc_info.value.translation_placeholders["pmap_ids"]
        assert "map_ground_floor" in placeholders
        assert "map_first_floor" in placeholders

    def test_empty_zone_data_raises(self):
        with pytest.raises(ServiceValidationError):
            _resolve_rooms_ref({}, ["Kitchen"], STATE_WITH_PMAP)

    def test_empty_room_list_returns_empty(self):
        result = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, [], STATE_WITH_PMAP)
        assert result == []

    def test_regions_payload_shape(self):
        """Verify the regions list shape expected by the MQTT command."""
        resolved = _resolve_rooms_ref(ZONE_DATA_SINGLE_FLOOR, ["Kitchen", "Hallway"], STATE_WITH_PMAP)
        regions = [{"region_id": rid, "type": "rid"} for rid, _ in resolved]
        assert regions == [
            {"region_id": "3", "type": "rid"},
            {"region_id": "5", "type": "rid"},
        ]

    def test_zone_missing_name_excluded(self):
        """Entries without a name are excluded from the index."""
        data = {
            "3": {"name": "Kitchen", "pmap_id": "map_a"},
            "4": {"pmap_id": "map_a"},   # no name — excluded
        }
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms_ref(data, ["Kitchen"], state)
        assert result == [("3", "map_a")]

    def test_zone_with_name_but_no_pmap_resolved_from_state(self):
        """Entry with name but no pmap_id: resolved from state.pmaps (v1.4.4)."""
        data = {"5": {"name": "Office"}}   # no pmap_id key at all
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms_ref(data, ["Office"], state)
        assert result == [("5", "map_a")]


# ─────────────────────────────────────────────────────────────────────────────
# 3. _resolve_rooms — empty pmap_id fallback (v1.4.4 Alt 1 manual entry)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoomsEmptyPmapId:
    """Tests for zones entered via manual entry with empty pmap_id."""

    def _zone(self, pmap_id: str = "") -> dict:
        return {"21": {"name": "Corridor", "pmap_id": pmap_id}}

    def _state(self, pmap_id: str = "abc123") -> dict:
        return {"pmaps": [{pmap_id: "v42"}]}

    def test_empty_pmap_resolved_from_live_state(self):
        """Zone with pmap_id='' gets pmap from state.pmaps at call time."""
        result = _resolve_rooms_ref(self._zone(""), ["Corridor"], self._state("abc123"))
        assert result == [("21", "abc123")]

    def test_stored_pmap_preferred_over_live(self):
        """Zone with stored pmap_id uses it — live state is not consulted."""
        result = _resolve_rooms_ref(self._zone("stored_pmap"), ["Corridor"], self._state("live_pmap"))
        assert result == [("21", "stored_pmap")]

    def test_empty_pmap_and_empty_state_raises(self):
        """Empty pmap_id + no state.pmaps → cannot resolve → raises."""
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(self._zone(""), ["Corridor"], {})
        assert exc_info.value.translation_key == "pmap_not_resolved"

    def test_empty_pmap_and_empty_pmap_list_raises(self):
        """Empty pmap_id + empty pmaps list → raises."""
        with pytest.raises(ServiceValidationError):
            _resolve_rooms_ref(self._zone(""), ["Corridor"], {"pmaps": []})

    def test_case_insensitive_with_empty_pmap(self):
        """Case-insensitivity works even when pmap_id must be resolved."""
        result = _resolve_rooms_ref(self._zone(""), ["CORRIDOR"], self._state())
        assert result[0][0] == "21"

    def test_unknown_room_raises_even_with_empty_pmap(self):
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(self._zone(""), ["Bedroom"], self._state())
        assert exc_info.value.translation_key == "rooms_not_found"

    def test_multiple_rooms_all_empty_pmap_resolved_consistently(self):
        """Multiple zones with empty pmap all resolve to the same live pmap."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},
            "22": {"name": "Kitchen",  "pmap_id": ""},
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms_ref(data, ["Corridor", "Kitchen"], state)
        assert result == [("21", "abc123"), ("22", "abc123")]

    def test_mixed_empty_and_stored_resolves_to_same_pmap(self):
        """Zone with empty pmap alongside zone with stored pmap.
        Empty one resolves to live value — both end up on same pmap → no cross-floor error."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},        # manual entry
            "22": {"name": "Kitchen",  "pmap_id": "abc123"},  # from MQTT
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms_ref(data, ["Corridor", "Kitchen"], state)
        assert result[0] == ("21", "abc123")
        assert result[1] == ("22", "abc123")

    def test_mixed_empty_and_different_stored_raises_cross_floor(self):
        """Empty pmap resolves to live pmap. If stored pmap differs → cross-floor error."""
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},              # resolves to abc123
            "22": {"name": "Bedroom",  "pmap_id": "other_floor"},   # different pmap
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        with pytest.raises(ServiceValidationError) as exc_info:
            _resolve_rooms_ref(data, ["Corridor", "Bedroom"], state)
        assert exc_info.value.translation_key == "rooms_different_floors"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _resolve_rooms — production implementation (imports from __init__)
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveRoomsProduction:
    """Runs a subset of tests against the real _resolve_rooms in __init__.py.

    This ensures the reference implementation in this test file stays in sync
    with the production code. If these tests fail but the reference tests pass,
    the production code has diverged from the spec.
    """

    def test_stored_pmap_used(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {"3": {"name": "Kitchen", "pmap_id": "map_a"}}
        state = {"pmaps": [{"map_a": "ts1"}]}
        result = _resolve_rooms(data, ["Kitchen"], state)
        assert result == [("3", "map_a")]

    def test_empty_pmap_resolved_from_state(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms(data, ["Corridor"], state)
        assert result == [("21", "abc123")]

    def test_empty_pmap_no_state_raises(self):
        from custom_components.roomba_plus import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {"21": {"name": "Corridor", "pmap_id": ""}}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Corridor"], {})

    def test_unknown_room_raises(self):
        from custom_components.roomba_plus import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {"3": {"name": "Kitchen", "pmap_id": "map_a"}}
        state = {"pmaps": [{"map_a": "ts1"}]}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Bathroom"], state)

    def test_mixed_pmap_resolves(self):
        from custom_components.roomba_plus import _resolve_rooms
        data = {
            "21": {"name": "Corridor", "pmap_id": ""},
            "22": {"name": "Kitchen",  "pmap_id": "abc123"},
        }
        state = {"pmaps": [{"abc123": "v42"}]}
        result = _resolve_rooms(data, ["Corridor", "Kitchen"], state)
        assert result[0] == ("21", "abc123")
        assert result[1] == ("22", "abc123")

    def test_cross_floor_raises(self):
        from custom_components.roomba_plus import _resolve_rooms
        from homeassistant.exceptions import ServiceValidationError
        data = {
            "3": {"name": "Kitchen", "pmap_id": "floor1"},
            "9": {"name": "Bedroom", "pmap_id": "floor2"},
        }
        state = {"pmaps": [{"floor1": "ts1"}, {"floor2": "ts2"}]}
        with pytest.raises(ServiceValidationError):
            _resolve_rooms(data, ["Kitchen", "Bedroom"], state)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Command params shape
# ─────────────────────────────────────────────────────────────────────────────

class TestCommandParams:
    """Verify the params dict passed to send_command is correctly formed."""

    def _build_params(self, resolved, pmap_id, user_pmapv_id, ordered=True):
        return {
            "ordered": 1 if ordered else 0,
            "pmap_id": pmap_id,
            "user_pmapv_id": user_pmapv_id,
            "regions": [
                {"region_id": rid, "type": "rid"}
                for rid, _ in resolved
            ],
        }

    def test_single_room_params(self):
        resolved = [("3", "map_a")]
        params = self._build_params(resolved, "map_a", "ts_fresh")
        assert params["pmap_id"] == "map_a"
        assert params["user_pmapv_id"] == "ts_fresh"
        assert params["ordered"] == 1
        assert params["regions"] == [{"region_id": "3", "type": "rid"}]

    def test_multi_room_params(self):
        resolved = [("3", "map_a"), ("5", "map_a")]
        params = self._build_params(resolved, "map_a", "ts_fresh")
        assert len(params["regions"]) == 2
        assert params["regions"][0] == {"region_id": "3", "type": "rid"}
        assert params["regions"][1] == {"region_id": "5", "type": "rid"}

    def test_ordered_false_sends_zero(self):
        resolved = [("3", "map_a"), ("5", "map_a")]
        params = self._build_params(resolved, "map_a", "ts", ordered=False)
        assert params["ordered"] == 0

    def test_ordered_true_sends_one(self):
        resolved = [("3", "map_a")]
        params = self._build_params(resolved, "map_a", "ts", ordered=True)
        assert params["ordered"] == 1

    def test_pmap_id_from_first_resolved_tuple(self):
        """pmap_id in params comes from the resolved tuple, not a separate lookup."""
        resolved = [("3", "map_ground"), ("5", "map_ground")]
        pmap_id = resolved[0][1]
        params = self._build_params(resolved, pmap_id, "ts")
        assert params["pmap_id"] == "map_ground"

    def test_regions_type_is_rid(self):
        """The iRobot API requires type='rid' for region targeting."""
        resolved = [("21", "map_a")]
        params = self._build_params(resolved, "map_a", "ts")
        assert params["regions"][0]["type"] == "rid"

    def test_user_pmapv_id_not_cached(self):
        """Simulate a map retrain: user_pmapv_id refreshed from live state."""
        state_v1 = {"pmaps": [{"map_a": "ts_old"}]}
        state_v2 = {"pmaps": [{"map_a": "ts_new"}]}
        assert _resolve_pmapv_id_ref(state_v1, "map_a") == "ts_old"
        assert _resolve_pmapv_id_ref(state_v2, "map_a") == "ts_new"
