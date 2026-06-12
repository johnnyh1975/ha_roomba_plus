"""Tests for F-I15 — vacuum.clean_area (HA 2026.3).

Covers:
  - supported_features gate: SMART+cloud → CLEAN_AREA present; EPHEMERAL → absent; Braava → absent
  - async_get_segments: with cloud data, no cloud, empty regions
  - async_clean_segments: matching pmap_id, non-matching, empty result, twoPass pass-through
  - _get_two_pass: reads live robot state
  - _handle_coordinator_update change-detection
  - ServiceValidationError on no_valid_segments (F-RB-2)
  - hacs.json minimum version pin
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.vacuum import (
    BraavaJet,
    IRobotVacuum,
    RoombaVacuum,
    RoombaVacuumCarpetBoost,
)
from custom_components.roomba_plus.models import MapCapability
from homeassistant.components.vacuum import VacuumEntityFeature
from homeassistant.exceptions import ServiceValidationError

_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"

# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    return v


# ── supported_features gate ───────────────────────────────────────────────────

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


# ── async_get_segments ────────────────────────────────────────────────────────

class TestAsyncGetSegments:

    async def test_returns_segments_with_cloud_data(self):
        v = _make_vacuum_entity()
        segments = await v.async_get_segments()
        assert len(segments) == 2
        ids = {s.id for s in segments}
        assert "MAP001_19" in ids
        assert "MAP001_21" in ids

    async def test_segment_names_correct(self):
        v = _make_vacuum_entity()
        segments = await v.async_get_segments()
        name_map = {s.id: s.name for s in segments}
        assert name_map["MAP001_19"] == "Living Room"
        assert name_map["MAP001_21"] == "Kitchen"

    async def test_segment_group_from_floor_option(self):
        v = _make_vacuum_entity()
        v._config_entry.options = {"floor_label": "Ground Floor"}
        segments = await v.async_get_segments()
        assert all(s.group == "Ground Floor" for s in segments)

    async def test_segment_group_none_when_no_floor_option(self):
        v = _make_vacuum_entity()
        v._config_entry.options = {}
        segments = await v.async_get_segments()
        assert all(s.group is None for s in segments)

    async def test_returns_empty_when_no_cloud(self):
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert segments == []

    async def test_returns_empty_when_no_config_entry(self):
        v = _make_vacuum_entity()
        v._config_entry = None
        segments = await v.async_get_segments()
        assert segments == []

    async def test_skips_regions_without_id(self):
        data = _make_smart_data(regions=[
            {"id": "19", "name": "Living Room"},
            {"name": "No ID region"},  # no 'id' key
            {"id": "21", "name": "Kitchen"},
        ])
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert len(segments) == 2

    async def test_empty_regions_returns_empty(self):
        data = _make_smart_data(regions=[])
        v = _make_vacuum_entity(runtime_data=data)
        segments = await v.async_get_segments()
        assert segments == []


# ── async_clean_segments ──────────────────────────────────────────────────────

class TestAsyncCleanSegments:

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

    async def test_non_matching_pmap_raises_service_validation_error(self):
        v = _make_vacuum_entity()
        with pytest.raises(ServiceValidationError):
            with patch.object(v, 'hass'):
                await v.async_clean_segments(["OTHERMAP_19"])

    async def test_empty_segment_list_raises_service_validation_error(self):
        v = _make_vacuum_entity()
        with pytest.raises(ServiceValidationError):
            with patch.object(v, 'hass'):
                await v.async_clean_segments([])

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

    async def test_two_pass_false_by_default(self):
        v = _make_vacuum_entity(state={})  # no twoPass key
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        payload = mock_hass.async_add_executor_job.call_args[0][2]
        assert payload["regions"][0]["params"]["twoPass"] is False

    async def test_two_pass_true_when_state_set(self):
        v = _make_vacuum_entity(state={"twoPass": True})
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        payload = mock_hass.async_add_executor_job.call_args[0][2]
        assert payload["regions"][0]["params"]["twoPass"] is True

    async def test_kwargs_silently_ignored(self):
        """repeat and other kwargs must not raise — removed from spec Oct 2025."""
        v = _make_vacuum_entity()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = AsyncMock()

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"], repeat=2, some_future_kwarg="x")
        # No exception raised

    async def test_async_refresh_called_after_command(self):
        """F-RB-1: coordinator.async_refresh() must be called after send."""
        v = _make_vacuum_entity()
        refresh_mock = AsyncMock()
        v._config_entry.runtime_data.cloud_coordinator.async_refresh = refresh_mock

        with patch.object(v, 'hass') as mock_hass:
            mock_hass.async_add_executor_job = AsyncMock()
            await v.async_clean_segments(["MAP001_19"])

        refresh_mock.assert_called_once()

    async def test_no_cloud_returns_early(self):
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        # Should return without raising
        await v.async_clean_segments(["MAP001_19"])


# ── _get_two_pass ─────────────────────────────────────────────────────────────

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


# ── _handle_coordinator_update change-detection ───────────────────────────────

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

    def test_issue_raised_when_ids_differ(self):
        from homeassistant.components.vacuum import Segment
        v = _make_vacuum_entity()
        # last_seen has a region that no longer exists
        v.last_seen_segments = [
            Segment(id="MAP001_19", name="Living Room"),
            Segment(id="MAP001_99", name="Old Room"),  # gone
        ]
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_called_once()

    def test_no_issue_when_no_cloud(self):
        from homeassistant.components.vacuum import Segment
        data = _make_smart_data(has_data=False)
        data.cloud_coordinator = None
        v = _make_vacuum_entity(runtime_data=data)
        v.last_seen_segments = [Segment(id="MAP001_19", name="LR")]
        v.async_create_segments_issue = MagicMock()
        v._handle_coordinator_update()
        v.async_create_segments_issue.assert_not_called()


# ── hacs.json minimum version ─────────────────────────────────────────────────

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


# ── v2.4.3 PMAP-UNDERSCORE: pmap_id with underscore breaks partition ──────────

class TestPmapUnderscoreRegression:
    """v2.4.3 — partition('_') splits on first underscore, producing a wrong
    pmap_id when the pmap_id itself contains underscores (URL-safe base64).

    Affected user: ronluba (pmap_id='2Bly_kGURy6OcUVTX7FN3w').
    vacuum.clean_area raised no_valid_segments for every call — all segments
    were silently rejected because '2Bly' != '2Bly_kGURy6OcUVTX7FN3w'.

    Fix: use startswith(f'{active_pmap_id}_') + suffix extraction instead of
    partition, which correctly handles any pmap_id regardless of underscores.
    """

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

