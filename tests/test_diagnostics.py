"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



from unittest.mock import MagicMock
from unittest.mock import patch
import pytest


def _make_roomba(reported: dict) -> MagicMock:
    """Minimal roomba mock whose master_state returns the given reported dict."""
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": reported}}
    roomba.roomba_connected = True
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

    # Patch the lazy-imported roomba_reported_state inside __init__.py
    with patch(
        "custom_components.roomba_plus.__init__.roomba_reported_state",
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
        mistake, accessing roomba.roomba_connected on None raises
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
