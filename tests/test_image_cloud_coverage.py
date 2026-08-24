"""Cloud-coverage fallback on RoombaMapImage.async_image() for pose-less
robots (i3+/lewis-daredevil firmware) — real captured i3+ fixtures.

Covers RoombaMapImage._async_cloud_coverage_png() and its integration
into async_image(): a robot whose `cap` has no `pose` key can never fill
the local renderer (nothing ever calls add_pose()), so when the renderer
has no data, async_image() falls back to compositing the newest cloud
mission's coverage layer instead of serving a blank canvas. Pose-capable
robots are untouched (const.has_pose() gates the whole path).
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.image import RoombaMapImage
from custom_components.roomba_plus.mission_map import (
    MissionMapMismatch,
    MissionMapUnavailable,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((_FIXTURES / name).read_text())


_MISSION_HISTORY = _load("irobot_missionhistory_i3plus.json")
_MISSION_UMF = _load("irobot_mission_umf_i3plus.json")
_HOUSEHOLD_PMAPS = _load("irobot_pmaps_i3plus.json")

# Newest record (index 0): nMssn 434, startTime 1787562322, pmaps_info
# points at pmap_id "0wOiGRFqRaKPJuVkOdwRtQ" / pmapv_id "260824T091539" —
# the MISSION's OWN map, deliberately different from the household pmap
# list's pmap_id "D8MepS5KRD6DTWlG-g5IEw" (irobot_pmaps_i3plus.json).
_NEWEST_RECORD = _MISSION_HISTORY[0]
_MISSION_PMAP_ID = "0wOiGRFqRaKPJuVkOdwRtQ"
_MISSION_PMAPV_ID = "260824T091539"
_HOUSEHOLD_PMAP_ID = _HOUSEHOLD_PMAPS[0]["pmap_id"]

assert _NEWEST_RECORD["nMssn"] == 434
assert _NEWEST_RECORD["startTime"] == 1787562322
assert _HOUSEHOLD_PMAP_ID != _MISSION_PMAP_ID


def _pose_less_state() -> dict:
    """Mirror the real i3+ reported state: no 'pose' key under cap."""
    return {"cap": {}, "sku": "i355640", "softwareVer": "daredevil+2.6.0"}


def _pose_capable_state() -> dict:
    return {"cap": {"pose": 1}, "sku": "j755840", "softwareVer": "sapphire+1.0.0"}


async def _run_executor(fn, *args):
    return fn(*args)


def _make_entity(
    *,
    vacuum_state: dict,
    has_data: bool,
    raw_records=None,
    cloud_coordinator=True,
    config_entry_present=True,
):
    entity = RoombaMapImage.__new__(RoombaMapImage)
    entity.hass = MagicMock()
    entity.hass.async_add_executor_job = AsyncMock(side_effect=_run_executor)
    entity.vacuum = MagicMock()
    # The implementation resolves capabilities through
    # roomba_reported_state(self.vacuum), i.e. master_state["state"]["reported"]
    # — the same shape the MQTT layer delivers and that test_image.py builds.
    # Setting only .vacuum_state would leave the real lookup on a MagicMock.
    entity.vacuum.master_state = {"state": {"reported": vacuum_state}}
    entity.vacuum_state = vacuum_state

    renderer = MagicMock()
    renderer.has_data = has_data
    renderer.render = MagicMock(return_value=b"local-render-bytes")
    entity._renderer = renderer

    entity._cloud_coverage_png = None
    entity._cloud_coverage_png_for = None

    if not config_entry_present:
        entity._config_entry = None
        return entity, None

    config_entry = MagicMock()
    entity._config_entry = config_entry
    data = config_entry.runtime_data
    data.blid = "9A37307A20804F0CABE9B6011B82DDBE"
    data.mission_map_cache = {}
    # No keepout/observed-zone/room-outline overlays by default — kept
    # minimal so tests assert on the cloud-vs-local split, not overlays.
    data.umf_aligner = None
    data.outline_store = None

    if cloud_coordinator:
        cc = data.cloud_coordinator
        cc.raw_records = raw_records if raw_records is not None else []
        cc.api.get_pmap_umf = AsyncMock(return_value=_MISSION_UMF)
    else:
        data.cloud_coordinator = None

    return entity, data


class TestCloudCoverageFallbackCoreRegression:
    """A pose-less robot with an empty renderer must get the real cloud
    coverage PNG, not the blank 200x200 canvas."""

    @pytest.mark.asyncio
    async def test_async_image_returns_cloud_render_not_blank(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        assert result != RoombaMapImage._blank_image()

    @pytest.mark.asyncio
    async def test_fetch_uses_missions_own_pmap_not_household_pmap(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        await entity.async_image()

        cc = data.cloud_coordinator
        cc.api.get_pmap_umf.assert_awaited_once_with(
            data.blid, _MISSION_PMAP_ID, _MISSION_PMAPV_ID
        )
        # The whole feature rests on this distinction: the fetch must use
        # the MISSION's own pmap_id/pmapv_id (from pmaps_info on the
        # mission-history record), never the household pmap list's
        # pmap_id — the two are deliberately different in the fixtures.
        called_args = cc.api.get_pmap_umf.await_args.args
        assert _HOUSEHOLD_PMAP_ID not in called_args

    @pytest.mark.asyncio
    async def test_decoded_png_has_real_coverage_drawn(self):
        from PIL import Image

        from custom_components.roomba_plus.mission_map import _PNG_SIZE_PX

        entity, _ = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        img = Image.open(io.BytesIO(result))
        assert img.size == (_PNG_SIZE_PX, _PNG_SIZE_PX)
        colours = img.convert("RGB").getcolors(maxcolors=1_000_000)
        assert colours is not None and len(colours) > 1, (
            "917-point coverage layer must produce more than one colour "
            "— a single-colour image would mean nothing was drawn"
        )


class TestCloudCoverageFeatureGate:
    """Pose-capable robots must never take the cloud path, even with an
    empty renderer."""

    @pytest.mark.asyncio
    async def test_pose_capable_robot_never_calls_cloud_fetch(self):
        entity, data = _make_entity(
            vacuum_state=_pose_capable_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()
        assert result == b"local-render-bytes"
        entity._renderer.render.assert_called_once()


class TestCloudCoverageNoRegressionWhenRendererHasData:
    @pytest.mark.asyncio
    async def test_has_data_true_skips_cloud_path_entirely(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=True,
            raw_records=[_NEWEST_RECORD],
        )

        result = await entity.async_image()

        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()
        assert result == b"local-render-bytes"


class TestCloudCoverageRendererNoneStillBlank:
    @pytest.mark.asyncio
    async def test_no_renderer_returns_blank_image(self):
        entity, _ = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        entity._renderer = None

        result = await entity.async_image()

        assert result == RoombaMapImage._blank_image()


class TestCloudCoverageFallsBackToLocalRenderGracefully:
    """Every "nothing to serve from the cloud" case must fall through to
    the existing local (blank, since has_data=False) render rather than
    raising."""

    @pytest.mark.asyncio
    async def test_no_config_entry(self):
        entity, _ = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            config_entry_present=False,
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        entity._renderer.render.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_cloud_coordinator(self):
        entity, _ = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            cloud_coordinator=False,
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_empty_raw_records(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_record_has_pmaps_info(self):
        record_without_pmaps = {**_NEWEST_RECORD, "pmaps_info": []}
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[record_without_pmaps],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_missing_both_starttime_and_timestamp(self):
        record = {**_NEWEST_RECORD}
        record.pop("startTime", None)
        record.pop("timestamp", None)
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[record],
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"
        data.cloud_coordinator.api.get_pmap_umf.assert_not_awaited()


class TestCloudCoverageErrorHandling:
    @pytest.mark.asyncio
    async def test_mission_map_unavailable_falls_back_to_local(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            side_effect=MissionMapUnavailable("no coverage layer")
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_mission_map_mismatch_falls_back_to_local(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        # nmssn in the UMF header disagrees with the record's nMssn.
        mismatched_umf = json.loads(json.dumps(_MISSION_UMF))
        mismatched_umf["maps"][0]["map_header"]["nmssn"] = 999
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            return_value=mismatched_umf
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_generic_exception_does_not_propagate(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            side_effect=RuntimeError("cloud transport exploded")
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"

    @pytest.mark.asyncio
    async def test_empty_coverage_mm_falls_back_to_local(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        empty_coverage_umf = json.loads(json.dumps(_MISSION_UMF))
        for layer in empty_coverage_umf["maps"][0]["layers"]:
            if layer.get("layer_type") == "coverage":
                layer["geometry"]["coordinates"] = []
        data.cloud_coordinator.api.get_pmap_umf = AsyncMock(
            return_value=empty_coverage_umf
        )

        result = await entity.async_image()

        assert result == b"local-render-bytes"


class TestCloudCoverageCaching:
    @pytest.mark.asyncio
    async def test_two_calls_for_same_mission_render_only_once(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        first = await entity.async_image()
        second = await entity.async_image()

        assert first == second
        data.cloud_coordinator.api.get_pmap_umf.assert_awaited_once()
        assert entity._cloud_coverage_png_for == (
            f"c_{int(_NEWEST_RECORD['startTime'])}"
        )
        assert entity._cloud_coverage_png == first

    @pytest.mark.asyncio
    async def test_new_newest_record_invalidates_cache_and_rerenders(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )

        first = await entity.async_image()
        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 1

        newer_record = {**_NEWEST_RECORD, "startTime": _NEWEST_RECORD["startTime"] + 100}
        data.cloud_coordinator.raw_records = [newer_record]

        second = await entity.async_image()

        assert data.cloud_coordinator.api.get_pmap_umf.await_count == 2
        assert entity._cloud_coverage_png_for == f"c_{int(newer_record['startTime'])}"
        assert second == first  # same UMF fixture -> identical render, different cache key


class TestCloudCoverageSkipsOverlays:
    """When the cloud path returns a PNG, async_image() must return it
    unchanged — the keepout/observed-zone overlay code (which projects
    pose-space mm through the local renderer's transform) must not run,
    since it has no meaning for the cloud-composited canvas."""

    @pytest.mark.asyncio
    async def test_keepout_zones_present_but_never_drawn(self):
        entity, data = _make_entity(
            vacuum_state=_pose_less_state(),
            has_data=False,
            raw_records=[_NEWEST_RECORD],
        )
        # Configure keepout data that WOULD be drawn if the overlay code
        # ran — an aligned aligner plus non-empty keepout_zones.
        aligner = MagicMock()
        aligner.aligned = True
        aligner.keepout_polygon_umf.return_value = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        aligner.umf_to_pose.side_effect = lambda x, y: (x, y)
        data.umf_aligner = aligner
        data.cloud_coordinator.keepout_zones = [{"id": "z1"}]

        result = await entity.async_image()

        # render_keepout_zones is only reachable via self._renderer, and
        # the renderer here is a bare MagicMock with has_data=False — if
        # the overlay branch ran it would call render_keepout_zones() on
        # it and that call's return value (a MagicMock, not real PNG
        # bytes) would replace the result. Getting real PNG bytes back
        # proves the overlay branch was skipped.
        assert result[:8] == b"\x89PNG\r\n\x1a\n"
        entity._renderer.render_keepout_zones.assert_not_called()
        entity._renderer.render.assert_not_called()
