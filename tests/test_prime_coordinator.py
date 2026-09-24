"""Tests for custom_components.roomba_plus.prime_coordinator.

NEW (V4/Prime implementation). Constructs a REAL PrimeCoordinator via its
actual __init__ (not the object.__new__() bypass used elsewhere in this
test suite for IrobotCloudCoordinator) -- DataUpdateCoordinator.__init__
itself does no real hass/event-loop interaction beyond storing
references and building a Debouncer, so a MagicMock() hass is safe here,
and this way async_set_updated_data()/async_set_update_error() (which
touch several DataUpdateCoordinator internals) work against a properly
initialized instance instead of a partially-stubbed one.
"""
from __future__ import annotations

import contextlib

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus.prime_coordinator import (
    PrimeCoordinator,
    PrimeStatusCoordinator,
    _deep_merge_reported,
)
from roombapy_prime import ShadowConnectionError, ShadowError, ShadowSSLError
from roombapy_prime.mqtt_client import ShadowResponse
from types import SimpleNamespace
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.roomba_plus import prime_coordinator as pc
from custom_components.roomba_plus.prime_coordinator import add_prime_entities_when_available


def _make_coordinator() -> tuple[PrimeCoordinator, MagicMock, MagicMock]:
    hass = MagicMock()
    config_entry = MagicMock()
    # async_create_background_task() receives the coroutine as an argument
    # but (being a MagicMock) never awaits/schedules it -- close it
    # explicitly here so tests that don't care about its actual execution
    # (test_connects_and_starts_background_task below) don't leak an
    # "coroutine was never awaited" warning.
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    prime_robot = MagicMock()
    prime_robot.connect = AsyncMock()
    coordinator = PrimeCoordinator(
        hass, config_entry, blid="BLID123", prime_robot=prime_robot,
    )
    return coordinator, config_entry, prime_robot


class TestAsyncStart:
    """async_start(): connects, then starts the background
    watch_mission_timeline() consumer. IMPORTANT (see the method's own
    docstring): login already happened earlier via
    PrimeFactory.create_prime_robot() -- connect() here only opens the
    MQTT connection with an already-valid token, so only connection-level
    failures (Shadow*Error) are possible at this step, never a
    credentials problem."""

    @pytest.mark.asyncio
    async def test_connects_and_starts_background_task(self) -> None:
        coordinator, config_entry, prime_robot = _make_coordinator()

        await coordinator.async_start()

        prime_robot.connect.assert_awaited_once()
        # TWO WATCHERS NOW: the mission timeline and the rejected-command
        # stream. The second exists because a command call returning
        # without an exception, on a robot that then does nothing, has
        # been diagnosed by elimination every time it happened.
        assert config_entry.async_create_background_task.call_count == 2
        names = [
            c.kwargs.get("name", "")
            for c in config_entry.async_create_background_task.call_args_list
        ]
        assert any("rejected" in n for n in names)
        call = config_entry.async_create_background_task.call_args
        assert call.args[0] is coordinator.hass
        assert "BLID123" in call.kwargs.get("name", "")

    @pytest.mark.asyncio
    async def test_translates_ssl_error_to_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowSSLError("cert problem"))

        with pytest.raises(ConfigEntryNotReady, match="BLID123"):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_translates_connection_error_to_not_ready(self) -> None:
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowConnectionError("dns failure"))

        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_translates_generic_shadow_error_to_not_ready(self) -> None:
        """Plain ShadowError (e.g. "connect timed out") must also map to
        ConfigEntryNotReady, not propagate as a raw library exception --
        it's the base class, not just its two typed subclasses."""
        from homeassistant.exceptions import ConfigEntryNotReady

        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=ShadowError("connect timed out after 10.0s"))

        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_start()

    @pytest.mark.asyncio
    async def test_unrelated_exception_is_not_swallowed(self) -> None:
        """A genuinely unexpected error type (not part of the Shadow*Error
        family) must propagate as-is -- this method only translates the
        specific, known connection-failure categories."""
        coordinator, _config_entry, prime_robot = _make_coordinator()
        prime_robot.connect = AsyncMock(side_effect=RuntimeError("something else entirely"))

        with pytest.raises(RuntimeError, match="something else entirely"):
            await coordinator.async_start()


class TestAsyncWatchMissionTimeline:
    """_async_watch_mission_timeline(): consumes watch_mission_timeline(),
    forwards every delta (parsed into a MissionTimelineReport) via
    async_set_updated_data(). watch_mission_timeline() itself
    (roombapy-prime v0.1.11a3+) reconnects transparently across drops
    with its own backoff -- this loop does not need its own retry logic
    on top.

    UPDATE (this session): switched from watch_state() after a live
    capture (chairstacker) proved the shadow delta channel never
    carries mission status at all -- see the module docstring."""

    @pytest.mark.asyncio
    async def test_forwards_deltas_via_async_set_updated_data(self) -> None:
        coordinator, _config_entry, prime_robot = _make_coordinator()

        async def fake_watch_mission_timeline():
            yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "start", "ts": 1}]})
            yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "room", "ts": 2, "room": {"rid": "11"}}]})
            # This generator is meant to run forever in real operation --
            # ending on its own is itself anomalous and now triggers the
            # coordinator's own outer retry loop. Terminate the test
            # cleanly via cancellation rather than letting it retry forever.
            raise asyncio.CancelledError

        prime_robot.watch_mission_timeline = MagicMock(return_value=fake_watch_mission_timeline())

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_watch_mission_timeline()

        from roombapy_prime.models import MissionTimelineReport

        assert isinstance(coordinator.data, MissionTimelineReport)
        assert coordinator.data.mission_id == "m1"
        assert coordinator.data.event[0].event_type == "room"
        assert coordinator.last_update_success is True

    @pytest.mark.asyncio
    async def test_unexpected_exception_calls_set_update_error(self) -> None:
        """watch_mission_timeline() is designed to never raise in normal
        operation (it retries forever internally) -- if it somehow still
        does, that must surface via async_set_update_error(), not
        disappear as silently-stale data."""
        coordinator, _config_entry, prime_robot = _make_coordinator()

        async def fake_watch_mission_timeline():
            if False:
                yield  # pragma: no cover -- makes this an async generator
            raise RuntimeError("watch_mission_timeline died unexpectedly")

        prime_robot.watch_mission_timeline = MagicMock(return_value=fake_watch_mission_timeline())

        with patch("asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_mission_timeline()

        assert coordinator.last_update_success is False
        assert isinstance(coordinator.last_exception, RuntimeError)

    @pytest.mark.asyncio
    async def test_retries_after_unexpected_exception_instead_of_dying_permanently(self) -> None:
        """CONFIRMED NECESSARY (real field report, chairstacker): the
        vacuum entity's own activity got stuck on "Cleaning" long after
        the robot had actually finished and docked -- this coordinator
        previously had no recovery path if anything unexpected
        interrupted its own watch task, exactly matching that report.
        Now retries instead of dying permanently for the rest of the
        session."""
        coordinator, _config_entry, prime_robot = _make_coordinator()
        call_count = 0

        def fake_watch_mission_timeline():
            nonlocal call_count
            call_count += 1

            async def _gen():
                if call_count == 1:
                    raise RuntimeError("simulated unexpected failure")
                    yield  # pragma: no cover -- unreachable, makes this an async generator
                yield ShadowResponse(topic="t", payload={"mission_id": "m1", "event": [{"type": "start", "ts": 1}]})
                raise asyncio.CancelledError

            return _gen()

        prime_robot.watch_mission_timeline = fake_watch_mission_timeline

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_mission_timeline()

        assert call_count == 2
        assert coordinator.data.mission_id == "m1"


def _make_status_coordinator() -> tuple[PrimeStatusCoordinator, MagicMock, MagicMock]:
    hass = MagicMock()
    config_entry = MagicMock()
    config_entry.async_create_background_task.side_effect = (
        lambda hass, coro, name, **kw: coro.close()
    )
    prime_robot = MagicMock()
    prime_robot.connect = AsyncMock()
    coordinator = PrimeStatusCoordinator(
        hass, config_entry, blid="BLID123", prime_robot=prime_robot,
    )
    return coordinator, config_entry, prime_robot


class TestAsyncStartSeedsClassicShadow:
    """NEW (this session) -- async_start() now ALSO seeds the classic/
    unnamed shadow (get_state(), not get_named_shadow()) once, under
    CLASSIC_SHADOW_KEY, for CapabilityFlags/DockCapabilities gating.
    Deliberately seed-only (see CLASSIC_SHADOW_KEY's own docstring) --
    no corresponding push-loop test needed, since it isn't added there."""

    @pytest.mark.asyncio
    async def test_seeds_classic_shadow_alongside_the_eight_named_ones(self) -> None:
        coordinator, _config_entry, prime_robot = _make_status_coordinator()

        async def fake_get_named_shadow(name):
            return ShadowResponse(topic="x", payload={"state": {"reported": {"_name": name}}})

        prime_robot.get_named_shadow = fake_get_named_shadow
        prime_robot.get_state = AsyncMock(
            return_value=ShadowResponse(topic="x", payload={"state": {"reported": {"cap": {"scrub": 3}}}})
        )

        await coordinator.async_start()

        assert coordinator.data[PrimeStatusCoordinator.CLASSIC_SHADOW_KEY] == {"cap": {"scrub": 3}}
        # The eight named shadows are still seeded too -- this is additive, not a replacement.
        assert coordinator.data["ro-currentstate"] == {"_name": "ro-currentstate"}

    @pytest.mark.asyncio
    async def test_classic_shadow_failure_does_not_block_setup(self) -> None:
        """A failure fetching the classic/unnamed shadow specifically
        must not raise ConfigEntryNotReady -- that guard exists for the
        eight named shadows all failing together, a different, more
        serious situation than this one shadow alone being unreachable."""
        coordinator, _config_entry, prime_robot = _make_status_coordinator()

        async def fake_get_named_shadow(name):
            return ShadowResponse(topic="x", payload={"state": {"reported": {"_name": name}}})

        prime_robot.get_named_shadow = fake_get_named_shadow
        prime_robot.get_state = AsyncMock(side_effect=ShadowConnectionError("simulated"))

        await coordinator.async_start()  # must not raise

        assert PrimeStatusCoordinator.CLASSIC_SHADOW_KEY not in coordinator.data
        assert coordinator.data["ro-currentstate"] == {"_name": "ro-currentstate"}


class TestAsyncWatchStatusUpdatesRetryBehavior:
    """_async_watch_status_updates()'s own outer retry loop -- added
    after a real field report (chairstacker): sensors got stuck at
    stale/Unknown values after some activity, fixed only by a full HA
    restart, not just reloading the integration. roombapy-prime's own
    watch_named_shadows_updates() already retries connection drops
    internally with unbounded retries -- this outer loop is a second,
    coordinator-level safety net for anything else that could end the
    generator unexpectedly, so a single unexpected error degrades to
    "retry after a delay" rather than "permanently dead until Home
    Assistant itself restarts"."""

    @pytest.mark.asyncio
    async def test_retries_after_unexpected_exception_instead_of_dying_permanently(self) -> None:
        coordinator, _config_entry, prime_robot = _make_status_coordinator()
        call_count = 0

        async def fake_watch_named_shadows_updates():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated unexpected failure")
            yield ShadowResponse(
                topic="$aws/things/BLID123/shadow/name/ro-currentstate/update/accepted",
                payload={"state": {"reported": {"batPct": 72}}},
            )
            # Stop the loop after one successful iteration so the test itself terminates --
            # a real run would keep going forever, which is exactly the point being tested.
            raise asyncio.CancelledError

        prime_robot.watch_named_shadows_updates = fake_watch_named_shadows_updates

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_watch_status_updates()

        # The key assertion: the generator was called a SECOND time after the first
        # one raised -- proving this is a real retry, not a permanent give-up.
        assert call_count == 2
        assert coordinator.data == {"ro-currentstate": {"batPct": 72}}


class TestDeepMergeReported:
    """_deep_merge_reported() -- CONFIRMED NECESSARY from a real field
    report with screenshots (chairstacker): starting a mission caused
    detected_pad/dock_status/firmware_version/runtime/pad_dry/pad_wash
    to ALL go "Unknown" simultaneously, while suction_level (a
    different named shadow, rw-settings, which received no update
    during that window) correctly kept its real value the whole time
    -- proving AWS's own "update/accepted" messages only echo back
    the fields that changed, not the shadow's full state, and a plain
    dict replacement was discarding everything else."""

    def test_preserves_existing_fields_not_mentioned_in_the_update(self):
        """Reproduces the exact reported scenario: a full ro-currentstate
        seed, then a mission-start update containing only
        cleanMissionStatus -- every other field must survive."""
        existing = {
            "batPct": 100,
            "detectedPad": "padPlate",
            "dock": {"state": 301, "pwState": 601, "pdState": 701},
            "runtimeStats": {"hr": 24, "min": 0},
        }
        partial_update = {"cleanMissionStatus": {"phase": "reloc"}}

        result = _deep_merge_reported(existing, partial_update)

        assert result["batPct"] == 100
        assert result["detectedPad"] == "padPlate"
        assert result["dock"] == {"state": 301, "pwState": 601, "pdState": 701}
        assert result["runtimeStats"] == {"hr": 24, "min": 0}
        assert result["cleanMissionStatus"] == {"phase": "reloc"}

    def test_merges_nested_dicts_recursively_not_just_top_level(self):
        """A partial update to a NESTED field (e.g. only dock.state
        changing) must not wipe out sibling fields within that same
        nested object (dock.pwState/pdState)."""
        existing = {"dock": {"state": 301, "pwState": 601, "pdState": 701}}
        partial_update = {"dock": {"state": 302}}

        result = _deep_merge_reported(existing, partial_update)

        assert result["dock"] == {"state": 302, "pwState": 601, "pdState": 701}

    def test_does_not_mutate_the_original_existing_dict(self):
        existing = {"batPct": 100}

        _deep_merge_reported(existing, {"detectedPad": "padPlate"})

        assert existing == {"batPct": 100}

    def test_explicit_null_does_not_clobber_a_real_existing_value(self):
        """SECOND REAL BUG (chairstacker, later report): battery still
        went to "Unknown" immediately on mission start even with the
        wholesale-replace fix above already in place. A mission-start
        update apparently includes "batPct": null EXPLICITLY, not an
        omitted key -- the exact same "explicit null vs missing key"
        class of bug this project has hit 12+ times elsewhere. An
        explicit null must not clobber a real, already-known value."""
        existing = {"batPct": 100, "detectedPad": "padPlate"}
        mission_start_update = {"cleanMissionStatus": {"phase": "run"}, "batPct": None}

        result = _deep_merge_reported(existing, mission_start_update)

        assert result["batPct"] == 100
        assert result["cleanMissionStatus"] == {"phase": "run"}

    def test_a_real_new_value_still_updates_normally(self):
        """Confirms the null-guard above doesn't accidentally block
        genuine updates -- only an explicit null is special-cased."""
        existing = {"batPct": 100}

        result = _deep_merge_reported(existing, {"batPct": 95})

        assert result["batPct"] == 95

    def test_explicit_null_is_accepted_when_no_real_value_exists_yet(self):
        """A null for a field that was never known in the first place
        (existing_value is None too) is harmless either way -- confirms
        the guard is specifically about NOT clobbering real data, not
        about rejecting null unconditionally."""
        existing: dict = {}

        result = _deep_merge_reported(existing, {"batPct": None})

        assert result["batPct"] is None


class TestAsyncWatchStatusUpdatesMergeBehavior:
    @pytest.mark.asyncio
    async def test_partial_update_preserves_previously_seeded_fields(self) -> None:
        """End-to-end version of the same bug, through the actual
        coordinator method: seed with a full ro-currentstate payload,
        then feed a partial update -- the previously-seeded fields
        must survive in coordinator.data afterward."""
        coordinator, _config_entry, prime_robot = _make_status_coordinator()
        coordinator.async_set_updated_data({
            "ro-currentstate": {"batPct": 100, "detectedPad": "padPlate"},
            "rw-settings": {"suctionLevel": 3},
        })

        async def fake_watch():
            yield ShadowResponse(
                topic="$aws/things/BLID123/shadow/name/ro-currentstate/update/accepted",
                payload={"state": {"reported": {"cleanMissionStatus": {"phase": "reloc"}}}},
            )
            raise asyncio.CancelledError

        prime_robot.watch_named_shadows_updates = fake_watch

        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_watch_status_updates()

        assert coordinator.data["ro-currentstate"]["batPct"] == 100
        assert coordinator.data["ro-currentstate"]["detectedPad"] == "padPlate"
        assert coordinator.data["ro-currentstate"]["cleanMissionStatus"] == {"phase": "reloc"}
        # the untouched shadow must survive completely unaffected.
        assert coordinator.data["rw-settings"] == {"suctionLevel": 3}


class TestGetPrimeCapabilityFlags:
    """NEW (this session) -- get_prime_capability_flags(), used to
    gate six Prime entities on real per-device hardware capability
    (see its own docstring for the "None means unknown, only explicit
    0 means absent" contract)."""

    def _make_config_entry(self, coordinator_data):
        config_entry = MagicMock()
        coordinator = MagicMock()
        coordinator.data = coordinator_data
        config_entry.runtime_data.prime_status_coordinator = coordinator
        return config_entry

    def test_returns_none_none_when_coordinator_missing(self):
        from custom_components.roomba_plus.prime_coordinator import get_prime_capability_flags

        config_entry = MagicMock()
        config_entry.runtime_data.prime_status_coordinator = None

        cap, dock_cap = get_prime_capability_flags(config_entry)

        assert cap is None
        assert dock_cap is None

    def test_returns_none_none_when_coordinator_data_missing(self):
        from custom_components.roomba_plus.prime_coordinator import get_prime_capability_flags

        config_entry = self._make_config_entry(None)

        cap, dock_cap = get_prime_capability_flags(config_entry)

        assert cap is None
        assert dock_cap is None

    def test_extracts_cap_from_classic_shadow(self):
        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator, get_prime_capability_flags,
        )

        config_entry = self._make_config_entry({
            PrimeStatusCoordinator.CLASSIC_SHADOW_KEY: {"cap": {"scrub": 3, "carpetBoost": 0}},
        })

        cap, dock_cap = get_prime_capability_flags(config_entry)

        assert cap.scrub == 3
        assert cap.carpet_boost == 0
        assert dock_cap is None

    def test_extracts_dock_cap_from_ro_currentstate(self):
        from custom_components.roomba_plus.prime_coordinator import get_prime_capability_flags

        config_entry = self._make_config_entry({
            "ro-currentstate": {"dock": {"cap": {"pw": 1, "pd": 0}}},
        })

        cap, dock_cap = get_prime_capability_flags(config_entry)

        assert cap is None
        assert dock_cap.pad_wash == 1
        assert dock_cap.pad_dry == 0

    def test_extracts_both_when_both_shadows_present(self):
        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator, get_prime_capability_flags,
        )

        config_entry = self._make_config_entry({
            PrimeStatusCoordinator.CLASSIC_SHADOW_KEY: {"cap": {"suctionLvl": 4}},
            "ro-currentstate": {"dock": {"cap": {"pw": 1, "pd": 1}}},
        })

        cap, dock_cap = get_prime_capability_flags(config_entry)

        assert cap.suction_lvl == 4
        assert dock_cap.pad_wash == 1


class TestPrimePushFeedsTheFreshnessSignal:
    """GAP FOUND IN THE FIELD (DaRealGuGu). `last_mqtt_message_ts` is
    written only from callbacks.py, which is a Classic-only code path --
    so for Prime robots it stayed at 0.0 forever and every staleness
    check built on it sat permanently silent.

    His symptom is exactly what that allows: the mission event sensor
    froze on "fin" and the dock status stopped updating, both at once,
    while the integration reported itself perfectly healthy. Two
    independent sensors stopping together points at the stream rather
    than at either sensor -- and nothing was watching the stream.

    The error path above cannot catch this: a watch that stops
    DELIVERING without ever RAISING simply never yields again. A
    timestamp is the only thing separating "quiet because nothing is
    happening" from "quiet because the stream died"."""

    def _entry(self):
        from unittest.mock import MagicMock

        entry = MagicMock()
        entry.runtime_data.last_mqtt_message_ts = 0.0
        # Derived on RoombaData: a MagicMock would answer with a
        # MagicMock, not a number.
        entry.runtime_data.silence_reference_ts = 0.0
        return entry

    @pytest.mark.asyncio
    async def test_a_mission_event_updates_the_timestamp(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from custom_components.roomba_plus.prime_coordinator import PrimeCoordinator

        entry = self._entry()
        robot = MagicMock()

        async def _one_event():
            yield MagicMock(payload={"event": [{"type": "start", "ts": 1}]})

        robot.watch_mission_timeline = _one_event
        coordinator = object.__new__(PrimeCoordinator)
        coordinator.blid = "B"
        coordinator.prime_robot = robot
        # BOTH, because `__new__` skips the constructor that sets them.
        #
        # The three coordinators narrow `config_entry` to `self.entry`
        # in `__init__` -- mypy cannot see through the base class's
        # `ConfigEntry | None`. A test that builds one with
        # `object.__new__` has to supply what the constructor would
        # have.
        coordinator.config_entry = entry
        coordinator.entry = entry
        coordinator.async_set_updated_data = MagicMock()

        # NARROWED (this session): this used to suppress bare Exception,
        # which swallowed an AttributeError from a method name that did
        # not exist -- so the test passed the wrong function name in
        # silence and asserted against a timestamp nothing had ever
        # written. A suppress broad enough to hide the test being wrong
        # is worse than no test.
        with patch.object(coordinator, "async_set_update_error", MagicMock()), \
             patch("asyncio.sleep", AsyncMock(side_effect=StopAsyncIteration)):
            with contextlib.suppress(StopAsyncIteration):
                await coordinator._async_watch_mission_timeline()

        assert entry.runtime_data.last_mqtt_message_ts > 0.0

    def test_the_timestamp_starts_at_zero(self):
        """First-boot guard elsewhere relies on 0.0 meaning 'nothing has
        ever arrived', so it must not be pre-seeded."""
        entry = self._entry()

        assert entry.runtime_data.last_mqtt_message_ts == 0.0


class TestZoneNamesComeFromTheCommand:
    """APK 3.0.0: `IrobotTimelineRegionNameResolver` reads
    `cmd.regions[].region_name`. The timeline events carry no name of
    their own — `RobotTimelineZone` has only `zid`.

    @chairstacker's `--list-rooms` reports `name=None` for all eight of
    his zones while his app timeline reads "Guest Access Zone" and
    "Living Room @Wall". Both true: the map has no names, the command
    does. Home Assistant showed `Zone {id}` because it read the map,
    correctly, and the data was elsewhere.
    """

    @staticmethod
    def _data(regions):
        from types import SimpleNamespace

        return SimpleNamespace(
            roomba_reported_state=lambda: {
                "lastCommand": {"regions": regions}
            }
        )

    def test_labels_are_collected(self):
        from custom_components.roomba_plus.prime_coordinator import (
            prime_region_names_from_command,
        )

        names = prime_region_names_from_command(
            self._data([
                {"region_id": "100", "region_name": "Guest Access Zone"},
                {"region_id": "16", "region_name": "Living Room @Wall"},
            ])
        )

        assert names == {
            "100": "Guest Access Zone",
            "16": "Living Room @Wall",
        }

    def test_an_unlabelled_region_stays_unnamed(self):
        """No invented names. A region cleaned without a label has
        none, and `Zone {id}` is the honest answer there."""
        from custom_components.roomba_plus.prime_coordinator import (
            prime_region_names_from_command,
        )

        names = prime_region_names_from_command(
            self._data([
                {"region_id": "100"},
                {"region_id": "101", "region_name": ""},
            ])
        )

        assert names == {}

    def test_a_command_with_no_regions_is_harmless(self):
        from custom_components.roomba_plus.prime_coordinator import (
            prime_region_names_from_command,
        )

        assert prime_region_names_from_command(self._data([])) == {}


class TestMissionHistorySyncsAtMissionEnd:
    """@chairstacker: `area_cleaned_today` read 0.0 after a successful
    mission, and `clean_streak` was right only after a reload.

    Mission records for Prime robots come from one place — the cloud
    history sync. Everything that writes a record from MQTT hangs off
    the Classic `roomba` callback chain, which a Prime robot never
    feeds.

    That sync ran at setup and then on the coordinator's own interval:
    six hours. A mission finishing at 07:00 was invisible to every
    mission sensor until the next tick or the next reload — which is
    why it looked non-deterministic.
    """

    def test_charge_triggers_a_sync(self):
        import inspect

        from custom_components.roomba_plus import prime_coordinator

        source = inspect.getsource(prime_coordinator)

        assert "_schedule_mission_history_sync" in source
        assert 'if phase == "charge":' in source

    def test_overlapping_syncs_are_guarded(self):
        """`charge` can arrive more than once for one mission — a robot
        that settles, or a shadow resend."""
        import inspect

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        source = inspect.getsource(
            PrimeStatusCoordinator._schedule_mission_history_sync
        )

        assert "_history_sync_running" in source
        assert "finally" in source

    def test_a_failed_sync_is_not_fatal(self):
        """A cloud blip must not surface as a broken mission: the
        six-hourly sync and the next mission end both try again."""
        import inspect

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        source = inspect.getsource(
            PrimeStatusCoordinator._schedule_mission_history_sync
        )

        assert "except Exception" in source


class TestPrimeFiresRoomCompleted:
    """Classic has fired `roomba_plus_room_completed` all along; Prime
    never did, because it comes from callbacks.py and that is a
    Classic-only path -- the same shape as the `last_mqtt_message_ts`
    gap @DaRealGuGu found.

    Prime users had the per-room state but no trigger to hang an
    automation on: "tell me when the kitchen is done" had nothing to
    fire on.
    """

    @staticmethod
    def _coordinator():
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        c = PrimeCoordinator.__new__(PrimeCoordinator)
        c.hass = MagicMock()
        c.entry = MagicMock(entry_id="E1", title="Robot")
        # `prime_room_names`, NOT the Classic coordinator. This fixture
        # fed a source a Prime entry does not have, so it agreed with a
        # lookup that returned None for every region -- and the test
        # passed while the event's `room_name` was always empty.
        c.entry.runtime_data.prime_room_names = {"10": "Kitchen", "11": "Hall"}
        c._last_room_id = None
        return c

    @staticmethod
    def _report(region_id, mission_id="M1"):
        from unittest.mock import MagicMock

        report = MagicMock(mission_id=mission_id)
        if region_id is None:
            report.event = []
        else:
            event = MagicMock()
            event.room = MagicMock(region_id=region_id)
            report.event = [event]
        return report

    def test_leaving_a_room_fires_the_event(self):
        c = self._coordinator()

        c._fire_room_completed_if_changed(self._report("10"))
        c.hass.bus.async_fire.assert_not_called()  # first report: nothing done yet

        c._fire_room_completed_if_changed(self._report("11"))

        c.hass.bus.async_fire.assert_called_once()
        name, payload = c.hass.bus.async_fire.call_args[0]
        assert name == "roomba_plus_room_completed"
        # The room that FINISHED, not the one now being cleaned.
        assert payload["room_id"] == "10"
        assert payload["room_name"] == "Kitchen"

    def test_the_same_room_repeated_does_not_re_fire(self):
        """The timeline repeats the current room on every update.
        Re-firing would make the event useless as a trigger."""
        c = self._coordinator()
        c._fire_room_completed_if_changed(self._report("10"))
        c._fire_room_completed_if_changed(self._report("11"))
        c.hass.bus.async_fire.reset_mock()

        c._fire_room_completed_if_changed(self._report("11"))

        c.hass.bus.async_fire.assert_not_called()

    def test_an_unknown_id_still_fires_with_no_name(self):
        """A region the cloud has no name for is still a room that was
        cleaned -- dropping the event would lose real information."""
        c = self._coordinator()
        c._fire_room_completed_if_changed(self._report("99"))
        c._fire_room_completed_if_changed(self._report("10"))

        payload = c.hass.bus.async_fire.call_args[0][1]
        assert payload["room_id"] == "99"
        assert payload["room_name"] is None

    def test_the_end_of_a_mission_fires_for_the_last_room(self):
        """The robot leaves the last room for no room at all."""
        c = self._coordinator()
        c._fire_room_completed_if_changed(self._report("10"))
        c._fire_room_completed_if_changed(self._report(None))

        payload = c.hass.bus.async_fire.call_args[0][1]
        assert payload["room_id"] == "10"


class TestAMissedChargeTransitionStillSyncs:
    """@chairstacker (#64): his second zone mission of the day was
    ignored completely — no sensor update, no activity entry — while the
    connection sensor read Connected. Reloading the integration brought
    it in.

    The history sync fired only on observing a `charge` transition. Miss
    that one message and the mission never syncs until a reload or the
    six-hour interval.

    `nMssn` counts missions and only goes up, so a change means one
    ended whatever was observed in between.
    """

    @staticmethod
    def _coordinator():
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeStatusCoordinator,
        )

        c = PrimeStatusCoordinator.__new__(PrimeStatusCoordinator)
        c._schedule_mission_history_sync = MagicMock()
        c._note_dock_position = MagicMock()
        # The handler returns early without a timer store, so the mock
        # has to carry one or nothing under test ever runs.
        c.entry = MagicMock()
        return c

    @staticmethod
    def _feed(c, phase, nmssn):
        """Calls the real handler.

        A FIRST VERSION OF THIS REIMPLEMENTED THE LOGIC HERE, which
        would have proved the test agrees with itself and nothing about
        the coordinator — the same mistake that let a broken zone-name
        reader ship green three times.
        """
        c._note_phase_for_timer({
            "ro-currentstate": {
                "cleanMissionStatus": {"phase": phase, "nMssn": nmssn}
            }
        })

    def test_a_mission_counted_without_a_charge_phase_still_syncs(self):
        c = self._coordinator()
        self._feed(c, "run", 372)          # baseline
        c._schedule_mission_history_sync.reset_mock()

        self._feed(c, "run", 373)          # a mission ended unseen

        c._schedule_mission_history_sync.assert_called_once()

    def test_the_first_reading_does_not_fire(self):
        """No previous count means no transition — firing here would
        sync on every reconnect."""
        c = self._coordinator()

        self._feed(c, "run", 373)

        c._schedule_mission_history_sync.assert_not_called()

    def test_an_unchanged_count_does_not_fire(self):
        c = self._coordinator()
        self._feed(c, "run", 373)
        c._schedule_mission_history_sync.reset_mock()

        self._feed(c, "run", 373)

        c._schedule_mission_history_sync.assert_not_called()

    def test_charge_still_syncs_on_its_own(self):
        """The existing path must keep working."""
        c = self._coordinator()
        self._feed(c, "run", 373)
        c._schedule_mission_history_sync.reset_mock()

        self._feed(c, "charge", 373)

        c._schedule_mission_history_sync.assert_called_once()


class TestTheRoomNameInItsOwnEvent:
    """`_room_name()` read `cloud_coordinator.regions_by_pmap`. A Prime
    entry has no `cloud_coordinator`, so it returned None for every
    region — and it feeds the `room_name` field of the event a caller
    automates on. "Kitchen finished" arrived without the kitchen.
    """

    @staticmethod
    def _coordinator(names):
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        c = PrimeCoordinator.__new__(PrimeCoordinator)
        entry = MagicMock()
        entry.runtime_data.prime_room_names = names
        c.entry = entry
        return c

    def test_a_room_resolves(self):
        assert self._coordinator({"10": "Kitchen"})._room_name("10") == "Kitchen"

    def test_a_zone_resolves_too(self):
        """`prime_room_names` is flat and holds both, which is what
        makes this work for a zone clean."""
        c = self._coordinator({"107": "Guest Access Zone"})

        assert c._room_name("107") == "Guest Access Zone"

    def test_an_unknown_id_is_none(self):
        assert self._coordinator({"10": "Kitchen"})._room_name("99") is None


class TestPrimeAdvancesFromItsOwnTimeline:
    """Prime reports where it is going more precisely than Classic does
    -- a region id in the mission timeline, against Classic's bare "am I
    driving" bit -- and every bit of that was being spent on firing an
    automation event.

    `advance_room()` had exactly one caller, in the Classic MQTT
    callback. So a Prime robot's room display could only move via the
    phase route, and that route requires a per-room time estimate. A
    robot in auto pass mode never gets one: it decides its passes while
    running, so there is nothing to estimate in advance, and the
    fallback is a whole-house average divided by the mission's room
    count.

    Same principle as the Classic fix: an observation may be acted on
    without a forecast agreeing.
    """

    @staticmethod
    def _coordinator(planned, index, names):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        store = MagicMock()
        store.planned_rooms = planned
        store.current_room_idx = index
        store.advance_room.return_value = True

        coordinator = PrimeCoordinator.__new__(PrimeCoordinator)
        coordinator.hass = MagicMock()
        coordinator.entry = SimpleNamespace(
            entry_id="e1",
            runtime_data=SimpleNamespace(mission_timer_store=store),
        )
        coordinator._room_name = lambda rid: names.get(rid)
        return coordinator, store

    def test_leaving_the_displayed_room_advances_it(self) -> None:
        coordinator, store = self._coordinator(
            ["Guest Bathroom", "Hallway"], 0, {"15": "Guest Bathroom"}
        )

        coordinator._advance_display_if_next("15")

        assert store.advance_room.called

    def test_a_report_for_another_room_is_ignored(self) -> None:
        """Missions revisit, and reports arrive late. Anything but an
        exact match on the room being shown is left alone rather than
        guessed at."""
        coordinator, store = self._coordinator(
            ["Guest Bathroom", "Hallway"], 0, {"99": "Kitchen"}
        )

        coordinator._advance_display_if_next("99")

        assert not store.advance_room.called

    def test_it_does_not_advance_out_of_the_last_room(self) -> None:
        coordinator, store = self._coordinator(
            ["Guest Bathroom", "Hallway"], 1, {"14": "Hallway"}
        )

        coordinator._advance_display_if_next("14")

        assert not store.advance_room.called

    def test_no_region_id_does_nothing(self) -> None:
        coordinator, store = self._coordinator(["A", "B"], 0, {})

        coordinator._advance_display_if_next(None)

        assert not store.advance_room.called

    def test_no_estimate_is_consulted(self) -> None:
        """The whole point: this route acts on an observation. If it
        grew an estimate check it would inherit the fault it exists to
        route around."""
        import inspect

        from custom_components.roomba_plus.prime_coordinator import (
            PrimeCoordinator,
        )

        # THE CODE, NOT THE COMMENTARY. The docstring explains what this
        # route avoids, so a plain substring search over the source
        # matches the explanation and fails on its own reasoning.
        import ast

        tree = ast.parse(
            inspect.getsource(PrimeCoordinator._advance_display_if_next).strip()
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                node.value = ast.Constant(value="")
        code = ast.unparse(tree)

        # NAMES READ, not words appearing. The debug line says "no
        # estimate consulted", so a substring search finds the word in
        # the message announcing its own absence.
        read = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } | {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        for forbidden in (
            "expected_room_sec", "room_estimate_cache",
            "_room_transition_confidence_ok", "cached_room_seconds",
        ):
            assert forbidden not in read, forbidden


# ── formerly tests/test_coverage_prime_coordinator.py ───────────────────────────
#
# prime_coordinator.py — quality scale, test-coverage.
#
# The Prime coordinators poll or stream from the cloud. A failing part of a
# refresh must not take the rest with it, the rejected-command stream must
# back off and reconnect rather than spin, and nothing half-read replaces
# good data.

def _sched(hass, **runtime):
    c = pc.PrimeScheduleCoordinator.__new__(pc.PrimeScheduleCoordinator)
    c.hass = hass
    c.entry = MagicMock()
    c.prime_robot = MagicMock()
    c.blid = "PRIMECOORD"
    c.weekday_names, c.room_names, c.quiet_hours = {}, {}, None
    for k, v in runtime.items():
        setattr(c.entry.runtime_data, k, v)
    return c


def _status(hass):
    c = pc.PrimeStatusCoordinator.__new__(pc.PrimeStatusCoordinator)
    c.hass = hass
    c.blid = "PRIMECOORD"
    c.prime_robot = MagicMock()
    c.entry = MagicMock()
    c.config_entry = MagicMock()
    c.async_set_updated_data = MagicMock()
    return c


class TestRejectedCommandWatch:

    def _coord(self, streams):
        c = pc.PrimeCoordinator.__new__(pc.PrimeCoordinator)
        c.prime_robot = MagicMock()
        it = iter(streams)

        def _watch():
            s = next(it)
            if isinstance(s, BaseException):
                raise s

            async def _gen():
                for m in s:
                    yield m
            return _gen()

        c.prime_robot.watch_rejected_commands = _watch
        c.blid = "PRIMECOORD"
        return c

    @pytest.mark.asyncio
    async def test_a_library_without_the_stream_ends_the_watch(self):
        c = self._coord([ValueError("not supported")])
        await c._async_watch_rejected_commands()   # returns, no loop

    @pytest.mark.asyncio
    async def test_failures_back_off_exponentially_up_to_five_minutes(self, monkeypatch):
        waits = []

        async def _sleep(s):
            waits.append(s)
            if len(waits) >= 8:
                raise asyncio.CancelledError

        monkeypatch.setattr(pc.asyncio, "sleep", _sleep)
        c = self._coord([RuntimeError("down")] * 10)
        with pytest.raises(asyncio.CancelledError):
            await c._async_watch_rejected_commands()
        assert waits[:5] == [5.0, 10.0, 20.0, 40.0, 80.0]
        assert max(waits) == 300.0

    @pytest.mark.asyncio
    async def test_a_message_resets_the_backoff(self, monkeypatch):
        waits = []

        async def _sleep(s):
            waits.append(s)
            if len(waits) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(pc.asyncio, "sleep", _sleep)
        c = self._coord([RuntimeError("down"), [SimpleNamespace(cmd="x")], RuntimeError("down")])
        with pytest.raises(asyncio.CancelledError):
            await c._async_watch_rejected_commands()
        assert waits[0] == 5.0 and waits[1] == 5.0, "the stream delivered: back to the start"


class TestPartsCoordinator:

    def _coord(self, parts=None, error=None, entry=True):
        c = pc.PrimePartsCoordinator.__new__(pc.PrimePartsCoordinator)
        c.prime_robot = MagicMock()
        c.prime_robot.get_robot_parts = AsyncMock(side_effect=error,
                                                  return_value=SimpleNamespace(parts=parts or []))
        c.config_entry = MagicMock() if entry else None
        return c

    @pytest.mark.asyncio
    async def test_a_cloud_error_is_an_update_failure(self):
        with pytest.raises(UpdateFailed):
            await self._coord(error=RuntimeError("down"))._async_update_data()

    @pytest.mark.asyncio
    async def test_parts_without_an_id_are_dropped_and_history_is_synced(self, monkeypatch):
        from custom_components.roomba_plus import prime_mission_sync

        sync = AsyncMock()
        monkeypatch.setattr(prime_mission_sync, "async_sync_prime_missions", sync)
        parts = [SimpleNamespace(part_id="filter"), SimpleNamespace(part_id=None)]
        result = await self._coord(parts)._async_update_data()
        assert list(result) == ["filter"]
        sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_failing_history_sync_does_not_lose_the_parts(self, monkeypatch):
        from custom_components.roomba_plus import prime_mission_sync

        monkeypatch.setattr(prime_mission_sync, "async_sync_prime_missions",
                            AsyncMock(side_effect=RuntimeError("history")))
        result = await self._coord([SimpleNamespace(part_id="filter")])._async_update_data()
        assert list(result) == ["filter"]


class TestScheduleCoordinator:

    @pytest.mark.asyncio
    async def test_no_schedules_readable_is_an_update_failure(self, hass, monkeypatch):
        from custom_components.roomba_plus import prime_schedule_switch

        c = _sched(hass, prime_robot=None, prime_household_id=None)
        c._async_load_weekday_names = AsyncMock()
        c._async_refresh_room_names = AsyncMock()
        monkeypatch.setattr(prime_schedule_switch, "async_read_schedule_containers", AsyncMock(return_value=None))
        with pytest.raises(UpdateFailed):
            await c._async_update_data()

    @pytest.mark.asyncio
    async def test_weekday_names_load_once_and_only_complete(self, hass, monkeypatch):
        from homeassistant.helpers import translation

        table = {f"component.roomba_plus.common.weekday_{i}": f"Tag{i}" for i in range(7)}
        get = AsyncMock(return_value=table)
        monkeypatch.setattr(translation, "async_get_translations", get)
        c = _sched(hass)
        await c._async_load_weekday_names()
        await c._async_load_weekday_names()
        assert c.weekday_names[0] == "Tag0" and get.await_count == 1

    @pytest.mark.asyncio
    async def test_an_incomplete_weekday_table_is_not_used(self, hass, monkeypatch):
        from homeassistant.helpers import translation

        monkeypatch.setattr(translation, "async_get_translations",
                            AsyncMock(return_value={"component.roomba_plus.common.weekday_0": "Mo"}))
        c = _sched(hass)
        await c._async_load_weekday_names()
        assert c.weekday_names == {}

    @pytest.mark.asyncio
    async def test_a_failing_translation_leaves_the_names_empty(self, hass, monkeypatch):
        from homeassistant.helpers import translation

        monkeypatch.setattr(translation, "async_get_translations", AsyncMock(side_effect=RuntimeError()))
        c = _sched(hass)
        await c._async_load_weekday_names()
        assert c.weekday_names == {}

    @pytest.mark.asyncio
    async def test_room_names_are_kept_when_a_refresh_fails_or_is_empty(self, hass, monkeypatch):
        from roombapy_prime import models

        c = _sched(hass)
        c.room_names = {"3": "Kitchen"}
        c.prime_robot.get_active_map_versions = AsyncMock(side_effect=RuntimeError())
        await c._async_refresh_room_names()
        assert c.room_names == {"3": "Kitchen"}
        c.prime_robot.get_active_map_versions = AsyncMock(return_value=[])
        monkeypatch.setattr(models, "build_room_name_map", lambda *_a, **_k: {})
        monkeypatch.setattr(models, "parse_active_map_versions", lambda r: r)
        await c._async_refresh_room_names()
        assert c.room_names == {"3": "Kitchen"}

    @pytest.mark.asyncio
    async def test_favourites_are_replaced_in_place(self, hass, monkeypatch):
        from custom_components.roomba_plus import button_prime

        shared = ["old"]
        c = _sched(hass, prime_robot=MagicMock(), prime_favorites=shared)
        monkeypatch.setattr(button_prime, "async_favorites_attribute", AsyncMock(return_value=["a", "b"]))
        await c._async_refresh_favourites()
        assert shared == ["a", "b"], "the same list object other code holds"

    @pytest.mark.asyncio
    async def test_a_failing_favourites_read_keeps_them(self, hass, monkeypatch):
        from custom_components.roomba_plus import button_prime

        shared = ["old"]
        c = _sched(hass, prime_robot=MagicMock(), prime_favorites=shared)
        monkeypatch.setattr(button_prime, "async_favorites_attribute", AsyncMock(side_effect=RuntimeError()))
        await c._async_refresh_favourites()
        assert shared == ["old"]

    @pytest.mark.asyncio
    async def test_quiet_hours_need_a_robot_and_a_household(self, hass):
        assert await _sched(hass, prime_robot=None, prime_household_id="h")._async_read_quiet_hours() is None
        robot = MagicMock(get_dnd_settings=AsyncMock(side_effect=RuntimeError()))
        assert await _sched(hass, prime_robot=robot, prime_household_id="h")._async_read_quiet_hours() is None
        robot = MagicMock(get_dnd_settings=AsyncMock(return_value="dnd"))
        assert await _sched(hass, prime_robot=robot, prime_household_id="h")._async_read_quiet_hours() == "dnd"


class TestStatusStart:

    @pytest.mark.asyncio
    async def test_no_shadow_readable_at_all_is_not_ready(self, hass):
        from homeassistant.exceptions import ConfigEntryNotReady

        c = _status(hass)
        c.prime_robot.get_named_shadow = AsyncMock(side_effect=ShadowConnectionError("down"))
        with pytest.raises(ConfigEntryNotReady):
            await c.async_start()

    @pytest.mark.asyncio
    async def test_one_readable_shadow_is_enough_to_start(self, hass):
        """A partial seed beats no robot at all; the stream fills the rest."""
        c = _status(hass)
        calls = {"n": 0}

        async def _named(name):
            calls["n"] += 1
            if calls["n"] == 1:
                return SimpleNamespace(payload={"state": {"reported": {"x": 1}}})
            raise ShadowConnectionError("down")

        c.prime_robot.get_named_shadow = _named
        c.prime_robot.get_state = AsyncMock(side_effect=ShadowConnectionError("down"))
        c.entry.async_create_background_task = MagicMock(side_effect=lambda _h, coro, **k: coro.close())
        await c.async_start()
        seeded = c.async_set_updated_data.call_args.args[0]
        assert len(seeded) == 1 and list(seeded.values())[0] == {"x": 1}


class TestHistorySyncAfterMission:

    @pytest.mark.asyncio
    async def test_it_runs_once_and_clears_its_flag_even_on_error(self, hass, monkeypatch):
        from custom_components.roomba_plus import prime_mission_sync

        sync = AsyncMock(side_effect=[2, RuntimeError("cloud")])
        monkeypatch.setattr(prime_mission_sync, "async_sync_prime_missions", sync)
        c = _status(hass)
        c._history_sync_running = False
        c._schedule_mission_history_sync()
        await hass.async_block_till_done()
        c._schedule_mission_history_sync()
        await hass.async_block_till_done()
        assert sync.await_count == 2
        assert c._history_sync_running is False

    def test_a_running_sync_is_not_started_twice(self, hass):
        c = _status(hass)
        c._history_sync_running = True
        c.hass = MagicMock()
        c._schedule_mission_history_sync()
        c.hass.async_create_task.assert_not_called()

    @pytest.mark.parametrize("entry", [None, SimpleNamespace(runtime_data=None)])
    def test_no_loaded_entry_no_sync(self, hass, entry):
        c = _status(hass)
        c._history_sync_running = False
        c.config_entry = entry
        c.hass = MagicMock()
        c._schedule_mission_history_sync()
        c.hass.async_create_task.assert_not_called()


class TestSmallHelpers:

    def _pc(self, names):
        c = pc.PrimeCoordinator.__new__(pc.PrimeCoordinator)
        c.entry = SimpleNamespace(runtime_data=SimpleNamespace(prime_room_names=names))
        return c

    @pytest.mark.parametrize("names,rid,erwartet", [
        (None, "3", None), ({"3": "Kitchen"}, 3, "Kitchen"), ({"3": ""}, "3", None), ({}, "9", None)])
    def test_room_name(self, names, rid, erwartet):
        assert self._pc(names)._room_name(rid) == erwartet

    @pytest.mark.parametrize("positions,dock", [
        (None, None), ([], None), ([(1,)], None), ([("x", "y")], None), ([(0, 0), (1.5, -2)], (1.5, -2.0)),
    ])
    def test_the_last_position_becomes_the_dock_only_when_readable(self, hass, positions, dock):
        c = _status(hass)
        data = SimpleNamespace(prime_positions=positions, prime_observed_dock=None)
        c.entry = SimpleNamespace(runtime_data=data)
        c._note_dock_position()
        assert data.prime_observed_dock == dock


# ── formerly tests/test_prime_dock_entity_timing.py ───────────────────
#
# The dock gate is re-read on every shadow, and never removes.
#
# WHY THIS EXISTS. Dock-derived entities were created once, in
# `async_setup_entry`, from whatever `ro-currentstate.dock` happened to say
# at that instant. The gate reads `dock.known`, and @AlakazipLabs showed
# that field is not stable: across 24,900 telemetry messages they logged 15
# true-to-false flips, 11 of them within seconds of a user `dock` command
# (median 4 s), returning on their own after 95 s, 18 min and 34 min with
# no dock contact. 0 of 35 self-docks flipped it.
#
# So a restart or config-entry reload landing inside one of those windows
# cost a robot with a real dock its pad-wash and pad-dry entities until the
# next reload happened to fall elsewhere.
#
# THE GATE RULE IS NOT WHAT CHANGED -- an evidence-based rule was tried and
# withdrawn, because no capture in this repo shows `pwState`/`pdState`
# arriving at rest and it would have removed the sensors from every dock
# that had not washed yet. What changed is that the question is asked again
# on every shadow, and that the answer can only ever ADD.
#
# NEGATIVE CONTROLS INCLUDED. Each test here was run against the old
# setup-only code path first; the recovery tests fail there, which is what
# makes them worth keeping.

class _Coordinator:
    """Minimal stand-in that records its listener and can fire it."""

    def __init__(self, data):
        self.data = data
        self._listeners = []

    def async_add_listener(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def fire(self):
        for callback in list(self._listeners):
            callback()


def _entry(dock: dict | None):
    entry = MagicMock()
    entry.runtime_data.prime_status_coordinator = _Coordinator(
        {"ro-currentstate": {"dock": dock}} if dock is not None else {}
    )
    entry.async_on_unload = MagicMock()
    return entry


def _entity(unique_id: str):
    entity = MagicMock()
    entity.unique_id = unique_id
    return entity


class TestDockEntitiesArriveLate:
    def test_a_dock_that_reports_nothing_yet_gets_nothing(self):
        entry = _entry(None)
        added: list = []

        add_prime_entities_when_available(
            entry, added.extend, lambda: []
        )

        assert added == []

    def test_the_entity_appears_when_the_shadow_says_so(self):
        """The reload-timing bug, directly. Setup sees an empty dock;
        the next shadow carries it."""
        entry = _entry(None)
        added: list = []
        wanted: list = []

        add_prime_entities_when_available(
            entry, added.extend, lambda: list(wanted)
        )
        assert added == []

        wanted.append(_entity("pad_wash"))
        entry.runtime_data.prime_status_coordinator.fire()

        assert [e.unique_id for e in added] == ["pad_wash"]


class TestNothingIsEverRemoved:
    def test_a_dip_to_unknown_takes_no_entity_away(self):
        """`known` flipping false is exactly @AlakazipLabs' case, and it
        must cost nothing. Add-only is what makes an unstable source
        survivable without changing what the source means."""
        entry = _entry({"known": True})
        added: list = []
        wanted = [_entity("pad_wash")]

        add_prime_entities_when_available(
            entry, added.extend, lambda: list(wanted)
        )
        assert len(added) == 1

        wanted.clear()
        entry.runtime_data.prime_status_coordinator.fire()

        assert [e.unique_id for e in added] == ["pad_wash"]

    def test_the_same_entity_is_not_added_twice(self):
        """The builder returns a fresh object each call, so identity is
        no guard -- the unique_id is."""
        entry = _entry({"known": True})
        added: list = []

        add_prime_entities_when_available(
            entry, added.extend, lambda: [_entity("pad_wash")]
        )
        for _ in range(3):
            entry.runtime_data.prime_status_coordinator.fire()

        assert len(added) == 1


class TestTheListenerIsCleanedUp:
    def test_it_registers_for_unload(self):
        """A listener that outlives the config entry keeps a dead
        builder alive and fires it against torn-down runtime_data."""
        entry = _entry({"known": True})

        add_prime_entities_when_available(entry, lambda _: None, lambda: [])

        assert entry.async_on_unload.called

    def test_no_coordinator_is_survivable(self):
        """A Prime entry whose status coordinator never came up still
        sets up; it simply gets the one static pass."""
        entry = MagicMock()
        entry.runtime_data.prime_status_coordinator = None
        added: list = []

        add_prime_entities_when_available(
            entry, added.extend, lambda: [_entity("pad_wash")]
        )

        assert len(added) == 1
