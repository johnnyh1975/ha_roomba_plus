

class TestTheSixControlsFromIssue46:
    """Six rw-settings pickers, built from the vendor's own enums rather
    than from the per-SKU picker lists.

    That distinction is the whole point. `getListBySKU` returns what one
    product mode's picker shows, and a robot can sit outside it:
    @chairstacker reports `autoevacFreq = 1` and `pwReturn = 2`, neither
    of which appears in the SKU list for his model, and both correct. A
    control built from the narrow list could not have shown his own
    settings, and his first tap would have changed one silently.
    """

    @staticmethod
    def _by_key(key):
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        return next(d for d in PRIME_SELECTS if d.key == key)

    def test_all_six_exist_with_the_right_wire_keys(self):
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        wire_keys = {d.wire_key for d in PRIME_SELECTS}

        assert {
            "padDryDur", "pwReturn", "pwAreaInterval",
            "pwTimeInterval", "pwHeat", "autoevacFreq",
        } <= wire_keys

    def test_pad_wash_return_spans_both_ranges(self):
        """`ReturnByMode` puts WHEN (0/1/2) and HOW THOROUGHLY
        (100/101/102) in one field. The app's own screen offers only the
        second range — @chairstacker's robot reads 2 and his screen
        shows nothing selected. This control can represent a state the
        vendor's app cannot."""
        values = self._by_key("prime_pad_wash_return").values

        assert set(values) == {0, 1, 2, 100, 101, 102}

    def test_pad_wash_return_is_one_entity_not_two(self):
        """`_updateWashFreqByType` branches on the value's type and
        writes this single field. Two entities would fight over one wire
        key."""
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        writers = [d for d in PRIME_SELECTS if d.wire_key == "pwReturn"]

        assert len(writers) == 1

    def test_the_field_confirmed_values_are_offered(self):
        """The two values a real robot actually holds."""
        assert 1 in self._by_key("prime_autoevac_frequency").values
        assert 2 in self._by_key("prime_pad_wash_return").values
        assert 15 in self._by_key("prime_pad_wash_area_interval").values


class TestAutoevacOptionsFollowTheCapabilityLevel:
    """`CapAutoEvac` is a level, not a flag, and it selects which
    `ClearFreqType` values apply.

    CONFIRMED AGAINST A SCREENSHOT: @chairstacker reports
    `cap.autoevac = 1` and his Auto-Empty Frequency screen shows exactly
    three options. Level 1 is `freqModes` and `freqModes` is 0/1/2.
    """

    @staticmethod
    def _options(level):
        from types import SimpleNamespace

        from custom_components.roomba_plus.select_prime import _autoevac_options

        return _autoevac_options(SimpleNamespace(autoevac=level))

    def test_freq_modes_offers_exactly_three(self):
        assert set(self._options(1)) == {0, 1, 2}

    def test_freq_with_area_adds_the_area_values(self):
        assert set(self._options(2)) == {0, 1, 2, 10, 15, 25, 30, 50}

    def test_dock_return_adds_the_fourth_mode(self):
        assert 4 in self._options(3)

    def test_task_end_only_offers_nothing(self):
        """A level that allows no choice must produce no control, not a
        control with no options."""
        assert self._options(0) == {}

    def test_an_unreported_capability_offers_everything(self):
        """Fail open, like every other capability check here: a robot
        that has not reported its level gets the full set."""
        from custom_components.roomba_plus.select_prime import (
            AUTOEVAC_FREQUENCIES,
            _autoevac_options,
        )

        assert _autoevac_options(None) == AUTOEVAC_FREQUENCIES
        assert self._options(None) == AUTOEVAC_FREQUENCIES


class TestControlsFollowTheKeySetNotTheHardware:
    """@utkjmitch's Y351020 has an auto-empty dock with a bag in it and
    reports `cap.autoevac = 1`. It has no `autoevacFreq` key, and the
    iRobot app offers him no frequency control anywhere.

    So the hardware is present and the setting is not. His reading is
    the one that fits: the key set tracks what is USER-CONFIGURABLE on
    the SKU, not what is installed — and no capability flag can answer
    that question.
    """

    def test_a_reported_key_set_is_returned(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.select_prime import _settings_keys

        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                prime_status_coordinator=SimpleNamespace(
                    data={"rw-settings": {"padDryDur": 3, "pwReturn": 2}}
                )
            )
        )

        assert _settings_keys(entry) == {"padDryDur", "pwReturn"}

    def test_a_missing_shadow_returns_none_rather_than_empty(self):
        """None means "offer everything". An empty set on a slow first
        connection would hide every control and read as a broken
        integration."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.select_prime import _settings_keys

        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                prime_status_coordinator=SimpleNamespace(data={})
            )
        )

        assert _settings_keys(entry) is None

    def test_a_wrapped_shadow_is_unwrapped(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.select_prime import _settings_keys

        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                prime_status_coordinator=SimpleNamespace(
                    data={"rw-settings": {"state": {"reported": {"pwHeat": 1}}}}
                )
            )
        )

        assert _settings_keys(entry) == {"pwHeat"}


class TestTheValueSetsMatchTheDartEnums:
    """The Dart snapshot was decoded, so every one of these six sets has
    a vendor enum behind it — none is inferred.

    Two of them were built before that check and one was built wrong.
    """

    @staticmethod
    def _values(key):
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        return set(next(d for d in PRIME_SELECTS if d.key == key).values)

    def test_pad_dry_duration_matches_dry_dur_type(self):
        """`DryDurType`: two=2 … six=6. An earlier note here claimed no
        vendor enum existed and the range was inferred from two field
        captures. The inference happened to be exactly right, which is
        luck rather than method."""
        assert self._values("prime_pad_dry_duration") == {2, 3, 4, 5, 6}

    def test_area_and_time_intervals_match_their_enums(self):
        """`ReturnByArea` and `ReturnByTime`."""
        assert self._values("prime_pad_wash_area_interval") == {6, 8, 10, 15, 20}
        assert self._values("prime_pad_wash_time_interval") == {10, 15, 20, 25}

    def test_return_modes_match_return_by_mode(self):
        assert self._values("prime_pad_wash_return") == {0, 1, 2, 100, 101, 102}

    def test_autoevac_matches_clear_freq_type(self):
        assert self._values("prime_autoevac_frequency") == {
            0, 1, 2, 4, 10, 15, 25, 30, 50
        }

    def test_heat_levels_match_heat_type(self):
        from custom_components.roomba_plus.select_prime import PAD_WASH_HEAT_LEVELS

        assert set(PAD_WASH_HEAT_LEVELS) == {0, 1, 2}

    def test_the_middle_heat_level_is_default_not_low(self):
        """`HeatType.defaultHeat`. Labelled "low" at first, which reads
        as below normal; the vendor's name says it is the standard
        heated setting."""
        from custom_components.roomba_plus.select_prime import PAD_WASH_HEAT_LEVELS

        assert PAD_WASH_HEAT_LEVELS[1] == "default_heat"
        assert "low" not in PAD_WASH_HEAT_LEVELS.values()


class TestHeatOptionsFollowTheDockCapability:
    """`DockPadWashingType` turns `dock.cap.pw` from an opaque graduated
    flag into four named states:

        0 notSupported · 1 supported · 2 heatedSupported · 3 highHeatSupported

    chairstacker's dock reads 1, which is why he has no `pwHeat` key.
    The case key presence cannot catch is a dock that HAS the key but
    not every level — a static 0/1/2 would offer high heat to a level-2
    dock that cannot produce it.
    """

    @staticmethod
    def _options(level):
        from types import SimpleNamespace

        from custom_components.roomba_plus.select_prime import _pad_wash_heat_options

        return _pad_wash_heat_options(SimpleNamespace(pad_wash=level))

    def test_a_heated_dock_offers_two_levels(self):
        assert set(self._options(2)) == {0, 1}

    def test_a_high_heat_dock_offers_three(self):
        assert set(self._options(3)) == {0, 1, 2}

    def test_an_unheated_dock_offers_nothing(self):
        """Levels 0 and 1 have no heat to control. No options means no
        entity, handled by the caller."""
        assert self._options(0) == {}
        assert self._options(1) == {}

    def test_an_unreported_dock_offers_everything(self):
        from custom_components.roomba_plus.select_prime import (
            PAD_WASH_HEAT_LEVELS,
            _pad_wash_heat_options,
        )

        assert _pad_wash_heat_options(None) == PAD_WASH_HEAT_LEVELS


class TestValueSetsAgreeWithTheVendorReference:
    """THE GUARD THAT REPLACES REMEMBERING.

    A full decode of app 3.0.0 produced 130 enums with wire values, and
    two of the six controls were still built from recall: `padDryDur`
    shipped claiming no vendor enum existed (`DryDurType` was in the
    extract), and `pwHeat` shipped with wrong labels and no dock gate
    (`HeatType`, `DockPadWashingType`). Both were caught by somebody
    asking.

    Reading a document does not make its contents available at the
    moment of writing code. This test does: every value table names its
    vendor enum, and the named enum is checked against the extract that
    ships with roombapy-prime.

    DECLARED, NOT DISCOVERED. Matching by shape produces false hits --
    `{0, 1, 2}` "exactly matches" `CleanPathDensity`, `HeatType` and
    `CheckFurnitureValidCode` alike. Small integer sets collide, the
    same way short lowercase words collided in an earlier literal
    search. A match is only evidence when the name came first.
    """

    @staticmethod
    def _table(name):
        from custom_components.roomba_plus import select_prime

        return getattr(select_prime, name)

    def test_every_declared_source_matches_the_extract(self):
        from roombapy_prime.vendor_reference import wire_values

        from custom_components.roomba_plus.select_prime import VENDOR_ENUM_SOURCES

        mismatches = []
        for table_name, enum_name in VENDOR_ENUM_SOURCES.items():
            if enum_name is None:
                continue
            ours = set(self._table(table_name))
            theirs = wire_values(enum_name)
            if ours != theirs:
                mismatches.append(
                    f"{table_name} has {sorted(ours)}, {enum_name} has {sorted(theirs)}"
                )

        assert not mismatches, "value sets disagree with app 3.0.0: " + "; ".join(mismatches)

    def test_every_value_table_declares_a_source(self):
        """A table added without an entry here would not be checked, and
        an unchecked table is how the last two mistakes happened."""
        import inspect

        from custom_components.roomba_plus import select_prime
        from custom_components.roomba_plus.select_prime import VENDOR_ENUM_SOURCES

        tables = {
            name
            for name, value in inspect.getmembers(select_prime)
            if name.isupper() or name.startswith("_")
            if isinstance(value, dict)
            and value
            and all(isinstance(k, int) for k in value)
        }

        assert tables <= set(VENDOR_ENUM_SOURCES), (
            "value tables with no declared vendor source: "
            f"{sorted(tables - set(VENDOR_ENUM_SOURCES))}"
        )

    def test_a_table_without_a_vendor_enum_says_so(self):
        """None is a valid answer and must be a deliberate one.
        `SUCTION_LEVELS` comes from two `operating_mode_defaults`
        captures, not from an enum."""
        from custom_components.roomba_plus.select_prime import VENDOR_ENUM_SOURCES

        assert VENDOR_ENUM_SOURCES["SUCTION_LEVELS"] is None

    def test_a_misspelled_enum_name_raises_rather_than_passing(self):
        """Silently treating a typo as "not in the extract" would
        restore the situation this guard exists to end."""
        import pytest

        from roombapy_prime.vendor_reference import VendorReferenceError, wire_values

        with pytest.raises(VendorReferenceError):
            wire_values("DryDurTyp")
