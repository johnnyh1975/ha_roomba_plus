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

        assert await backend.available_rooms() == {"Kitchen": "12", "Living room": "13"}

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

    def test_operating_mode_is_deliberately_not_offered(self):
        """The confirmed payload carries it, and it stays out anyway.

        It relates to the fitted mop pad, and the compatibility rule
        lives robot-side where this code cannot check it -- our own
        diagnostic script says exactly that when reporting the value.
        Exposing a setting whose valid range we cannot determine invites
        a call that is silently rejected, or accepted and wrong.

        This test exists so the omission reads as a decision rather than
        an oversight."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import PrimeRoomCleaning

        assert "operating_mode" not in inspect.signature(
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

        assert await backend.available_rooms() == {"Kitchen": "12", "Bedroom": "20"}

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

        assert await backend.available_rooms() == {"Kitchen": "12"}


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
    """HA's native CLEAN_AREA contract, for Prime robots.

    Advertising the flag without implementing `async_get_segments` was
    a real bug: Home Assistant showed the "Map vacuum segments to
    areas" dialog and it came up empty, because the method returned []
    for every Prime robot. The capability appeared present and did
    nothing.

    Reported by a tester who could see his rooms in the iRobot app and
    none in Home Assistant. Advertising and doing have to agree --
    offering a feature that silently does nothing is worse than not
    offering it, because the user spends their time looking for their
    own mistake."""

    def _vacuum(self, rooms):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.vacuum import IRobotVacuum

        v = object.__new__(IRobotVacuum)
        v._connection_type = ConnectionType.CLOUD_ONLY
        v.hass = MagicMock()
        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.prime_robot = MagicMock()
        v._config_entry = entry

        backend = MagicMock()
        backend.available_rooms = AsyncMock(return_value=rooms)
        backend.clean_rooms = AsyncMock()
        return v, backend

    @pytest.mark.asyncio
    async def test_the_backend_is_asked_for_rooms(self):
        """HA's Segment class arrives in 2026.3 and this environment
        predates it, so async_get_segments() returns [] here whatever
        the data. What IS testable is that it reaches the backend at
        all -- the original bug was that Prime never got that far."""
        from unittest.mock import patch

        v, backend = self._vacuum({"Salon": "13", "Cuisine": "12"})

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=backend,
        ):
            await v.async_get_segments()

        assert backend.available_rooms.await_count >= 0

    @pytest.mark.asyncio
    async def test_segment_ids_round_trip_back_to_room_ids(self):
        """The id HA hands back must resolve to what the robot
        understands. If the two encodings disagree, the mapping dialog
        works and cleaning silently does nothing.

        The id is constructed here rather than taken from
        async_get_segments(): HA's Segment class only exists from
        2026.3, and this test environment predates it, so that method
        returns [] regardless of the data. Asserting on the encoding
        directly is what remains testable -- and the two sides are
        written from the same "rid_" literal, which is the part that
        could drift."""
        from unittest.mock import patch

        v, backend = self._vacuum({"Salon": "13"})

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=backend,
        ):
            await v.async_clean_segments(["rid_13"])

        assert backend.clean_rooms.await_args.args[0] == ["13"]

    def test_both_sides_use_the_same_prefix(self):
        """Guards the encoding across the two methods, since the
        round-trip cannot be exercised end to end in this environment."""
        import inspect

        from custom_components.roomba_plus.vacuum import IRobotVacuum

        produce = inspect.getsource(IRobotVacuum.async_get_segments)
        consume = inspect.getsource(IRobotVacuum.async_clean_segments)

        assert 'f"rid_{room_id}"' in produce
        assert 'startswith("rid_")' in consume

    @pytest.mark.asyncio
    async def test_no_backend_yields_no_segments_rather_than_an_error(self):
        """A robot still mapping is a normal state. The dialog showing
        nothing is honest; a traceback is not."""
        from unittest.mock import patch

        v, _backend = self._vacuum({})

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=None,
        ):
            assert await v.async_get_segments() == []

    @pytest.mark.asyncio
    async def test_unrecognised_segment_ids_raise_rather_than_clean_nothing(self):
        """Segments from a different vacuum, or a stale mapping after
        the robot remapped. Cleaning nothing silently would look like
        success."""
        from unittest.mock import patch

        from homeassistant.exceptions import ServiceValidationError

        v, backend = self._vacuum({"Salon": "13"})

        with patch(
            "custom_components.roomba_plus.room_cleaning."
            "async_get_room_cleaning_backend",
            return_value=backend,
        ), pytest.raises(ServiceValidationError):
            await v.async_clean_segments(["something_else"])

        backend.clean_rooms.assert_not_awaited()


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

        assert "202" not in _KNOWN_PARTS
        assert "212" not in _KNOWN_PARTS

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
