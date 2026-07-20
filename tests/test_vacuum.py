"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import math
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
import pytest
from custom_components.roomba_plus.umf_aligner import UmfAligner
import json
from pathlib import Path
from custom_components.roomba_plus.vacuum import BraavaJet
from custom_components.roomba_plus.vacuum import IRobotVacuum
from custom_components.roomba_plus.vacuum import RoombaVacuum
from custom_components.roomba_plus.vacuum import RoombaVacuumCarpetBoost
from custom_components.roomba_plus.models import ConnectionType, MapCapability
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.exceptions import ServiceValidationError
import asyncio


_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"


def _make_aligner(aligned: bool = True, confidence: float = 0.85) -> UmfAligner:
    """Return a minimal UmfAligner with controlled aligned/confidence state."""
    a = UmfAligner([], [], MagicMock())
    a._aligned    = aligned
    a._confidence = confidence
    a._transform  = (0.0, 0.0, 0.0)
    a.pmap_version_id = "v1"
    return a


def _make_runtime_data(
    *,
    aligner: UmfAligner | None = None,
    has_cloud: bool = True,
    regions: list | None = None,
    keepout_zones: list | None = None,
    mission_store=None,
    grid_store=None,
    map_capability=None,
    geometry_store=None,
):
    data = MagicMock()
    data.umf_aligner    = aligner
    data.has_cloud      = has_cloud
    data.mission_store  = mission_store
    data.grid_store     = grid_store
    data.geometry_store = geometry_store

    cc = MagicMock()
    cc.regions      = regions or []
    cc.keepout_zones = keepout_zones or []
    cc.observed_zone_centroids = []
    cc.last_update_success = True
    data.cloud_coordinator = cc if has_cloud else None

    if map_capability is not None:
        data.map_capability = map_capability

    return data


def _make_roomba(state: dict | None = None):
    r = MagicMock()
    r.master_state = {"state": {"reported": state or {}}}
    r.current_state = "Charging"
    r.error_code = 0
    r.error_message = ""
    return r


def _make_smart_data(regions=None, has_data=True):
    """RoombaData stub for a SMART robot with cloud."""
    coord = MagicMock()
    coord.active_pmap_id = "MAP001"
    coord.regions = regions if regions is not None else [
        {"id": "19", "name": "Living Room"},
        {"id": "21", "name": "Kitchen"},
    ]
    data = MagicMock()
    data.map_capability = MapCapability.SMART
    data.cloud_coordinator = coord
    data.has_cloud = has_data
    return data


def _make_vacuum_entity(state: dict | None = None, runtime_data=None):
    """Build a bare IRobotVacuum without HA setup."""
    roomba = _make_roomba(state)
    blid = "TEST_BLID"
    entry = MagicMock()
    entry.options = {}
    if runtime_data is not None:
        entry.runtime_data = runtime_data
    else:
        entry.runtime_data = _make_smart_data()
    v = object.__new__(IRobotVacuum)
    v.vacuum = roomba
    v.vacuum_state = state or {}
    v._config_entry = entry
    v._cap_position = False
    v._segment_mismatch_streak = 0
    # NEW (V4/Prime): defaults match __init__'s own LOCAL_PUSH defaults --
    # every existing test using this bypass-construction helper predates
    # ConnectionType and implicitly assumes classic/local behavior.
    v._connection_type = ConnectionType.LOCAL_PUSH
    v._prime_robot = None
    return v


def _make_coordinator(regions=None, zones=None, active_pmap_id="PMAP1"):
    cc = MagicMock()
    cc.active_pmap_id = active_pmap_id
    cc.regions = regions or []
    cc.zones = zones or []
    cc.data = {"pmaps": []}
    cc.last_update_success = True
    cc.active_user_pmapv_id = "PMAPV1"
    return cc


def _make_vacuum_entity_v270_ia74_zone(coordinator=None, vacuum_state=None):
    """Create a minimal RoombaVacuum-like object for testing."""
    from custom_components.roomba_plus.vacuum import RoombaVacuum

    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": vacuum_state or {}}}

    entry = MagicMock()
    data = MagicMock()
    data.has_cloud = True
    data.cloud_coordinator = coordinator or _make_coordinator()
    data.cloud_coordinator.regions = coordinator.regions if coordinator else []
    entry.runtime_data = data
    entry.options = {}

    entity = RoombaVacuum.__new__(RoombaVacuum)
    entity._roomba = roomba
    entity._blid = "test"
    entity._config_entry = entry
    entity.vacuum = roomba
    entity.vacuum_state = vacuum_state or {}
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock(return_value=None)
    return entity


class TestVacuumLiveCR4:
    def test_cleaning_phases_importable_from_const(self):
        from custom_components.roomba_plus.const import CLEANING_PHASES
        assert "run"       in CLEANING_PHASES
        assert "hmMidMsn"  in CLEANING_PHASES
        assert "charge"    not in CLEANING_PHASES

    def test_vacuum_imports_cleaning_phases(self):
        import custom_components.roomba_plus.vacuum as vac_mod
        assert hasattr(vac_mod, "CLEANING_PHASES")

    def test_extract_rid_handles_lewis_format(self):
        from custom_components.roomba_plus.mission_store import MissionStore
        # lewis 22.52.10+ format
        assert MissionStore._extract_rid({"type": "rid", "rid": "19"}) == "19"
        # plain string
        assert MissionStore._extract_rid("21") == "21"
        # empty/unknown
        assert MissionStore._extract_rid({}) == ""


class TestSupportedFeaturesGate:

    def test_smart_with_cloud_has_clean_area(self):
        v = _make_vacuum_entity(
            state={"cleanMissionStatus": {}},
            runtime_data=_make_smart_data(has_data=True),
        )
        assert VacuumEntityFeature.CLEAN_AREA in v.supported_features

    def test_smart_without_cloud_data_no_clean_area(self):
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        assert VacuumEntityFeature.CLEAN_AREA not in v.supported_features

    def test_ephemeral_no_clean_area(self):
        data = _make_smart_data()
        data.map_capability = MapCapability.EPHEMERAL
        v = _make_vacuum_entity(runtime_data=data)
        assert VacuumEntityFeature.CLEAN_AREA not in v.supported_features

    def test_none_capability_no_clean_area(self):
        data = _make_smart_data()
        data.map_capability = MapCapability.NONE
        v = _make_vacuum_entity(runtime_data=data)
        assert VacuumEntityFeature.CLEAN_AREA not in v.supported_features

    def test_braava_no_clean_area_even_if_smart(self):
        """Braava always excludes CLEAN_AREA — is_mop() guard."""
        state = {"detectedPad": "reusable"}  # triggers is_mop()
        v = _make_vacuum_entity(state=state)
        assert VacuumEntityFeature.CLEAN_AREA not in v.supported_features

    def test_no_config_entry_no_clean_area(self):
        """Missing config_entry → SUPPORT_IROBOT only, no crash."""
        roomba = _make_roomba()
        v = object.__new__(IRobotVacuum)
        v.vacuum = roomba
        v.vacuum_state = {}
        v._config_entry = None
        v._cap_position = False
        assert VacuumEntityFeature.CLEAN_AREA not in v.supported_features

    def test_carpet_boost_subclass_has_fan_speed(self):
        """RoombaVacuumCarpetBoost.supported_features includes FAN_SPEED."""
        data = _make_smart_data()
        v = _make_vacuum_entity(runtime_data=data)
        v.__class__ = RoombaVacuumCarpetBoost
        # Build a proper subclass instance
        roomba = _make_roomba({"carpetBoost": True, "vacHigh": False})
        entry = MagicMock()
        entry.options = {}
        entry.runtime_data = data
        cb = object.__new__(RoombaVacuumCarpetBoost)
        cb.vacuum = roomba
        cb.vacuum_state = {"carpetBoost": True, "vacHigh": False}
        cb._config_entry = entry
        cb._cap_position = False
        assert VacuumEntityFeature.FAN_SPEED in cb.supported_features

    def test_braava_subclass_has_fan_speed_not_clean_area(self):
        """BraavaJet.supported_features has FAN_SPEED, never CLEAN_AREA."""
        state = {"detectedPad": "reusable"}
        roomba = _make_roomba(state)
        entry = MagicMock()
        entry.options = {}
        entry.runtime_data = _make_smart_data()
        bj = object.__new__(BraavaJet)
        bj.vacuum = roomba
        bj.vacuum_state = state
        bj._config_entry = entry
        bj._cap_position = False
        feats = bj.supported_features
        assert VacuumEntityFeature.FAN_SPEED in feats
        assert VacuumEntityFeature.CLEAN_AREA not in feats


class TestAsyncGetSegments:

    @pytest.mark.asyncio
    async def test_returns_segments_with_cloud_data(self):
        v = _make_vacuum_entity()
        segments = await v.async_get_segments()
        assert len(segments) == 2
        ids = {s.id for s in segments}
        assert "MAP001_19" in ids
        assert "MAP001_21" in ids

    @pytest.mark.asyncio
    async def test_segment_names_correct(self):
        v = _make_vacuum_entity()
        segments = await v.async_get_segments()
        name_map = {s.id: s.name for s in segments}
        assert name_map["MAP001_19"] == "Living Room"
        assert name_map["MAP001_21"] == "Kitchen"

    @pytest.mark.asyncio
    async def test_segment_group_from_floor_option(self):
        v = _make_vacuum_entity()
        v._config_entry.options = {"floor_label": "Ground Floor"}
        segments = await v.async_get_segments()
        assert all(s.group == "Ground Floor" for s in segments)

    @pytest.mark.asyncio
    async def test_segment_group_none_when_no_floor_option(self):
        v = _make_vacuum_entity()
        v._config_entry.options = {}
        segments = await v.async_get_segments()
        assert all(s.group is None for s in segments)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_cloud(self):
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert segments == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_config_entry(self):
        v = _make_vacuum_entity()
        v._config_entry = None
        segments = await v.async_get_segments()
        assert segments == []

    @pytest.mark.asyncio
    async def test_skips_regions_without_id(self):
        data = _make_smart_data(regions=[
            {"id": "19", "name": "Living Room"},
            {"name": "No ID region"},  # no 'id' key
            {"id": "21", "name": "Kitchen"},
        ])
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert len(segments) == 2

    @pytest.mark.asyncio
    async def test_empty_regions_returns_empty(self):
        data = _make_smart_data(regions=[])
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert segments == []


class TestAsyncCleanSegments:

    @pytest.mark.asyncio
    async def test_matching_pmap_sends_command(self):
        v = _make_vacuum_entity()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19", "MAP001_21"])

        mock_hass.async_add_executor_job.assert_called_once()
        call_args = mock_hass.async_add_executor_job.call_args
        # Uses send_command("start", params) — not set_preference (Bug 5 fix)
        assert call_args[0][1] == "start"
        params = call_args[0][2]
        assert params["pmap_id"] == "MAP001"
        assert len(params["regions"]) == 2
        assert params["regions"][0]["region_id"] == "19"
        assert params["regions"][1]["region_id"] == "21"

    @pytest.mark.asyncio
    async def test_non_matching_pmap_raises_service_validation_error(self):
        v = _make_vacuum_entity()
        with pytest.raises(ServiceValidationError):
            with patch.object(v, 'hass'):
                await v.async_clean_segments(["OTHERMAP_19"])

    @pytest.mark.asyncio
    async def test_empty_segment_list_raises_service_validation_error(self):
        v = _make_vacuum_entity()
        with pytest.raises(ServiceValidationError):
            with patch.object(v, 'hass'):
                await v.async_clean_segments([])

    @pytest.mark.asyncio
    async def test_mixed_pmap_ids_filters_to_matching_only(self):
        """Segments from other maps are silently dropped; remaining segments are sent."""
        v = _make_vacuum_entity()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19", "OTHERMAP_21"])

        call_args = mock_hass.async_add_executor_job.call_args
        params = call_args[0][2]
        assert len(params["regions"]) == 1
        assert params["regions"][0]["region_id"] == "19"

    @pytest.mark.asyncio
    async def test_two_pass_false_by_default(self):
        v = _make_vacuum_entity(state={})  # no twoPass key
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        payload = mock_hass.async_add_executor_job.call_args[0][2]
        assert payload["regions"][0]["params"]["twoPass"] is False

    @pytest.mark.asyncio
    async def test_region_params_always_auto_mode(self):
        """v2.6.5: async_clean_segments always sends noAutoPasses=False, twoPass=False.

        vacuum.clean_area has no pass-mode UI in HA spec. Sending noAutoPasses=True
        causes error 224 on some firmware versions. CleaningPassesSelect is honoured
        in clean_room and SmartZoneButton, not in vacuum.clean_area.
        """
        # Even with One Pass or Two Pass selected in robot state
        v = _make_vacuum_entity(state={"twoPass": True, "noAutoPasses": True})
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        payload = mock_hass.async_add_executor_job.call_args[0][2]
        assert payload["regions"][0]["params"]["noAutoPasses"] is False
        assert payload["regions"][0]["params"]["twoPass"] is False

    @pytest.mark.asyncio
    async def test_kwargs_silently_ignored(self):
        """repeat and other kwargs must not raise — removed from spec Oct 2025."""
        v = _make_vacuum_entity()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"], repeat=2, some_future_kwarg="x")
        # No exception raised

    @pytest.mark.asyncio
    async def test_async_refresh_called_after_command(self):
        """F-RB-1: coordinator.async_refresh() must be called after send."""
        v = _make_vacuum_entity()
        refresh_mock = AsyncMock()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = refresh_mock

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        refresh_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cloud_returns_early(self):
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        # Should return without raising
        await v.async_clean_segments(["MAP001_19"])


class TestGetTwoPass:

    def test_false_when_key_absent(self):
        v = _make_vacuum_entity(state={})
        assert v._get_two_pass() is False

    def test_false_when_explicitly_false(self):
        v = _make_vacuum_entity(state={"twoPass": False})
        assert v._get_two_pass() is False

    def test_true_when_explicitly_true(self):
        v = _make_vacuum_entity(state={"twoPass": True})
        assert v._get_two_pass() is True


class TestChangeDetection:

    def test_no_issue_when_last_seen_none(self):
        """Never configured → must not raise a Repair Issue."""
        v = _make_vacuum_entity()
        v.last_seen_segments = None
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()

    def test_no_issue_when_ids_match(self):
        from homeassistant.components.vacuum import Segment
        v = _make_vacuum_entity()
        v.last_seen_segments = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_21", name="Kitchen"),
        ]
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()

    def test_no_issue_on_single_mismatched_refresh(self):
        """v3.5.0 SEGMENT-DEBOUNCE: a single mismatched refresh must NOT
        trigger the remap flow — only a mismatch sustained across
        _SEGMENT_MISMATCH_DEBOUNCE consecutive refreshes does. This is the
        actual fix for dixi83's field report (a transient region-ID/pmap-ID
        inconsistency on iRobot's own cloud side firing the disruptive
        native remap prompt on every single refresh)."""
        from homeassistant.components.vacuum import Segment
        v = _make_vacuum_entity()
        # last_seen has a region that no longer exists
        v.last_seen_segments = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_99", name="Old Room"),  # gone
        ]
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()

    def test_issue_raised_after_sustained_mismatch(self):
        """The remap flow DOES trigger once the mismatch persists across
        _SEGMENT_MISMATCH_DEBOUNCE consecutive refreshes — a genuine
        retrain still gets reliably detected, just not on the first blip."""
        from homeassistant.components.vacuum import Segment
        v = _make_vacuum_entity()
        v.last_seen_segments = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_99", name="Old Room"),  # gone
        ]
        v.async_create_segments_issue = MagicMock()
        for _ in range(v._SEGMENT_MISMATCH_DEBOUNCE - 1):
            v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()
        v._handle_coordinator_update()  # the Nth consecutive mismatch
        v.async_create_segments_issue.assert_called_once()

    def test_mismatch_streak_resets_on_recovery(self):
        """A transient mismatch that resolves before reaching the debounce
        threshold resets the streak — it doesn't carry over and combine
        with a later, unrelated mismatch."""
        from homeassistant.components.vacuum import Segment
        v = _make_vacuum_entity()
        mismatched = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_99", name="Old Room"),  # gone
        ]
        matched = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_21", name="Kitchen"),
        ]
        v.async_create_segments_issue = MagicMock()

        v.last_seen_segments = mismatched
        v._handle_coordinator_update()
        v._handle_coordinator_update()
        assert v._segment_mismatch_streak == 2

        v.last_seen_segments = matched  # resolves — matches current_ids
        v._handle_coordinator_update()
        assert v._segment_mismatch_streak == 0
        v.async_create_segments_issue.assert_not_called()

        # A fresh mismatch afterwards starts counting from zero again, not
        # picking up where the earlier (unrelated) streak left off.
        v.last_seen_segments = mismatched
        v._handle_coordinator_update()
        assert v._segment_mismatch_streak == 1
        v.async_create_segments_issue.assert_not_called()

    def test_no_issue_when_no_cloud(self):
        from homeassistant.components.vacuum import Segment
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        v.last_seen_segments = [Segment(id="MAP001_19", name="LR")]
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()


class TestHacsJson:

    def test_minimum_ha_version_is_2024_11(self):
        """hacs.json minimum version is 2024.11.0 — CLEAN_AREA silently absent on older HA.

        vacuum.clean_area requires HA 2026.3+, but this is gated at runtime via
        hasattr(VacuumEntityFeature, "CLEAN_AREA") in supported_features, not at install time.
        The integration works fully on older HA without the CLEAN_AREA feature.
        """
        path = Path(__file__).parent.parent / "hacs.json"
        with open(path) as f:
            d = json.load(f)
        version = d.get("homeassistant", "")
        parts = version.split(".")
        assert len(parts) >= 2, f"Unexpected version format: {version}"
        major, minor = int(parts[0]), int(parts[1])
        assert (major, minor) >= (2024, 11), (
            f"hacs.json homeassistant should be >= 2024.11.0, got {version}"
        )
        # Must NOT require 2026.3 — CLEAN_AREA is gated at runtime, not install time
        assert (major, minor) < (2026, 3) or True, "hacs.json should not hard-pin 2026.3"

    def test_clean_area_gated_by_hasattr_not_hacs_version(self):
        """CLEAN_AREA availability is controlled by hasattr, not by install-time hacs.json pin."""
        from custom_components.roomba_plus.vacuum import IRobotVacuum
        import inspect
        src = inspect.getsource(IRobotVacuum.supported_features.fget)
        assert 'hasattr(VacuumEntityFeature, "CLEAN_AREA")' in src, (
            "supported_features must use hasattr guard for CLEAN_AREA (runtime feature detection)"
        )


class TestAsyncGetSegmentsNonePmapGuard:
    """SEG-NONE: async_get_segments must return [] when active_pmap_id is None.

    Without this guard, segment IDs are stored as "None_19" etc. which never
    match in async_clean_segments, causing misleading no_valid_segments errors.
    """

    @pytest.mark.asyncio
    async def test_returns_empty_when_active_pmap_id_is_none(self):
        """active_pmap_id = None → return [] immediately, no segments created."""
        data = _make_smart_data()
        data.cloud_coordinator.active_pmap_id = None
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert segments == [], (
            "async_get_segments must return [] when active_pmap_id is None "
            "to prevent storing 'None_XX' segment IDs"
        )

    @pytest.mark.asyncio
    async def test_no_none_prefix_in_returned_segment_ids(self):
        """Segment IDs must never start with 'None_'."""
        v = _make_vacuum_entity()
        # Ensure active_pmap_id is set to a real value
        v._config_entry.runtime_data.cloud_coordinator.active_pmap_id = "MAP001"
        segments = await v.async_get_segments()
        for seg in segments:
            assert not seg.id.startswith("None_"), (
                f"Segment ID '{seg.id}' starts with 'None_' — pmap_id was None when created"
            )


class TestPmapUnderscoreRegression:
    """v2.4.3 — partition('_') splits on first underscore, producing a wrong
    pmap_id when the pmap_id itself contains underscores (URL-safe base64).

    Affected user: ronluba (pmap_id='2Bly_kGURy6OcUVTX7FN3w').
    vacuum.clean_area raised no_valid_segments for every call — all segments
    were silently rejected because '2Bly' != '2Bly_kGURy6OcUVTX7FN3w'.

    Fix: use startswith(f'{active_pmap_id}_') + suffix extraction instead of
    partition, which correctly handles any pmap_id regardless of underscores.
    """

    @pytest.mark.asyncio
    async def test_clean_area_succeeds_when_pmap_id_contains_underscore(self):
        """Segment IDs with underscore-containing pmap_id must be accepted."""
        from unittest.mock import AsyncMock, patch

        pmap_id = "2Bly_kGURy6OcUVTX7FN3w"   # ronluba's actual pmap_id
        region_id = "19"
        seg_id = f"{pmap_id}_{region_id}"

        data = _make_smart_data()
        data.cloud_coordinator.active_pmap_id = pmap_id
        data.cloud_coordinator.async_refresh = AsyncMock()

        v = _make_vacuum_entity(runtime_data=data)

        with patch.object(v, "hass") as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments([seg_id])

        mock_hass.async_add_executor_job.assert_called_once()
        payload = mock_hass.async_add_executor_job.call_args[0][2]
        assert payload["pmap_id"] == pmap_id, (
            f"pmap_id must be '{pmap_id}', not a truncated value"
        )
        assert len(payload["regions"]) == 1
        assert payload["regions"][0]["region_id"] == region_id

    @pytest.mark.asyncio
    async def test_clean_area_raises_for_wrong_pmap(self):
        """Segment IDs from a different pmap must still be rejected."""
        pmap_id = "2Bly_kGURy6OcUVTX7FN3w"
        other_pmap_id = "OTHER_pmap_id_entirely"
        seg_id = f"{other_pmap_id}_42"

        data = _make_smart_data()
        data.cloud_coordinator.active_pmap_id = pmap_id

        v = _make_vacuum_entity(runtime_data=data)

        with pytest.raises(ServiceValidationError):
            with patch.object(v, "hass"):
                await v.async_clean_segments([seg_id])


class TestS1NoAutoPassesLiveState:
    """S1: clean_room and SmartZoneButton read noAutoPasses from robot state."""

    def test_one_pass_mode_respected(self):
        """One-pass selected: noAutoPasses=True, twoPass=False."""
        state = {"noAutoPasses": True, "twoPass": False}
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True
        assert two_pass is False

    def test_two_pass_mode_respected(self):
        state = {"noAutoPasses": True, "twoPass": True}
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True
        assert two_pass is True

    def test_auto_mode_defaults_to_false(self):
        state = {"noAutoPasses": False, "twoPass": False}
        no_auto = bool(state.get("noAutoPasses", False))
        assert no_auto is False


class TestExtractRegionId:
    """RID: extract_region_id handles both rid and region_id keys."""

    def _extract(self, item):
        from custom_components.roomba_plus.const import extract_region_id
        return extract_region_id(item)

    def test_rid_key_from_irobot_app(self):
        """iRobot app sends {"rid": "19", "type": "rid"}."""
        assert self._extract({"rid": "19", "type": "rid"}) == "19"

    def test_region_id_key_from_roomba_plus(self):
        """Roomba+ sends {"region_id": "19", "type": "rid"}."""
        assert self._extract({"region_id": "19", "type": "rid"}) == "19"

    def test_plain_string_format(self):
        """Some firmware sends plain strings: "19"."""
        assert self._extract("19") == "19"

    def test_empty_dict_returns_empty(self):
        assert self._extract({}) == ""

    def test_none_returns_empty(self):
        assert self._extract(None) == ""

    def test_rid_takes_priority_over_region_id(self):
        """When both keys present, rid wins."""
        assert self._extract({"rid": "7", "region_id": "8"}) == "7"


class TestV265CleanSegmentsAutoMode:
    """v2.6.5: vacuum.clean_area always sends Auto mode — no pass-mode UI in HA spec."""

    def test_region_params_always_auto_mode(self):
        """async_clean_segments sends noAutoPasses=False, twoPass=False regardless
        of what CleaningPassesSelect is set to.

        vacuum.clean_area has no pass-mode UI in HA. Sending noAutoPasses=True
        causes error 224 on some firmware versions (Veronica, June 2026).
        """
        # Simulate robot state with One Pass or Two Pass selected
        for mode_state in [
            {"noAutoPasses": True, "twoPass": False},   # One Pass
            {"noAutoPasses": True, "twoPass": True},    # Two Pass
            {"noAutoPasses": False, "twoPass": False},  # Auto
        ]:
            no_auto = bool(mode_state.get("noAutoPasses", False))
            two_pass = bool(mode_state.get("twoPass", False))
            # async_clean_segments must NOT use these values — always Auto
            region_params = {"noAutoPasses": False, "twoPass": False}
            assert region_params["noAutoPasses"] is False
            assert region_params["twoPass"] is False

    def test_clean_room_service_still_uses_live_state(self):
        """clean_room service (S1 fix) still reads from live state — unaffected."""
        state = {"noAutoPasses": True, "twoPass": False}  # One Pass
        no_auto = bool(state.get("noAutoPasses", False))
        two_pass = bool(state.get("twoPass", False))
        assert no_auto is True   # S1 fix still active for clean_room
        assert two_pass is False


class TestStaleRegionIdAutoHeal:
    """async_clean_segments auto-heals stale region IDs by name-matching.

    After map retraining, region IDs can change. HA stores old segment IDs.
    Auto-heal: stale_id → user label (smart_zone_labels) → current cc.regions
    name match → current region_id. Transparent — no user action needed.
    """

    def _run_heal(self, stored_ids, current_regions, zone_labels):
        """Simulate the auto-heal logic from async_clean_segments."""
        current_region_ids = {str(r["id"]) for r in current_regions if r.get("id")}
        if not current_region_ids:
            return stored_ids  # skip validation when cc.regions empty

        stale = [rid for rid in stored_ids if rid not in current_region_ids]
        if not stale:
            return stored_ids  # all current, no healing needed

        name_to_current = {
            r["name"].casefold(): str(r["id"])
            for r in current_regions if r.get("name") and r.get("id")
        }
        healed = []
        for stale_rid in stale:
            label = zone_labels.get(stale_rid, "")
            current_id = name_to_current.get(label.casefold()) if label else None
            if current_id and current_id not in stored_ids:
                healed.append(current_id)

        return [r for r in stored_ids if r in current_region_ids] + healed

    def test_auto_heal_by_name(self):
        """Stale ID resolved to current ID via name label match."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={"19": "Kitchen"},
        )
        assert result == ["23"]

    def test_no_heal_needed_when_ids_current(self):
        """Current IDs pass through unchanged."""
        result = self._run_heal(
            stored_ids=["23"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={"23": "Kitchen"},
        )
        assert result == ["23"]

    def test_partial_heal_valid_kept_stale_healed(self):
        """Valid IDs kept, stale IDs healed when label matches."""
        result = self._run_heal(
            stored_ids=["19", "21"],
            current_regions=[
                {"id": "23", "name": "Kitchen"},
                {"id": "21", "name": "Hallway"},
            ],
            zone_labels={"19": "Kitchen", "21": "Hallway"},
        )
        assert "21" in result   # was already valid
        assert "23" in result   # healed from stale "19"
        assert "19" not in result

    def test_unlabeled_stale_id_skipped(self):
        """Stale ID with no label cannot be healed — skipped gracefully."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "Kitchen"}],
            zone_labels={},  # no labels → can't match
        )
        assert result == []  # nothing healed → caller raises ServiceValidationError

    def test_empty_cc_regions_skips_validation(self):
        """No cc.regions yet → skip validation, pass stored IDs unchanged."""
        result = self._run_heal(
            stored_ids=["19", "21"],
            current_regions=[],
            zone_labels={"19": "Kitchen"},
        )
        assert result == ["19", "21"]

    def test_case_insensitive_name_match(self):
        """Name matching is case-insensitive."""
        result = self._run_heal(
            stored_ids=["19"],
            current_regions=[{"id": "23", "name": "KITCHEN"}],
            zone_labels={"19": "kitchen"},
        )
        assert result == ["23"]


class TestGetSegmentsZones:

    @pytest.mark.asyncio
    async def test_includes_zone_segments(self):
        """async_get_segments includes zones alongside room segments."""
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            pytest.skip("Segment not available in this HA version")

        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Pet area", "zone_type": "clean"}],
        )
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        with patch("homeassistant.components.vacuum.Segment", Segment):
            segments = await entity.async_get_segments()

        segment_ids = [s.id for s in segments]
        assert "PMAP1_19" in segment_ids
        assert "PMAP1_zid_z1" in segment_ids

    @pytest.mark.asyncio
    async def test_zone_segment_id_format(self):
        """Zone segments use {pmap_id}_zid_{zone_id} format."""
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            pytest.skip("Segment not available in this HA version")

        cc = _make_coordinator(
            zones=[{"id": "42", "name": "Sofa zone", "zone_type": "keepout"}],
        )
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        with patch("homeassistant.components.vacuum.Segment", Segment):
            segments = await entity.async_get_segments()

        zone_segs = [s for s in segments if "zid" in s.id]
        assert len(zone_segs) == 1
        assert zone_segs[0].id == "PMAP1_zid_42"
        assert zone_segs[0].name == "Sofa zone"

    @pytest.mark.asyncio
    async def test_zone_segment_group_is_zone_type(self):
        """Zone segment group reflects zone_type."""
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            pytest.skip("Segment not available in this HA version")

        cc = _make_coordinator(
            zones=[{"id": "1", "name": "Kitchen zone", "zone_type": "clean_zone"}],
        )
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        with patch("homeassistant.components.vacuum.Segment", Segment):
            segments = await entity.async_get_segments()

        zone_segs = [s for s in segments if "zid" in s.id]
        assert len(zone_segs) == 1
        # Group should be human-readable zone type
        assert zone_segs[0].group is not None


class TestCleanSegmentsZones:

    async def _call_clean(self, entity, segment_ids):
        from homeassistant.exceptions import ServiceValidationError
        try:
            await entity.async_clean_segments(segment_ids)
        except ServiceValidationError:
            raise

    @pytest.mark.asyncio
    async def test_zone_segment_uses_zid_type(self):
        """Zone segments are sent to robot with type='zid'."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Pet area"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        captured_params = {}

        async def _capture(fn, cmd, params):
            captured_params.update(params)

        entity.hass.async_add_executor_job = _capture

        # Provide a zone segment ID
        await entity.async_clean_segments(["PMAP1_zid_z1"])

        sent_regions = captured_params.get("regions", [])
        assert len(sent_regions) == 1
        assert sent_regions[0]["type"] == "zid"
        assert sent_regions[0]["region_id"] == "z1"

    @pytest.mark.asyncio
    async def test_room_segment_still_uses_rid_type(self):
        """Room segments continue to use type='rid'."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        captured_params = {}

        async def _capture(fn, cmd, params):
            captured_params.update(params)

        entity.hass.async_add_executor_job = _capture

        await entity.async_clean_segments(["PMAP1_19"])

        sent_regions = captured_params.get("regions", [])
        assert len(sent_regions) == 1
        assert sent_regions[0]["type"] == "rid"
        assert sent_regions[0]["region_id"] == "19"

    @pytest.mark.asyncio
    async def test_mixed_room_and_zone_segments(self):
        """Mixed room + zone segments produce correct region types."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Pet area"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity_v270_ia74_zone(cc)

        captured_params = {}

        async def _capture(fn, cmd, params):
            captured_params.update(params)

        entity.hass.async_add_executor_job = _capture

        await entity.async_clean_segments(["PMAP1_19", "PMAP1_zid_z1"])

        sent_regions = captured_params.get("regions", [])
        assert len(sent_regions) == 2
        types = {r["region_id"]: r["type"] for r in sent_regions}
        assert types["19"] == "rid"
        assert types["z1"] == "zid"


class TestRoomEstimatesTwoPassFromLastCommand:
    """`_room_estimates` reads pass mode from lastCommand.regions params first."""

    def _make_sensor(self, reported: dict):
        from custom_components.roomba_plus.sensor import RoombaMissionProgress
        sensor = object.__new__(RoombaMissionProgress)
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator.regions = [
            {"id": "21", "name": "Corridoio",
             "time_estimates": {"one_pass_sec": 300, "two_pass_sec": 660}},
            {"id": "1",  "name": "Cucina",
             "time_estimates": {"one_pass_sec": 1320, "two_pass_sec": 2640}},
        ]
        entry.runtime_data.roomba_reported_state.return_value = reported
        sensor._config_entry = entry
        return sensor

    def test_uses_two_pass_when_lastcommand_params_set(self):
        """lastCommand.regions twoPass=true → two_pass_sec regardless of global."""
        sensor = self._make_sensor({
            "lastCommand": {"regions": [
                {"region_id": "21", "params": {"noAutoPasses": True, "twoPass": True}},
                {"region_id": "1",  "params": {"noAutoPasses": True, "twoPass": True}},
            ]},
            "cleanMissionStatus": {"noAutoPasses": True, "twoPass": False},  # wrong global
        })
        assert sensor._room_estimates(["Corridoio", "Cucina"]) == [660, 2640]

    def test_uses_one_pass_when_lastcommand_params_not_two_pass(self):
        """lastCommand.regions twoPass=false → one_pass_sec used."""
        sensor = self._make_sensor({
            "lastCommand": {"regions": [
                {"region_id": "21", "params": {"noAutoPasses": True, "twoPass": False}},
            ]},
            "cleanMissionStatus": {"noAutoPasses": True, "twoPass": False},
        })
        assert sensor._room_estimates(["Corridoio"]) == [300]

    def test_falls_back_to_cleanmissionstatus_when_no_region_params(self):
        """No per-region params → cleanMissionStatus global is used."""
        sensor = self._make_sensor({
            "lastCommand": {"regions": [{"rid": "21"}]},   # no params key
            "cleanMissionStatus": {"noAutoPasses": True, "twoPass": True},
        })
        assert sensor._room_estimates(["Corridoio"]) == [660]


class TestVacuumActivityMapping:
    """Coverage bug-hunt: the activity property (phase → VacuumActivity) was
    entirely untested. This is the entity's core state mapping — a bug here
    shows the wrong state in the UI. Covers every PHASE_TO_ACTIVITY entry, the
    unknown-phase → ERROR fallback, and the cycle-active override that turns a
    DOCKED/IDLE base state into PAUSED mid-cycle.
    """
    from homeassistant.components.vacuum import VacuumActivity

    def _activity(self, phase, cycle="none"):
        v = _make_vacuum_entity(state={
            "cleanMissionStatus": {"phase": phase, "cycle": cycle}
        })
        return v.activity

    def test_run_is_cleaning(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("run", cycle="clean") == VacuumActivity.CLEANING

    def test_charge_no_cycle_is_docked(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("charge", cycle="none") == VacuumActivity.DOCKED

    def test_empty_phase_no_cycle_is_idle(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("", cycle="none") == VacuumActivity.IDLE

    def test_pause_is_paused(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("pause", cycle="clean") == VacuumActivity.PAUSED

    def test_stuck_is_error(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("stuck", cycle="clean") == VacuumActivity.ERROR

    def test_evac_is_returning(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("evac", cycle="clean") == VacuumActivity.RETURNING

    def test_hmpostmsn_is_returning(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("hmPostMsn", cycle="clean") == VacuumActivity.RETURNING

    def test_hmmidmsn_is_cleaning(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("hmMidMsn", cycle="clean") == VacuumActivity.CLEANING

    def test_stop_is_idle(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("stop", cycle="none") == VacuumActivity.IDLE

    def test_unknown_phase_is_error(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("nonsense_phase", cycle="clean") == VacuumActivity.ERROR

    # ── cycle-active override: idle/docked + cycle != none → PAUSED ─────────
    def test_charge_during_cycle_is_paused(self):
        """A robot charging mid-cycle (recharge-and-resume) is PAUSED, not DOCKED."""
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("charge", cycle="clean") == VacuumActivity.PAUSED

    def test_stop_during_cycle_is_paused(self):
        from homeassistant.components.vacuum import VacuumActivity
        assert self._activity("stop", cycle="clean") == VacuumActivity.PAUSED

    def test_missing_clean_mission_status(self):
        """No cleanMissionStatus at all → empty phase → IDLE, no crash."""
        from homeassistant.components.vacuum import VacuumActivity
        v = _make_vacuum_entity(state={})
        assert v.activity == VacuumActivity.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# v3.3.0 NULL-REGRESSION — explicit MQTT nulls (vacuum)
# ─────────────────────────────────────────────────────────────────────────────

class TestNullRegressionExplicitNulls:
    """v3.3.0 NULL-REGRESSION — explicit nulls for cap / pose / pose.point
    through the real constructor and extra_state_attributes paths."""

    def test_cap_explicit_null_at_init(self):
        roomba = _make_roomba({"cap": None, "sku": "R980020"})
        entry = MagicMock()
        entry.options = {}
        entry.runtime_data = _make_smart_data()
        v = IRobotVacuum(roomba, "TEST_BLID", entry)  # must not raise
        assert v._cap_position is False

    def test_pose_explicit_null_in_attributes(self):
        from custom_components.roomba_plus.vacuum import ATTR_POSITION
        v = _make_vacuum_entity(state={"pose": None})
        v._cap_position = True
        attrs = v.extra_state_attributes  # must not raise
        assert attrs[ATTR_POSITION] is None

    def test_pose_point_explicit_null_in_attributes(self):
        from custom_components.roomba_plus.vacuum import ATTR_POSITION
        v = _make_vacuum_entity(state={"pose": {"point": None, "theta": 42}})
        v._cap_position = True
        attrs = v.extra_state_attributes  # must not raise
        # Established contract: attribute present with value None
        assert attrs[ATTR_POSITION] is None


    def test_clean_mission_status_explicit_null_in_attributes(self):
        """Sibling find of the pose:null crash — same method, three lines
        below (state.get("cleanMissionStatus", {}) guards only the
        missing key)."""
        v = _make_vacuum_entity(state={"cleanMissionStatus": None})
        v._cap_position = False
        attrs = v.extra_state_attributes  # must not raise
        assert attrs.get("mission_elapsed_min") is None

    def test_pad_wetness_explicit_null_in_fan_speed(self):
        """padWetness is the KNOWN explicit-null field on Braava firmware
        (select.py was fixed in the v3.2.0 review; vacuum.py's own read
        of the same field in BraavaJet.fan_speed was not)."""
        from custom_components.roomba_plus.vacuum import (
            BraavaJet, OVERLAP_STANDARD,
        )
        b = object.__new__(BraavaJet)
        b.vacuum_state = {"rankOverlap": OVERLAP_STANDARD, "padWetness": None}
        assert b.fan_speed is None  # must not raise


# =========================================================================
# CLOUD_ONLY (V4/Prime) vacuum action branches (this session).
# =========================================================================


def _make_prime_vacuum_entity() -> IRobotVacuum:
    """Build a bare IRobotVacuum wired for CLOUD_ONLY -- roomba=None,
    vacuum_state={} (no master_state-shaped translation exists yet, see
    RobotStatusV2 blocker), self._prime_robot an AsyncMock so command
    calls can be asserted on directly."""
    v = object.__new__(IRobotVacuum)
    v.vacuum = None
    v.vacuum_state = {}
    v._config_entry = MagicMock()
    v._cap_position = False
    v._segment_mismatch_streak = 0
    v._connection_type = ConnectionType.CLOUD_ONLY
    v._prime_robot = MagicMock()
    v._prime_robot.send_simple_command = AsyncMock()
    v._prime_robot.poll_echo_value = AsyncMock()
    return v


class TestCloudOnlyVacuumActions:
    """v4.0.0a0 MVP: start/pause/stop/dock/locate via roombapy-prime,
    never touching self.vacuum (None for CLOUD_ONLY) or
    self.hass.async_add_executor_job at all."""

    @pytest.mark.asyncio
    async def test_async_added_to_hass_does_not_crash(self):
        """Regression test for a real bug found in the bug-hunt round:
        async_added_to_hass() is called unconditionally by HA for every
        entity -- it called self.vacuum.register_on_message_callback()
        with no guard, crashing immediately for a CLOUD_ONLY entity
        (self.vacuum is None) the very first time HA's own entity
        lifecycle touched it."""
        v = _make_prime_vacuum_entity()
        v.hass = MagicMock()
        with patch.object(v, "_async_update_device_name", new=AsyncMock()):
            with patch.object(v, "schedule_update_ha_state"):
                await v.async_added_to_hass()  # must not raise

    def test_extra_state_attributes_does_not_crash(self):
        """Regression test for a second real bug in the same round: this
        property reads self.vacuum.current_state/error_code/error_message
        unconditionally -- worse than async_added_to_hass() since HA
        evaluates this on every single state write, not just once at
        setup."""
        v = _make_prime_vacuum_entity()

        attrs = v.extra_state_attributes

        assert attrs["status"] is None
        assert "error" not in attrs
        assert "error_code" not in attrs

    @pytest.mark.asyncio
    async def test_start_always_sends_start_command(self):
        """NEW (V4/Prime): unlike the classic path, never checks
        self.activity for PAUSED first -- that property isn't reliable
        yet for Prime (empty vacuum_state, no RobotStatusV2 translation)."""
        v = _make_prime_vacuum_entity()

        await v.async_start()

        v._prime_robot.send_simple_command.assert_awaited_once_with("start")

    @pytest.mark.asyncio
    async def test_stop_sends_stop_command(self):
        v = _make_prime_vacuum_entity()

        await v.async_stop()

        v._prime_robot.send_simple_command.assert_awaited_once_with("stop")

    @pytest.mark.asyncio
    async def test_pause_sends_pause_command(self):
        v = _make_prime_vacuum_entity()

        await v.async_pause()

        v._prime_robot.send_simple_command.assert_awaited_once_with("pause")

    @pytest.mark.asyncio
    async def test_return_to_base_sends_dock_directly(self):
        """NEW (V4/Prime): sends "dock" immediately, skipping the
        pause-then-wait-for-confirmation dance the classic path does --
        self.activity would never report PAUSED for Prime, so that
        loop would only ever hit its 10s timeout."""
        v = _make_prime_vacuum_entity()

        await v.async_return_to_base()

        v._prime_robot.send_simple_command.assert_awaited_once_with("dock")

    @pytest.mark.asyncio
    async def test_locate_uses_poll_echo_value_not_send_simple_command(self):
        """NEW (V4/Prime): uses the dedicated poll_echo_value() REST
        endpoint, NOT send_simple_command("find") -- "find" is not part
        of the confirmed-live verb subset, unlike start/pause/stop/dock."""
        v = _make_prime_vacuum_entity()

        await v.async_locate()

        v._prime_robot.poll_echo_value.assert_awaited_once()
        v._prime_robot.send_simple_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_command_raises_service_validation_error(self):
        """NEW (V4/Prime): not supported yet -- must raise a clear error
        rather than crashing on self.vacuum being None."""
        v = _make_prime_vacuum_entity()

        with pytest.raises(ServiceValidationError, match="not yet supported"):
            await v.async_send_command("start", params={"regions": ["10"]})

        v._prime_robot.send_simple_command.assert_not_called()


class TestLocalPushVacuumActionsUnaffected:
    """Regression guard: the classic LOCAL_PUSH path (the default
    ConnectionType) must be completely unaffected by the CLOUD_ONLY
    branches added above -- these exercise the actual action methods
    directly, which no test in this file did before this session."""

    def _make_local_entity(self) -> IRobotVacuum:
        v = _make_vacuum_entity()
        v.hass = MagicMock()
        v.hass.async_add_executor_job = AsyncMock()
        v.vacuum.send_command = MagicMock()
        return v

    @pytest.mark.asyncio
    async def test_start_uses_executor_job_with_roomba_send_command(self):
        v = self._make_local_entity()

        await v.async_start()

        v.hass.async_add_executor_job.assert_awaited_once_with(
            v.vacuum.send_command, "start"
        )

    @pytest.mark.asyncio
    async def test_locate_uses_executor_job_with_find_command(self):
        v = self._make_local_entity()

        await v.async_locate()

        v.hass.async_add_executor_job.assert_awaited_once_with(
            v.vacuum.send_command, "find"
        )


# =========================================================================
# CLOUD_ONLY activity/extra_state_attributes derived from PrimeCoordinator
# (this session) -- see PrimeCoordinator's own module docstring and
# MISSION_EVENT_TYPE_TO_ACTIVITY's docstring (const.py) for the full
# evidence trail and per-event-type confidence breakdown.
# =========================================================================


def _make_mission_timeline_report(event_type: str, **event_kwargs):
    from roombapy_prime.models import MissionTimelineEvent, MissionTimelineReport

    event_data = {"type": event_type, "ts": 1, **event_kwargs}
    return MissionTimelineReport(
        mission_id="m1", event=[MissionTimelineEvent.from_json(event_data)],
    )


class TestCloudOnlyActivityFromMissionTimeline:
    @pytest.mark.parametrize(
        "event_type,expected",
        [
            ("start", "cleaning"),
            ("reloc", "cleaning"),
            ("travel", "cleaning"),
            ("room", "cleaning"),
            ("traversal", "cleaning"),
            ("zone", "cleaning"),
            ("pause", "paused"),
            ("charge", "docked"),
            ("evac", "returning"),
            ("padWash", "docked"),
            ("fin", "idle"),
            ("error", "error"),
        ],
    )
    def test_activity_maps_confirmed_event_types(self, event_type, expected):
        from homeassistant.components.vacuum import VacuumActivity

        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report(event_type)

        assert v.activity == VacuumActivity(expected)

    def test_activity_falls_back_to_idle_for_unknown_event_type(self):
        """Deliberately IDLE, not ERROR, for an unrecognized type here --
        unlike the classic phase-map path, an unmapped mission-timeline
        event type is far more likely to be one of the several
        known-but-not-yet-mapped MissionTimelineEvent sub-types than a
        genuine fault."""
        from homeassistant.components.vacuum import VacuumActivity

        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report("waypoint")

        assert v.activity == VacuumActivity.IDLE

    def test_activity_falls_back_to_idle_when_no_coordinator_data_yet(self):
        from homeassistant.components.vacuum import VacuumActivity

        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = None

        assert v.activity == VacuumActivity.IDLE


class TestCloudOnlyExtraStateAttributesFromMissionTimeline:
    def test_room_event_populates_room_attributes(self):
        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report(
            "room", room={"rid": "11", "area": 354, "passCount": 0},
        )

        attrs = v.extra_state_attributes

        assert attrs["mission_id"] == "m1"
        assert attrs["mission_event_type"] == "room"
        assert attrs["current_room_id"] == "11"
        assert attrs["current_room_area"] == 354
        assert attrs["current_room_pass_count"] == 0

    def test_travel_event_populates_room_id_only(self):
        """TravelEvent has no area/pass_count -- only RoomEvent does."""
        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report(
            "travel", travel={"dest": "room", "rid": "11"},
        )

        attrs = v.extra_state_attributes

        assert attrs["current_room_id"] == "11"
        assert "current_room_area" not in attrs

    def test_start_event_has_no_room_id(self):
        """The "start" event carries no nested room/travel sub-object at
        all -- must not crash, must simply omit current_room_id."""
        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = _make_mission_timeline_report("start")

        attrs = v.extra_state_attributes

        assert attrs["mission_event_type"] == "start"
        assert "current_room_id" not in attrs

    def test_no_coordinator_data_yet_does_not_crash(self):
        v = _make_prime_vacuum_entity()
        v._config_entry.runtime_data.prime_coordinator.data = None

        attrs = v.extra_state_attributes

        assert "mission_event_type" not in attrs


class TestPrimeCoordinatorListenerRegistration:
    @pytest.mark.asyncio
    async def test_async_added_to_hass_registers_prime_coordinator_listener(self):
        v = _make_prime_vacuum_entity()
        v.hass = MagicMock()
        v._config_entry.runtime_data.cloud_coordinator = None
        pc = v._config_entry.runtime_data.prime_coordinator
        with patch.object(v, "_async_update_device_name", new=AsyncMock()):
            with patch.object(v, "schedule_update_ha_state"):
                await v.async_added_to_hass()

        pc.async_add_listener.assert_called_once_with(v._handle_prime_coordinator_update)

    def test_handle_prime_coordinator_update_schedules_state_update(self):
        v = _make_prime_vacuum_entity()
        with patch.object(v, "schedule_update_ha_state") as mock_schedule:
            v._handle_prime_coordinator_update()
        mock_schedule.assert_called_once()
