"""Commands taken and not acted on, and a state that stopped following the
robot (4.6.0, command_watch.py)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.roomba_plus import command_watch as cw
from custom_components.roomba_plus.models import ConnectionType


def _classic_entry(reported):
    entry = MagicMock()
    entry.runtime_data.roomba = MagicMock()
    entry.runtime_data.connection_type = ConnectionType.LOCAL_PUSH
    entry.runtime_data.frozen_state = cw.FrozenStateDetector()
    return entry, patch(
        "custom_components.roomba_plus.roomba_reported_state", return_value=reported,
    )


def _prime_entry(current):
    entry = MagicMock()
    entry.entry_id = "E1"
    entry.title = "Robo"
    entry.runtime_data.roomba = None
    entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
    entry.runtime_data.frozen_state = cw.FrozenStateDetector()
    entry.runtime_data.prime_status_coordinator.data = (
        None if current is None else {"ro-currentstate": current}
    )
    return entry


class TestTheMissionStatusOfBothGenerations:
    def test_classic_reads_its_local_state(self):
        entry, patched = _classic_entry({
            "cleanMissionStatus": {"cycle": "clean", "phase": "run", "notReady": 0},
            "batPct": 80,
        })
        with patched:
            status = cw.mission_status(entry)
        assert status["cycle"] == "clean"
        assert status["phase"] == "run"
        assert status["batPct"] == 80

    def test_prime_reads_its_cloud_shadow_in_the_classic_shape(self):
        """The 4.5.2 check read the local state, which Prime does not have,
        and took the empty answer for "no mission"."""
        entry = _prime_entry({
            "cleanMissionStatus": {
                "cycle": "none", "phase": "charge", "notReady": 0, "error": 0,
                "nMssn": 12, "initiator": "localApp",
            },
            "batPct": 95,
        })
        status = cw.mission_status(entry)
        assert status["cycle"] == "none"
        assert status["phase"] == "charge"
        assert status["notReady"] == 0
        assert status["nMssn"] == 12
        assert status["batPct"] == 95

    def test_nothing_to_read_is_none_not_no_mission(self):
        assert cw.mission_status(_prime_entry(None)) is None
        entry, _ = _classic_entry({})
        with patch(
            "custom_components.roomba_plus.roomba_reported_state",
            side_effect=RuntimeError,
        ):
            assert cw.mission_status(entry) is None

    @pytest.mark.parametrize(("status", "open_"), [
        ({"cycle": "clean"}, True),
        ({"cycle": "none"}, False),
        ({}, False),
        (None, False),
    ])
    def test_an_open_cycle(self, status, open_):
        assert cw.cycle_is_open(status) is open_


class TestAFrozenState:
    """@utkjmitch's Y351020: `phase: run` and `error: 48` for 61 hours
    while the battery charged."""

    def test_running_while_charging_is_reported_once(self):
        d = cw.FrozenStateDetector()
        assert d.feed("run", 60, now=0) is None
        assert d.feed("run", 62, now=300) is None
        found = d.feed("run", 63, now=cw.FROZEN_MIN_SECONDS)
        assert found == {"minutes_in_run": 10, "battery_from": 60, "battery_to": 63}
        assert d.feed("run", 70, now=cw.FROZEN_MIN_SECONDS + 600) is None
        assert d.reported is True

    @pytest.mark.parametrize("battery", [61, 62])
    def test_a_point_or_two_of_jitter_is_not_frozen(self, battery):
        d = cw.FrozenStateDetector()
        d.feed("run", 60, now=0)
        assert d.feed("run", battery, now=2 * cw.FROZEN_MIN_SECONDS) is None

    def test_a_short_run_is_not_frozen(self):
        d = cw.FrozenStateDetector()
        d.feed("run", 60, now=0)
        assert d.feed("run", 70, now=cw.FROZEN_MIN_SECONDS - 1) is None

    def test_leaving_run_starts_over(self):
        d = cw.FrozenStateDetector()
        d.feed("run", 60, now=0)
        d.feed("charge", 80, now=100)
        assert d.run_since is None and d.reported is False
        assert d.feed("run", 80, now=200) is None

    def test_an_unreadable_battery_is_ignored(self):
        d = cw.FrozenStateDetector()
        assert d.feed("run", None, now=0) is None
        assert d.feed("run", True, now=cw.FROZEN_MIN_SECONDS) is None


class TestTheFrozenStateIsReported:
    def test_the_event_carries_the_evidence(self, caplog):
        entry = _prime_entry(None)
        entry.runtime_data.frozen_state.run_since = 0.0
        entry.runtime_data.frozen_state.lowest_battery = 50
        hass = MagicMock()
        with patch.object(cw.time, "time", return_value=cw.FROZEN_MIN_SECONDS):
            cw.note_prime_shadows(hass, entry, {
                "ro-currentstate": {
                    "cleanMissionStatus": {"phase": "run", "error": 48}, "batPct": 55,
                },
            })
        hass.bus.async_fire.assert_called_once()
        event, payload = hass.bus.async_fire.call_args.args
        assert event == "roomba_plus_state_frozen"
        assert payload["entry_id"] == "E1"
        assert payload["error"] == 48
        assert payload["battery_from"] == 50 and payload["battery_to"] == 55
        assert "cannot do both" in caplog.text
        assert cw.frozen(entry) is True

    def test_the_classic_feed_reads_the_full_state(self):
        entry, patched = _classic_entry({
            "cleanMissionStatus": {"phase": "run"}, "batPct": 70,
        })
        hass = MagicMock()
        callback = cw.make_classic_state_callback(hass, entry)
        with patched:
            callback({"state": {"reported": {"batPct": 70}}})
        assert entry.runtime_data.frozen_state.run_since is not None
        hass.bus.async_fire.assert_not_called()

    def test_no_detector_and_no_shadow_are_harmless(self):
        entry = MagicMock()
        entry.runtime_data.frozen_state = None
        cw.note_state(MagicMock(), entry, {"phase": "run"})
        cw.note_prime_shadows(MagicMock(), _prime_entry(None), {})
        assert cw.frozen(MagicMock(runtime_data=None)) is False


class TestTheNotDeliveredEvent:
    def test_payload(self):
        hass = MagicMock()
        entry = MagicMock(entry_id="E1", title="Robo")
        cw.fire_not_delivered(
            hass, entry, "clean_zone",
            {"phase": "charge", "notReady": 0, "error": 0},
            waited_sec=60.4, resent=True,
        )
        event, payload = hass.bus.async_fire.call_args.args
        assert event == "roomba_plus_command_not_delivered"
        assert payload == {
            "entry_id": "E1", "name": "Robo", "command": "clean_zone",
            "phase": "charge", "not_ready": 0, "error": 0,
            "waited_sec": 60, "resent": True,
        }


@pytest.mark.asyncio
async def test_sleep_waits():
    await cw.sleep(0)
