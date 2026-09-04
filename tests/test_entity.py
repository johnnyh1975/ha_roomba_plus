"""Tests for entity.py's IRobotEntity base class.

v3.4.2 NULL-REGRESSION — this base class had NO dedicated test file before
this addition, despite being the constructor every single entity in this
integration runs through. Found via the same systematic sweep that fixed
select.py/cloud_coordinator.py's active_pmapv_details null-guard gap:
`hwPartsRev`/`dock` sub-objects being explicitly `null` (not just absent)
in a robot's local MQTT state — the same confirmed-real class of bug as
`cleanMissionStatus: None`/`bbrun: None` elsewhere in this codebase — would
previously raise AttributeError right in DeviceInfo construction inside
__init__, before HA even finishes setting up the entity. A single affected
robot would have broken every entity's setup, not just one.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roomba_plus.entity import IRobotEntity


def _make_roomba(reported: dict) -> MagicMock:
    r = MagicMock()
    r.master_state = {"state": {"reported": reported}}
    return r


class TestIRobotEntityInitNullGuards:
    def test_init_survives_explicit_null_hwpartsrev(self):
        """hwPartsRev: null must not crash DeviceInfo construction."""
        roomba = _make_roomba({"hwPartsRev": None, "sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info["serial_number"] is None

    def test_init_survives_missing_hwpartsrev(self):
        """hwPartsRev absent entirely — the pre-existing, already-safe case,
        kept here so both null and missing are covered side by side."""
        roomba = _make_roomba({"sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info["serial_number"] is None

    def test_init_survives_explicit_null_hwpartsrev_no_mac_fallback(self):
        """hwPartsRev: null with no top-level `mac` fallback either —
        mac_address resolution must not raise and should end up None."""
        roomba = _make_roomba({"hwPartsRev": None, "sku": "i755840"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity._attr_device_info.get("connections") in (None, set())

    def test_dock_tank_level_survives_explicit_null_dock(self):
        roomba = _make_roomba({"dock": None, "sku": "m611020"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity.dock_tank_level is None

    def test_dock_tank_level_normal_case_unaffected(self):
        roomba = _make_roomba({"dock": {"tankLvl": 42}, "sku": "m611020"})
        entity = IRobotEntity(roomba, "BLID123")
        assert entity.dock_tank_level == 42


class TestPrimeDeviceInfo:
    """REAL BUG FOUND AND FIXED (architecture review, not a field
    report): every Prime entity passes roomba=None (no roombapy Roomba
    object exists for a cloud-only device) -- IRobotEntity.__init__
    previously always built DeviceInfo from roomba_reported_state(None)
    == {}, regardless of connection type. Every Prime robot's device
    page showed a generic "Roomba XXXX" name, no model, no serial, no
    firmware version -- despite PrimeFirmwareVersionSensor and others
    already showing the SAME underlying data correctly as individual
    sensors. Device-level info and sensor-level info are entirely
    separate code paths; only the sensor one had ever been built
    correctly for Prime."""

    def _make_prime_config_entry(self, title="Bogdana", serial_info=None, software_shadow=None):
        config_entry = MagicMock()
        config_entry.title = title
        config_entry.runtime_data.prime_serial_info = serial_info
        config_entry.runtime_data.prime_status_coordinator.data = (
            {"rw-software": software_shadow} if software_shadow is not None else {}
        )
        return config_entry

    def test_name_comes_from_config_entry_title(self):
        """config_entry.title has ALWAYS correctly held the real robot
        name since this project's very first Prime release (set at
        onboarding time) -- no migration needed for already-configured
        installs, unlike model/serial/firmware below."""
        config_entry = self._make_prime_config_entry(title="Bogdana")

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["name"] == "Bogdana"

    def test_falls_back_to_blid_when_title_is_empty(self):
        config_entry = self._make_prime_config_entry(title="")

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["name"] == "Roomba D123"

    def test_model_and_serial_from_prime_serial_info(self):
        from roombapy_prime.models import RobotSerialInfo

        serial_info = RobotSerialInfo(serial_number="SN123456", sku="G185020", family="Roomba Combo")
        config_entry = self._make_prime_config_entry(serial_info=serial_info)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["serial_number"] == "SN123456"
        assert entity._attr_device_info["model"] == "G185020"
        assert entity._attr_device_info["model_id"] == "G185020"

    def test_model_falls_back_to_family_when_sku_missing(self):
        from roombapy_prime.models import RobotSerialInfo

        serial_info = RobotSerialInfo(serial_number="SN123456", sku=None, family="Roomba Combo")
        config_entry = self._make_prime_config_entry(serial_info=serial_info)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["model"] == "Roomba Combo"

    def test_missing_serial_info_degrades_gracefully_not_raises(self):
        config_entry = self._make_prime_config_entry(serial_info=None)

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["serial_number"] is None
        assert entity._attr_device_info["model"] is None

    def test_firmware_version_from_coordinator_data(self):
        config_entry = self._make_prime_config_entry(software_shadow={"softwareVer": "p25-405+9.3.7"})

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["sw_version"] == "p25-405+9.3.7"

    def test_missing_coordinator_data_degrades_gracefully_not_raises(self):
        config_entry = self._make_prime_config_entry()
        config_entry.runtime_data.prime_status_coordinator = None

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["sw_version"] is None

    def test_no_config_entry_at_all_falls_back_to_original_classic_behavior(self):
        """roomba=None with NO config_entry either (shouldn't happen in
        practice for a real Prime entity, but must not crash) -- same
        as the original, pre-fix behavior."""
        entity = IRobotEntity(None, "BLID123")

        assert entity._attr_device_info["name"] == "Roomba D123"
        assert entity._attr_device_info["model"] is None

    def test_manufacturer_is_always_irobot(self):
        config_entry = self._make_prime_config_entry()

        entity = IRobotEntity(None, "BLID123", config_entry)

        assert entity._attr_device_info["manufacturer"] == "iRobot"


class TestPrimeNeverRenamesItsOwnDevice:
    """`_async_update_device_name` resolves from `vacuum_state`, which is
    `{}` on a CLOUD_ONLY entry by design — so it fell through to
    `Roomba {blid[-4:]}` and overwrote the name
    `_build_prime_device_info` had just set from `config_entry.title`.

    Two sources for one name, and this one runs from EVERY entity's
    `async_added_to_hass`.

    @ratpic83 caught it from the far end: `friendly_name` flipping
    between "MalleHausMaus" and "Roomba 6099" across 72 entities at
    once, four times in an hour. Each flip is a 72-entity state burst,
    and every connected websocket client was dropped with "4096 pending
    messages" — a wall-mounted tablet and two browser sessions
    together, which is what ruled out a slow client.
    """

    def test_the_rename_is_skipped_without_a_classic_robot(self):
        import asyncio
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.entity import IRobotEntity

        entity = IRobotEntity.__new__(IRobotEntity)
        entity.vacuum = None
        entity.vacuum_state = {}
        entity._blid = "31B8091056099"
        entity.hass = MagicMock()

        with patch(
            "custom_components.roomba_plus.entity.dr.async_get"
        ) as registry:
            asyncio.run(entity._async_update_device_name())

        assert not registry.called, (
            "the device registry was touched on a Prime entry -- the name "
            "is decided once at config entry level and re-asserting a "
            "fallback over it is strictly destructive"
        )

    def test_classic_still_renames(self):
        """The method exists for a real reason: without it a Classic
        device keeps whatever HA stored at first setup, which may be the
        MAC address."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus.entity import IRobotEntity

        entity = IRobotEntity.__new__(IRobotEntity)
        entity.vacuum = MagicMock()
        entity.vacuum_state = {"name": "Kitchen bot"}
        entity._blid = "31B8091056099"
        entity.hass = MagicMock()
        entity._attr_device_info = {"identifiers": {("roomba_plus", "x")}}

        with patch(
            "custom_components.roomba_plus.entity.dr.async_get"
        ) as registry:
            registry.return_value.async_get_device.return_value = None
            asyncio.run(entity._async_update_device_name())

        assert registry.called

    def test_the_fallback_name_is_what_was_overwriting(self):
        """`_resolve_name({}, blid)` is where "Roomba 6099" came from —
        the last four characters of the BLID, which is exactly what
        appeared in his registry."""
        from custom_components.roomba_plus.entity import IRobotEntity

        assert IRobotEntity._resolve_name({}, "31B8091056099") == "Roomba 6099"


class TestEveryReaderOfConfigEntryIsGuaranteedOne:
    """`IRobotEntity._config_entry` is declared non-Optional, which is a
    cast: the parameter still defaults to None because 81 subclasses
    call `super().__init__(roomba, blid)` and assign the field
    themselves afterwards.

    The declaration is only honest while every class that READS the
    field is guaranteed to have one — directly, or through an inherited
    `__init__`. This checks that, across the whole component, including
    inheritance chains.

    Without it, the cast would let mypy wave through a real None access
    — the exact shape of six crashes found by hand this week.
    """

    @staticmethod
    def _analyse():
        import ast
        import pathlib

        info = {}
        for p in pathlib.Path("custom_components/roomba_plus").glob("*.py"):
            for cls in [
                n for n in ast.walk(ast.parse(p.read_text()))
                if isinstance(n, ast.ClassDef)
            ]:
                init = next(
                    (f for f in cls.body
                     if isinstance(f, ast.FunctionDef) and f.name == "__init__"),
                    None,
                )
                required = False
                if init is not None:
                    args = [a.arg for a in init.args.args][1:]
                    ndef = len(init.args.defaults)
                    optional = set(args[len(args) - ndef:]) if ndef else set()
                    required = (
                        "config_entry" in args and "config_entry" not in optional
                    )
                info[cls.name] = {
                    "file": p.name,
                    "bases": [
                        b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                        for b in cls.bases
                    ],
                    "has_init": init is not None,
                    "required": required,
                    "sets": init is not None and (
                        any(
                            isinstance(n, ast.Attribute)
                            and n.attr in ("_config_entry", "_entry")
                            and isinstance(n.ctx, ast.Store)
                            for n in ast.walk(init)
                        )
                        # Passing it straight to super() is the better
                        # form and has to count: the base assigns it.
                        or any(
                            isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute)
                            and n.func.attr == "__init__"
                            and (
                                len(n.args) >= 3
                                or "config_entry" in {k.arg for k in n.keywords}
                            )
                            for n in ast.walk(init)
                        )
                    ),
                    "reads": any(
                        isinstance(n, ast.Attribute)
                        and n.attr in ("_config_entry", "_entry")
                        and isinstance(n.ctx, ast.Load)
                        for n in ast.walk(cls)
                    ),
                }
        return info

    @classmethod
    def _guaranteed(cls, name, info, seen=None):
        """A base class whose own parameter is OPTIONAL guarantees
        nothing — that is exactly `IRobotEntity`, whose default is None.
        Only `required and sets` counts, or inheriting from something
        that has it."""
        seen = seen or set()
        if name in seen or name not in info:
            return False
        seen.add(name)
        d = info[name]
        if d["required"] and d["sets"]:
            return True
        if not d["has_init"]:
            return any(cls._guaranteed(b, info, seen) for b in d["bases"])
        return any(cls._guaranteed(b, info, seen) for b in d["bases"])

    def test_no_class_reads_an_entry_it_may_not_have(self):
        info = self._analyse()

        #: CHECKED INDIVIDUALLY, both safe.
        #:
        #: `IRobotVacuum` takes the entry optionally and guards every
        #: read with `if self._config_entry is not None`.
        #: `PrimeRoomCleaning` is not an entity and does not read the
        #: field at all -- it matches only because a subclass does.
        known_safe = {"IRobotVacuum", "PrimeRoomCleaning"}

        risky = [
            f"{d['file']}:{n}"
            for n, d in info.items()
            if d["reads"] and not self._guaranteed(n, info) and n not in known_safe
        ]

        assert not risky, (
            f"these read _config_entry without being guaranteed one: "
            f"{risky} -- either pass it in, or the non-Optional "
            f"declaration in IRobotEntity is no longer true"
        )

    def test_the_guard_is_actually_looking_at_something(self):
        """A census that finds nothing to check passes vacuously."""
        info = self._analyse()

        readers = [n for n, d in info.items() if d["reads"]]

        assert len(readers) >= 40
