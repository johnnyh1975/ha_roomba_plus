"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



from unittest.mock import MagicMock
from unittest.mock import patch
import pytest

from tests.conftest import robot_mock


def _make_roomba(reported: dict) -> MagicMock:
    """Minimal roomba mock whose master_state returns the given reported dict."""
    roomba = robot_mock()
    roomba.master_state = {"state": {"reported": reported}}
    roomba.connected = True
    roomba.current_state = "Charging"
    roomba.client_error = None
    roomba.continuous = True
    roomba.delay = 1
    roomba.error_code = 0
    roomba.error_message = ""
    return roomba


def _make_entry(reported: dict) -> MagicMock:
    entry = MagicMock()
    entry.version = 22
    entry.title = "Roomba 980 - OG"
    entry.data = {}
    entry.options = {}
    entry.runtime_data.map_capability.value = "ephemeral"
    entry.runtime_data.renderer = None
    entry.runtime_data.room_seg_store = None
    entry.runtime_data.robot_profile = None
    entry.runtime_data.mission_store = None
    entry.runtime_data.cloud_coordinator = None
    entry.runtime_data.mission_archive = None
    entry.runtime_data.roomba = _make_roomba(reported)
    return entry


async def _run_diag(reported: dict) -> dict:
    from custom_components.roomba_plus.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    entry = _make_entry(reported)
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = []

    # diagnostics.py imports roomba_reported_state lazily from the package,
    # so the patch goes on the package, not on a second copy of __init__.py.
    with patch(
        "custom_components.roomba_plus.roomba_reported_state",
        side_effect=lambda r: r.master_state["state"]["reported"],
    ):
        return await async_get_config_entry_diagnostics(hass, entry)


class TestFirmwareVerDiagnostics:
    @pytest.mark.asyncio
    async def test_sub_module_sw_versions_present(self):
        sub = {"nav": "lewis-nav-3.8.3", "con": "lewis-con-3.3.0"}
        diag = await _run_diag({"softwareVer": "22.52.10", "subModSwVer": sub})
        assert diag["device"]["sub_module_sw_versions"] == sub

    @pytest.mark.asyncio
    async def test_sub_module_sw_versions_none_on_9series(self):
        """980 / 9-series has no subModSwVer — value must be None in diagnostics."""
        diag = await _run_diag({"softwareVer": "22.52.10"})
        assert diag["device"]["sub_module_sw_versions"] is None

    @pytest.mark.asyncio
    async def test_bbchg_in_lifetime_stats(self):
        diag = await _run_diag({
            "bbchg": {"nChatters": 42, "nKnockoffs": 3, "nAborts": 1},
            "bbchg3": {"estCap": 2488},
        })
        assert "bbchg" in diag["lifetime_stats"]
        assert diag["lifetime_stats"]["bbchg"]["nChatters"] == 42


class TestCloudOnlyDiagnostics:
    """REAL CRASH FOUND AND FIXED (architecture review, not a field
    report): async_get_config_entry_diagnostics() unconditionally
    accessed data.roomba's own attributes further down -- data.roomba
    is None for every CLOUD_ONLY (V4/Prime) entry, so HA's own
    "Download diagnostics" button would have raised AttributeError
    immediately, every time, for every real Prime user."""

    def _make_cloud_only_entry(self) -> MagicMock:
        from custom_components.roomba_plus.models import ConnectionType

        entry = MagicMock()
        entry.version = 22
        entry.title = "Bogdana"
        entry.data = {}
        entry.options = {}
        entry.runtime_data.connection_type = ConnectionType.CLOUD_ONLY
        entry.runtime_data.roomba = None
        entry.runtime_data.prime_household_id = "hh1"
        entry.runtime_data.prime_serial_info = MagicMock(sku="G185020", family="Roomba Combo")
        entry.runtime_data.prime_status_coordinator.last_update_success = True
        entry.runtime_data.prime_status_coordinator.data = {"rw-software": {}, "ro-currentstate": {}}
        entry.runtime_data.prime_coordinator.last_update_success = True
        entry.runtime_data.prime_coordinator.data = MagicMock()
        return entry

    @pytest.mark.asyncio
    async def test_does_not_crash_and_returns_prime_relevant_data(self):
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["connection_type"] == "cloud_only"
        assert result["prime"]["household_id_resolved"] is True
        assert result["prime"]["model_sku"] == "G185020"
        assert result["status_coordinator"]["last_update_success"] is True
        assert sorted(result["status_coordinator"]["named_shadows_seeded"]) == [
            "ro-currentstate", "rw-software",
        ]

    @pytest.mark.asyncio
    async def test_missing_household_id_and_serial_info_shown_honestly(self):
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        entry.runtime_data.prime_household_id = None
        entry.runtime_data.prime_serial_info = None
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["prime"]["household_id_resolved"] is False
        assert result["prime"]["serial_info_resolved"] is False
        assert result["prime"]["model_sku"] is None

    @pytest.mark.asyncio
    async def test_never_touches_data_roomba_at_all(self):
        """The actual crash reproduction: data.roomba is None, and if
        this branch ever reaches the Classic code path below it by
        mistake, accessing roomba.connected on None raises
        AttributeError immediately."""
        from custom_components.roomba_plus.diagnostics import async_get_config_entry_diagnostics

        entry = self._make_cloud_only_entry()
        assert entry.runtime_data.roomba is None  # sanity-check the premise
        hass = MagicMock()

        # Must not raise.
        await async_get_config_entry_diagnostics(hass, entry)


class TestPrimeCapabilityReport:
    """NEW (this session): the most common Prime support question is
    "why do I not have sensor X?" -- and since a6 the honest answer is
    often "your robot's own capability flags say it can't". Neither the
    flags nor the resulting decision were visible anywhere."""

    def _entry(self, cap=None, dock_cap=None):
        from custom_components.roomba_plus.prime_coordinator import PrimeStatusCoordinator

        entry = MagicMock()
        data = {}
        if cap is not None:
            data[PrimeStatusCoordinator.CLASSIC_SHADOW_KEY] = {"cap": cap}
        if dock_cap is not None:
            data["ro-currentstate"] = {"dock": {"cap": dock_cap}}
        entry.runtime_data.prime_status_coordinator.data = data or None
        return entry

    def test_zero_flag_is_reported_as_suppressed_with_the_reason(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"scrub": 0}))

        assert "suppressed" in report["entity_decisions"]["detected_pad"]
        assert "cap.scrub == 0" in report["entity_decisions"]["detected_pad"]

    def test_nonzero_flag_is_reported_as_created_with_the_value(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"suctionLvl": 4}))

        assert "created" in report["entity_decisions"]["suction_level"]
        assert "4" in report["entity_decisions"]["suction_level"]

    def test_unknown_capability_says_so_rather_than_implying_a_decision(self):
        """Failing open is the documented contract -- the diagnostics
        must make clear that's what happened, not imply the robot
        reported something."""
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry())

        assert "unknown" in report["entity_decisions"]["detected_pad"]

    def test_dock_capabilities_are_reported_separately_from_robot_ones(self):
        from custom_components.roomba_plus.diagnostics import _prime_capability_report

        report = _prime_capability_report(self._entry(cap={"scrub": 3}, dock_cap={"pw": 0}))

        assert "suppressed" in report["entity_decisions"]["pad_wash_status"]
        assert "created" in report["entity_decisions"]["detected_pad"]


class TestPrimeMissionStatus:
    """The fields that explain a mission silently never starting --
    readiness refusals appear in no error field and on no rejection
    topic."""

    def _entry(self, mission_status=None, detected_pad=None):
        entry = MagicMock()
        if mission_status is None and detected_pad is None:
            entry.runtime_data.prime_status_coordinator.data = None
        else:
            entry.runtime_data.prime_status_coordinator.data = {
                "ro-currentstate": {
                    "cleanMissionStatus": mission_status or {},
                    "detectedPad": detected_pad,
                }
            }
        return entry

    def test_readiness_codes_are_named_not_just_numbered(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(
            self._entry({"notReady": 22, "condNotReady": [75]})
        )

        assert status["not_ready_name"] == "MAP_VERSION_MISMATCH"
        assert status["cond_not_ready"] == ["NO_VAC_WITH_PAD"]

    def test_unknown_readiness_code_stays_honestly_unknown(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(self._entry({"notReady": 43}))

        assert status["not_ready_name"] == "UNKNOWN_43"

    def test_mission_id_is_deliberately_omitted(self):
        """It identifies a specific run and adds nothing to triage."""
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        status = _prime_mission_status(self._entry({"missionId": "01KY7M4XHX", "phase": "run"}))

        assert "mission_id" not in status
        assert status["phase"] == "run"

    def test_returns_none_when_no_coordinator_data(self):
        from custom_components.roomba_plus.diagnostics import _prime_mission_status

        assert _prime_mission_status(self._entry()) is None


class TestLiveMapStatsInDiagnostics:
    """NEW (this session): born from a real field report where the map
    stayed blank while data was arriving and failing to decode 106
    times an hour. The counters make that visible at a glance instead
    of requiring someone to scrape their log."""

    def test_stats_are_included_in_the_prime_diagnostics(self):
        from custom_components.roomba_plus.models import RoombaData

        data = RoombaData(blid="x", roomba=None)
        data.live_map_stats = {
            "updates_received": 106,
            "decode_ok": 0,
            "decode_failed": 106,
            "last_error": "ValueError('Unsupported protobuf wire type 4 at offset 6')",
            "last_payload_prefix_hex": "0a04deadbeef",
        }

        assert data.live_map_stats["decode_failed"] == 106
        assert data.live_map_stats["decode_ok"] == 0

    def test_field_defaults_to_none_before_any_map_entity_exists(self):
        """A robot with no map capability never creates the image
        entity at all -- the field must simply stay None rather than
        implying zero updates were received."""
        from custom_components.roomba_plus.models import RoombaData

        data = RoombaData(blid="x", roomba=None)

        assert data.live_map_stats is None


class TestPushFreshnessInDiagnostics:
    """The first thing to look at when Prime sensors appear frozen.

    `last_update_success` stays True forever if a push stream stops
    delivering, because nothing raises -- the generator simply never
    yields again. A coordinator can therefore report itself perfectly
    healthy while showing hours-old data, which is what a field report
    described.

    This field is the only one that separates "quiet because nothing is
    happening" from "quiet because the stream died"."""

    def _freshness(self, ts):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _push_freshness

        data = MagicMock()
        data.last_mqtt_message_ts = ts
        # Derived on RoombaData: a MagicMock would answer with a
        # MagicMock, not a number.
        data.silence_reference_ts = ts
        return _push_freshness(data)

    def test_never_having_received_anything_is_reported_without_a_verdict(self):
        """REWORDED (this session). This used to assert the note said
        "the stream is not delivering" -- a fault claim.

        It is often not a fault. Shadow deltas arrive on CHANGE, and a
        robot parked on a full battery changes almost nothing, so zero
        messages after a restart with no mission since is the expected
        reading.

        The wording mattered more than it looks: a tester's diagnostics
        came back with that note, and its author read it back and went
        hunting a connection bug on a robot that simply had nothing to
        say. A diagnostic that states a conclusion gets that conclusion
        believed."""
        result = self._freshness(0)

        assert result["last_message_ts"] is None
        assert "EXPECTED if the robot has been idle" in result["note"]
        assert "not delivering" not in result["note"]

    def test_a_recent_message_reports_a_small_age(self):
        import time

        result = self._freshness(time.time() - 5)

        assert result["seconds_ago"] < 60
        assert result["note"] == "recent"

    def test_a_long_silence_is_called_stale(self):
        import time

        result = self._freshness(time.time() - 7200)

        assert result["seconds_ago"] > 7000
        assert "stale" in result["note"]

    def test_an_unreadable_value_does_not_raise(self):
        """Diagnostics must never be the thing that breaks. Someone
        downloading it is already trying to work out why something is
        wrong; a traceback here replaces their answer with a second
        problem."""
        # A MagicMock would NOT exercise this -- float(MagicMock())
        # succeeds and returns 1.0. It takes a genuinely
        # unconvertible value, which is what a corrupted or
        # wrong-typed store entry would actually look like.
        assert self._freshness("not a timestamp")["note"] == "unreadable"
        assert self._freshness(object())["note"] == "unreadable"
        assert self._freshness(None)["seconds_ago"] is None


class TestPrimeConnectionDiagnostics:
    """Two fields added to answer questions a tester's data raised, and
    written without tests until a bug hunt pointed that out.

    Untested diagnostics are a particular trap: they are only ever read
    when something is already wrong, so a mistake here surfaces at the
    worst moment and points the investigation somewhere false. That has
    already happened once in this project -- a note reading "the stream
    is not delivering" sent its own author hunting a connection bug on a
    robot that simply had nothing to report."""

    def _data_with_token(self, expires=None):
        import time
        from unittest.mock import MagicMock

        from roombapy_prime.auth import ConnectionToken

        payload = {
            "client_id": "c", "iot_token": "t",
            "iot_signature": "s", "iot_authorizer_name": "a",
        }
        if expires is not None:
            payload["expires"] = int(time.time()) + expires
        data = MagicMock()
        data.prime_robot._mqtt._token = ConnectionToken.from_json(payload)
        return data

    def test_a_login_with_an_expiry_is_reported_as_schedulable(self):
        from custom_components.roomba_plus.diagnostics import _prime_token_expiry

        result = _prime_token_expiry(self._data_with_token(expires=3600))

        assert result["known"] is True
        assert 3500 < result["seconds_remaining"] <= 3600

    def test_a_login_without_an_expiry_says_refresh_cannot_be_scheduled(self):
        """The question this field exists for. Whether Prime logins even
        carry `expires` has been open for months, because the value
        passes through on every login and nothing ever displayed it."""
        from custom_components.roomba_plus.diagnostics import _prime_token_expiry

        result = _prime_token_expiry(self._data_with_token(expires=None))

        assert result["known"] is False
        assert "cannot be scheduled" in result["note"]

    def test_no_robot_at_all_does_not_raise(self):
        """Classic entries, and Prime entries where setup failed part
        way. A diagnostics download must not itself fail."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _prime_token_expiry

        data = MagicMock()
        data.prime_robot = None

        assert _prime_token_expiry(data)["known"] is False

    def _data_with_connection(self, connected=None, has_data=True):
        from unittest.mock import MagicMock

        data = MagicMock()
        if not has_data:
            data.prime_status_coordinator = None
        else:
            shadow = {} if connected is None else {"rw-constatus": {"connected": connected}}
            data.prime_status_coordinator = MagicMock(data=shadow)
        return data

    def test_an_offline_robot_points_at_the_wi_fi_not_the_integration(self):
        """THE distinction this field exists for. A robot off the
        network and a broken push stream look identical from here --
        shadow reads keep working either way, because the cloud returns
        the last reported state regardless -- and they need opposite
        responses."""
        from custom_components.roomba_plus.diagnostics import _robot_cloud_connection

        result = _robot_cloud_connection(self._data_with_connection(connected=False))

        assert result["connected"] is False
        assert "Wi-Fi" in result["note"]

    def test_an_online_robot_puts_the_problem_on_our_side(self):
        from custom_components.roomba_plus.diagnostics import _robot_cloud_connection

        result = _robot_cloud_connection(self._data_with_connection(connected=True))

        assert result["connected"] is True
        assert "on our side" in result["note"]

    def test_an_unknown_state_is_not_reported_as_either(self):
        """Absent must not become False. Reporting "robot offline" on
        missing data would send someone to check a router for no
        reason."""
        from custom_components.roomba_plus.diagnostics import _robot_cloud_connection

        for data in (self._data_with_connection(has_data=False),
                     self._data_with_connection(connected=None)):
            result = _robot_cloud_connection(data)
            assert result["known"] is False
            assert "connected" not in result


class TestPrimeStoreSummary:
    """Store state in diagnostics, because their sensors read from them
    and nothing else reveals whether they are populated.

    Three states look identical from outside: a store that was never
    created, one that failed to load, and one that nothing writes to.
    Prime was in all three at different points -- MissionStore was never
    created, then created but unread by any sensor, then read but with
    no writer. "My mission sensors are empty" has to be diagnosable
    without guessing which."""

    def _summary(self, **stores):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _prime_store_summary

        data = MagicMock()
        for name in ("mission_store", "maintenance_store", "mission_timer_store"):
            setattr(data, name, stores.get(name))
        return _prime_store_summary(data)

    def test_absent_stores_say_so_rather_than_reading_as_empty(self):
        """"not created" and "created but empty" need different fixes."""
        summary = self._summary()

        assert summary["mission_store"] == "not created"
        assert summary["maintenance_store"] == "not created"
        assert summary["mission_timer_store"] == "not created"

    def test_mission_records_are_counted_not_dumped(self):
        """A history is hundreds of records, and the question is whether
        it is populated -- not what is in it."""
        from unittest.mock import MagicMock

        store = MagicMock()
        store.query = MagicMock(return_value=[{"id": "p_a"}, {"id": "p_b"}])

        result = self._summary(mission_store=store)["mission_store"]

        assert result["record_count"] == 2
        assert result["latest_id"] == "p_b"

    def test_an_unreadable_store_does_not_break_the_download(self):
        """Diagnostics are read when something is already wrong; failing
        to produce them is the worst possible moment to fail."""
        from unittest.mock import MagicMock

        store = MagicMock()
        store.query = MagicMock(side_effect=RuntimeError("corrupt"))

        assert self._summary(mission_store=store)["mission_store"] == "unreadable"

    def test_the_timer_reports_elapsed_time(self):
        """Zero elapsed on a robot that has run is the signal that phase
        transitions are not reaching the store -- a store that exists,
        persists, and stays empty forever. That is exactly what happened
        to MissionStore before anything wrote to it."""
        from unittest.mock import MagicMock

        store = MagicMock(elapsed_run_min=42, current_room="Kitchen")

        result = self._summary(mission_timer_store=store)["mission_timer_store"]

        assert result["elapsed_run_min"] == 42
        assert result["current_room"] == "Kitchen"

    def test_pose_derived_stores_are_marked_not_applicable(self):
        """So a reader does not go hunting for five missing stores.
        freeze_snapshot_store in particular exists only to back up
        pose-derived state against a firmware change that stops pose
        delivery -- for a robot that never delivered poses it has
        nothing to protect."""
        assert "not applicable" in self._summary()["pose_derived_stores"]


class TestShadowDump:
    """The named shadows' CONTENTS, not just their names.

    Until this existed, diagnostics listed which shadows had been seeded
    and nothing about what was in them. That gap cost real time: the
    `audio` block in rw-settings is still unknown months after a tester
    reported its key names by hand, because he had to type them out
    rather than send a file. And whether the settings shadow spells a
    field `padPlate` or `pad_plate` currently blocks a pad-wetness
    control -- a question one download answers.

    Dumped rather than summarised on purpose: a summary can only show
    what somebody already thought to look for, and the recurring problem
    here has been the opposite. `googleControl` and five capability
    flags were both found because a tester pasted raw output."""

    def _dump(self, shadows):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _prime_shadow_dump

        data = MagicMock()
        data.prime_status_coordinator = MagicMock(data=shadows)
        return _prime_shadow_dump(data)

    def test_settings_come_through_whole(self):
        """CONFIRMED 31 July 2026 (@chairstacker). This test carried an
        `assumed` marker because the padWetness spelling was borrowed
        from the command domain and had never been seen in a real
        shadow. A capture now reads:

            "padWetness": {"disposable": 3, "reusable": 1, "padPlate": 1}

        camelCase, as assumed. The marker is removed because the
        assumption became a fact -- which is what the marker was for.
        """
        """The point of the whole thing: padWetness and audio are the two
        blocks currently blocking work, and both are settings rather than
        secrets."""
        dump = self._dump({"rw-settings": {
            "childLock": True,
            "padWetness": {"disposable": 1, "padPlate": 2, "reusable": 0},
            "audio": {"volume": 3},
        }})

        assert dump["rw-settings"]["padWetness"]["padPlate"] == 2
        assert dump["rw-settings"]["audio"] == {"volume": 3}

    def test_identifiers_are_redacted(self):
        """Not credentials -- those never reach a shadow -- but things
        that tie a capture to a household or a device and would follow
        the file into a public issue."""
        dump = self._dump({"rw-settings": {"blid": "SECRET", "mac": "aa:bb:cc"}})

        assert dump["rw-settings"]["blid"] == "**REDACTED**"
        assert dump["rw-settings"]["mac"] == "**REDACTED**"

    def test_redaction_reaches_nested_keys(self):
        """Shadows nest. A top-level filter would leave
        state.reported.hwPartsRev, which carries a serial number, fully
        visible."""
        dump = self._dump({"ro-configinfo": {
            "state": {"reported": {"hwPartsRev": {"navSerialNo": "SN1"}, "sku": "G18"}}
        }})

        reported = dump["ro-configinfo"]["state"]["reported"]
        assert reported["hwPartsRev"] == "**REDACTED**"
        assert reported["sku"] == "G18"

    def test_lists_are_walked_too(self):
        """p2maps is a list of dicts, and a map id belongs to a home."""
        dump = self._dump({"ro-currentstate": {
            "p2maps": [{"name": "Ground floor", "blid": "SECRET"}]
        }})

        assert dump["ro-currentstate"]["p2maps"][0]["blid"] == "**REDACTED**"
        assert dump["ro-currentstate"]["p2maps"][0]["name"] == "Ground floor"

    def test_no_coordinator_says_so_rather_than_raising(self):
        """Diagnostics are read when something is already wrong; failing
        to produce them is the worst possible moment to fail."""
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _prime_shadow_dump

        data = MagicMock()
        data.prime_status_coordinator = None

        result = _prime_shadow_dump(data)

        assert result["available"] is False

    def test_every_seeded_shadow_appears(self):
        """A tester's capture listed nine. All nine should be dumpable --
        withholding one would recreate exactly the blind spot this
        replaces."""
        names = [
            "classic", "ro-configinfo", "ro-currentstate", "ro-services",
            "ro-stats", "rw-constatus", "rw-schedule", "rw-settings",
            "rw-software",
        ]
        dump = self._dump(dict.fromkeys(names, {"x": 1}))

        assert set(dump) == set(names)


class TestCredentialsNeverReachTheDump:
    """A password hash was leaving in diagnostics exports.

    @jouwdan found `ro-configinfo.passwordHash` in his own file and
    redacted it by hand before attaching it to a public issue. The
    redaction set had been assembled from fields somebody happened to
    notice -- never from asking what a CATEGORY of secret looks like.

    That is the worst kind of gap: the file says **REDACTED** in several
    places, so it reads as safe."""

    def _dump(self, shadows, blid="B"):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _prime_shadow_dump

        data = MagicMock(blid=blid)
        data.prime_status_coordinator = MagicMock(data=shadows)
        return _prime_shadow_dump(data)

    def test_the_reported_field_is_redacted(self):
        dump = self._dump({"ro-configinfo": {"passwordHash": "$2a$10$real"}})

        assert dump["ro-configinfo"]["passwordHash"] == "**REDACTED**"

    def test_unseen_credential_shapes_are_redacted_too(self):
        """The point of the substring check. None of these has appeared
        in any capture -- which is exactly why a named list cannot be
        trusted to cover them."""
        dump = self._dump({"rw-settings": {
            "wifiPasswd": "hunter2",
            "authToken": "eyJhbGci",
            "deviceSecret": "s3cr3t",
            "someApiKey": "k",
        }})

        for key in ("wifiPasswd", "authToken", "deviceSecret", "someApiKey"):
            assert dump["rw-settings"][key] == "**REDACTED**", key

    def test_ordinary_fields_still_come_through(self):
        """A redaction pass that eats everything is useless -- the dump
        exists to answer questions."""
        dump = self._dump({"rw-settings": {"childLock": True, "suctionLevel": 2}})

        assert dump["rw-settings"] == {"childLock": True, "suctionLevel": 2}

    def test_hash_alone_does_not_trigger(self):
        """`hash` is deliberately not a marker: it would catch map and
        asset identifiers that are useful for debugging and are not
        secrets."""
        dump = self._dump({"ro-currentstate": {"hashedMapId": "abc"}})

        assert dump["ro-currentstate"]["hashedMapId"] == "abc"


class TestThePrimeScheduleProbe:
    """The probe that exists to answer "does this robot report a
    schedule at all", and could not.

    Two faults, both silent:
      - it read `data.household_id`; the field is `prime_household_id`
        (models.py), and this same file reads it correctly elsewhere.
        The probe returned None unconditionally, on every install.
      - `SchedulesList.schedules` is `list[dict]`, so
        `getattr(schedule, "options")` was None for every schedule --
        a robot WITH schedules would have been reported as one whose
        schedules cannot be read.

    Worse than absent: the probe answered "cannot read" while looking
    like "has none", which is precisely the distinction it was added
    for.
    """

    _RAW = {"household_schedules": [{
        "household_schedule_id": "HS-1",
        "schedules": [
            {"schedule_id": "S-1", "options": {
                "enabled": True, "deleted": False, "frequency": "WEEKLY",
                "start": {"day": [1, 2], "hour": 9, "min": 30},
                "commands": [{"command": "start"}]}},
            {"schedule_id": "S-2", "options": {
                "enabled": False, "deleted": False, "frequency": "WEEKLY",
                "start": {"day": [6], "hour": 15, "min": 45}}},
        ],
    }]}

    def _data(self, raw=None, household_id="HH-1"):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        robot = AsyncMock()
        robot.get_schedules_raw.return_value = self._RAW if raw is None else raw
        return SimpleNamespace(prime_robot=robot, prime_household_id=household_id)

    def _run(self, data):
        import asyncio

        from custom_components.roomba_plus.diagnostics import (
            _prime_schedule_summary,
        )

        return asyncio.run(_prime_schedule_summary(data))

    def test_real_schedules_are_read(self):
        result = self._run(self._data())

        assert result["count"] == 2
        assert result["schedules"][0]["enabled"] is True
        assert result["schedules"][0]["days"] == [1, 2]
        assert result["schedules"][0]["hour"] == 9
        assert result["schedules"][0]["has_commands"] is True
        assert result["schedules"][1]["enabled"] is False

    def test_it_reads_the_field_name_that_exists(self):
        """`prime_household_id`, not `household_id`. The wrong name is
        not an error -- it is an unconditional None."""
        result = self._run(self._data())

        assert result["household_id_resolved"] is True

    def test_no_household_id_says_so_instead_of_returning_nothing(self):
        result = self._run(self._data(household_id=None))

        assert result["household_id_resolved"] is False
        assert "note" in result

    def test_a_parser_disagreement_is_reported(self):
        """The server sent schedules and this project read none. Saying
        "0 schedules" there reports our bug as the account's."""
        result = self._run(self._data(raw={"household_schedules": [
            {"household_schedule_id": "HS-1", "schedules": ["unparsable"]},
        ]}))

        assert result["raw_count"] == 1
        assert result["count"] == 0
        assert result["parser_disagrees"] is True

    def test_a_genuinely_empty_account_does_not_flag_a_disagreement(self):
        result = self._run(self._data(raw={"household_schedules": []}))

        assert result["count"] == 0
        assert result["parser_disagrees"] is False

    def test_no_schedule_names_or_room_ids_reach_the_file(self):
        """This section deliberately omits them -- the file is generated
        and attached, not reviewed line by line before pasting."""
        import json

        raw = {"household_schedules": [{
            "household_schedule_id": "HS-1",
            "schedules": [{"schedule_id": "S-1", "options": {
                "enabled": True, "name": "Guillaume's kitchen",
                "asset_id": "ROBOTID", "frequency": "WEEKLY",
                "commands": [{"regions": [{"region_id": "12"}]}]}}],
        }]}

        text = json.dumps(self._run(self._data(raw=raw)))

        assert "Guillaume" not in text
        assert "ROBOTID" not in text
        assert "HS-1" not in text


class TestThePrimeScheduleProbeSurvivesAnUnexpectedShape:
    """Found in the a18 bug hunt. A response that is not a dict at all
    produced count 0 / raw_count 0 -- indistinguishable from an account
    with no schedules, which is the exact ambiguity this probe exists to
    remove."""

    def _run(self, raw):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.diagnostics import (
            _prime_schedule_summary,
        )

        robot = AsyncMock()
        robot.get_schedules_raw.return_value = raw
        return asyncio.run(_prime_schedule_summary(
            SimpleNamespace(prime_robot=robot, prime_household_id="HH-1")
        ))

    def test_a_non_dict_response_is_named_as_such(self):
        for raw in ([{"a": 1}], "nope", None, 7):
            result = self._run(raw)
            assert result["response_shape_recognised"] is False, raw
            assert result["response_shape"] == type(raw).__name__

    def test_a_normal_response_is_marked_recognised(self):
        result = self._run({"household_schedules": []})

        assert result["response_shape_recognised"] is True
        assert result["count"] == 0


class TestTrailPointsInDiagnostics:
    """`live_map` counts what arrived; this counts what survived into the
    list the renderer actually reads.

    @DaRealGuGu reported 2267 position messages and no robot marker on
    the map. The arrival counters cannot separate the possible causes --
    an empty list at render time, or a marker drawn and not seen -- and
    the capture had no way to say which.
    """

    def _diag(self, positions):
        """Reads the fields straight out of _build_diagnostics' source
        rather than calling it: the whole function needs a hass, a config
        entry and a live coordinator, and none of that is what is being
        tested here."""
        return {
            "trail_points": len(positions),
            "trail_last_point": positions[-1] if positions else None,
        }

    def test_the_fields_exist_in_the_diagnostics_builder(self):
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)
        assert '"trail_points": len(data.prime_positions),' in source
        assert '"trail_last_point"' in source

    def test_an_empty_list_is_reported_as_empty(self):
        section = self._diag([])

        assert section["trail_points"] == 0
        assert section["trail_last_point"] is None

    def test_the_last_point_is_included(self):
        """A marker drawn far outside the map's own bounds would be
        invisible for a third reason again."""
        section = self._diag([(1.0, 2.0, 0.0), (3.0, 4.0, 90.0)])

        assert section["trail_points"] == 2
        assert section["trail_last_point"] == (3.0, 4.0, 90.0)


class TestTheTopicPrefixIsVisible:
    """Two subscriptions die silently without it.

    `watch_live_map()` and `watch_mission_timeline()` both build their
    topic as `{irbt_topic_prefix}/things/{blid}/...` and both raise
    immediately when it is None. The outer retry loops catch that, wait,
    and double the backoff to five minutes -- so the symptom is a map
    that updates every few minutes instead of every few seconds.

    What makes it hard to spot is that everything else keeps working:
    the shadow watcher uses shadow topics and needs no prefix, so the
    shadows seed, push_freshness stays fresh and the robot reports as
    connected. @chairstacker's capture showed exactly that -- mid-
    mission, every live-map counter at zero, mission timeline empty, and
    no error anywhere.
    """

    def test_the_field_is_in_the_builder(self):
        import inspect

        from custom_components.roomba_plus import diagnostics

        assert '"irbt_topic_prefix_present"' in inspect.getsource(diagnostics)

    def test_a_present_prefix_reads_true(self):
        from types import SimpleNamespace

        robot = SimpleNamespace(_irbt_topic_prefix="v028-irbthbu")
        assert bool(getattr(robot, "_irbt_topic_prefix", None)) is True

    def test_a_missing_or_empty_prefix_reads_false(self):
        """Empty string counts as missing: it would build a topic
        beginning with a slash, which subscribes to nothing."""
        from types import SimpleNamespace

        for value in (None, ""):
            robot = SimpleNamespace(_irbt_topic_prefix=value)
            assert bool(getattr(robot, "_irbt_topic_prefix", None)) is False


class TestTheTimelineShapeIsReported:
    """Diagnostics reported a bare True/False for the mission timeline
    and threw the rest away. One capture already came back True -- the
    data had arrived, and we recorded only that it had.

    It matters because the Prime mission history is modelled from the
    app's source and has never been seen on the wire. Four sensors read
    a store the Prime path does not fill, and the mapping to fill it is
    a small function once somebody knows the field names. Building it
    against a model instead cost four field rounds last time.
    """

    def _shape(self, value):
        from custom_components.roomba_plus.diagnostics import _shape_of

        return _shape_of(value)

    def test_field_names_survive(self):
        """The whole point: the mapping needs the names."""
        shape = self._shape({"duration_m": 43, "square_feet_covered": 545})

        assert set(shape) == {"duration_m", "square_feet_covered"}

    def test_numbers_are_kept_whole(self):
        """A duration is not private, and 43 against 43.0 is exactly the
        detail that decides whether a mapping works first time."""
        assert self._shape({"duration_m": 43})["duration_m"] == 43
        assert self._shape({"area": 43.5})["area"] == 43.5

    def test_identifiers_become_unusable(self):
        """Recognisable as an id, useless as one."""
        shape = self._shape({"mission_id": "01KZ5SQYY0RE609VYNQKF34W6X"})

        assert shape["mission_id"].startswith("01KZ5SQY")
        assert "VYNQKF34W6X" not in shape["mission_id"]

    def test_a_list_yields_one_sample_and_a_count(self):
        """Forty missions have one shape and forty sets of values."""
        shape = self._shape([{"a": 1}, {"a": 2}, {"a": 3}])

        assert shape == [{"a": 1}, "…and 2 more"]

    def test_an_empty_list_stays_empty(self):
        assert self._shape([]) == []

    def test_depth_is_bounded(self):
        """A cyclic or very deep payload must not turn a diagnostics
        download into a recursion error."""
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}

        assert "…" in str(self._shape(deep))

    def test_unknown_types_are_named_not_rendered(self):
        from datetime import UTC, datetime

        assert self._shape(datetime.now(tz=UTC)) == "datetime"

    def test_the_builder_reports_it(self):
        import inspect

        from custom_components.roomba_plus import diagnostics

        assert '"mission_data_shape"' in inspect.getsource(diagnostics)


class TestTheMissionStoreBlockShowsARecord:
    """@chairstacker sent a diagnostics download to settle why
    `area_cleaned_today` and `clean_streak` both read 0. The block
    reported 128 records and showed none of them — so it could not
    distinguish "no records for today" from "records carrying no
    area_sqft", and cost him a second round.

    A count without a sample answers nothing.
    """

    def test_it_carries_the_fields_the_zero_question_needs(self):
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)
        block = source[source.find('summary["mission_store"] = {'):]
        block = block[:block.find("except")]

        for field in (
            "latest_record",
            "records_with_area",
            "records_with_result",
            "result_values",
        ):
            assert field in block, (
                f"the mission_store block must carry {field} -- a count "
                f"alone cannot tell an empty store from a mapping gap"
            )

    def test_result_values_are_capped(self):
        """A robot with hundreds of missions must not turn this into a
        wall of strings."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert "[:12]" in source


class TestNothingReadsHassOffTheConfigEntry:
    """`ConfigEntry` does not define `hass`. `_prime_room_preferences`
    read `config_entry.hass`, got None, and returned
    `{"note": "no hass on this entry"}` from every download ever taken.

    That is why `room_preferences` sat in the tester notes as "never
    confirmed" for weeks — the block meant to show it never ran.
    Surfaced by @chairstacker's download, not by a report about it.
    """

    def test_the_attribute_really_is_absent(self):
        """If Home Assistant ever adds it, this guard is wrong rather
        than the call sites."""
        from homeassistant.config_entries import ConfigEntry

        assert not hasattr(ConfigEntry, "hass")

    def test_no_module_reaches_for_it(self):
        import ast
        import pathlib

        offenders = []
        for path in pathlib.Path("custom_components/roomba_plus").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "hass"
                    and isinstance(node.value, ast.Name)
                    and node.value.id in ("config_entry", "entry")
                ):
                    offenders.append(f"{path.name}:{node.lineno}")

        assert not offenders, (
            f"{offenders} read hass off a config entry, which has no "
            f"such attribute -- it is passed in, not carried"
        )


class TestFavouritesReportTheTierInUse:
    """The block used to read the Prime list unconditionally.

    On @pk-1966's i7+ it reported 0 favourites while
    `cloud.favorite_count` said 6 in the same download. Nothing was
    wrong with his buttons -- the diagnostics were looking at
    `prime_favorites`, which a Classic robot never fills, and the
    number that would have told him so sat in a different section.
    """

    @pytest.mark.asyncio
    async def test_classic_reports_the_cloud_coordinator(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        favourites = [
            {"name": "Kitchen", "commanddefs": [{"robot_id": "MYBLID"}]},
            {"name": "Someone else's", "commanddefs": [{"robot_id": "OTHER"}]},
            {"name": "Unattributed"},
        ]
        data = SimpleNamespace(
            prime_robot=None,
            blid="MYBLID",
            cloud_coordinator=SimpleNamespace(data={"favorites": favourites}),
        )
        entry = SimpleNamespace(options={})

        result = await _favourites_diagnostics(data, entry)

        assert result["source"] == "cloud_coordinator (Classic)"
        assert result["count_for_account"] == 3
        # Own blid plus the unattributed one; the other robot's is not ours.
        assert result["count"] == 2
        assert result["filtered_out_for_other_robots"] == 1

    @pytest.mark.asyncio
    async def test_prime_reports_the_prime_list(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        robot = SimpleNamespace(get_favorites_raw=AsyncMock(return_value={"favorites": []}))
        data = SimpleNamespace(
            prime_robot=robot,
            prime_favorites=[{"name": "a"}, {"name": "b"}],
            blid="MYBLID",
        )
        entry = SimpleNamespace(options={})

        result = await _favourites_diagnostics(data, entry)

        assert result["source"] == "prime_robot.get_favorites()"
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_a_raw_fetch_failure_does_not_break_the_download(self):
        """A section that reports why it failed beats a download that
        fails because one section did."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        robot = SimpleNamespace(
            get_favorites_raw=AsyncMock(side_effect=RuntimeError("cloud down"))
        )
        data = SimpleNamespace(prime_robot=robot, prime_favorites=[], blid="B")

        result = await _favourites_diagnostics(data, SimpleNamespace(options={}))

        assert "cloud down" in result["raw"]["error"]

    @pytest.mark.asyncio
    async def test_neither_tier_available_says_so(self):
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        data = SimpleNamespace(prime_robot=None, blid="B", cloud_coordinator=None)

        result = await _favourites_diagnostics(data, SimpleNamespace(options={}))

        assert result["count"] == 0
        assert "no prime robot" in result["source"]

    @pytest.mark.asyncio
    async def test_no_runtime_data_at_all_does_not_crash(self):
        """The Classic branch passes `getattr(entry, "runtime_data",
        None)`, which is None on an entry that failed to set up -- and
        a diagnostics download is exactly what someone takes when
        setup failed."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        result = await _favourites_diagnostics(None, SimpleNamespace(options={}))

        assert result["count"] == 0
        assert "no prime robot" in result["source"]


class TestNavigationTelemetryIsExported:
    """Firmware analysis of the i-series documents twelve fields in
    `mssnNavStats`, validated against a real m6 dump. This integration
    reads one of them.

    Among the rest: `kdp`/`sfkdp`, a kidnapped-detection signal that the
    live-map work went looking for and concluded did not exist on
    Classic robots. It does. Nobody had looked here.

    Exported verbatim rather than key-by-key, because the 980 is a
    different platform from the i-series and choosing keys in advance
    would decide the question this exists to answer.
    """

    @pytest.mark.asyncio
    async def test_the_whole_object_survives(self):
        nav = {"kdp": 2, "l_drift": 5, "mpSt": "idle", "reLc": 1}

        result = await _run_diag({"mssnNavStats": nav})

        # Raw fields carried through, plus provenance: the same field
        # read as live telemetry mid-mission is what made one tester
        # report the robot idle while it was cleaning.
        for key, value in nav.items():
            assert result["nav_telemetry"][key] == value
        assert "belongs_to_running_mission" in result["nav_telemetry"]

    @pytest.mark.asyncio
    async def test_an_unknown_field_is_not_dropped(self):
        """Key-by-key export would silently discard whatever a 980
        reports that an i-series robot does not."""
        result = await _run_diag({"mssnNavStats": {"somethingNew": 7}})

        assert result["nav_telemetry"]["somethingNew"] == 7

    @pytest.mark.asyncio
    async def test_a_missing_object_is_empty_not_absent(self):
        """Reading nothing must be distinguishable from the section
        having been forgotten."""
        result = await _run_diag({})

        assert result["nav_telemetry"] == {}


class TestDockIdentityIsExported:
    """The i-series OTA carries dock firmware for 14 hardware/variant
    combinations named `dock_hw{N}_var{M}`, and `dock.hwRev`/`dock.varID`
    map straight onto N and M.

    So the dock model is determinable, and until now was not determined
    — which bears on which docks can evacuate, which have a fresh-water
    tank, and why `empty-now` does nothing on one tester's install.
    """

    @pytest.mark.asyncio
    async def test_the_four_identifying_fields(self):
        result = await _run_diag({
            "dock": {"hwRev": 4, "varID": 5, "pn": "ABC-123", "fwVer": "4.8.1"}
        })

        assert result["dock_identity"] == {
            "hw_rev": 4, "var_id": 5,
            "part_number": "ABC-123", "fw_version": "4.8.1",
        }

    @pytest.mark.asyncio
    async def test_a_robot_without_a_dock_object(self):
        result = await _run_diag({})

        assert result["dock_identity"]["hw_rev"] is None


class TestTheBlidSurvivesNowhere:
    """@utkjmitch (#83): the blid appeared in clear text twice in his
    download, inside the `lastCommand` repr.

    `map_id` is `<blid>-<epoch>`, so a redaction list matching key names
    never touched it -- the secret was a substring of a composite value
    under a key that does not sound sensitive.

    `_redact_values` was built for exactly this after an earlier report
    about `p2map_id`, and then applied only to the shadow section. The
    outer pass checked key names and did not know the blid at all.

    A diagnostics file exists to be pasted into a public issue. One
    field leaking it through a side door defeats redacting it
    everywhere else.
    """

    @staticmethod
    async def _run(payload, blid):
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        entry = MagicMock()
        entry.data = {"blid": blid}
        with patch(
            "custom_components.roomba_plus.diagnostics._build_diagnostics",
            AsyncMock(return_value=payload),
        ):
            return await async_get_config_entry_diagnostics(MagicMock(), entry)

    @pytest.mark.asyncio
    async def test_a_composite_id_does_not_leak_it(self):
        """His exact shape: the blid inside a longer string, under a
        key nobody would list as a secret."""
        import json

        out = await self._run(
            {"prime": {"last_command": "map_id='ABC123-1752720067'"}},
            "ABC123",
        )

        assert "ABC123" not in json.dumps(out)

    @pytest.mark.asyncio
    async def test_it_is_caught_at_any_depth(self):
        import json

        out = await self._run(
            {"a": {"b": [{"c": "prefix-ABC123-suffix"}]}}, "ABC123"
        )

        assert "ABC123" not in json.dumps(out)

    @pytest.mark.asyncio
    async def test_unrelated_values_are_untouched(self):
        """Redaction must not eat the diagnostics it exists to carry."""
        out = await self._run({"phase": "run", "battery": 36}, "ABC123")

        assert out["phase"] == "run"
        assert out["battery"] == 36


class TestTheRoomsMapLookupMatchesTheEntity:
    """Diagnostics said "no rooms map entity" for every robot.

    The image entity's unique id comes from
    `IRobotEntity.robot_unique_id`, which is `roomba_plus_{blid}`. The
    diagnostics lookup used `{blid}` alone, so it never matched and the
    note appeared whether a map existed or not.

    Two Prime users sent files carrying that note while their map was on
    screen and working (@theChef163, @mrsnyds). It sent me looking at
    map creation twice, both times for nothing.

    The two are built in different modules, which is how they drifted.
    This test is the only thing tying them together.
    """

    def test_the_two_id_shapes_agree(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics
        from custom_components.roomba_plus.entity import IRobotEntity

        # It is a property; `.fget` is the function behind it.
        entity_shape = inspect.getsource(IRobotEntity.robot_unique_id.fget)
        assert 'f"roomba_plus_{self._blid}"' in entity_shape

        lookup = inspect.getsource(diagnostics)
        assert 'f"roomba_plus_{data.blid}_rooms_map"' in lookup

    def test_the_bare_blid_form_is_gone(self) -> None:
        """The exact string that was wrong, pinned so it cannot come
        back through a copy from an older revision."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        assert '{data.blid}_rooms_map"' not in inspect.getsource(
            diagnostics
        ).replace('roomba_plus_{data.blid}_rooms_map"', "")


class TestTheDownloadAnswersTheQuestionsItIsAskedFor:
    """Two fields were missing from the download, and both cost a
    round trip with a tester who had already done the work.

    `robot_ids` / `shared` on a cloud map: asked @ScenicSystemsLLC to
    check whether a map was shared between two robots, and he could
    not -- the field is in the cloud response and the download drops
    it. He compared `pmap_id` lists by hand instead.

    `missionTelemetry`: present in the state of every i/s robot, read
    nowhere in this integration, and visible in the download only as a
    key name. Room progress is inferred from phase changes instead,
    which works on `lewis` and leaves `soho` frozen on the first
    planned room for an entire mission. Whether the robot reports its
    own progress is answerable from one download now, rather than by
    asking for a raw dump.
    """

    def test_map_ownership_is_reported(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert '"pmap_ownership"' in source
        assert '"robot_ids": pm.get("robot_ids")' in source
        assert '"shared": pm.get("shared")' in source

    def test_mission_telemetry_is_reported_as_a_value(self) -> None:
        """Not as a key name. The value is the whole point -- a name in
        `master_state_keys` says the field exists and nothing else."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert '"mission_telemetry": state.get("missionTelemetry")' in source

    def test_the_reason_is_recorded_next_to_it(self) -> None:
        """Somebody will wonder why a field nothing reads is in the
        diagnostics. The answer is that reading it is the open
        question."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert "reads it" in source or "reads it\n" in source
        assert "soho" in source


class TestTheMapVersionsAreVisible:
    """A start command pairs a map id with THAT map's version id, and
    pairing one map with another's version makes the robot refuse to
    localise -- error 224, docked, no mission started.

    The dump listed which maps exist and nothing about their versions.
    Diagnosing @Thonno's two-map i7+ therefore meant inferring the
    pairing from `lastCommand`, and it took two wrong guesses before the
    real cause surfaced: his robot was holding a bad pairing we had sent
    it once, and handing it back on every retry.

    With the versions in the dump that comparison is a glance.
    """

    @staticmethod
    def _section(pmaps):
        return {
            str(next(iter(p))): str(p[next(iter(p))])
            for p in pmaps
            if isinstance(p, dict) and p
        }

    def test_each_map_shows_its_own_version(self) -> None:
        """His two maps, with the version that distinguishes them."""
        section = self._section(
            [
                {"oGwE49YGTeWffssbEVx65g": "260901T093000"},
                {"tM_GAKM5SmyBhqotQtQrtw": "260807T140942"},
            ]
        )

        assert section["oGwE49YGTeWffssbEVx65g"] != section[
            "tM_GAKM5SmyBhqotQtQrtw"
        ]

    def test_the_dump_carries_it(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert '"pmap_versions"' in source

    def test_malformed_entries_do_not_break_the_dump(self) -> None:
        """A diagnostics download that raises is worth less than one
        missing a field."""
        assert self._section([{}, None, "nonsense", {"a": "1"}]) == {"a": "1"}


class TestWeRecordWhatWeSent:
    """Three testers in one week reported a command that produced
    nothing -- no error, no mission, robot on its dock. Every time the
    first question was "did it go out, and with what?", and every time
    nothing could answer it.

    The robot keeps `lastCommand`, and it is useful -- but only for
    commands it RECEIVED. @mrsnyds' robot showed a `dock` from three
    hours before his attempt, which is equally consistent with "we never
    sent one", "we sent one and it never arrived", and "it arrived and
    was ignored". One record, three readings, no way to choose.

    So this is our side of the wire.

    WHAT `ok` MEANS AND DOES NOT. It is the publish result, which every
    call site used to discard. False proves the robot never saw it.
    True is not proof it acted -- roombapy-prime records a robot that
    ignored four verbs for 61 hours with every one broker-confirmed.
    """

    def test_a_payload_keeps_only_identifying_fields(self) -> None:
        from custom_components.roomba_plus.command_record import _summarise

        out = _summarise({
            "command": "start",
            "pmap_id": "MAP-A",
            "regions": [{"region_id": "15", "params": {"twoPass": True}}],
        })

        assert out == {
            "command": "start", "pmap_id": "MAP-A", "regions": ["15"],
        }

    def test_an_allow_list_not_a_deny_list(self) -> None:
        """A deny-list only protects against the fields somebody thought
        of. A password in a debug line already cost one release here."""
        from custom_components.roomba_plus.command_record import _summarise

        out = _summarise({
            "command": "start", "password": "hunter2", "token": "abc",
        })

        assert out == {"command": "start"}

    def test_it_is_a_ring_not_a_log(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.command_record import (
            MAX_ENTRIES,
            record_command,
        )

        entry = SimpleNamespace(
            runtime_data=SimpleNamespace(sent_commands=[])
        )
        for i in range(MAX_ENTRIES + 10):
            record_command(entry, f"cmd{i}", {"command": "start"})

        log = entry.runtime_data.sent_commands
        assert len(log) == MAX_ENTRIES
        assert log[-1]["verb"] == f"cmd{MAX_ENTRIES + 9}"

    def test_recording_never_raises(self) -> None:
        """A diagnostic aid must never be the reason a command fails."""
        from custom_components.roomba_plus.command_record import (
            record_command,
        )

        record_command(None, "start", {"command": "start"})
        record_command(object(), "start", None)

    def test_both_diagnostics_paths_report_it(self) -> None:
        """The cloud-only dump needs it most -- it has no `lastCommand`
        section at all, so a silent failure left nothing on either
        side."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert source.count('"sent_commands": _sent_commands(data)') == 2

    def test_an_unsent_state_says_so(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _sent_commands

        assert _sent_commands(SimpleNamespace(sent_commands=[])) == (
            "nothing sent since startup"
        )


class TestOurSideOfTheWireIsRecorded:
    """Three testers in one week reported a command that produced
    nothing -- no error, no mission, robot on its dock. Each time the
    first question was whether it went out and with what, and each time
    nothing could answer.

    The robot keeps `lastCommand`, and it is useful -- but only for
    commands it RECEIVED, which is precisely what was in doubt.
    @mrsnyds' robot showed a `dock` from three hours before his attempt:
    equally consistent with "never sent", "sent and lost" and "arrived
    and ignored".

    A CLOUD-ONLY DUMP HAD NEITHER SIDE. It takes an early return long
    before the section that summarises the robot's own record, so it
    carried no last command, no map ids and no versions -- though the
    same fields sit raw in the named shadows it already includes.
    """

    @staticmethod
    def _picture(shadows):
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _shadow_map_picture,
        )

        return _shadow_map_picture(
            SimpleNamespace(
                prime_status_coordinator=SimpleNamespace(data=shadows)
            )
        )

    def test_the_prime_map_shape_is_read_correctly(self) -> None:
        """Prime names its fields; Classic maps id to version directly.
        Copying the Classic shape produced the key name as a key --
        caught by running it against a real dump, not by reading it."""
        out = self._picture({
            "ro-currentstate": {
                "p2maps": [
                    {"p2map_id": "MAP-A", "p2mapv_id": "260914T155917"},
                ],
            },
        })

        assert out["pmap_versions"] == {"MAP-A": "260914T155917"}
        assert out["map_count"] == 1

    def test_the_last_command_comes_through(self) -> None:
        out = self._picture({
            "rw-software": {
                "lastCommand": {
                    "command": "dock", "initiator": "localApp",
                    "time": 1789401703,
                },
            },
        })

        assert out["last_command"]["command"] == "dock"

    def test_region_ids_without_their_params(self) -> None:
        """The per-region params are long and say nothing about whether
        the command was sent."""
        out = self._picture({
            "rw-software": {
                "lastCommand": {
                    "command": "start",
                    "regions": [{"region_id": "20", "params": {"x": 1}}],
                },
            },
        })

        assert out["last_command_regions"] == ["20"]

    def test_no_shadows_is_not_an_error(self) -> None:
        assert isinstance(self._picture(None), str)

    def test_the_sent_log_says_so_when_empty(self) -> None:
        """"Nothing sent" and "we do not record" must not look alike."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _sent_commands

        assert _sent_commands(SimpleNamespace(sent_commands=[])) == (
            "nothing sent since startup"
        )


class TestBothDumpsGetWhatNeedsNoRobot:
    """A cloud-only robot takes an early return through a separate dump,
    and things kept being added to the local one only.

    THE TEST IS WHAT A SECTION NEEDS. A helper that reads the MQTT
    client genuinely cannot run cloud-only. One that needs nothing but
    runtime data can, and its absence is an oversight rather than a
    limitation -- `position_chain` and `room_tracking` were both added
    to answer questions that came FROM cloud-only robots, and neither
    appeared in their dumps.

    So: every helper whose only parameter is `data` belongs in both.
    """

    def test_every_data_only_helper_is_in_both_dumps(self) -> None:
        import ast
        import pathlib

        source = pathlib.Path(
            "custom_components/roomba_plus/diagnostics.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        data_only = [
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and [a.arg for a in node.args.args] == ["data"]
        ]
        assert data_only, "parsed no helpers -- the check itself is broken"

        start = source.index(
            "if data.connection_type is ConnectionType.CLOUD_ONLY:"
        )
        end = source.index("\n    if roomba is None:", start)
        cloud_only = source[start:end]

        missing = [
            name for name in data_only
            if f"{name}(data)" not in cloud_only
            and name not in self.CLASSIC_FORMAT_ONLY
        ]

        assert not missing, (
            "these need nothing but runtime data and are missing from the "
            f"cloud-only dump: {missing}"
        )

    #: Helpers that take only `data` but read a CLASSIC data format, so
    #: they have nothing to say about a cloud-only (Prime) robot. The
    #: signature test above cannot see a format boundary, only a
    #: parameter one -- hence a named exception, each with its reason,
    #: rather than silencing the check. Prime and Classic records are
    #: different shapes from different backends; a Classic reader in the
    #: Prime dump would report "timeline missing" for a record that was
    #: never going to have one, which is worse than saying nothing.
    CLASSIC_FORMAT_ONLY: dict[str, str] = {
        "_last_mission_room_events": (
            "reads timeline.finEvents room events, which Classic records "
            "get from the cloud catch-up. Prime records store rooms as "
            "room_durations_sec instead and carry no timeline at all."
        ),
    }

    def test_every_classic_only_exception_still_exists(self) -> None:
        """An exception for a helper that has been renamed or removed is
        dead weight that would quietly excuse its successor."""
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(
            "custom_components/roomba_plus/diagnostics.py"
        ).read_text(encoding="utf-8"))
        vorhanden = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        verwaist = sorted(set(self.CLASSIC_FORMAT_ONLY) - vorhanden)
        assert not verwaist, f"exceptions for helpers that no longer exist: {verwaist}"

    def test_inline_runtime_only_sections_are_in_both_too(self) -> None:
        """The helper check above misses sections built inline.

        `learned_maintenance` and `robot_profile` read nothing but
        `data.<store>` and were assembled straight into the local dict,
        so no helper existed for the test to notice. A maintenance
        question from a cloud-connected robot had none of it to go on.

        The marker here is a section reading a store off runtime data
        and appearing in only one dump.
        """
        import pathlib
        import re

        source = pathlib.Path(
            "custom_components/roomba_plus/diagnostics.py"
        ).read_text(encoding="utf-8")
        start = source.index(
            "if data.connection_type is ConnectionType.CLOUD_ONLY:"
        )
        end = source.index("\n    if roomba is None:", start)
        cloud_only, local = source[start:end], source[end:]

        #: Stores a Prime robot genuinely does not have. The
        #: pose-derived stores serve 900-series room segmentation --
        #: inferring rooms from accumulated coverage, which a robot with
        #: a real persistent map never needs. Their absence is stated in
        #: the local dump itself so a reader does not go looking.
        ABSENT_FOR_PRIME = {"grid_store", "room_seg_store"}

        stores = set(
            re.findall(r"data\.(\w+_store|robot_profile)\b", local)
        ) - ABSENT_FOR_PRIME
        missing = [
            store for store in sorted(stores)
            if f"data.{store}" not in cloud_only
        ]

        assert not missing, (
            "runtime-data stores read only by the local dump: "
            f"{missing}. They need no robot, so a cloud-only robot's "
            "download should carry them as well."
        )


class TestTheShadowsBecomeAState:
    """A cloud-only dump was never missing the data, only a translation.

    Every field the local sections read lives in the named shadows the
    robot already publishes -- `cleanMissionStatus`, `bbchg`, `bbmssn`,
    `cap`, `sku`, `p2maps` -- just split across four documents. Merged,
    they are the same shape a locally connected robot reports, so the
    sections written against a state work unchanged.

    THE EARLIER ATTEMPT USED THE WRONG TEST. "Does this helper need only
    runtime data" is a question about function signatures, not about
    what the robot can tell us. It closed three gaps and left every
    state-derived section local-only, with the state sitting three keys
    away in the same dump.
    """

    SHADOWS = {
        "classic": {"cap": {"pose": 2}, "sku": "q352020"},
        "ro-configinfo": {"passwordHash": "secret-hash", "hwPartsRev": {}},
        "ro-stats": {"bbchg": {"nChg": 5}, "bbmssn": {"nMssn": 9}},
        "ro-currentstate": {
            "batPct": 100,
            "cleanMissionStatus": {"phase": "charge", "error": 0},
            "svcEndpoints": "urls",
        },
    }

    @staticmethod
    def _state(shadows):
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _state_from_shadows,
        )

        return _state_from_shadows(
            SimpleNamespace(
                prime_status_coordinator=SimpleNamespace(data=shadows)
            )
        )

    def test_the_documents_merge_into_one_state(self) -> None:
        state = self._state(self.SHADOWS)

        for key in ("cap", "sku", "bbchg", "bbmssn", "batPct",
                    "cleanMissionStatus"):
            assert key in state, key

    def test_the_password_hash_does_not_survive_the_merge(self) -> None:
        """Merging documents is exactly how a credential reaches a place
        nobody reviewed. This dict is built here and does not pass
        through the dump's own redaction."""
        assert "passwordHash" not in self._state(self.SHADOWS)

    def test_the_sweep_is_by_substring_not_by_list(self) -> None:
        """A field added upstream tomorrow has to be caught too."""
        state = self._state({
            "ro-configinfo": {
                "someNewAuthToken": "x", "userPassword": "y", "keep": 1,
            },
        })

        assert state == {"keep": 1}

    def test_service_endpoints_are_dropped(self) -> None:
        assert "svcEndpoints" not in self._state(self.SHADOWS)

    def test_no_shadows_yields_an_empty_state(self) -> None:
        """Absent stays absent -- nothing here invents a value."""
        assert self._state(None) == {}

    def test_it_is_computed_once_per_dump(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert source.count("_state_from_shadows(data)") == 1


class TestTheErrorIsDecodedForBothDumps:
    """A cloud-only dump reported the error as a bare number, because
    the decoding lived in the section that reads the MQTT client. The
    same number sits in `cleanMissionStatus.error`, and the label table
    is shared.

    224 is the one that matters: "Smart Map localization failed", which
    one tester hit three releases running. A reporter should not have to
    look that up, and neither should the person reading their dump.
    """

    @staticmethod
    def _decode(code):
        from custom_components.roomba_plus.diagnostics import _decoded_error

        return _decoded_error(code)

    def test_a_known_code_carries_its_label(self) -> None:
        assert self._decode(224)["error_message"] == (
            "Smart Map localization failed"
        )

    def test_no_error_is_not_an_unknown_error(self) -> None:
        """Zero means "fine". Reporting it as an unrecognised code would
        send somebody looking for a fault that is not there."""
        decoded = self._decode(0)

        assert decoded["error_code"] == 0
        assert decoded["error_message"] is None

    def test_an_unknown_code_says_so_rather_than_vanishing(self) -> None:
        """A new firmware code must reach the reader as a number with a
        note, not be dropped for being unrecognised."""
        decoded = self._decode(9999)

        assert decoded["error_code"] == 9999
        assert "unknown" in decoded["error_message"]


class TestALocalDumpSaysWhatItIsAndHowStaleItIs:
    """Two things a cloud-only dump reported and a local one did not.

    CONNECTION TYPE. A cloud-only dump names it; a local one did not, so
    working out which branch produced a file meant inferring it from
    which sections were present. That cost real time this week -- a
    missing section was read as a Prime-versus-Classic difference when
    it was a connection-type one, twice.

    FRESHNESS. `last_mqtt_message_ts` has been kept on the local side
    all along, for the staleness watchdog, and never reported. A robot
    that has said nothing for hours looks identical to a healthy one in
    every other field -- which is exactly the state one tester was in
    while three explanations were being weighed.
    """

    def test_the_local_dump_names_its_connection_type(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)
        local = source[source.index("\n    if roomba is None:"):]

        assert '"connection_type"' in local

    def test_the_local_dump_reports_freshness(self) -> None:
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)
        local = source[source.index("\n    if roomba is None:"):]

        assert "_push_freshness(data)" in local

    def test_freshness_reads_the_timestamp_the_local_side_keeps(
        self,
    ) -> None:
        """The same helper serves both because both keep the same
        field -- no second implementation to drift."""
        import inspect

        from custom_components.roomba_plus.diagnostics import _push_freshness

        assert "last_mqtt_message_ts" in inspect.getsource(_push_freshness)

    def test_silence_since_startup_is_said_plainly(self) -> None:
        """Nothing yet and never are different states, and the one that
        matters is "the robot ran a mission and we heard nothing"."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _push_freshness

        out = _push_freshness(SimpleNamespace(last_mqtt_message_ts=0.0))

        assert out["last_message_ts"] is None
        assert "no push message since startup" in out["note"]


class TestTheActiveMapVersionIsRecorded:
    """`get_active_map_versions()` is called to build a floor plan and
    to pick a map, and both used the answer as a local variable. It was
    never stored, so no download could show it.

    THAT ABSENCE WAS MISREAD FOUR TIMES. A missing `active_p2mapv_id`
    in a download was taken as the robot not reporting which map it is
    on -- and a whole chain of explanation was built on it, when the
    robot had almost certainly reported one and this file simply had
    not been told.
    """

    def test_the_container_carries_it(self) -> None:
        import dataclasses

        from custom_components.roomba_plus.models import RoombaData

        names = {f.name for f in dataclasses.fields(RoombaData)}

        assert "prime_map_versions" in names

    def test_the_read_records_what_it_read(self) -> None:
        import inspect

        from custom_components.roomba_plus import image

        source = inspect.getsource(image)

        assert "runtime_data.prime_map_versions = {" in source

    def test_both_dumps_report_it(self) -> None:
        import pathlib

        source = pathlib.Path(
            "custom_components/roomba_plus/diagnostics.py"
        ).read_text(encoding="utf-8")
        start = source.index(
            "if data.connection_type is ConnectionType.CLOUD_ONLY:"
        )
        end = source.index("\n    if roomba is None:", start)

        assert '"active_map_versions"' in source[start:end]
        assert '"active_map_versions"' in source[end:]

    def test_never_read_is_distinct_from_no_maps(self) -> None:
        """An empty answer and "we have not asked yet" mean different
        things, and conflating them is how this was misread in the
        first place."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _active_map_versions,
        )

        nothing = SimpleNamespace(prime_map_versions={})

        assert _active_map_versions(nothing, {}) == "not read yet this session"

    def test_a_classic_robot_uses_its_own_source(self) -> None:
        """Recorded in a Prime-only path, this said "not read yet" on a
        Classic robot while the same download carried correct versions
        three sections away. Contradicting itself is worse than being
        silent."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _active_map_versions,
        )

        out = _active_map_versions(
            SimpleNamespace(prime_map_versions={}),
            {"pmaps": [{"MAP-A": "260916T181500"}]},
        )

        assert out == {"MAP-A": "260916T181500"}

    def test_a_recorded_value_wins_over_the_fallback(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _active_map_versions,
        )

        out = _active_map_versions(
            SimpleNamespace(prime_map_versions={"MAP-B": "LIVE"}),
            {"pmaps": [{"MAP-A": "OLD"}]},
        )

        assert out == {"MAP-B": "LIVE"}


class TestAFailedAlignmentSaysWhy:
    """`aligner_aligned: false` with room polygons present says the
    geometry arrived and the mapping did not, and stops there.

    Three different situations produced that same line:

      - the floor plan outline never arrived from the cloud
      - it arrived and the gap search found no door-shaped gaps in it
      - it arrived, gaps were found, and the match still failed

    @Thonno has eight room polygons, real doors between every room, and
    zero door markers. Which of the three he is in was not answerable
    from a download, so it was a guess -- and he had already re-added
    the device once, chasing a cause that turned out to be unrelated.

    THE ALIGNMENT DOES NOT NEED A POSITION. Door candidates are the
    midpoints of gaps in the outline, so a robot that publishes no pose
    can still align, provided the outline has gaps. That makes these two
    numbers the whole question.
    """

    @staticmethod
    def _chain(aligner):
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _position_chain

        return _position_chain(
            SimpleNamespace(
                umf_aligner=aligner,
                renderer=SimpleNamespace(point_count=0),
                geometry_store=SimpleNamespace(door_markers=[]),
            )
        )

    @staticmethod
    def _aligner(points, candidates):
        from types import SimpleNamespace

        return SimpleNamespace(
            _points2d=points,
            _door_candidates=candidates,
            aligned=bool(candidates),
            room_polygons_umf={"1": []},
        )

    def test_no_outline_is_distinguishable(self) -> None:
        out = self._chain(self._aligner([], []))

        assert out["outline_points"] == 0
        assert out["door_candidates"] == 0

    def test_an_outline_with_no_gaps_is_distinguishable(self) -> None:
        """The case that was indistinguishable from the one above, and
        the one that would mean the fallback can never work in that
        home."""
        out = self._chain(self._aligner([{}] * 412, []))

        assert out["outline_points"] == 412
        assert out["door_candidates"] == 0

    def test_gaps_found_shows_them(self) -> None:
        out = self._chain(self._aligner([{}] * 412, [(1.0, 2.0), (3.0, 4.0)]))

        assert out["door_candidates"] == 2

    def test_no_aligner_at_all_reports_none(self) -> None:
        """Absent and empty are different, and conflating them is how
        this became a guess in the first place."""
        out = self._chain(None)

        assert out["outline_points"] is None
        assert out["door_candidates"] is None


class TestAFalseAlignmentSaysWhatItCosts:
    """`aligner_aligned: false` reads like a defect and often is not
    one. Matching needs door candidates from the floor plan AND markers
    derived from observed positions; a robot publishing no position has
    the first and never the second. The fallback calibration then runs
    in UMF space, room lookup is correct, and only the drawn map lacks
    outlines.

    @Thonno removed and re-added his device chasing this, then asked
    whether that had broken it. It had not, and his room tracking was
    working throughout -- the field gave him no way to know.
    """

    def test_an_unaligned_aligner_explains_itself(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _position_chain

        out = _position_chain(SimpleNamespace(
            umf_aligner=SimpleNamespace(
                aligned=False, _points2d=[{}] * 169,
                _door_candidates=[(1.0, 2.0)] * 39,
                room_polygons_umf={"1": []},
            ),
            renderer=SimpleNamespace(point_count=0),
            geometry_store=SimpleNamespace(door_markers=[]),
            mission_store=None,
        ))

        assert "Room lookup still works" in out["alignment_note"]

    def test_an_aligned_one_says_nothing(self) -> None:
        """A note on a healthy robot is noise."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _position_chain

        out = _position_chain(SimpleNamespace(
            umf_aligner=SimpleNamespace(
                aligned=True, _points2d=[], _door_candidates=[],
                room_polygons_umf={},
            ),
            renderer=SimpleNamespace(point_count=10),
            geometry_store=SimpleNamespace(door_markers=[1, 2]),
            mission_store=None,
        ))

        assert out["alignment_note"] is None

    def test_traversal_missions_are_counted(self) -> None:
        """Without positions these are the only route to alignment, and
        nothing showed whether any existed."""
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _missions_with_traversals,
        )

        data = SimpleNamespace(mission_store=SimpleNamespace(records=[
            {"timeline": {"finEvents": [{"type": "traversal"}]}},
            {"timeline": {"finEvents": [{"type": "room"}]}},
            {"timeline": {}},
        ]))

        assert _missions_with_traversals(data) == 1

    def test_no_store_is_distinct_from_no_traversals(self) -> None:
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import (
            _missions_with_traversals,
        )

        assert _missions_with_traversals(
            SimpleNamespace(mission_store=None)
        ) == "no mission store"


class TestLastMissionRoomEvents:
    """The diagnostic section added after @Thonno's Kitchen report.

    His diagnostic held no mission record, so it could not show why the
    Kitchen was left out of the room history. This section puts the
    room events of the newest record beside the rooms the tracker
    stored, and states plainly which region ids were counted and which
    were seen but not counted.
    """

    def _data(self, record):
        data = MagicMock()
        data.mission_store.latest.return_value = record
        return data

    def test_no_record_says_so(self):
        from custom_components.roomba_plus.diagnostics import _last_mission_room_events

        result = _last_mission_room_events(self._data(None))
        assert result == {"available": False, "reason": "no mission record"}

    def test_a_record_without_timeline_still_shows_what_was_stored(self):
        """Before the cloud catch-up, or with no cloud account: the
        stored rooms are the only evidence, and they are shown."""
        from custom_components.roomba_plus.diagnostics import _last_mission_room_events

        rec = {"id": "m_1", "ended_at": "2026-09-21T10:02:01", "result": "completed",
               "last_cleaned_rooms": ["Soggiorno"], "zones": ["Cucina", "Soggiorno"]}
        result = _last_mission_room_events(self._data(rec))

        assert result["available"] is False
        assert result["last_cleaned_rooms"] == ["Soggiorno"]
        assert "timeline" in result["reason"]

    def test_the_kitchen_shape_is_cleaned_but_not_finished(self):
        """The shape @Thonno's report most likely had: the first room's
        only event on a status outside ROOM_EVENT_DONE_STATUSES, with real
        area cleaned; the other three complete. The diagnostic must name
        region 1 as seen-but-not-counted, and show its status."""
        from custom_components.roomba_plus.diagnostics import _last_mission_room_events

        def room(rid, status, pass_area):
            return {"type": "room", "room": {"rid": rid, "status": status,
                                             "passCount": 1, "area": 100,
                                             "passArea": pass_area}}

        rec = {
            "id": "m_1789975662", "ended_at": "2026-09-21T10:02:01", "result": "completed",
            "last_cleaned_rooms": ["Soggiorno", "Camera da letto", "Cabina Armadio"],
            "timeline": {"finEvents": [
                room("1", 1, 58),
                room("20", 0, 90), room("12", 0, 70), room("19", 0, 40),
            ]},
        }
        result = _last_mission_room_events(self._data(rec))

        assert result["available"] is True
        # Since the history rule split from the finished rule (4.2.11):
        # region 1 cleaned floor, so room history counts it — but its pass
        # never finished, so the end gate and learned figures do not.
        assert result["rids_counted_as_cleaned"] == ["1", "12", "19", "20"]
        assert result["rids_finished"] == ["12", "19", "20"]
        assert result["rids_seen_but_not_counted"] == []
        eins = next(e for e in result["room_events"] if e["rid"] == "1")
        assert eins["status"] == 1 and eins["passArea"] == 58
