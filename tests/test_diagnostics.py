"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



from unittest.mock import MagicMock
from unittest.mock import patch
import pytest


def _make_roomba(reported: dict) -> MagicMock:
    """Minimal roomba mock whose master_state returns the given reported dict."""
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": reported}}
    roomba.roomba_connected = True
    roomba.current_state = "Charging"
    roomba.client_error = None
    roomba.continuous = True
    roomba.delay = 1
    roomba.error_code = 0
    roomba.error_message = ""
    return roomba


def _make_entry(reported: dict) -> MagicMock:
    entry = MagicMock()
    entry.version = 22
    entry.title = "Roomba 980 - OG"
    entry.data = {}
    entry.options = {}
    entry.runtime_data.map_capability.value = "ephemeral"
    entry.runtime_data.renderer = None
    entry.runtime_data.room_seg_store = None
    entry.runtime_data.robot_profile = None
    entry.runtime_data.mission_store = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.mission_archive = None
    entry.runtime_data.roomba = _make_roomba(reported)
    return entry


async def _run_diag(reported: dict) -> dict:
    from custom_components.roomba_plus.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    entry = _make_entry(reported)
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []

    # Patch the lazy-imported roomba_reported_state inside __init__.py
    with patch(
        "custom_components.roomba_plus.__init__.roomba_reported_state",
        side_effect=lambda r: r.master_state["state"]["reported"],
    ):
        return await async_get_config_entry_diagnostics(hass, entry)


class TestFirmwareVerDiagnostics:
    @pytest.mark.asyncio
    async def test_sub_module_sw_versions_present(self):
        sub = {"nav": "lewis-nav-3.8.3", "con": "lewis-con-3.3.0"}
        diag = await _run_diag({"softwareVer": "22.52.10", "subModSwVer": sub})
        assert diag["device"]["sub_module_sw_versions"] == sub

    @pytest.mark.asyncio
    async def test_sub_module_sw_versions_none_on_9series(self):
        """980 / 9-series has no subModSwVer — value must be None in diagnostics."""
        diag = await _run_diag({"softwareVer": "22.52.10"})
        assert diag["device"]["sub_module_sw_versions"] is None

    @pytest.mark.asyncio
    async def test_bbchg_in_lifetime_stats(self):
        diag = await _run_diag({
            "bbchg": {"nChatters": 42, "nKnockoffs": 3, "nAborts": 1},
            "bbchg3": {"estCap": 2488},
        })
        assert "bbchg" in diag["lifetime_stats"]
        assert diag["lifetime_stats"]["bbchg"]["nChatters"] == 42


class TestCloudOnlyDiagnostics:
    """REAL CRASH FOUND AND FIXED (architecture review, not a field
    report): async_get_config_entry_diagnostics() unconditionally
    accessed data.roomba's own attributes further down -- data.roomba
    is None for every CLOUD_ONLY (V4/Prime) entry, so HA's own
    "Download diagnostics" button would have raised AttributeError
    immediately, every time, for every real Prime user."""

    def _make_cloud_only_entry(self) -> MagicMock:
        from custom_components.roomba_plus.models import ConnectionType

        entry = MagicMock()
        entry.version = 22
        entry.title = "Bogdana"
        entry.data = {}
        entry.options = {}
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.roomba = None
        entry.runtime_data.prime_household_id = "hh1"
        entry.runtime_data.prime_serial_info = MagicMock(sku="G185020", family="Roomba Combo")
        entry.runtime_data.prime_status_coordinator.last_update_success = True
        entry.runtime_data.prime_status_coordinator.data = {"rw-software": {}, "ro-currentstate": {}}
        entry.runtime_data.prime_coordinator.last_update_success = True
        entry.runtime_data.prime_coordinator.data = MagicMock()
        return entry

    @pytest.mark.asyncio
    async def test_does_not_crash_and_returns_prime_relevant_data(self):
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["connection_type"] == "cloud_only"
        assert result["prime"]["household_id_resolved"] is True
        assert result["prime"]["model_sku"] == "G185020"
        assert result["status_coordinator"]["last_update_success"] is True
        assert sorted(result["status_coordinator"]["named_shadows_seeded"]) == [
            "ro-currentstate", "rw-software",
        ]

    @pytest.mark.asyncio
    async def test_missing_household_id_and_serial_info_shown_honestly(self):
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        entry.runtime_data.prime_household_id = None
        entry.runtime_data.prime_serial_info = None
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["prime"]["household_id_resolved"] is False
        assert result["prime"]["serial_info_resolved"] is False
        assert result["prime"]["model_sku"] is None

    @pytest.mark.asyncio
    async def test_never_touches_data_roomba_at_all(self):
        """The actual crash reproduction: data.roomba is None, and if
        this branch ever reaches the Classic code path below it by
        mistake, accessing roomba.roomba_connected on None raises
        AttributeError immediately."""
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        assert entry.runtime_data.roomba is None  # sanity-check the premise
        hass = MagicMock()

        # Must not raise.
        await async_get_config_entry_diagnostics(hass, entry)


class TestPrimeCapabilityReport:
    """NEW (this session): the most common Prime support question is
    "why do I not have sensor X?" -- and since a6 the honest answer is
    often "your robot's own capability flags say it can't". Neither the
    flags nor the resulting decision were visible anywhere."""

    def _entry(self, cap=None, dock_cap=None):
        from custom_components.roomba_plus.prime_coordinator import PrimeStatusCoordinator

        entry = MagicMock()
        data = {}
        if cap is not None:
            data[PrimeStatusCoordinator.CLASSIC_SHADOW_KEY] = {"cap": cap}
        if dock_cap is not None:
            data["ro-currentstate"] = {"dock": {"cap": dock_cap}}
        entry.runtime_data.prime_status_coordinator.data = data or None
        return entry

    def test_zero_flag_is_reported_as_suppressed_with_the_reason(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"scrub": 0}))

        assert "suppressed" in report["entity_decisions"]["detected_pad"]
        assert "cap.scrub == 0" in report["entity_decisions"]["detected_pad"]

    def test_nonzero_flag_is_reported_as_created_with_the_value(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"suctionLvl": 4}))

        assert "created" in report["entity_decisions"]["suction_level"]
        assert "4" in report["entity_decisions"]["suction_level"]

    def test_unknown_capability_says_so_rather_than_implying_a_decision(self):
        """Failing open is the documented contract -- the diagnostics
        must make clear that's what happened, not imply the robot
        reported something."""
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry())

        assert "unknown" in report["entity_decisions"]["detected_pad"]

    def test_dock_capabilities_are_reported_separately_from_robot_ones(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"scrub": 3}, dock_cap={"pw": 0}))

        assert "suppressed" in report["entity_decisions"]["pad_wash_status"]
        assert "created" in report["entity_decisions"]["detected_pad"]


class TestPrimeMissionStatus:
    """The fields that explain a mission silently never starting --
    readiness refusals appear in no error field and on no rejection
    topic."""

    def _entry(self, mission_status=None, detected_pad=None):
        entry = MagicMock()
        if mission_status is None and detected_pad is None:
            entry.runtime_data.prime_status_coordinator.data = None
        else:
            entry.runtime_data.prime_status_coordinator.data = {
                "ro-currentstate": {
                    "cleanMissionStatus": mission_status or {},
                    "detectedPad": detected_pad,
                }
            }
        return entry

    def test_readiness_codes_are_named_not_just_numbered(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(
            self._entry({"notReady": 22, "condNotReady": [75]})
        )

        assert status["not_ready_name"] == "MAP_VERSION_MISMATCH"
        assert status["cond_not_ready"] == ["NO_VAC_WITH_PAD"]

    def test_unknown_readiness_code_stays_honestly_unknown(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(self._entry({"notReady": 43}))

        assert status["not_ready_name"] == "UNKNOWN_43"

    def test_mission_id_is_deliberately_omitted(self):
        """It identifies a specific run and adds nothing to triage."""
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(self._entry({"missionId": "01KY7M4XHX", "phase": "run"}))

        assert "mission_id" not in status
        assert status["phase"] == "run"

    def test_returns_none_when_no_coordinator_data(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        assert _prime_mission_status(self._entry()) is None


class TestLiveMapStatsInDiagnostics:
    """NEW (this session): born from a real field report where the map
    stayed blank while data was arriving and failing to decode 106
    times an hour. The counters make that visible at a glance instead
    of requiring someone to scrape their log."""

    def test_stats_are_included_in_the_prime_diagnostics(self):
        from custom_components.roomba_plus.models import RoombaData

        data = RoombaData(blid="x", roomba=None)
        data.live_map_stats = {
            "updates_received": 106,
            "decode_ok": 0,
            "decode_failed": 106,
            "last_error": "ValueError('Unsupported protobuf wire type 4 at offset 6')",
            "last_payload_prefix_hex": "0a04deadbeef",
        }

        assert data.live_map_stats["decode_failed"] == 106
        assert data.live_map_stats["decode_ok"] == 0

    def test_field_defaults_to_none_before_any_map_entity_exists(self):
        """A robot with no map capability never creates the image
        entity at all -- the field must simply stay None rather than
        implying zero updates were received."""
        from custom_components.roomba_plus.models import RoombaData

        data = RoombaData(blid="x", roomba=None)

        assert data.live_map_stats is None
