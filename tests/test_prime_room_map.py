"""Prime room polygons, built the way Classic builds them."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _geometry(ring):
    return MagicMock(coordinates=[ring])


def _room(room_id, name, ring, simplified=None):
    room = MagicMock(
        room_id=room_id,
        properties=MagicMock(
            simplified_geometry=_geometry(simplified) if simplified else None,
            geometry=_geometry(ring),
        ),
    )
    room.name = name
    return room


def _entry(rooms=None, raises=False):
    entry = MagicMock()
    robot = entry.runtime_data.prime_robot
    if raises:
        robot.get_map_metadata = AsyncMock(side_effect=RuntimeError("boom"))
    else:
        robot.get_map_metadata = AsyncMock(
            return_value=MagicMock(rooms_metadata=rooms or [])
        )
    return entry


class TestPolygonsAndNamesAreSeparate:
    """Classic draws room polygons and exposes names as ATTRIBUTES.

    That is easy to get backwards. "A room map with names" sounds like
    labels drawn into the image, and Classic does the opposite: since
    v2.7.3 the labels were REMOVED from the PNG because the
    xiaomi-vacuum-map-card renders its own overlay from the attributes.
    Rotating per-room fill colours keep adjacent rooms distinguishable
    without them.

    An earlier draft of this file rasterised polygons into RoomSegStore
    cells, which is the path that *does* draw labels -- and would have
    doubled them up against the card's own."""

    _KITCHEN = [(0.0, 0.0), (3.0, 0.0), (3.0, 4.0), (0.0, 4.0)]

    @pytest.mark.asyncio
    async def test_polygons_and_names_come_back_keyed_alike(self):
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([_room("1", "Kitchen", self._KITCHEN)]), "MAP-1"
        )

        assert set(polygons) == set(names) == {"1"}
        assert names["1"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_coordinates_are_converted_to_millimetres(self):
        """THE conversion worth a test of its own: Prime reports metres,
        the renderer works in millimetres. Getting it wrong collapses
        every room into a few pixels -- a map that looks broken rather
        than empty, which is harder to attribute."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        polygons, _, _p = await async_build_prime_room_polygons(
            _entry([_room("1", "Kitchen", self._KITCHEN)]), "MAP-1"
        )

        xs = [x for x, _ in polygons["1"]]
        assert max(xs) == 3000.0

    @pytest.mark.asyncio
    async def test_simplified_geometry_is_preferred(self):
        """The app's own reduced outline: fewer points, and it keeps our
        rendering closer to what the user sees in the iRobot app."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        polygons, _, _p = await async_build_prime_room_polygons(
            _entry([_room(
                "1", "Kitchen",
                ring=[(0.0, 0.0), (9.0, 0.0), (9.0, 9.0), (0.0, 9.0)],
                simplified=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)],
            )]),
            "MAP-1",
        )

        assert max(x for x, _ in polygons["1"]) == 2000.0

    @pytest.mark.asyncio
    async def test_a_concave_room_keeps_all_its_vertices(self):
        """L-shaped and open-plan rooms are ordinary. Polygons are drawn
        as given, so unlike the cell-based path there is nothing to get
        wrong about concavity -- but a silent vertex loss would still
        square off the room."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        ell = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0), (2.0, 4.0), (0.0, 4.0)]
        polygons, _, _p = await async_build_prime_room_polygons(
            _entry([_room("1", "L-room", ell)]), "MAP-1"
        )

        assert len(polygons["1"]) == 6

    @pytest.mark.asyncio
    async def test_negative_coordinates_survive(self):
        """A Prime map's origin is wherever the robot first docked, so
        roughly half of a typical map is negative."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        polygons, _, _p = await async_build_prime_room_polygons(
            _entry([_room("1", "Salon", [(-2.0, -2.0), (0.0, -2.0), (0.0, 0.0), (-2.0, 0.0)])]),
            "MAP-1",
        )

        assert min(x for x, _ in polygons["1"]) == -2000.0


class TestRoomsThatCannotBeDrawn:
    """Omitted rather than kept: an entry with no outline would appear
    in the card's room list with nothing to highlight."""

    @pytest.mark.asyncio
    async def test_a_two_point_ring_is_omitted(self):
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([_room("1", "Broken", [(0.0, 0.0), (1.0, 1.0)])]), "MAP-1"
        )

        assert polygons == {} and names == {}

    @pytest.mark.asyncio
    async def test_a_room_without_an_id_is_omitted(self):
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        room = _room(None, "Nameless", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
        polygons, _, _p = await async_build_prime_room_polygons(_entry([room]), "MAP-1")

        assert polygons == {}

    @pytest.mark.asyncio
    async def test_a_failing_cloud_call_yields_nothing(self):
        """A map is enrichment, not a hard dependency."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        assert await async_build_prime_room_polygons(_entry(raises=True), "M") == ({}, {}, {})

    @pytest.mark.asyncio
    async def test_a_classic_entry_yields_nothing(self):
        """There is no prime_robot on a Classic entry, and this must not
        raise -- it is reachable from shared code paths."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        entry = MagicMock()
        entry.runtime_data.prime_robot = None

        assert await async_build_prime_room_polygons(entry, "M") == ({}, {}, {})


class TestXvmcAttributes:
    """The xiaomi-vacuum-map-card contract, for Prime.

    Same keys and same coordinate convention as the Classic rooms map,
    so a card configuration written for one robot works for the other."""

    _POLYGONS = {
        "1": [(0.0, 0.0), (3000.0, 0.0), (3000.0, 4000.0), (0.0, 4000.0)],
        "2": [(4000.0, 0.0), (7000.0, 0.0), (7000.0, 4000.0), (4000.0, 4000.0)],
    }

    def _points(self, polygons=None):
        from custom_components.roomba_plus.prime_room_map import prime_calibration_points

        return prime_calibration_points(
            polygons if polygons is not None else self._POLYGONS,
            lambda x, y: (int(x / 10), int(y / 10)),
        )

    def test_three_anchors_are_returned(self):
        """The card needs exactly three pairs for its affine transform."""
        assert len(self._points()) == 3

    def test_each_anchor_pairs_vacuum_millimetres_with_image_pixels(self):
        for point in self._points():
            assert set(point) == {"vacuum", "map"}
            assert set(point["vacuum"]) == {"x", "y"}
            assert set(point["map"]) == {"x", "y"}

    def test_anchors_are_bounding_box_corners_not_the_origin(self):
        """THE v2.7.2 lesson, copied deliberately. Classic used the dock
        origin (0, 0) as its first anchor, and for a robot docked in a
        corner -- against a wall, which is where people put them -- that
        point maps outside the rendered image and corrupts the card's
        transform.

        It would have reproduced here exactly: a Prime map's origin is
        wherever the robot first docked."""
        offset = {
            "1": [(-5000.0, -5000.0), (-2000.0, -5000.0), (-2000.0, -1000.0)],
        }
        anchors = [(p["vacuum"]["x"], p["vacuum"]["y"]) for p in self._points(offset)]

        assert (0.0, 0.0) not in anchors
        assert (-5000.0, -5000.0) in anchors

    def test_anchors_span_every_room_not_just_the_first(self):
        """With two rooms side by side the transform has to cover both,
        or the card highlights land on the wrong room."""
        anchors = [(p["vacuum"]["x"], p["vacuum"]["y"]) for p in self._points()]

        assert max(x for x, _ in anchors) == 7000.0

    def test_no_rooms_means_no_calibration(self):
        """Returning anchors for an empty map would give the card a
        degenerate transform rather than telling it to wait."""
        assert self._points({}) is None


class TestTheRoomMapIsRefreshedOnlyWhenItChanges:
    """Room geometry is stable, and the refresh strategy has to match
    that rather than the update rate of everything around it.

    Two approaches were built and discarded:

    A status-coordinator subscription fires on every shadow change --
    battery percent, phase, dock state -- and each would have triggered
    a get_map_metadata() cloud call. A request per battery percentage
    point, for data that changes when somebody renames a room.

    Re-rendering on every image request is what Classic's rooms map
    does, and it is free there: the polygons already sit in
    runtime_data. Prime's need a cloud call, so an open dashboard would
    poll iRobot's servers indefinitely.

    What actually invalidates the image is the MAP VERSION, which the
    robot bumps when geometry changes. Checking it is a shadow read that
    is already happening, so the common case costs nothing."""

    def _entity(self, version=None, rendered_for=None, *, maps=None,
                reported_map=None, rendered_map="M1"):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity.hass = MagicMock()
        entity._png = b"CACHED"
        entity._rendered_for_map_version = rendered_for
        entity._rendered_map_id = rendered_map
        entity._blid = "BLID"
        entry = MagicMock()
        if maps is None:
            maps = (
                [{"p2map_id": "M1", "active_p2mapv_id": version}] if version else []
            )
        state = {"p2maps": maps}
        if reported_map is not None:
            state["cleanMissionStatus"] = {"p2mapId": reported_map}
        entry.runtime_data.prime_status_coordinator = MagicMock(
            data={"ro-currentstate": state} if maps else {}
        )
        entity._config_entry = entry
        entity._async_refresh_rooms = AsyncMock()
        return entity

    @pytest.mark.asyncio
    async def test_an_unchanged_map_version_makes_no_cloud_call(self):
        """The common case, and the whole point: an open dashboard must
        not poll iRobot's servers."""
        entity = self._entity(version="V1", rendered_for="V1")

        await entity._async_refresh_if_map_changed()

        entity._async_refresh_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_changed_map_version_triggers_a_refresh(self):
        """A retrain, a room renamed, rooms split or merged -- all bump
        the version."""
        entity = self._entity(version="V2", rendered_for="V1")

        await entity._async_refresh_if_map_changed()

        entity._async_refresh_rooms.assert_awaited_once()
        assert entity._rendered_for_map_version == "V2"

    @pytest.mark.asyncio
    async def test_the_first_request_refreshes(self):
        """Nothing rendered yet, so there is nothing to compare against."""
        entity = self._entity(version="V1", rendered_for=None)

        await entity._async_refresh_if_map_changed()

        entity._async_refresh_rooms.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unreadable_version_still_refreshes(self):
        """Early in startup the shadow may carry no p2maps yet. Refusing
        to refresh would leave the map permanently blank; refreshing is
        the safe direction, and it settles once a version appears."""
        entity = self._entity(version=None, rendered_for=None)

        await entity._async_refresh_if_map_changed()

        entity._async_refresh_rooms.assert_awaited_once()

    def test_no_coordinator_subscription_is_registered(self):
        """Guards the decision. A subscription reads as the obvious
        improvement and would reintroduce a cloud call per shadow
        update."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage)

        assert "async_add_listener" not in source

    @pytest.mark.asyncio
    async def test_empty_post_mission_refresh_keeps_the_last_map(self):
        """Max 705 metadata has no geometry, so a transient bundle miss
        must not make an already rendered Rooms Map unavailable."""
        from unittest.mock import patch

        from custom_components.roomba_plus.image import PrimeRoomsImage
        entity = object.__new__(PrimeRoomsImage)
        entity.hass = MagicMock()
        entity._polygons = {"room": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]}
        entity._names = {"room": "Kitchen"}
        entity._preferences = {"room": {"profile": "normal"}}
        entity._floor_plan = SimpleNamespace(borders=[[(0.0, 0.0)]])
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.prime_robot.get_active_map_versions = AsyncMock(
            return_value=[]
        )
        backend = MagicMock()
        backend._all_map_ids = AsyncMock(return_value=["MAP-1"])
        backend._current_map_id = AsyncMock(return_value="MAP-1")

        with patch(
            "custom_components.roomba_plus.room_cleaning.async_get_room_cleaning_backend",
            return_value=backend,
        ), patch(
            "custom_components.roomba_plus.prime_room_map.async_build_prime_room_polygons",
            new=AsyncMock(return_value=({}, {}, {})),
        ):
            await entity._async_refresh_rooms()

        assert entity._polygons == {"room": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]}


class TestRoomsWithoutNames:
    """Two real captures disagree about whether rooms_metadata carries a
    `name`.

    One account has it for every room -- "Salon", "Bureau", "Couloir".
    Another has none at all, same endpoint, same firmware family. So the
    name is optional in practice regardless of what the model suggests.

    A room without a name still has an outline worth drawing, so it is
    kept with an empty name and the caller supplies a "Room <id>" label.
    Skipping it would leave a hole in the floor plan."""

    @pytest.mark.asyncio
    async def test_a_room_without_a_name_is_kept(self):
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        room = _room("15", "", [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)])
        polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([room]), "MAP-1"
        )

        assert "15" in polygons
        assert names["15"] == ""

    @pytest.mark.asyncio
    async def test_named_and_unnamed_rooms_coexist(self):
        """The realistic case if a user names some rooms and not others."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        square = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([_room("10", "Salon", square), _room("15", "", square)]),
            "MAP-1",
        )

        assert set(polygons) == {"10", "15"}
        assert names == {"10": "Salon", "15": ""}


class TestRoomsWithoutNames:
    """Not every robot supplies room names in its map metadata.

    Two real captures on the same day differed. One carried "Salon",
    "Bureau", "Couloir" per room; the other carried region_type and
    operating-mode defaults and no name field at all -- same firmware
    family, same endpoint, same day.

    Whether that is a per-robot difference, a per-map one, or depends on
    whether the user ever renamed a room is unknown. What matters is that
    the code cannot assume the field is there: an empty string renders as
    a blank label on the map and an unnamed entry in the card's room
    list, which looks like a bug rather than missing data."""

    @pytest.mark.asyncio
    async def test_a_room_without_a_name_falls_back_to_its_id(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        room = MagicMock(
            room_id="15",
            properties=MagicMock(
                simplified_geometry=None,
                geometry=_geometry([(0.0, 0.0), (3.0, 0.0), (3.0, 3.0)]),
            ),
        )
        # A capture without the field: getattr returns "" rather than a
        # name. Set explicitly, because a MagicMock would happily invent
        # one and hide the case.
        room.name = ""

        _polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([room]), "MAP-1"
        )

        assert names["15"] == "Room 15"

    @pytest.mark.asyncio
    async def test_a_named_room_keeps_its_name(self):
        """Verbatim from the other capture."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        _polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([_room("10", "Salon", [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0)])]),
            "MAP-1",
        )

        assert names["10"] == "Salon"

    @pytest.mark.asyncio
    async def test_accented_names_survive(self):
        """"Salle d'eau" and "Mattéo " came back in a real capture --
        including the trailing space, which is the user's own typing and
        not ours to trim."""
        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        _polygons, names, _prefs = await async_build_prime_room_polygons(
            _entry([_room("11", "Mattéo ", [(0.0, 0.0), (3.0, 0.0), (3.0, 3.0)])]),
            "MAP-1",
        )

        assert names["11"] == "Mattéo "


class TestFloorPlanFromTheMapBundle:
    """Walls, carpet and the dock — the parts of the bundle worth
    drawing.

    All three were modelled from decompiled serializer classes and never
    checked against real data until 30 July 2026, when two testers sent
    captures. That is the same position `set_virtual_wall` was in while
    it looked complete and failed for months, so these are tested against
    the shapes those captures actually contained."""

    def _plan(self, parsed):
        from unittest.mock import AsyncMock, MagicMock, patch

        import asyncio

        entry = MagicMock()
        robot = entry.runtime_data.prime_robot
        robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://x"})
        robot.download_map_bundle = AsyncMock(return_value=b"tgz")

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed
        ):
            return asyncio.get_event_loop().run_until_complete(
                async_build_prime_floor_plan(entry, "MAP-1", "V1")
            )

    @pytest.mark.asyncio
    async def test_borders_are_read_as_multipolygons(self):
        """Confirmed from the capture: borders.geojson carries
        MultiPolygon, which nests one level deeper than Polygon. Reading
        it as Polygon would take the first RING as a coordinate pair and
        produce nonsense."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        parsed = {"borders": {"features": [{
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]],
            },
        }]}}
        entry = MagicMock()
        entry.runtime_data.prime_robot.get_map_geojson_link = AsyncMock(
            return_value={"map_url": "https://x"}
        )
        entry.runtime_data.prime_robot.download_map_bundle = AsyncMock(return_value=b"x")

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed
        ):
            plan = await async_build_prime_floor_plan(entry, "MAP-1", "V1")

        assert len(plan.borders) == 1
        assert plan.borders[0][1] == (1000.0, 0.0)

    @pytest.mark.asyncio
    async def test_only_carpet_features_are_taken(self):
        """The wire key is `type`, not `floor_type` — a GeoJSON feature
        already has three other `type` keys around it. And filtered
        rather than taking everything: colouring hard floor as carpet is
        worse than drawing nothing."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        ring = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
        parsed = {"floorTypes": {"features": [
            {"geometry": {"type": "Polygon", "coordinates": [ring]},
             "properties": {"type": "carpet"}},
            {"geometry": {"type": "Polygon", "coordinates": [ring]},
             "properties": {"type": "hardfloor"}},
        ]}}
        entry = MagicMock()
        entry.runtime_data.prime_robot.get_map_geojson_link = AsyncMock(
            return_value={"map_url": "https://x"}
        )
        entry.runtime_data.prime_robot.download_map_bundle = AsyncMock(return_value=b"x")

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed
        ):
            plan = await async_build_prime_floor_plan(entry, "MAP-1", "V1")

        assert len(plan.carpet) == 1

    @pytest.mark.asyncio
    async def test_floor_plan_holes_and_furniture_are_loaded(self):
        """The saved-map detail layer retains interior wall outlines."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        outer = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0]]
        inner = [[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0]]
        furniture = [[1.5, 1.5], [2.5, 1.5], [2.5, 2.5], [1.5, 2.5]]
        parsed = {
        "floorPlan": {"features": [{"geometry": {
            "type": "MultiLineString", "coordinates": [outer, inner],
        }}]},
            "furniture": {"features": [{"geometry": {
                "type": "Polygon", "coordinates": [furniture],
            }}]},
        }
        entry = MagicMock()
        entry.runtime_data.prime_robot.get_map_geojson_link = AsyncMock(
            return_value={"map_url": "https://x"}
        )
        entry.runtime_data.prime_robot.download_map_bundle = AsyncMock(return_value=b"x")

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed
        ):
            plan = await async_build_prime_floor_plan(entry, "MAP-1", "V1")

        assert len(plan.floor_plan) == 2
        assert plan.floor_plan[0][0] == (0.0, 0.0)
        assert plan.floor_plan[0][1] == (4000.0, 0.0)
        assert len(plan.furniture) == 1

    @pytest.mark.asyncio
    async def test_the_dock_carries_an_orientation(self):
        """Confirmed from the capture's key list. Means a rendered dock
        can point the right way rather than being a dot."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        parsed = {"dockPose": {"features": [{
            "geometry": {"type": "Point", "coordinates": [1.5, -2.0]},
            "properties": {"orientation": 1.57},
        }]}}
        entry = MagicMock()
        entry.runtime_data.prime_robot.get_map_geojson_link = AsyncMock(
            return_value={"map_url": "https://x"}
        )
        entry.runtime_data.prime_robot.download_map_bundle = AsyncMock(return_value=b"x")

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed
        ):
            plan = await async_build_prime_floor_plan(entry, "MAP-1", "V1")

        assert plan.dock == (1500.0, -2000.0, 1.57)

    @pytest.mark.asyncio
    async def test_a_bundle_failure_costs_only_the_floor_plan(self):
        """It is a second cloud call, separate from the room polygons on
        purpose: the rooms are what the map is for."""
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        entry = MagicMock()
        entry.runtime_data.prime_robot.get_map_geojson_link = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        plan = await async_build_prime_floor_plan(entry, "MAP-1", "V1")

        assert plan.borders == [] and plan.carpet == [] and plan.dock is None


class TestRoomLabelOption:
    """Labels in the image are OFF by default, and that reads backwards
    until you know what Classic does.

    Classic removed its own labels in v2.7.3 because the
    xiaomi-vacuum-map-card renders an overlay from the `rooms` attribute,
    and drawing both doubles them up. So the default suits the card user.

    The option exists for everyone else: a plain picture-entity card
    shows an image and nothing else, and for them the names have to be in
    the picture or they do not exist."""

    def test_the_default_is_off(self):
        from custom_components.roomba_plus.const import (
            DEFAULT_MAP_ROOM_LABELS,
        )

        assert DEFAULT_MAP_ROOM_LABELS is False

    def test_the_attributes_are_published_regardless(self):
        """The option controls the IMAGE only. Withholding the attributes
        as well would break the card the default is designed for."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        attrs = inspect.getsource(PrimeRoomsImage.extra_state_attributes.fget)

        assert "PRIME_MAP_ROOM_LABELS" not in attrs
        assert "rooms" in attrs

    def test_it_is_offered_in_the_prime_options_form(self):
        import inspect

        from custom_components.roomba_plus import config_flow

        source = inspect.getsource(config_flow)

        assert "CONF_MAP_ROOM_LABELS" in source

    def test_it_is_translated_everywhere(self):
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        for locale_file in sorted((base / "translations").glob("*.json")):
            data = json.loads(locale_file.read_text(encoding="utf-8"))
            fields = data["options"]["step"]["settings"]["data"]
            assert "map_room_labels" in fields, locale_file.name


class TestRoomLabelsApplyToBothGenerations:
    """The label option is about MAPS, not about robot generations.

    Classic removed its labels in v2.7.3 for the same reason the Prime
    map never drew them: the xiaomi-vacuum-map-card renders its own
    overlay from the `rooms` attribute, and both at once doubles them up.
    So the same preference belongs on both, and a Classic user with a
    plain picture-entity card has exactly the problem the option solves.

    The key was briefly named prime_* while only the Prime map existed.
    It was renamed the same day, before shipping.

    Worth recording why the rename happened at all: a comment had already
    been written arguing AGAINST it, on the grounds that changing the key
    would reset the preference for existing users. There were no existing
    users -- the constant was hours old and had never been in a release.
    The argument was reasonable in general and false in this case, and
    nobody would have checked."""

    def test_the_classic_renderer_honours_the_option(self):
        import inspect

        from custom_components.roomba_plus.image import RoombaRoomsImage

        source = inspect.getsource(RoombaRoomsImage._render_rooms_png)

        assert "CONF_MAP_ROOM_LABELS" in source
        assert "rid_to_name" in source

    def test_the_prime_renderer_honours_the_same_option(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage._render_png)

        assert "CONF_MAP_ROOM_LABELS" in source

    def test_both_options_forms_offer_it(self):
        """The Prime and Classic branches of the settings step are
        separate schemas -- adding it to one and not the other would
        leave half the users unable to reach it."""
        import inspect

        from custom_components.roomba_plus import config_flow

        source = inspect.getsource(config_flow)

        # Once in the imports, once per form.
        assert source.count("CONF_MAP_ROOM_LABELS") >= 3

    def test_the_key_is_generation_neutral(self):
        """It controls both maps, so it should not name one of them."""
        from custom_components.roomba_plus.const import CONF_MAP_ROOM_LABELS

        assert CONF_MAP_ROOM_LABELS == "map_room_labels"
        assert "prime" not in CONF_MAP_ROOM_LABELS


class TestRoomCleaningPreferences:
    """Per-room settings the user made in the iRobot app.

    READ ONLY, deliberately. The obvious next step is a service that
    writes them, and that would be wrong: the robot already stores a
    preference per room per mode, set by hand, and a service call
    overriding it discards that with no way back.

    Surfacing them lets an automation HONOUR what the user configured --
    "clean the kitchen the way I set it up" rather than "clean the
    kitchen on deep because the automation says so"."""

    def _room(self, room_id, mode, defaults):
        from unittest.mock import MagicMock

        return MagicMock(
            room_id=room_id,
            last_operating_mode=mode,
            operating_mode_defaults=defaults,
        )

    def _prefs(self, rooms):
        from custom_components.roomba_plus.prime_room_map import (
            room_cleaning_preferences,
        )

        return room_cleaning_preferences(rooms)

    def test_settings_for_the_last_used_mode_are_read(self):
        """Verbatim from a real capture."""
        prefs = self._prefs([self._room("11", 2, {
            "2": {"profile": "normal", "suctionLevel": 3, "twoPass": False,
                  "carpetBoost": True},
        })])

        assert prefs["11"] == {
            "profile": "normal", "suction_level": 3, "two_pass": False,
            "carpet_boost": True, "operating_mode": 2,
        }

    def test_other_modes_are_not_reported(self):
        """A room stores defaults for several modes.
        last_operating_mode says which one it is actually in; reading any
        other would report a setting that does not currently apply."""
        prefs = self._prefs([self._room("15", 2, {
            "2": {"profile": "normal", "suctionLevel": 3},
            "512": {"profile": "deep", "suctionLevel": 4},
        })])

        assert prefs["15"]["suction_level"] == 3

    def test_absent_keys_are_omitted_not_defaulted(self):
        """An absent key means the robot did not report it, which is
        different from a zero."""
        prefs = self._prefs([self._room("13", 2, {"2": {"suctionLevel": 3}})])

        assert "carpet_boost" not in prefs["13"]
        assert "two_pass" not in prefs["13"]

    def test_the_mode_number_is_never_translated_to_a_profile(self):
        """A table used to map 2->normal, 32->light, 512->deep, built
        from one account's capture.

        A second account disproved it: operatingMode 2 came back as
        "smart" there, not "normal". The same number means different
        things on different robots, so the profile is read from the
        payload and never inferred.

        A room with no profile string has none. That is honest; a wrong
        word in front of the user is not."""
        prefs = self._prefs([self._room("16", 512, {"512": {"suctionLevel": 4}})])

        assert "profile" not in prefs["16"]
        assert prefs["16"]["suction_level"] == 4

    def test_an_unknown_mode_is_not_guessed(self):
        """The robot may have modes nobody has observed. Reporting one of
        the three known profiles for an unknown number would be a lie the
        user cannot detect."""
        prefs = self._prefs([self._room("17", 999, {"999": {"suctionLevel": 2}})])

        assert "profile" not in prefs["17"]
        assert prefs["17"]["suction_level"] == 2

    def test_a_room_with_no_defaults_is_skipped(self):
        assert self._prefs([self._room("18", 2, {})]) == {}

    def test_no_write_path_exists(self):
        """Guards the decision, because a write service is the obvious
        thing to add next."""
        import inspect

        from custom_components.roomba_plus import prime_room_map

        source = inspect.getsource(prime_room_map)

        assert "set_room_metadata" not in source
        assert "SetRoomMetadata" not in source


class TestLiveTrail:
    """Where the robot has actually driven, drawn on the rooms map.

    Confirmed possible by a tester's diagnostics: 904 position points
    across 451 messages in a single mission, roughly two per message.
    That was the open question -- whether Prime reports enough positions
    to draw a path or only enough to place a dot.

    THE TWO HALVES LIVE APART. The live map stream is watched by
    PrimeMapImage, which shows iRobot's own rendered PNG and has no
    renderer; the trail belongs on PrimeRoomsImage, which draws its own
    map and never sees the stream. The positions go through runtime_data
    because of that split -- a first version called self._renderer from
    the streaming entity, where it is None, so every point was silently
    discarded."""

    def _segments(self, positions):
        """Mirrors the trail splitting in _render_png."""
        out, trail, previous = [], [], None
        for x_mm, y_mm, _deg in positions:
            if previous is not None:
                dx, dy = x_mm - previous[0], y_mm - previous[1]
                if (dx * dx + dy * dy) ** 0.5 > 500.0:
                    if len(trail) >= 2:
                        out.append(len(trail))
                    trail, previous = [], (x_mm, y_mm)
                    continue
            trail.append((x_mm, y_mm))
            previous = (x_mm, y_mm)
        if len(trail) >= 2:
            out.append(len(trail))
        return out

    def test_a_straight_run_is_one_segment(self):
        assert self._segments([(i * 100.0, 0.0, 0.0) for i in range(10)]) == [10]

    def test_a_relocalisation_splits_the_trail(self):
        """Without the 500 mm rejection a relocalisation draws a straight
        line across the whole home -- the same guard the Classic renderer
        has had since v2."""
        positions = (
            [(i * 100.0, 0.0, 0.0) for i in range(10)]
            + [(9000.0, 0.0, 0.0)]
            + [(9000.0 + i * 100.0, 0.0, 0.0) for i in range(1, 6)]
        )

        assert self._segments(positions) == [10, 5]

    def test_a_single_point_draws_nothing(self):
        assert self._segments([(0.0, 0.0, 0.0)]) == []

    def test_metres_are_converted_to_millimetres(self):
        """THE trap. Prime reports metres -- a real keep-out zone
        measures 2.0 by 2.0 -- while the map is drawn in millimetres.
        Feeding metres straight in puts every point inside the same pixel
        and produces a blank map with no error anywhere.

        The comment at the stream call site warned about this before
        anything used the values."""
        import inspect

        from custom_components.roomba_plus.image import PrimeMapImage

        source = inspect.getsource(PrimeMapImage._feed_trail)

        assert "METRES_TO_MM" in source

    def test_radians_are_converted_to_degrees(self):
        """Quieter version of the same mistake: the trail would be drawn
        correctly and only the heading would point wrongly."""
        import inspect

        from custom_components.roomba_plus.image import PrimeMapImage

        source = inspect.getsource(PrimeMapImage._feed_trail)

        assert "math.degrees" in source

    def test_the_buffer_is_bounded(self):
        """904 points per mission, and a robot left running all day would
        grow this without limit. The oldest go first -- a trail showing
        the last stretch is more useful than one showing the first."""
        from custom_components.roomba_plus.image import _MAX_PRIME_POSITIONS

        assert 1000 <= _MAX_PRIME_POSITIONS <= 20000

        positions = list(range(_MAX_PRIME_POSITIONS + 500))
        del positions[:-_MAX_PRIME_POSITIONS]

        assert len(positions) == _MAX_PRIME_POSITIONS
        assert positions[-1] == _MAX_PRIME_POSITIONS + 499


class TestTheTrailIsClearedPerMission:
    """Positions only ever accumulated in the first version.

    Last night's path stayed on the map underneath tonight's, and after a
    week the map was a solid block. Bounded by count is not the same as
    bounded by mission -- the 5000-point cap would have kept several
    missions' worth visible at once, which looks like a rendering bug
    rather than like history."""

    def _coordinator(self, mission_id, positions, previous=None):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator,
        )

        coordinator = object.__new__(PrimeStatusCoordinator)
        coordinator.hass = MagicMock()
        coordinator._trail_mission_id = previous
        entry = MagicMock()
        entry.entry_id = "e1"
        entry.runtime_data.prime_positions = positions
        entry.runtime_data.mission_timer_store = MagicMock()
        coordinator.config_entry = entry
        shadows = {
            "ro-currentstate": {
                "cleanMissionStatus": {"phase": "run", "nMssn": mission_id}
            }
        }
        return coordinator, shadows

    def test_a_new_mission_clears_the_trail(self):
        positions = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
        coordinator, shadows = self._coordinator("m2", positions, previous="m1")

        coordinator._note_phase_for_timer(shadows)

        assert positions == []

    def test_the_same_mission_keeps_it(self):
        """A robot that pauses and resumes re-enters "run" with the same
        mission id. Clearing on phase rather than on id would erase the
        trail every time somebody picked the robot up."""
        positions = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
        coordinator, shadows = self._coordinator("m1", positions, previous="m1")

        coordinator._note_phase_for_timer(shadows)

        assert len(positions) == 2

    def test_the_first_mission_after_startup_starts_clean(self):
        """previous is None after a restart, so the first run clears --
        which is right: whatever is in the list came from before the
        reload and belongs to no mission this coordinator knows."""
        positions = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)]
        coordinator, shadows = self._coordinator("m1", positions, previous=None)

        coordinator._note_phase_for_timer(shadows)

        assert positions == []


class TestBareGeoJsonFeature:
    """At least one robot sends a bare Feature, not a FeatureCollection.

    The Roomba Max 705 (W155042) returns its border layer that way --
    reported by @jouwdan in PR #63, on a SKU nobody had tested.

    Reading only `features` yielded nothing on that robot: no error, no
    log line, just a map without walls. That is the failure mode this
    project keeps producing -- a shape assumption that is right for the
    robots you have."""

    def test_a_bare_feature_is_read(self):
        from custom_components.roomba_plus.prime_room_map import rings_mm

        bare = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
            },
        }

        assert len(rings_mm(bare)) == 1

    def test_a_feature_collection_still_works(self):
        from custom_components.roomba_plus.prime_room_map import rings_mm

        collection = {
            "type": "FeatureCollection",
            "features": [{
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
                },
            }],
        }

        assert len(rings_mm(collection)) == 1

    def test_a_bare_multipolygon_feature_is_read(self):
        """Borders are MultiPolygon, so the two quirks combine on exactly
        the layer where they were found."""
        from custom_components.roomba_plus.prime_room_map import rings_mm

        bare = {
            "type": "Feature",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [[[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]]],
            },
        }

        rings = rings_mm(bare)

        assert len(rings) == 1
        assert rings[0][1] == (2000.0, 0.0)


class TestStaticPrimeRoomsMap:
    def test_live_variant_has_a_non_colliding_entity_slug(self):
        """The existing raw image already owns ``cleaning_map`` on upgrades."""
        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._include_live = True

        assert entity.suggested_object_id == "prime_cleaning_map"

    def test_static_map_omits_live_room_and_coverage_fills(self):
        """The saved floor plan must not look like a completed mission."""
        import io

        from PIL import Image

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._include_live = False
        entity._renderer = None
        entity._polygons = {
            "room": [(0.0, 0.0), (2000.0, 0.0), (2000.0, 2000.0), (0.0, 2000.0)]
        }
        entity._floor_plan = SimpleNamespace(
            floor_plan=[], furniture=[], carpet=[], borders=[], dock=None
        )
        entity._live_bundle = {
            "coverage": {"features": [{"geometry": {"type": "Polygon", "coordinates": [
                [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5]]
            ]}}]}
        }
        entity._config_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(prime_positions=[]), options={}
        )

        image = Image.open(io.BytesIO(entity._render_png())).convert("RGB")

        assert image.getpixel((300, 300)) == (30, 30, 30)


class TestLiveBundleUpdatesTheRoomsMap:
    """The rooms map redraws during a mission, not only when the map
    version changes.

    Contributed by @jouwdan (PR #63), who found that MapUpdateMessage
    carries a SECOND url: `livemap_url` alongside `livemap_url_raw`. We
    had been consuming only the raw occupancy grid, which is what the
    other Prime image entity renders. The second returns a full map
    bundle.

    THE TWO ENTITIES ARE SPLIT BY CAPABILITY, which is why this needs a
    dispatcher signal rather than a direct call: PrimeMapImage watches
    the stream and has no renderer, PrimeRoomsImage draws its own map and
    never sees the stream."""

    def test_the_stream_entity_fetches_the_second_url(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeMapImage

        source = inspect.getsource(PrimeMapImage)

        assert "livemap_url_raw" in source
        assert "message.livemap_url" in source

    def test_the_rooms_map_subscribes(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage.async_added_to_hass)

        assert "async_dispatcher_connect" in source
        assert "_on_live_bundle" in source
        assert "_on_live_position" in source

    def test_the_subscription_is_removed_on_unload(self):
        """A reload would otherwise leave one connection per cycle, each
        rendering a PNG on every bundle."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage.async_added_to_hass)

        assert "async_on_remove" in source

    def test_live_updates_mark_the_image_dirty_without_rendering(self):
        """The dispatcher path must not spend CPU when nobody is viewing."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._live_dirty = False
        entity._last_live_state_write = float("inf")
        entity.async_write_ha_state = MagicMock()

        entity._on_live_bundle(MagicMock())

        assert entity._live_dirty
        entity.async_write_ha_state.assert_not_called()

    def test_rendering_happens_off_the_event_loop(self):
        """This is a dispatcher callback. Rendering a PNG inline would
        block the loop for as long as PIL takes."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage.async_image)

        assert "async_add_executor_job" in source

    def test_live_state_updates_are_throttled(self):
        """A dashboard should not refetch the image for every pose packet."""
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._live_dirty = False
        entity._last_live_state_write = 0.0
        entity.async_write_ha_state = MagicMock()

        with patch("custom_components.roomba_plus.image._time_mod.monotonic", side_effect=(10.0, 11.0, 12.0)):
            entity._on_live_position()
            entity._on_live_position()
            entity._on_live_position()

        assert entity._live_dirty
        assert entity.async_write_ha_state.call_count == 2

    def test_live_layers_are_rendered_without_changing_the_room_fit(self):
        """The live bundle is a layer, not a second coordinate system."""
        import io

        from PIL import Image

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        # The xiaomi card uses the static Rooms Map.  It must retain its
        # selectable-room attributes while still rendering live layers.
        entity._include_live = False
        entity._show_live_overlay = True
        entity._renderer = None
        entity._polygons = {
            "room": [
                (0.0, 0.0),
                (2000.0, 0.0),
                (2000.0, 2000.0),
                (0.0, 2000.0),
            ]
        }
        entity._floor_plan = SimpleNamespace(carpet=[], borders=[], dock=None)
        entity._live_bundle = {
            "coverage": {
                "features": [{
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[0.5, 0.5], [1.5, 0.5], [1.5, 1.5], [0.5, 1.5]]
                        ],
                    }
                }]
            },
            "trajectories": {
                "features": [{
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[0.25, 0.25], [0.75, 0.25]],
                    }
                }]
            },
            "hazard": {
                "features": [{
                    "geometry": {"type": "Point", "coordinates": [1.75, 1.75]}
                }]
            },
        }
        entity._config_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(prime_positions=[]), options={}
        )

        image = Image.open(io.BytesIO(entity._render_png())).convert("RGB")
        coverage_px = entity._renderer._mm_to_px_fit(1000.0, 1000.0)  # noqa: SLF001
        trajectory_px = entity._renderer._mm_to_px_fit(500.0, 250.0)  # noqa: SLF001
        hazard_px = entity._renderer._mm_to_px_fit(1750.0, 1750.0)  # noqa: SLF001

        assert image.getpixel(tuple(map(round, coverage_px))) == (120, 190, 145)
        assert image.getpixel(tuple(map(round, trajectory_px))) == (80, 150, 235)
        assert image.getpixel(tuple(map(round, hazard_px))) == (240, 150, 60)


class TestPolygonVerticesAreNotPoses:
    """Room corners are static geometry, not consecutive robot poses.

    `add_pose` applies a 500 mm jump rejection meant for telemetry from
    a driving robot. A 4 x 4 metre room has corners 4000 mm apart, so
    three of its four vertices were discarded as noise and the map was
    fitted to whatever survived.

    @jouwdan reported this against a13 and I could not reproduce it. He
    came back with before/after screenshots (PR #64). He was right; my
    check looked at the trail path, where the filter belongs, and not at
    this one -- twice, because he had also said outlines live in the map
    bundle before three testers confirmed it.

    The fix is his: seed the fit directly and compute it, bypassing the
    telemetry path entirely."""

    def _fitted(self, rings):
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer,
            RendererConfig,
        )

        renderer = MapRenderer(RendererConfig(), None, None)
        renderer._points = [
            renderer._mm_to_px(x, y) for ring in rings for x, y in ring
        ]
        _, _, _, scale, cx, cy = renderer._compute_fit()
        renderer._fit_scale, renderer._fit_cx, renderer._fit_cy = scale, cx, cy
        return renderer, [
            renderer._mm_to_px_fit(x, y) for ring in rings for x, y in ring
        ]

    def test_every_corner_survives(self):
        """The regression in one line: four in, four out."""
        room = [(0.0, 0.0), (4000.0, 0.0), (4000.0, 4000.0), (0.0, 4000.0)]

        renderer, _ = self._fitted([room])

        assert len(renderer._points) == 4

    def test_add_pose_would_have_dropped_them(self):
        """Asserting the premise, so this test explains itself if the
        renderer's filter ever changes."""
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer,
            RendererConfig,
        )

        renderer = MapRenderer(RendererConfig(), None, None)
        for x, y in [(0, 0), (4000, 0), (4000, 4000), (0, 4000)]:
            renderer.add_pose(x, y, 0.0)

        assert renderer.point_count < 4

    def test_the_room_fills_the_canvas(self):
        room = [(0.0, 0.0), (4000.0, 0.0), (4000.0, 4000.0), (0.0, 4000.0)]

        renderer, points = self._fitted([room])
        size = renderer._cfg.size_px
        xs = [p[0] for p in points]

        # Inside the canvas, and using most of it rather than a corner.
        assert 0 <= min(xs) and max(xs) <= size
        assert (max(xs) - min(xs)) > size * 0.5

    def test_borders_outside_the_rooms_still_fit(self):
        """The fit uses rooms, carpet AND borders. A wall beyond every
        room would otherwise be drawn off-canvas."""
        room = [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]
        border = [(-2000.0, -2000.0), (3000.0, -2000.0), (3000.0, 3000.0)]

        renderer, points = self._fitted([room, border])
        size = renderer._cfg.size_px

        assert all(0 <= x <= size and 0 <= y <= size for x, y in points)

    def test_borders_are_drawn_as_outlines(self):
        """Borders are MultiPolygon AREAS. Filling them paints a solid
        slab over the rooms underneath -- which is what @jouwdan's
        "before" screenshot showed."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage._render_png)
        # The DRAW loop, not the fit list -- borders appear in both, and
        # the fit list comes first. An earlier version of this test
        # matched the wrong one and failed for the right reason.
        border_line = next(
            line for line in source.splitlines()
            if "for ring in self._floor_plan.borders:" in line
        )
        after = source[source.index(border_line):]

        # A generous window: the explanation above the draw call is
        # longer than the call itself, which is the point of it.
        window = after[:1200]

        assert "outline=(90, 90, 90)" in window
        assert "fill=(90, 90, 90)" not in window


class TestTheFitChangeDoesNotTouchClassic:
    """The fit fix writes `_points` directly on a MapRenderer, which is
    the same class Classic robots use for their coverage map.

    Worth checking rather than assuming: bypassing a filter is exactly
    the kind of change that works for the case it was written for and
    breaks the one it was not."""

    def test_the_two_paths_use_separate_renderer_instances(self):
        """Classic builds its renderer during setup; PrimeRoomsImage
        builds its own. Neither can see the other's points."""
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer,
            RendererConfig,
        )

        classic = MapRenderer(RendererConfig(), None, None)
        for x in range(0, 1000, 100):
            classic.add_pose(x, 0, 0.0)
        before = classic.point_count

        prime = MapRenderer(RendererConfig(), None, None)
        prime._points = [prime._mm_to_px(x, y) for x, y in [(0, 0), (4000, 4000)]]

        assert classic.point_count == before

    def test_add_pose_still_filters_for_telemetry(self):
        """The filter is right for a driving robot and must stay. This
        change removes it from the polygon path only."""
        from custom_components.roomba_plus.map_renderer import (
            MapRenderer,
            RendererConfig,
        )

        renderer = MapRenderer(RendererConfig(), None, None)
        renderer.add_pose(0, 0, 0.0)
        renderer.add_pose(9000, 9000, 0.0)

        assert renderer.point_count == 1

    def test_the_prime_rooms_map_is_built_on_the_prime_branch_only(self):
        """A Classic entry must never reach this renderer usage at
        all."""
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image.async_setup_entry)
        branch = source[: source.index("PrimeRoomsImage(")]

        assert "ConnectionType.CLOUD_ONLY" in branch

    def test_only_one_place_constructs_a_renderer_in_image_py(self):
        """If a second one appears, the isolation above stops being
        something this test can vouch for."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "custom_components" / "roomba_plus" / "image.py"
        ).read_text(encoding="utf-8")

        assert source.count("MapRenderer(RendererConfig()") == 1


class TestTheRobotMarkerIsActuallyVisible:
    """Two testers independently reported the marker "not working" on
    a20. It was drawn the whole time.

    The measurement, not the impression: the marker was (60, 170, 255)
    and the trail it sits at the end of is (120, 200, 255) -- a
    difference of 90 across three channels out of 765. The dock, at
    (200, 200, 90), differs from the same trail by 245, and both testers
    could see that one immediately.

    "I cannot see it" and "it is not there" look identical in a
    screenshot, which is why this is asserted on pixels rather than on
    the drawing call existing.
    """

    def _render(self, dock_at_end=False):
        import io
        from types import SimpleNamespace

        from PIL import Image

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._renderer = None
        entity._polygons = {"r": [(0.0, 0.0), (4000.0, 0.0),
                                  (4000.0, 3000.0), (0.0, 3000.0)]}
        entity._names = {}
        entity._live_bundle = None
        positions = [(500.0 + i * 30, 1500.0, 0.0) for i in range(100)]
        entity._floor_plan = SimpleNamespace(
            carpet=[], borders=[],
            dock=(*positions[-1][:2], 0.0) if dock_at_end else None,
            room_names={}, room_polygons={},
        )
        entity._config_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(prime_positions=positions), options={}
        )
        image = Image.open(io.BytesIO(entity._render_png())).convert("RGB")
        return image, entity, positions

    @staticmethod
    def _distance(a, b):
        return sum(abs(x - y) for x, y in zip(a, b))

    def test_the_marker_is_big_enough_to_survive_scaling(self):
        """Contrast is useless if nothing is left to carry it.

        At radius 7 the marker was fourteen pixels on a 600-pixel
        render -- about four and a half on the card @chairstacker
        screenshotted, where a two-pixel ring is half a pixel. He still
        could not make out the robot after the colour was fixed, which
        is a size problem wearing a contrast problem's clothes.
        """
        image, entity, positions = self._render()

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        width = image.size[0]
        # Sample outwards until the marker ends.
        radius = next(
            offset for offset in range(1, 40)
            if image.getpixel((round(px), round(py) + offset))
            == image.getpixel((round(px), round(py) + 39))
        )
        assert radius >= 10, "marker radius shrank"
        # Roughly nine pixels on a 250-pixel dashboard card.
        assert (radius * 2) / width * 250 >= 8

    def test_the_dock_is_a_square_not_a_second_circle(self):
        """Both used to be circles within two pixels of each other, so a
        viewer who spotted one dot still could not tell which marker it
        was. Shape says it now, independently of size and colour."""
        image, entity, positions = self._render(dock_at_end=True)

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        # A square's diagonal corner is filled; a circle's is background.
        corner = image.getpixel((round(px) + 6, round(py) + 6))
        assert corner != image.getpixel((round(px) + 30, round(py) + 30))

    def test_the_marker_stands_out_from_the_trail(self):
        image, entity, positions = self._render()

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        tx, ty = entity._renderer._mm_to_px_fit(*positions[50][:2])
        trail = image.getpixel((round(tx), round(ty)))
        body = image.getpixel((round(px), round(py) + 4))

        # The old marker scored 90 here and was invisible to two people.
        assert self._distance(body, trail) > 180

    def test_a_white_ring_separates_it_from_whatever_is_underneath(self):
        """The ring is what makes this robust. A hue chosen to contrast
        with the trail would still vanish on a coverage fill or a room
        of the wrong colour; a white outline does not."""
        image, entity, positions = self._render()

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        # At the marker's edge -- radius 11 since the size fix, so -7
        # now lands in the fill rather than on the ring.
        assert image.getpixel((round(px), round(py) - 11)) == (255, 255, 255)

    def test_the_dock_also_carries_a_ring(self):
        """The dock reads clearly against the trail (245) and much less
        so against a cleaned area (145): yellow on the coverage green.
        Not the 90 that made the robot invisible, but a marker whose
        legibility depends on whether its own room has been cleaned yet
        is worth making unconditional."""
        image, entity, positions = self._render(dock_at_end=True)

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        # The robot sits on the dock here and is drawn last, so probe
        # the dock's ring where the robot's own circle does not reach.
        assert (255, 255, 255) in [
            image.getpixel((round(px) + dx, round(py)))
            for dx in (-8, 8)
        ]

    def test_a_docked_robot_is_not_hidden_by_the_dock(self):
        """The dock used to be drawn after the robot, so a robot sitting
        on it disappeared underneath."""
        image, entity, positions = self._render(dock_at_end=True)

        px, py = entity._renderer._mm_to_px_fit(*positions[-1][:2])
        assert image.getpixel((round(px), round(py) + 4)) == (20, 110, 220)


class TestTrailCountersMeasureSurvival:
    """`position_points` counts what the robot sent. Everything after it
    can still drop every sample: one without a `point`, or a point
    without x/y, is skipped silently.

    @DaRealGuGu's capture had 2267 messages and 5553 points and no robot
    marker on the map -- and no number in it could say whether they
    arrived and were dropped, or arrived and were drawn out of view.

    Same shape as every other counter mistake here: counting arrival
    instead of survival.
    """

    def _feed(self, samples):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        data = SimpleNamespace(
            prime_positions=[],
            live_map_stats={"position_points": 0},
        )
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data = data
        entity._feed_trail(SimpleNamespace(updates=samples))
        return data

    @staticmethod
    def _sample(x=None, y=None, with_point=True):
        """A TUPLE, because that is what the library produces.

        This fixture built `SimpleNamespace(x=..., y=...)` when it was
        written, matching the `getattr(point, "x")` the code did -- so
        the test agreed with the bug and passed while no robot ever got
        a trail point. Exactly what tests/prime_fixtures.py exists to
        prevent, made in a test rather than in the code, for the second
        time in this project.
        """
        from types import SimpleNamespace

        point = None if not with_point else (x, y)
        return SimpleNamespace(point=point, orientation=0.0)

    def test_good_samples_are_counted_as_added(self):
        data = self._feed([self._sample(1.0, 2.0), self._sample(1.1, 2.1)])

        assert data.live_map_stats["trail_points_added"] == 2
        assert len(data.prime_positions) == 2

    def test_a_sample_without_a_point_is_counted_as_skipped(self):
        data = self._feed([self._sample(with_point=False)])

        assert data.live_map_stats["trail_skipped_no_point"] == 1
        assert data.live_map_stats.get("trail_points_added", 0) == 0
        assert data.prime_positions == []

    def test_a_point_without_coordinates_is_counted_separately(self):
        """Two different failures deserve two different numbers: a
        missing point means the sample is shaped differently than
        expected, a missing x/y means the point is."""
        data = self._feed([self._sample(None, 2.0), self._sample(1.0, None)])

        assert data.live_map_stats["trail_skipped_no_xy"] == 2
        assert data.prime_positions == []

    def test_the_three_counters_account_for_every_sample(self):
        """The property that makes them useful: added plus the two skips
        equals what arrived, so a shortfall has exactly one place to
        hide."""
        samples = [
            self._sample(1.0, 2.0),
            self._sample(with_point=False),
            self._sample(None, 5.0),
            self._sample(1.1, 2.2),
        ]
        stats = self._feed(samples).live_map_stats

        total = (
            stats.get("trail_points_added", 0)
            + stats.get("trail_skipped_no_point", 0)
            + stats.get("trail_skipped_no_xy", 0)
        )
        assert total == len(samples)


class TestWatcherFailuresReachDiagnostics:
    """The outer retry loop logged and wrote nothing a capture could see.

    Only the DECODE path ever set `last_error`, so a diagnostics
    download showing every counter at zero and `last_error: null` could
    not distinguish "nothing arrived" from "this task has been crashing
    and retrying since setup".

    @chairstacker's capture was exactly that: all zeros, mid-mission, no
    error anywhere. His aside is what pointed here -- the map timestamp
    moved at roughly four-minute intervals instead of the two to twelve
    seconds he was used to, and `backoff` doubles from 5s to 300s.
    """

    def _entity(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity._blid = "BLID"
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data = SimpleNamespace(
            live_map_stats={"watch_failures": 0}
        )
        return entity

    def test_a_failure_is_counted_and_named(self):
        entity = self._entity()

        entity._record_watch_failure("ConnectionResetError()", 20.0)

        stats = entity._config_entry.runtime_data.live_map_stats
        assert stats["watch_failures"] == 1
        assert stats["last_watch_error"] == "ConnectionResetError()"
        assert stats["watch_retry_backoff_s"] == 20.0

    def test_repeated_failures_accumulate(self):
        """The count matters as much as the message: one failure at
        startup is noise, forty is the whole story."""
        entity = self._entity()

        for _ in range(40):
            entity._record_watch_failure("boom", 300.0)

        assert entity._config_entry.runtime_data.live_map_stats["watch_failures"] == 40

    def test_the_backoff_is_recorded_so_the_gap_is_explainable(self):
        """A four-minute gap between map updates is a grown backoff seen
        from the outside. Recording it turns a tester's impression into
        a number."""
        entity = self._entity()

        entity._record_watch_failure("boom", 300.0)

        assert entity._config_entry.runtime_data.live_map_stats[
            "watch_retry_backoff_s"
        ] == 300.0

    def test_missing_stats_do_not_raise(self):
        """This runs inside a background task -- an exception here would
        kill the very loop that is already failing."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        entity._blid = "B"
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data = SimpleNamespace(live_map_stats=None)

        entity._record_watch_failure("boom", 5.0)


class TestTheVersionCheckWatchesTheMapOnScreen:
    """It watched `p2maps[0]` while rendering a different map.

    `_async_refresh_rooms()` picks the map the robot says it is standing
    on and only falls back to the first when that is unknown. On a
    two-map account the two diverge, and then a change to the map being
    shown goes unnoticed while a change to the other one triggers a
    pointless refresh.

    @chairstacker has two maps and edited the older one -- exactly the
    arrangement where this shows.
    """

    _MAPS = [
        {"p2map_id": "WHOLE_HOUSE", "active_p2mapv_id": "v1"},
        {"p2map_id": "BATHROOM", "active_p2mapv_id": "v9"},
    ]

    async def _refresh(self, *, reported, rendered_map, rendered_version, maps=None):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._blid = "B"
        entity._rendered_for_map_version = rendered_version
        entity._rendered_map_id = rendered_map
        entity._async_refresh_rooms = AsyncMock()
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator = MagicMock(
            data={"ro-currentstate": {
                "p2maps": self._MAPS if maps is None else maps,
                "cleanMissionStatus": {"p2mapId": reported},
            }}
        )
        entity._config_entry = entry

        await entity._async_refresh_if_map_changed()
        return entity

    @pytest.mark.asyncio
    async def test_a_change_to_the_second_map_is_noticed(self):
        """The bug. Rendering the bathroom map, its version moves, and
        the old check compared Whole House's version instead."""
        entity = await self._refresh(
            reported="BATHROOM", rendered_map="BATHROOM", rendered_version="v8"
        )

        entity._async_refresh_rooms.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_change_to_a_map_not_on_screen_is_ignored(self):
        """The other half. Editing Whole House while the bathroom map is
        shown used to force a re-render for nothing."""
        maps = [
            {"p2map_id": "WHOLE_HOUSE", "active_p2mapv_id": "SOMETHING_NEW"},
            {"p2map_id": "BATHROOM", "active_p2mapv_id": "v9"},
        ]
        entity = await self._refresh(
            reported="BATHROOM", rendered_map="BATHROOM",
            rendered_version="v9", maps=maps,
        )

        entity._async_refresh_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_moving_between_floors_refreshes_even_at_the_same_version(self):
        """Neither map's version moves when the robot changes floors --
        only which one is rendered. Comparing the id is what catches
        it."""
        entity = await self._refresh(
            reported="BATHROOM", rendered_map="WHOLE_HOUSE", rendered_version="v9"
        )

        entity._async_refresh_rooms.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_unknown_reported_map_falls_back_to_the_first(self):
        """Same fallback the renderer uses -- parked or freshly booted,
        the robot reports no map at all."""
        entity = await self._refresh(
            reported=None, rendered_map="WHOLE_HOUSE", rendered_version="v1"
        )

        entity._async_refresh_rooms.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_single_map_account_is_unaffected(self):
        maps = [{"p2map_id": "ONLY", "active_p2mapv_id": "v3"}]
        entity = await self._refresh(
            reported="ONLY", rendered_map="ONLY", rendered_version="v3", maps=maps
        )

        entity._async_refresh_rooms.assert_not_awaited()


class TestTrailPointsAreActuallyAppended:
    """THE MISSING ROBOT MARKER, and it was two getattr() calls.

    `_feed_trail` read `getattr(sample.point, "x", None)` and skipped
    every sample when it came back None -- which it always did, because
    `PositionSample.point` is a plain `(x, y)` tuple. The trail list
    stayed empty on every robot, forever, while `position_points`
    counted the samples arriving.

    That is @DaRealGuGu's capture explained: 2267 messages, 5553 points,
    no marker. The marker is drawn only when this list is non-empty, and
    what he saw instead was the trajectories layer from the downloaded
    bundle -- which looks like a trail and is not one.

    The defensiveness is what hid it. An exception would have been found
    in a day; a silent `continue` written against an unverified shape
    survived every release.
    """

    def _feed(self, cur_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from roombapy_prime.models.livemap import PositionUpdateMessage

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        data = SimpleNamespace(prime_positions=[], live_map_stats={})
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data = data
        entity._trail_mission_id = None
        entity._feed_trail(PositionUpdateMessage.from_json({"cur_path": cur_path}))
        return data

    def test_a_real_message_produces_real_points(self):
        """Parsed by the library's own from_json, not hand-built -- the
        hand-built fixture is exactly what would have agreed with the
        bug."""
        data = self._feed([1, 1.5, 2.5, 0.0, 2.0, 3.5, 4.5, 0.1, 2.0, 1700000000])

        assert len(data.prime_positions) == 2
        assert data.live_map_stats["trail_points_added"] == 2

    def test_metres_become_millimetres(self):
        data = self._feed([1, 1.5, 2.5, 0.0, 2.0, 1700000000])

        x_mm, y_mm, _heading = data.prime_positions[0]
        assert (x_mm, y_mm) == (1500.0, 2500.0)

    def test_nothing_is_skipped_for_a_well_formed_message(self):
        """The counters have to add up: every arriving sample either
        lands in the trail or is counted as skipped, and a healthy
        message skips none."""
        data = self._feed([1, 1.5, 2.5, 0.0, 2.0, 3.5, 4.5, 0.1, 2.0, 1700000000])

        assert data.live_map_stats.get("trail_skipped_no_point", 0) == 0
        assert data.live_map_stats.get("trail_skipped_no_xy", 0) == 0

    def test_a_malformed_point_is_counted_rather_than_raising(self):
        """Still runs inside a coordinator callback -- a shape that
        cannot be unpacked must be counted, not thrown."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeMapImage

        entity = object.__new__(PrimeMapImage)
        data = SimpleNamespace(prime_positions=[], live_map_stats={})
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data = data
        entity._trail_mission_id = None

        entity._feed_trail(SimpleNamespace(updates=[
            SimpleNamespace(point="nonsense", orientation=0.0),
            SimpleNamespace(point=(1.0,), orientation=0.0),
        ]))

        assert data.prime_positions == []
        assert data.live_map_stats["trail_skipped_no_xy"] == 2


class TestTheImageFollowsTheSelection:
    """The select writes an id; the image has to read it -- in both the
    render and the version check.

    Those two diverging is the bug that was just fixed: watching one
    map's version while rendering another. Adding a third source of
    truth without wiring it into both would put it straight back.
    """

    def _entity(self, selected):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._blid = "B"
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_selected_map_id=selected)
        entity._config_entry = entry
        return entity

    def test_both_places_read_the_same_field(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        render = inspect.getsource(PrimeRoomsImage._async_refresh_rooms)
        check = inspect.getsource(PrimeRoomsImage._async_refresh_if_map_changed)

        assert "prime_selected_map_id" in render
        assert "prime_selected_map_id" in check

    @pytest.mark.asyncio
    async def test_the_version_check_watches_the_selected_map(self):
        from unittest.mock import AsyncMock, MagicMock

        entity = self._entity("M_BATH")
        entity._rendered_for_map_version = "v1"
        entity._rendered_map_id = "M_BATH"
        entity._async_refresh_rooms = AsyncMock()
        coordinator = MagicMock()
        coordinator.data = {"ro-currentstate": {
            "p2maps": [
                {"p2map_id": "M_HOUSE", "active_p2mapv_id": "v1"},
                {"p2map_id": "M_BATH", "active_p2mapv_id": "v2"},
            ],
            # The robot says it is on the house map; the user chose the
            # bathroom one, and the choice wins.
            "cleanMissionStatus": {"p2mapId": "M_HOUSE"},
        }}
        entity._config_entry.runtime_data.prime_status_coordinator = coordinator

        await entity._async_refresh_if_map_changed()

        entity._async_refresh_rooms.assert_awaited_once()
        assert entity._rendered_map_id == "M_BATH"


class TestTheTrailClearsOnANewMissionNumber:
    """It used to require catching a shadow update while the robot was
    cleaning, and a Prime robot may push `ro-currentstate` only around
    the edges of a run -- every field capture shows `phase: charge`.

    @chairstacker ended up with four missions' trails on one map and
    could only clear it by reloading the integration. The mission number
    had moved each time; we were not looking when it did.
    """

    def _coordinator(self, points=5):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator,
        )

        coordinator = object.__new__(PrimeStatusCoordinator)
        coordinator._trail_mission_id = None
        coordinator.hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "E"
        entry.runtime_data = SimpleNamespace(
            prime_positions=[(1.0, 2.0, 0.0)] * points,
            mission_timer_store=MagicMock(),
        )
        coordinator.config_entry = entry
        return coordinator

    @staticmethod
    def _shadow(phase, nmssn):
        return {
            "ro-currentstate": {
                "cleanMissionStatus": {"phase": phase, "nMssn": nmssn}
            }
        }

    def _positions(self, coordinator):
        return coordinator.config_entry.runtime_data.prime_positions

    def test_a_new_mission_clears_even_while_charging(self):
        """The case that was broken. Both captures around a mission read
        `charge`, and the number moved in between."""
        coordinator = self._coordinator()
        coordinator._note_phase_for_timer(self._shadow("charge", 306))
        self._positions(coordinator).extend([(1.0, 2.0, 0.0)] * 5)

        coordinator._note_phase_for_timer(self._shadow("charge", 307))

        assert self._positions(coordinator) == []

    def test_the_same_mission_keeps_its_trail(self):
        coordinator = self._coordinator()
        coordinator._note_phase_for_timer(self._shadow("run", 306))
        self._positions(coordinator).extend([(1.0, 2.0, 0.0)] * 5)

        coordinator._note_phase_for_timer(self._shadow("charge", 306))

        assert len(self._positions(coordinator)) == 5

    def test_a_robot_reporting_no_number_never_clears(self):
        """The fallback is inert on purpose: with no number the value
        never changes, which is the old behaviour rather than a trail
        that clears at random."""
        coordinator = self._coordinator()
        for phase in ("run", "charge", "run"):
            coordinator._note_phase_for_timer(
                {"ro-currentstate": {"cleanMissionStatus": {"phase": phase}}}
            )
            self._positions(coordinator).extend([(1.0, 2.0, 0.0)])

        assert len(self._positions(coordinator)) > 5

    def test_four_missions_leave_only_the_last(self):
        """His case, end to end."""
        coordinator = self._coordinator(points=0)
        for number in (306, 307, 308, 309):
            self._positions(coordinator).extend([(1.0, 2.0, 0.0)] * 10)
            coordinator._note_phase_for_timer(self._shadow("charge", number))

        assert self._positions(coordinator) == []


class TestTheDockIsDrawnWhereItWasSeen:
    """The map bundle's `dockPose` records where the dock stood when the
    map was BUILT, and nothing corrects it when the dock moves.

    @utkjmitch's map put the dock on a spot now occupied by a treadmill,
    while the iRobot app showed it correctly on the same map. `dockPose`
    is the only dock data the bundle carries, so the app reads something
    fresher -- and a robot reporting `charge` is standing on the dock,
    reporting its position through the same stream in the same
    coordinates.
    """

    def _image(self, *, observed=None, bundle=(1000.0, 2000.0, 0.0)):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_observed_dock=observed)
        entity._config_entry = entry
        entity._floor_plan = SimpleNamespace(dock=bundle)
        return entity

    def test_an_observation_wins_over_the_bundle(self):
        assert self._image(observed=(500.0, 600.0))._dock_position() == (500.0, 600.0)

    def test_the_bundle_is_the_fallback_not_a_last_resort(self):
        """Available immediately, on every account, without waiting for
        a mission to end. A remembered dock in roughly the right place
        beats no dock at all."""
        assert self._image()._dock_position() == (1000.0, 2000.0)

    def test_no_source_draws_nothing(self):
        assert self._image(bundle=None)._dock_position() is None

    def test_a_malformed_observation_falls_back(self):
        for bad in ((1.0,), "nope", 7):
            assert self._image(observed=bad)._dock_position() == (1000.0, 2000.0)


class TestTheDockIsLearnedWhileCharging:
    """Read at `charge` rather than at the end of the mission: the trail
    is cleared when the NEXT mission starts, so it is still intact here,
    and `charge` is the one phase where the robot is unambiguously on
    the dock rather than on its way to it."""

    def _coordinator(self, positions, phase="charge"):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator,
        )

        coordinator = object.__new__(PrimeStatusCoordinator)
        coordinator._trail_mission_id = "m1"
        coordinator.hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "E"
        entry.runtime_data = SimpleNamespace(
            prime_positions=positions,
            prime_observed_dock=None,
            mission_timer_store=MagicMock(),
        )
        coordinator.config_entry = entry
        shadows = {"ro-currentstate": {
            "cleanMissionStatus": {"phase": phase, "nMssn": 1}
        }}
        coordinator._note_phase_for_timer(shadows)
        return entry.runtime_data

    def test_the_last_point_while_charging_becomes_the_dock(self):
        data = self._coordinator([(10.0, 20.0, 0.0), (700.0, 800.0, 1.5)])

        assert data.prime_observed_dock == (700.0, 800.0)

    def test_a_running_robot_teaches_nothing(self):
        """It is somewhere in the middle of the floor."""
        data = self._coordinator([(700.0, 800.0, 0.0)], phase="run")

        assert data.prime_observed_dock is None

    def test_an_empty_trail_teaches_nothing(self):
        """A robot docked since startup has no positions, and the bundle
        value stands -- which is the behaviour this had all along."""
        data = self._coordinator([])

        assert data.prime_observed_dock is None


class TestTheBundleTrajectoriesFillAGap:
    """They do not compete with the live trail.

    Our own trail only exists while Home Assistant is watching -- a
    restart mid-mission loses it, while the bundle has the whole path
    because iRobot recorded it. That is what the layer is for.

    But the bundle also carries the PREVIOUS mission until the robot
    uploads a new one, which is why @chairstacker saw the old route
    persist as a thin line under the new one and could only clear it by
    reloading. Our trail had already been cleared; the bundle brought
    the old path back.
    """

    def _source(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        return inspect.getsource(PrimeRoomsImage)

    def test_the_layer_is_gated_on_having_our_own_points(self):
        """A SOURCE ASSERTION ON PURPOSE, and worth saying why.

        Observing this properly means rendering a PNG with a real bundle
        and counting drawn lines. An attempt to fake it in the test
        reimplemented the gate inside the test, which proves the test
        rather than the renderer -- strictly worse than reading the
        source.

        So this stays a source check, and it checks a DECISION (the
        renderer consults its own positions before drawing the bundle's
        layer) rather than an expression's spelling.
        """
        source = self._source()
        gate = source[source.index("have_own_trail"):]

        assert "prime_positions" in gate[:200]
        assert "trajectories" in source

    def test_the_gate_reads_the_live_positions(self):
        """The same list the trail is drawn from and that a new mission
        clears -- so the layer returns by itself when there is nothing
        of our own to show."""
        source = self._source()
        gate = source[source.index("have_own_trail = bool("):]

        assert "prime_positions" in gate[:200]


class TestZonesDoNotNeedARunningMission:
    """The live bundle arrives on a map-update message — only while a
    mission runs. @chairstacker ticked all three zone boxes, reloaded
    twice and saw nothing.

    That was correct behaviour and useless to him: **zones barely ever
    change, and nobody inspects their keep-out areas mid-clean.** The
    tick boxes promised something permanent and delivered something
    momentary.
    """

    def _resolve(self, live, stored):
        """The renderer's own lookup, in the two states that matter."""
        def _zone_layer(name):
            layer = (live or {}).get(name)
            if layer:
                return layer
            return (stored or {}).get(name)

        return _zone_layer

    def test_the_live_bundle_wins_while_a_mission_runs(self):
        resolve = self._resolve(
            {"policyZones": {"features": ["live"]}},
            {"policyZones": {"features": ["stored"]}},
        )

        assert resolve("policyZones")["features"] == ["live"]

    def test_the_stored_bundle_answers_when_the_robot_is_idle(self):
        """His case: no mission, no live bundle, and three ticked boxes
        that had nothing to draw."""
        resolve = self._resolve({}, {"policyZones": {"features": ["stored"]}})

        assert resolve("policyZones")["features"] == ["stored"]

    def test_neither_is_still_nothing(self):
        assert self._resolve({}, {})("policyZones") is None

    def test_the_floor_plan_keeps_all_three_files(self):
        import inspect

        from custom_components.roomba_plus import prime_room_map

        source = inspect.getsource(prime_room_map)
        assert '"cleanZones", "adHocCleanZones", "policyZones"' in source

    def test_the_renderer_consults_the_floor_plan(self):
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage)
        assert 'getattr(self._floor_plan, "zone_layers", None)' in source
    def test_live_bundle_storage_excludes_unrendered_layers(self):
        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity._current_map_id = "MAP-1"
        entity._live_bundle = {
            "coverage": {"features": []},
            "trajectories": {"features": []},
            "hazard": {"features": []},
            "rooms": {"features": []},
        }

        assert entity._live_bundle_save_payload() == {
            "map_id": "MAP-1",
            "bundle": {
                "coverage": {"features": []},
                "trajectories": {"features": []},
                "hazard": {"features": []},
            },
        }
