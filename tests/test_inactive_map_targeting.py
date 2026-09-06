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
