"""Tests for the V4/Prime setup/unload path in __init__.py:
_connection_type(), _async_setup_entry_prime(), and the CLOUD_ONLY
branch in async_unload_entry().

NEW (V4/Prime implementation). No existing test file covers
async_setup_entry()/async_unload_entry() or the phase functions
directly anywhere in this suite (confirmed before writing this file) --
those are tested indirectly, through the platform tests that construct
RoombaData by hand. This file establishes its own pattern for the new
CLOUD_ONLY path specifically, since it's a genuinely separate code path
from the existing 4-phase pipeline.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import (
    _async_setup_entry_prime,
    _connection_type,
    async_unload_entry,
)
from custom_components.roomba_plus.const import (
    CONF_BLID,
    CONF_CONNECTION_TYPE,
    CONF_IROBOT_PASSWORD,
    CONF_IROBOT_USERNAME,
)
from custom_components.roomba_plus.models import ConnectionType, RoombaData
from roombapy_prime import (
    AuthConnectionError,
    AuthCredentialsError,
    AuthRateLimitedError,
)


def _make_hass_and_entry() -> tuple[MagicMock, MagicMock]:
    hass = MagicMock()
    hass.config.country = "US"
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    config_entry = MagicMock()
    config_entry.data = {
        CONF_CONNECTION_TYPE: ConnectionType.CLOUD_ONLY.value,
        CONF_BLID: "BLID123",
        CONF_IROBOT_USERNAME: "user@example.com",
        CONF_IROBOT_PASSWORD: "hunter2",
    }
    # Mirrors _make_coordinator()'s pattern in test_prime_coordinator.py --
    # avoids leaking an "coroutine was never awaited" warning for the
    # background task PrimeCoordinator.async_start() schedules.
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    return hass, config_entry


@pytest.fixture(autouse=True)
def _mock_clientsession():
    """async_get_clientsession(hass) with a plain MagicMock() hass falls
    through to creating a REAL aiohttp.ClientSession() (HA's real
    implementation checks hass.data, which a bare MagicMock doesn't
    behave like a dict for) -- leaks an unclosed-session warning/error
    in every test here, none of which make real network calls anyway
    (PrimeFactory.create_prime_robot is always mocked). Patched for
    every test in this file rather than per-test."""
    with patch(
        "custom_components.roomba_plus.async_get_clientsession",
        return_value=MagicMock(),
    ):
        yield


class TestConnectionType:
    def test_defaults_to_local_push_when_absent(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {}
        assert _connection_type(config_entry) is ConnectionType.LOCAL_PUSH

    def test_reads_cloud_only_from_data(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {CONF_CONNECTION_TYPE: "cloud_only"}
        assert _connection_type(config_entry) is ConnectionType.CLOUD_ONLY

    def test_reads_local_push_explicitly(self) -> None:
        config_entry = MagicMock()
        config_entry.data = {CONF_CONNECTION_TYPE: "local_push"}
        assert _connection_type(config_entry) is ConnectionType.LOCAL_PUSH


class TestAsyncSetupEntryPrime:
    """v4.0.0a0 MVP scope: login, MQTT connect, PrimeCoordinator running.
    Deliberately forwards NO platforms yet -- see the function's own
    docstring for why (vacuum.py would crash on a roomba=None entry)."""

    @pytest.mark.asyncio
    async def test_success_path_sets_runtime_data(self) -> None:
        from roombapy_prime.models import RobotSerialInfo

        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock()
        fake_prime_robot.get_named_shadow = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_state = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_household_id = AsyncMock(return_value="hh1")
        fake_serial_info = RobotSerialInfo(serial_number="SN1", sku="G185020")
        fake_prime_robot.get_serial_number_data = AsyncMock(return_value=fake_serial_info)

        async def _empty_named_shadows_updates():
            return
            yield  # pragma: no cover -- makes this an async generator

        fake_prime_robot.watch_named_shadows_updates = _empty_named_shadows_updates

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ) as mock_create:
            result = await _async_setup_entry_prime(hass, config_entry)

        assert result is True
        mock_create.assert_awaited_once()
        call = mock_create.call_args
        assert call.args[1] == "user@example.com"
        assert call.args[2] == "hunter2"
        assert call.args[3] == "US"
        assert call.kwargs["blid"] == "BLID123"
        assert call.kwargs["auto_refresh"] is True

        runtime_data: RoombaData = config_entry.runtime_data
        assert runtime_data.blid == "BLID123"
        assert runtime_data.roomba is None
        assert runtime_data.connection_type is ConnectionType.CLOUD_ONLY
        assert runtime_data.prime_robot is fake_prime_robot
        assert runtime_data.prime_coordinator is not None
        assert runtime_data.prime_status_coordinator is not None
        assert runtime_data.prime_household_id == "hh1"
        assert runtime_data.prime_serial_info is fake_serial_info
        fake_prime_robot.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_serial_info_failure_does_not_block_setup(self) -> None:
        """Same reasoning as test_household_id_failure_does_not_block_setup
        above -- a device page missing model/serial is a far better
        failure mode than blocking the entire V4/Prime setup over it."""
        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock()
        fake_prime_robot.get_named_shadow = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_state = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_household_id = AsyncMock(return_value="hh1")
        fake_prime_robot.get_serial_number_data = AsyncMock(side_effect=RuntimeError("simulated failure"))

        async def _empty_named_shadows_updates():
            return
            yield  # pragma: no cover -- makes this an async generator

        fake_prime_robot.watch_named_shadows_updates = _empty_named_shadows_updates

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ):
            result = await _async_setup_entry_prime(hass, config_entry)

        assert result is True
        runtime_data: RoombaData = config_entry.runtime_data
        assert runtime_data.prime_serial_info is None
        assert runtime_data.prime_robot is fake_prime_robot  # rest of setup unaffected

    @pytest.mark.asyncio
    async def test_household_id_failure_does_not_block_setup(self) -> None:
        """CONFIRMED DELIBERATE (this session): get_household_id()'s own
        response-shape handling isn't confirmed against every real
        account shape yet -- a failure here must degrade to "no
        schedule data", not fail the entire V4/Prime setup (battery/
        vacuum/etc., all already working, over one optional feature)."""
        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock()
        fake_prime_robot.get_named_shadow = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_state = AsyncMock(
            return_value=MagicMock(payload={"state": {"reported": {}}})
        )
        fake_prime_robot.get_household_id = AsyncMock(side_effect=RuntimeError("simulated failure"))

        async def _empty_named_shadows_updates():
            return
            yield  # pragma: no cover -- makes this an async generator

        fake_prime_robot.watch_named_shadows_updates = _empty_named_shadows_updates

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ):
            result = await _async_setup_entry_prime(hass, config_entry)

        assert result is True
        runtime_data: RoombaData = config_entry.runtime_data
        assert runtime_data.prime_household_id is None
        assert runtime_data.prime_robot is fake_prime_robot  # rest of setup unaffected

    @pytest.mark.asyncio
    async def test_credentials_error_raises_config_entry_auth_failed(self) -> None:
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthCredentialsError("wrong password")),
        ):
            with pytest.raises(ConfigEntryAuthFailed, match="BLID123"):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_rate_limited_error_raises_config_entry_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthRateLimitedError("close the app")),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_connection_error_raises_config_entry_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        hass, config_entry = _make_hass_and_entry()

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(side_effect=AuthConnectionError("dns failure")),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)

    @pytest.mark.asyncio
    async def test_mqtt_connect_failure_raises_config_entry_not_ready(self) -> None:
        """Login succeeds, but PrimeCoordinator.async_start()'s own MQTT
        connect() fails -- must still map to ConfigEntryNotReady, via
        PrimeCoordinator's own translation, not this function's."""
        from homeassistant.exceptions import ConfigEntryNotReady
        from roombapy_prime import ShadowConnectionError

        hass, config_entry = _make_hass_and_entry()
        fake_prime_robot = MagicMock()
        fake_prime_robot.connect = AsyncMock(side_effect=ShadowConnectionError("mqtt unreachable"))

        with patch(
            "custom_components.roomba_plus.PrimeFactory.create_prime_robot",
            new=AsyncMock(return_value=fake_prime_robot),
        ):
            with pytest.raises(ConfigEntryNotReady):
                await _async_setup_entry_prime(hass, config_entry)


class TestAsyncUnloadEntryCloudOnly:
    @pytest.mark.asyncio
    async def test_disconnects_prime_robot_and_forwards_no_platforms(self) -> None:
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        config_entry = MagicMock()
        fake_prime_robot = MagicMock()
        fake_prime_robot.disconnect = AsyncMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123",
            roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY,
            prime_robot=fake_prime_robot,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is True
        fake_prime_robot.disconnect.assert_awaited_once()
        hass.config_entries.async_unload_platforms.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_missing_prime_robot_gracefully(self) -> None:
        """Defensive: if setup failed before prime_robot was ever set
        (shouldn't happen given _async_setup_entry_prime()'s ordering,
        but this guards against a future refactor introducing that
        gap), unload must not crash."""
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        config_entry = MagicMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123", roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY, prime_robot=None,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_does_not_disconnect_when_platform_unload_fails(self) -> None:
        """Mirrors the classic path's own convention (see the LOCAL_PUSH
        branch just below this one): only disconnect/cleanup if platform
        unloading actually succeeded."""
        hass = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        config_entry = MagicMock()
        fake_prime_robot = MagicMock()
        fake_prime_robot.disconnect = AsyncMock()
        config_entry.runtime_data = RoombaData(
            blid="BLID123", roomba=None,
            connection_type=ConnectionType.CLOUD_ONLY, prime_robot=fake_prime_robot,
        )

        result = await async_unload_entry(hass, config_entry)

        assert result is False
        fake_prime_robot.disconnect.assert_not_called()



def _identifier_counts(base, *extra_dirs):
    """How often every identifier appears across the given source.

    ONE PASS, NOT ONE PER NAME. Both orphan guards below used to run a
    separate `re.findall` for each candidate over the whole 6 MB of
    source -- 574 constants and 400-odd private functions, each scanning
    everything. Together they took 178 of the suite's 245 seconds, 73%
    of the total, for two assertions.

    Counting every identifier once and looking names up in the result
    gives identical answers in 0.4 seconds. Verified against the
    per-name search rather than assumed: the counts match exactly.

    The bug was copied rather than invented -- the constants guard was
    written by following the function guard's shape without asking what
    it cost. Reviewing a pattern before reusing it is the cheaper habit.
    """
    import collections
    import re

    text = "".join(f.read_text() for f in base.glob("*.py"))
    for extra in extra_dirs:
        text += "".join(f.read_text() for f in extra.glob("*.py"))
    return collections.Counter(re.findall(r"\b\w+\b", text))


class TestNothingWasFetchingTheTimeEstimates:
    """`_async_fetch_prime_time_estimates` fills
    `runtime_data.prime_time_estimates`. The calendar reads it to give
    schedule occurrences a real duration instead of a flat hour, and
    `mission_progress` needs it for a denominator.

    **Nothing called it.** Both readers got `None` for the whole life of
    the feature.

    @utkjmitch traced `mission_progress` reading `unknown` on a robot
    with 50 imported missions and found the profile-store half of it
    (`hass_ref`, read twice and written never). This is the other half.

    Found by listing private functions whose name appears exactly once
    in the source — the same shape as `hass_ref` and as
    `_readable_part_name`, which was written and never called.
    """

    def test_setup_calls_it(self):
        import inspect

        from custom_components.roomba_plus import _async_setup_entry_prime

        source = inspect.getsource(_async_setup_entry_prime)

        assert "await _async_fetch_prime_time_estimates(config_entry)" in source

    def test_no_private_helper_is_defined_and_never_named_again(self):
        """The check that found it, kept as a guard.

        A private function whose name appears once in the whole
        component is one nothing can reach — and three of those turned
        up in a single day, each costing a working feature."""
        import ast
        import pathlib

        # THE TESTS COUNT AS A CALLER.
        #
        # `_get_two_pass` is read by nothing in production -- `select.py`
        # reads the same preference itself -- but it has its own tests,
        # and deleting a tested helper to satisfy this check would trade
        # a harmless duplicate for a lost intent. What this guard is for
        # is a helper nothing anywhere reaches.
        base = pathlib.Path("custom_components/roomba_plus")
        counts = _identifier_counts(base, pathlib.Path("tests"))
        orphans = []
        for path in base.glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = node.name
                if not name.startswith("_") or name.startswith("__"):
                    continue
                if counts[name] <= 1:
                    orphans.append(f"{path.name}:{name}")

        # FOUR KNOWN, EACH A FEATURE NOTHING REACHES. Listed rather
        # than fixed in one pass: they are separate features and each
        # needs its own reasoning about where the call belongs.
        #
        #   image.py:_dock_position       the a26 "seen beats
        #       remembered" dock correction. @utkjmitch's map draws the
        #       dock where a treadmill now stands; he reported it as
        #       unreachable in the frozen-shadow state, and it is
        #       unreachable in every state.
        #   vacuum.py:_get_two_pass       reads twoPass from live state
        #   sensor_cloud.py:_classify_dirt_cause
        #   room_seg_store.py:_boundary_stability
        #
        # **Nothing may be added to this list.** It shrinks or the guard
        # has failed at its job.
        known = {
            "image.py:_dock_position",
            "vacuum.py:_get_two_pass",
            "sensor_cloud.py:_classify_dirt_cause",
            "room_seg_store.py:_boundary_stability",
        }
        new = sorted(set(orphans) - known)

        assert not new, (
            "defined and never named again anywhere -- nothing can reach "
            f"these: {new}"
        )
        assert set(orphans) <= known, "the known list is stale"

    def test_no_module_constant_is_defined_and_never_named_again(self):
        """The same shape one level over, and the guard above missed it.

        `CYCLE_LABELS` sat in const.py read by nothing, for as long as it
        had existed. The check above only walks private FUNCTIONS, so a
        public module CONSTANT went straight through it — the same
        failure the guard exists to prevent, in the one place the guard
        did not look.

        Constants are a weaker signal than functions: an unused label
        table costs a feature nobody built, not a feature that silently
        does nothing. The list below is therefore allowed to hold
        entries with a stated reason, unlike the function list.
        """
        import ast
        import pathlib

        base = pathlib.Path("custom_components/roomba_plus")
        counts = _identifier_counts(base, pathlib.Path("tests"))

        orphans = []
        for path in base.glob("*.py"):
            for node in ast.parse(path.read_text()).body:
                names: list[str] = []
                if isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names = [node.target.id]
                for name in names:
                    if not name.isupper() or len(name) < 4:
                        continue
                    if counts[name] <= 1:
                        orphans.append(f"{path.name}:{name}")

        # KNOWN, EACH WITH ITS REASON.
        #
        #   CONF_CERT / DEFAULT_CERT        config keys kept for
        #       migration compatibility; removing them would break
        #       reading an old entry.
        #   ROOMBA_CLEAN_WIDTH_MM           a physical constant, used by
        #       coverage maths that is currently disabled.
        #   MAP_UPDATING_NOT_READY_BIT      documents a notReady bit.
        #   BIN_LABELS / YES_NO_LABELS      display tables, same shape as
        #       CYCLE_LABELS: available for a caller, read by none yet.
        #   ZONE_TYPE_ICONS                 its one non-default entry was
        #       "furniture", which is not a zone_type any confirmed
        #       source produces -- unreachable AND wrong.
        #   ATTR_STATUS / ATTR_PAD_WETNESS  attribute names.
        #   SUPPORT_ROOMBA_CARPET_BOOST / SUPPORT_BRAAVA   legacy
        #       feature-flag combinations from before VacuumEntityFeature.
        #   _AUTO_CONFIRM_CONFIDENCE        threshold for a path not
        #       currently taken.
        #
        # CYCLE_LABELS IS DELIBERATELY NOT LISTED. It is the one this
        # guard was written for, and it stays visible until something
        # reads it.
        known = {
            "const.py:CONF_CERT",
            "const.py:DEFAULT_CERT",
            "const.py:ROOMBA_CLEAN_WIDTH_MM",
            "const.py:MAP_UPDATING_NOT_READY_BIT",
            "const.py:BIN_LABELS",
            "const.py:YES_NO_LABELS",
            "const.py:ATTR_STATUS",
            "const.py:ATTR_PAD_WETNESS",
            "const.py:ZONE_TYPE_ICONS",
            "const.py:CYCLE_LABELS",
            "umf_aligner.py:_AUTO_CONFIRM_CONFIDENCE",
            "vacuum.py:SUPPORT_ROOMBA_CARPET_BOOST",
            "vacuum.py:SUPPORT_BRAAVA",
        }
        new = sorted(set(orphans) - known)

        assert not new, f"module constants nothing reaches: {new}"


class TestPartIdsFromAppThreeZero:
    """App 3.0.0 replaced numeric `part_id` values with speaking names.
    A server sending those would fall straight through the numeric
    lookup and the maintenance list would read "Replace main_brush" —
    the exact bug `_readable_part_name`'s docstring says it exists to
    avoid.
    """

    def test_both_vocabularies_resolve_to_the_same_translation(self):
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        pairs = [
            ("67", "side_brush"),
            ("71", "main_brush"),
            ("72", "filter"),
            ("147", "bag"),
            ("148", "pad"),
            ("213", "sensor"),
        ]
        for numeric, named in pairs:
            assert _KNOWN_PARTS[numeric] == _KNOWN_PARTS[named], named

    def test_the_two_washing_systems_are_not_pinned_to_202_or_212(self):
        """3.0.0 has two washing-system parts and this integration has
        two unnamed pad-wash counters. Suggestive, not evidence — which
        maps to which is unknown, and pairing them would print a coin
        flip as a fact."""
        from custom_components.roomba_plus.sensor_prime import _KNOWN_PARTS

        assert _KNOWN_PARTS["dock_washing_system"] != _KNOWN_PARTS["202"]
        assert _KNOWN_PARTS["dock_washing_system"] != _KNOWN_PARTS["212"]
        assert _KNOWN_PARTS["mop_washing_system"] != _KNOWN_PARTS["202"]
        assert _KNOWN_PARTS["mop_washing_system"] != _KNOWN_PARTS["212"]

    def test_the_untranslated_keys_still_read_as_words(self):
        """Three new keys have no locale entry on purpose: the fallback
        strips the prefix and title-cases, which already gives iRobot's
        own wording."""
        for key, expected in [
            ("prime_part_dock_washing_system", "Dock Washing System"),
            ("prime_part_mop_washing_system", "Mop Washing System"),
            ("prime_part_battery", "Battery"),
        ]:
            assert key.replace("prime_part_", "").replace("_", " ").title() == expected


class TestPhaseAndCycleLabelsCoverTheVendorsEnums:
    """`_phase_value()` falls back to the raw wire string, so a missing
    label showed "padWash" in the sensor instead of words — no crash, no
    failing test, and it looked deliberate.
    """

    def test_every_mission_phase_has_a_label(self):
        from custom_components.roomba_plus.const import PHASE_LABELS

        # MissionPhase, app 3.0.0. "unknown" serialises as "toPhase" and
        # is not a state a robot reports.
        vendor = {
            "charge", "chargingerror", "chgerr", "evac", "hmMidMsn",
            "hmPostMsn", "hmUsrChrg", "hmUsrDock", "mapupd", "padDry",
            "padWash", "refill", "run", "stop", "stuck",
        }

        assert not vendor - set(PHASE_LABELS)

    def test_every_mission_cycle_has_a_label(self):
        from custom_components.roomba_plus.const import CYCLE_LABELS

        vendor = {
            "clean", "dock", "dockupg", "evac", "manual", "monitor",
            "none", "quick", "spot", "tidy", "train",
        }

        assert not vendor - set(CYCLE_LABELS)

    def test_the_dock_phases_are_the_ones_that_mattered(self):
        """A combo robot spends real time washing and drying its pad at
        the end of every mop mission. Those three were the missing
        labels a user would actually have seen."""
        from custom_components.roomba_plus.const import PHASE_LABELS

        assert PHASE_LABELS["padWash"] == "Washing pad"
        assert PHASE_LABELS["padDry"] == "Drying pad"
        assert PHASE_LABELS["refill"] == "Refilling tank"


class TestDroppedFavouritesAreReported:
    """Seven favourites arriving and none becoming buttons looked
    exactly like an account with no favourites. That silence is why the
    bug survived several plausible fixes — none could be confirmed or
    ruled out.
    """

    def test_the_filter_still_drops_what_it_should(self):
        from custom_components.roomba_plus import button_prime

        assert "is_deleted" in inspect_source(button_prime)
        assert "is_hidden" in inspect_source(button_prime)

    def test_the_drop_is_logged_with_both_counts(self):
        """"7 of 7 not offered" is a next step; "no buttons" is a
        shrug."""
        from custom_components.roomba_plus import button_prime

        source = inspect_source(button_prime)

        assert "were not offered as buttons" in source
        assert "dropped," in source and "len(favorites or [])," in source


def inspect_source(module):
    import inspect

    return inspect.getsource(module)


class TestStopPadDrySurvivesTheAppsBlanketLock:
    """App 3.0.0's Dock Controls sheet greys out all three controls once
    any dock task starts — "Dock task in progress. Try again later". So
    a drying cycle cannot be stopped from the app once it has begun.

    `stoppaddry` sent from here during a running cycle works, confirmed
    on the same account and dock. The app's block is client-side, not a
    refusal from the robot.

    This test exists because the obvious "fix" would be to match the
    app, and matching it would remove a working capability.
    """

    def test_stop_is_available_exactly_while_drying_runs(self):
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        by_key = {c.key: c for c in PRIME_DOCK_COMMANDS}
        stop = by_key["prime_stop_pad_dry"]

        assert stop.ready_states == (702,)
        assert stop.state_attr == "pd_state"

    def test_start_and_stop_gate_on_opposite_states(self):
        """The one control whose rule inverts its counterpart's: you can
        start when it is not running and stop when it is."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        by_key = {c.key: c for c in PRIME_DOCK_COMMANDS}
        start = by_key["prime_start_pad_dry"]
        stop = by_key["prime_stop_pad_dry"]

        assert not set(start.ready_states) & set(stop.ready_states)

    def test_no_blanket_dock_task_lock_exists(self):
        """Nothing here disables a dock button because another dock task
        is running. If that ever appears, it should be because a robot
        refused the command — not because the app does."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        for command in PRIME_DOCK_COMMANDS:
            assert command.state_attr in ("state", "pw_state", "pd_state")
            assert command.ready_states


class TestStopPadDryStaysAvailableWhileDrying:
    """The one control where this integration deliberately does MORE
    than the iRobot app, at a tester's explicit request.

    App 3.0.0 greys out every Dock Control once a dock task starts —
    tapping any of them answers "Dock task in progress. Try again
    later". So a drying cycle cannot be stopped from the app once begun.
    @chairstacker calls that the big drawback of the new UI, and being
    able to stop it from Home Assistant the reason to keep ours as it
    is.

    The block is client-side: `stoppaddry` sent during a running dry
    cycle works, confirmed on the same account and the same dock.

    This test exists because "align with the app" is a plausible-sounding
    future change that would silently remove a working capability.
    """

    @staticmethod
    def _command(key):
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        return next(c for c in PRIME_DOCK_COMMANDS if c.key == key)

    def test_it_is_ready_exactly_while_drying_runs(self):
        """702 is the state that means the dry cycle is running — the
        one moment the app refuses to act."""
        stop = self._command("prime_stop_pad_dry")

        assert stop.ready_states == (702,)
        assert stop.state_attr == "pd_state"

    def test_it_is_the_inverse_of_the_start_button(self):
        """Start is offered at 701/703 (idle, finished) and stop at 702.
        No state offers both, and no state offers neither."""
        start = self._command("prime_start_pad_dry")
        stop = self._command("prime_stop_pad_dry")

        assert not set(start.ready_states) & set(stop.ready_states)
        assert 702 not in start.ready_states

    def test_no_blanket_dock_task_lock_exists(self):
        """The app's lock is cross-control: any running dock task
        disables all three. Ours is per-control and per-state, which is
        what makes stopping possible."""
        from custom_components.roomba_plus.button_prime import PRIME_DOCK_COMMANDS

        state_attrs = {c.state_attr for c in PRIME_DOCK_COMMANDS}

        # Each control reads its OWN status field. A blanket lock would
        # need one shared "a task is running" gate; there is none.
        assert state_attrs == {"state", "pw_state", "pd_state"}

    def test_the_divergence_is_written_down_as_deliberate(self):
        import inspect

        from custom_components.roomba_plus import button_prime

        source = inspect.getsource(button_prime)

        assert "DO NOT IMPLEMENT THE APP'S LOCK HERE" in source


class TestClassicShapedReadsOnPrime:
    """`roomba_reported_state()` returns `{}` on a CLOUD_ONLY entry BY
    DESIGN. Every caller reading the Classic shape therefore gets an
    empty dict for the life of the entry, and any gate built on it is
    decided before the robot says anything.

    @utkjmitch found the first instance — `mission_progress` stayed
    `unknown` through a whole mission while its own attributes rendered
    the elapsed time and the estimate. Three more had the same shape,
    and two of them failed OPEN.
    """

    @staticmethod
    def _prime_entry(**shadows):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        data = MagicMock()
        data.roomba_reported_state.return_value = {}
        data.prime_status_coordinator = SimpleNamespace(data=shadows)
        return data

    def test_the_cycle_gate_no_longer_passes_while_cleaning(self):
        """`dirt_threshold`'s "robot must be docked" gate compared
        `cycle` against `"none"` and always matched on Prime — a gate
        that opens for a cleaning robot is worse than none."""
        from custom_components.roomba_plus.prime_coordinator import (
            prime_mission_cycle,
        )

        data = self._prime_entry(
            **{"ro-currentstate": {"cleanMissionStatus": {"cycle": "clean"}}}
        )

        assert prime_mission_cycle(data) == "clean"

    def test_classic_still_wins_and_the_fallback_is_untouched(self):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            prime_mission_cycle,
        )

        data = MagicMock()
        data.roomba_reported_state.return_value = {
            "cleanMissionStatus": {"cycle": "quick"}
        }
        data.prime_status_coordinator = None

        assert prime_mission_cycle(data) == "quick"

    def test_the_last_command_comes_from_rw_software_on_prime(self):
        """Prime carries it on a different shadow — confirmed from a
        real dump: `{"command": "stoppaddry", "initiator": "rmtApp"}`."""
        from custom_components.roomba_plus.prime_coordinator import (
            prime_last_command,
        )

        data = self._prime_entry(
            **{
                "rw-software": {
                    "lastCommand": {"command": "stoppaddry", "initiator": "rmtApp"}
                }
            }
        )

        assert prime_last_command(data)["command"] == "stoppaddry"

    def test_the_pass_settings_come_from_rw_settings_not_mission_status(self):
        """The first draft read them from `cleanMissionStatus`, mirroring
        the Classic shape. The library's `CleanMissionStatus` has no such
        fields — it would have returned two Nones and looked like "the
        robot reports nothing"."""
        from custom_components.roomba_plus.sensor_rooms import _prime_pass_settings

        data = self._prime_entry(
            **{"rw-settings": {"noAutoPasses": False, "twoPass": True}}
        )

        assert _prime_pass_settings(data) == {"noAutoPasses": False, "twoPass": True}

    def test_a_missing_shadow_yields_nothing_rather_than_a_default(self):
        """An absent setting must not read as `False` — the caller
        distinguishes "not reported" from "reported off"."""
        from custom_components.roomba_plus.sensor_rooms import _prime_pass_settings

        assert _prime_pass_settings(self._prime_entry()) == {}

    def test_every_remaining_classic_read_is_accounted_for(self):
        """A census rather than a spot check. `roomba_reported_state()`
        has 23 call sites; this asserts nobody adds a 24th without
        deciding whether Prime reaches it."""
        import pathlib
        import re

        base = pathlib.Path("custom_components/roomba_plus")
        count = sum(
            len(re.findall(r"roomba_reported_state\(\)", p.read_text()))
            for p in base.glob("*.py")
        )

        assert count == 28, (
            f"{count} call sites, expected 28 — a new one needs a decision "
            "about whether a Prime entry reaches it, and a Prime fallback "
            "if it does. Four of the 28 are inside the fallback helpers "
            "themselves, which read Classic first on purpose."
        )


class TestThePrimeDiagnosticsBranchLosesNothingItNeeds:
    """The Prime path returns its own dict and never reaches the Classic
    code below. That early return exists for a real crash — this
    function used to touch `data.roomba`'s attributes unconditionally,
    which is None on every CLOUD_ONLY entry.

    Fixing the crash quietly decided that everything below was Classic,
    and that was never checked block by block. Four blocks were not:

      favourites            built for the favourites bug, unreachable
                            from the tier reporting it
      vendor_capabilities   its own docstring says "Empty for a Classic
                            robot" — written for Prime, placed where
                            Prime cannot see it
      never_succeeded       takes no arguments; a global record of what
                            has never once worked
      warnings              asks whether HA's core roomba integration
                            is also loaded, which is generation-blind
    """

    @staticmethod
    def _prime_keys():
        import inspect
        import re

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)
        start = source.find("if data.connection_type is ConnectionType.CLOUD_ONLY:")
        end = source.find("\n    # ", start + 100)
        return set(re.findall(r'^\s{12}"([a-z_]+)":', source[start:end], re.M))

    def test_the_four_recovered_blocks_are_present(self):
        keys = self._prime_keys()

        for block in (
            "favourites",
            "vendor_capabilities",
            "never_succeeded",
            "warnings",
        ):
            assert block in keys, f"{block} is missing from the Prime branch"

    def test_the_favourites_block_carries_the_raw_response(self):
        """The count alone cannot separate "option off" from "list
        arrived empty" from "one entry failed to parse"."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        assert "_prime_favorites_raw" in inspect.getsource(diagnostics)

    def test_the_raw_fetch_never_breaks_the_download(self):
        """A diagnostics download that fails because one section failed
        is worse than a section that reports why."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.diagnostics import _prime_favorites_raw

        robot = SimpleNamespace(
            get_favorites_raw=AsyncMock(side_effect=RuntimeError("boom"))
        )
        result = asyncio.run(_prime_favorites_raw(SimpleNamespace(prime_robot=robot)))

        assert "RuntimeError" in result["error"]

    def test_no_prime_entry_reaches_the_classic_block(self):
        """The guard this whole class rests on. If the early return ever
        goes away, these tests stop meaning anything — and the crash it
        prevents comes back."""
        import inspect

        from custom_components.roomba_plus import diagnostics

        source = inspect.getsource(diagnostics)

        assert "if data.connection_type is ConnectionType.CLOUD_ONLY:" in source
