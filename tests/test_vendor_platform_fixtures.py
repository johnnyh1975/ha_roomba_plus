"""Capability gates checked against iRobot's OWN sample robot states.

WHERE THIS DATA COMES FROM. The Prime app ships simulator responses in
`res/raw` -- one complete `state.reported` per hardware platform, kept
by the vendor for its own demo mode. Seven platforms, covering a Braava
jet m6, an i7, a j9, an s9 and three others.

WHY IT MATTERS. Every field structure this project relies on was
reconstructed from tester captures, and testers own the robots they own:
nobody in the group has an s9 or a j9 or an R111840. These fixtures cover
capability combinations no capture could reach, and they come from the
vendor rather than from us.

WHAT THEY ARE NOT. A snapshot from app version 2.2.4, and simulator data
rather than live robots -- so a disagreement between a fixture and a real
capture is a question, not a verdict. Read them as "the vendor thinks
this shape is representative", which is worth a great deal and is not the
same as "this is what your robot sends".
"""

import json
import pathlib

import pytest

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "vendor_platform_shadows.json"


def _platforms() -> dict:
    return json.loads(_FIXTURE.read_text())


class TestTheFixtureItself:
    def test_every_platform_carries_a_capability_block(self):
        for name, data in _platforms().items():
            assert data["cap"], name

    def test_the_platforms_are_meaningfully_different(self):
        """A fixture set where every entry is the same shape tests
        nothing. These differ in 27 of 32 capability keys."""
        platforms = _platforms()
        keys = {k for p in platforms.values() for k in p["cap"]}
        varying = [
            k for k in keys
            if len({str(p["cap"].get(k)) for p in platforms.values()}) > 1
        ]

        assert len(varying) > 20


class TestCapabilityValuesAreNotBooleans:
    """The failure this fixture set exists to catch.

    `dict.get(key, default)` does not protect against an explicit 0, and
    a capability flag read as a boolean turns a graduated value into
    on/off. Both mistakes have been made in this project, repeatedly.
    """

    def test_operating_mode_is_a_number_not_a_flag(self):
        """`oMode` reads 2 on three platforms and 6 on another. Anything
        treating it as truthy loses the distinction entirely."""
        values = {
            name: p["cap"]["oMode"]
            for name, p in _platforms().items() if "oMode" in p["cap"]
        }

        assert len(set(values.values())) > 1
        assert all(isinstance(v, int) for v in values.values())

    def test_an_explicit_zero_is_a_real_value(self):
        """`pp` reads 0 on every platform that reports it, and `edge`
        likewise. A gate that treats absent and zero alike would offer
        features no robot in this set has."""
        zeros = {
            name: [k for k, v in p["cap"].items() if v == 0]
            for name, p in _platforms().items()
        }

        assert any(zeros.values()), "no platform reports an explicit zero"

    def test_missing_is_not_the_same_as_zero(self):
        """`carpetBoost` is absent on five platforms and present on two.
        Absent means the robot did not say; zero means it said no."""
        platforms = _platforms()
        absent = [n for n, p in platforms.items() if "carpetBoost" not in p["cap"]]
        present = [n for n, p in platforms.items() if "carpetBoost" in p["cap"]]

        assert absent and present


class TestPadValuesHaveTwoSpellings:
    """Found by these fixtures, not by a tester.

    `san_marino` (Braava jet m6) reports `reusablewet` and `stingray`
    reports `reusableWet` -- the same value with different casing, from
    the vendor's own sample data. PAD_LABELS knows only the camelCase
    form, so a Braava would have shown "Unknown".

    No tester could have found this: it needs two robots that differ
    only in how the vendor spelled a string.
    """

    def test_both_spellings_appear_in_the_vendors_own_data(self):
        pads = {
            name: p["detectedPad"]
            for name, p in _platforms().items() if p.get("detectedPad")
        }

        assert "reusablewet" in pads.values()
        assert "reusableWet" in pads.values()

    def test_every_reported_pad_value_resolves_to_a_label(self):
        from custom_components.roomba_plus.const import PAD_LABELS

        for name, p in _platforms().items():
            value = p.get("detectedPad")
            if value is None:
                continue
            assert PAD_LABELS.get(value) is not None, f"{name}: {value!r}"


class TestBraavaDetectionAgainstRealSkus:
    """`is_braava()` decides on the SKU prefix. These are the vendor's
    own SKUs, which is a better test than any invented string."""

    def test_the_braava_is_recognised(self):
        from custom_components.roomba_plus.const import is_braava

        sku = _platforms()["san_marino"]["sku"]
        assert sku.lower().startswith("m")
        assert is_braava({"sku": sku, "detectedPad": "reusablewet"}) is True

    def test_the_vacuums_are_not(self):
        from custom_components.roomba_plus.const import is_braava

        for name in ("lewis", "ruby", "soho", "sapphire"):
            sku = _platforms()[name]["sku"]
            assert is_braava({"sku": sku}) is False, f"{name} ({sku})"

    def test_a_mopping_vacuum_is_not_a_braava(self):
        """`stingray` reports a wet pad and is not an m-series SKU --
        the case the is_mop/is_braava split exists for."""
        from custom_components.roomba_plus.const import is_braava, is_mop

        data = _platforms()["stingray"]
        state = {"sku": data["sku"], "detectedPad": data["detectedPad"]}

        assert is_mop(state) is True
        assert is_braava(state) is False
