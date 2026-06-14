"""IA74-ZONE full (v2.7.0) — zone segment tests.

Tests that async_get_segments() includes zone segments with correct IDs,
and that async_clean_segments() routes zone vs room segments correctly.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_coordinator(regions=None, zones=None, active_pmap_id="PMAP1"):
    cc = MagicMock()
    cc.active_pmap_id = active_pmap_id
    cc.regions = regions or []
    cc.zones = zones or []
    cc.data = {"pmaps": []}
    cc.last_update_success = True
    cc.active_user_pmapv_id = "PMAPV1"
    return cc


def _make_vacuum_entity(coordinator=None, vacuum_state=None):
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


class TestGetSegmentsZones:

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
        entity = _make_vacuum_entity(cc)

        with patch("homeassistant.components.vacuum.Segment", Segment):
            segments = await entity.async_get_segments()

        segment_ids = [s.id for s in segments]
        assert "PMAP1_19" in segment_ids
        assert "PMAP1_zid_z1" in segment_ids

    async def test_zone_segment_id_format(self):
        """Zone segments use {pmap_id}_zid_{zone_id} format."""
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            pytest.skip("Segment not available in this HA version")

        cc = _make_coordinator(
            zones=[{"id": "42", "name": "Sofa zone", "zone_type": "keepout"}],
        )
        entity = _make_vacuum_entity(cc)

        with patch("homeassistant.components.vacuum.Segment", Segment):
            segments = await entity.async_get_segments()

        zone_segs = [s for s in segments if "zid" in s.id]
        assert len(zone_segs) == 1
        assert zone_segs[0].id == "PMAP1_zid_42"
        assert zone_segs[0].name == "Sofa zone"

    async def test_zone_segment_group_is_zone_type(self):
        """Zone segment group reflects zone_type."""
        try:
            from homeassistant.components.vacuum import Segment
        except ImportError:
            pytest.skip("Segment not available in this HA version")

        cc = _make_coordinator(
            zones=[{"id": "1", "name": "Kitchen zone", "zone_type": "clean_zone"}],
        )
        entity = _make_vacuum_entity(cc)

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

    async def test_zone_segment_uses_zid_type(self):
        """Zone segments are sent to robot with type='zid'."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Pet area"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity(cc)

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

    async def test_room_segment_still_uses_rid_type(self):
        """Room segments continue to use type='rid'."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity(cc)

        captured_params = {}

        async def _capture(fn, cmd, params):
            captured_params.update(params)

        entity.hass.async_add_executor_job = _capture

        await entity.async_clean_segments(["PMAP1_19"])

        sent_regions = captured_params.get("regions", [])
        assert len(sent_regions) == 1
        assert sent_regions[0]["type"] == "rid"
        assert sent_regions[0]["region_id"] == "19"

    async def test_mixed_room_and_zone_segments(self):
        """Mixed room + zone segments produce correct region types."""
        cc = _make_coordinator(
            regions=[{"id": "19", "name": "Kitchen"}],
            zones=[{"id": "z1", "name": "Pet area"}],
        )
        cc.active_user_pmapv_id = "V1"
        entity = _make_vacuum_entity(cc)

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
