"""Room-targeted cleaning backends, both generations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import robot_mock, hass_mock, entry_mock


class TestBackendSelection:
    """Which backend a robot gets — and None as a real answer.

    None replaces the old `map_capability == SMART` gate, which was a
    Classic-shaped question: has_smart_map() looks for a "pmaps" key and
    Prime robots report "p2maps", so every Prime robot failed it even
    after room cleaning was confirmed working on real hardware.

    Redefining that flag was the obvious fix and the wrong one — it is
    read in 32 places across seven modules, many of them Classic cloud
    paths that do not exist for Prime. "Is there a backend" answers the
    narrow question directly and cannot be misread at 32 call sites."""

    def _entry(self, *, connection_type, prime_robot=None,
               map_capability=None, has_cloud=False):
        entry = entry_mock()
        entry.runtime_data.connection_type = connection_type
        entry.runtime_data.prime_robot = prime_robot
        entry.runtime_data.map_capability = map_capability
        entry.runtime_data.has_cloud = has_cloud
        return entry

    def test_a_prime_robot_gets_the_prime_backend(self):
        """The case the old gate could never satisfy."""
        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
            async_get_room_cleaning_backend,
        )

        entry = self._entry(
            connection_type=ConnectionType.CLOUD_ONLY, prime_robot=MagicMock()
        )

        assert isinstance(async_get_room_cleaning_backend(entry), PrimeRoomCleaning)

    def test_a_smart_classic_robot_still_gets_one(self):
        """Existing behaviour must not change while adding the new path."""
        from custom_components.roomba_plus.models import ConnectionType, MapCapability
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
            async_get_room_cleaning_backend,
        )

        entry = self._entry(
            connection_type=ConnectionType.LOCAL_PUSH,
            map_capability=MapCapability.SMART,
            has_cloud=True,
        )

        assert isinstance(async_get_room_cleaning_backend(entry), ClassicRoomCleaning)

    def test_a_classic_robot_without_smart_maps_gets_none(self):
        """A 600-series robot genuinely cannot clean a named room."""
        from custom_components.roomba_plus.models import ConnectionType, MapCapability
        from custom_components.roomba_plus.room_cleaning import (
            async_get_room_cleaning_backend,
        )

        entry = self._entry(
            connection_type=ConnectionType.LOCAL_PUSH,
            map_capability=MapCapability.EPHEMERAL,
            has_cloud=True,
        )

        assert async_get_room_cleaning_backend(entry) is None

    def test_a_smart_classic_robot_without_cloud_still_gets_one(self):
        """CORRECTED from an earlier draft of this test, which asserted
        the opposite and was wrong.

        Room names live in config_entry.options (smart_zone_data); the
        cloud coordinator only ENRICHES that list. Requiring cloud here
        would lock out every Classic user running without credentials --
        a real configuration, and one the gate this replaced allowed.

        Three existing service tests caught it, which is the reason the
        old gate's exact shape was worth preserving rather than
        tightening on the way past."""
        from custom_components.roomba_plus.models import ConnectionType, MapCapability
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
            async_get_room_cleaning_backend,
        )

        entry = self._entry(
            connection_type=ConnectionType.LOCAL_PUSH,
            map_capability=MapCapability.SMART,
            has_cloud=False,
        )

        assert isinstance(async_get_room_cleaning_backend(entry), ClassicRoomCleaning)


class TestPrimeRoomCleaning:
    """The Prime backend, against the payload shape confirmed on real
    hardware (DaRealGuGu: the robot travelled to the named room and
    cleaned it)."""

    @staticmethod
    def _room(room_id, name):
        """MagicMock(name=...) sets the MOCK's name, not a `name`
        attribute -- a trap that silently produces a mock repr where a
        room name was expected."""
        room = MagicMock(room_id=room_id)
        room.name = name
        return room

    def _backend(self, *, rooms=None, maps=None, current_map=None):
        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        robot = MagicMock()
        robot.get_active_map_versions = AsyncMock(
            return_value=maps if maps is not None else [{"p2map_id": "MAP-1"}]
        )
        map_data = MagicMock()
        map_data.rooms_metadata = rooms if rooms is not None else [
            self._room("12", "Kitchen"),
            self._room("13", "Living room"),
        ]
        robot.get_map_metadata = AsyncMock(return_value=map_data)
        robot.send_routine_command_via_cmd_topic = AsyncMock()

        data = MagicMock(blid="BLID123", prime_robot=robot)
        data.prime_status_coordinator.data = (
            {"ro-currentstate": {"cleanMissionStatus": {"p2mapId": current_map}}}
            if current_map else {}
        )
        return PrimeRoomCleaning(data), robot

    @pytest.mark.asyncio
    async def test_room_names_come_back_mapped_to_ids(self):
        backend, _robot = self._backend()

        assert await backend.available_rooms() == {
            "Kitchen": "MAP-1/12", "Living room": "MAP-1/13",
        }

    @pytest.mark.asyncio
    async def test_the_command_carries_an_initiator(self):
        """THE field two field sessions were spent establishing. Without
        it the command is delivered, acknowledged with a PUBACK, and
        silently ignored — the most expensive failure mode available,
        because it looks like success."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12"])

        command = robot.send_routine_command_via_cmd_topic.await_args.args[0]
        assert command.to_json()["initiator"] == "rmtApp"

    @pytest.mark.asyncio
    async def test_the_command_uses_start_and_region_id(self):
        """The other half of the finding. `clean` and `id` were an
        assumption in this project's own code until real data settled
        it — a command using them was delivered and did nothing."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12", "13"])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert payload["command"] == "start"
        assert [r["region_id"] for r in payload["regions"]] == ["12", "13"]

    @pytest.mark.asyncio
    async def test_two_pass_is_sent_per_room(self):
        """The Classic path has always supported this per room, and the
        confirmed Prime field payload carries twoPass per region too. A
        first draft of the backend interface took only room ids, which
        would have quietly dropped a capability users already have."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12", "13"], two_pass=[True, False])

        regions = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["regions"]
        assert regions[0]["params"]["twoPass"] is True
        assert regions[1]["params"]["twoPass"] is False

    @pytest.mark.asyncio
    async def test_no_opinion_means_no_params_at_all(self):
        """Omitted rather than sent as False. Sending False would switch
        two-pass OFF for someone who deliberately turned it on -- the
        robot's own setting is the right default, not ours."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12"])

        regions = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["regions"]
        assert "params" not in regions[0]

    @pytest.mark.asyncio
    async def test_a_short_two_pass_list_leaves_the_rest_alone(self):
        """Callers pass what the user supplied, which may cover fewer
        rooms than were requested. Missing entries must not become
        False."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12", "13"], two_pass=[True])

        regions = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["regions"]
        assert regions[0]["params"]["twoPass"] is True
        assert "params" not in regions[1]

    @pytest.mark.asyncio
    async def test_suction_level_is_sent_per_room(self):
        """Prime-only: the confirmed field payload carries suctionLevel
        per region, and the Classic room payload has no equivalent.
        Leaving it out would have meant Prime users could not do
        something their own app can."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12", "13"], suction_level=[2, 1])

        regions = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["regions"]
        assert regions[0]["params"]["suctionLevel"] == 2
        assert regions[1]["params"]["suctionLevel"] == 1

    @pytest.mark.asyncio
    async def test_two_pass_and_suction_level_combine(self):
        """Both are per room and independent; setting one must not
        silently drop the other."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12"], two_pass=[True], suction_level=[2])

        params = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["regions"][0]["params"]
        assert params["twoPass"] is True
        assert params["suctionLevel"] == 2

    def test_operating_mode_is_offered_now_that_its_values_are_known(self):
        """IT USED TO BE DELIBERATELY OMITTED, and this test used to pin
        that. The reason was honest: "exposing a setting whose valid
        range we cannot determine invites a call that is silently
        rejected, or accepted and wrong."

        The range is determined now -- 2 vacuum, 4 mop, 32 both at once,
        512 vacuum then mop -- confirmed from schedules on two accounts,
        from the app's own operating_mode_defaults, and from a real
        robot's last start command. A user asked for the control
        (@arielgr) and the objection no longer applies.

        The test is kept, inverted: the reversal of a documented
        decision should be as visible as the decision was."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        assert "operating_mode" in inspect.signature(
            PrimeRoomCleaning.clean_rooms
        ).parameters

    @pytest.mark.asyncio
    async def test_ordered_is_passed_through(self):
        """Whether the robot follows the given sequence or picks its own
        route. clean_overdue_rooms sorts by urgency and means it."""
        backend, robot = self._backend()

        await backend.clean_rooms(["12"], ordered=False)

        assert robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["ordered"] == 0

    @pytest.mark.asyncio
    async def test_room_order_is_preserved(self):
        """clean_overdue_rooms and auto_clean_dirty_rooms sort by
        urgency; a backend that reorders silently discards that."""
        backend, robot = self._backend()

        await backend.clean_rooms(["13", "12"])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert [r["region_id"] for r in payload["regions"]] == ["13", "12"]

    @pytest.mark.asyncio
    async def test_a_single_map_needs_no_disambiguation(self):
        """Most accounts. With one map there is nothing to get wrong."""
        backend, robot = self._backend(maps=[{"p2map_id": "ONLY-MAP"}])

        await backend.clean_rooms(["12"])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert payload["p2map_id"] == "ONLY-MAP"

    @pytest.mark.asyncio
    async def test_the_robot_own_report_wins_over_list_order(self):
        """MULTI-FLOOR IS REAL: one tester's account holds "1st floor"
        and "2nd floor". The map list has no documented order, so
        picking the first entry is a coin flip performed on someone's
        floor. The robot says where it is; that is the answer."""
        backend, robot = self._backend(
            maps=[{"p2map_id": "FLOOR-1"}, {"p2map_id": "FLOOR-2"}],
            current_map="FLOOR-2",
        )

        await backend.clean_rooms(["12"])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert payload["p2map_id"] == "FLOOR-2"

    @pytest.mark.asyncio
    async def test_several_maps_and_no_current_one_refuses(self):
        """Parked, freshly booted, not yet relocalised -- there is no
        honest single answer. Guessing would clean the wrong floor,
        which is obvious to the user and baffling from the logs."""
        from homeassistant.exceptions import HomeAssistantError

        backend, _robot = self._backend(
            maps=[{"p2map_id": "FLOOR-1"}, {"p2map_id": "FLOOR-2"}]
        )

        with pytest.raises(HomeAssistantError, match="2 maps"):
            await backend.clean_rooms(["12"])

    @pytest.mark.asyncio
    async def test_rooms_are_collected_from_every_map(self):
        """A user asking for "Bedroom" should get a match whether or not
        the robot is parked on that floor. Restricting to the current
        map would make one automation work in the evening and fail in
        the morning."""
        backend, robot = self._backend(
            maps=[{"p2map_id": "FLOOR-1"}, {"p2map_id": "FLOOR-2"}]
        )
        first = MagicMock(rooms_metadata=[self._room("12", "Kitchen")])
        second = MagicMock(rooms_metadata=[self._room("20", "Bedroom")])
        robot.get_map_metadata = AsyncMock(side_effect=[first, second])

        assert await backend.available_rooms() == {
            "Kitchen": "FLOOR-1/12", "Bedroom": "FLOOR-2/20",
        }

    @pytest.mark.asyncio
    async def test_map_versions_are_read_as_dicts_not_objects(self):
        """get_active_map_versions() returns plain dicts. Reading them
        with getattr() returns None silently — a mistake made three
        times across these two codebases."""
        backend, _robot = self._backend(maps=[{"p2map_id": "MAP-9"}])

        assert await backend.available_rooms()

    @pytest.mark.asyncio
    async def test_no_map_yields_no_rooms_rather_than_an_error(self):
        """A robot that has not finished mapping yet is a normal state,
        not a fault."""
        backend, _robot = self._backend(maps=[])

        assert await backend.available_rooms() == {}

    @pytest.mark.asyncio
    async def test_cleaning_with_no_maps_at_all_fails_clearly(self):
        """Here an error IS right: the user asked for something specific
        that cannot be done, and silence would look like success."""
        from homeassistant.exceptions import HomeAssistantError

        backend, _robot = self._backend(maps=[])

        with pytest.raises(HomeAssistantError, match="no saved maps"):
            await backend.clean_rooms(["12"])

    @pytest.mark.asyncio
    async def test_a_failing_metadata_call_yields_no_rooms(self):
        """Enrichment, not a hard dependency — a cloud hiccup should not
        take down whatever asked for the room list."""
        backend, robot = self._backend()
        robot.get_map_metadata = AsyncMock(side_effect=RuntimeError("boom"))

        assert await backend.available_rooms() == {}

    @pytest.mark.asyncio
    async def test_rooms_without_a_name_are_skipped(self):
        """An unnamed room cannot be matched against user input, so
        offering it would only produce confusing failures."""
        backend, _robot = self._backend(
            rooms=[self._room("12", "Kitchen"), self._room("13", None)]
        )

        assert await backend.available_rooms() == {"Kitchen": "MAP-1/12"}


class TestRoomNameMatching:
    """Matching typed names against a robot's actual rooms.

    Extracted from services.py::_resolve_rooms rather than reused: 51 of
    its 141 lines deal with pmap ids and cross-map conflicts, and it
    returns (region_id, pmap_id) tuples that mean nothing to a Prime
    robot. The name matching itself is generation-independent, so only
    that part moved.

    The room names below are real ones from field testers' robots."""

    _ROOMS = {"Küche": "12", "Salle à manger": "10", "Salon": "13", "Cellier": "11"}

    def _match(self, requested):
        from custom_components.roomba_plus.room_cleaning import match_room_names

        return match_room_names(self._ROOMS, requested)

    def test_an_exact_name_matches(self):
        assert self._match(["Salon"]) == (["13"], [])


    def test_a_unique_bare_prime_region_id_matches(self):
        """What the map card sends. The Rooms Map publishes each room's
        own id, and xiaomi-vacuum-map-card sends it back on a tap —
        matching only on name and slug meant the card could draw rooms
        and not clean them. @jouwdan (#88)."""
        from custom_components.roomba_plus.room_cleaning import match_room_names

        assert match_room_names({"Study": "MAP-1/16"}, ["16"]) == (["MAP-1/16"], [])

    def test_an_ambiguous_bare_region_id_is_rejected(self):
        """A household with several maps can hold the same bare id twice.
        Picking one would clean a room on the wrong floor, so this
        reports an honest miss — the same rule the name matching
        follows."""
        from custom_components.roomba_plus.room_cleaning import match_room_names

        rooms = {"Downstairs": "MAP-1/16", "Upstairs": "MAP-2/16"}
        assert match_room_names(rooms, ["16"]) == ([], ["16"])

    def test_case_does_not_matter(self):
        """Automations and voice assistants are inconsistent about it."""
        assert self._match(["salon"]) == (["13"], [])
        assert self._match(["SALON"]) == (["13"], [])

    def test_accents_can_be_omitted(self):
        """THE reason this logic is worth sharing. Accents are awkward on
        phone keyboards and absent from voice assistants entirely --
        without the slug fallback a German or French user's automation
        fails with 'unknown room' for a room that plainly exists."""
        assert self._match(["kuche"]) == (["12"], [])
        assert self._match(["salle a manger"]) == (["10"], [])
        assert self._match(["salle_a_manger"]) == (["10"], [])

    def test_request_order_is_preserved(self):
        """clean_overdue_rooms sorts by urgency; reordering here would
        silently discard that."""
        assert self._match(["Salon", "Küche", "Cellier"]) == (["13", "12", "11"], [])

    def test_an_unknown_room_is_reported_not_guessed(self):
        """No fuzzy scoring, deliberately: cleaning the wrong room is
        worse than an honest miss, and the user can correct a miss."""
        assert self._match(["Bathroom"]) == ([], ["Bathroom"])

    def test_partial_matches_are_misses(self):
        """"Sal" could be Salon or Salle à manger. Picking one would be
        a coin flip performed on someone's floor."""
        assert self._match(["Sal"]) == ([], ["Sal"])

    def test_a_mixed_request_returns_both_halves(self):
        """The caller needs to know what it can do AND what it cannot,
        so it can act on one and report the other."""
        matched, unmatched = self._match(["Salon", "Bathroom", "kuche"])

        assert matched == ["13", "12"]
        assert unmatched == ["Bathroom"]

    def test_surrounding_whitespace_is_ignored(self):
        """Copy-paste from the iRobot app brings it along."""
        assert self._match(["  Salon  "]) == (["13"], [])

    def test_a_room_requested_twice_is_cleaned_once(self):
        """Sending the same region twice is at best wasted battery."""
        assert self._match(["Salon", "salon"]) == (["13"], [])

    def test_no_rooms_available_means_everything_is_unmatched(self):
        """A robot still mapping has no rooms yet -- the request is not
        wrong, it is just unanswerable right now."""
        from custom_components.roomba_plus.room_cleaning import match_room_names

        assert match_room_names({}, ["Salon"]) == ([], ["Salon"])


class TestCleanRoomUsesTheBackend:
    """The service actually calls the backend it asked for.

    Worth its own tests because the half-finished state was WORSE than
    the original: the gate had been replaced, so a Prime robot passed
    the check, and then the send path still called data.roomba, which is
    None for every Prime entry. The user got through the door and
    crashed behind it.

    The whole suite stayed green throughout, because no test drove a
    Prime robot through this service. That is the same pattern the
    version plan names as the cause of four silent bugs: written,
    unit-tested, never wired up."""

    def _call(self, rooms, *, two_pass=None, ordered=True):
        call = MagicMock()
        # `call.hass` launches the swallow-watch background task, so it
        # needs the helper rather than a bare mock.
        call.hass = hass_mock()
        call.data = {"room_name": rooms}
        if two_pass is not None:
            call.data["two_pass"] = two_pass
        return call

    @pytest.mark.asyncio
    async def test_the_backend_receives_the_resolved_ids(self):
        from custom_components.roomba_plus.services import (
            _async_clean_rooms_via_backend,
        )

        backend = MagicMock()

        # The swallow-watch task is launched through
        # `backend._config_entry.async_create_background_task`, so that
        # entry needs the helper too.
        backend._config_entry = entry_mock()
        backend.available_rooms = AsyncMock(return_value={"Küche": "12", "Salon": "13"})
        backend.clean_rooms = AsyncMock()

        await _async_clean_rooms_via_backend(
            backend, "vacuum.test", ["Salon"], True, [], self._call(["Salon"])
        )

        assert backend.clean_rooms.await_args.args[0] == ["13"]

    @pytest.mark.asyncio
    async def test_an_unknown_room_is_refused_before_anything_moves(self):
        """Better a clear error than cleaning some rooms and silently
        skipping others -- the user would not know which."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus.services import (
            _async_clean_rooms_via_backend,
        )

        backend = MagicMock()

        backend._config_entry = entry_mock()
        backend.available_rooms = AsyncMock(return_value={"Küche": "12"})
        backend.clean_rooms = AsyncMock()

        with pytest.raises(ServiceValidationError, match="Bathroom"):
            await _async_clean_rooms_via_backend(
                backend, "vacuum.test", ["Bathroom"], True, [], self._call(["Bathroom"])
            )

        backend.clean_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_error_lists_the_rooms_that_do_exist(self):
        """A user who mistyped needs to see the options, not just be
        told they were wrong."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus.services import (
            _async_clean_rooms_via_backend,
        )

        backend = MagicMock()

        backend._config_entry = entry_mock()
        backend.available_rooms = AsyncMock(return_value={"Küche": "12", "Salon": "13"})

        with pytest.raises(ServiceValidationError, match="Küche"):
            await _async_clean_rooms_via_backend(
                backend, "vacuum.test", ["Nope"], True, [], self._call(["Nope"])
            )

    @pytest.mark.asyncio
    async def test_an_explicit_per_room_false_is_not_overridden(self):
        """A first draft used `or` here, so `False or True` silently
        turned a user's explicit "no second pass on this room" into
        yes. This project has repeatedly hit absent-treated-as-false;
        this is the mirror image and just as wrong."""
        from custom_components.roomba_plus.services import (
            _async_clean_rooms_via_backend,
        )

        backend = MagicMock()

        backend._config_entry = entry_mock()
        backend.available_rooms = AsyncMock(return_value={"A": "1", "B": "2"})
        backend.clean_rooms = AsyncMock()

        await _async_clean_rooms_via_backend(
            backend, "vacuum.test", ["A", "B"], True, [False, None],
            self._call(["A", "B"], two_pass=True),
        )

        assert backend.clean_rooms.await_args.kwargs["two_pass"] == [False, True]

    @pytest.mark.asyncio
    async def test_no_rooms_at_all_is_a_clear_error(self):
        """A robot still mapping, or one whose rooms were never named."""
        from homeassistant.exceptions import ServiceValidationError

        from custom_components.roomba_plus.services import (
            _async_clean_rooms_via_backend,
        )

        backend = MagicMock()

        backend._config_entry = entry_mock()
        backend.available_rooms = AsyncMock(return_value={})

        with pytest.raises(ServiceValidationError, match="No rooms with names"):
            await _async_clean_rooms_via_backend(
                backend, "vacuum.test", ["Salon"], True, [], self._call(["Salon"])
            )


class TestPrimeSegmentsForHomeAssistantAreaMapping:
    """Prime's own segment id format, now agreed inside one class.

    Producer and consumer share a prefix that nothing outside these two
    methods would notice drifting apart. They lived in vacuum.py while
    the Classic pair lived here, which split one contract across two
    files -- and briefly across two files in opposite directions, after
    the Classic half moved and the Prime half did not.

    The original bug this covers: a11 advertised CLEAN_AREA to Home
    Assistant and returned no segments for Prime, so the "map segments
    to areas" dialog opened empty. A capability that appears present and
    does nothing is worse than none -- both testers who hit it spent
    their time looking for their own mistake."""

    def _backend(self, rooms):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        backend = PrimeRoomCleaning(MagicMock(blid="BLID"))
        backend.available_rooms = AsyncMock(return_value=rooms)
        backend.clean_rooms = AsyncMock()
        return backend

    @pytest.mark.asyncio
    async def test_room_ids_round_trip_through_the_segment_format(self):
        """The property that matters: whatever get_segments emits,
        clean_segments must decode back to the same room id. If the two
        ever disagree the dialog works and cleaning silently does
        nothing."""
        backend = self._backend({"Salon": "13"})

        await backend.clean_segments([f"{backend._SEGMENT_PREFIX}13"])

        assert backend.clean_rooms.await_args.args[0] == ["13"]

    @pytest.mark.asyncio
    async def test_unrecognised_segment_ids_raise_rather_than_clean_nothing(self):
        """A stale area mapping after a retrain, or segments from a
        different vacuum. Cleaning nothing quietly would look like
        success."""
        from homeassistant.exceptions import ServiceValidationError

        backend = self._backend({"Salon": "13"})

        with pytest.raises(ServiceValidationError):
            await backend.clean_segments(["something_else"])

        backend.clean_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_segments_are_built_from_the_room_list(self):
        """HA's Segment class arrives in 2026.3 and this environment
        predates it, so get_segments() returns [] here whatever the
        data. What is testable is that it consults the backend at all --
        the original bug was that Prime never got that far."""
        backend = self._backend({"Salon": "13", "Cuisine": "12"})

        await backend.get_segments()

        assert backend.available_rooms.await_count == 1

    def test_both_sides_share_one_prefix_constant(self):
        """Written as a shared constant rather than two literals, so
        producer and consumer cannot drift by a typo."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        produce = inspect.getsource(PrimeRoomCleaning.get_segments)
        consume = inspect.getsource(PrimeRoomCleaning.clean_segments)

        assert "_SEGMENT_PREFIX" in produce
        assert "_SEGMENT_PREFIX" in consume

class TestConsumablePartNaming:
    """Part names come from a table; UNITS come from the server.

    The API identifies consumables by number. A sensor called
    "Consumable - 67" is accurate and useless, so ids map to
    translation keys -- and to keys rather than to a {part} placeholder,
    because a placeholder cannot be translated: "Consommable - Edge
    sweeping brush" looks like a translation that failed halfway.

    UNITS ARE DELIBERATELY NOT IN THAT TABLE. An earlier version put
    them there, inferred by comparing sensor values against app
    screenshots -- 5100 against "85 heures restantes", and so on. The
    inference was correct and hardcoding it was still wrong: a
    diagnostics download then showed the server states count_type per
    part outright. A hardcoded unit disagrees silently the moment a
    robot reports something else."""

    _PARTS = {
        "67": "prime_part_edge_brush",
        "71": "prime_part_multi_surface_brush",
        "72": "prime_part_filter",
        "147": "prime_part_dirt_bag",
        "148": "prime_part_mop_pads",
        # 213 added after @arielgr put the app's maintenance list beside
        # Home Assistant's: four values agreed in sequence -- 14
        # routines, 19, 92 hr, 300 hr -- so the one showing 19 is Cliff
        # Sensors.
        #
        # That method needs two screenshots and settles an id outright.
        # It is how 202 and 212 should be named, if anyone ever finds
        # them in the app at all.
        "213": "prime_part_cliff_sensors",
        # @utkjmitch, by the same method: the app's robot-health screen
        # beside the ids. "Washable mop pad, 8 routines" against 149,
        # "Multi-surface brush, 179 hrs" against 69. 68 is by
        # elimination -- the only remaining part on a robot the app
        # warned needed a new filter -- and he called it the weakest of
        # the three himself.
        #
        # THE SAME THREE PARTS AS 148 / 71 / 72 ABOVE, differently
        # numbered. The part id space is per-SKU, so both sets belong
        # here rather than one replacing the other.
        "68": "prime_part_filter",
        "69": "prime_part_multi_surface_brush",
        "149": "prime_part_mop_pads",
        "202": "prime_part_pad_wash_cleaning",
        "212": "prime_part_pad_wash_replacement",
    }

    def test_the_named_parts_match_the_field_reports(self):
        """SCOPED TO THE NUMERIC IDS, which is what this table records.

        It used to assert equality against the whole of `_KNOWN_PARTS`,
        which was right while that dict held nothing else. App 3.0.0
        added a second vocabulary of speaking `part_id` values
        (`main_brush`, `bag`, `sensor` …), and mapping those broke this
        assertion.

        Narrowed rather than updated: the field reports behind these
        seven are what the test is for, and folding the aliases in would
        mean re-asserting a mapping that has its own test."""
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        numeric = {k: v for k, v in _KNOWN_PARTS.items() if k.isdigit()}

        assert numeric == self._PARTS

    def test_no_numeric_id_was_lost_when_the_names_arrived(self):
        """The failure mode a narrowed assertion could hide: an alias
        overwriting a numeric entry, or one quietly disappearing."""
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        assert set(self._PARTS) <= set(_KNOWN_PARTS)

    def test_unidentified_parts_stay_numeric(self):
        """202 and 212 both report count_type "pad_washes_used" and
        differ only by category. Two testers looked for them in the app
        and neither found either one -- so they keep their numbers. A
        made-up label gets believed; a bare number invites a question."""
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        # 202 and 212 ARE named now -- but for the counter and the
        # action, never for the physical part.
        #
        # The app names two candidate dock components, a "pad washing
        # roller" and a "pad washing basin", and the data does not say
        # which is which: one part with two thresholds and two parts with
        # one each fit equally well. Calling 202 "roller cleaning" would
        # send someone to scrub the wrong component.
        #
        # What IS proven, on two accounts: counter `pad_washes_used`,
        # thresholds 50 and 300, categories maintenance and replacement.
        for key in ("prime_part_pad_wash_cleaning", "prime_part_pad_wash_replacement"):
            assert key in _KNOWN_PARTS.values()

        import json
        from pathlib import Path as _Path

        base = (
            _Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        english = json.loads(
            (base / "translations" / "en.json").read_text(encoding="utf-8")
        )["entity"]["sensor"]

        for key in ("prime_part_pad_wash_cleaning", "prime_part_pad_wash_replacement"):
            label = english[key]["name"].lower()
            assert "roller" not in label, label
            assert "basin" not in label, label

    def test_every_named_part_is_translated_in_every_locale(self):
        """A translation_key with no entry renders as the raw key."""
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        for locale_file in sorted((base / "translations").glob("*.json")):
            sensors = json.loads(locale_file.read_text(encoding="utf-8"))["entity"]["sensor"]
            missing = [k for k in self._PARTS.values() if k not in sensors]
            assert not missing, f"{locale_file.name}: {missing}"

    def test_names_follow_the_maintenance_prefix_used_by_classic(self):
        """Classic names every consumable "Maintenance - <part>". A
        second vocabulary for the same concept, sitting in the same
        entity list, reads as two integrations rather than one."""
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        sensors = json.loads(
            (base / "translations" / "en.json").read_text(encoding="utf-8")
        )["entity"]["sensor"]

        classic_prefix = sensors["brush_remaining_hours"]["name"].split("–")[0].strip()
        for key in self._PARTS.values():
            assert sensors[key]["name"].startswith(classic_prefix), key

    def test_minutes_are_shown_as_hours(self):
        """The app displays hours; the wire carries minutes. 5100
        unitless beside an app saying "85 heures" is a wrong number, not
        a labelling quibble."""
        from custom_components.roomba_plus.sensor_prime import _PART_COUNT_UNITS

        assert "minutes" in _PART_COUNT_UNITS

    def test_every_count_type_seen_in_the_field_has_a_unit(self):
        """Verbatim from a real diagnostics download. A missing entry
        means a number with no unit at all."""
        from custom_components.roomba_plus.sensor_prime import _PART_COUNT_UNITS

        for count_type in ("minutes", "evacs", "combo_missions", "pad_washes_used"):
            assert _PART_COUNT_UNITS.get(count_type), count_type

    def test_both_spellings_of_pad_washes_resolve(self):
        """`asset_health_enum` in app 3.0.0 lists `padWashesUsed` AND
        `pad_washes_used` side by side. Only the snake_case form was
        here, so a robot reporting the other showed a bare number.

        Same shape as the `reusablewet`/`reusableWet` bug: one vendor,
        two spellings, and the capture we happened to have carried only
        one of them."""
        from custom_components.roomba_plus.sensor_prime import _PART_COUNT_UNITS

        assert _PART_COUNT_UNITS["padWashesUsed"] == _PART_COUNT_UNITS["pad_washes_used"]


class TestClassicRoomDataDetection:
    """Decides whether a Classic robot is offered room cleaning at all,
    and took three attempts to get right.

    Draft one required `has_cloud` -- too strict. Room names live in
    config_entry.options; the cloud coordinator only ENRICHES them, so
    that locked out every Classic user without credentials. Six existing
    service tests caught it.

    Draft two accepted any SMART robot -- too loose. A smart map with no
    named rooms anywhere has nothing to target, so the service would be
    offered and could only fail. A vacuum test caught that one.

    Neither draft was caught by a test of this function, because it had
    none. Written after a bug hunt found three new functions with zero
    test references between them."""

    def _check(self, *, zone_data=None, coordinator_data=None, has_coordinator=True):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.room_cleaning import _classic_has_room_data

        data = MagicMock(
            cloud_coordinator=MagicMock(data=coordinator_data) if has_coordinator else None
        )
        entry = MagicMock(options={"smart_zone_data": zone_data} if zone_data else {})
        return _classic_has_room_data(data, entry)

    def test_stored_zone_data_alone_is_enough(self):
        """The case draft one broke: a user who named their rooms and
        runs without cloud credentials."""
        assert self._check(zone_data={"1": {"name": "Kitchen"}}, has_coordinator=False)

    def test_cloud_regions_alone_are_enough(self):
        """A user who has never opened the options but whose coordinator
        knows the regions."""
        assert self._check(coordinator_data={"anything": 1})

    def test_neither_source_means_no_room_cleaning(self):
        """The case draft two broke: offering a feature whose every call
        would fail."""
        assert not self._check(has_coordinator=False)

    def test_empty_zone_data_does_not_count(self):
        """The options key exists on every entry once anything has been
        configured. Its presence says nothing; its contents do."""
        assert not self._check(zone_data={}, has_coordinator=False)

    def test_a_coordinator_that_has_not_fetched_yet_does_not_count(self):
        """Early in startup. Answering yes here would offer room
        cleaning for a few seconds and then withdraw it."""
        assert not self._check(coordinator_data=None)


class TestClassicBackendRequiresTheRoomListFirst:
    """`clean_rooms` depends on state that `available_rooms` populates.

    Classic needs each region's pmap_id alongside its id, and that is
    recorded while reading the room list. A backend instance is created
    fresh per request, so calling clean_rooms without having read the
    rooms leaves the index empty.

    This is a genuine ordering dependency between two methods of the
    same object -- exactly the shape that has bitten this project
    before, where step B silently used something step A was supposed to
    have prepared. The difference here is that it fails loudly.

    Worth stating in a test because the interface does not express it:
    nothing in the signature says available_rooms must come first."""

    def _backend(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.room_cleaning import ClassicRoomCleaning

        # The client comes off `data`, not from a positional argument --
        # the constructor takes (data, config_entry, hass). Its
        # `send_command` is awaited since roombapy 2.x, so a plain
        # MagicMock fails on the await before the test reaches what it
        # is checking.
        data = MagicMock()
        data.roomba = robot_mock()
        return ClassicRoomCleaning(data, MagicMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_cleaning_without_reading_rooms_first_raises(self):
        """Refusing beats sending. An empty pmap_id produces a command
        the robot accepts and ignores -- the most expensive failure
        available, because it looks like success."""
        from homeassistant.exceptions import HomeAssistantError

        with pytest.raises(HomeAssistantError, match="not been read yet"):
            await self._backend().clean_rooms(["12"])

    @pytest.mark.asyncio
    async def test_reading_rooms_first_makes_cleaning_possible(self):
        """The normal path: both service callers read the room list to
        match names before they can have any ids to clean."""
        from unittest.mock import AsyncMock, MagicMock, patch

        backend = self._backend()
        backend._data.cloud_coordinator = MagicMock(
            data={"x": 1},
            regions=[{"id": "12", "name": "Kitchen", "pmap_id": "MAP-1"}],
            zones=[],
        )
        backend._config_entry.options = {}
        backend._data.roomba_reported_state = MagicMock(return_value={})
        backend._data.has_cloud = True
        backend._hass.async_add_executor_job = AsyncMock()

        rooms = await backend.available_rooms()
        assert rooms == {"Kitchen": "12"}

        with patch.object(backend, "_raise_if_map_updating"):
            await backend.clean_rooms(["12"])

        # The command reaches the robot -- which is the point of the
        # test, and no longer travels through the executor.
        assert backend._data.roomba.send_command.await_count == 1


class TestPrimeMapConsistency:
    """Room ids and the map to clean against come from two independent
    sources, and nothing checked them against each other.

    available_rooms() reads ids per map, from get_map_metadata(). The
    map to send against comes from the robot's own report of where it
    is. Both are correct individually; together they can disagree.

    Found by feeding deliberately inconsistent data rather than by
    reading code -- the pattern-based passes over this file had all come
    back clean."""

    def _backend(self, *, maps, current):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        data = MagicMock(blid="BLID")
        data.prime_robot.get_active_map_versions = AsyncMock(return_value=maps)
        room = MagicMock(room_id="1")
        room.name = "Kitchen"
        data.prime_robot.get_map_metadata = AsyncMock(
            return_value=MagicMock(rooms_metadata=[room])
        )
        data.prime_robot.send_routine_command_via_cmd_topic = AsyncMock()
        data.prime_status_coordinator.data = {
            "ro-currentstate": {"cleanMissionStatus": {"p2mapId": current}}
        }
        return PrimeRoomCleaning(data), data.prime_robot

    def _sent_map(self, robot):
        return robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()["p2map_id"]

    @pytest.mark.asyncio
    async def test_a_map_the_robot_reports_but_does_not_own_is_ignored(self):
        """Carried to another floor, or a map deleted in the app while
        Home Assistant was running. With one map left, its ids are the
        only ids there are, so falling back to it is safe."""
        backend, robot = self._backend(
            maps=[{"p2map_id": "MAP-A"}], current="MAP-GONE"
        )

        await backend.clean_rooms(["1"])

        assert self._sent_map(robot) == "MAP-A"

    @pytest.mark.asyncio
    async def test_an_inconsistent_report_with_several_maps_refuses(self):
        """Here there is no safe fallback: the ids could belong to
        either map, and guessing cleans the wrong floor."""
        from homeassistant.exceptions import HomeAssistantError

        backend, _robot = self._backend(
            maps=[{"p2map_id": "MAP-A"}, {"p2map_id": "MAP-B"}], current="MAP-GONE"
        )

        with pytest.raises(HomeAssistantError, match="2 maps"):
            await backend.clean_rooms(["1"])

    @pytest.mark.asyncio
    async def test_a_consistent_report_is_used_as_before(self):
        """The check must not break the normal multi-floor case it was
        added to protect."""
        backend, robot = self._backend(
            maps=[{"p2map_id": "MAP-A"}, {"p2map_id": "MAP-B"}], current="MAP-B"
        )

        await backend.clean_rooms(["1"])

        assert self._sent_map(robot) == "MAP-B"


class TestRoomIdsAreQualifiedByTheirMap:
    """Room ids are per-map, so two floors both have a room "1".

    Returning bare ids meant "Kitchen" downstairs and "Bedroom"
    upstairs could both resolve to "1", and cleaning targeted whichever
    map the robot happened to be on. Picking Kitchen while the robot
    was upstairs cleaned the bedroom -- silently, because the id was
    perfectly valid on that map too.

    Two testers have multi-floor accounts, so this was reachable rather
    than theoretical.

    Found by feeding two maps with colliding ids. Every pattern-based
    pass over this file had come back clean, because both halves are
    individually correct: listing rooms per map is right, and sending
    against the robot's current map is right. Only together are they
    wrong."""

    def _backend(self, rooms_by_map, maps, current=None):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        def _room(rid, name):
            room = MagicMock(room_id=rid)
            room.name = name
            return room

        data = MagicMock(blid="BLID")
        data.prime_robot.get_active_map_versions = AsyncMock(return_value=maps)

        async def _meta(map_id):
            return MagicMock(
                rooms_metadata=[_room(r, n) for r, n in rooms_by_map.get(map_id, [])]
            )

        data.prime_robot.get_map_metadata = AsyncMock(side_effect=_meta)
        data.prime_robot.send_routine_command_via_cmd_topic = AsyncMock()
        data.prime_status_coordinator.data = (
            {"ro-currentstate": {"cleanMissionStatus": {"p2mapId": current}}}
            if current else {}
        )
        return PrimeRoomCleaning(data), data.prime_robot

    _TWO_FLOORS = {"M1": [("1", "Kitchen")], "M2": [("1", "Bedroom")]}
    _MAPS = [{"p2map_id": "M1"}, {"p2map_id": "M2"}]

    @pytest.mark.asyncio
    async def test_colliding_ids_stay_distinguishable(self):
        """THE bug. Both rooms are id "1" on their own map."""
        backend, _robot = self._backend(self._TWO_FLOORS, self._MAPS, current="M2")

        rooms = await backend.available_rooms()

        assert rooms["Kitchen"] != rooms["Bedroom"]

    @pytest.mark.asyncio
    async def test_the_map_comes_from_the_room_not_from_the_robot(self):
        """Choosing a downstairs room while the robot is upstairs must
        clean downstairs. The id says which map; where the robot stands
        does not enter into it."""
        backend, robot = self._backend(self._TWO_FLOORS, self._MAPS, current="M2")

        rooms = await backend.available_rooms()
        await backend.clean_rooms([rooms["Kitchen"]])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert payload["p2map_id"] == "M1"
        assert payload["regions"][0]["region_id"] == "1"

    @pytest.mark.asyncio
    async def test_rooms_from_two_maps_in_one_call_are_refused(self):
        """One command targets one map. Cleaning half the request and
        silently dropping the rest would be worse than saying so."""
        from homeassistant.exceptions import HomeAssistantError

        backend, _robot = self._backend(self._TWO_FLOORS, self._MAPS, current="M1")

        rooms = await backend.available_rooms()

        with pytest.raises(HomeAssistantError, match="different maps"):
            await backend.clean_rooms([rooms["Kitchen"], rooms["Bedroom"]])

    @pytest.mark.asyncio
    async def test_a_single_map_still_works_normally(self):
        """The common case must not pay for the multi-floor fix."""
        backend, robot = self._backend(
            {"ONLY": [("7", "Salon")]}, [{"p2map_id": "ONLY"}]
        )

        rooms = await backend.available_rooms()
        await backend.clean_rooms([rooms["Salon"]])

        payload = robot.send_routine_command_via_cmd_topic.await_args.args[0].to_json()
        assert payload["p2map_id"] == "ONLY"
        assert payload["regions"][0]["region_id"] == "7"


class TestTheMissionPlanReachesTheTimerStore:
    """The link that was missing after the timer store was wired.

    MissionTimerStore was created for Prime and fed phase transitions,
    but nothing called set_mission_plan() -- only the Classic MQTT
    callback path does. Four things silently depended on it:

      - the advance_room service, whose planned_rooms was always empty,
        so it did nothing at all
      - current_room and next_room, both blank
      - the mission progress sensor, showing elapsed time and no
        remaining estimate

    Half working and looking whole, which is the shape this project keeps
    producing: a store created, a store filled, a store read -- each step
    tested fine on its own."""

    def _backend(self):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.mission_timer_store import MissionTimerStore
        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        data = MagicMock(blid="BLID")
        data.prime_robot.get_active_map_versions = AsyncMock(
            return_value=[{"p2map_id": "M1"}]
        )
        room = MagicMock(room_id="13")
        room.name = "Salon"
        data.prime_robot.get_map_metadata = AsyncMock(
            return_value=MagicMock(rooms_metadata=[room])
        )
        data.prime_robot.send_routine_command_via_cmd_topic = AsyncMock()
        data.prime_status_coordinator.data = {
            "ro-currentstate": {"cleanMissionStatus": {"p2mapId": "M1"}}
        }
        data.mission_timer_store = MissionTimerStore()
        data.hass_ref = MagicMock()
        entry = MagicMock(entry_id="e1")
        return PrimeRoomCleaning(data, entry, MagicMock()), data.mission_timer_store

    @pytest.mark.asyncio
    async def test_cleaning_records_the_planned_rooms(self):
        from unittest.mock import patch

        backend, store = self._backend()

        with patch.object(backend, "_raise_if_map_updating"):
            await backend.clean_rooms(["M1/13"])

        assert store.planned_rooms == ["13"]

    @pytest.mark.asyncio
    async def test_the_current_room_becomes_the_first_one(self):
        """Which is what makes advance_room able to move to a second."""
        from unittest.mock import patch

        backend, store = self._backend()

        with patch.object(backend, "_raise_if_map_updating"):
            await backend.clean_rooms(["M1/13"])

        assert store.current_room == "13"

    @pytest.mark.asyncio
    async def test_no_timer_store_does_not_break_cleaning(self):
        """Recording the plan is enrichment. A Prime entry whose timer
        store failed to load must still be able to clean."""
        from unittest.mock import patch

        backend, _store = self._backend()
        backend._data.mission_timer_store = None

        with patch.object(backend, "_raise_if_map_updating"):
            await backend.clean_rooms(["M1/13"])

        backend._robot.send_routine_command_via_cmd_topic.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_read_only_backend_needs_no_context(self):
        """Several call sites build this backend just to list rooms.
        Requiring hass and the config entry would have broken them, which
        is why both are optional."""
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        data = MagicMock(blid="B")
        data.prime_robot.get_active_map_versions = AsyncMock(return_value=[])

        backend = PrimeRoomCleaning(data)

        assert await backend.available_rooms() == {}

    def test_no_time_estimates_are_invented(self):
        """get_time_estimates() would supply them, and its own docstring
        says the request body's key names are unconfirmed. A progress
        sensor that counts rooms without predicting minutes is honest;
        guessing at the body would produce numbers with nothing behind
        them."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        source = inspect.getsource(PrimeRoomCleaning._note_mission_plan)
        # Strip the docstring: it MENTIONS get_time_estimates to explain
        # why it is not used, and asserting on the whole source would
        # therefore fail for the wrong reason. A first version of this
        # test did exactly that.
        code = source.split('"""')[-1]

        assert "get_time_estimates" not in code
        assert "set_mission_plan" in code


class TestTheFactoryNeverReadsHassOffTheConfigEntry:
    """`ConfigEntry` has no `hass` attribute.

    A version of this factory read `config_entry.hass` as a fallback when
    the caller passed no hass. That raises AttributeError on a real
    config entry -- and the factory is called from `supported_features`,
    a property Home Assistant evaluates while registering the entity.

    An exception there means the entity is never added. The vacuum
    entity, the one that shows the robot and starts a clean, vanished for
    a tester on v4.0.0a14 for exactly that reason.

    WHY 4,577 TESTS MISSED IT. Every test passes a MagicMock as the
    config entry, and a MagicMock answers any attribute access with
    another MagicMock. The suite was green against code that raises on
    the first real ConfigEntry it meets.

    That is the general lesson, not a detail: a MagicMock cannot fail an
    attribute the real object does not have."""

    def test_config_entry_really_has_no_hass_attribute(self):
        """The premise. If HA ever adds one, this test says so and the
        rest of this class can be reconsidered."""
        from homeassistant.config_entries import ConfigEntry

        assert not hasattr(ConfigEntry, "hass")

    def test_every_other_config_entry_attribute_this_code_uses_is_real(self):
        """The general form of the bug, checked rather than assumed.

        `config_entry.hass` was the one that bit. This asserts there is
        no second one waiting: every attribute the integration reads off
        a config entry has to be declared on ConfigEntry.

        Worth doing because a MagicMock answers anything, so the test
        suite cannot distinguish a real attribute from an invented one.
        Every access below was green before `hass` was found, and would
        have stayed green with five more like it."""
        import ast
        import inspect
        import re
        from pathlib import Path

        from homeassistant.config_entries import ConfigEntry

        source = inspect.getsource(ConfigEntry)
        declared = set(re.findall(r"^\s{4}(\w+):\s", source, re.M))
        declared |= set(re.findall(r"_setter\(self, [\"'](\w+)[\"']", source))
        declared |= {a for a in dir(ConfigEntry) if not a.startswith("__")}

        root = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        used: set[str] = set()
        for path in root.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Attribute):
                    continue
                value = node.value
                if isinstance(value, ast.Name) and value.id == "config_entry":
                    used.add(node.attr)
                elif isinstance(value, ast.Attribute) and value.attr == "_config_entry":
                    used.add(node.attr)

        unknown = sorted(used - declared)

        assert not unknown, (
            f"read off a config entry but not declared on ConfigEntry: {unknown}"
        )

    def test_the_factory_does_not_reach_for_it(self):
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            async_get_room_cleaning_backend,
        )

        source = inspect.getsource(async_get_room_cleaning_backend)
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )

        assert "config_entry.hass" not in code

    def test_no_module_reads_hass_off_a_config_entry(self):
        """Broader than the factory: the same mistake anywhere else fails
        the same way, and there is no reason to allow it."""
        import ast
        import re
        from pathlib import Path

        root = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus"
        )
        for path in root.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            # Strip docstrings as well as comments: several of them
            # explain WHY this attribute must not be used, and a naive
            # text search flags the explanation as the offence.
            docstrings = {
                node.body[0].value.value
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            code = "\n".join(
                line
                for line in source.splitlines()
                if not line.strip().startswith("#")
            )
            for doc in docstrings:
                code = code.replace(doc, "")

            assert not re.search(r"config_entry\.hass\b", code), path.name

    def test_a_backend_without_hass_still_works(self):
        """hass is only needed to record the mission plan. Callers that
        have none -- supported_features among them -- must still get a
        working backend, or the capability check turns into a crash."""
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        data = MagicMock(blid="B")
        data.prime_robot.get_active_map_versions = AsyncMock(return_value=[])

        backend = PrimeRoomCleaning(data)

        assert backend is not None


class TestBothZoneKeysCount:
    """Two keys for the same thing, and the check read the wrong one.

    The naming flow writes `smart_zone_labels`. `smart_zone_data` is
    written by exactly one path — the rest980 migration, for people
    arriving from another integration. So a user who named their rooms
    through our own flow still failed this check, and the docstring
    saying "requiring cloud is too strict, room names live in options"
    described a route that did not exist for them.
    """

    def _has_rooms(self, options):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.room_cleaning import (
            _classic_has_room_data,
        )

        entry = entry_mock()
        entry.options = options
        data = SimpleNamespace(cloud_coordinator=None)
        return _classic_has_room_data(data, entry)

    def test_the_key_the_naming_flow_writes(self):
        assert self._has_rooms({"smart_zone_labels": {"11": "Kitchen"}}) is True

    def test_the_key_the_migration_writes(self):
        assert self._has_rooms({"smart_zone_data": {"11": {}}}) is True

    def test_neither_and_no_cloud_means_no(self):
        assert self._has_rooms({}) is False

    def test_an_empty_label_map_does_not_count(self):
        """An empty dict is what a config entry looks like before anyone
        has named anything -- it must not read as "rooms available"."""
        assert self._has_rooms({"smart_zone_labels": {}}) is False


class TestTheNamingFlowLooksBeyondSchedules:
    """It read only `cleanSchedule2`, so a robot whose owner has never
    built a schedule WITH ROOMS offered nothing to name -- the step
    opened and immediately reported itself finished (@connormxy). His
    robot knows its twelve rooms; they simply are not in a schedule.
    """

    def _source(self):
        import inspect

        from custom_components.roomba_plus import config_flow

        src = inspect.getsource(config_flow)
        i = src.index("THREE SOURCES, NOT ONE")
        return src[i:i + 1600]

    def test_the_last_command_is_read(self):
        assert 'state.get("lastCommand")' in self._source()

    def test_the_cloud_coordinator_is_read(self):
        assert 'getattr(coordinator, "regions", None)' in self._source()

    def test_schedules_are_still_read(self):
        assert 'state.get("cleanSchedule2", [])' in self._source()


class TestAnEmptyNamingStepSaysWhy:
    """It announced itself finished, which reads as "done" when it means
    "found nothing". The two cases are different problems."""

    def _source(self):
        import inspect

        from custom_components.roomba_plus import config_flow

        src = inspect.getsource(config_flow)
        i = src.index("SAY WHY, rather than reporting success")
        return src[i:i + 500]

    def test_nothing_found_and_all_named_are_different_reasons(self):
        source = self._source()

        assert "no_rooms_to_name" in source
        assert "all_rooms_named" in source

    def test_it_aborts_rather_than_claiming_success(self):
        assert "async_abort" in self._source()

    def test_both_reasons_are_translated(self):
        import json
        import pathlib

        for loc in ("de", "en", "fr"):
            d = json.loads(
                (pathlib.Path("custom_components/roomba_plus/translations")
                 / f"{loc}.json").read_text()
            )
            abort = d.get("options", {}).get("abort", {})
            assert "no_rooms_to_name" in abort, loc
            assert "all_rooms_named" in abort, loc


class TestAnEmptyMapIdIsRefusedRatherThanSent:
    """@Echovictor37 sent a region command with `map_id=None` on a Combo
    105: the broker returned a PUBACK **and the robot cleaned the whole
    house.** Not the requested room, and not nothing — accepted,
    effective, and not what was asked.

    Our Prime room clean fell through to `""` and sent it. A user asking
    for the kitchen would have got every room, with a success in the log.
    """

    def _source(self):
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        # The CLASSIC path is the one that fell through. Prime already
        # refused a missing map with a HomeAssistantError -- checked
        # before adding anything, after nearly building the guard twice.
        return inspect.getsource(ClassicRoomCleaning)

    def test_the_empty_case_is_rejected(self):
        source = self._source()

        assert "if not pmap_id:" in source
        assert "ServiceValidationError" in source

    def test_the_message_says_what_would_have_happened(self):
        """"Cannot be sent" is half an answer. Somebody who knows a
        whole-house clean was the alternative can decide whether to start
        one deliberately."""
        source = self._source()
        i = source.index("if not pmap_id:")

        assert "whole-house clean" in source[i:i + 700]

    def test_it_refuses_before_building_the_payload(self):
        """After would be too late: the point is that the payload is
        valid enough to be accepted."""
        source = self._source()

        assert source.index("if not pmap_id:") < source.index('"regions":')


class TestPrimeSendsZonesAsZid:
    """Zones are marked by a `zid_` prefix inside the same region-id
    list as rooms. Classic has split on it since v2.7.0; Prime
    hardcoded RID.

    Found while wiring the clean_zone service for @chairstacker, whose
    robot is Prime — the service would have looked correct and cleaned
    nothing, or the wrong region if a room happened to carry that
    number.
    """

    def test_a_prefixed_id_becomes_a_zid_region(self):
        import inspect

        from custom_components.roomba_plus import room_cleaning

        source = inspect.getsource(room_cleaning.PrimeRoomCleaning)

        assert "RegionType.ZID" in source
        assert "ZID_PREFIX" in source

    def test_the_prefix_is_stripped_before_sending(self):
        """The wire carries a bare `100`, not `zid_100` — the prefix is
        our marker, not the robot's."""
        import inspect

        from custom_components.roomba_plus import room_cleaning

        source = inspect.getsource(room_cleaning.PrimeRoomCleaning)

        assert "rid[len(ZID_PREFIX):]" in source

    def test_both_backends_share_one_constant(self):
        """A second literal is how the two would drift apart."""
        from custom_components.roomba_plus.room_cleaning import ZID_PREFIX

        assert ZID_PREFIX == "zid_"

    def test_an_unprefixed_id_is_still_a_room(self):
        import inspect

        from custom_components.roomba_plus import room_cleaning

        source = inspect.getsource(room_cleaning.PrimeRoomCleaning)

        assert "else RegionType.RID" in source


class TestTheMissionPlanGetsBareIds:
    """The mission timer matches planned regions against what the robot
    reports as it cleans, and the robot reports a bare `100`.

    A plan carrying our `zid_` marker would never match, so a zone
    mission would show every region as unvisited for its whole run —
    a failure that looks like the robot ignoring the command.
    """

    def test_the_prefix_is_stripped_for_the_plan(self):
        import inspect

        from custom_components.roomba_plus import room_cleaning

        source = inspect.getsource(room_cleaning.PrimeRoomCleaning)
        after_send = source.split("send_routine_command_via_cmd_topic")[1]

        assert "ZID_PREFIX" in after_send
        assert "_note_mission_plan" in after_send


class TestCleanZoneOnATwoMapRobot:
    """@chairstacker's clean_zone failed on a two-map robot with "not
    currently reporting which map", even mid-mission. _current_map_id
    reads `cleanMissionStatus.p2mapId`, which his robot did not fill --
    but a zone id belongs to exactly one map, and that map was already
    recorded. The command should derive the map from the zone rather
    than refuse.
    """

    @staticmethod
    def _room(room_id, name):
        room = MagicMock(room_id=room_id)
        room.name = name
        return room

    def _backend(self):
        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        robot = MagicMock()
        # Two maps, and the robot reports neither as current -- exactly
        # the state that produced the refusal.
        robot.get_active_map_versions = AsyncMock(
            return_value=[{"p2map_id": "MAP-A"}, {"p2map_id": "MAP-B"}]
        )

        # Zone 101 lives on MAP-A, with no name (as @chairstacker's did).
        # get_map_metadata is called per map id; return the right rooms
        # for each.
        map_a = MagicMock()
        map_a.rooms_metadata = [self._room("101", None)]
        map_b = MagicMock()
        map_b.rooms_metadata = [self._room("202", None)]

        async def _metadata(p2map_id):
            return map_a if p2map_id == "MAP-A" else map_b

        robot.get_map_metadata = AsyncMock(side_effect=_metadata)
        robot.send_routine_command_via_cmd_topic = AsyncMock()

        data = MagicMock(blid="BLID123", prime_robot=robot)
        data.prime_status_coordinator.data = {}  # no current map reported
        return PrimeRoomCleaning(data), robot

    @pytest.mark.asyncio
    async def test_zone_command_derives_the_map_from_the_zone(self):
        backend, robot = self._backend()

        # Must NOT raise "not currently reporting which map".
        await backend.clean_rooms(["101"])

        robot.send_routine_command_via_cmd_topic.assert_called_once()
        sent = robot.send_routine_command_via_cmd_topic.call_args
        # The map the zone belongs to, not a guess and not a refusal.
        assert "MAP-A" in str(sent)

    @pytest.mark.asyncio
    async def test_zones_spanning_two_maps_still_refuse(self):
        """Deriving the map only works when the zones agree on one. A
        request mixing maps has no single answer and must still be
        refused rather than silently picking one."""
        from homeassistant.exceptions import HomeAssistantError

        backend, _robot = self._backend()

        with pytest.raises(HomeAssistantError):
            await backend.clean_rooms(["101", "202"])


class TestClassicMopParamsMatchTheCapture:
    """@ia74 asked how to start mopping and could not find a way.

    His capture of the iRobot app's own outbound command is what makes
    these shapes safe rather than guessed:

        vacuum          operatingMode 2
        vacuum + mop    operatingMode 6, padWetness {disposable, reusable}
        smart scrub     swScrub 1 (the app control was labelled)
        wetness         1 light, 2 standard, 3 hard

    These build the real payload rather than reading the source. Two
    earlier drafts of this class asserted on `inspect.getsource` -- the
    antipattern this project spent a day removing, written by the same
    hand that removed it.
    """

    @staticmethod
    def _params(**kwargs):
        """The `params` a Classic region clean actually sends."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.room_cleaning import ClassicRoomCleaning

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend._config_entry = MagicMock()
        backend._config_entry.options = {}
        backend._hass = hass_mock()
        backend._roomba = MagicMock()
        backend._pmap_by_region = {"2": "MAP-A"}

        captured = {}

        # CAPTURED AT THE ROBOT, not at the executor. Since roombapy 2.x
        # the command goes straight to `send_command`, so intercepting
        # `async_add_executor_job` would catch nothing and the test would
        # assert against an empty dict.
        async def _send(name, params):
            captured["name"] = name
            captured["params"] = params

        backend._roomba.send_command = _send

        with patch.object(
            ClassicRoomCleaning, "available_rooms", AsyncMock(return_value={"K": "2"})
        ), patch.object(
            ClassicRoomCleaning, "_raise_if_map_updating", MagicMock()
        ), patch.object(
            ClassicRoomCleaning, "_current_pmap_ids",
            MagicMock(return_value=("MAP-A", "V1")), create=True,
        ):
            asyncio.run(backend.clean_rooms(["2"], **kwargs))

        regions = (captured.get("params") or {}).get("regions") or [{}]
        return regions[0].get("params", {})

    def test_pad_wetness_sets_both_pad_types(self):
        """Classic's shape is `{disposable, reusable}`; Prime's is
        `{padPlate}`. Same field name, different structure."""
        params = self._params(pad_wetness=[2])

        assert params["padWetness"] == {"disposable": 2, "reusable": 2}

    def test_smart_scrub_goes_out_as_an_int(self):
        """The wire carries 0/1. `True` is not `1` to a JSON encoder
        that writes `true`."""
        on = self._params(smart_scrub=[True])
        off = self._params(smart_scrub=[False])

        assert on["swScrub"] == 1
        assert off["swScrub"] == 0
        assert not isinstance(on["swScrub"], bool)

    def test_both_are_omitted_when_not_asked_for(self):
        """The pad-wetness selects are device settings. A per-region
        value would silently override what somebody set there."""
        params = self._params()

        assert "padWetness" not in params
        assert "swScrub" not in params


class TestPrimeOffersZonesForAreaMapping:
    """A zone could not be mapped to a Home Assistant area, because it
    was never in the list the mapping dialog shows.

    `get_segments()` built from `available_rooms()`, which reads
    `rooms_metadata` — rooms, not zones. Classic has offered its zones
    all along, in this same file.

    Not a decision: when this was written `available_rooms()` was the
    only name source Prime had. `prime_room_names` came later, for the
    map, and the places already reading the older source were never
    revisited.
    """

    @staticmethod
    def _backend(rooms, names):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend.available_rooms = AsyncMock(return_value=rooms)
        entry = entry_mock()
        entry.runtime_data.prime_room_names = names
        backend._config_entry = entry
        return backend

    @pytest.mark.asyncio
    async def test_a_zone_is_offered(self):
        backend = self._backend(
            rooms={"Kitchen": "10"},
            names={"10": "Kitchen", "107": "Guest Access Zone"},
        )

        segments = await backend.get_segments()
        by_id = {s.id: s for s in segments}

        assert "zid_107" in by_id
        assert by_id["zid_107"].name == "Guest Access Zone"

    @pytest.mark.asyncio
    async def test_a_zone_uses_its_own_prefix(self):
        """`zid_`, not `rid_`: area_resolver distinguishes them, and a
        zone under a room's prefix would collide with a room of the same
        id."""
        backend = self._backend(
            rooms={"Kitchen": "10"}, names={"10": "Kitchen", "10x": "Zone"},
        )

        ids = {s.id for s in await backend.get_segments()}

        assert "rid_10" in ids
        assert "zid_10x" in ids

    @pytest.mark.asyncio
    async def test_rooms_are_not_duplicated_as_zones(self):
        """`prime_room_names` holds both, so a room appearing there must
        not be offered twice."""
        backend = self._backend(
            rooms={"Kitchen": "10"}, names={"10": "Kitchen"},
        )

        segments = await backend.get_segments()

        assert len(segments) == 1

    @pytest.mark.asyncio
    async def test_a_zone_segment_keeps_its_prefix(self):
        """A `zid_` id must reach `clean_rooms` WITH its prefix.

        This asserted the opposite -- that the prefix is stripped, like
        `rid_` -- and shipped in 4.1.0. `_send_region_command()` decides
        between RegionType.ZID and RegionType.RID by looking for exactly
        that prefix, so a stripped zone went out as a room with an id no
        room has: @chairstacker's robot left the dock, localised, found
        nothing and declared the mission complete.

        The old docstring worried that keeping the prefix would "clean
        whatever room shared the remainder". That is the hazard for
        `rid_`, whose remainder is a room id. For `zid_`, stripping is
        what creates the collision.
        """
        from unittest.mock import AsyncMock

        backend = self._backend(rooms={}, names={})
        backend.clean_rooms = AsyncMock()

        await backend.clean_segments(["zid_107"])

        assert backend.clean_rooms.await_args[0][0] == ["zid_107"]

    async def test_a_room_segment_still_loses_its_prefix(self):
        """The negative control: `rid_` must still be stripped, or the
        room id would carry four characters no room id has."""
        from unittest.mock import AsyncMock

        backend = self._backend(rooms={}, names={})
        backend.clean_rooms = AsyncMock()

        await backend.clean_segments(["rid_12"])

        assert backend.clean_rooms.await_args[0][0] == ["12"]


class TestStoredZonesSurviveWithoutCloud:
    """Stored zone data lives in the config entry options and needs no
    cloud. A guard clause discarded it anyway.

    The comment inside this method says a first version "lost every
    user-named room on installs without cloud credentials" and that six
    tests caught it. It came back four lines higher as
    `if coordinator is None: return {}` — and nothing caught it the
    second time, because every test builds a coordinator.

    @Young9898 reported that local-only installs have no way to get
    region ids, and proposed discovering them by launching real
    missions one id at a time. The ids were already there.
    """

    @staticmethod
    def _backend(zone_data, with_cloud):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        b = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        entry = entry_mock()
        entry.options = {"smart_zone_data": zone_data}
        b._config_entry = entry
        data = MagicMock()
        if with_cloud:
            data.cloud_coordinator.regions = [
                {"id": "10", "name": "Kitchen", "pmap_id": "MAP"}
            ]
            data.cloud_coordinator.zones = []
        else:
            data.cloud_coordinator = None
        b._data = data
        return b

    @pytest.mark.asyncio
    async def test_a_local_only_install_still_gets_its_rooms(self):
        """The regression, in one assertion."""
        b = self._backend(
            {"107": {"name": "Guest Zone", "pmap_id": "MAP"}}, with_cloud=False
        )

        rooms = await b.available_rooms()

        assert rooms == {"Guest Zone": "107"}

    @pytest.mark.asyncio
    async def test_cloud_names_still_layer_on_top(self):
        b = self._backend(
            {"107": {"name": "Guest Zone", "pmap_id": "MAP"}}, with_cloud=True
        )

        rooms = await b.available_rooms()

        assert rooms == {"Guest Zone": "107", "Kitchen": "10"}

    @pytest.mark.asyncio
    async def test_nothing_anywhere_is_still_empty(self):
        b = self._backend({}, with_cloud=False)

        assert await b.available_rooms() == {}


class TestAZoneCarriesItsMap:
    """@chairstacker: rooms cleaned, zones failed with "this robot has 2
    maps and is not currently reporting which one it is on".

    `available_rooms()` returns `{p2map_id}/{room_id}`, and
    `clean_rooms()` splits on that slash to learn which map the command
    is for. Zone segment ids had no map in them, so there was nothing to
    split and the two-map branch refused rather than guess. On a
    one-map robot it would have worked by luck.

    The two readers want the parts in opposite orders, which is the
    whole difficulty: `clean_rooms()` wants the map first, and
    `_send_region_command()` looks for `zid_` on what is left after the
    split. So the id is `zid_<map>/<region>` in the UI and
    `<map>/zid_<region>` on the way to the robot.
    """

    @staticmethod
    def _backend():
        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        return backend

    async def test_the_map_moves_in_front_of_the_prefix(self) -> None:
        from unittest.mock import AsyncMock

        backend = self._backend()
        backend.clean_rooms = AsyncMock()

        await backend.clean_segments(["zid_MAP-A/107"])

        assert backend.clean_rooms.await_args[0][0] == ["MAP-A/zid_107"]

    async def test_a_room_still_loses_only_its_prefix(self) -> None:
        """The negative control: rooms were never broken and must stay
        exactly as they were."""
        from unittest.mock import AsyncMock

        backend = self._backend()
        backend.clean_rooms = AsyncMock()

        await backend.clean_segments(["rid_MAP-A/12"])

        assert backend.clean_rooms.await_args[0][0] == ["MAP-A/12"]

    async def test_an_unqualified_zone_is_left_alone(self) -> None:
        """Stored zone data predating this has no map in it. Passing it
        through unchanged keeps a one-map robot working rather than
        turning a silent success into a crash."""
        from unittest.mock import AsyncMock

        backend = self._backend()
        backend.clean_rooms = AsyncMock()

        await backend.clean_segments(["zid_107"])

        assert backend.clean_rooms.await_args[0][0] == ["zid_107"]


class TestEveryRoomWasAlsoOfferedAsAZone:
    """@theChef163's zone "Litter" was missing and every room was
    duplicated. One line caused both.

    `available_rooms()` has returned map-qualified ids since 4.1.0 --
    `{p2map_id}/{room_id}` -- but the zone loop's "is this already a
    room?" check inverted that dict and compared its keys against the
    BARE region ids that `prime_room_names` is keyed by. "MAP-A/11"
    never equals "11", so the check never matched and every room was
    emitted a second time under a `zid_` id.

    Invisible in the selector, which keys by name and collapsed each
    pair back to one entry -- which is why it read as "the zone is
    missing" rather than "everything is doubled". Visible in the
    area-mapping dialog, which keys by id: @chairstacker reported
    "they both still have rooms and zones in them", and I read it as a
    description of the grouping rather than the bug it was.

    Reproduced from his diagnostics before being fixed: 21 segments
    where 11 were meant.
    """

    @staticmethod
    def _backend(rooms, names):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend.available_rooms = AsyncMock(return_value=rooms)
        backend._named_regions_across_maps = AsyncMock(return_value={})
        backend._region_map_ids = {}
        entry = MagicMock()
        entry.runtime_data.prime_room_names = names
        backend._config_entry = entry
        return backend

    #: His ten rooms and one zone, ids as his diagnostics carry them.
    _ROOMS = {
        "Dining Room": "MAP-A/10", "Living Room": "MAP-A/11",
        "Kitchen": "MAP-A/14", "Laundry": "MAP-A/19",
    }
    _NAMES = {
        "10": "Dining Room", "11": "Living Room",
        "14": "Kitchen", "19": "Laundry", "100": "Litter",
    }

    async def test_a_room_is_not_repeated_as_a_zone(self) -> None:
        backend = self._backend(self._ROOMS, self._NAMES)

        segments = await backend.get_segments()

        rooms = [s for s in segments if s.group == "Room"]
        zones = [s for s in segments if s.group == "Zone"]

        assert len(rooms) == 4
        assert [z.name for z in zones] == ["Litter"]

    async def test_the_zone_is_actually_offered(self) -> None:
        """The half @theChef163 saw: his one zone, absent from a list
        that had ten rooms in it."""
        backend = self._backend(self._ROOMS, self._NAMES)

        segments = await backend.get_segments()

        assert "Litter" in {s.name for s in segments}

    async def test_a_bare_room_id_still_matches(self) -> None:
        """Not every path returns qualified ids -- stored zone data and
        older entries do not. Both spellings have to suppress."""
        backend = self._backend({"Kitchen": "14"}, {"14": "Kitchen"})

        segments = await backend.get_segments()

        assert [s.group for s in segments] == ["Room"]


class TestClassicOffersEveryMapToo:
    """Parity with Prime for Home Assistant's area mapping.

    `clean_rooms()` has resolved the map per region since 4.1.0 --
    "every requested region carries its own map" -- but the segment
    path was left behind. It built every id with the cloud-ACTIVE map's
    prefix and, at clean time, dropped anything not matching it.

    Two consequences. A room on a second floor could not be mapped to an
    area at all. And a mapping that was correct when it was made stopped
    working the moment the cloud's list order moved -- in silence, which
    is the failure this project has spent a week removing.
    """

    @staticmethod
    def _backend(by_map, active="MAP-A"):
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        data = MagicMock()
        data.blid = "BLID1"
        data.has_cloud = True
        cloud = MagicMock()
        cloud.active_pmap_id = active
        cloud.regions_by_pmap = by_map
        cloud.regions = [
            {"id": rid, "name": name}
            for rid, name in (by_map.get(active) or {}).items()
        ]
        cloud.zones = []
        data.cloud_coordinator = cloud
        # `_cloud` is a property reading `_data.cloud_coordinator`;
        # setting it directly raises.
        backend._data = data
        backend._config_entry = MagicMock()
        backend._config_entry.options = {}
        # The rest of `clean_segments` needs these; the decode half is
        # what these tests are about.
        backend._roomba = robot_mock()
        backend._pmap_by_region = {}
        backend._hass = MagicMock()
        return backend

    _TWO_MAPS = {
        "MAP-A": {"10": "Kitchen", "11": "Hallway"},
        "MAP-B": {"20": "Master Bathroom", "21": "Hallway"},
    }

    async def test_rooms_from_the_second_map_are_offered(self) -> None:
        backend = self._backend(self._TWO_MAPS)

        names = {s.name for s in await backend.get_segments()}

        assert "Master Bathroom" in names

    async def test_each_id_carries_its_own_map(self) -> None:
        backend = self._backend(self._TWO_MAPS)

        ids = {s.name: s.id for s in await backend.get_segments()}

        assert ids["Master Bathroom"].startswith("MAP-B_")
        assert ids["Kitchen"].startswith("MAP-A_")

    async def test_the_active_map_wins_a_duplicate_name(self) -> None:
        """"Hallway" exists on both floors. The one the robot is on is
        the reading that is right more often -- the same rule
        `available_rooms()` follows."""
        backend = self._backend(self._TWO_MAPS)

        ids = {s.name: s.id for s in await backend.get_segments()}

        assert ids["Hallway"] == "MAP-A_11"

    async def test_a_stored_id_for_another_map_still_cleans(self) -> None:
        """The regression risk: existing area mappings hold ids in this
        exact format. One naming a non-active map was silently dropped;
        it has to reach the robot now, not merely warn.

        Asserts on the command sent: `clean_segments` builds and sends
        the payload itself on Classic rather than delegating to
        `clean_rooms()`, which is a thing worth knowing before writing
        a test against it."""
        backend = self._backend(self._TWO_MAPS)
        backend._raise_if_map_updating = MagicMock()

        await backend.clean_segments(["MAP-B_20"])

        command, params = backend._roomba.send_command.await_args[0]
        assert command == "start"
        assert [r["region_id"] for r in params["regions"]] == ["20"]

    async def test_two_maps_in_one_command_are_refused(self) -> None:
        """The payload carries one pmap_id. Cleaning half of what was
        asked for would be worse than saying no."""
        from unittest.mock import AsyncMock

        from homeassistant.exceptions import ServiceValidationError

        backend = self._backend(self._TWO_MAPS)
        backend.clean_rooms = AsyncMock()
        backend._raise_if_map_updating = MagicMock()

        with pytest.raises(ServiceValidationError):
            await backend.clean_segments(["MAP-A_10", "MAP-B_20"])

        backend._roomba.send_command.assert_not_awaited()

    async def test_a_map_id_containing_underscores_survives(self) -> None:
        """Real p2map ids contain underscores. Splitting on the first
        one would have made "2Bly_kGURy6OcUVTX7FN3w_19" lose its map."""
        from unittest.mock import AsyncMock

        backend = self._backend({"2Bly_kGURy6OcUVTX7FN3w": {"19": "Laundry"}},
                                active="2Bly_kGURy6OcUVTX7FN3w")
        backend._raise_if_map_updating = MagicMock()

        await backend.clean_segments(["2Bly_kGURy6OcUVTX7FN3w_19"])

        _command, params = backend._roomba.send_command.await_args[0]
        assert [r["region_id"] for r in params["regions"]] == ["19"]
        assert params["pmap_id"] == "2Bly_kGURy6OcUVTX7FN3w"


class TestBothGenerationsNameTheirMaps:
    """Parity: the selector labels each room with its floor, and a raw
    pmap id means nothing to a person.

    Prime reads the name from `get_active_map_versions()`. Classic has
    it too, but in a different place -- `active_pmapv_details.
    map_header.name`, not at the top of the pmap entry, where there is
    no name at all. Verified against a real /pmaps capture.
    """

    async def test_classic_reads_the_name_from_the_map_header(self) -> None:
        import json
        import pathlib

        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        pmaps = json.loads(
            (
                pathlib.Path(__file__).parent / "fixtures"
                / "irobot_pmaps_i3plus.json"
            ).read_text(encoding="utf-8")
        )
        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        data = MagicMock()
        cloud = MagicMock()
        cloud.data = {"pmaps": pmaps}
        data.cloud_coordinator = cloud
        backend._data = data

        assert await backend.map_names() == {"D8MepS5KRD6DTWlG-g5IEw": "Dom"}

    async def test_no_cloud_is_not_an_error(self) -> None:
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        data = MagicMock()
        data.cloud_coordinator = None
        backend._data = data

        assert await backend.map_names() == {}

    def test_both_backends_implement_it(self) -> None:
        """The point of the exercise. A method only Prime has is a
        feature only Prime users get."""
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
            PrimeRoomCleaning,
        )

        assert "map_names" in ClassicRoomCleaning.__dict__
        assert "map_names" in PrimeRoomCleaning.__dict__


class TestARegionIdMeansNothingWithoutItsMap:
    """@ScenicSystemsLLC's Braava ran a favourite against her "Second
    Floor" map while a one-room "master bathroom" map was active. Both
    maps have a region `1`.

    The command was correct -- diagnostics confirm
    `pmap_id: jUdHT1VfTZCaZklWE-_6tw`, regions `["1","5","16","10","18"]`
    -- and the robot cleaned the right five rooms. The DISPLAY said
    "Primary Bathroom" for the whole eighty minutes, because region `1`
    resolved against the active map.

    WORSE THAN THE BUG THIS HELPER WAS WRITTEN FOR. That one produced
    "Room ID 18" -- obviously broken. This produces a real room name
    from a real map, and nothing about it looks wrong.

    Region ids are unique per map, never across maps. The only thing
    that disambiguates them is which map the command named.
    """

    @staticmethod
    def _coordinator():
        cc = MagicMock()
        # Active map: one room, whose id collides with the other map's.
        cc.regions = [{"id": "1", "name": "Primary Bathroom"}]
        cc.zones = []
        cc.regions_by_pmap = {
            "ND9h7_4oR0qDPddhVuW8AQ": {"1": "Primary Bathroom"},
            "jUdHT1VfTZCaZklWE-_6tw": {
                "1": "Master Bedroom", "5": "Bedroom",
                "16": "Bedroom 2", "10": "Guest Bathroom", "18": "Hallway",
            },
        }
        return cc

    def test_without_the_hint_the_active_map_still_wins(self) -> None:
        """Unchanged for every existing caller. The old behaviour is not
        wrong in general -- it is wrong when the command named a
        different map, which the caller has to say."""
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        names = region_names_across_maps(self._coordinator())

        assert names["1"] == "Primary Bathroom"

    def test_the_commanded_map_wins_when_named(self) -> None:
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        names = region_names_across_maps(
            self._coordinator(), "jUdHT1VfTZCaZklWE-_6tw"
        )

        assert names["1"] == "Master Bedroom"

    def test_his_whole_room_list_resolves(self) -> None:
        """All five, in the order the favourite commanded them."""
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        names = region_names_across_maps(
            self._coordinator(), "jUdHT1VfTZCaZklWE-_6tw"
        )
        commanded = ["1", "5", "16", "10", "18"]

        assert [names[r] for r in commanded] == [
            "Master Bedroom", "Bedroom", "Bedroom 2",
            "Guest Bathroom", "Hallway",
        ]

    def test_an_unknown_map_does_not_lose_the_other_names(self) -> None:
        """A stale or misspelled pmap id must not empty the list --
        the hint is a preference, not a filter."""
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        names = region_names_across_maps(self._coordinator(), "no-such-map")

        assert names["1"] == "Primary Bathroom"
        assert names["18"] == "Hallway"


class TestTheRobotSaysWhichRegionsAreZones:
    """@theChef163's "Clean selected room" started a mission that ended
    71 seconds later, `completed`, with zero area cleaned.

    His mission record shows why:

        Region(region_id='100', region_type=RegionType.RID)

    Region 100 is his zone "Litter". His rooms are 10-19; there is no
    room 100. The robot accepted a room id that does not exist, found
    nothing to clean and reported success.

    `rooms_metadata` lists every region on the map -- zones included --
    and each entry carries `region_type`, which the library reads from a
    confirmed live response and maps to `rid` or `zid`. This code
    ignored it and treated every entry as a room.

    Absence still means room: not every capture carries the field, and
    rooms outnumber zones by far.
    """

    @staticmethod
    def _backend(entries):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend._config_entry = MagicMock()
        backend._config_entry.runtime_data.prime_room_names = {}
        backend._all_map_ids = AsyncMock(return_value=["MAP-A"])
        backend._current_map_id = AsyncMock(return_value="MAP-A")
        backend._map_metadata = AsyncMock(
            return_value=SimpleNamespace(rooms_metadata=entries)
        )
        return backend

    @staticmethod
    def _entry(room_id, name, region_type):
        from types import SimpleNamespace

        return SimpleNamespace(
            room_id=room_id, name=name, region_type=region_type
        )

    #: His map as the cloud reports it.
    def _his_map(self):
        return [
            self._entry("10", "Dining Room", "rid"),
            self._entry("14", "Kitchen", "rid"),
            self._entry("19", "Laundry", "rid"),
            self._entry("100", "Litter", "zid"),
        ]

    async def test_a_zone_is_not_offered_as_a_room(self) -> None:
        backend = self._backend(self._his_map())

        rooms = await backend.available_rooms()

        assert "Litter" not in rooms
        assert set(rooms) == {"Dining Room", "Kitchen", "Laundry"}

    async def test_the_zone_is_recorded_rather_than_dropped(self) -> None:
        """It still has to reach the user -- under the zone prefix, so
        the command goes out as `zid`."""
        backend = self._backend(self._his_map())

        await backend.available_rooms()

        assert backend._zone_region_ids == {"100"}

    async def test_a_missing_region_type_still_means_room(self) -> None:
        """Older captures carry no such field. Treating absence as
        "zone" would empty the room list on every one of them."""
        backend = self._backend([self._entry("10", "Dining Room", None)])

        rooms = await backend.available_rooms()

        assert "Dining Room" in rooms
        assert backend._zone_region_ids == set()

    async def test_the_enum_form_is_accepted_too(self) -> None:
        """The library maps the wire value to a `RegionType`; a plain
        string arrives from older or hand-built data."""
        from roombapy_prime.models import RegionType

        backend = self._backend([self._entry("100", "Litter", RegionType.ZID)])

        rooms = await backend.available_rooms()

        assert rooms == {}
        assert backend._zone_region_ids == {"100"}


class TestAZoneReachesTheRobotAsAZone:
    """End to end for @theChef163's case: the zone has to survive from
    the segment list to the wire.

    Before the `region_type` fix his zone was offered as a ROOM, so the
    command went out as `RegionType.RID` with region 100 -- an id no
    room on his map has. The robot accepted it, found nothing and
    reported `completed` in 71 seconds.
    """

    @staticmethod
    def _backend():
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend._raise_if_map_updating = MagicMock()
        backend._send_region_command = AsyncMock()
        backend._current_map_id = AsyncMock(return_value="MAP-A")
        backend._all_map_ids = AsyncMock(return_value=["MAP-A"])
        backend._region_to_map = AsyncMock(return_value={})
        backend._note_mission_plan = MagicMock()
        return backend

    async def test_the_prefix_survives_to_the_send(self) -> None:
        """`_send_region_command()` picks RID or ZID from this prefix.
        Strip it anywhere earlier and the zone goes out as a room."""
        backend = self._backend()

        await backend.clean_segments(["zid_100"])

        _map, region_ids = backend._send_region_command.await_args[0][:2]
        assert region_ids == ["zid_100"]

    async def test_a_room_still_arrives_bare(self) -> None:
        """The other half: a room must NOT gain a zone prefix."""
        backend = self._backend()

        await backend.clean_segments(["rid_MAP-A/14"])

        _map, region_ids = backend._send_region_command.await_args[0][:2]
        assert region_ids == ["14"]

    def test_the_send_decides_by_prefix(self) -> None:
        """Pinned because it is the only thing that distinguishes them
        at that point -- there is no separate type argument."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        source = inspect.getsource(PrimeRoomCleaning._send_region_command)

        assert "RegionType.ZID" in source
        assert "rid.startswith(ZID_PREFIX)" in source


class TestAZoneKnowsItsMapEvenWhenTheRobotDoesNot:
    """@chairstacker's robot has two maps and does not populate
    `cleanMissionStatus.p2mapId`. Cleaning a zone failed with:

        This robot has 2 maps and is not currently reporting which one
        it is on, so there is no way to tell which floor's rooms you
        mean.

    The map was knowable the whole time. `_region_to_map()` reads
    `rooms_metadata`, which lists every region including zones, and is
    keyed by BARE region id. A zone arrives as `zid_100`, so the lookup
    missed and the map stayed unknown.

    Invisible on a one-map robot -- the fallback picks the only map.
    Fatal on two.
    """

    @staticmethod
    def _backend(region_to_map, map_ids):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend._raise_if_map_updating = MagicMock()
        backend._send_region_command = AsyncMock()
        backend._note_mission_plan = MagicMock()
        backend._current_map_id = AsyncMock(return_value=None)
        backend._all_map_ids = AsyncMock(return_value=map_ids)
        backend._region_to_map = AsyncMock(return_value=region_to_map)
        return backend

    async def test_a_zone_resolves_its_map_on_a_two_map_robot(self) -> None:
        backend = self._backend({"100": "UPSTAIRS", "11": "DOWN"},
                                ["DOWN", "UPSTAIRS"])

        await backend.clean_segments(["zid_100"])

        p2map_id = backend._send_region_command.await_args[0][0]
        assert p2map_id == "UPSTAIRS"

    async def test_the_prefix_still_reaches_the_send(self) -> None:
        """Resolving the map must not consume the prefix -- the send
        decides room-versus-zone from it."""
        backend = self._backend({"100": "UPSTAIRS"}, ["DOWN", "UPSTAIRS"])

        await backend.clean_segments(["zid_100"])

        region_ids = backend._send_region_command.await_args[0][1]
        assert region_ids == ["zid_100"]

    async def test_an_unknown_zone_still_refuses_honestly(self) -> None:
        """If the id is on no map we know of, guessing between two is
        worse than refusing. That guard stays."""
        from homeassistant.exceptions import HomeAssistantError

        backend = self._backend({"11": "DOWN"}, ["DOWN", "UPSTAIRS"])

        with pytest.raises(HomeAssistantError):
            await backend.clean_segments(["zid_999"])

    async def test_one_map_was_never_affected(self) -> None:
        """Why this went unnoticed: with a single map the fallback picks
        it regardless of whether the lookup matched."""
        backend = self._backend({}, ["ONLY"])

        await backend.clean_segments(["zid_100"])

        assert backend._send_region_command.await_args[0][0] == "ONLY"


class TestTheServiceKnowsTheRoomsTheDisplayShows:
    """@mnsnyds passed "Guest Bath" and "Dining Room" to `clean_room`.
    Both are real rooms on his robot -- they appear in his room names,
    in the selector and in the display. The service answered:

        Unknown room(s) for vacuum.ronald_roomba:
        ['Guest Bath', 'Dining Room']

    The name matcher handles both fine, including whitespace and case.
    What was empty was the LIST it matched against.

    TWO SOURCES FOR THE SAME NAMES. The display and the selector read
    `prime_room_names`; `available_rooms()` read only `rooms_metadata`.
    When map metadata is empty -- not fetched, or a failed fetch -- the
    service sees no rooms at all while the names sit visible beside it.
    """

    #: His nine rooms, from his own diagnostics.
    HIS_ROOMS = {
        "10": "Great Room", "11": "Master Bedroom", "17": "Kitchen",
        "18": "Dining Room", "13": "Guest Room", "12": "Master Bath",
        "14": "Hallway", "16": "Master Closet", "15": "Guest Bath",
    }

    def _backend(self, map_ids, names=None):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        entry = MagicMock()
        entry.runtime_data.prime_room_names = (
            self.HIS_ROOMS if names is None else names
        )
        backend._config_entry = entry
        backend._all_map_ids = AsyncMock(return_value=map_ids)
        backend._current_map_id = AsyncMock(
            return_value=map_ids[0] if map_ids else None
        )
        backend._map_metadata = AsyncMock(
            return_value=SimpleNamespace(rooms_metadata=[])
        )
        return backend

    async def test_his_request_resolves(self) -> None:
        from custom_components.roomba_plus.room_cleaning import (
            match_room_names,
        )

        available = await self._backend(["MAP-A"]).available_rooms()
        room_ids, unknown = match_room_names(
            available, ["Guest Bath", "Dining Room"]
        )

        assert unknown == []
        assert room_ids == ["MAP-A/15", "MAP-A/18"]

    async def test_every_named_room_comes_through(self) -> None:
        available = await self._backend(["MAP-A"]).available_rooms()

        assert len(available) == len(self.HIS_ROOMS)

    async def test_two_maps_still_refuse(self) -> None:
        """`prime_room_names` is merged across maps and carries no map of
        its own. Qualifying an id on a two-map robot would be guessing a
        floor -- refusing is the honest answer."""
        available = await self._backend(["MAP-A", "MAP-B"]).available_rooms()

        assert available == {}

    async def test_metadata_wins_when_it_has_anything(self) -> None:
        """The fallback is last-resort. Real metadata carries per-map,
        user-set names and must not be displaced by the merged cache."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        backend = self._backend(["MAP-A"])
        backend._map_metadata = AsyncMock(
            return_value=SimpleNamespace(
                rooms_metadata=[
                    SimpleNamespace(
                        room_id="10", name="Renamed Room", region_type="rid"
                    )
                ]
            )
        )

        available = await backend.available_rooms()

        assert available == {"Renamed Room": "MAP-A/10"}

    async def test_no_names_cached_is_not_an_error(self) -> None:
        backend = self._backend(["MAP-A"], names=None if False else {})

        assert await backend.available_rooms() == {}


class TestTheNameFallbackDoesNotUndoTheZoneFix:
    """The cached-name fallback added for @mrsnyds nearly reintroduced
    @theChef163's bug one release later.

    `prime_room_names` is a flat id-to-name map with NO region types in
    it. Building rooms from it offers every region as a room -- zones
    included -- which is exactly the fault that made his "Clean selected
    room" start a mission that ended in 71 seconds with nothing cleaned.

    `discovered_zone_ids` on the config entry is the only zone knowledge
    available when map metadata is not.
    """

    def _backend(self, names, zone_ids=()):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        backend = PrimeRoomCleaning.__new__(PrimeRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        entry = MagicMock()
        entry.runtime_data.prime_room_names = names
        entry.options = {"discovered_zone_ids": list(zone_ids)}
        backend._config_entry = entry
        backend._all_map_ids = AsyncMock(return_value=["MAP-A"])
        backend._current_map_id = AsyncMock(return_value="MAP-A")
        backend._map_metadata = AsyncMock(
            return_value=SimpleNamespace(rooms_metadata=[])
        )
        return backend

    async def test_a_known_zone_is_not_offered_as_a_room(self) -> None:
        """His layout: 100 is the zone "Litter", 10-19 are rooms."""
        backend = self._backend(
            {"100": "Litter", "10": "Dining Room", "14": "Kitchen"},
            zone_ids=["100"],
        )

        rooms = await backend.available_rooms()

        assert "Litter" not in rooms
        assert sorted(rooms) == ["Dining Room", "Kitchen"]

    async def test_the_rooms_still_come_through(self) -> None:
        backend = self._backend({"15": "Guest Bath", "18": "Dining Room"})

        rooms = await backend.available_rooms()

        assert sorted(rooms) == ["Dining Room", "Guest Bath"]

    async def test_the_ids_carry_their_map(self) -> None:
        """Qualified, so `clean_rooms()` takes the explicit-map path and
        never needs the region-to-map lookup -- which reads the same
        empty metadata and would have failed the same way."""
        backend = self._backend({"15": "Guest Bath"})

        rooms = await backend.available_rooms()

        assert rooms["Guest Bath"] == "MAP-A/15"

    async def test_the_map_lookup_is_not_consulted(self) -> None:
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.room_cleaning import (
            match_room_names,
        )

        backend = self._backend({"15": "Guest Bath", "18": "Dining Room"})
        backend._raise_if_map_updating = MagicMock()
        backend._send_region_command = AsyncMock()
        backend._note_mission_plan = MagicMock()
        backend._region_to_map = AsyncMock(
            side_effect=AssertionError("should not be needed")
        )

        available = await backend.available_rooms()
        room_ids, _ = match_room_names(
            available, ["Guest Bath", "Dining Room"]
        )
        await backend.clean_rooms(room_ids)

        p2map_id, regions = backend._send_region_command.await_args[0][:2]
        assert p2map_id == "MAP-A"
        assert regions == ["15", "18"]


class TestClassicKnowsWhichFloorItIsOn:
    """A Classic robot reports no `p2mapId` -- that is Prime's field --
    so `where_the_robot_is()` returned nothing for it, and the wrong-floor
    warning could never fire on the generation that needed it most.

    IT DOES NOT NEED TO REPORT ONE. `lastCommand.pmap_id` names the map
    the robot was last sent to, and a robot that finished a mission on a
    floor is standing on that floor: it drove back to the dock there.

    NOT `active_pmap_id`, which is the cloud's most-recently-updated map.
    @ScenicSystemsLLC checked directly -- two missions against one map,
    forced fresh reads, and the active map never moved off the other.
    On his Braava and on @Thonno's i7+ the two disagree, and
    `lastCommand` is the one that tracks reality.

    @Thonno's start failed with "Smart Map localization failed" because
    the robot was on the floor `lastCommand` names. Nothing in the log
    connected the error to the floor.
    """

    @staticmethod
    def _backend(last_command):
        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        backend._data = MagicMock()
        backend._data.blid = "BLID1"
        backend._data.roomba_reported_state = MagicMock(
            return_value={"lastCommand": last_command}
        )
        return backend

    async def test_the_last_commanded_map_is_the_remembered_floor(self) -> None:
        """His real values: the command named one map, the cloud another."""
        backend = self._backend({"pmap_id": "oGwE49YGTeWffssbEVx65g"})

        on_map, is_live = await backend.where_the_robot_is()

        assert on_map == "oGwE49YGTeWffssbEVx65g"

    async def test_it_is_never_reported_as_live(self) -> None:
        """A memory, not a measurement. It is right until somebody
        carries the robot upstairs, and a consumer has to be able to
        tell the difference."""
        backend = self._backend({"pmap_id": "MAP-A"})

        _on_map, is_live = await backend.where_the_robot_is()

        assert is_live is False

    async def test_no_last_command_means_no_claim(self) -> None:
        """A robot that has never been sent anywhere has no floor, and
        guessing one would be worse than admitting it."""
        for empty in ({}, {"pmap_id": None}, {"pmap_id": ""}):
            backend = self._backend(empty)

            assert await backend.where_the_robot_is() == (None, False)

    def test_the_warning_reaches_the_classic_path(self) -> None:
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        source = inspect.getsource(ClassicRoomCleaning.clean_rooms)

        assert "where_the_robot_is()" in source
        assert "Sending anyway" in source

    def test_the_warning_does_not_block(self) -> None:
        """Somebody may have carried the robot. Refusing would turn a
        hint into an obstacle."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            ClassicRoomCleaning,
        )

        source = inspect.getsource(ClassicRoomCleaning.clean_rooms)
        # From the CALL, not the comment that mentions it first.
        block = source[source.index("await self.where_the_robot_is()"):]

        assert "_LOGGER.warning" in block[:600]
        assert "raise" not in block[:600]
