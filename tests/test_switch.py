"""Tests for the switch platform (coverage bug-hunt — was 0% covered).

Covers the three config switches and especially their INVERTED logic:
EdgeClean and AlwaysFinish both negate the underlying robot preference
(openOnly / binPause), which is the most bug-prone part. Also covers
async_setup_entry gating (which switches appear for which robots) and the
turn_on/turn_off → set_preference command mapping.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.switch import (
    EdgeCleanSwitch,
    AlwaysFinishSwitch,
    ScheduleHoldSwitch,
    ChildLockSwitch,
    EcoChargeSwitch,
    GentleModeSwitch,
    PrimeCarpetBoostSwitch,
    async_setup_entry,
)


def _make(cls, state):
    """Build a switch instance bypassing __init__, with vacuum_state set."""
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": state}}
    s = cls.__new__(cls)
    s.vacuum = roomba
    s.vacuum_state = state
    s._blid = "test_blid"
    s.hass = MagicMock()
    s.hass.async_add_executor_job = AsyncMock()
    return s


# ── EdgeCleanSwitch: ON when openOnly is False (inverted) ────────────────────

class TestEdgeCleanSwitch:
    def test_on_when_openonly_false(self):
        s = _make(EdgeCleanSwitch, {"openOnly": False})
        assert s.is_on is True

    def test_off_when_openonly_true(self):
        s = _make(EdgeCleanSwitch, {"openOnly": True})
        assert s.is_on is False

    def test_default_on_when_key_missing(self):
        # .get("openOnly", False) → not False → True (edge clean on by default)
        s = _make(EdgeCleanSwitch, {})
        assert s.is_on is True

    @pytest.mark.asyncio
    async def test_turn_on_sends_openonly_false(self):
        s = _make(EdgeCleanSwitch, {"openOnly": True})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "openOnly", False
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_openonly_true(self):
        s = _make(EdgeCleanSwitch, {"openOnly": False})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "openOnly", True
        )

    def test_new_state_filter(self):
        s = _make(EdgeCleanSwitch, {})
        assert s.new_state_filter({"openOnly": True}) is True
        assert s.new_state_filter({"other": 1}) is False


# ── AlwaysFinishSwitch: ON when binPause is False (inverted) ─────────────────

class TestAlwaysFinishSwitch:
    def test_on_when_binpause_false(self):
        s = _make(AlwaysFinishSwitch, {"binPause": False})
        assert s.is_on is True

    def test_off_when_binpause_true(self):
        s = _make(AlwaysFinishSwitch, {"binPause": True})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        # .get("binPause", True) → not True → False (pause by default)
        s = _make(AlwaysFinishSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_binpause_false(self):
        s = _make(AlwaysFinishSwitch, {"binPause": True})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "binPause", False
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_binpause_true(self):
        s = _make(AlwaysFinishSwitch, {"binPause": False})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "binPause", True
        )


# ── ScheduleHoldSwitch: ON when schedHold is True (NOT inverted) ─────────────

class TestScheduleHoldSwitch:
    def test_on_when_schedhold_true(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": True})
        assert s.is_on is True

    def test_off_when_schedhold_false(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(ScheduleHoldSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_schedhold_true(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "schedHold", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_schedhold_false(self):
        s = _make(ScheduleHoldSwitch, {"schedHold": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "schedHold", False
        )


# ── ChildLockSwitch: ON when childLock is True (NOT inverted) ────────────────

class TestChildLockSwitch:
    def test_on_when_childlock_true(self):
        s = _make(ChildLockSwitch, {"childLock": True})
        assert s.is_on is True

    def test_off_when_childlock_false(self):
        s = _make(ChildLockSwitch, {"childLock": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(ChildLockSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_childlock_true(self):
        s = _make(ChildLockSwitch, {"childLock": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "childLock", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_childlock_false(self):
        s = _make(ChildLockSwitch, {"childLock": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "childLock", False
        )


# ── EcoChargeSwitch: ON when ecoCharge is True (NOT inverted) ────────────────

class TestEcoChargeSwitch:
    def test_on_when_ecocharge_true(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": True})
        assert s.is_on is True

    def test_off_when_ecocharge_false(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(EcoChargeSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_ecocharge_true(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "ecoCharge", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_ecocharge_false(self):
        s = _make(EcoChargeSwitch, {"ecoCharge": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "ecoCharge", False
        )


# ── GentleModeSwitch: ON when gentle is True (NOT inverted) ─────────────────
# v3.4.3 GENTLE-MODE

class TestGentleModeSwitch:
    def test_on_when_gentle_true(self):
        s = _make(GentleModeSwitch, {"gentle": True})
        assert s.is_on is True

    def test_off_when_gentle_false(self):
        s = _make(GentleModeSwitch, {"gentle": False})
        assert s.is_on is False

    def test_default_off_when_key_missing(self):
        s = _make(GentleModeSwitch, {})
        assert s.is_on is False

    @pytest.mark.asyncio
    async def test_turn_on_sends_gentle_true(self):
        s = _make(GentleModeSwitch, {"gentle": False})
        await s.async_turn_on()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "gentle", True
        )

    @pytest.mark.asyncio
    async def test_turn_off_sends_gentle_false(self):
        s = _make(GentleModeSwitch, {"gentle": True})
        await s.async_turn_off()
        s.hass.async_add_executor_job.assert_awaited_once_with(
            s.vacuum.set_preference, "gentle", False
        )

    def test_new_state_filter_true_when_gentle_present(self):
        s = _make(GentleModeSwitch, {"gentle": True})
        assert s.new_state_filter({"gentle": False}) is True

    def test_new_state_filter_false_when_gentle_absent(self):
        s = _make(GentleModeSwitch, {"gentle": True})
        assert s.new_state_filter({"ecoCharge": False}) is False


# ── async_setup_entry: gating by capability key presence ────────────────────

class TestSwitchSetupGating:
    def _setup(self, reported_state):
        roomba = MagicMock()
        roomba.master_state = {"state": {"reported": reported_state}}
        entry = MagicMock()
        entry.runtime_data.roomba = roomba
        entry.runtime_data.blid = "blid123"
        added: list = []

        def _add(entities):
            added.extend(entities)

        hass = MagicMock()
        # Stub IRobotEntity.__init__ so the switch constructors don't try to
        # build DeviceInfo / read robot_unique_id from a MagicMock.
        with patch("custom_components.roomba_plus.switch.IRobotEntity.__init__",
                   return_value=None), \
             patch("custom_components.roomba_plus.entity.IRobotEntity.robot_unique_id",
                   new_callable=lambda: property(lambda self: "uid")):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                async_setup_entry(hass, entry, _add)
            )
        return added

    def test_no_switches_when_no_keys(self):
        added = self._setup({})
        assert added == []

    def test_only_edge_clean_when_openonly_present(self):
        added = self._setup({"openOnly": False})
        assert len(added) == 1
        assert isinstance(added[0], EdgeCleanSwitch)

    def test_all_six_when_all_keys_present(self):
        added = self._setup({
            "openOnly": False,
            "binPause": True,
            "schedHold": False,
            "childLock": False,
            "ecoCharge": False,
            "gentle": False,
        })
        types = {type(e) for e in added}
        assert types == {
            EdgeCleanSwitch,
            AlwaysFinishSwitch,
            ScheduleHoldSwitch,
            ChildLockSwitch,
            EcoChargeSwitch,
            GentleModeSwitch,
        }

    def test_clean_base_model_gets_always_finish(self):
        added = self._setup({"binPause": True})
        assert len(added) == 1
        assert isinstance(added[0], AlwaysFinishSwitch)

    def test_only_child_lock_when_childlock_present(self):
        added = self._setup({"childLock": True})
        assert len(added) == 1
        assert isinstance(added[0], ChildLockSwitch)

    def test_only_eco_charge_when_ecocharge_present(self):
        added = self._setup({"ecoCharge": False})
        assert len(added) == 1
        assert isinstance(added[0], EcoChargeSwitch)

    def test_only_gentle_mode_when_gentle_present(self):
        added = self._setup({"gentle": False})
        assert len(added) == 1
        assert isinstance(added[0], GentleModeSwitch)


class TestPrimeCarpetBoostSwitch:
    """PrimeCarpetBoostSwitch: reads/writes RobotSettings.carpet_boost
    via the named shadow "rw-settings" -- a genuinely different data
    source and write mechanism from every other switch in this file
    (those all use roomba.set_preference() over local MQTT)."""

    def _make(self, rw_settings: dict | None) -> "PrimeCarpetBoostSwitch":
        from custom_components.roomba_plus.switch import PrimeCarpetBoostSwitch

        config_entry = MagicMock()
        config_entry.runtime_data.prime_status_coordinator.data = (
            {"rw-settings": rw_settings} if rw_settings is not None else None
        )
        config_entry.runtime_data.prime_robot = MagicMock()
        config_entry.runtime_data.prime_robot.set_setting = AsyncMock()
        with patch(
            "custom_components.roomba_plus.switch.IRobotEntity.__init__", return_value=None
        ), patch(
            "custom_components.roomba_plus.entity.IRobotEntity.robot_unique_id",
            new_callable=lambda: property(lambda self: "uid"),
        ):
            switch = PrimeCarpetBoostSwitch("BLID123", config_entry)
        switch._config_entry = config_entry
        return switch

    def test_is_on_reflects_real_captured_value(self):
        switch = self._make({"carpetBoost": True})
        assert switch.is_on is True

    def test_is_on_none_when_no_coordinator_data_yet(self):
        switch = self._make(None)
        assert switch.is_on is None

    @pytest.mark.asyncio
    async def test_turn_on_calls_set_setting_with_carpet_boost_true(self):
        switch = self._make({"carpetBoost": False})
        await switch.async_turn_on()
        switch._prime_robot.set_setting.assert_awaited_once_with("carpetBoost", True)

    @pytest.mark.asyncio
    async def test_turn_off_calls_set_setting_with_carpet_boost_false(self):
        switch = self._make({"carpetBoost": True})
        await switch.async_turn_off()
        switch._prime_robot.set_setting.assert_awaited_once_with("carpetBoost", False)


class TestPrimeCarpetBoostSwitchDeviceInfo:
    """End-to-end confirmation that config_entry actually flows through
    to IRobotEntity.__init__ for a real Prime entity class -- the
    other PrimeCarpetBoostSwitch tests above patch __init__ away
    entirely, which would not have caught a regression in this
    specific wiring (config_entry now passed to the base __init__,
    not just stored separately afterward)."""

    def test_device_info_uses_config_entry_title_and_serial_info(self):
        from roombapy_prime.models import RobotSerialInfo
        from custom_components.roomba_plus.switch import PrimeCarpetBoostSwitch

        config_entry = MagicMock()
        config_entry.title = "Bogdana"
        config_entry.runtime_data.prime_serial_info = RobotSerialInfo(
            serial_number="SN1", sku="G185020",
        )
        config_entry.runtime_data.prime_status_coordinator.data = {
            "rw-software": {"softwareVer": "p25-405+9.3.7"},
        }

        switch = PrimeCarpetBoostSwitch("BLID123", config_entry)

        assert switch._attr_device_info["name"] == "Bogdana"
        assert switch._attr_device_info["model"] == "G185020"
        assert switch._attr_device_info["serial_number"] == "SN1"
        assert switch._attr_device_info["sw_version"] == "p25-405+9.3.7"


class TestPrimeSwitchSetupCapabilityGating:
    """NEW (this session) -- PrimeCarpetBoostSwitch is now capability-
    gated on cap.carpetBoost. See get_prime_capability_flags()'s own
    docstring for the "None means unknown, only explicit 0 means
    absent" contract."""

    def _entry(self, cap: dict | None):
        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.prime_coordinator import PrimeStatusCoordinator

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        entry.runtime_data.prime_status_coordinator.data = (
            {PrimeStatusCoordinator.CLASSIC_SHADOW_KEY: {"cap": cap}} if cap is not None else None
        )
        return entry

    @pytest.mark.asyncio
    async def test_excluded_when_carpet_boost_is_zero(self):
        """REWORDED (this session): asserts on carpet boost specifically
        rather than on an empty list.

        The Prime branch now also creates setting switches -- child lock,
        eco charging, two-pass, extra suction -- so "nothing was added"
        stopped meaning "carpet boost was excluded". It fired the moment
        those arrived, which is the count assertion doing its job."""
        entry = self._entry({"carpetBoost": 0})
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert not any(isinstance(e, PrimeCarpetBoostSwitch) for e in added)

    @pytest.mark.asyncio
    async def test_included_when_carpet_boost_is_nonzero(self):
        entry = self._entry({"carpetBoost": 3})
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert any(isinstance(e, PrimeCarpetBoostSwitch) for e in added)

    @pytest.mark.asyncio
    async def test_included_when_capability_unknown(self):
        """Fail-open default -- no coordinator data yet."""
        entry = self._entry(None)
        added: list = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        # Fail-open: unknown capabilities must not cost the user their
        # switches. Both carpet boost and the setting switches appear.
        assert any(isinstance(e, PrimeCarpetBoostSwitch) for e in added)
        assert len(added) > 1


class TestPrimeSettingSwitches:
    """Four rw-settings booleans, exposed as switches.

    All four are recorded in the write-path test status as write ✅ /
    read-back ✅ -- the robot echoed the new value, which is the proof
    that it accepted it. childLock additionally has a confirmed physical
    effect: the robot announced it audibly.

    Built from one description-driven class rather than four copies of
    PrimeCarpetBoostSwitch, so the shadow name and read path exist in one
    place."""

    def _descriptions(self):
        from custom_components.roomba_plus.switch import PRIME_SETTING_SWITCHES

        return PRIME_SETTING_SWITCHES

    def test_only_settings_with_their_own_write_command_are_present(self):
        """An exact set, not a minimum, so that adding one is a
        deliberate act with a reason attached.

        padDryAllowed joined the four in a20. It is the only one of the
        seven AutoWash-related rw-settings fields with its own
        SetPadDryAllowCommand in the app; the rest travel through
        SetAllRoombaPreferences as a bundle, and writing one field of a
        bundle alone is how schedHold behaves -- accepted, ignored."""
        assert {d.wire_key for d in self._descriptions()} == {
            "childLock", "ecoCharge", "noAutoPasses", "vacHigh",
            "padDryAllowed",
        }

    def test_the_bundled_autowash_settings_stay_out(self):
        """Six siblings sit next to padDryAllowed in the same shadow and
        are deliberately not offered: the app writes them together
        through a ten-boolean command value, and which of them move
        together is not statically readable."""
        offered = {d.wire_key for d in self._descriptions()}

        for bundled in ("padDryDur", "padWashAllowed", "pwAreaInterval",
                        "pwTimeInterval", "pwReturn", "autoevacFreq"):
            assert bundled not in offered

    def test_sched_hold_is_absent(self):
        """THE exclusion that matters. schedHold writes and reads back
        successfully and the robot ignores it entirely -- confirmed in
        the field. A switch the robot accepts and does nothing about is
        worse than no switch, because the UI would state something
        false."""
        assert "schedHold" not in {d.wire_key for d in self._descriptions()}

    def test_every_model_attribute_exists_on_RobotSettings(self):
        """The wire key and the read-back attribute differ, and assuming
        they match has produced false "field missing" reports in this
        project before -- swScrub/scrub, langs2/languages_raw. A typo
        here would make the switch permanently unavailable."""
        import dataclasses

        from roombapy_prime.models import RobotSettings

        fields = {f.name for f in dataclasses.fields(RobotSettings)}
        for description in self._descriptions():
            assert description.model_attr in fields, description.key

    def test_entity_id_slugs_are_locale_independent(self):
        """has_entity_name plus translation_key otherwise makes HA derive
        the entity_id from the TRANSLATED name."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.switch import PrimeSettingSwitch

        for description in self._descriptions():
            switch = object.__new__(PrimeSettingSwitch)
            switch.entity_description = description
            assert switch.suggested_object_id == description.key

    def test_an_unread_setting_is_unavailable_not_off(self):
        """Rendering unknown as off would have someone believe child lock
        was disabled when it was on -- and toggling it would then write a
        value that was already set."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.switch import PrimeSettingSwitch

        switch = object.__new__(PrimeSettingSwitch)
        switch.entity_description = self._descriptions()[0]
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator = None
        switch._config_entry = entry

        assert switch.is_on is None

    def test_every_switch_is_translated_in_every_locale(self):
        import json
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        for locale_file in sorted((base / "translations").glob("*.json")):
            switches = json.loads(locale_file.read_text(encoding="utf-8"))["entity"]["switch"]
            for description in self._descriptions():
                assert description.translation_key in switches, (
                    f"{locale_file.name}: {description.translation_key}"
                )


class TestNoPadWetnessControlIsOffered:
    """Deliberately absent, and the reason is not the obvious one.

    The first reason given was that the robot picks which of the three
    pad categories applies, so a control would have to guess. An APK
    pass disproved that: the app reads the whole map, changes one entry
    and writes all three back -- the same read-modify-write shape as
    set_virtual_wall. Guessing was never required.

    The real blocker came from the deserializer side. MoppingAsset-
    Constants holds SEPARATE wetness tables:

        kPadWetnessMap      / kReversePadWetnessMap
        kPadPlateWetnessMap / kReversePadPlateWetnessMap

    with their own schema constants (kPadWetness,
    kPadWetnessPadPlateFieldName, kPadPlateWetnessLevel). A `1` under
    `disposable` may not mean what a `1` under `padPlate` means, and a
    control writing one level across all three would be wrong for at
    least one -- silently, because the robot accepts it.

    The tables are BSS constants, so their contents cannot be read
    statically. What settles it is an rw-settings capture with
    padWetness populated, which the shadow dump added in this release
    will produce from the next mopping-robot download."""

    def test_no_pad_wetness_switch(self):
        from custom_components.roomba_plus.switch import PRIME_SETTING_SWITCHES

        keys = {d.wire_key for d in PRIME_SETTING_SWITCHES}

        assert "padWetness" not in keys

    def test_no_pad_wetness_select(self):
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        keys = {d.wire_key for d in PRIME_SELECTS}

        assert "padWetness" not in keys

    def test_nothing_writes_the_field(self):
        """Stronger than checking the entity lists: a service or a
        coordinator could write it without an entity existing."""
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        for source_file in root.glob("*.py"):
            source = source_file.read_text(encoding="utf-8")
            # The Classic select writes disposable/reusable through its
            # own path and predates all of this; it is not the Prime
            # set_setting route this guards.
            assert not re.search(
                r'set_setting\(\s*["\']padWetness', source
            ), source_file.name
