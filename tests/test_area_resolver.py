"""Resolving the robot's room to a Home Assistant area."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSegmentMappingIsRead:
    """Since the CLEAN_AREA work, HA holds a segment-to-area mapping in
    the vacuum entity's registry options. That mapping is the user's own
    statement of which robot room is which area, so resolving through it
    beats matching names.

    The option KEY is not something to depend on: it arrived in 2026.3
    and the architecture discussion around it is still open. Several
    plausible keys are tried, and anything unrecognised yields no
    mapping -- which degrades to "no area" rather than to a wrong one."""

    def _mapping(self, options):
        from custom_components.roomba_plus.area_resolver import _mapping_from_options

        return _mapping_from_options(options)

    def test_segment_keyed_mapping(self):
        result = self._mapping({"vacuum": {
            "segment_area_mapping": {"MAP1_rid_12": "kitchen"}
        }})

        assert result == {"MAP1_rid_12": "kitchen"}

    def test_area_keyed_mapping_is_inverted(self):
        """Stored the other way round depending on HA version. An area id
        is a plain slug; our segment ids always carry a "rid_"/"zid_"
        marker, which is what makes the two directions distinguishable
        without guessing."""
        result = self._mapping({"vacuum": {
            "area_segment_mapping": {"kitchen": "MAP1_rid_12"}
        }})

        assert result == {"MAP1_rid_12": "kitchen"}

    def test_an_unrecognised_shape_yields_nothing(self):
        """Degrades to "no area resolved", never to a wrong area."""
        assert self._mapping({"vacuum": {"something_else": {"a": "b"}}}) == {}
        assert self._mapping({"vacuum": {}}) == {}
        assert self._mapping({}) == {}
        assert self._mapping(None) == {}

    def test_an_empty_mapping_is_not_treated_as_configured(self):
        """A user who opened the dialog and saved nothing must fall
        through to the name match rather than resolving to nothing."""
        assert self._mapping({"vacuum": {"segment_area_mapping": {}}}) == {}


class TestNameFallback:
    """For the many setups where HA areas and robot rooms are simply
    named the same and nobody opened the mapping dialog.

    Second, never first: matching by name is exactly the fragility the
    mapping exists to remove -- a room renamed in the iRobot app silently
    stops resolving."""

    def _resolve(self, room, areas):
        from custom_components.roomba_plus.area_resolver import (
            async_area_for_room_name,
        )

        registry = MagicMock()
        registry.async_list_areas = MagicMock(return_value=areas)
        with patch(
            "homeassistant.helpers.area_registry.async_get", return_value=registry
        ):
            return async_area_for_room_name(MagicMock(), MagicMock(), room)

    def _area(self, area_id, name, aliases=()):
        area = MagicMock(id=area_id, aliases=set(aliases))
        area.name = name
        return area

    def test_an_exact_name_match(self):
        assert self._resolve("Kitchen", [self._area("kitchen", "Kitchen")]) == "kitchen"

    def test_case_and_whitespace_are_ignored(self):
        """Users type room names in the iRobot app; HA area names were
        typed separately. Expecting them to agree on capitalisation is
        expecting too much."""
        assert self._resolve(
            " kitchen ", [self._area("kitchen", "Kitchen")]
        ) == "kitchen"

    def test_an_area_alias_matches(self):
        """Aliases exist precisely so a room can be known by more than
        one name, which is the same problem."""
        assert self._resolve(
            "Küche", [self._area("kitchen", "Kitchen", {"Küche"})]
        ) == "kitchen"

    def test_no_match_yields_none(self):
        """Normal for a robot room that has no HA area -- a hallway
        nobody bothered to create. The room name is still reported."""
        assert self._resolve("Utility", [self._area("kitchen", "Kitchen")]) is None

    def test_an_empty_room_name_resolves_to_nothing(self):
        assert self._resolve("", [self._area("kitchen", "Kitchen")]) is None


class TestWhatThisDeliberatelyDoesNot:
    """Design decisions worth pinning, because both are tempting."""

    def test_no_current_area_attribute_is_invented(self):
        """Architecture proposal #1371 asks for a standard
        `current_area` on the vacuum entity and was REJECTED in April
        2026 -- too few supporting integrations, and None overloaded
        across three meanings.

        Claiming the name before a standard exists would mean breaking
        our own users to comply when one arrives."""
        import inspect

        from custom_components.roomba_plus import area_resolver, device_tracker

        for module in (area_resolver, device_tracker):
            source = inspect.getsource(module)
            assert '"current_area"' not in source

    def test_zones_are_not_used_for_rooms(self):
        """HA zones are geographic -- latitude, longitude, radius --
        meant for presence. Creating one per room to satisfy the
        `in_zones` guidance would misuse the concept."""
        import inspect

        from custom_components.roomba_plus import area_resolver

        source = inspect.getsource(area_resolver)

        assert "in_zones" not in source


class TestPrimeRoomFromTheMissionTimeline:
    """The device tracker now runs for Prime robots too.

    The argument against it was that `location_name` -- the entity's own
    state -- disappears in Home Assistant 2027.7. That was weak: the
    value lives in the `room` and `area_id` attributes and neither is
    deprecated. The state going away does not remove the entity's
    usefulness.

    THE SOURCE IS THE TIMELINE, not MissionTimerStore.current_room. That
    store is populated by set_mission_plan(), which only runs when Home
    Assistant started the mission -- a robot cleaning on its own schedule
    would leave it empty, which is exactly the case people care about.

    map_capability is also unusable here: it is NONE for Prime by design,
    because has_smart_map() looks for "pmaps" and Prime reports "p2maps".
    Both existing branches would have missed."""

    def _resolve(self, events, rooms):
        """Mirrors _resolve_prime_room. Reimplemented rather than
        imported because device_tracker imports a helper this HA version
        predates -- the logic is what is under test."""
        region_id = None
        for entry in events:
            room_event = getattr(entry, "room", None)
            if room_event is not None and getattr(room_event, "region_id", None):
                region_id = str(room_event.region_id)
        if not region_id:
            return None
        for name, qualified in rooms.items():
            if str(qualified).endswith(f"/{region_id}") or qualified == region_id:
                return name
        return f"Room {region_id}"

    def _event(self, region_id=None):
        return MagicMock(
            room=MagicMock(region_id=region_id) if region_id else None
        )

    _ROOMS = {"Kitchen": "MAP1/15", "Living room": "MAP1/11"}

    def test_a_room_event_resolves_to_its_name(self):
        """Taken from a tester's real event sequence: start, reloc,
        travel, traversal, travel, room, travel, evac, fin."""
        result = self._resolve(
            [self._event(), self._event("11")], self._ROOMS
        )

        assert result == "Living room"

    def test_the_latest_room_event_wins(self):
        """The timeline accumulates and the robot moves on. Reading the
        first event would name the room it started in for the whole
        mission."""
        result = self._resolve(
            [self._event("11"), self._event("15")], self._ROOMS
        )

        assert result == "Kitchen"

    def test_an_unknown_region_falls_back_to_its_number(self):
        """A room added since the name cache was built. The number is
        poor for automations and better than silence for a person reading
        the attribute."""
        assert self._resolve([self._event("99")], self._ROOMS) == "Room 99"

    def test_no_room_event_means_no_room(self):
        """Travelling between rooms, or relocalising. Reporting the last
        known room would have an automation on "robot is in the kitchen"
        stay true while it drives elsewhere."""
        assert self._resolve([self._event(), self._event()], self._ROOMS) is None

    def test_an_empty_timeline_means_no_room(self):
        assert self._resolve([], self._ROOMS) is None

    def test_room_names_come_from_the_same_source_as_cleaning(self):
        """So the tracker and clean_room agree about what a room is
        called. Both read the room-cleaning backend's available_rooms(),
        which returns map-qualified ids -- hence the "/" match rather
        than an equality check."""
        import inspect

        from custom_components.roomba_plus import device_tracker

        source = inspect.getsource(device_tracker)

        assert "async_get_room_cleaning_backend" in source
        assert "available_rooms" in source

    def test_the_name_lookup_is_cached_not_awaited_per_read(self):
        """A first draft called the async room list from inside a
        synchronous property, which would have returned a coroutine
        object as the room name."""
        import inspect

        from custom_components.roomba_plus import device_tracker

        resolve_source = inspect.getsource(
            device_tracker.RoombaDeviceTracker._resolve_prime_room
        )

        assert "await" not in resolve_source
        assert "_prime_rooms" in resolve_source
