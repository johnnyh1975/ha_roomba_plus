"""Prime room maps, built the way Classic builds them.

WHAT CLASSIC DOES, AND WHY IT MATTERS HERE.

`RoombaRoomsImage` draws room polygons onto a dark canvas with rotating
per-room fill colours, and does **not** draw the room names into the
image. That is deliberate: since v2.7.3 the names are exposed as entity
ATTRIBUTES instead, because the xiaomi-vacuum-map-card renders its own
name overlay from them. Drawing them into the PNG as well would double
them up.

That is worth stating plainly, because "add a room map with names"
sounds like it means labels in the image, and for Classic it means the
opposite: coloured polygons in the image, names in the attributes.

So this file produces exactly the two things the Classic path consumes:

  - `{room_id: [(x_mm, y_mm), ...]}` -- polygons, for rendering
  - `{room_id: name}` -- for the attribute payload

Everything downstream (canvas, fill palette, outline colour, auto-fit,
the attribute shape the card expects) is the existing Classic code.

UNITS. Prime reports metres; the renderer works in millimetres. Getting
that wrong collapses every room into a few pixels and produces a map
that looks broken rather than empty, so the conversion is a named
constant rather than an inline 1000.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Prime coordinates are metres, the renderer works in millimetres.
_METRES_TO_MM = 1000.0


def _ring_mm(geometry: Any) -> list[tuple[float, float]]:
    """A room's outer ring, converted to millimetres.

    Only `coordinates[0]`: interior rings are ignored, which matches
    what the app does elsewhere. A room with a hole would render filled,
    and for a floor plan that is the right answer.
    """
    coords = getattr(geometry, "coordinates", None)
    if not coords:
        return []
    try:
        ring = [
            (float(x) * _METRES_TO_MM, float(y) * _METRES_TO_MM)
            for x, y in coords[0]
        ]
    except (TypeError, ValueError, IndexError):
        # Present but not a coordinate ring. Returning [] lets the next
        # geometry candidate be tried instead of masking it.
        return []
    return ring if len(ring) >= 3 else []


def _geometry_candidates(room: Any) -> list[Any]:
    """Geometry sources for one room, in order of preference.

    `simplified_geometry` first when the cloud supplies one: it is the
    app's own reduced outline, so using it keeps our rendering closer to
    what the user sees in the iRobot app, with fewer points to draw.
    """
    props = getattr(room, "properties", None)
    return [
        getattr(props, "simplified_geometry", None) if props else None,
        getattr(props, "geometry", None) if props else None,
        getattr(room, "simplified_geometry", None),
        getattr(room, "geometry", None),
    ]


async def async_build_prime_room_polygons(
    config_entry: RoombaConfigEntry, p2map_id: str
) -> tuple[
    dict[str, list[tuple[float, float]]],
    dict[str, str],
    dict[str, dict[str, Any]],
]:
    """Room polygons in millimetres, and their names.

    Returns the two structures the Classic rooms-map path already
    consumes, so nothing about rendering or the attribute payload needs
    a Prime variant.

    A room whose geometry cannot be read is omitted rather than kept:
    keeping it would put an entry in the card's room list that has no
    outline to highlight.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    if robot is None:
        return {}, {}, {}

    preferences: dict[str, dict[str, Any]] = {}
    try:
        map_data = await robot.get_map_metadata(p2map_id)
        # PREFERENCES COME FROM THIS SAME RESPONSE. Reading them in a
        # separate function meant a second get_map_metadata() per map
        # refresh -- for identical data, while the comment beside it
        # claimed "so no extra request".
        #
        # Returned alongside rather than fetched again, because the
        # caller wants both every time.
        preferences.update(
            room_cleaning_preferences(getattr(map_data, "rooms_metadata", None))
        )
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "roomba_plus: could not read room geometry for map %s", p2map_id,
            exc_info=True,
        )
        return {}, {}, {}

    polygons: dict[str, list[tuple[float, float]]] = {}
    names: dict[str, str] = {}

    for room in getattr(map_data, "rooms_metadata", None) or []:
        room_id = getattr(room, "room_id", None)
        if not room_id:
            continue

        ring: list[tuple[float, float]] = []
        for candidate in _geometry_candidates(room):
            ring = _ring_mm(candidate)
            if ring:
                break
        if not ring:
            continue

        polygons[str(room_id)] = ring
        # A NAME IS NOT GUARANTEED. Two real captures differ: one
        # account's rooms_metadata carries `name` for every room
        # ("Salon", "Bureau", "Couloir"), another's carries none at all
        # -- same firmware family, same endpoint.
        #
        # Stored as an empty string rather than skipped: the room still
        # has an outline worth drawing, and the caller supplies its own
        # "Room <id>" fallback for the label. Dropping unnamed rooms
        # would leave holes in the floor plan.
        names[str(room_id)] = getattr(room, "name", "") or f"Room {room_id}"

    _LOGGER.debug(
        "roomba_plus: built %d Prime room polygon(s) for map %s",
        len(polygons), p2map_id,
    )
    return polygons, names, preferences


def prime_calibration_points(
    polygons_mm: dict[str, list[tuple[float, float]]],
    mm_to_px_fn: Any,
) -> list[dict[str, dict[str, float]]] | None:
    """Three anchor pairs for xiaomi-vacuum-map-card.

    WHY PRIME NEEDS ITS OWN, AND WHY IT IS SHORTER.

    UmfAligner.calibration_points() does the same job for Classic, but
    it is gated on `self._aligned` -- the state reached once the cloud's
    UMF map has been fitted onto the robot's pose coordinate space. That
    fitting is the hard part of the Classic path, and Prime does not
    have it: the cloud hands over polygons already in the robot's own
    coordinates. There is nothing to align, so the gate would simply
    never open.

    ANCHOR CHOICE IS COPIED DELIBERATELY, including the reasoning.
    Classic used the dock origin (0, 0) as its first anchor until
    v2.7.2, and for a robot docked in a corner -- against a wall, which
    is where people put them -- that point maps OUTSIDE the rendered
    image and corrupts the card's affine transform. Three bounding-box
    corners are always inside it.

    That bug would have reproduced here exactly: Prime map origins are
    wherever the robot first docked, so (0, 0) is a corner far more
    often than not.
    """
    all_points = [pt for ring in polygons_mm.values() for pt in ring]
    if not all_points:
        return None

    xs = [x for x, _ in all_points]
    ys = [y for _, y in all_points]
    anchors_mm = [
        (min(xs), min(ys)),
        (max(xs), min(ys)),
        (max(xs), max(ys)),
    ]

    result: list[dict[str, dict[str, float]]] = []
    for x_mm, y_mm in anchors_mm:
        px_x, px_y = mm_to_px_fn(x_mm, y_mm)
        result.append({
            "vacuum": {"x": x_mm, "y": y_mm},
            "map": {"x": px_x, "y": px_y},
        })
    return result


@dataclass(frozen=True)
class PrimeFloorPlan:
    """The parts of a Prime map bundle worth drawing.

    All three came from the same tester capture and are field-confirmed
    on two accounts, which matters: they were modelled from decompiled
    serializer classes and never checked against real data until 30 July
    2026 -- the same position `set_virtual_wall` was in while it looked
    complete and failed for months.

    Everything is millimetres by the time it lands here.
    """

    #: Wall and boundary areas. MultiPolygon on the wire, so these are
    #: AREAS rather than lines -- confirmed, and worth stating because
    #: guessing lines would draw thin strokes where solid regions belong.
    borders: list[list[tuple[float, float]]]
    #: Carpeted areas. The only observed floor_type value is "carpet",
    #: which suggests the file lists carpet rather than classifying every
    #: surface -- anything uncovered is hard floor by omission. Not
    #: confirmed: a robot with no carpet would settle it.
    carpet: list[list[tuple[float, float]]]
    #: Dock position and which way it faces, or None.
    dock: tuple[float, float, float] | None


def _rings_mm(features: Any) -> list[list[tuple[float, float]]]:
    """Every outer ring in a GeoJSON FeatureCollection, in millimetres.

    Handles Polygon and MultiPolygon in one pass, because borders use the
    latter and floor types the former, and the difference is not worth
    two functions.
    """
    rings: list[list[tuple[float, float]]] = []
    for feature in (features or {}).get("features") or []:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        kind = geometry.get("type")
        # MultiPolygon nests one level deeper than Polygon.
        polygons = coords if kind == "MultiPolygon" else [coords]
        for polygon in polygons:
            if not polygon:
                continue
            try:
                ring = [
                    (float(x) * _METRES_TO_MM, float(y) * _METRES_TO_MM)
                    for x, y in polygon[0]
                ]
            except (TypeError, ValueError, IndexError):
                continue
            if len(ring) >= 3:
                rings.append(ring)
    return rings


def _carpet_rings_mm(features: Any) -> list[list[tuple[float, float]]]:
    """Rings whose properties say "carpet".

    Filtered rather than taking everything: the file may one day list
    other surfaces, and colouring hard floor as carpet is worse than
    drawing nothing.

    THE WIRE KEY IS `type`, not `floor_type`. A GeoJSON feature already
    has three other `type` keys around it -- the collection's, the
    feature's and the geometry's -- which is why the library names the
    attribute differently, and why a tester asked to grep for
    "floor_type" found nothing.
    """
    carpet: list[list[tuple[float, float]]] = []
    for feature in (features or {}).get("features") or []:
        if (feature.get("properties") or {}).get("type") != "carpet":
            continue
        carpet.extend(_rings_mm({"features": [feature]}))
    return carpet


def _dock_from(features: Any) -> tuple[float, float, float] | None:
    """Dock position and orientation, in millimetres and radians."""
    for feature in (features or {}).get("features") or []:
        coords = (feature.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        try:
            x, y = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue
        orientation = (feature.get("properties") or {}).get("orientation")
        return (
            x * _METRES_TO_MM,
            y * _METRES_TO_MM,
            float(orientation) if orientation is not None else 0.0,
        )
    return None


async def async_build_prime_floor_plan(
    config_entry: RoombaConfigEntry, p2map_id: str, p2mapv_id: str
) -> PrimeFloorPlan:
    """Walls, carpet and the dock, from the map bundle.

    A SEPARATE CLOUD CALL from the room polygons: rooms come from
    get_map_metadata(), this needs the bundle downloaded and unpacked.
    Kept separate rather than merged so a bundle failure costs the floor
    plan and not the rooms -- the rooms are what the map is for.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    empty = PrimeFloorPlan(borders=[], carpet=[], dock=None)
    if robot is None:
        return empty

    try:
        from roombapy_prime.models.map_bundle import parse_map_bundle  # noqa: PLC0415

        link = await robot.get_map_geojson_link(p2map_id, p2mapv_id)
        url = link.get("map_url") or next(
            (v for v in link.values() if isinstance(v, str) and v.startswith("http")),
            None,
        )
        if not url:
            return empty
        parsed = parse_map_bundle(await robot.download_map_bundle(url))
    except Exception:  # noqa: BLE001
        _LOGGER.debug(
            "roomba_plus: could not read the map bundle for %s", p2map_id, exc_info=True
        )
        return empty

    plan = PrimeFloorPlan(
        borders=_rings_mm(parsed.get("borders")),
        carpet=_carpet_rings_mm(parsed.get("floorTypes")),
        dock=_dock_from(parsed.get("dockPose")),
    )
    _LOGGER.debug(
        "roomba_plus: floor plan for %s -- %d border(s), %d carpet area(s), dock %s",
        p2map_id, len(plan.borders), len(plan.carpet),
        "found" if plan.dock else "absent",
    )
    return plan


#: Operating-mode numbers to the profile names the iRobot app shows.
#:
#: CONFIRMED from two independent captures of `operating_mode_defaults`,
#: where each room stores a profile string alongside the mode number.
#: Anything outside this set is reported by its number rather than
#: guessed at -- the robot may have modes nobody has observed.
OPERATING_MODE_PROFILES: dict[str, str] = {
    "2": "normal", "4": "normal", "32": "light", "512": "deep",
}


def room_cleaning_preferences(rooms: Any) -> dict[str, dict[str, Any]]:
    """Per-room cleaning preferences the user set in the iRobot app.

    READ ONLY, deliberately. The obvious next step is a service that
    writes these, and that would be wrong: the robot already stores a
    preference per room per mode, set by hand in the app, and a service
    call overriding it discards that with no way back.

    Surfacing them instead lets an automation HONOUR what the user
    configured -- "clean the kitchen the way I set it up" rather than
    "clean the kitchen on deep because the automation says so".

    Returns {room_id: {profile, suction_level, two_pass, carpet_boost,
    scrub}}, omitting whatever a given room does not carry. An absent
    key means the robot did not report it, which is different from a
    zero.
    """
    preferences: dict[str, dict[str, Any]] = {}
    for room in rooms or []:
        room_id = getattr(room, "room_id", None)
        if room_id is None:
            continue
        mode = getattr(room, "last_operating_mode", None)
        defaults = getattr(room, "operating_mode_defaults", None) or {}
        # The defaults are keyed by mode, and last_operating_mode says
        # which one was actually used. Reading any other key would
        # report a setting for a mode the room is not in.
        settings = defaults.get(str(mode)) if mode is not None else None
        if not isinstance(settings, dict):
            continue

        entry: dict[str, Any] = {}
        profile = settings.get("profile") or OPERATING_MODE_PROFILES.get(str(mode))
        if profile:
            entry["profile"] = profile
        for wire_key, attr in (
            ("suctionLevel", "suction_level"),
            ("twoPass", "two_pass"),
            ("carpetBoost", "carpet_boost"),
            ("swScrub", "scrub"),
        ):
            if wire_key in settings:
                entry[attr] = settings[wire_key]
        if entry:
            entry["operating_mode"] = mode
            preferences[str(room_id)] = entry
    return preferences
