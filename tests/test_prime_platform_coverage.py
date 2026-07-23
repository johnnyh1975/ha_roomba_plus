"""Comprehensive PRIME_PLATFORMS coverage test.

REAL BUG THIS IS BUILT TO PREVENT: PrimeBinPresentSensor/
PrimeTankPresentSensor/PrimeRobotConnectivitySensor (binary_sensor.py)
and PrimeCarpetBoostSwitch (switch.py) were built and unit-tested in
isolation, but PRIME_PLATFORMS (const.py) was never updated to include
their platforms — meaning HA never actually called either module's
async_setup_entry() for a real CLOUD_ONLY config entry, so none of
these entities were ever created for a real user despite fully working,
individually-tested code existing for them. Caught only by manually
reviewing a real field tester's own screenshots (which never showed
these entities), not by any test.

TWO DIRECTIONS, BOTH NEEDED (an earlier version of this file only had
the first one, which turned out to have the SAME structural flaw as
the original bug: a second, separately-maintained list that could
drift out of sync just as easily as PRIME_PLATFORMS itself did):

1. FORWARD: for every platform actually listed in PRIME_PLATFORMS,
   calling that module's own async_setup_entry() against a realistic
   CLOUD_ONLY config_entry must produce at least one entity. Iterates
   PRIME_PLATFORMS directly -- no separate list to drift.

2. BACKWARD (the direction the original bug needed): scans every
   platform .py file for a literal "ConnectionType.CLOUD_ONLY"
   reference -- any file that has one is a module claiming to support
   CLOUD_ONLY, and must have a corresponding entry in PRIME_PLATFORMS.
   THIS is what would have caught the original bug: binary_sensor.py
   and switch.py both had real CLOUD_ONLY branches, fully coded and
   unit-tested, while PRIME_PLATFORMS simply never listed them.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.const import Platform
import pytest

from custom_components.roomba_plus.const import PRIME_PLATFORMS
from custom_components.roomba_plus.models import ConnectionType

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "roomba_plus"

# Maps a Platform enum value to its own platform module's filename --
# NOT a duplicate risk-of-drift list like the removed
# _EXPECTED_MINIMUM_ENTITIES was: this is a STATIC, structural fact
# about Home Assistant's own platform-name convention (Platform.SENSOR
# always means sensor.py, for every HA integration, not just this
# one) -- it cannot itself silently drift the way a maintained "which
# platforms does OUR integration support" list can.
_PLATFORM_TO_MODULE: dict[Platform, str] = {
    Platform.VACUUM: "vacuum",
    Platform.SENSOR: "sensor",
    Platform.BINARY_SENSOR: "binary_sensor",
    Platform.SWITCH: "switch",
    Platform.CALENDAR: "calendar",
    Platform.IMAGE: "image",
    Platform.BUTTON: "button",
    Platform.SELECT: "select",
    Platform.DEVICE_TRACKER: "device_tracker",
}


def _make_cloud_only_config_entry() -> MagicMock:
    """A single, realistic CLOUD_ONLY config_entry, explicit about
    every attribute each platform's own CLOUD_ONLY branch actually
    reads (deliberately NOT relying on MagicMock's own
    auto-attribute-generation for anything a real async_setup_entry
    branches on, e.g. prime_status_coordinator -- an accidentally
    "truthy" auto-generated MagicMock there would mask whether that
    guard is real)."""
    config_entry = MagicMock()
    data = config_entry.runtime_data
    data.connection_type = ConnectionType.CLOUD_ONLY
    data.blid = "TESTBLID"
    data.roomba = None
    data.prime_robot = MagicMock()
    data.prime_coordinator = MagicMock()
    data.prime_status_coordinator = MagicMock()
    data.prime_status_coordinator.data = {}
    data.prime_household_id = "hh1"
    return config_entry


def _platform_files_referencing_cloud_only() -> set[str]:
    """Every platform .py file (direct children of the integration
    directory, matching a name in _PLATFORM_TO_MODULE's own values)
    that contains a literal "ConnectionType.CLOUD_ONLY" reference --
    a module claiming CLOUD_ONLY support, regardless of whether
    PRIME_PLATFORMS agrees."""
    referencing: set[str] = set()
    for module_name in _PLATFORM_TO_MODULE.values():
        path = INTEGRATION_DIR / f"{module_name}.py"
        if path.exists() and "ConnectionType.CLOUD_ONLY" in path.read_text():
            referencing.add(module_name)
    return referencing


class TestForwardEveryListedPlatformCreatesEntities:
    """Direction 1: PRIME_PLATFORMS -> does it actually work."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("platform", PRIME_PLATFORMS)
    async def test_platform_creates_at_least_one_entity(self, platform: Platform) -> None:
        module_name = _PLATFORM_TO_MODULE[platform]
        module = importlib.import_module(f"custom_components.roomba_plus.{module_name}")
        config_entry = _make_cloud_only_config_entry()
        created: list = []

        await module.async_setup_entry(MagicMock(), config_entry, created.extend)

        assert created, (
            f"Platform.{platform.name} ({module_name}.py) is listed in PRIME_PLATFORMS "
            f"but created ZERO entities for a realistic CLOUD_ONLY config entry. Either "
            f"its own CLOUD_ONLY branch is broken, or it doesn't have one at all."
        )


class TestBackwardEveryCloudOnlyModuleIsListed:
    """Direction 2: does the code -> is it actually reachable at all.

    This is the direction that would have caught the REAL bug:
    binary_sensor.py/switch.py both had real, working, unit-tested
    CLOUD_ONLY branches while PRIME_PLATFORMS simply never listed
    them, so HA never called either module's async_setup_entry() for
    a real CLOUD_ONLY entry at all."""

    def test_every_module_referencing_cloud_only_is_in_prime_platforms(self) -> None:
        referencing = _platform_files_referencing_cloud_only()
        listed_modules = {_PLATFORM_TO_MODULE[p] for p in PRIME_PLATFORMS}
        missing = referencing - listed_modules
        assert not missing, (
            f"{missing} reference ConnectionType.CLOUD_ONLY but are NOT in "
            f"PRIME_PLATFORMS -- this is exactly the shape of the original bug "
            f"(binary_sensor.py/switch.py had real CLOUD_ONLY code that was never "
            f"actually reachable). Add the corresponding Platform to PRIME_PLATFORMS."
        )
