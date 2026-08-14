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

import dataclasses

import logging
from .structural_failures import record_failure, record_success
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Prime coordinates are metres, the renderer works in millimetres.
METRES_TO_MM = 1000.0


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
            (float(x) * METRES_TO_MM, float(y) * METRES_TO_MM)
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
    #: {room_id: name} from rooms.geojson, where the app appears to read
    #: them. Empty when the bundle has no rooms layer.
    #: The raw `cleanZones`, `adHocCleanZones` and `policyZones` files,
    #: unparsed. The renderer knows their shape; this only has to keep
    #: them so they are available when no mission is running.
    room_names: dict[str, str]
    #: {room_id: ring_mm} from rooms.geojson. On several robots this is
    #: the ONLY place outlines exist -- get_map_metadata() returns names
    #: and cleaning defaults but no geometry.
    room_polygons: dict[str, list[tuple[float, float]]]
    #: Fine-grained floor-plan outlines, including interior walls and
    #: doorway cut-outs.  These are separate from cleanable room polygons.
    floor_plan: list[list[tuple[float, float]]]
    borders: list[list[tuple[float, float]]]
    #: Carpeted areas. The only observed floor_type value is "carpet",
    #: which suggests the file lists carpet rather than classifying every
    #: surface -- anything uncovered is hard floor by omission. Not
    #: confirmed: a robot with no carpet would settle it.
    carpet: list[list[tuple[float, float]]]
    #: Detected furniture outlines from the saved map.
    furniture: list[list[tuple[float, float]]]
    #: Dock position and which way it faces, or None.
    dock: tuple[float, float, float] | None
    #: The raw `cleanZones`, `adHocCleanZones` and `policyZones` files,
    #: unparsed. The renderer knows their shape; this only has to keep
    #: them so they are available when no mission is running.
    zone_layers: dict[str, Any] = dataclasses.field(default_factory=dict)
    #: Which map this plan describes.
    #:
    #: NEEDED FOR THE MULTI-MAP CASE. The renderer prefers the LIVE
    #: bundle's zone layers over the saved ones, and the live bundle
    #: belongs to whichever map the robot is currently DRIVING -- not
    #: necessarily the one the dropdown selected.
    #:
    #: @chairstacker selected Master_Bathroom while a Whole_House
    #: mission ran and saw Whole_House's clean and keep-out boundaries
    #: drawn over the bathroom. Two sets of maps switching correctly,
    #: one set of zones that was simply on or off.
    #:
    #: Without an id on both sides there is nothing to compare, so the
    #: renderer had no way to notice.
    p2map_id: str | None = None


def _room_names_from_bundle(features: Any) -> dict[str, str]:
    """{room_id: name} from the bundle's rooms layer.

    A SECOND SOURCE, and on at least one account the more complete one.
    @DaRealGuGu's N185240 returns four rooms from get_map_metadata();
    three carry a name and the fourth carries none -- yet his iRobot app
    shows all four, the fourth being "Cuisine". The app is reading a name
    this endpoint does not return, and rooms.geojson carries `name` per
    feature.

    @jouwdan reached the same conclusion from a Roomba Max 705 by a
    different route (PR #63).

    Used as the PREFERRED source where present, because it is the one
    that matches what the user sees in the app. Metadata names remain the
    fallback: they are field-confirmed on three accounts and this layer
    is not guaranteed to exist.
    """
    if isinstance(features, dict) and features.get("type") == "Feature":
        features = {"features": [features]}

    names: dict[str, str] = {}
    for feature in (features or {}).get("features") or []:
        properties = feature.get("properties") or {}
        room_id = (
            feature.get("id")
            or properties.get("room_id")
            or properties.get("id")
        )
        name = properties.get("name")
        if room_id is not None and name:
            names[str(room_id)] = str(name)
    return names


def _room_polygons_from_bundle(
    features: Any,
) -> dict[str, list[tuple[float, float]]]:
    """{room_id: ring in mm} from the bundle's rooms layer.

    ON SEVERAL ROBOTS THIS IS THE ONLY SOURCE. get_map_metadata()
    returns names, types and cleaning defaults per room, but no
    geometry -- confirmed on a G185020 (@chairstacker, rooms map
    permanently unavailable) and an N185240 (@DaRealGuGu, metadata with
    no geometry field). @jouwdan reported the same from a Roomba Max 705
    and said outright that outlines live in rooms.geojson (PR #63).

    Three robots, three routes, one answer -- and the rooms map was
    unavailable on all of them because it looked in the metadata only.
    """
    if isinstance(features, dict) and features.get("type") == "Feature":
        features = {"features": [features]}

    polygons: dict[str, list[tuple[float, float]]] = {}
    for feature in (features or {}).get("features") or []:
        properties = feature.get("properties") or {}
        room_id = (
            feature.get("id")
            or properties.get("room_id")
            or properties.get("id")
        )
        if room_id is None:
            continue
        rings = rings_mm({"features": [feature]})
        if rings:
            polygons[str(room_id)] = rings[0]
    return polygons


def rings_mm(
    features: Any, *, include_holes: bool = False
) -> list[list[tuple[float, float]]]:
    """GeoJSON polygon rings in millimetres.

    Floor-plan holes are structural outlines too, so callers can retain
    them with ``include_holes``.  Other layers intentionally use only an
    outer ring.
    """
    rings: list[list[tuple[float, float]]] = []

    # A BARE FEATURE IS ALSO VALID GeoJSON, and at least one robot sends
    # one: the Roomba Max 705 returns its border layer as a single
    # Feature rather than a FeatureCollection (reported by @jouwdan,
    # PR #63).
    #
    # Reading only `features` silently yielded nothing on that robot --
    # no error, just a map without walls. Normalised here so every
    # caller gets the same shape.
    if isinstance(features, dict) and features.get("type") == "Feature":
        features = {"features": [features]}

    for feature in (features or {}).get("features") or []:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        kind = geometry.get("type")
        # MultiPolygon nests one level deeper than Polygon.
        polygons = coords if kind == "MultiPolygon" else [coords]
        for polygon in polygons:
            for coordinates in (polygon if include_holes else polygon[:1]):
                try:
                    ring = [
                        (float(x) * METRES_TO_MM, float(y) * METRES_TO_MM)
                        for x, y in coordinates
                    ]
                except (TypeError, ValueError, IndexError):
                    continue
                if len(ring) >= 3:
                    rings.append(ring)
    return rings


def lines_mm(features: Any) -> list[list[tuple[float, float]]]:
    """GeoJSON line paths in millimetres, retaining wall openings."""
    lines: list[list[tuple[float, float]]] = []
    if isinstance(features, dict) and features.get("type") == "Feature":
        features = {"features": [features]}

    for feature in (features or {}).get("features") or []:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        kind = geometry.get("type")
        if kind == "MultiLineString":
            paths = coords
        elif kind == "LineString":
            paths = [coords]
        elif kind == "Polygon":
            paths = coords
        elif kind == "MultiPolygon":
            paths = [ring for polygon in coords for ring in polygon]
        else:
            paths = []
        for path in paths:
            try:
                line = [
                    (float(x) * METRES_TO_MM, float(y) * METRES_TO_MM)
                    for x, y in path
                ]
            except (TypeError, ValueError, IndexError):
                continue
            if len(line) >= 2:
                if kind in {"Polygon", "MultiPolygon"} and line[0] != line[-1]:
                    line.append(line[0])
                lines.append(line)
    return lines


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
        carpet.extend(rings_mm({"features": [feature]}))
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
            x * METRES_TO_MM,
            y * METRES_TO_MM,
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
    empty = PrimeFloorPlan(
        room_names={}, room_polygons={}, floor_plan=[], borders=[], carpet=[],
        furniture=[], dock=None, p2map_id=p2map_id,
    )
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
        record_success("map bundle read")
    except Exception:  # noqa: BLE001
        record_failure("map bundle read", "downloading the bundle")
        _LOGGER.debug(
            "roomba_plus: could not read the map bundle for %s", p2map_id, exc_info=True
        )
        return empty

    # EVERY FILE IS OPTIONAL, and that is confirmed rather than
    # defensive. APK analysis: P2MapBundleContentHolder carries
    # `Map<String, byte[]> featureData` whose keys come from manifest.json
    # at runtime, and FeatureType is a Java generic parameter rather than
    # an enumeration -- there is no fixed set of files a bundle can hold.
    # The server decides per map which features it generates.
    #
    # Confirmed in the field too: one account's bundle has five files
    # (borders, dockPose, manifest, metadata, rooms) and another's has
    # eight, and the five-file set INCLUDES two that were previously
    # taken for extras of the eight-file set. It is not a per-SKU split.
    #
    # So: .get() throughout, absent means empty, and nobody should ever
    # add a required-file list here.
    # KEPT WHERE OTHER READERS CAN SEE THEM.
    #
    # The calendar builds its summaries from the schedule coordinator's
    # names and showed `Zone 10` for rooms this bundle names correctly
    # (@utkjmitch, a four-map account). One fetch, two consumers.
    names_for_others = _room_names_from_bundle(parsed.get("rooms"))
    runtime = getattr(config_entry, "runtime_data", None)
    if runtime is not None and names_for_others:
        existing = dict(getattr(runtime, "prime_room_names", None) or {})
        existing.update(names_for_others)
        runtime.prime_room_names = existing

    plan = PrimeFloorPlan(
        p2map_id=p2map_id,
        # THE ZONE LAYERS, KEPT RATHER THAN DROPPED.
        #
        # The renderer read them from the LIVE bundle, which arrives on
        # a map-update message -- that is, only while a mission runs.
        # @chairstacker ticked the three zone boxes, reloaded twice, and
        # saw nothing: correct, because a reload does not produce a live
        # bundle. Only driving does.
        #
        # Zones barely ever change, and nobody checks their keep-out
        # areas while the robot is mid-clean. This bundle is fetched
        # whenever the room map is built and carries the same three
        # files, so it is the right fallback.
        zone_layers={
            name: parsed.get(name)
            for name in ("cleanZones", "adHocCleanZones", "policyZones")
            if parsed.get(name)
        },
        room_names=_room_names_from_bundle(parsed.get("rooms")),
        room_polygons=_room_polygons_from_bundle(parsed.get("rooms")),
        floor_plan=lines_mm(parsed.get("floorPlan")),
        borders=rings_mm(parsed.get("borders")),
        carpet=_carpet_rings_mm(parsed.get("floorTypes")),
        furniture=rings_mm(parsed.get("furniture")),
        dock=_dock_from(parsed.get("dockPose")),
    )
    _LOGGER.debug(
        "roomba_plus: floor plan for %s -- %d border(s), %d carpet area(s), dock %s",
        p2map_id, len(plan.borders), len(plan.carpet),
        "found" if plan.dock else "absent",
    )
    return plan


#: NO MAPPING FROM OPERATING MODE TO PROFILE NAME. Removed 31 July 2026.
#:
#: There was one here, built from a single account:
#:
#:     2 -> normal   4 -> normal   32 -> light   512 -> deep
#:
#: A second account disproved it. @DaRealGuGu's N185240 reports
#: operatingMode 2 with profile "smart" where @chairstacker's G185020
#: reports the same mode as "normal". The number does not determine the
#: name, and "smart" was not in the table at all.
#:
#: The profile string is read straight from the robot's own
#: operating_mode_defaults instead. When it is absent, no profile is
#: reported -- guessing one from the mode number is precisely what turned
#: out to be wrong.


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
        # Read, never derived. See the note where the mode-to-profile
        # table used to be.
        profile = settings.get("profile")
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
