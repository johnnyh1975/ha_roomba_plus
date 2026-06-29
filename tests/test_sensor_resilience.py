"""Sensor platform resilience (real-diagnostic stress test).

Project-memory rationale: every sensor filter_fn runs in a single list
comprehension in async_setup_entry — ONE crash fails the whole sensor
platform. So no filter_fn may raise on any reported-state shape, however
sparse or corrupted. value_fn / native_value must degrade to None too.

The baseline state is reconstructed from the real diagnostic of a Roomba 980
(SKU R980040, NiMH, firmware v2.4.17, 425 lifetime missions, 438 run-hours).
"""
from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus.sensor import SENSORS


REAL_980_STATE = {
    "batPct": 100,
    "batteryType": "F12432712",
    "bbchg": {"nChgOk": 325, "nLithF": 0, "aborts": [1, 1, 1]},
    "bbchg3": {"avgMin": 415, "hOnDock": 30557, "nAvail": 1160, "estCap": 9720,
               "nLithChrg": 290, "nNimhChrg": 36, "nDocks": 229},
    "bbmssn": {"nMssn": 425, "nMssnOk": 135, "nMssnC": 182, "nMssnF": 108,
               "aMssnM": 94, "aCycleM": 42},
    "bbrun": {"hr": 438, "min": 5, "sqft": 1903, "nStuck": 168, "nScrubs": 958,
              "nPicks": 1099, "nPanics": 1544, "nCliffsF": 6968,
              "nCliffsR": 3555, "nMBStll": 24, "nWStll": 23, "nCBump": 0},
    "bin": {"present": True, "full": False},
    "binPause": True,
    "cap": {"pose": 1, "ota": 2, "multiPass": 2, "carpetBoost": 1, "pp": 1,
            "binFullDetect": 1, "maps": 1, "edge": 1, "eco": 1},
    "carpetBoost": True,
    "cleanMissionStatus": {"cycle": "none", "phase": "charge", "error": 0,
                           "sqft": 0, "mssnM": 0, "nMssn": 425, "notReady": 0,
                           "initiator": ""},
    "dock": {"known": True},
    "hardwareRev": 3,
    "mapUploadAllowed": True,
    "name": "Roomba",
    "noAutoPasses": False,
    "openOnly": False,
    "pose": {"point": {"x": 0, "y": 0}, "theta": 0},
    "schedHold": False,
    "signal": {"rssi": -47, "snr": 42},
    "sku": "R980040",
    "softwareVer": "v2.4.17-138",
    "twoPass": False,
    "vacHigh": False,
    "wifistat": {"rssi": -47},
}


def _all_none(state):
    return {k: None for k in state}


def _empty_subdicts(state):
    return {k: ({} if isinstance(v, dict) else v) for k, v in state.items()}


def _null_subdicts(state):
    out = copy.deepcopy(state)
    for k in ("bbrun", "bbchg3", "bbmssn", "cleanMissionStatus", "bin", "cap",
              "pose", "signal", "wifistat", "dock", "bbchg"):
        if k in out:
            out[k] = None
    return out


def _missing_subdicts(state):
    return {k: v for k, v in state.items() if not isinstance(v, dict)}


SHAPES = {
    "REAL": REAL_980_STATE,
    "all-none": _all_none(REAL_980_STATE),
    "empty-subdicts": _empty_subdicts(REAL_980_STATE),
    "null-subdicts": _null_subdicts(REAL_980_STATE),
    "missing-subdicts": _missing_subdicts(REAL_980_STATE),
    "empty-dict": {},
}


class TestSensorFilterFnResilience:
    """No filter_fn may raise on any reported-state shape — a single crash in
    the async_setup_entry list comprehension takes down the whole platform."""

    @pytest.mark.parametrize("shape_name", list(SHAPES))
    def test_all_filter_fns_survive_shape(self, shape_name):
        state = SHAPES[shape_name]
        failures = []
        for desc in SENSORS:
            fn = getattr(desc, "filter_fn", None)
            if fn is None:
                continue
            try:
                fn(state)
            except Exception as e:  # noqa: BLE001
                failures.append(f"{desc.key}: {type(e).__name__}: {e}")
        assert not failures, (
            f"{len(failures)} filter_fn crash(es) on '{shape_name}' state:\n"
            + "\n".join(failures)
        )

    def test_real_state_surfaces_sensors(self):
        """Sanity: the real 980 state should surface a healthy number of
        sensors (filter_fn → True), proving the test state is realistic."""
        surfaced = sum(
            1 for d in SENSORS
            if getattr(d, "filter_fn", None) and d.filter_fn(REAL_980_STATE)
        )
        # A 980 with full bbrun/bbchg/bbmssn should surface many sensors
        assert surfaced >= 10


class TestSensorValueFnResilience:
    """value_fn(entity) resilience is covered comprehensively by the per-sensor
    test files (test_sensors.py etc.), which build properly-wired entities.

    A platform-wide value_fn stress test was evaluated here but a minimal mock
    entity cannot distinguish a real crash from MagicMock-arithmetic noise (a
    value_fn reading entity._config_entry.runtime_data.* gets a MagicMock, and
    `MagicMock / int` raises TypeError that would not occur with a real
    entity). The meaningful platform-failure guard is the filter_fn test above:
    filter_fn takes the raw state dict directly and runs in the single list
    comprehension that can take down the whole platform.

    This placeholder documents that the value_fn path is intentionally covered
    elsewhere rather than with an unreliable platform-wide mock.
    """

    def test_real_state_is_well_formed(self):
        """Guard the test fixture itself: the reconstructed 980 state has the
        sub-dicts the value_fns expect, so the per-sensor tests that reuse
        similar shapes stay representative of real field data."""
        for key in ("bbrun", "bbchg3", "bbmssn", "cleanMissionStatus"):
            assert isinstance(REAL_980_STATE[key], dict)
        assert REAL_980_STATE["bbrun"]["hr"] == 438
        assert REAL_980_STATE["bbmssn"]["nMssn"] == 425
