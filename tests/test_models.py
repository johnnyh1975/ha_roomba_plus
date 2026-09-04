"""Tests for custom_components.roomba_plus.models.

NEW (V4/Prime prep). Previously models.py had no dedicated test file --
MapCapability was only ever exercised inline within other test files.
ConnectionType gets its own small file since it's genuinely new, not
yet referenced anywhere else to piggyback tests onto."""

from __future__ import annotations

from custom_components.roomba_plus.models import ConnectionType, MapCapability


class TestConnectionType:
    """ConnectionType — NEW (V4/Prime prep, July 2026). Deliberately not
    yet referenced by RoombaData or any runtime code -- see the enum's
    own docstring for why that's a separate, later step."""

    def test_local_push_value(self):
        assert ConnectionType.LOCAL_PUSH.value == "local_push"

    def test_cloud_only_value(self):
        assert ConnectionType.CLOUD_ONLY.value == "cloud_only"

    def test_exactly_two_members(self):
        """Guard against an accidental third value being added without
        updating the places that will eventually branch on this enum."""
        assert set(ConnectionType) == {ConnectionType.LOCAL_PUSH, ConnectionType.CLOUD_ONLY}

    def test_is_independent_of_map_capability(self):
        """Orthogonal dimensions, deliberately -- a value here says
        nothing about map/room richness, and vice versa."""
        assert not issubclass(ConnectionType, MapCapability)
        assert not issubclass(MapCapability, ConnectionType)


class TestRoombaDataHasNoDeadFields:
    """Guards against scaffolding outliving the feature it served.

    v3.5.0 removed the F6a/F6b Repair Issues and left four fields
    behind. Two were never written again; two were recomputed on every
    cloud update -- including a median over ten mission records, in two
    separate places -- for a consumer that had not existed for two
    minor versions.

    What made it survive: a comment claiming "this now only maintains
    the cached values other code reads". An assertion that a consumer
    exists is exactly what stops anyone looking for one.

    This test does the looking."""

    def _fields(self):
        import dataclasses

        from custom_components.roomba_plus.models import RoombaData

        return {f.name for f in dataclasses.fields(RoombaData)}

    def test_the_four_removed_fields_stay_removed(self):
        gone = {
            "consecutive_declining_speed", "consecutive_battery_warn",
            "dirt_density_rising", "recharge_fraction_value",
        }

        assert not (gone & self._fields())

    def test_the_two_that_are_still_read_are_still_there(self):
        """The other half of the guard: this must not become an excuse
        to delete fields that something depends on."""
        kept = {"cleaning_speed_trend_value", "battery_retention_value"}

        assert kept <= self._fields()

    def test_every_field_is_referenced_somewhere_in_production(self):
        """The general form. A field nothing touches is either dead or
        a feature someone forgot to finish -- both worth knowing about
        before it has been true for two releases."""
        import re
        from pathlib import Path

        source_dir = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        prod = "\n".join(
            p.read_text(encoding="utf-8")
            for p in source_dir.glob("*.py")
            if p.name != "models.py"
        )

        orphans = sorted(
            f for f in self._fields()
            if not re.search(rf"(\.{f}\b|\b{f}\s*=|[\"']{f}[\"'])", prod)
        )

        assert not orphans, (
            f"RoombaData fields nothing outside models.py references: {orphans}. "
            "Either wire them up or remove them -- an unused field in a shared "
            "container reads as intentional to whoever comes next."
        )


class TestBatteryContactStateStaysContained:
    """The six battery-contact fields form one state machine with
    invariants that live only in async_check_battery_contact_issue().

    The comment on them says they are touched from exactly one place.
    A comment asserting containment is worth very little on its own --
    this project has already lost two minor versions to a comment that
    claimed a consumer existed when none did. So this checks it.

    If this fails, the choice is real: either bring the new caller into
    the same function, or extract the state into its own object with
    the invariants enforced in methods. What must not happen is a
    second place quietly maintaining half of them."""

    _FIELDS = (
        "last_batpct_value", "last_batpct_at",
        "consecutive_battery_contact_anomaly", "current_charge_cycle_peak",
        "charge_cycle_peaks", "was_charging",
    )

    def _writers(self, field_name):
        import re
        from pathlib import Path

        source_dir = Path(__file__).resolve().parent.parent / "custom_components" / "roomba_plus"
        return {
            p.name
            for p in source_dir.glob("*.py")
            if p.name != "models.py"
            and re.search(rf"\.{field_name}\s*(=[^=]|\.append\()", p.read_text(encoding="utf-8"))
        }

    def test_only_repairs_writes_them(self):
        for field_name in self._FIELDS:
            assert self._writers(field_name) <= {"repairs.py"}, (
                f"{field_name} is now written outside repairs.py. These six fields share "
                "invariants that are only enforced in async_check_battery_contact_issue -- "
                "a second writer breaks them silently."
            )

    def test_they_are_all_still_present(self):
        """Guards the other direction: removing one of these without the
        others would leave the state machine half-built."""
        import dataclasses

        from custom_components.roomba_plus.models import RoombaData

        fields = {f.name for f in dataclasses.fields(RoombaData)}

        assert set(self._FIELDS) <= fields

    def test_the_containment_claim_is_documented(self):
        """The comment is what tells the next person why a stray write
        is a bug rather than a convenience."""
        import inspect

        from custom_components.roomba_plus import models

        source = inspect.getsource(models)

        assert "ONE STATE MACHINE" in source
