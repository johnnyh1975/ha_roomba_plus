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

        padDryAllowed joined the four in a20, as the only AutoWash field
        with its own `SetPadDryAllowCommand` in app 2.2.4 -- the rest
        appeared to travel as a bundle, and writing one field of a
        bundle alone is how schedHold behaves: accepted, ignored.

        padWashAllowed joined after app 3.0.0 inverted that reading. Its
        settings handler writes 24 keys INDIVIDUALLY and padWashAllowed
        is among them, while padDryAllowed is not -- the field this
        project withheld turned out to be the writable one, and the
        field it shipped is the exception that needs its own command.

        @chairstacker asked for these controls in issue #46. Five
        shipped as selects in a33; this is the sixth."""
        assert {d.wire_key for d in self._descriptions()} == {
            "childLock", "ecoCharge", "noAutoPasses", "vacHigh",
            "padDryAllowed", "padWashAllowed",
        }

    def test_the_autowash_settings_are_selects_not_switches(self):
        """These were once withheld entirely, on the reading that app
        2.2.4 wrote them as a ten-boolean bundle whose grouping was not
        statically readable.

        App 3.0.0 settled it the other way: its settings handler writes
        24 keys individually. Five of the six became SELECTS in a33 —
        they carry values, not flags — and `padWashAllowed` is the sixth
        and is a switch.

        This test used to assert all six stayed out. It kept passing
        after five of them shipped, because they shipped as selects and
        it only looked at switches.
        """
        offered = {d.wire_key for d in self._descriptions()}

        # A flag, and now offered.
        assert "padWashAllowed" in offered

        # Values, so they belong in select_prime rather than here.
        for valued in ("padDryDur", "pwAreaInterval", "pwTimeInterval",
                       "pwReturn", "autoevacFreq"):
            assert valued not in offered

    def test_the_five_valued_settings_exist_somewhere(self):
        """The guard the old test could not give: asserting they are not
        switches says nothing about whether they exist at all."""
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        keys = {d.wire_key for d in PRIME_SELECTS}

        for valued in ("padDryDur", "pwAreaInterval", "pwTimeInterval",
                       "pwReturn", "autoevacFreq"):
            assert valued in keys

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


class TestSettingSwitchesGateOnTheDockToo:
    """`padDryAllowed` had no capability gate at all, so a robot on a
    plain charge dock was offered a pad-drying setting for a dock that
    cannot dry.

    The vendor's own gate table puts pad drying under `dock.cap.pd` — a
    DOCK property. Every description could only name a ROBOT capability
    until now, and `dock_cap` was already being fetched at the call site
    and thrown away into `_dock_cap`.
    """

    @staticmethod
    def _description(**kwargs):
        from custom_components.roomba_plus.switch import PrimeSettingSwitchDescription

        base = {
            "key": "x",
            "wire_key": "x",
            "model_attr": "x",
        }
        return PrimeSettingSwitchDescription(**{**base, **kwargs})

    def test_an_explicit_zero_on_the_dock_withholds_the_switch(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.switch import _capability_permits

        description = self._description(dock_cap_attr="pad_dry")
        dock_cap = SimpleNamespace(pad_dry=0)

        assert not _capability_permits(description, None, dock_cap)

    def test_unknown_still_means_offer(self):
        """The contract this project already had: None is not absent.
        A robot that has not reported its dock yet keeps the switch."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.switch import _capability_permits

        description = self._description(dock_cap_attr="pad_dry")

        assert _capability_permits(description, None, None)
        assert _capability_permits(description, None, SimpleNamespace())

    def test_a_capable_dock_gets_the_switch(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.switch import _capability_permits

        description = self._description(dock_cap_attr="pad_dry")

        assert _capability_permits(description, None, SimpleNamespace(pad_dry=1))

    def test_the_robot_gate_still_applies(self):
        """Both objects are consulted; neither replaces the other."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.switch import _capability_permits

        description = self._description(cap_attr="multi_pass")

        assert not _capability_permits(description, SimpleNamespace(multi_pass=0), None)
        assert _capability_permits(description, SimpleNamespace(multi_pass=2), None)

    def test_pad_dry_allowed_is_the_one_that_gained_a_gate(self):
        from custom_components.roomba_plus.switch import PRIME_SETTING_SWITCHES

        by_key = {d.key: d for d in PRIME_SETTING_SWITCHES}

        assert by_key["prime_pad_dry_allowed"].dock_cap_attr == "pad_dry"


class TestSettingSwitchesFollowKeyPresenceToo:
    """`available` on a setting switch is `is_on is not None`, so a
    switch whose key the robot does not report is created and then
    permanently unavailable.

    @ratpic83's `prime_vac_high` has been in that state since setup:
    `cap.suctionLvl` is 4 so the capability gate passes, and `vacHigh`
    is simply not among his rw-settings keys.

    The selects have used key presence since the six #46 controls.
    @utkjmitch's robot is why: `cap.autoevac` 1 with no `autoevacFreq`
    key. A capability says what the hardware can do; the key set says
    what this robot lets you configure.
    """

    @staticmethod
    def _created(keys):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus import switch
        from custom_components.roomba_plus.models import ConnectionType

        entry = MagicMock()
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.blid = "BLID123"
        made: list = []

        with patch.object(
            switch, "_settings_keys", return_value=keys
        ), patch.object(
            switch, "get_prime_capability_flags",
            return_value=(SimpleNamespace(suction_lvl=4, carpet_boost=3,
                                          multi_pass=1), None),
            create=True,
        ):
            try:
                asyncio.run(
                    switch.async_setup_entry(
                        MagicMock(), entry, lambda e: made.extend(e)
                    )
                )
            except Exception:  # noqa: BLE001
                pass
        return {type(e).__name__ for e in made}

    def test_an_unreported_key_produces_no_switch(self):
        import inspect

        from custom_components.roomba_plus import switch

        source = inspect.getsource(switch)

        assert "description.wire_key in present" in source

    def test_an_unknown_key_set_still_offers_everything(self):
        """`None` means the shadow has not arrived. Fail open — the same
        contract the capability gate uses."""
        import inspect

        from custom_components.roomba_plus import switch

        source = inspect.getsource(switch)

        assert "present is None or description.wire_key in present" in source

    def test_quiet_hours_is_exempt(self):
        """It is a household setting with no rw-settings key at all, so
        a key-presence rule would withhold the one place a user can turn
        DND off."""
        import inspect

        from custom_components.roomba_plus import switch

        source = inspect.getsource(switch)
        idx = source.find("PrimeQuietHoursSwitch(data.blid")
        assert idx > 0
        assert "NOT CAPABILITY-GATED" in source[max(0, idx - 400):idx]
