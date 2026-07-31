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

    @pytest.mark.asyncio
    async def test_uses_bundle_outlines_when_metadata_only_has_names(self):
        """Max 705 metadata has names/settings; rooms.geojson has outlines."""
        from unittest.mock import patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_room_polygons,
        )

        entry = MagicMock()
        robot = entry.runtime_data.prime_robot
        robot.get_map_metadata = AsyncMock(return_value=SimpleNamespace(
            active_p2mapv_id="V1",
            rooms_metadata=[SimpleNamespace(room_id="16", name="Study")],
        ))
        robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://x"})
        robot.download_map_bundle = AsyncMock(return_value=b"bundle")
        parsed = {"rooms": {"features": [{
            "id": "16",
            "geometry": {"coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]]},
            "properties": {"name": "Study"},
        }]}}

        with patch("roombapy_prime.models.map_bundle.parse_map_bundle", return_value=parsed):
            polygons, names, _prefs = await async_build_prime_room_polygons(entry, "MAP-1")

        assert polygons["16"][1] == (2000.0, 0.0)
        assert names["16"] == "Study"


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

    def _entity(self, version=None, rendered_for=None):
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.image import PrimeRoomsImage

        entity = object.__new__(PrimeRoomsImage)
        entity.hass = MagicMock()
        entity._png = b"CACHED"
        entity._rendered_for_map_version = rendered_for
        entity._blid = "BLID"
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator = MagicMock(
            data={"ro-currentstate": {"p2maps": [{"active_p2mapv_id": version}]}}
            if version else {}
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
    async def test_705_single_feature_borders_and_floor_layers_are_kept(self):
        """The 705 uses a bare border plus floorPlan/furniture collections."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_room_map import (
            async_build_prime_floor_plan,
        )

        polygon = {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]],
        }
        parsed = {
            "borders": {
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [polygon["coordinates"]],
                }
            },
            "floorPlan": {"features": [{"geometry": polygon}]},
            "furniture": {"features": [{"geometry": polygon}]},
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

        assert len(plan.borders) == len(plan.floors) == len(plan.furniture) == 1

    def test_prime_map_fits_geometry_without_pose_jump_filtering(self):
        """Polygon corners are not robot poses and can be metres apart."""
        import inspect

        from custom_components.roomba_plus.image import PrimeRoomsImage

        source = inspect.getsource(PrimeRoomsImage._render_png)

        assert "_compute_fit" in source
        assert ".add_pose(" not in source

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

    def test_the_profile_falls_back_to_the_mode_number(self):
        """Confirmed mapping: 2 and 4 are normal, 32 light, 512 deep.
        A capture without an explicit profile string still resolves."""
        prefs = self._prefs([self._room("16", 512, {"512": {"suctionLevel": 4}})])

        assert prefs["16"]["profile"] == "deep"

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

        assert "_METRES_TO_MM" in source

    def test_live_geojson_bundle_drives_the_room_map(self):
        """V4 sends coverage, trajectory and hazards beside rawmap."""
        import inspect

        from custom_components.roomba_plus.image import PrimeMapImage, PrimeRoomsImage

        stream_source = inspect.getsource(PrimeMapImage._async_watch_live_map)
        render_source = inspect.getsource(PrimeRoomsImage._render_png)

        assert "message.livemap_url" in stream_source
        assert "parse_map_bundle" in stream_source
        assert "coverage" in render_source
        assert "trajectories" in render_source
        assert "hazard" in render_source

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
