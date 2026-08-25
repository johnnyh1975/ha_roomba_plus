


def _make_entry(mission_store=None, cloud_coordinator=None, umf_aligner=None):
    entry = MagicMock()
    rd = MagicMock()
    rd.has_cloud = cloud_coordinator is not None
    rd.cloud_coordinator = cloud_coordinator
    rd.umf_aligner = umf_aligner
    rd.mission_store = mission_store
    rd.robot_profile_store = None
    entry.runtime_data = rd
    return entry



class TestAFinishedMissionReadsOneHundred:
    """@chairstacker (#72 follow-up): a favourite that completed
    successfully — 4 minutes, 20 sq ft, app header "Cleaning
    Completed" — froze at **34%**.

    The percentage is elapsed time against a per-room estimate, and his
    estimate was three times the real duration. Holding the last value
    (a38) was right for an aborted run and wrong for a completed one.
    """

    @staticmethod
    def _sensor(phase, last):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.sensor import (
            RoombaMissionProgress,
        )

        sensor = object.__new__(RoombaMissionProgress)
        sensor._last_progress = last
        sensor._last_mission_id = "m1"
        entry = MagicMock()
        entry.runtime_data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"phase": phase}
        }
        sensor._config_entry = entry
        return sensor, RoombaMissionProgress

    def test_docking_after_work_completes_it(self):
        sensor, RoombaMissionProgress = self._sensor("charge", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 100

    def test_returning_home_completes_it_too(self):
        sensor, RoombaMissionProgress = self._sensor("hmPostMsn", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 100

    def test_a_stopped_mission_keeps_what_it_reached(self):
        """`stop` is a mission halted where it stood. 34% is the honest
        figure there, and claiming 100 would be a lie."""
        sensor, RoombaMissionProgress = self._sensor("stop", 34)

        assert RoombaMissionProgress.native_value.fget(sensor) == 34

    def test_nothing_run_yet_stays_unknown(self):
        sensor, RoombaMissionProgress = self._sensor("charge", None)

        assert RoombaMissionProgress.native_value.fget(sensor) is None


# ============================================================================
# ROOM SENSORS -- areas, cleaning history, accessibility, last mission.
#
# Moved here from test_sensors.py (August 2026). All four entity classes
# are defined in this module.
#
# `_store_with` is COPIED: TestSensorSetupEntryCloud stays behind and
# still uses it.
# ============================================================================


from unittest.mock import MagicMock, patch  # noqa: E402
from custom_components.roomba_plus.mission_store import MissionStore  # noqa: E402

from custom_components.roomba_plus.sensor_rooms import (  # noqa: E402
    RoombaLastMissionSummarySensor,
    RoombaRoomAccessibilityScoresSensor,
    RoombaRoomAreasSensor,
    RoombaRoomCleaningHistorySensor,
)


def _store_with(*records) -> MissionStore:
    store = MissionStore()
    for r in records:
        store._records.append(r)
    return store



def _make_last_mission_summary_sensor(mission_store=None, cloud_coordinator=None, umf_aligner=None):
    """Return a RoombaLastMissionSummarySensor backed by the given store."""
    from custom_components.roomba_plus.sensor import RoombaLastMissionSummarySensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = _make_entry(
        mission_store=mission_store,
        cloud_coordinator=cloud_coordinator,
        umf_aligner=umf_aligner,
    )
    sensor = RoombaLastMissionSummarySensor.__new__(RoombaLastMissionSummarySensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_last_mission_summary"
    return sensor


class TestLastMissionSummarySensor:
    """LAST-MISSION-SUMMARY (v3.1.0) — sensor that exposes the latest mission record."""

    def test_empty_store_returns_none(self):
        """No records → native_value is None, all attributes are None."""
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with())
        assert sensor.native_value is None
        attrs = sensor.extra_state_attributes
        assert attrs["result"] is None
        assert attrs["duration_min"] is None
        assert attrs["area_sqft"] is None
        assert attrs["cleaned_rooms"] is None
        assert attrs["room_coverage"] is None
        assert attrs["started_at"] is None

    def test_no_store_returns_none(self):
        """MissionStore not initialised → native_value is None."""
        sensor = _make_last_mission_summary_sensor(mission_store=None)
        assert sensor.native_value is None
        assert sensor.extra_state_attributes["result"] is None

    def test_completed_mission_all_fields(self):
        """Completed mission → native_value = 'completed', attributes populated.

        v3.1.1 ROOM-COVERAGE-IN-SUMMARY: cleaned_rooms/room_coverage now come
        from MissionStore.latest_cleaned_rooms()/latest_room_coverage() via
        timeline.finEvents, not a literal "last_cleaned_rooms" record key
        (which never actually exists on a real record — that was the bug
        this fix corrected). Fixture below uses the same finEvents shape
        test_mission_store.py's region-map tests use.
        """
        record = {
            "result": "completed",
            "duration_min": 45,
            "area_sqft": 320.5,
            "cleaning_passes": 1,
            "battery_start_pct": 100,
            "battery_end_pct": 72,
            "recharges": 0,
            "dirt_events": 3,
            "evacuations": 1,
            "error_code": None,
            "initiator": "schedule",
            "started_at": "2026-06-29T08:00:00",
            "ended_at": "2026-06-29T08:45:00",
            "timeline": {
                "plan": {"upcoming": ["19", "21"]},
                "finEvents": [
                    {"type": "room", "room": {"rid": "19", "status": 0,
                                               "area": 100, "totalArea": 80}},
                    {"type": "room", "room": {"rid": "21", "status": 0,
                                               "area": 120, "totalArea": 90}},
                ],
            },
        }
        cc = MagicMock()
        cc.regions = [
            {"id": "19", "name": "Kitchen"},
            {"id": "21", "name": "Living Room"},
        ]
        sensor = _make_last_mission_summary_sensor(
            mission_store=_store_with(record), cloud_coordinator=cc,
        )

        assert sensor.native_value == "completed"
        attrs = sensor.extra_state_attributes
        assert attrs["duration_min"] == 45
        assert attrs["area_sqft"] == 320.5
        assert attrs["cleaned_rooms"] == ["Kitchen", "Living Room"]
        assert attrs["room_coverage"] == {"Kitchen": 0.8, "Living Room": 0.75}
        assert attrs["battery_start_pct"] == 100
        assert attrs["battery_end_pct"] == 72
        assert attrs["dirt_events"] == 3
        assert attrs["initiator"] == "schedule"
        assert attrs["started_at"] == "2026-06-29T08:00:00"

    def test_cleaned_rooms_and_coverage_none_without_region_map_or_umf(self):
        """v3.1.1: no cloud_coordinator and no aligned umf_aligner →
        cleaned_rooms/room_coverage stay None (no region source to resolve
        room names — same gate as vacuum.py's attribute computation).
        """
        record = {
            "result": "completed",
            "timeline": {
                "plan": {"upcoming": ["19"]},
                "finEvents": [
                    {"type": "room", "room": {"rid": "19", "status": 0,
                                               "area": 100, "totalArea": 80}},
                ],
            },
        }
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with(record))
        attrs = sensor.extra_state_attributes
        assert attrs["cleaned_rooms"] is None
        assert attrs["room_coverage"] is None

    def test_cleaned_rooms_and_coverage_use_umf_fallback(self):
        """v3.1.1: EPHEMERAL robots without cloud use the aligned UmfAligner's
        rid_to_name() as a fallback region source, same as vacuum.py.
        """
        record = {
            "result": "completed",
            "timeline": {
                "plan": {"upcoming": ["5"]},
                "finEvents": [
                    {"type": "room", "room": {"rid": "5", "status": 0,
                                               "area": 50, "totalArea": 40}},
                ],
            },
        }
        umf = MagicMock()
        umf.aligned = True
        umf.rid_to_name.return_value = {"5": "Hallway"}
        sensor = _make_last_mission_summary_sensor(
            mission_store=_store_with(record), umf_aligner=umf,
        )

        attrs = sensor.extra_state_attributes
        assert attrs["cleaned_rooms"] == ["Hallway"]
        assert attrs["room_coverage"] == {"Hallway": 0.8}

    def test_error_mission_error_code_populated(self):
        """Error mission → native_value = 'error', error_code present."""
        record = {
            "result": "error",
            "error_code": 11,
            "duration_min": 5,
            "area_sqft": 0,
        }
        sensor = _make_last_mission_summary_sensor(mission_store=_store_with(record))
        assert sensor.native_value == "error"
        assert sensor.extra_state_attributes["error_code"] == 11

    def test_returns_latest_record_not_first(self):
        """With multiple records, native_value reflects the last (most recent) record."""
        older = {"result": "cancelled", "duration_min": 10}
        newer = {"result": "completed", "duration_min": 55}
        sensor = _make_last_mission_summary_sensor(
            mission_store=_store_with(older, newer)
        )
        assert sensor.native_value == "completed"
        assert sensor.extra_state_attributes["duration_min"] == 55


# ─────────────────────────────────────────────────────────────────────────────
# ROOM-CLEANING-HISTORY (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _make_room_cleaning_history_sensor(mission_store=None):
    """Return a RoombaRoomCleaningHistorySensor backed by the given store."""
    from custom_components.roomba_plus.sensor import RoombaRoomCleaningHistorySensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = _make_entry(mission_store=mission_store)
    sensor = RoombaRoomCleaningHistorySensor.__new__(RoombaRoomCleaningHistorySensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_room_cleaning_history"
    return sensor


class TestRoomCleaningHistorySensor:
    """ROOM-CLEANING-HISTORY (v3.1.0) — per-room last-clean timestamps."""

    def test_empty_store_returns_zero(self):
        """No records → native_value = 0, attributes = {}."""
        sensor = _make_room_cleaning_history_sensor(mission_store=_store_with())
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_no_store_returns_zero(self):
        """MissionStore not initialised → native_value = 0."""
        sensor = _make_room_cleaning_history_sensor(mission_store=None)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_single_mission_populates_rooms(self):
        """Record with last_cleaned_rooms → each room gets ended_at timestamp."""
        record = {
            "last_cleaned_rooms": ["Kitchen", "Living Room"],
            "ended_at": "2026-06-29T08:45:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(mission_store=_store_with(record))
        assert sensor.native_value == 2
        attrs = sensor.extra_state_attributes
        assert attrs["Kitchen"] == "2026-06-29T08:45:00"
        assert attrs["Living Room"] == "2026-06-29T08:45:00"

    def test_newest_record_wins_per_room(self):
        """Multiple records — each room shows its most recent ended_at."""
        older = {
            "last_cleaned_rooms": ["Kitchen", "Hallway"],
            "ended_at": "2026-06-27T09:00:00",
            "result": "completed",
        }
        newer = {
            "last_cleaned_rooms": ["Kitchen", "Living Room"],
            "ended_at": "2026-06-29T08:45:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(
            mission_store=_store_with(older, newer)
        )
        attrs = sensor.extra_state_attributes
        # Kitchen: newer record wins
        assert attrs["Kitchen"] == "2026-06-29T08:45:00"
        # Hallway: only in older record
        assert attrs["Hallway"] == "2026-06-27T09:00:00"
        # Living Room: only in newer record
        assert attrs["Living Room"] == "2026-06-29T08:45:00"
        assert sensor.native_value == 3

    def test_record_without_rooms_is_skipped(self):
        """Records lacking last_cleaned_rooms (whole-home missions) are skipped."""
        whole_home = {
            "last_cleaned_rooms": None,
            "ended_at": "2026-06-28T10:00:00",
            "result": "completed",
        }
        room_mission = {
            "last_cleaned_rooms": ["Bedroom"],
            "ended_at": "2026-06-29T08:00:00",
            "result": "completed",
        }
        sensor = _make_room_cleaning_history_sensor(
            mission_store=_store_with(whole_home, room_mission)
        )
        assert sensor.native_value == 1
        assert "Bedroom" in sensor.extra_state_attributes


# ─────────────────────────────────────────────────────────────────────────────
# ROOM-SIZE / room_areas (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

def _make_room_areas_sensor(umf_aligner=None, regions=None):
    """Return a RoombaRoomAreasSensor with the given aligner and cc.regions."""
    from custom_components.roomba_plus.sensor import RoombaRoomAreasSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    rd = MagicMock()
    cc = MagicMock()
    cc.regions = regions or []
    rd.umf_aligner = umf_aligner
    rd.cloud_coordinator = cc if umf_aligner is not None else None
    entry.runtime_data = rd
    sensor = RoombaRoomAreasSensor.__new__(RoombaRoomAreasSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_room_areas"
    return sensor


class TestIdToDisplayName:
    """v3.2.0 ROOM-TYPE-SUGGEST — _id_to_display_name(), the shared
    fallback resolver used by ROOM-SIZE and ROOM-ACCESS."""

    def test_none_coordinator_returns_empty(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        assert _id_to_display_name(None) == {}

    def test_user_set_name_used_directly(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = [{"id": "1", "name": "Kitchen"}]
        cc.region_suggestions = []
        assert _id_to_display_name(cc) == {"1": "Kitchen"}

    def test_falls_back_to_top_suggestion_when_unnamed(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = []
        cc.region_suggestions = [
            {"region_id": "1", "suggested_types": [
                {"region_type": "living_room", "score": 0.62},
                {"region_type": "office", "score": 0.17},
            ]},
        ]
        result = _id_to_display_name(cc)
        assert result["1"] == "Living Room"

    def test_user_name_never_overridden_by_suggestion(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = [{"id": "1", "name": "My Custom Room"}]
        cc.region_suggestions = [
            {"region_id": "1", "suggested_types": [
                {"region_type": "living_room", "score": 0.9},
            ]},
        ]
        result = _id_to_display_name(cc)
        assert result["1"] == "My Custom Room"

    def test_negative_top_score_not_used(self):
        """A negative score (confirmed real in field data — see
        MISSIONSTORE_FIELD_REGISTRY.md) means "probably NOT this type",
        not just "less confident" — must not be used as a label."""
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = []
        cc.region_suggestions = [
            {"region_id": "1", "suggested_types": [
                {"region_type": "dining_room", "score": -0.9484},
            ]},
        ]
        result = _id_to_display_name(cc)
        assert "1" not in result

    def test_picks_highest_scored_type_among_multiple(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = []
        cc.region_suggestions = [
            {"region_id": "2", "suggested_types": [
                {"region_type": "hallway", "score": 1.8449},
                {"region_type": "bathroom", "score": -0.9081},
            ]},
        ]
        result = _id_to_display_name(cc)
        assert result["2"] == "Hallway"

    def test_no_suggestions_no_fallback(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = []
        cc.region_suggestions = []
        assert _id_to_display_name(cc) == {}

    def test_missing_suggested_types_skipped_gracefully(self):
        from custom_components.roomba_plus.sensor import _id_to_display_name
        cc = MagicMock()
        cc.regions = []
        cc.region_suggestions = [{"region_id": "1", "suggested_types": []}]
        assert _id_to_display_name(cc) == {}


class TestRoomAreasSensor:
    """ROOM-SIZE (v3.1.0) — per-room floor area dictionary sensor."""

    def test_no_aligner_returns_zero(self):
        """umf_aligner=None → native_value=0, attributes={}."""
        sensor = _make_room_areas_sensor(umf_aligner=None)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_areas_translated_to_display_names(self):
        """rid keys from room_areas_m2 are translated via cc.regions."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {"19": 14.3, "21": 22.1}
        regions = [
            {"id": "19", "name": "Kitchen"},
            {"id": "21", "name": "Living Room"},
        ]
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=regions)
        assert sensor.native_value == 2
        attrs = sensor.extra_state_attributes
        assert attrs["Kitchen"] == 14.3
        assert attrs["Living Room"] == 22.1

    def test_unknown_rid_falls_back_to_rid(self):
        """rid without a matching region entry is used as-is as key."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {"99": 8.5}
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=[])
        assert "99" in sensor.extra_state_attributes
        assert sensor.extra_state_attributes["99"] == 8.5

    def test_empty_room_polygons_returns_zero(self):
        """Aligner present but no polygons resolved → native_value=0."""
        aligner = MagicMock()
        aligner.room_areas_m2 = {}
        sensor = _make_room_areas_sensor(umf_aligner=aligner, regions=[])
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}


def _make_room_accessibility_sensor(
    umf_aligner=None, grid_store=None, mission_archive=None, regions=None,
):
    """Return a RoombaRoomAccessibilityScoresSensor with the given
    collaborators wired in — mirrors _make_room_areas_sensor's pattern."""
    from custom_components.roomba_plus.sensor import RoombaRoomAccessibilityScoresSensor
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {}}}
    entry = MagicMock()
    rd = MagicMock()
    cc = MagicMock()
    cc.regions = regions or []
    rd.umf_aligner = umf_aligner
    rd.cloud_coordinator = cc if umf_aligner is not None else None
    rd.grid_store = grid_store
    rd.mission_archive = mission_archive
    entry.runtime_data = rd
    sensor = RoombaRoomAccessibilityScoresSensor.__new__(RoombaRoomAccessibilityScoresSensor)
    sensor._roomba = roomba
    sensor._blid = "test_blid"
    sensor._entry = entry
    sensor._attr_unique_id = "test_blid_room_accessibility_scores"
    return sensor


class TestRoomAccessibilityScoresSensor:
    """ROOM-ACCESS (v3.2.0) — per-room accessibility score dict sensor."""

    def test_no_aligner_returns_zero(self):
        sensor = _make_room_accessibility_sensor(umf_aligner=None)
        assert sensor.native_value == 0
        assert sensor.extra_state_attributes == {}

    def test_no_polygons_returns_zero(self):
        aligner = MagicMock()
        aligner.room_polygons_umf = {}
        sensor = _make_room_accessibility_sensor(umf_aligner=aligner)
        assert sensor.native_value == 0

    def test_coverage_only_scores_via_gridstore(self):
        """No grid_store/mission_archive at all — still scores from
        whatever's available (coverage requires grid_store though, so this
        specifically checks the "gs is None" branch degrades to {})."""
        aligner = MagicMock()
        aligner.room_polygons_umf = {"1": [(0, 0), (100, 0), (100, 100)]}
        aligner.room_areas_m2 = {"1": 10.0}
        sensor = _make_room_accessibility_sensor(
            umf_aligner=aligner, grid_store=None, mission_archive=None,
        )
        # No grid_store -> no coverage/stuck signal, no mission_archive ->
        # no time signal -> room_accessibility_scores gets nothing at all
        # for this rid -> native_value 0.
        assert sensor.native_value == 0

    def test_full_pipeline_with_display_name_translation(self):
        aligner = MagicMock()
        aligner.room_polygons_umf = {"1": [(0, 0), (100, 0), (100, 100)]}
        aligner.room_areas_m2 = {"1": 10.0}

        gs = MagicMock()
        gs.coverage_by_polygon.return_value = {"1": 0.9}
        gs.stuck_by_polygon.return_value = {"1": 2}

        archive = MagicMock()
        archive.all_derived_oldest_first.return_value = [
            {"room_visits": [{"rid": "1", "ts": 0}, {"rid": "1", "ts": 60}]},
        ]

        regions = [{"id": "1", "name": "Kitchen"}]
        sensor = _make_room_accessibility_sensor(
            umf_aligner=aligner, grid_store=gs, mission_archive=archive,
            regions=regions,
        )
        assert sensor.native_value == 1
        attrs = sensor.extra_state_attributes
        assert "Kitchen" in attrs
        assert attrs["Kitchen"]["score"] is not None
        assert "limiting_factor" in attrs["Kitchen"]

    def test_room_polygons_umf_accessed_as_property_not_method(self):
        """Regression guard, real bug (field-caught via BouIIIx's debug
        log, 2026-07-04): sensor.py called aligner.room_polygons_umf()
        with parentheses, but UmfAligner declares it as a @property (a
        dict, not callable) — crashed with 'dict' object is not callable
        on every entity setup. A bare MagicMock() masks this entirely
        (any attribute access "succeeds"); spec=UmfAligner restricts the
        mock to the real class's actual interface, so calling a real
        property as a method raises the same TypeError production code
        would raise.
        """
        from custom_components.roomba_plus.umf_aligner import UmfAligner

        aligner = MagicMock(spec=UmfAligner)
        aligner.room_polygons_umf = {"1": [(0, 0), (100, 0), (100, 100)]}
        aligner.room_areas_m2 = {"1": 10.0}
        sensor = _make_room_accessibility_sensor(
            umf_aligner=aligner, grid_store=None, mission_archive=None,
        )
        # Must not raise TypeError: 'dict' object is not callable.
        sensor.native_value

    def test_unknown_rid_falls_back_to_rid(self):
        aligner = MagicMock()
        aligner.room_polygons_umf = {"99": [(0, 0), (100, 0), (100, 100)]}
        aligner.room_areas_m2 = {"99": 5.0}
        gs = MagicMock()
        gs.coverage_by_polygon.return_value = {"99": 1.0}
        gs.stuck_by_polygon.return_value = {}
        sensor = _make_room_accessibility_sensor(
            umf_aligner=aligner, grid_store=gs, mission_archive=None, regions=[],
        )
        assert "99" in sensor.extra_state_attributes

    def test_no_mission_archive_still_scores_from_coverage(self):
        """time_per_area signal absent (no mission_archive) shouldn't
        block coverage/stuck-based scoring."""
        aligner = MagicMock()
        aligner.room_polygons_umf = {"1": [(0, 0), (100, 0), (100, 100)]}
        aligner.room_areas_m2 = {"1": 10.0}
        gs = MagicMock()
        gs.coverage_by_polygon.return_value = {"1": 1.0}
        gs.stuck_by_polygon.return_value = {}
        sensor = _make_room_accessibility_sensor(
            umf_aligner=aligner, grid_store=gs, mission_archive=None,
        )
        assert sensor.native_value == 1
        assert sensor.extra_state_attributes["1"]["score"] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# PRIMARY-SLIM (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────


# ============================================================================
# ROOMS-OVERDUE and DIRT-CORRELATION sensors.
#
# Moved here from test_sensors.py (August 2026). Both entity classes are
# defined in this module; each carries its own `self._sensor` builder,
# so only `_rec` came with them -- COPIED, since tests elsewhere still
# build records with it.
# ============================================================================


def _rec(done="done", done_raw="done", pause_id=0, chrgs=0, evacs=0,
         dirt=0, timestamp=1700000000, classified=None):
    """Build a minimal raw record dict with classified_result pre-computed."""
    r = {
        "done":      done,
        "done_raw":  done_raw,
        "pauseId":   pause_id,
        "chrgs":     chrgs,
        "evacs":     evacs,
        "dirt":      dirt,
        "timestamp": timestamp,
    }
    if classified is None:
        from custom_components.roomba_plus.cloud_coordinator import classify_mission_result
        classified = classify_mission_result(r)
    r["classified_result"] = classified
    return r



class TestRoomsOverdueSensor:
    """v3.3.0 ROOM-SCHED — state/attributes wiring of the merged rule
    plus the DIRT-VEL self-calibration attributes."""

    def _sensor(self, records, options=None, rps=None):
        from custom_components.roomba_plus.sensor import RoombaRoomsOverdueSensor
        from custom_components.roomba_plus.mission_store import MissionStore
        entity = object.__new__(RoombaRoomsOverdueSensor)
        entry = MagicMock()
        entry.options = options or {}
        ms = MissionStore()
        ms._records = records
        data = entry.runtime_data
        data.mission_store = ms
        data.has_cloud = True
        data.cloud_coordinator.regions = [
            {"id": "7", "name": "Kitchen"}, {"id": "9", "name": "Hall"},
        ]
        data.robot_profile_store = rps
        entity._config_entry = entry
        return entity

    @staticmethod
    def _rec(i, ended, rids):
        return {"id": f"m_{i}", "ended_at": ended,
                "timeline": {"finEvents": [
                    {"type": "room", "room": {"rid": r, "status": 0}}
                    for r in rids]}}

    def test_state_counts_overdue_and_attrs_expose_merge(self):
        recs = [self._rec(i, f"2026-06-{d}T10:00:00+00:00", ["7"])
                for i, d in enumerate(["20", "22", "24", "26"])]
        sensor = self._sensor(
            recs, options={"room_schedule": {"Kitchen": "daily"}}
        )
        with patch("custom_components.roomba_plus.sensor_rooms.dt_util") as dt_m:
            from homeassistant.util import dt as real_dt
            dt_m.now.return_value = real_dt.parse_datetime(
                "2026-07-04T10:00:00+00:00"
            )
            assert sensor.native_value == 1
            attrs = sensor.extra_state_attributes
        k = attrs["rooms"]["Kitchen"]
        assert k["source"] == "configured" and k["status"] == "overdue"
        assert attrs["overdue_rooms"] == ["Kitchen"]

    def test_self_calibration_attributes_with_name_resolution(self):
        from custom_components.roomba_plus.robot_profile_store import (
            RobotProfileStore,
        )
        rps = RobotProfileStore()
        rps.room_dirt_index = {"7": 3.0, "9": 1.0}    # median 2.0
        rps.room_dirt_velocity = {"7": 2.0, "9": 0.25}
        sensor = self._sensor([], rps=rps)
        attrs = sensor.extra_state_attributes
        # rid → display name resolved; Kitchen suggested daily (1.0 < 1.5)
        assert attrs["suggested_interval_days"] == {"Kitchen": 1.0, "Hall": 8.0}
        assert attrs["daily_suggested"] == ["Kitchen"]
        # Already configured daily → drops out of the suggestion
        sensor._config_entry.options = {"room_schedule": {"Kitchen": "daily"}}
        assert sensor.extra_state_attributes["daily_suggested"] == []


class TestDirtCorrelationSensor:
    """v3.3.0 CROSS-CORR — |r| > 0.3 sensor gate and strongest-entity
    selection."""

    def _sensor(self, results):
        from custom_components.roomba_plus.sensor import (
            RoombaDirtCorrelationSensor,
        )
        from custom_components.roomba_plus.robot_profile_store import (
            RobotProfileStore,
        )
        entity = object.__new__(RoombaDirtCorrelationSensor)
        entry = MagicMock()
        rps = RobotProfileStore()
        rps.correlation_results = MagicMock(return_value=results)
        entry.runtime_data.robot_profile_store = rps
        entity._config_entry = entry
        return entity

    def test_gate_and_strongest_selection(self):
        s = self._sensor({
            "sensor.humidity": {"r": 0.61, "n": 42},
            "sensor.pollen": {"r": -0.72, "n": 35},
            "sensor.temp": {"r": 0.10, "n": 50},      # below |0.3| gate
            "sensor.new": {"r": None, "n": 12},       # below n gate
        })
        # Strongest |r| wins — the negative pollen correlation
        assert s.native_value == -0.72
        attrs = s.extra_state_attributes
        assert attrs["strongest_entity"] == "sensor.pollen"
        assert attrs["by_entity"]["sensor.new"] == {"r": None, "n": 12}

    def test_none_when_nothing_passes(self):
        s = self._sensor({
            "sensor.temp": {"r": 0.25, "n": 60},
            "sensor.new": {"r": None, "n": 5},
        })
        assert s.native_value is None
        assert s.extra_state_attributes["strongest_entity"] is None




class TestPrimeRoomsOverdueSensor:
    """Prime had no overdue sensor -- the Classic one sits behind a
    `map_capability` check in a branch CLOUD_ONLY entries never reach.

    It matters more here than on Classic: Prime robots have zones as
    well as rooms (@chairstacker: seven rooms, twelve zones), so there
    is more to fall behind, and watching for that is his stated use for
    the integration.
    """

    @staticmethod
    def _sensor(merged):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.sensor_rooms import (
            PrimeRoomsOverdueSensor,
        )

        entry = MagicMock()
        with patch.object(
            PrimeRoomsOverdueSensor, "robot_unique_id", "BLID1"
        ), patch(
            "custom_components.roomba_plus.sensor_rooms.IRobotEntity.__init__",
            return_value=None,
        ):
            sensor = PrimeRoomsOverdueSensor("BLID1", entry)
        sensor._config_entry = entry
        sensor._merged = lambda: merged
        return sensor

    def test_it_counts_only_the_overdue_ones(self):
        sensor = self._sensor({
            "Kitchen": {"status": "overdue"},
            "Hall": {"status": "ok"},
            "Sofa corner": {"status": "overdue"},
        })

        assert sensor.native_value == 2

    def test_nothing_overdue_is_zero_not_unknown(self):
        """Zero is a real answer and a useful one; unknown reads as a
        broken sensor."""
        sensor = self._sensor({"Kitchen": {"status": "ok"}})

        assert sensor.native_value == 0

    def test_its_unique_id_is_distinct_from_the_classic_one(self):
        sensor = self._sensor({})

        assert sensor._attr_unique_id == "BLID1_prime_rooms_overdue"

    def test_it_subscribes_so_the_count_moves(self):
        """The Classic sensor is refreshed by the local state push.
        Prime has none -- without a subscription the count would be
        read once at start-up and never move."""
        from unittest.mock import AsyncMock, MagicMock, patch

        sensor = self._sensor({})
        coordinator = sensor._config_entry.runtime_data.prime_status_coordinator
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        sensor.async_on_remove = MagicMock()

        import asyncio

        with patch(
            "custom_components.roomba_plus.sensor_rooms.RoombaRoomsOverdueSensor"
            ".async_added_to_hass",
            AsyncMock(),
        ):
            asyncio.run(sensor.async_added_to_hass())

        coordinator.async_add_listener.assert_called_once()


class TestSuggestedIntervalsResolveAcrossMaps:
    """@dduff617 (#94, follow-up): the history fix landed and this
    neighbour kept raw ids.

    `suggested_interval_days` resolved through `_region_maps_for` — the
    active-map view, filtered on purpose so a room COMMAND cannot go to
    the wrong floor (#8). A suggested interval is not a command. It is a
    statement about a room that exists somewhere, and on his four-map
    household most rooms are not on whichever map is active.

    Same distinction the history fix rested on: naming a room is not
    deciding where to send the robot.
    """

    @staticmethod
    def _attrs(active, per_map, suggested):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.sensor_rooms import (
            RoombaRoomsOverdueSensor,
        )

        sensor = RoombaRoomsOverdueSensor.__new__(RoombaRoomsOverdueSensor)
        entry = MagicMock()
        entry.options = {}
        sensor._config_entry = entry
        sensor.vacuum = MagicMock()

        data = MagicMock()
        rps = MagicMock()
        rps.suggested_cleaning_interval_days.return_value = suggested
        data.robot_profile_store = rps
        ms = MagicMock()
        ms.rooms_overdue_merged.return_value = {}
        data.mission_store = ms
        entry.runtime_data = data

        with patch(
            "custom_components.roomba_plus.sensor_rooms._region_maps_for",
            return_value=(active, None),
        ), patch(
            "custom_components.roomba_plus.sensor_rooms._regions_by_pmap_for",
            return_value=per_map,
        ):
            return sensor.extra_state_attributes

    def test_a_room_on_another_map_gets_its_name(self):
        attrs = self._attrs(
            active={"10": "Kitchen"},
            per_map={"MAP-B": {"2": "Pantry", "5": "Foyer"}},
            suggested={"10": 3, "2": 7, "5": 14},
        )

        assert "Pantry" in attrs["suggested_interval_days"]
        assert "5" not in attrs["suggested_interval_days"]

    def test_the_active_map_wins_a_collision(self):
        """It is the map the user is looking at."""
        attrs = self._attrs(
            active={"2": "Study"},
            per_map={"MAP-B": {"2": "Pantry"}},
            suggested={"2": 7},
        )

        assert "Study" in attrs["suggested_interval_days"]

    def test_an_unknown_id_still_survives_as_itself(self):
        """Better a number than a dropped room."""
        attrs = self._attrs(
            active={}, per_map={}, suggested={"99": 5},
        )

        assert attrs["suggested_interval_days"] == {"99": 5}
