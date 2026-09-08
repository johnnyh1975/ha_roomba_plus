"""Targeting a room that lives on a map the robot is not currently using.

@ScenicSystemsLLC trained a bathroom-only map for spot cleaning. After
using it, every room on his real Second Floor map became unreachable --
from the button AND from `clean_room`, for two unrelated reasons. He
found both by testing, after being told the room list was already
map-independent. It was not.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.roomba_plus.button import pick_zone_selection


def _select(*, uid: str, region: str | None, active: bool, pmap: str = ""):
    entity = SimpleNamespace()
    entity.unique_id = uid
    entity.selected_region_id = region
    entity._is_active_map = active
    entity.selected_pmap_info = {"pmap_id": pmap} if pmap else {}
    return entity


def _chosen(entities: list) -> object | None:
    """The entity the button would act on -- via the REAL rule.

    An earlier version of this helper reimplemented the selection logic
    here, which meant reverting the fix in `button.py` left every test
    passing. It now calls the same function the button calls.
    """
    candidates = [e for e in entities if getattr(e, "selected_region_id", None)]
    return pick_zone_selection(candidates)


class TestTheButtonPrefersRatherThanRequires:
    def test_a_selection_on_an_inactive_map_is_used(self) -> None:
        """The bug: this used to be discarded, and the button fell
        through to `lastCommand` -- cleaning whatever ran last instead
        of the room the user picked."""
        inactive = _select(uid="r_cloud_zone_second", region="19", active=False)

        assert _chosen([inactive]) is inactive

    def test_the_active_map_still_wins_when_both_have_one(self) -> None:
        """The original intent, and it must survive the fix: with a
        selection on each, the map the robot is using takes it."""
        inactive = _select(uid="r_cloud_zone_bath", region="19", active=False)
        active = _select(uid="r_cloud_zone_second", region="21", active=True)

        assert _chosen([inactive, active]) is active

    def test_nothing_selected_stays_nothing(self) -> None:
        """No selection anywhere must not invent one -- the
        `lastCommand` fallback below it is the intended path there."""
        assert _chosen([_select(uid="r_x", region=None, active=True)]) is None


class TestRoomsFromEveryMap:
    """`coordinator.regions` is the active map only, deliberately: two
    maps sharing a name would otherwise clean the wrong floor silently.
    That stays. What changed is that rooms existing ONLY on another map
    are now nameable at all.
    """

    @staticmethod
    def _backend(active_regions, by_pmap, active_id):
        from custom_components.roomba_plus.room_cleaning import ClassicRoomCleaning

        coordinator = MagicMock()
        coordinator.data = {"pmaps": []}
        coordinator.regions = active_regions
        coordinator.zones = []
        coordinator.regions_by_pmap = by_pmap
        coordinator.active_pmap_id = active_id

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        backend._config_entry = SimpleNamespace(options={})
        backend._data = SimpleNamespace(cloud_coordinator=coordinator)
        return backend

    async def test_a_room_on_another_map_can_be_named(self) -> None:
        backend = self._backend(
            active_regions=[{"id": "1", "name": "Master Bathroom", "pmap_id": "bath"}],
            by_pmap={"second": {"19": "Hallway"}, "bath": {"1": "Master Bathroom"}},
            active_id="bath",
        )

        rooms = await backend.available_rooms()

        assert rooms["Hallway"] == "19", (
            "a room that exists only on a non-active map used to answer "
            "'unknown room'"
        )
        assert rooms["Master Bathroom"] == "1"

    async def test_a_duplicate_name_still_resolves_to_the_active_map(self) -> None:
        """The protection that made the restriction right in the first
        place. Two maps with a 'Bathroom' must not become a coin toss."""
        backend = self._backend(
            active_regions=[{"id": "1", "name": "Bathroom", "pmap_id": "bath"}],
            by_pmap={"second": {"19": "Bathroom"}, "bath": {"1": "Bathroom"}},
            active_id="bath",
        )

        rooms = await backend.available_rooms()

        assert rooms["Bathroom"] == "1", "the active map must win a name collision"


class TestFavouritesReachAnyMap:
    """A favourite carries its own `pmap_id`, fixed when it was created.

    That is why it reaches a room on a map the robot is not using --
    the one thing neither the zone button nor `clean_room` managed.
    @ScenicSystemsLLC captured the iRobot app doing exactly this on a
    CLASSIC robot, with the inactive map's id, and the robot drove
    straight there. The buttons here have always worked; only the
    `run_favorite` service was scoped to Prime, for no structural
    reason anyone had written down.
    """

    @staticmethod
    def _entry(favorites):
        coordinator = MagicMock()
        coordinator.data = {"favorites": favorites}
        return SimpleNamespace(
            runtime_data=SimpleNamespace(
                roomba=MagicMock(),
                cloud_coordinator=coordinator,
                blid="BLID123",
            )
        )

    async def test_a_classic_favourite_is_sent(self) -> None:
        from custom_components.roomba_plus.button import async_run_classic_favorite

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        entry = self._entry([
            {
                "favorite_id": "abc",
                "commanddefs": [{
                    "command": "start",
                    # robot_id lives INSIDE the command definition, not
                    # on the favourite -- `/user/favorites` is an account
                    # endpoint and attribution is per command.
                    "robot_id": "BLID123",
                    "pmap_id": "second-floor",
                    "regions": [{"region_id": "10", "type": "rid"}],
                }],
            }
        ])

        assert await async_run_classic_favorite(hass, entry, "abc") is True

        _fn, command, params = hass.async_add_executor_job.await_args.args
        assert command == "start"
        assert params["pmap_id"] == "second-floor", (
            "the favourite's own map must travel with the command -- that is "
            "what makes it work on an inactive map"
        )

    async def test_another_robots_favourite_is_not_run(self) -> None:
        """`/user/favorites` is an ACCOUNT endpoint. Running a sibling
        robot's favourite would send this one somewhere its map does not
        describe."""
        from custom_components.roomba_plus.button import async_run_classic_favorite

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()
        entry = self._entry([
            {
                "favorite_id": "abc",
                "commanddefs": [{"command": "start", "robot_id": "OTHER"}],
            }
        ])

        assert await async_run_classic_favorite(hass, entry, "abc") is False
        hass.async_add_executor_job.assert_not_awaited()

    async def test_an_unknown_id_sends_nothing(self) -> None:
        from custom_components.roomba_plus.button import async_run_classic_favorite

        hass = MagicMock()
        hass.async_add_executor_job = AsyncMock()

        assert await async_run_classic_favorite(hass, self._entry([]), "abc") is False


class TestZonesGoOutAsZones:
    """A zone is `type: "zid"` on the wire, not `"rid"`.

    The robot's own command schema enumerates
    `["rid", "zid", "wid", "tag"]` (ruby firmware), so sending a zone as
    a room contradicts the vendor contract rather than merely differing
    from the Prime path. And ids are numbered per map, so the wrong type
    very likely names a real but different region.

    `clean_zone` routes through `clean_rooms`, which hardcoded "rid".
    The Prime backend had the same bug and was fixed when the service
    was first wired -- the fourth repair in this project to reach one of
    two sibling classes and not the other.
    """

    @staticmethod
    def _backend_with(regions, zones):
        from custom_components.roomba_plus.room_cleaning import ClassicRoomCleaning

        coordinator = MagicMock()
        coordinator.data = {"pmaps": []}
        coordinator.regions = regions
        coordinator.zones = zones
        coordinator.regions_by_pmap = {}
        coordinator.active_pmap_id = "map1"

        backend = ClassicRoomCleaning.__new__(ClassicRoomCleaning)
        backend._config_entry = SimpleNamespace(options={})
        backend._data = SimpleNamespace(cloud_coordinator=coordinator)
        backend._pmap_by_region = {}
        backend._type_by_region = {}
        return backend

    async def test_a_zone_is_remembered_as_a_zone(self) -> None:
        backend = self._backend_with(
            regions=[{"id": "5", "name": "Kitchen", "pmap_id": "map1"}],
            zones=[{"id": "19", "name": "Under Table", "pmap_id": "map1"}],
        )

        await backend.available_rooms()

        assert backend._type_by_region["19"] == "zid"
        assert backend._type_by_region["5"] == "rid"

    async def test_an_unknown_id_still_defaults_to_a_room(self) -> None:
        """The previous behaviour, kept: an id that never went through
        the room list is most likely one a caller supplied directly, and
        rooms are the common case."""
        backend = self._backend_with(regions=[], zones=[])

        await backend.available_rooms()

        assert backend._type_by_region.get("99", "rid") == "rid"



class TestRoomNamesResolveAcrossMaps:
    """`current_room` showed "Room ID 18" instead of "Hallway".

    @ScenicSystemsLLC ran a favourite that cleaned a room on a map the
    robot was not currently using -- possible for the first time in
    4.1.0 -- and the robot went to the right room while the sensor
    displayed a raw id.

    `cloud_coordinator.regions` returns the ACTIVE map only. 4.1.0
    lifted that for `clean_room`'s lookup and left three other consumers
    reading the same property: the mission-progress sensors, the
    callbacks that name visited rooms, and the API view. All four built
    the same dict by hand.
    """

    @staticmethod
    def _coordinator():
        cc = MagicMock()
        cc.regions = [{"id": "1", "name": "Master Bathroom"}]
        cc.regions_by_pmap = {
            "second-floor": {"18": "Hallway", "1": "Bathroom"},
            "bathroom-map": {"1": "Master Bathroom"},
        }
        return cc

    def test_a_room_on_another_map_gets_its_name(self) -> None:
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        names = region_names_across_maps(self._coordinator())

        assert names["18"] == "Hallway", "this is the one that read 'Room ID 18'"

    def test_the_active_map_still_wins_a_duplicate(self) -> None:
        """Both maps have a region 1 with different names. The active
        map's must win, exactly as it did when only that map was read."""
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        assert region_names_across_maps(self._coordinator())["1"] == "Master Bathroom"

    def test_no_coordinator_is_not_an_error(self) -> None:
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        assert region_names_across_maps(None) == {}

    def test_no_consumer_builds_the_lookup_by_hand(self) -> None:
        """The guard. Four copies of one dict comprehension is how three
        of them kept the restriction after the fourth lost it."""
        import pathlib
        import re

        pattern = re.compile(r'r\["id"\]:\s*r\["name"\]')
        offenders = [
            path.name
            for path in sorted(
                (
                    pathlib.Path(__file__).parent.parent
                    / "custom_components"
                    / "roomba_plus"
                ).glob("*.py")
            )
            if pattern.search(path.read_text(encoding="utf-8"))
            and path.name != "room_cleaning.py"
        ]

        assert not offenders, (
            "these build the region-name lookup themselves instead of calling "
            f"region_names_across_maps(), and will read the active map only: "
            f"{offenders}"
        )



class TestZonesAcrossEveryMap:
    """@chairstacker on 4.1.0: rooms from every map appeared in the
    area-mapping dialog, zones only from the one being drawn.

    "Master Bathroom" showed up under Rooms and not under Zones;
    "Testing Zone 13d" showed up because a command had named it;
    "Testin Zone 13b" appeared nowhere at all.

    Two causes, fixed separately below.
    """

    def test_the_all_maps_source_carries_zones(self) -> None:
        """`regions_by_pmap` read `details["regions"]` and ignored
        `details["zones"]` -- the two sit side by side in the same
        object, and reading one of them is why a zone on another map had
        no name anywhere."""
        from custom_components.roomba_plus.cloud_coordinator import (
            IrobotCloudCoordinator,
        )

        coordinator = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        coordinator.data = {
            "pmaps": [
                {
                    # The id lives inside `active_pmapv`, not at the top
                    # of the pmap -- taken from a real /pmaps response.
                    "active_pmapv_details": {
                        "active_pmapv": {"pmap_id": "second-floor"},
                        "regions": [{"region_id": "10", "name": "Hallway"}],
                        "zones": [{"region_id": "13", "name": "Testin Zone 13b"}],
                    },
                }
            ]
        }

        by_map = coordinator.regions_by_pmap

        assert by_map["second-floor"] == {
            "10": "Hallway",
            "13": "Testin Zone 13b",
        }

    def test_a_room_wins_an_id_it_shares_with_a_zone(self) -> None:
        """Regions are read first, so the result matches what `regions`
        alone used to return for any id they both carry."""
        from custom_components.roomba_plus.cloud_coordinator import (
            IrobotCloudCoordinator,
        )

        coordinator = IrobotCloudCoordinator.__new__(IrobotCloudCoordinator)
        coordinator.data = {
            "pmaps": [
                {
                    "active_pmapv_details": {
                        "active_pmapv": {"pmap_id": "m1"},
                        "regions": [{"region_id": "1", "name": "Kitchen"}],
                        "zones": [{"region_id": "1", "name": "Under the table"}],
                    },
                }
            ]
        }

        assert coordinator.regions_by_pmap["m1"]["1"] == "Kitchen"

    def test_the_active_maps_zones_are_not_lost(self) -> None:
        """The regression this nearly shipped with. A first version of
        `region_names_across_maps()` read `regions` and not `zones`, so
        every consumer would have lost the active map's zones at once --
        a wider break than the gap being closed."""
        from custom_components.roomba_plus.room_cleaning import (
            region_names_across_maps,
        )

        cc = MagicMock()
        cc.regions = [{"id": "10", "name": "Kitchen"}]
        cc.zones = [{"id": "101", "name": "Under the table"}]
        cc.regions_by_pmap = {}

        assert region_names_across_maps(cc) == {
            "10": "Kitchen",
            "101": "Under the table",
        }


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


class TestAShortListDoesNotLookComplete:
    """@chairstacker: 18 entries in the selector, 17 in the area-mapping
    dialog, and nothing in the log about the difference.

    Both read the same backend method. They read it at different times:
    the selector loaded once at startup, the dialog reads live on every
    open. A per-map fetch that failed in one of those calls produced a
    shorter list that looks exactly like a complete one.

    Two separate faults, fixed separately below.
    """

    def test_a_failed_map_is_logged_as_a_warning(self) -> None:
        """It was `debug`, so nothing appeared in a normal log. The
        message has to say the list is incomplete, because a shorter
        list is otherwise indistinguishable from a full one."""
        import inspect

        from custom_components.roomba_plus.room_cleaning import (
            PrimeRoomCleaning,
        )

        source = inspect.getsource(PrimeRoomCleaning._named_regions_across_maps)

        assert "_LOGGER.warning" in source
        assert "_LOGGER.debug" not in source
        assert "missing from the room/zone list" in source

    def test_the_selector_reloads_rather_than_freezing(self) -> None:
        """Loading once at `async_added_to_hass` left the list frozen
        until the next restart -- a map retrained in the app, or a zone
        renamed, would never show up."""
        import inspect

        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        added = inspect.getsource(PrimeZoneSelect.async_added_to_hass)
        assert "async_add_listener" in added

        reload_src = inspect.getsource(PrimeZoneSelect._async_reload_segments)
        assert "_async_load_segments" in reload_src

    def test_the_reload_only_writes_state_when_something_changed(self) -> None:
        """The coordinator fires often. Writing state on every tick
        would be a state change per cloud poll for a list that hardly
        ever moves."""
        import inspect

        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        source = inspect.getsource(PrimeZoneSelect._async_reload_segments)

        assert "if self._segments != before" in source

    def test_the_reload_does_not_run_inside_the_callback(self) -> None:
        """Coordinator listeners are called synchronously. Reading the
        cloud in one holds up every listener behind it."""
        import inspect

        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        source = inspect.getsource(PrimeZoneSelect._schedule_segment_reload)

        assert "async_create_background_task" in source


class TestTheSelectorWaitsForTheMapNames:
    """@theChef163's zone "Litter" (region 100) was in the integration's
    own `region_names` and absent from the selector.

    The tell was in his report: Home Assistant's area-mapping dialog
    found the zone, and cleaning through it worked. That dialog reads
    the backend live. The selector read it once at
    `async_added_to_hass` -- before any map had been drawn, and zone
    names only arrive when the map image builds a floor plan.

    `SIGNAL_PRIME_ROOM_NAMES` already existed for this exact shape of
    problem: the per-region sensors were created only when a map
    happened to be built before the sensor platform set up
    (@chairstacker, #84). The selector now listens to the same signal
    instead of a second mechanism being invented for it.
    """

    def test_it_listens_for_the_names_arriving(self) -> None:
        import inspect

        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        source = inspect.getsource(PrimeZoneSelect.async_added_to_hass)

        assert "SIGNAL_PRIME_ROOM_NAMES" in source
        assert "async_dispatcher_connect" in source

    def test_the_signal_is_the_one_the_map_sends(self) -> None:
        """Both sides must agree on the name, and nothing checks a
        dispatcher signal at runtime -- a typo is silent."""
        import inspect

        from custom_components.roomba_plus import prime_room_map
        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        sent = inspect.getsource(prime_room_map)
        listened = inspect.getsource(PrimeZoneSelect.async_added_to_hass)

        assert "async_dispatcher_send" in sent
        assert prime_room_map.SIGNAL_PRIME_ROOM_NAMES.format("x") == (
            "roomba_plus_prime_room_names_x"
        )
        assert "SIGNAL_PRIME_ROOM_NAMES.format(" in listened

    def test_the_map_coordinator_is_still_watched_too(self) -> None:
        """The names signal covers zones appearing. A retrained map
        changes the rooms themselves, which it does not cover."""
        import inspect

        from custom_components.roomba_plus.select_prime import PrimeZoneSelect

        source = inspect.getsource(PrimeZoneSelect.async_added_to_hass)

        assert "async_add_listener" in source
