"""Tests for the device_tracker platform (v2.9.0 DEVICE-TRACKER).

Tier-aware location reporting: SMART robots get room-level granularity via
the shared _resolve_smart_tier_room_state() function (same source as
RoombaMissionProgress's current_room), EPHEMERAL robots get "Angedockt"/
"Unterwegs" only for now (room/zone detection extension point, currently
returning None — see _resolve_ephemeral_tier_room's docstring).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_tracker(map_capability_value: str = "smart"):
    """Build a minimal RoombaDeviceTracker with stubbed vacuum/entry state."""
    from custom_components.roomba_plus.device_tracker import RoombaDeviceTracker

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}

    entry = MagicMock()
    entry.runtime_data.map_capability.value = map_capability_value

    tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
    tracker.vacuum = roomba
    tracker._blid = "TESTBLID"
    tracker.vacuum_state = {}
    tracker._config_entry = entry
    tracker.hass = MagicMock()
    tracker.hass.config.language = "de"
    return tracker, roomba, entry


def _set_state(roomba, phase: str = "", pose: dict | None = None):
    reported: dict = {"cleanMissionStatus": {"phase": phase}}
    if pose is not None:
        reported["pose"] = pose
    roomba.master_state = {"state": {"reported": reported}}


class TestLocationNameDockedFallback:
    """Docked/idle must always show the dock label, never "home"."""

    def test_docked_phase_shows_docked_label(self):
        tracker, roomba, _ = _make_tracker()
        _set_state(roomba, phase="charge")
        assert tracker.state == "Angedockt"

    def test_idle_empty_phase_shows_docked_label(self):
        tracker, roomba, _ = _make_tracker()
        _set_state(roomba, phase="")
        assert tracker.state == "Angedockt"


class TestLocationNameNullRegression:
    """v3.4.2 NULL-REGRESSION — cleanMissionStatus: null must not crash
    location_name, the same confirmed-real class of bug as bbrun/bin/cap
    elsewhere in this codebase (see test_edge_cases.py)."""

    def test_explicit_null_clean_mission_status_does_not_raise(self):
        tracker, roomba, _ = _make_tracker()
        roomba.master_state = {"state": {"reported": {"cleanMissionStatus": None}}}
        # Falls through to the empty-phase branch, same as a docked robot.
        assert tracker.state == "Angedockt"

    def test_docked_label_respects_language(self):
        tracker, roomba, _ = _make_tracker()
        tracker.hass.config.language = "en"
        _set_state(roomba, phase="stop")
        assert tracker.state == "Docked"

    def test_unknown_language_falls_back_to_english(self):
        tracker, roomba, _ = _make_tracker()
        tracker.hass.config.language = "ja"
        _set_state(roomba, phase="charge")
        assert tracker.state == "Docked"


class TestLocationNameSmartTier:
    """SMART-tier robots get room-level granularity, shared with
    RoombaMissionProgress's current_room via _resolve_smart_tier_room_state."""

    def test_returns_room_name_when_resolved(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="smart")
        _set_state(roomba, phase="run")
        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={"current_room": "Kitchen", "next_room": "Hallway"},
        ):
            assert tracker.state == "Kitchen"

    def test_falls_back_to_active_label_when_room_unknown(self):
        """No room resolved (e.g. no MTS mission, or estimates entirely
        unavailable) — must show the generic active-mission label, not
        None and not crash."""
        tracker, roomba, _ = _make_tracker(map_capability_value="smart")
        _set_state(roomba, phase="run")
        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={},
        ):
            assert tracker.state == "Unterwegs"


class TestLocationNameEphemeralTier:
    """v2.9.0 — EPHEMERAL tier (e.g. the 980) must ALWAYS get a sensible
    state, even though room/zone detection isn't available yet. The
    extension point (_resolve_ephemeral_tier_room) currently always
    returns None — confirmed structurally limited for dense-MQTT-sampling
    robots — but the platform around it is fully tier-agnostic, ready for
    when that's fixed.
    """

    def test_active_mission_shows_generic_fallback_not_none(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="ephemeral")
        _set_state(roomba, phase="run")
        assert tracker.state == "Unterwegs"

    def test_docked_shows_docked_label_same_as_smart_tier(self):
        tracker, roomba, _ = _make_tracker(map_capability_value="ephemeral")
        _set_state(roomba, phase="charge")
        assert tracker.state == "Angedockt"

    def test_extension_point_returns_none_today(self):
        """Documents current behaviour explicitly — once EPHEMERAL room/
        zone detection is fixed, only this function's return value
        should need to change."""
        tracker, roomba, entry = _make_tracker(map_capability_value="ephemeral")
        result = tracker._resolve_ephemeral_tier_room(entry.runtime_data)
        assert result is None


class TestExtraStateAttributes:
    """Raw pose (x_mm/y_mm) always exposed when available, regardless of
    tier — for users who want their own zone logic externally."""

    def test_pose_converted_cm_to_mm(self):
        """v2.9.0 units fix: pose.point.x/y is in centimetres, not
        millimetres — must be converted, matching POSE_POINT_CM_TO_MM."""
        tracker, roomba, entry = _make_tracker()
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="charge", pose={"point": {"x": 120, "y": 45}})

        attrs = tracker.extra_state_attributes
        assert attrs["x_mm"] == 1200
        assert attrs["y_mm"] == 450

    def test_no_pose_data_omits_coordinates(self):
        tracker, roomba, entry = _make_tracker()
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="charge", pose=None)

        attrs = tracker.extra_state_attributes
        assert "x_mm" not in attrs
        assert "y_mm" not in attrs

    def test_room_and_next_room_exposed_during_active_smart_mission(self):
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        mts = MagicMock()
        mts.mission_id = "m1"
        entry.runtime_data.mission_timer_store = mts
        _set_state(roomba, phase="run")

        with patch(
            "custom_components.roomba_plus.sensor._resolve_smart_tier_room_state",
            return_value={"current_room": "Kitchen", "next_room": "Hallway"},
        ):
            attrs = tracker.extra_state_attributes

        assert attrs["room"] == "Kitchen"
        assert attrs["next_room"] == "Hallway"

    def test_room_omitted_when_docked(self):
        """No active mission — room/next_room attributes must not appear
        at all (not even as None), since they're meaningless while docked."""
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        mts = MagicMock()
        mts.mission_id = "m1"
        entry.runtime_data.mission_timer_store = mts
        _set_state(roomba, phase="charge")

        attrs = tracker.extra_state_attributes
        assert "room" not in attrs
        assert "next_room" not in attrs

    def test_room_omitted_when_no_mission_timer_store(self):
        tracker, roomba, entry = _make_tracker(map_capability_value="smart")
        entry.runtime_data.mission_timer_store = None
        _set_state(roomba, phase="run")

        attrs = tracker.extra_state_attributes
        assert "room" not in attrs


class TestNewStateFilter:
    def test_filters_on_mission_status_or_pose(self):
        tracker, _, _ = _make_tracker()
        assert tracker.new_state_filter({"cleanMissionStatus": {}}) is True
        assert tracker.new_state_filter({"pose": {}}) is True
        assert tracker.new_state_filter({"batPct": 50}) is False


class TestSourceType:
    def test_source_type_is_router(self):
        """ROUTER is the closest existing SourceType for a locally-
        determined, non-GPS data source — there is no dedicated 'robot
        odometry' source type in HA core."""
        from homeassistant.components.device_tracker import SourceType
        tracker, _, _ = _make_tracker()
        assert tracker.source_type == SourceType.ROUTER


class TestEntityRegistryEnabledDefault:
    """v2.10.3 — device tracker must be enabled by default.

    TrackerEntity.entity_registry_enabled_default returns False when both
    mac_address and device_info are None. Both are always None here:
    we use BLID for identity (no MAC), and TrackerEntity's own device_info
    is None by design ('device tracker entities should not create device
    registry entries'). Without _attr_entity_registry_enabled_default = True
    the entity is registered but disabled, invisible in the UI.

    Confirmed root cause of Thonno's field report: 'I don't seem to have
    that entity on my i7+' — the entity existed in the registry but was
    disabled by default on every installation regardless of robot tier.
    """

    def test_entity_registry_enabled_default_is_true(self):
        """entity_registry_enabled_default must return True on instances.
        HA's Entity base class mangles _attr_* keys internally, so the
        class-level __dict__ key name is not reliable to test — the
        property's runtime return value is what actually matters."""
        tracker, _, _ = _make_tracker()
        assert tracker.entity_registry_enabled_default is True

    def test_instance_entity_registry_enabled_default_is_true(self):
        tracker, _, _ = _make_tracker()
        assert tracker.entity_registry_enabled_default is True



class TestPrimeReadsItsPhaseFromTheShadow:
    """@chairstacker (#70): the tracker always said "Docked" on a Prime
    robot, whatever it was doing.

    `roomba_reported_state(self.vacuum)` returns `{}` when there is no
    local robot — which is every Prime entry — so the phase was always
    empty, the docked guard always matched, and `_resolve_room()`'s own
    Prime branch was unreachable.
    """

    @staticmethod
    def _tracker(phase):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.device_tracker import (
            RoombaDeviceTracker,
        )

        tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
        tracker.vacuum = None
        tracker._prime_rooms = {}
        coordinator = SimpleNamespace(
            data={"ro-currentstate": {"cleanMissionStatus": {"phase": phase}}}
        )
        tracker.hass = MagicMock()
        tracker.hass.config.language = 'en'
        tracker._config_entry = MagicMock()
        tracker._config_entry.runtime_data = SimpleNamespace(
            connection_type=ConnectionType.CLOUD_ONLY,
            prime_status_coordinator=coordinator,
            prime_live_rooms=None,
        )
        return tracker

    def test_a_running_prime_robot_is_not_reported_as_docked(self):
        tracker = self._tracker("run")

        assert tracker.state != "Docked"

    def test_a_docked_prime_robot_still_is(self):
        tracker = self._tracker("charge")

        assert tracker.state == "Docked"


class TestTheRoomNameCacheRefillsWhenEmpty:
    """@chairstacker (#70 follow-up): the tracker reported rooms in a38
    and reported "Room 16" — the number, not the name.

    `async_added_to_hass` fetches the room list once, at setup. On a
    cold start the map bundle has usually not been built yet, so the
    cache stays empty and every room falls through to `Room {id}`
    forever.
    """

    @staticmethod
    def _tracker(cached):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.device_tracker import (
            RoombaDeviceTracker,
        )

        tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
        tracker._prime_rooms = dict(cached)
        tracker.hass = MagicMock()
        tracker.async_write_ha_state = MagicMock()
        return tracker

    def test_an_empty_cache_triggers_a_refetch(self):
        tracker = self._tracker({})

        tracker._handle_prime_update()

        tracker.async_write_ha_state.assert_called_once()
        tracker.hass.async_create_task.assert_called_once()

    def test_a_filled_cache_does_not(self):
        """A robot with named rooms must not refetch the list on every
        coordinator message."""
        tracker = self._tracker({"Kitchen": "MAP-1/16"})

        tracker._handle_prime_update()

        tracker.async_write_ha_state.assert_called_once()
        tracker.hass.async_create_task.assert_not_called()


class TestZonesReachTheTrackerCache:
    """@chairstacker (#70): the tracker showed nothing while a zone was
    being cleaned.

    He assumed the missing zone names from #47 were the cause. They are
    not. `available_rooms()` reads `rooms_metadata`, which carries rooms
    and not zones — so a zone-targeted mission produced a region id that
    matched nothing in the cache. This would still be empty if every
    zone on his map had a name.

    Two mechanisms, and an earlier fix covered only one of them.
    """

    @staticmethod
    def _tracker(rooms, zone_names):
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.device_tracker import (
            RoombaDeviceTracker,
        )

        tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
        tracker._prime_rooms = {}
        entry = MagicMock()
        entry.runtime_data.prime_room_names = zone_names
        tracker._config_entry = entry
        tracker.hass = MagicMock()

        backend = MagicMock()
        backend.available_rooms = AsyncMock(return_value=dict(rooms))
        return tracker, backend, patch

    async def _refresh(self, rooms, zone_names):
        import asyncio

        tracker, backend, patch = self._tracker(rooms, zone_names)
        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=backend,
        ):
            await tracker._async_refresh_prime_rooms()
        return tracker._prime_rooms

    @pytest.mark.asyncio
    async def test_a_zone_reaches_the_cache(self):
        cache = await self._refresh(
            rooms={"Kitchen": "MAP/10"},
            zone_names={"101": "Guest Access Zone"},
        )

        assert cache.get("Guest Access Zone") == "101"

    @pytest.mark.asyncio
    async def test_rooms_win_a_name_collision(self):
        """A room's name comes from the map's own metadata; a zone's
        comes from whatever the last command called it."""
        cache = await self._refresh(
            rooms={"Kitchen": "MAP/10"},
            zone_names={"999": "Kitchen"},
        )

        assert cache["Kitchen"] == "MAP/10"

    @pytest.mark.asyncio
    async def test_rooms_still_arrive(self):
        """The zone merge must not disturb what already worked."""
        cache = await self._refresh(rooms={"Kitchen": "MAP/10"}, zone_names={})

        assert cache == {"Kitchen": "MAP/10"}


class TestAZoneCleanIsNamedToo:
    """@chairstacker (#64): during a zone mission the tracker read
    "Cleaning" — its fallback for "somewhere, unknown" — while his own
    diagnostics carried all seven zone names under
    `region_names.merged`.

    A timeline event has three place fields: `room` and `travel`, both
    keyed `region_id`, and `zone`, keyed `zone_id`. Only the first two
    were read, so a zone mission produced no id — and a name lookup
    with nothing to look up.

    Three rounds of explanation called this a protocol gap. It was a
    field nobody read, which is the same shape as `zone_layers` and
    `pd_state` this week.
    """

    @staticmethod
    def _resolve(event_kwargs, names):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.device_tracker import (
            RoombaDeviceTracker,
        )

        tracker = RoombaDeviceTracker.__new__(RoombaDeviceTracker)
        tracker._prime_rooms = names

        event = MagicMock()
        event.room = event_kwargs.get("room")
        event.travel = event_kwargs.get("travel")
        event.zone = event_kwargs.get("zone")
        report = MagicMock()
        report.event = [event]

        data = MagicMock()
        data.prime_coordinator.data = report
        return tracker._resolve_prime_room(data)

    @staticmethod
    def _zone(zone_id):
        from unittest.mock import MagicMock

        z = MagicMock()
        z.zone_id = zone_id
        return z

    @staticmethod
    def _room(region_id):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.region_id = region_id
        return r

    def test_a_zone_event_resolves_to_its_name(self):
        """Zone 107 is the one his last recorded mission targeted."""
        name = self._resolve(
            {"room": None, "travel": None, "zone": self._zone("107")},
            {"Guest Access Zone": "107"},
        )

        assert name == "Guest Access Zone"

    def test_a_room_event_still_resolves(self):
        name = self._resolve(
            {"room": self._room("10"), "travel": None, "zone": None},
            {"Kitchen": "MAP/10"},
        )

        assert name == "Kitchen"

    def test_neither_present_resolves_nothing(self):
        assert self._resolve(
            {"room": None, "travel": None, "zone": None}, {"Kitchen": "10"}
        ) is None
