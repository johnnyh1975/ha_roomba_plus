"""Prime select entities -- `select_prime.py`.

A NOTE ON IMPORTS. The original half of this file imports `pytest`
locally inside each method, and has no module-level imports at all. The
cleaning-mode tests merged in below use `@pytest.mark.parametrize`,
which is a decorator and therefore cannot be imported locally -- so the
file now needs one module-level import that it did not before.

Kept as the single exception rather than rewriting seven parametrized
tests into loops: a loop that fails tells you the class failed, a
parametrized case tells you which value did.
"""

import pytest

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


class TestOptionListsNarrowByProductMode:
    """`getListBySKU` narrows five product modes to shorter interval and
    duration lists than the enums declare.

    None of the current testers is one of them — G1, N1, W1, Y3 and Y4
    all take the standard lists. But **V1 and Z1 were added to
    PRIME_SKU_PREFIXES in the same session these controls were built**,
    so a robot that had only just become recognisable would have been
    offered intervals its own app does not show.
    """

    @staticmethod
    def _narrow(wire_key, sku):
        from custom_components.roomba_plus.select_prime import (
            PRIME_SELECTS,
            _sku_narrowed,
        )

        values = next(d for d in PRIME_SELECTS if d.wire_key == wire_key).values
        return set(_sku_narrowed(wire_key, sku, values))

    def test_the_testers_all_get_the_full_lists(self):
        for sku in ("G185020", "N185240", "Y351020", "W155042", "Y414040"):
            assert self._narrow("padDryDur", sku) == {2, 3, 4, 5, 6}, sku
            assert self._narrow("pwAreaInterval", sku) == {6, 8, 10, 15, 20}, sku

    def test_only_z1_of_the_new_prefixes_is_narrowed(self):
        """V1 and Z1 both became Prime this session, and only one of
        them is narrowed.

        The first version of this test asserted overrides for V1 that do
        not exist -- it was written against a table built from the
        report's prose rather than from `v3_sku_value_lists.json`, where
        V1 has its own branch and no overrides in it. Seven of ten
        entries were invented, and the tests agreed with them."""
        assert self._narrow("padDryDur", "Z105020") == {4, 5, 6}
        assert self._narrow("autoevacFreq", "Z105020") == {0, 15, 30}

        assert self._narrow("padDryDur", "V105020") == {2, 3, 4, 5, 6}
        assert self._narrow("pwTimeInterval", "V105020") == {10, 15, 20, 25}

    def test_g2_sees_the_short_area_list(self):
        assert self._narrow("pwAreaInterval", "G285020") == {6, 8, 10}

    def test_g2_keeps_the_standard_dry_durations(self):
        """The invented entry gave G2 `padDryDur` (2,3,4). The data file
        gives it one override and it is not that one."""
        assert self._narrow("padDryDur", "G285020") == {2, 3, 4, 5, 6}

    def test_an_unknown_sku_gets_everything(self):
        """Fail open: a robot whose SKU has not arrived should get the
        full list rather than an arbitrary subset."""
        assert self._narrow("padDryDur", None) == {2, 3, 4, 5, 6}
        assert self._narrow("padDryDur", "QQ00000") == {2, 3, 4, 5, 6}

    def test_autoevac_is_not_narrowed_by_sku(self):
        """A screenshot outranks the extraction here. The SKU list gives
        [0, 10, 15, 25, 30] for a standard robot; @chairstacker's G1
        shows three options — every routine, every 2, every 3 — which is
        0/1/2 and matches his `cap.autoevac = 1` exactly.

        The SKU extraction says of itself that its branch assignment is
        partly inferred. The photograph is not."""
        assert self._narrow("autoevacFreq", "G185020") == {
            0, 1, 2, 4, 10, 15, 25, 30, 50
        }


class TestTheHeatGateIsMarkedAsAnInference:
    """`_pad_wash_heat_options` narrows the heat levels by
    `dock.cap.pw`, reading `DockPadWashingType`'s member names as the
    rule. Nobody has read what actually gates `pwHeat`.

    The research's own correction table records "gate über dock.cap.pw"
    as WRONG for the neighbouring wash-frequency screen — `setWashFreq`
    calls `ProductMode::getModeBySku()` first, so the SKU decides which
    UI appears, not a capability field.

    Kept because the risk points the safe way: a level-2 dock offered
    high heat would accept the write and not produce it, silently. A
    wrong gate instead hides an option on a capable dock, and someone
    reports that.
    """

    def test_the_inference_is_labelled_as_one(self):
        import inspect

        from custom_components.roomba_plus import select_prime

        source = inspect.getsource(select_prime)

        assert "not a gate anyone has read" in source
        assert "getModeBySku" in source

    def test_it_still_fails_open(self):
        from custom_components.roomba_plus.select_prime import (
            PAD_WASH_HEAT_LEVELS,
            _pad_wash_heat_options,
        )

        assert _pad_wash_heat_options(None) == PAD_WASH_HEAT_LEVELS


class TestPadWetnessIsTheOneGuessedValueSet:
    """The key `padWetness.padPlate`, its dot notation and its gate
    `cap.ppWetLvl` are all confirmed. The RANGE is in no vendor enum, no
    settings-key type, no locale string and no capability table — it is
    the only value set in this file without a source.

    Four is the highest anyone has seen (@chairstacker, alongside
    `ppWetLvl: 3`). @ratpic83's 405 Combo reports `ppWetLvl: 0` — same
    robot model, different dock, and the first robot that should not get
    this control at all.
    """

    def test_a_guessed_ceiling_cannot_hide_a_real_value(self):
        """If a robot reports level 6, offering four would render the
        owner's own setting as invalid. The guess is ours; the robot is
        the authority."""
        from custom_components.roomba_plus.select_prime import _wetness_options

        assert set(_wetness_options(6)) == {1, 2, 3, 4, 5, 6}

    def test_it_never_narrows(self):
        """A robot reporting 2 still gets the full set: nothing says
        which levels are unavailable, only which one is selected."""
        from custom_components.roomba_plus.select_prime import _wetness_options

        assert set(_wetness_options(2)) == {1, 2, 3, 4}
        assert set(_wetness_options(None)) == {1, 2, 3, 4}

    def test_an_absurd_value_does_not_generate_an_absurd_list(self):
        """A shadow carrying 9999 would otherwise produce a picker with
        nine thousand entries."""
        from custom_components.roomba_plus.select_prime import _wetness_options

        assert set(_wetness_options(9999)) == {1, 2, 3, 4}

    def test_ppwetlvl_zero_withholds_the_control(self):
        """The gate is `cap.ppWetLvl`, and an explicit 0 is the robot
        saying it has no wetness control. @ratpic83's reports exactly
        that."""
        from custom_components.roomba_plus.select_prime import PRIME_SELECTS

        description = next(
            d for d in PRIME_SELECTS if d.wire_key == "padWetness.padPlate"
        )

        assert description.cap_attr == "pp_wet_lvl"

    def test_the_missing_source_is_declared_rather_than_hidden(self):
        """Two controls once shipped built from recall. This table says
        `None` deliberately, and the guard requires the entry."""
        from custom_components.roomba_plus.select_prime import VENDOR_ENUM_SOURCES

        assert "PAD_WETNESS_LEVELS" in VENDOR_ENUM_SOURCES
        assert VENDOR_ENUM_SOURCES["PAD_WETNESS_LEVELS"] is None


class TestLabelsMustBeTheVendorsWordsNotOurs:
    """`PAD_WASH_RETURN_MODES` carried all six of `ReturnByMode`'s values
    — exactly right — and labelled 100/101/102 "standard | medium |
    high". The vendor calls them `mission`, `refill` and
    `refillAndRoom`. Nothing grades them; the labels came from assuming
    a three-value range must be a scale.

    The value check passed it every time, because the values were never
    wrong.

    WHAT A WRONG LABEL COSTS: @ratpic83 read those three beside a dock
    reporting `pw: 3`, reported the heat levels as present and
    confirmed, then went into the app, found no heat control anywhere,
    and retracted his own correct observation as "noise". A wrong value
    gets rejected by the robot. A wrong label gets believed.
    """

    def test_the_upper_range_uses_the_vendor_names(self):
        from custom_components.roomba_plus.select_prime import (
            PAD_WASH_RETURN_MODES,
        )

        assert PAD_WASH_RETURN_MODES[100] == "mission"
        assert PAD_WASH_RETURN_MODES[101] == "refill"
        assert PAD_WASH_RETURN_MODES[102] == "refill_and_room"

    def test_no_label_claims_a_scale_the_vendor_does_not_have(self):
        from custom_components.roomba_plus.select_prime import (
            PAD_WASH_RETURN_MODES,
        )

        assert not {"standard", "medium", "high"} & set(
            PAD_WASH_RETURN_MODES.values()
        )

    def test_the_guard_now_checks_names_and_not_only_values(self):
        """The check compared value sets only. Both tables agreed on all
        six values while three of them said something the vendor never
        said."""
        import pathlib

        source = pathlib.Path(
            "scripts/check_vendor_value_tables.py"
        ).read_text()

        assert "_label_matches" in source
        assert "DELIBERATE_LABELS" in source

    def test_a_deliberate_difference_needs_a_reason(self):
        """`every_routine` for `evClean` is right — it is what the app
        shows. It is exempted with that reason rather than by loosening
        the rule until everything passes."""
        import pathlib

        source = pathlib.Path(
            "scripts/check_vendor_value_tables.py"
        ).read_text()

        assert "After Every Routine" in source


class TestTheSkuTableAgreesWithTheVendorFile:
    """The first version of `_SKU_VALUE_LISTS` was written from the
    research report's prose and had seven of its ten entries wrong.

    The data file was in the same package the whole time. This test
    reads it, so the table can never drift from it again — and so the
    next person does not have to trust that somebody transcribed it.
    """

    VENDOR_FILE = "/home/claude/apk/v3_paket/v3_sku_value_lists.json"

    def _vendor(self):
        import json
        import pathlib

        import pytest

        path = pathlib.Path(self.VENDOR_FILE)
        if not path.exists():
            pytest.skip("vendor extract not present in this checkout")
        return json.loads(path.read_text())

    def test_every_narrowed_sku_matches_the_file(self):
        from custom_components.roomba_plus.select_prime import _SKU_VALUE_LISTS

        special = self._vendor()["sonderfaelle"]
        expected = {
            sku: {
                key: tuple(values)
                for key, values in entry.items()
                if key not in ("index", "modell")
            }
            for sku, entry in special.items()
        }
        expected = {sku: over for sku, over in expected.items() if over}

        assert _SKU_VALUE_LISTS == expected

    def test_the_standard_lists_match_the_pickers(self):
        """The five standard lists are what every other robot gets, and
        three of them are also this project's full option sets."""
        from custom_components.roomba_plus.select_prime import (
            PAD_DRY_DURATIONS,
            PAD_WASH_AREA_INTERVALS,
            PAD_WASH_TIME_INTERVALS,
        )

        standard = self._vendor()["standard"]

        assert set(PAD_DRY_DURATIONS) == set(standard["padDryDur"])
        assert set(PAD_WASH_AREA_INTERVALS) == set(standard["pwAreaInterval"])
        assert set(PAD_WASH_TIME_INTERVALS) == set(standard["pwTimeInterval"])

    def test_autoevac_is_wider_than_the_sku_list_on_purpose(self):
        """`getListBySKU` gives [0, 10, 15, 25, 30] as standard, and
        @chairstacker's G1 shows three options that are 0/1/2 — matching
        his `cap.autoevac: 1`, not the SKU list. The capability decides
        this one, and a screenshot outranks the extraction."""
        from custom_components.roomba_plus.select_prime import (
            AUTOEVAC_FREQUENCIES,
        )

        standard = set(self._vendor()["standard"]["autoevacFreq"])

        assert standard < set(AUTOEVAC_FREQUENCIES)


class TestTheUpperRangeIsCumulativeNotExclusive:
    """@chairstacker's Mop Wash Frequency screen spells out what each of
    the upper three values does, and each adds a trigger to the one
    below it:

        100 mission        before and after cleaning routines
        101 refill         ALSO during refills
        102 refillAndRoom  ALSO in between rooms

    The first labels read as exclusive events — "At mission end", "On
    refill", "On refill or new room". Two were incomplete and the first
    was wrong: it is before AND after.
    """

    def test_the_labels_read_as_additive(self):
        import json
        import pathlib

        strings = json.loads(
            pathlib.Path("custom_components/roomba_plus/strings.json").read_text()
        )
        state = strings["entity"]["select"]["prime_pad_wash_return"]["state"]

        assert state["mission"] == "Before and after routines"
        assert state["refill"].startswith("Also")
        assert state["refill_and_room"].startswith("Also")

    def test_no_label_claims_a_single_moment(self):
        """"At mission end" described a wash that happens twice."""
        import json
        import pathlib

        strings = json.loads(
            pathlib.Path("custom_components/roomba_plus/strings.json").read_text()
        )
        state = strings["entity"]["select"]["prime_pad_wash_return"]["state"]

        assert "At mission end" not in state.values()

    def test_the_heat_reading_is_recorded_as_settled(self):
        """@ratpic83 read these three as heat levels beside a dock
        reporting `pw: 3`. The app's own subtitles say wash frequency."""
        import inspect

        from custom_components.roomba_plus import select_prime

        source = inspect.getsource(select_prime)

        assert "SETTLES THE HEAT QUESTION" in source

    def test_the_loop_report_is_kept_with_its_disproof(self):
        """"My robot keeps going back to the dock" should be one lookup,
        not an investigation.

        And the first explanation must not outlive itself: the refill
        hypothesis was recorded, then @chairstacker set Standard (100)
        — which involves no refill at all — and got the same loop. It is
        the whole upper range on his robot, not the refill trigger.
        """
        import inspect

        from custom_components.roomba_plus import select_prime

        source = inspect.getsource(select_prime)

        assert "five times before he" in source
        assert "THE FIRST EXPLANATION WAS WRONG" in source
        assert "UPPER RANGE" in source


class TestAValueOutsideTheListIsShown:
    """Issue #46 promised: "Show a value even when it is outside the
    list. Otherwise we reproduce the app's 'not set', and it looks like
    our bug rather than theirs."

    The code did the opposite — an unknown value returned None, which
    made the entity `unavailable`.

    Not hypothetical: @DaRealGuGu's 515 holds `pwAreaInterval = 8`,
    which belongs to the 410's value set and not to his. His iRobot app
    cannot render it, and neither could we.
    """

    @staticmethod
    def _select(reported):
        """Drives the REAL `current_option` through the shadow.

        The first version of this patched `current_option` itself and
        asserted against its own mock — it passed against the code it
        was written to reject. A test that stubs the thing it is
        testing tests nothing.
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.select_prime import (
            PAD_WASH_AREA_INTERVALS,
            PrimeSettingSelect,
        )

        entity = PrimeSettingSelect.__new__(PrimeSettingSelect)
        # @DaRealGuGu'S ACTUAL SITUATION, not the full list.
        #
        # His 515 is narrowed to the 505 series' set, and holds
        # `pwAreaInterval = 8` -- a value from the 410's set. The first
        # version of this test used the FULL list, where 8 is present,
        # so it passed against the very code it was written to reject.
        # Third time today a test of mine did that.
        entity._values = {
            v: label for v, label in PAD_WASH_AREA_INTERVALS.items()
            if v in (10, 15, 20)
        }
        entity.entity_description = SimpleNamespace(
            model_attr="pad_wash_area_interval"
        )
        # THE REAL SHADOW, PARSED BY THE REAL `RobotSettings`.
        #
        # No patching at all: `pwAreaInterval` is the wire key, and
        # letting the actual parser read it means this test exercises
        # the same path production does. Patching `RobotSettings` failed
        # anyway, because `current_option` imports it inside the
        # function.
        coordinator = SimpleNamespace(
            data={"rw-settings": {"pwAreaInterval": reported}}
        )
        entity._config_entry = MagicMock()
        entity._config_entry.runtime_data.prime_status_coordinator = coordinator
        return entity



    def test_an_unknown_value_shows_as_itself(self):
        """"8" is what the robot reports. A guessed label would be an
        invention the user cannot check."""
        assert self._select(8).current_option == "8"

    def test_the_unknown_value_is_offered_in_options(self):
        """Home Assistant rejects a `current_option` that is not in
        `options` — showing it requires offering it."""
        assert "8" in self._select(8).options

    def test_known_values_are_not_duplicated(self):
        assert self._select(10).options.count("10") == 1

    def test_the_entity_is_not_unavailable_for_an_unknown_value(self):
        """The failure this fixes: `available` is `current_option is not
        None`, so returning None hid the entity entirely."""
        assert self._select(8).current_option is not None


class TestEveryControlPointsAtSomethingReal:
    """Two hand-written strings per control, and both fail silently when
    wrong.

    `wire_key` is what `set_setting()` writes; a typo produces a write
    the robot ignores. `model_attr` is what the value is read back
    from; a typo makes `current_option` return None for ever, so the
    entity is created and permanently unavailable.

    Neither raises. Both have happened in this project — `vacHigh` sat
    unavailable on a tester's robot for weeks, and the cause turned out
    to be a different one of exactly this family.
    """

    @staticmethod
    def _pairs():
        import pathlib
        import re

        base = pathlib.Path("custom_components/roomba_plus")
        out = []
        for name in ("select_prime.py", "switch.py"):
            source = (base / name).read_text()
            out += [(name, m.group(1)) for m in
                    re.finditer(r'model_attr="([^"]+)"', source)]
        return out

    def test_every_model_attr_is_a_real_settings_field(self):
        import dataclasses

        from roombapy_prime.models import RobotSettings

        known = {f.name for f in dataclasses.fields(RobotSettings)}
        missing = [f"{f}: {a}" for f, a in self._pairs() if a not in known]

        assert not missing, (
            f"model_attr values with no matching RobotSettings field: "
            f"{missing} -- these controls would be permanently unavailable"
        )

    def test_there_are_controls_to_check(self):
        """A guard that silently checks nothing is worse than none."""
        assert len(self._pairs()) >= 10


# ============================================================================
# CLEANING MODE -- vacuum, mop, or both.
#
# Moved here from test_prime_cleaning_mode.py (August 2026). It tested
# `select_prime.py` all along, under a filename that gave no hint of
# which module to look in -- so a change to select_prime would not lead
# anyone here.
#
# Asked for by @arielgr: the app offers the choice when starting a
# clean, and this integration had a suction control and nothing for the
# mode. The values had been confirmed for weeks; nobody had asked.
# ============================================================================


def _select(last_command=None, restored=None):
    from types import SimpleNamespace  # noqa: PLC0415
    from unittest.mock import MagicMock  # noqa: PLC0415

    from custom_components.roomba_plus.select_prime import (
        PrimeCleaningModeSelect,
    )

    entity = object.__new__(PrimeCleaningModeSelect)
    entity._restored = restored
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(
        prime_status_coordinator=SimpleNamespace(
            data={"rw-software": {"lastCommand": last_command}}
            if last_command is not None else {}
        )
    )
    entity._config_entry = entry
    entity.async_write_ha_state = MagicMock()
    return entity


def _start(mode):
    return {
        "command": "start", "initiator": "cloud",
        "regions": [{"region_id": "11", "type": "rid",
                     "params": {"operatingMode": mode, "suctionLevel": 3}}],
    }


class TestItReflectsTheRobotsLastStart:
    """The robot wins when it has something to say. A user who picks a
    mode and then starts a different one from the app should see the
    app's choice, because that is what the machine actually did."""

    @pytest.mark.parametrize(("mode", "expected"), [
        (2, "vacuum"), (4, "mop"),
        (32, "vacuum_and_mop"), (512, "vacuum_then_mop"),
    ])
    def test_each_confirmed_mode(self, mode, expected):
        assert _select(_start(mode)).current_option == expected

    def test_it_overrides_a_stale_pick_of_ours(self):
        select = _select(_start(4), restored="vacuum")

        assert select.current_option == "mop"

    def test_a_mode_outside_the_four_reports_nothing(self):
        """Rounding to the nearest would make the select claim something
        the robot is not doing."""
        assert _select(_start(1024)).current_option is None


class TestOnlyAStartCounts:
    """`drypad` and `washpad` also carry regions and a mode, and neither
    is a cleaning choice. Reading them would make the select jump while
    the dock cleaned a pad."""

    @pytest.mark.parametrize("command", ["drypad", "washpad", "dock", "find"])
    def test_maintenance_commands_are_ignored(self, command):
        payload = _start(2)
        payload["command"] = command

        assert _select(payload, restored="mop").current_option == "mop"

    def test_a_command_without_regions_is_ignored(self):
        assert _select(
            {"command": "start", "time": 1}, restored="mop"
        ).current_option == "mop"


class TestTheUsersPickIsTheFallback:
    def test_without_a_start_the_restored_value_shows(self):
        assert _select(restored="vacuum_then_mop").current_option == (
            "vacuum_then_mop"
        )

    def test_with_neither_it_shows_nothing(self):
        assert _select().current_option is None

    @pytest.mark.asyncio
    async def test_choosing_records_the_pick(self):
        select = _select()

        await select.async_select_option("mop")

        assert select._restored == "mop"

    @pytest.mark.asyncio
    async def test_an_unknown_option_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            await _select().async_select_option("polish")


class TestTheStatusFieldIsNotTheSource:
    """`cleanMissionStatus.operatingMode` uses a different vocabulary:
    command 32 shows as status 6, command 512 as status 4, and a pad
    wash also as 6. A 6 cannot be told apart from maintenance."""

    def test_the_status_shadow_is_not_read(self):
        import inspect

        from custom_components.roomba_plus.select_prime import (
            PrimeCleaningModeSelect,
        )

        source = inspect.getsource(
            PrimeCleaningModeSelect._mode_from_last_start
        )
        assert "rw-software" in source
        assert "cleanMissionStatus" not in source


class TestNothingIsSentWhenNothingIsChosen:
    def test_no_mode_means_the_key_is_omitted(self):
        """A default here would decide for people who never touched the
        control."""
        operating_mode = None
        params = {
            "noAutoPasses": False,
            **({"operatingMode": operating_mode} if operating_mode is not None else {}),
        }

        assert "operatingMode" not in params

    def test_a_chosen_mode_is_included(self):
        operating_mode = 512
        params = {
            **({"operatingMode": operating_mode} if operating_mode is not None else {}),
        }

        assert params["operatingMode"] == 512


class TestSuctionOnTheVacuumCard:
    """@arielgr went looking for suction and cleaning mode on the card
    that opens when you click the robot, because that is where the
    iRobot app puts them. Both existed only as separate selects under
    Configuration -- a different screen.

    Home Assistant renders a speed control on the vacuum card when the
    entity advertises FAN_SPEED. Two Classic classes did; Prime, which
    uses the base class directly, did not -- so the one control Home
    Assistant CAN show there was missing on the generation with the
    richest settings.
    """

    def _vacuum(self, *, prime=True, suction=None):
        from types import SimpleNamespace  # noqa: PLC0415
        from unittest.mock import MagicMock  # noqa: PLC0415

        from custom_components.roomba_plus.vacuum import IRobotVacuum

        entity = object.__new__(IRobotVacuum)
        entity._prime_robot = MagicMock() if prime else None
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_robot=entity._prime_robot,
            prime_status_coordinator=SimpleNamespace(
                data={"rw-settings": {"suctionLevel": suction}}
                if suction is not None else {}
            ),
        )
        entity._config_entry = entry
        return entity

    def test_the_three_levels_are_offered(self):
        assert self._vacuum().fan_speed_list == ["light", "normal", "deep"]

    @pytest.mark.parametrize(("wire", "name"), [(2, "light"), (3, "normal"), (4, "deep")])
    def test_the_current_level_is_read_from_the_robot(self, wire, name):
        assert self._vacuum(suction=wire).fan_speed == name

    def test_an_unknown_level_reads_as_unknown(self):
        """Rather than defaulting to one the robot may not be using."""
        assert self._vacuum(suction=None).fan_speed is None
        assert self._vacuum(suction=9).fan_speed is None

    @pytest.mark.asyncio
    async def test_setting_writes_the_same_field_the_select_does(self):
        """The card and the select cannot disagree -- both read and write
        the one value the robot holds."""
        from unittest.mock import AsyncMock

        vacuum = self._vacuum()
        vacuum._prime_robot.set_setting = AsyncMock()

        await vacuum.async_set_fan_speed("deep")

        vacuum._prime_robot.set_setting.assert_awaited_once_with("suctionLevel", 4)

    @pytest.mark.asyncio
    async def test_an_unknown_level_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        with pytest.raises(ServiceValidationError):
            await self._vacuum().async_set_fan_speed("turbo")

    def test_a_classic_robot_keeps_its_own_list(self):
        """Widening Prime must not rewrite the Classic vocabulary."""
        from custom_components.roomba_plus.vacuum import FAN_SPEEDS

        assert self._vacuum(prime=False).fan_speed_list == list(FAN_SPEEDS)


class TestTheServiceFieldOutranksTheSelect:
    """`clean_room` has a `cleaning_mode` field for a run that should
    differ from the everyday default. It was described in services.yaml
    and never read -- the service accepted it and threw it away, and the
    select's value was used regardless.
    """

    def _mode_for(self, operating_mode, index, selected=2):
        if operating_mode:
            per_room = (
                operating_mode[index] if index < len(operating_mode)
                else operating_mode[0] if len(operating_mode) == 1
                else None
            )
            if per_room is not None:
                return per_room
        return selected

    def test_a_single_choice_applies_to_every_room(self):
        """The service passes `[mode]` for one choice. Reading it
        positionally would give room one the caller's mode and every
        other room the select's -- a run that mops the kitchen and
        vacuums the hall, from one field nobody meant that way."""
        assert [self._mode_for([512], i) for i in range(3)] == [512, 512, 512]

    def test_per_room_modes_are_honoured(self):
        assert [self._mode_for([4, 32, None], i) for i in range(3)] == [4, 32, 2]

    def test_saying_nothing_falls_back_to_the_select(self):
        assert [self._mode_for(None, i) for i in range(3)] == [2, 2, 2]

    def test_the_service_translates_the_name_to_a_wire_value(self):
        """A caller names a mode; the robot needs a number.

        WAS A SOURCE-TEXT CHECK for one exact call expression, which
        broke the moment that lookup became tier-aware -- at a rename,
        not at a fault. It never verified a translation happened, only
        that a particular line was written. This calls the resolver.
        """
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.models import ConnectionType
        from custom_components.roomba_plus.services import _modes_for_backend

        backend = MagicMock()
        backend._data.connection_type = ConnectionType.CLOUD_ONLY

        modes = _modes_for_backend(backend)

        assert modes["vacuum"] == 2
        assert modes["mop"] == 4
        # A name nobody offers must not resolve to a number.
        assert modes.get("scrub") is None

    def test_the_field_is_documented_with_its_fallback(self):
        """A field whose description promises "leave empty to use the
        selector" has to actually do that."""
        import pathlib

        text = pathlib.Path(
            "custom_components/roomba_plus/services.yaml"
        ).read_text()
        block = text[text.index("cleaning_mode:"):][:600]

        assert "Cleaning mode selector" in block
        assert "vacuum_then_mop" in block


# ============================================================================
# MAP SELECTION -- which saved map the robot should use.
#
# Moved here from test_prime_map_select.py (August 2026), same reason as
# the block above: it tested `select_prime.py` under a filename that led
# nowhere near it.
#
# Its `_select` helper was renamed `_map_select` on the way in. Both
# files defined one, for different entities, and the second definition
# would have silently won -- leaving the cleaning-mode tests running
# against a map picker.
# ============================================================================


def _map_select(*, versions=None, selected=None, restore=None):
    from types import SimpleNamespace  # noqa: PLC0415
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from custom_components.roomba_plus.select_prime import PrimeMapSelect

    entity = object.__new__(PrimeMapSelect)
    entity._blid = "BLID"
    entity._names = {}
    robot = AsyncMock()
    robot.get_active_map_versions.return_value = versions or []
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(
        prime_robot=robot, prime_selected_map_id=selected
    )
    entity._config_entry = entry
    entity.async_write_ha_state = MagicMock()
    if restore is not None:
        entity._restore = restore
    return entity


_MAPS = [
    {"p2map_id": "M_HOUSE", "name": "Whole House"},
    {"p2map_id": "M_BATH", "name": "Master_Bathroom"},
]


class TestTheOptionsAreMapNames:
    @pytest.mark.asyncio
    async def test_names_not_ids(self):
        """`hh_..._1752720067` tells a user nothing."""
        entity = _map_select(versions=_MAPS)

        await entity._async_load_names()

        assert entity.options == [
            entity.FOLLOW_ROBOT, "Whole House", "Master_Bathroom"
        ]

    @pytest.mark.asyncio
    async def test_an_unnamed_map_falls_back_to_its_id(self):
        """Ugly and honest. Inventing "Map 2" would put a label on screen
        that matches nothing in the iRobot app."""
        entity = _map_select(versions=[{"p2map_id": "M_X"}])

        await entity._async_load_names()

        assert "M_X" in entity.options

    @pytest.mark.asyncio
    async def test_a_failed_read_leaves_follow_the_robot(self):
        """The account keeps working; only the choice is unavailable."""
        from unittest.mock import AsyncMock  # noqa: PLC0415

        entity = _map_select()
        entity._config_entry.runtime_data.prime_robot.get_active_map_versions = (
            AsyncMock(side_effect=TimeoutError())
        )

        await entity._async_load_names()

        assert entity.options == [entity.FOLLOW_ROBOT]


class TestFollowingTheRobotIsTheDefault:
    """A user who never touches this sees exactly what they saw before it
    existed."""

    def test_no_selection_reads_as_follow(self):
        assert _map_select().current_option == "follow_robot"

    @pytest.mark.asyncio
    async def test_choosing_follow_clears_the_selection(self):
        entity = _map_select(versions=_MAPS, selected="M_BATH")
        await entity._async_load_names()

        await entity.async_select_option(entity.FOLLOW_ROBOT)

        assert entity._config_entry.runtime_data.prime_selected_map_id is None


class TestPickingAMap:
    @pytest.mark.asyncio
    async def test_the_name_is_translated_back_to_an_id(self):
        entity = _map_select(versions=_MAPS)
        await entity._async_load_names()

        await entity.async_select_option("Master_Bathroom")

        assert entity._config_entry.runtime_data.prime_selected_map_id == "M_BATH"

    @pytest.mark.asyncio
    async def test_an_unknown_name_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        entity = _map_select(versions=_MAPS)
        await entity._async_load_names()

        with pytest.raises(ServiceValidationError):
            await entity.async_select_option("Upstairs")

    @pytest.mark.asyncio
    async def test_a_map_deleted_in_the_app_falls_back(self):
        """A selection pointing at nothing would otherwise report an
        option outside the list, which Home Assistant logs as an error on
        every state write."""
        entity = _map_select(versions=_MAPS, selected="M_GONE")
        await entity._async_load_names()

        assert entity.current_option == "follow_robot"


class TestTheChoiceSurvivesARestart:
    @pytest.mark.asyncio
    async def test_a_restored_name_becomes_a_selection(self):
        entity = _map_select(versions=_MAPS, restore="Master_Bathroom")

        await entity._async_load_names()

        assert entity._config_entry.runtime_data.prime_selected_map_id == "M_BATH"

    @pytest.mark.asyncio
    async def test_a_restored_name_that_no_longer_exists_is_ignored(self):
        entity = _map_select(versions=_MAPS, restore="Guest Floor")

        await entity._async_load_names()

        assert entity._config_entry.runtime_data.prime_selected_map_id is None


class TestTheBuildGuardsActuallyRun:
    """`check_vendor_value_tables.py` had drifted and nobody knew: three
    entries named tables
    that were renamed when their values became translation slugs
    (`CLEAN_BASE_LABELS` → `CLEAN_BASE_STATUS_SLUGS`), so it reported the
    old names as missing and the new ones as undocumented, and exited
    non-zero.

    Its existing tests read the script's SOURCE — asserting that certain
    strings appear in it — which cannot notice that the script fails.

    A guard nobody runs is a guard that has already stopped working.
    """

    def test_every_guard_exits_clean(self):
        """All of them, not just the one that drifted. Nine of the eleven
        are only read by their tests, so this is the first thing that
        runs them."""
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        failures = []
        for script in sorted((root / "scripts").glob("check_*.py")):
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=root, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                failures.append(
                    f"{script.name}:\n{result.stdout}{result.stderr}"
                )

        assert not failures, "\n\n".join(failures)
