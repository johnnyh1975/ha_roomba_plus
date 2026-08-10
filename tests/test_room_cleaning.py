"""Room-targeted cleaning backends, both generations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


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
        entry = MagicMock()
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
        backend.available_rooms = AsyncMock(return_value={})

        with pytest.raises(ServiceValidationError, match="no named rooms"):
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
        "202": "prime_part_pad_wash_cleaning",
        "212": "prime_part_pad_wash_replacement",
    }

    def test_the_named_parts_match_the_field_reports(self):
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        assert _KNOWN_PARTS == self._PARTS

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

        return ClassicRoomCleaning(MagicMock(), MagicMock(), MagicMock())

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

        assert backend._hass.async_add_executor_job.await_count == 1


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

        entry = MagicMock()
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
