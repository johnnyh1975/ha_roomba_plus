from unittest.mock import AsyncMock
from unittest.mock import MagicMock
import pytest
import custom_components.roomba_plus as init_mod
from homeassistant.const import CONF_NAME
from custom_components.roomba_plus.const import ROOMBA_SESSION
from types import SimpleNamespace
from homeassistant.exceptions import ConfigEntryNotReady
from custom_components.roomba_plus.cloud_coordinator import IrobotCloudCoordinator as _RealCloud
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.roomba_plus.const import DOMAIN
from unittest.mock import patch
from custom_components.roomba_plus import _STORAGE_KEYS_TO_REMOVE
from custom_components.roomba_plus import async_remove_entry
import time
import statistics
from custom_components.roomba_plus.mission_archive import MissionArchive
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.robot_profile_store import RobotProfileStore
from typing import Any
from custom_components.roomba_plus import _SetupContext
from custom_components.roomba_plus import _phase_spatial
from custom_components.roomba_plus.models import MapCapability


class TestUnknownSkuIsReportedAfterSetup:
    """An unrecognised SKU produces a report request AFTER the account
    setup succeeds, not at discovery time.

    The timing is the whole point. At discovery all that exists is an
    SKU string, and whether the robot is really Prime is a guess. By the
    time setup has succeeded it is a fact -- account-based setup only
    works for a Prime-generation robot -- and a diagnostics download
    exists carrying the capability flags and shadow structure.

    That is what makes a new model worth reporting. Every capability gap
    this project has found came from exactly that data, and each
    surfaced only because a tester pasted raw output nobody had thought
    to ask for.

    Asking earlier would also have asked more people: an unrecognised
    SKU at discovery is sometimes just a Classic robot the table missed."""

    def _warn_for(self, sku):
        from unittest.mock import patch

        from custom_components.roomba_plus import _async_note_unknown_sku

        with patch("custom_components.roomba_plus._LOGGER") as logger:
            _async_note_unknown_sku(sku, "BLID123")
        return logger.warning

    def test_a_known_prime_sku_produces_no_request(self):
        """The overwhelmingly common case. A warning here would train
        users to ignore the log, and the real one would go unread."""
        for sku in ("G185020", "N185240", "Y414040"):
            self._warn_for(sku).assert_not_called()

    def test_an_unknown_sku_produces_one(self):
        assert self._warn_for("Z995020").called

    def test_a_missing_sku_produces_one(self):
        """Absent is not the same as recognised."""
        assert self._warn_for(None).called

    def test_the_message_makes_clear_nothing_is_broken(self):
        """The robot works. Without saying so, an unexplained warning
        reads as a fault the user is expected to fix."""
        message = self._warn_for("Z995020").call_args.args[0]

        assert "Everything should work" in message

    def test_the_message_asks_for_the_diagnostics_download(self):
        """The reason for reporting at this moment rather than earlier:
        the file exists now, and it holds the capability data."""
        message = self._warn_for("Z995020").call_args.args[0]

        assert "diagnostics" in message.lower()

    def test_the_url_carries_the_sku_but_never_the_blid(self):
        """An SKU identifies a product. A BLID identifies one specific
        robot, adds nothing to classifying a model, and belongs to the
        user rather than a public issue."""
        from urllib.parse import parse_qs, urlparse

        from custom_components.roomba_plus import _unknown_sku_issue_url

        query = parse_qs(urlparse(_unknown_sku_issue_url("Z995020")).query)

        assert "Z995020" in query["title"][0]
        assert "Z995020" in query["body"][0]
        assert "BLID123" not in query["body"][0]

    def test_the_url_points_at_the_real_tracker(self):
        from urllib.parse import urlparse

        from custom_components.roomba_plus import _unknown_sku_issue_url
        from custom_components.roomba_plus.const import ISSUE_TRACKER_URL

        parsed = urlparse(_unknown_sku_issue_url("Z995020"))

        assert parsed.scheme == "https"
        assert ISSUE_TRACKER_URL.startswith(f"https://{parsed.netloc}")
        assert parsed.path.endswith("/issues/new")


class TestRepairIssuesAreClearedOnUnload:
    """@ScenicSystemsLLC: after rolling back from a38 and reinstalling,
    a `smart_zones_need_naming` issue was still showing — timestamped
    from the failed attempt, for zones that were already named.

    Home Assistant keeps issues in its own registry, so nothing about
    unloading or removing an integration clears them. An issue raised
    by a condition that no longer exists sends someone to check
    something that is already fine.
    """

    def test_unload_clears_them(self):
        from unittest.mock import MagicMock, patch

        from custom_components.roomba_plus import (
            _async_clear_repair_issues,
        )

        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "abc123"

        with patch(
            "homeassistant.helpers.issue_registry.async_delete_issue"
        ) as deleter:
            _async_clear_repair_issues(hass, entry)

        cleared = {call.args[2] for call in deleter.call_args_list}
        assert "smart_zones_need_naming_abc123" in cleared
        assert "smart_zones_need_naming" in cleared, (
            "the singleton form has no entry_id and is the one that "
            "actually survived his reinstall"
        )

    def test_unload_actually_calls_it(self):
        """A helper nothing calls clears nothing."""
        import inspect

        from custom_components.roomba_plus import async_unload_entry

        assert "_async_clear_repair_issues" in inspect.getsource(
            async_unload_entry
        )


class TestTheTlsContextIsBuiltOffTheLoop:
    """Home Assistant caught this on a live install:

        Detected blocking call to load_default_certs ... inside the
        event loop by custom integration 'roomba_plus' at
        __init__.py, line 355: roomba = RoombaClient(

    The 4.2 migration took the client construction out of the executor
    with the comment "the constructor does no I/O". It does:
    `RoombaClient.__init__` builds a TLS context, and
    `load_default_certs()` reads the system trust store from disk.

    roombapy caches that context, so the cost is paid once per process
    -- but once in the event loop is still once in the event loop, on
    every fresh start.

    Nothing in this project's own guards catches this shape. They check
    that coroutines are awaited rather than threaded; a synchronous
    constructor doing hidden file I/O is the opposite mistake, and only
    Home Assistant's own loop protection sees it.
    """

    def test_the_cache_is_warmed_before_the_client_is_built(self) -> None:
        import inspect

        import custom_components.roomba_plus as integration

        source = inspect.getsource(integration._phase_connect)

        warm = source.index("_warm_tls_context")
        build = source.index("roomba = RoombaClient(")

        assert warm < build, "the context must be cached before construction"

    def test_the_warm_up_runs_in_the_executor(self) -> None:
        import inspect

        import custom_components.roomba_plus as integration

        source = inspect.getsource(integration._phase_connect)

        assert "async_add_executor_job(_warm_tls_context)" in source

    def test_a_failure_to_warm_is_not_fatal(self) -> None:
        """The fallback is a log line, not a failed setup: the
        constructor would build the context itself and Home Assistant
        would warn again. A warning beats a robot that will not set up.
        """
        import inspect

        import custom_components.roomba_plus as integration

        source = inspect.getsource(integration._warm_tls_context)

        assert "except Exception" in source
        assert "raise" not in source


class TestSavingAnEntityOptionTakesEffect:
    """Options that decide whether an entity EXISTS are read once, when
    the platform sets up. Changing one and saving therefore does nothing
    until the integration happens to reload for another reason.

    @chairstacker enabled "Separate sensor per room and zone" and the
    entities never appeared. The option was saved correctly; nothing
    asked for them to be built.

    The map options are deliberately excluded: they are read on every
    render and take effect on the next frame. The test for membership is
    "read at setup time", not "affects what the user sees".
    """

    def test_entity_creating_options_trigger_a_reload(self) -> None:
        import inspect

        from custom_components.roomba_plus import __init__ as module

        source = inspect.getsource(module._async_reload_on_options_change)

        for option in (
            "CONF_ENABLE_SCHEDULE_CALENDAR",
            "CONF_REGION_SENSORS",
            "CONF_BLOCKING_SENSORS",
        ):
            assert option in source, option

    def test_render_time_options_are_not_included(self) -> None:
        """Reloading an entry to change a map colour would be a heavy
        answer to a light question."""
        import inspect

        from custom_components.roomba_plus import __init__ as module

        source = inspect.getsource(module._async_reload_on_options_change)

        for option in ("CONF_MAP_ROOM_LABELS", "CONF_MAP_CLEAN_ZONES"):
            assert option not in source, option


# ── formerly tests/test_coverage_init.py ────────────────────────────────────────
#
# __init__.py — quality scale, test-coverage.
#
# `async_connect_or_timeout` decides whether a Classic setup succeeds; every
# test replaced it. It must wait for the robot's name, give a smart-map
# robot time to report its maps, and turn every connection failure into
# CannotConnect so the entry retries instead of crashing.

class _Robot:
    """Reports connected and its name after `steps` polls."""

    def __init__(self, *, steps=1, name="Robbie", cap=None, pmaps_after=0, error=None):
        self._polls, self._steps, self._name = 0, steps, name
        self._cap, self._pmaps_after, self._error = cap or {}, pmaps_after, error
        self.connected = False

    async def connect(self):
        if self._error:
            raise self._error
        self.connected = True

    @property
    def master_state(self):
        self._polls += 1
        reported = {"cap": self._cap}
        if self._polls >= self._steps:
            reported["name"] = self._name
        if self._polls >= self._steps + self._pmaps_after:
            reported["pmaps"] = [{"p1": "v"}]
        return {"state": {"reported": reported}}


@pytest.fixture
def _fast(monkeypatch):
    waits = []

    async def _sleep(s):
        waits.append(s)

    monkeypatch.setattr(init_mod.asyncio, "sleep", _sleep)
    return waits


class _FakeCloud(_RealCloud):
    """The real coordinator with only the network replaced: every derived
    property (regions, parts, raw records, geometry) is the real code."""

    fail: Exception | None = None

    async def async_config_entry_first_refresh(self):
        if self.fail:
            raise self.fail
        self.last_update_success = True
        self.data = {"pmaps": [], "mission_history_raw": [{"nMssn": 1}],
                     "parts": {"parts": [{"part_id": "filter"}]}, "umf": {}}

    @property
    def observed_zone_centroids(self):
        return [{"x": 1.0, "y": 2.0}]


def _cloud_ctx(hass, monkeypatch, *, fail=None):
    monkeypatch.setattr(_FakeCloud, "fail", fail)
    monkeypatch.setattr(init_mod, "IrobotCloudCoordinator", _FakeCloud)
    ctx = _ctx(hass, _STATE_POSE_ONLY)
    ctx.config_entry.data = {"blid": "BLID_TEST", "irobot_username": "u@x.invalid",
                             "irobot_password": "pw"}
    ctx.roomba = MagicMock()
    ctx.roomba.master_state = {"state": {"reported": {**_STATE_POSE_ONLY, "bbrun": {"hr": 120}}}}
    ctx.state = {**_STATE_POSE_ONLY, "bbrun": {"hr": 120}}
    ms = MagicMock()
    ms.backfill_from_cloud.return_value = SimpleNamespace(corrected=True, enriched=False)
    ms.async_save = AsyncMock()
    ctx.mission_store = ms
    grid = MagicMock()
    grid.seed_from_observed_zones.return_value = 1
    grid.async_save = AsyncMock()
    ctx.grid_store = grid
    maint = MagicMock()
    maint.hydrate_from_cloud_parts.return_value = True
    maint.async_save = AsyncMock()
    ctx.maintenance_store = maint
    return ctx


def _robot_mock(state):
    r = MagicMock()
    r.master_state = {"state": {"reported": state}}
    r.register_on_message_callback = MagicMock(return_value=lambda: None)
    r.register_on_connection_state_callback = MagicMock(return_value=lambda: None)
    r.connected = True
    r.disconnect = AsyncMock()   # the real client's disconnect is a coroutine
    return r


async def _setup(hass, monkeypatch, *, data=None, options=None, state=None):
    state = state or {**_STATE_POSE_ONLY, "name": "Robbie", "bbrun": {"hr": 10}}

    async def _connect(ctx):
        ctx.roomba = _robot_mock(state)
        ctx.state = state
        return True

    monkeypatch.setattr(init_mod, "_phase_connect", _connect)
    entry = MockConfigEntry(domain=DOMAIN, version=25, unique_id="BLID_E2E",
                            data={"blid": "BLID_E2E", "host": "10.0.0.9", "password": "pw", **(data or {})},
                            options=options or {})
    entry.add_to_hass(hass)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    monkeypatch.setattr(hass, "http", MagicMock())   # no HTTP component in the test hass
    # Through Home Assistant's own entry manager, so the whole lifecycle
    # runs — including the unload callbacks that stop the timers setup
    # starts.
    ok = await hass.config_entries.async_setup(entry.entry_id)
    return ok, entry


async def _teardown(hass, entry):
    from homeassistant.config_entries import ConfigEntryState

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    assert entry.state is ConfigEntryState.NOT_LOADED
    await hass.async_block_till_done()


class TestConnectOrTimeout:

    @pytest.mark.asyncio
    async def test_it_waits_for_the_name(self, hass, _fast):
        robot = _Robot(steps=3)
        result = await init_mod.async_connect_or_timeout(hass, robot)
        assert result == {ROOMBA_SESSION: robot, CONF_NAME: "Robbie"}

    @pytest.mark.asyncio
    async def test_waiting_for_smart_maps_is_bounded(self, hass, _fast):
        """A smart-map robot gets time to report its maps — but if they
        never come, setup goes on after six seconds instead of hanging."""
        robot = _Robot(cap={"pmaps": 1}, pmaps_after=10_000)
        result = await init_mod.async_connect_or_timeout(hass, robot)
        assert result[CONF_NAME] == "Robbie"
        assert _fast.count(1) == 6

    @pytest.mark.asyncio
    async def test_maps_that_arrive_end_the_wait_early(self, hass, _fast):
        robot = _Robot(cap={"pmaps": 1}, pmaps_after=0)
        await init_mod.async_connect_or_timeout(hass, robot)
        assert _fast.count(1) < 6

    @pytest.mark.asyncio
    async def test_a_robot_without_smart_maps_does_not_wait_for_them(self, hass, _fast):
        await init_mod.async_connect_or_timeout(hass, _Robot(cap={"maps": 1}))
        assert _fast == [2]   # only the settle pause after the name

    @pytest.mark.asyncio
    async def test_a_refused_connection_is_cannot_connect(self, hass, _fast):
        from roombapy import RoombaConnectionError

        with pytest.raises(init_mod.CannotConnect):
            await init_mod.async_connect_or_timeout(hass, _Robot(error=RoombaConnectionError("no")))

    @pytest.mark.asyncio
    async def test_a_timeout_disconnects_and_is_cannot_connect(self, hass, _fast, monkeypatch):
        disconnect = AsyncMock()
        monkeypatch.setattr(init_mod, "async_disconnect_or_timeout", disconnect)
        with pytest.raises(init_mod.CannotConnect):
            await init_mod.async_connect_or_timeout(hass, _Robot(error=TimeoutError()))
        disconnect.assert_awaited_once()


class TestPhaseCloudWithCoordinator:

    @pytest.mark.asyncio
    async def test_the_first_refresh_backfills_seeds_and_hydrates(self, hass, monkeypatch):
        from custom_components.roomba_plus import _phase_cloud, _phase_spatial

        ctx = _cloud_ctx(hass, monkeypatch)
        await _phase_spatial(ctx)
        ctx.grid_store = ctx.grid_store or MagicMock()
        await _phase_cloud(ctx)
        ctx.mission_store.backfill_from_cloud.assert_called_once()
        ctx.mission_store.async_save.assert_awaited()
        assert ctx.maintenance_store.hydrate_from_cloud_parts.call_args.args[1] == 120
        ctx.maintenance_store.async_save.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_first_refresh_is_logged_with_its_cause_and_setup_goes_on(
        self, hass, monkeypatch, caplog
    ):
        """By design: the robot stays usable locally. The log names the
        exception type — two incidents were argued about without it."""
        from custom_components.roomba_plus import _phase_cloud, _phase_spatial

        ctx = _cloud_ctx(hass, monkeypatch, fail=OSError("dns"))
        await _phase_spatial(ctx)
        await _phase_cloud(ctx)
        assert "OSError" in caplog.text
        ctx.mission_store.backfill_from_cloud.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_credentials_stop_setup_for_reauthentication(self, hass, monkeypatch):
        from homeassistant.exceptions import ConfigEntryAuthFailed

        from custom_components.roomba_plus import _phase_cloud, _phase_spatial

        ctx = _cloud_ctx(hass, monkeypatch, fail=ConfigEntryAuthFailed("bad password"))
        await _phase_spatial(ctx)
        with pytest.raises(ConfigEntryAuthFailed):
            await _phase_cloud(ctx)


class TestClassicSetupEndToEnd:
    """Every phase individually was tested; their chain never ran."""

    @pytest.fixture(autouse=True)
    def _custom(self, enable_custom_integrations):
        """Let Home Assistant's loader find the integration."""

    @pytest.mark.asyncio
    async def test_a_local_robot_sets_up_fully(self, hass, monkeypatch):
        ok, entry = await _setup(hass, monkeypatch)
        assert ok is True
        assert entry.runtime_data is not None
        assert entry.runtime_data.mission_store is not None
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        await _teardown(hass, entry)

    @pytest.mark.asyncio
    async def test_a_smart_map_robot_with_a_cloud_account_sets_up_fully(self, hass, monkeypatch):
        """The platform list is gated on the SMART map, not on the account.
        Note: CLOUD_PLATFORMS (select, button) are also local platforms, so
        that extend adds nothing today; what a map adds is the image."""
        from homeassistant.const import Platform

        monkeypatch.setattr(_FakeCloud, "fail", None)
        monkeypatch.setattr(init_mod, "IrobotCloudCoordinator", _FakeCloud)
        state = {**_STATE_SMART_MAP_NO_POSE, "name": "Robbie", "bbrun": {"hr": 10}}
        ok, entry = await _setup(hass, monkeypatch, state=state,
                                 data={"irobot_username": "u@x.invalid", "irobot_password": "pw"})
        assert ok is True
        assert entry.runtime_data.cloud_coordinator is not None
        platforms = hass.config_entries.async_forward_entry_setups.await_args.args[1]
        assert Platform.IMAGE in platforms
        await _teardown(hass, entry)

    @pytest.mark.asyncio
    async def test_an_old_roombapy_is_not_ready_with_an_explanation(self, hass, monkeypatch):
        from homeassistant.exceptions import ConfigEntryNotReady

        from custom_components.roomba_plus import repairs

        monkeypatch.setattr(init_mod, "ROOMBAPY_IMPORT_ERROR", "roombapy 1.9")
        monkeypatch.setattr(repairs, "async_check_core_roomba_conflict", AsyncMock())
        entry = MockConfigEntry(domain=DOMAIN, version=25, data={"blid": "B", "host": "h", "password": "p"})
        entry.add_to_hass(hass)
        with pytest.raises(ConfigEntryNotReady, match="roombapy 2.x"):
            await init_mod.async_setup_entry(hass, entry)


class TestSmallModuleHelpers:

    def test_warming_tls_never_raises(self, monkeypatch):
        import roombapy.tls

        monkeypatch.setattr(roombapy.tls, "generate_tls_context", MagicMock(side_effect=OSError("no ssl")))
        init_mod._warm_tls_context()   # must not raise

    @pytest.mark.asyncio
    async def test_time_estimates_need_a_robot_and_survive_a_failure(self):
        entry = SimpleNamespace(runtime_data=SimpleNamespace(prime_robot=None))
        await init_mod._async_fetch_prime_time_estimates(entry)
        robot = MagicMock(get_time_estimates=AsyncMock(side_effect=RuntimeError("cloud")))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(prime_robot=robot, prime_time_estimates=None))
        await init_mod._async_fetch_prime_time_estimates(entry)
        assert entry.runtime_data.prime_time_estimates is None


class TestSmartCloudStores:

    @pytest.fixture(autouse=True)
    def _custom(self, enable_custom_integrations):
        """Let Home Assistant's loader find the integration."""

    @pytest.mark.asyncio
    async def test_demand_cleaning_and_the_mission_timer_come_with_smart_and_cloud(self, hass, monkeypatch):
        from custom_components.roomba_plus.const import CONF_DEMAND_CLEANING_ENABLED

        monkeypatch.setattr(_FakeCloud, "fail", None)
        monkeypatch.setattr(init_mod, "IrobotCloudCoordinator", _FakeCloud)
        state = {**_STATE_SMART_MAP_NO_POSE, "name": "Robbie", "bbrun": {"hr": 10}}
        ok, entry = await _setup(hass, monkeypatch, state=state,
                                 data={"irobot_username": "u@x.invalid", "irobot_password": "pw"},
                                 options={CONF_DEMAND_CLEANING_ENABLED: True})
        assert ok is True
        assert entry.runtime_data.dirt_threshold_manager is not None
        assert entry.runtime_data.mission_timer_store is not None
        await _teardown(hass, entry)


class TestLastErrorAtStartup:
    """The last error sensor must show the last error after a restart —
    read back from the stored missions, newest first."""

    @pytest.mark.parametrize("records,code,zone", [
        ([{"result": "completed"}, {"result": "error", "error_code": 17, "ended_at": "t2", "zones": ["Hall"]}],
         17, "Hall"),
        ([{"result": "error", "error_code": 5, "ended_at": "t1"}, {"result": "stuck_and_abandoned", "ended_at": "t2"}],
         None, None),
    ])
    @pytest.mark.asyncio
    async def test_the_newest_error_or_stuck_mission_wins(self, hass, records, code, zone, monkeypatch):
        from custom_components.roomba_plus import _phase_data, _phase_spatial
        from custom_components.roomba_plus.mission_store import MissionStore

        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
        await _phase_spatial(ctx)

        async def _load(self, _h, _e):
            self._records = [dict(r) for r in records]

        monkeypatch.setattr(MissionStore, "async_load", _load)
        await _phase_data(ctx)
        assert ctx.last_error_code == code
        assert ctx.last_error_at == records[-1]["ended_at"]
        assert ctx.last_error_zone == zone


class TestTimeEstimatesStored:

    @pytest.mark.asyncio
    async def test_a_successful_read_is_stored_and_remembered(self, monkeypatch):
        from roombapy_prime import models

        from custom_components.roomba_plus import sensor_rooms

        monkeypatch.setattr(models.TimeEstimates, "from_json", staticmethod(lambda raw: ("parsed", raw)))
        remembered = MagicMock(return_value=3)
        monkeypatch.setattr(sensor_rooms, "remember_confident_estimates", remembered)
        robot = MagicMock(get_time_estimates=AsyncMock(return_value={"x": 1}))
        entry = SimpleNamespace(runtime_data=SimpleNamespace(prime_robot=robot, prime_time_estimates=None))
        await init_mod._async_fetch_prime_time_estimates(entry)
        assert entry.runtime_data.prime_time_estimates == ("parsed", {"x": 1})
        remembered.assert_called_once_with(entry)


class TestUnloadNeverFails:

    @pytest.fixture(autouse=True)
    def _custom(self, enable_custom_integrations):
        """Let Home Assistant's loader find the integration."""

    @pytest.mark.asyncio
    async def test_a_queued_start_is_cancelled_and_a_failing_save_is_survived(self, hass, monkeypatch):
        """A robot waiting for a door to close, and a disk that refuses the
        timer store: unload still completes and the queue is let go."""

        monkeypatch.setattr(_FakeCloud, "fail", None)
        monkeypatch.setattr(init_mod, "IrobotCloudCoordinator", _FakeCloud)
        state = {**_STATE_SMART_MAP_NO_POSE, "name": "Robbie", "bbrun": {"hr": 10}}
        ok, entry = await _setup(hass, monkeypatch, state=state,
                                 data={"irobot_username": "u@x.invalid", "irobot_password": "pw"})
        assert ok is True
        bm = MagicMock(is_queued=True)
        entry.runtime_data.blocking_manager = bm
        entry.runtime_data.mission_timer_store.async_save = AsyncMock(side_effect=OSError("disk full"))
        await _teardown(hass, entry)
        bm.cancel_queue.assert_called_once()


class TestDiscoveredZonesBackfill:

    @pytest.mark.asyncio
    async def test_named_zones_missing_from_the_discovered_list_are_added(self, hass, monkeypatch):
        """Zones the user named must count as discovered, or the naming
        step would hide them."""
        from custom_components.roomba_plus import _phase_connect, _SetupContext
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_DATA

        robot = _robot_mock({"name": "Robbie"})
        monkeypatch.setattr(init_mod, "RoombaClient", MagicMock(return_value=robot))
        monkeypatch.setattr(init_mod, "async_connect_or_timeout",
                            AsyncMock(return_value={ROOMBA_SESSION: robot, CONF_NAME: "Robbie"}))
        entry = MockConfigEntry(domain=DOMAIN, version=25,
                                data={"blid": "B", "host": "10.0.0.9", "password": "pw"},
                                options={CONF_SMART_ZONE_DATA: {"3": {"name": "Kitchen"}, "5": {}},
                                         "discovered_zone_ids": ["1"], "continuous": True})
        entry.add_to_hass(hass)
        await _phase_connect(_SetupContext(hass=hass, config_entry=entry))
        assert entry.options["discovered_zone_ids"] == ["1", "3", "5"]


class TestPresenceAtSetup:

    @pytest.fixture(autouse=True)
    def _custom(self, enable_custom_integrations):
        """Let Home Assistant's loader find the integration."""

    @pytest.mark.asyncio
    async def test_presence_scheduling_enabled_starts_the_manager(self, hass, monkeypatch):
        from custom_components.roomba_plus.const import (
            CONF_PRESENCE_ENTITIES,
            CONF_PRESENCE_SCHEDULING_ENABLED,
        )

        ok, entry = await _setup(hass, monkeypatch, options={
            CONF_PRESENCE_ENTITIES: ["person.anna"], CONF_PRESENCE_SCHEDULING_ENABLED: True})
        assert ok is True
        pm = entry.runtime_data.presence_manager
        assert pm is not None and pm._cancel_listeners, "started: it listens to the people"
        await _teardown(hass, entry)

    @pytest.mark.asyncio
    async def test_people_alone_do_not_start_it(self, hass, monkeypatch):
        """Configured people may serve other features; presence scheduling
        is its own switch."""
        from custom_components.roomba_plus.const import CONF_PRESENCE_ENTITIES

        ok, entry = await _setup(hass, monkeypatch, options={CONF_PRESENCE_ENTITIES: ["person.anna"]})
        assert ok is True and entry.runtime_data.presence_manager is None
        await _teardown(hass, entry)


# ── formerly tests/test_config_entry_removal.py ───────────────────────
#
# v3.4.0 bug-hunt fix — tests for async_remove_entry().
#
# Found during the README/docs review: __init__.py had async_unload_entry
# (runs on every reload too) but no async_remove_entry (HA's hook that
# fires ONLY on permanent config-entry deletion) — meaning none of the
# 15 hass.storage files this integration persists were ever actually
# deleted when a user removed the integration, contradicting the
# TROUBLESHOOTING.md claim that deletion "removes... cleanly".
#
# These tests verify every expected storage key gets a removal attempt
# with the correct entry_id substituted, and that one failing removal
# doesn't prevent the rest from being attempted.

def _make_entry(entry_id: str = "test_entry_123"):
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


class TestStorageKeyRegistry:
    """Guard test — a store added to the integration in the future
    without a matching entry here would silently leak its storage file
    on every removal, same class of bug this whole fix addresses."""

    def test_expected_key_count(self):
        """16 stores: 13 STORAGE_KEY_PREFIX modules + image.py's 3 own
        keys (the third is the Prime map PNG, added this session).

        A count assertion looks brittle and earns its place here. It
        fired the moment a new Store was added without a removal entry
        -- which is the whole failure mode: nothing else notices a
        forgotten key until a user uninstalls and a file with a picture
        of their home stays behind.

        Bumping this number is the point at which you confirm you added
        the entry deliberately rather than making a test pass."""
        assert len(_STORAGE_KEYS_TO_REMOVE) == 16

    def test_no_duplicate_keys(self):
        templates = [k for _, k in _STORAGE_KEYS_TO_REMOVE]
        assert len(templates) == len(set(templates))

    def test_every_template_takes_entry_id(self):
        for label, template in _STORAGE_KEYS_TO_REMOVE:
            assert "{entry_id}" in template, f"{label}: missing entry_id placeholder"

    @pytest.mark.parametrize("expected_key", [
        "roomba_plus_dirt_threshold_{entry_id}",
        "roomba_plus_freeze_{entry_id}",
        "roomba_plus_geometry_{entry_id}",
        "roomba_plus_grid_{entry_id}",
        "roomba_plus_zones_{entry_id}",
        "roomba_plus_maintenance_{entry_id}",
        "roomba_plus_mission_archive_{entry_id}",
        "roomba_plus_missions_{entry_id}",
        "roomba_plus_mission_timer_{entry_id}",
        "roomba_plus_trajectories_{entry_id}",
        "roomba_plus_outline_{entry_id}",
        "roomba_plus_robot_profile_{entry_id}",
        "roomba_plus_roomseg_{entry_id}",
        "roomba_plus_map_{entry_id}",
        "roomba_plus_map_checkpoint_{entry_id}",
    ])
    def test_each_known_store_key_present(self, expected_key):
        templates = [k for _, k in _STORAGE_KEYS_TO_REMOVE]
        assert expected_key in templates


class TestAsyncRemoveEntry:
    @pytest.mark.asyncio
    async def test_removes_every_storage_key_with_correct_entry_id(self):
        entry = _make_entry("abc123")
        hass = MagicMock()
        created_stores = []

        def _fake_store(hass_arg, version, key):
            store = MagicMock()
            store.async_remove = AsyncMock()
            created_stores.append((version, key, store))
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)

        assert len(created_stores) == len(_STORAGE_KEYS_TO_REMOVE)
        created_keys = {key for _, key, _ in created_stores}
        expected_keys = {
            template.format(entry_id="abc123")
            for _, template in _STORAGE_KEYS_TO_REMOVE
        }
        assert created_keys == expected_keys
        for _, _, store in created_stores:
            store.async_remove.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_stores_use_version_1(self):
        """Confirmed against source: every store in this integration
        declares HA-store version 1. Store.async_remove() doesn't
        actually use the version for anything, but using the wrong one
        would be a needless inconsistency."""
        entry = _make_entry()
        hass = MagicMock()
        versions = []

        def _fake_store(hass_arg, version, key):
            versions.append(version)
            store = MagicMock()
            store.async_remove = AsyncMock()
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)

        assert all(v == 1 for v in versions)

    @pytest.mark.asyncio
    async def test_one_failing_removal_does_not_block_the_rest(self):
        """The core resilience property — a permissions error or
        unexpected I/O failure on ONE file must not leave the other 14
        untouched."""
        entry = _make_entry()
        hass = MagicMock()
        call_count = 0

        def _fake_store(hass_arg, version, key):
            nonlocal call_count
            call_count += 1
            store = MagicMock()
            if call_count == 3:
                store.async_remove = AsyncMock(side_effect=OSError("disk error"))
            else:
                store.async_remove = AsyncMock()
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store):
            await async_remove_entry(hass, entry)  # must not raise

        assert call_count == len(_STORAGE_KEYS_TO_REMOVE)

    @pytest.mark.asyncio
    async def test_missing_file_is_not_treated_as_a_failure(self):
        """Store.async_remove() already suppresses FileNotFoundError
        internally (verified against HA source) — a robot tier that
        never created a given store (e.g. a 600-series robot has no
        GridStore data) must not log a warning for every such key."""
        entry = _make_entry()
        hass = MagicMock()

        def _fake_store(hass_arg, version, key):
            store = MagicMock()
            store.async_remove = AsyncMock()  # succeeds silently, as real Store does
            return store

        with patch("homeassistant.helpers.storage.Store", side_effect=_fake_store),\
             patch("custom_components.roomba_plus._LOGGER") as mock_logger:
            await async_remove_entry(hass, entry)

        mock_logger.warning.assert_not_called()
        mock_logger.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_stores_attempted_regardless_of_robot_tier(self):
        """No gating by map_capability or any other runtime_data field —
        deletion cleans up every possible file unconditionally, since a
        config entry being removed may have switched tiers over its
        lifetime (e.g. a firmware update) and could have leftover data
        from an earlier tier."""
        entry = _make_entry()
        hass = MagicMock()
        # Deliberately do NOT set up entry.runtime_data — if the
        # implementation ever started gating on it, this test would
        # fail with an AttributeError on a MagicMock-default access
        # that wasn't anticipated, surfacing the coupling immediately.
        del entry.runtime_data

        with patch("homeassistant.helpers.storage.Store") as mock_store_cls:
            mock_store_cls.return_value.async_remove = AsyncMock()
            await async_remove_entry(hass, entry)  # must not raise

        assert mock_store_cls.call_count == len(_STORAGE_KEYS_TO_REMOVE)


# ── formerly tests/test_init_wiring.py ────────────────────────────────
#
# Consolidated domain test file (TEST-REORG).
#
# Merged by the v2.8.x test reorganisation from multiple version-named
# test files; see git history for provenance.

SQFT_TO_M2 = 0.092903  # from const.py


def _derived(
    n_mssn: int,
    duration_min: int = 45,
    sqft: float = 300.0,
    dirt: int = 5,
    result: str = "completed",
    rooms: dict | None = None,
) -> dict:
    return {
        "nMssn": n_mssn,
        "duration_min": duration_min,
        "sqft": sqft,
        "dirt": dirt,
        "result": result,
        "rooms_completed": rooms or {},
    }


def _make_archive(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    archive = MissionArchive()
    for rec in records:
        archive._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            archive._archived_nmssns.add(int(n))
    archive._initial_load_done = initial_load_done
    return archive


def _make_ms() -> MissionStore:
    return MissionStore()


def _make_archive_v280_l5_arc(
    records: list[dict],
    initial_load_done: bool = True,
) -> MissionArchive:
    """Build a MissionArchive with pre-populated derived records."""
    archive = MissionArchive()
    # Insert oldest-first so newest ends up at index 0 (archive is newest-first)
    for rec in records:
        archive._derived.insert(0, rec)
        n = rec.get("nMssn")
        if n:
            archive._archived_nmssns.add(int(n))
            if int(n) > archive._last_nMssn:
                archive._last_nMssn = int(n)
    archive._initial_load_done = initial_load_done
    return archive


def _derived_v280_l5_arc(
    n_mssn: int,
    rooms: dict | None = None,
) -> dict:
    """Build a minimal derived record for testing."""
    return {
        "nMssn": n_mssn,
        "result": "completed",
        "rooms_completed": rooms or {},
    }


def _make_rps() -> RobotProfileStore:
    return RobotProfileStore()


def _make_hass() -> MagicMock:
    return MagicMock()


class TestGsSmartUmfLogging:
    """_extract_traversal_umf_positions logs when < min_missions traversal found."""

    def _make_aligner(self, candidates: int = 5) -> MagicMock:
        aligner = MagicMock()
        aligner._door_candidates = [(float(i), float(i)) for i in range(candidates)]
        return aligner

    def _make_records(self, with_traversal: int, total: int = 10) -> list:
        records = []
        for i in range(total):
            if i < with_traversal:
                records.append({
                    "timeline": {
                        "finEvents": [{"type": "traversal", "traversal": {"rid": "1"}}]
                    }
                })
            else:
                records.append({"timeline": {"finEvents": []}})
        return records

    def test_info_log_emitted_when_below_min(self):
        from custom_components.roomba_plus.callbacks import _extract_traversal_umf_positions
        aligner = self._make_aligner()
        records = self._make_records(with_traversal=1, total=100)

        with patch("custom_components.roomba_plus.callbacks._LOGGER") as mock_log:
            result = _extract_traversal_umf_positions(records, aligner, min_missions=3)

        assert result == []
        mock_log.info.assert_called_once()
        call_args = mock_log.info.call_args[0]
        assert "GS-SMART-UMF" in call_args[0]

    def test_returns_candidates_when_enough_missions(self):
        from custom_components.roomba_plus.callbacks import _extract_traversal_umf_positions
        aligner = self._make_aligner(candidates=5)
        records = self._make_records(with_traversal=5, total=10)

        with patch("custom_components.roomba_plus.callbacks._LOGGER"):
            result = _extract_traversal_umf_positions(records, aligner, min_missions=3)

        assert len(result) == 5


class TestSeedL3FromArchive:
    async def _seed(
        self,
        archive: MissionArchive,
        ms: MissionStore | None = None,
    ) -> MissionStore:
        if ms is None:
            ms = _make_ms()
        from custom_components.roomba_plus.__init__ import _async_seed_l3_from_archive
        await _async_seed_l3_from_archive(archive, ms)
        return ms

    @pytest.mark.asyncio
    async def test_injects_baseline(self):
        records = [_derived(i) for i in range(1, 25)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is not None
        assert "duration_mean" in ms.archive_baseline

    @pytest.mark.asyncio
    async def test_guard_initial_load_not_done(self):
        records = [_derived(i) for i in range(1, 25)]
        archive = _make_archive(records, initial_load_done=False)
        ms = await self._seed(archive)
        assert ms.archive_baseline is None

    @pytest.mark.asyncio
    async def test_guard_too_few_records(self):
        records = [_derived(i) for i in range(1, 15)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is None

    @pytest.mark.asyncio
    async def test_baseline_stats_correct(self):
        records = [_derived(i, duration_min=50, sqft=400.0) for i in range(1, 25)]
        archive = _make_archive(records)
        ms = await self._seed(archive)
        assert ms.archive_baseline is not None
        assert abs(ms.archive_baseline["duration_mean"] - 50.0) < 0.01


class TestSeedL5FromArchive:
    async def _seed(
        self,
        archive: MissionArchive,
        rps: RobotProfileStore | None = None,
        save_side_effect: Exception | None = None,
    ) -> tuple[RobotProfileStore, MagicMock]:
        """Helper: run seed function with mocked storage."""
        if rps is None:
            rps = _make_rps()
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock(side_effect=save_side_effect)
        store_mock.async_load = AsyncMock(return_value=None)

        from custom_components.roomba_plus.__init__ import _async_seed_l5_from_archive

        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_seed_l5_from_archive(hass, "entry", archive, rps)
        return rps, store_mock

    @pytest.mark.asyncio
    async def test_seeds_room_dirt_index(self):
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 150.0}}),
            _derived_v280_l5_arc(2, rooms={"19": {"passes": 2, "area": 150.0}}),
        ])
        rps, _ = await self._seed(archive)
        assert "19" in rps.room_dirt_index
        assert rps.room_dirt_index["19"] > 0

    @pytest.mark.asyncio
    async def test_ema_oldest_first_ordering(self):
        """Two passes in mission 2 (recent) should weight higher than 1 pass (older)."""
        rps_low = _make_rps()
        rps_high = _make_rps()
        area = 100.0 * SQFT_TO_M2  # convert: area in sqft in archive

        # Low: only 1 pass
        archive_low = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}}),
        ])
        # High: 1 pass then 3 passes (recent high-pass mission dominates)
        archive_high = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}}),
            _derived_v280_l5_arc(2, rooms={"19": {"passes": 3, "area": 100.0}}),
        ])

        from custom_components.roomba_plus.__init__ import _async_seed_l5_from_archive
        hass = _make_hass()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        store_mock.async_load = AsyncMock(return_value=None)
        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_seed_l5_from_archive(hass, "e", archive_low, rps_low)
            await _async_seed_l5_from_archive(hass, "e", archive_high, rps_high)

        assert rps_high.room_dirt_index["19"] > rps_low.room_dirt_index["19"]

    @pytest.mark.asyncio
    async def test_guard_already_seeded(self):
        """Skips when room_dirt_index already has data."""
        rps = _make_rps()
        rps.room_dirt_index["19"] = 1.5  # pre-populated

        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"21": {"passes": 2, "area": 100.0}}),
        ])
        _, store_mock = await self._seed(archive, rps)
        # 21 should NOT have been added (guard hit)
        assert "21" not in rps.room_dirt_index
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_initial_load_not_done(self):
        """Skips when archive initial load is not complete."""
        archive = _make_archive_v280_l5_arc(
            [_derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 100.0}})],
            initial_load_done=False,
        )
        rps, store_mock = await self._seed(archive)
        assert rps.room_dirt_index == {}
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_empty_archive(self):
        """Skips when archive has no records."""
        archive = MissionArchive()
        archive._initial_load_done = True
        rps, store_mock = await self._seed(archive)
        assert rps.room_dirt_index == {}
        store_mock.async_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_zero_area_rooms(self):
        """Rooms with area=0 must not be seeded (avoids division by zero)."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 2, "area": 0.0}}),
        ])
        rps, _ = await self._seed(archive)
        assert "19" not in rps.room_dirt_index

    @pytest.mark.asyncio
    async def test_saves_when_seeded(self):
        """Store is saved when at least one room was seeded."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={"19": {"passes": 1, "area": 150.0}}),
        ])
        _, store_mock = await self._seed(archive)
        store_mock.async_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_rooms(self):
        """All rooms from all missions in archive are seeded."""
        archive = _make_archive_v280_l5_arc([
            _derived_v280_l5_arc(1, rooms={
                "19": {"passes": 1, "area": 100.0},
                "21": {"passes": 2, "area": 200.0},
            }),
            _derived_v280_l5_arc(2, rooms={
                "19": {"passes": 1, "area": 100.0},
                "5":  {"passes": 1, "area": 80.0},
            }),
        ])
        rps, _ = await self._seed(archive)
        assert "19" in rps.room_dirt_index
        assert "21" in rps.room_dirt_index
        assert "5" in rps.room_dirt_index


class TestUpdateRobotProfileStoreMissionStats:
    """v2.10.2 bug-hunt fix: update_mission_stats() existed since at least
    v2.6.0 (docstring: "L3/L8 — called after each mission from the
    callback chain") but had no caller anywhere in the codebase — the
    mission_duration_mean/mission_area_mean baseline this feeds never
    populated in production regardless of how much MissionStore history
    existed. _async_update_robot_profile_store() is that callback chain
    (L5/L6/J already lived there); these tests cover the new L3/L8 block."""

    @staticmethod
    def _make_entry(entry_id: str = "e"):
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.runtime_data.grid_store = None  # skip L6 cleanly
        # Real dict (not a bare MagicMock) for the J section's
        # roomba_reported_state() lookup chain — a bare MagicMock's
        # auto-configured __float__ would otherwise make float(_sqft)
        # silently succeed with a fake value instead of hitting the
        # "no sqft reading" None path this test class wants.
        entry.runtime_data.roomba.master_state = {"state": {"reported": {}}}
        return entry

    @staticmethod
    def _ms_with_records(n: int, duration_min: int = 45, area_sqft: float = 300.0):
        # v3.2.0 bug-hunt fix: was hardcoded to "2026-06-01T07:00:00+00:00",
        # a fixed absolute date. query(days=30)'s cutoff is relative to
        # wall-clock "now" — once real time passed the point where that
        # fixed date fell outside the 30-day window, this test started
        # failing not because of a code bug, but because the fixture
        # itself had quietly gone stale. Use a relative offset instead so
        # this can't happen again regardless of when the suite runs.
        from homeassistant.util import dt as dt_util
        import datetime
        started = dt_util.now() - datetime.timedelta(days=5)
        ended = started + datetime.timedelta(minutes=30)
        ms = _make_ms()
        ms._records = [
            {
                "id": f"m_{i}",
                "started_at": started.isoformat(),
                "ended_at": ended.isoformat(),
                "duration_min": duration_min,
                "area_sqft": area_sqft,
                "result": "completed",
            }
            for i in range(n)
        ]
        return ms

    async def _run(self, ms, rps=None):
        from custom_components.roomba_plus.callbacks import _async_update_robot_profile_store
        if rps is None:
            rps = _make_rps()
        hass = _make_hass()
        entry = self._make_entry()
        store_mock = MagicMock()
        store_mock.async_save = AsyncMock()
        with patch("custom_components.roomba_plus.robot_profile_store.Store",
                   return_value=store_mock):
            await _async_update_robot_profile_store(hass, entry, ms, rps)
        return rps, store_mock

    @pytest.mark.asyncio
    async def test_populates_mission_duration_and_area_mean(self):
        ms = self._ms_with_records(5, duration_min=40, area_sqft=250.0)
        rps, _ = await self._run(ms)
        assert rps.mission_duration_mean == pytest.approx(40.0)
        assert rps.mission_area_mean == pytest.approx(250.0)

    @pytest.mark.asyncio
    async def test_below_minimum_records_does_not_populate(self):
        """Fewer than 5 qualifying records (the threshold inside
        update_mission_stats itself) must leave the means untouched —
        confirms this test isn't passing merely because any non-empty
        query() result trivially satisfies the assertion."""
        ms = self._ms_with_records(4)
        rps, _ = await self._run(ms)
        assert rps.mission_duration_mean is None
        assert rps.mission_area_mean is None

    @pytest.mark.asyncio
    async def test_saves_when_mission_stats_populated(self):
        ms = self._ms_with_records(5)
        _, store_mock = await self._run(ms)
        store_mock.async_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_save_when_nothing_changed(self):
        """Empty MissionStore + no GridStore + a roomba mock that yields no
        sqft reading: none of L3/L8, L5, L6, or J should report a change,
        so the store must not be saved at all."""
        ms = _make_ms()
        rps, store_mock = await self._run(ms)
        store_mock.async_save.assert_not_called()


class TestCleanupRemovedRepairsDispatched:
    """v3.5.0 Repairs redesign bug-hunt fix — source guard for
    async_cleanup_removed_repairs's dispatch.

    _phase_finalize is too heavy to call directly in a unit test (real
    platform forwarding, HTTP view registration, service registration) —
    same reasoning as TestMqttStampCallbackRegisteredBeforePlatforms above.
    This guards against the exact bug class this project has already found
    twice before (v3.2.0 bug-hunt: repair checks built, tested standalone,
    but never actually wired into anything the running integration calls):
    confirms the cleanup call is actually present in _phase_finalize's
    source, not just that async_cleanup_removed_repairs itself works when
    called directly (TestCleanupRemovedRepairs in test_repairs.py already
    covers that).
    """

    def test_cleanup_dispatched_from_phase_finalize(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "__init__.py").read_text()
        finalize_idx = src.find("async def _phase_finalize")
        assert finalize_idx != -1, "_phase_finalize not found in __init__.py"

        # Slice to just this function's body (up to the next top-level def)
        next_def_idx = src.find("\nasync def ", finalize_idx + 1)
        if next_def_idx == -1:
            next_def_idx = src.find("\ndef ", finalize_idx + 1)
        body = src[finalize_idx:next_def_idx if next_def_idx != -1 else None]

        assert "async_cleanup_removed_repairs" in body, (
            "_phase_finalize no longer dispatches "
            "async_cleanup_removed_repairs — users upgrading from a "
            "pre-v3.5.0 install would be left with permanently stuck "
            "stale Repair Issues (see async_cleanup_removed_repairs's "
            "own docstring in repairs.py for the full rationale)"
        )
        # Scheduling, without pinning WHO schedules it. This read
        # "hass.async_create_task" until the task moved to the config
        # entry (so it is cancelled on unload and its exceptions are
        # logged) — a change with no bearing on what this test is for.
        assert "async_create_task" in body


class TestMqttStampCallbackRegisteredBeforePlatforms:
    """v3.2.1 — source-order guard for the watchdog race fix.

    The entire point of make_mqtt_stamp_callback is that it runs BEFORE
    any entity's on_message callback; roombapy invokes callbacks in
    registration order, and entities register theirs during platform
    setup.  If a future refactor moves the registration below
    async_forward_entry_setups, the false-"Problem"-blip race silently
    returns with every test still green — hence this explicit guard on
    the source order itself (same style as test_locale_slug_guard).
    """

    def test_stamp_registration_precedes_platform_forwarding(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "__init__.py").read_text()
        stamp_idx = src.find("make_mqtt_stamp_callback(config_entry)")
        forward_idx = src.find(
            "await hass.config_entries.async_forward_entry_setups"
        )
        assert stamp_idx != -1, "stamp callback registration missing from __init__.py"
        assert forward_idx != -1
        assert stamp_idx < forward_idx, (
            "make_mqtt_stamp_callback must be registered BEFORE "
            "async_forward_entry_setups — entities register their "
            "on_message callbacks during platform setup, and roombapy "
            "calls callbacks in registration order (v3.2.1 watchdog fix)"
        )


class TestOutlineSyncRecomputePrecedesFreezeSnapshot:
    """v3.2.1 FIELD FIX — source-order guard, same style as
    TestMqttStampCallbackRegisteredBeforePlatforms above.

    outline_store.recompute_sync() must run BEFORE the room-seg recompute
    block (which contains the FreezeSnapshotStore trigger) in image.py's
    mission-end handler — otherwise the freeze snapshot reads a stale
    (previous-mission, or on the very first-ever snapshot, empty)
    contour. Field-confirmed: the first FreezeSnapshotStore snapshot
    captured outline_points=0 for exactly this ordering reason. A future
    refactor that moves recompute_sync() below the room-seg block would
    silently reintroduce the bug with every test still green — hence
    this explicit guard on the source order itself.
    """

    def test_recompute_sync_call_precedes_room_seg_block(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        sync_idx = src.find("_gdata.outline_store.recompute_sync(")
        room_seg_idx = src.find("_gdata.room_seg_store.maybe_recompute(")
        assert sync_idx != -1, "outline_store.recompute_sync() call missing from image.py"
        assert room_seg_idx != -1
        assert sync_idx < room_seg_idx, (
            "outline_store.recompute_sync() must be called BEFORE the "
            "room-seg recompute block — the FreezeSnapshotStore trigger "
            "inside that block reads outline_store.contour_points and "
            "must see THIS mission's fresh value, not a stale one "
            "(v3.2.1 field-confirmed: first-ever snapshot had "
            "outline_points=0 because of this exact ordering bug)"
        )

    def test_later_outline_call_site_saves_not_recomputes(self):
        """The second (later) outline call site must call async_save(),
        NOT async_recompute() — calling the latter there too would
        silently double-increment OutlineStore.mission_count for the
        same mission (recompute_sync() already ran once, earlier)."""
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        sync_idx = src.find("_gdata.outline_store.recompute_sync(")
        # Search for the LATER outline_store call site, after the sync one.
        later_section = src[sync_idx + 1:]
        assert "_gdata.outline_store.async_save(" in later_section, (
            "expected a later outline_store.async_save() call for "
            "persistence after the synchronous recompute_sync() above"
        )
        assert "_gdata.outline_store.async_recompute(" not in later_section, (
            "a second async_recompute() call for the same mission would "
            "double-increment mission_count on top of the recompute_sync() "
            "call already made earlier in the same mission"
        )


class TestDockContactConfirmedPrecedesMissionEnd:
    """v3.2.1 DOCK-ANCHOR — source-order guard, same style as
    TestOutlineSyncRecomputePrecedesFreezeSnapshot above.

    _handle_dock_contact_confirmed() MUST be called before
    _handle_mission_end() in image.py's message handler — a real bug,
    caught before shipping: _handle_mission_end() feeds GridStore/
    RoomSegStore/OutlineStore via grid_store.update_from_mission(
    self._mission_points, ...) as a SINGLE, one-shot call. If the
    dock-anchor correction ran after that call (as it did in the first
    version of this feature), the most important case this mechanism
    exists for — a stuck-buffered segment resolving exactly at the
    mission's final dock contact — would have its correction reach only
    the live map, never the stores, for that mission's contribution. A
    future refactor that moves the call order back would silently
    reintroduce this with every other test still green — hence this
    explicit guard on the source order itself.
    """

    def test_dock_contact_confirmed_call_precedes_mission_end_call(self):
        from pathlib import Path
        import custom_components.roomba_plus as pkg

        src = (Path(pkg.__file__).parent / "image.py").read_text()
        dock_idx = src.find("self._handle_dock_contact_confirmed()")
        end_idx = src.find("self._handle_mission_end(current_phase)")
        assert dock_idx != -1, "_handle_dock_contact_confirmed() call missing from image.py"
        assert end_idx != -1
        assert dock_idx < end_idx, (
            "_handle_dock_contact_confirmed() must be called BEFORE "
            "_handle_mission_end() — the latter feeds GridStore/RoomSeg/"
            "Outline via a single one-shot grid_store.update_from_mission() "
            "call and must see the ALREADY-corrected _mission_points, not "
            "a stale pre-correction version (v3.2.1 field-confirmed: this "
            "exact ordering bug was caught before shipping)"
        )


class TestOptionalPlatforms:
    """NEW (this session) -- CONF_ENABLE_SCHEDULE_CALENDAR opt-out for
    Platform.CALENDAR, default True so existing installations keep
    their calendar entity after upgrading (see that constant's own
    docstring, const.py, for why default True specifically)."""

    def test_returns_calendar_platform_by_default(self):
        from custom_components.roomba_plus import _optional_platforms
        from homeassistant.const import Platform

        config_entry = MagicMock()
        config_entry.options = {}

        assert _optional_platforms(config_entry) == [Platform.CALENDAR]

    def test_returns_empty_when_explicitly_disabled(self):
        from custom_components.roomba_plus import _optional_platforms
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}

        assert _optional_platforms(config_entry) == []

    def test_returns_calendar_platform_when_explicitly_enabled(self):
        from custom_components.roomba_plus import _optional_platforms
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR
        from homeassistant.const import Platform

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: True}

        assert _optional_platforms(config_entry) == [Platform.CALENDAR]


class TestRemoveCalendarEntityIfDisabled:
    """NEW (this session) -- explicit entity-registry cleanup so a
    disabled calendar entity doesn't linger forever as "unavailable"
    (unloading a platform doesn't remove its registry record on its
    own)."""

    def test_removes_calendar_entity_when_disabled(self):
        from custom_components.roomba_plus import _remove_calendar_entity_if_disabled
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"

        calendar_entry = MagicMock(domain="calendar", entity_id="calendar.roomba_schedule")
        sensor_entry = MagicMock(domain="sensor", entity_id="sensor.roomba_battery")
        fake_er = MagicMock()

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er),\
             patch(
                 "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                 return_value=[calendar_entry, sensor_entry],
             ):
            _remove_calendar_entity_if_disabled(MagicMock(), config_entry)

        fake_er.async_remove.assert_called_once_with("calendar.roomba_schedule")

    def test_does_nothing_when_enabled(self):
        """Default (no option set at all) must NOT remove anything --
        the whole point of defaulting to True is that existing
        installations are left untouched."""
        from custom_components.roomba_plus import _remove_calendar_entity_if_disabled

        config_entry = MagicMock()
        config_entry.options = {}
        config_entry.entry_id = "entry1"

        fake_er = MagicMock()
        with patch("homeassistant.helpers.entity_registry.async_get", return_value=fake_er),\
             patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry") as mock_entries:
            _remove_calendar_entity_if_disabled(MagicMock(), config_entry)

        mock_entries.assert_not_called()
        fake_er.async_remove.assert_not_called()


class TestReloadOnOptionsChangeIncludesCalendar:
    """NEW (this session) -- CONF_ENABLE_SCHEDULE_CALENDAR added to the
    reload-trigger key set (renamed _CONNECTION_KEYS ->
    _RELOAD_TRIGGER_KEYS internally). Without this, saving the option
    would silently do nothing until the user manually reloaded the
    integration -- unlike every other option on this form, which is
    read fresh at render/runtime rather than baked into the platforms
    list at setup time."""

    @pytest.mark.asyncio
    async def test_reload_triggered_when_calendar_option_changes(self):
        from custom_components.roomba_plus import _async_reload_on_options_change
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.data = {}  # never synced before -- an existing installation
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_awaited_once_with("entry1")

    @pytest.mark.asyncio
    async def test_no_reload_when_calendar_option_matches_default_and_was_never_touched(self):
        """The default-application fix (this session): before it, an
        existing installation's very first options save of ANYTHING
        (even something unrelated) would spuriously reload once, since
        .data has no key at all yet (reads as None) while .options
        would already resolve to the real default (True) -- None !=
        True looks like a change even though nothing meaningful did."""
        from custom_components.roomba_plus import _async_reload_on_options_change

        config_entry = MagicMock()
        config_entry.data = {}
        config_entry.options = {}  # both resolve to the same default -- no real change
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_reload_after_sync(self):
        from custom_components.roomba_plus import _async_reload_on_options_change
        from custom_components.roomba_plus.const import CONF_ENABLE_SCHEDULE_CALENDAR

        config_entry = MagicMock()
        config_entry.data = {CONF_ENABLE_SCHEDULE_CALENDAR: False}  # already synced
        config_entry.options = {CONF_ENABLE_SCHEDULE_CALENDAR: False}
        config_entry.entry_id = "entry1"
        hass = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        await _async_reload_on_options_change(hass, config_entry)

        hass.config_entries.async_reload.assert_not_awaited()


# ── formerly tests/test_setup_phases.py ───────────────────────────────
#
# The setup phase chain, exercised against a real hass.
#
# WHY THIS FILE EXISTS. `__init__.py` sits at 41% coverage — the lowest in
# the integration, and it is the code every user runs on every start. The
# gap was known and reasoned, not overlooked: `test_const.py` records that
# `_phase_spatial` needs a real hass, config entry and HA storage because
# `GeometryStore.async_load` reaches for them, and that no test had built
# that scaffolding. The boolean conditions were tested in isolation
# instead.
#
# This is that scaffolding. `pytest-homeassistant-custom-component`
# provides a working `hass` with storage, so the phases can be driven
# rather than read.
#
# WHAT IT DELIBERATELY DOES NOT DO. It does not drive `_phase_connect`,
# which talks to a robot, nor `_phase_cloud`, which talks to iRobot. Those
# need their own doubles and belong in their own file. What is here is the
# part that decides which stores and capabilities an entry ends up with —
# the Classic/Prime split that `ARCHITECTURE.md` calls the load-bearing
# decision, and which until now nothing executed.

def _entry(**options: Any) -> MagicMock:
    # entry_mock(), not a bare MagicMock: the phases schedule background
    # work through `config_entry.async_create_task`, and a bare mock
    # accepts the coroutine and drops it unawaited — which this suite
    # treats as an error, correctly.
    from tests.conftest import entry_mock

    entry = entry_mock()
    entry.entry_id = "phase_test_entry"
    entry.title = "Test Robot"
    entry.options = dict(options)
    entry.data = {"blid": "BLID_TEST"}
    entry.runtime_data = MagicMock()
    return entry


def _ctx(hass: Any, state: dict[str, Any], **options: Any) -> _SetupContext:
    return _SetupContext(hass=hass, config_entry=_entry(**options), state=state)


#: A 900-series robot: reports pose, no Smart Map. The generation whose
#: spatial stores are derived entirely from the position stream.
_STATE_POSE_ONLY = {
    "cap": {"pose": 1},
    "pose": {"point": {"x": 0, "y": 0}, "theta": 0},
}


#: An i-series robot with a Smart Map and no pose capability — the shape
#: that motivated the has_pose-OR-has_smart_map gate (mdarocha, i3+).
_STATE_SMART_MAP_NO_POSE = {
    "cap": {"pmaps": 1},
    "pmaps": [{"abc123": "v1"}],
    "lastCommand": {"pmap_id": "abc123"},
}


#: A 600-series robot: neither.
_STATE_NEITHER: dict[str, Any] = {"cap": {}}


def _ctx_for_finalize(hass: Any) -> _SetupContext:
    """A context populated as phases 1-4 would leave it."""
    ctx = _ctx(hass, _STATE_POSE_ONLY)
    ctx.roomba = MagicMock()
    ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
    ctx.map_capability = MapCapability.EPHEMERAL
    ctx.cloud_coordinator = None
    ctx.mission_archive = MagicMock()
    ctx.presence_manager = None
    ctx.grid_store = MagicMock()
    return ctx


class TestPhaseSpatialDecidesMapCapability:
    """The gate is `(has_pose or has_smart_map) and map_enabled`.

    It used to be `has_pose and map_enabled`, which skipped Smart Map
    robots whose `cap` omits "pose" — and with it the cloud coordinator,
    which is gated on map_capability != NONE. Valid cloud credentials
    sat unused, there was no map, and total cleaned area fell back to
    the known-unreliable bbrun.sqft.
    """

    @pytest.mark.asyncio
    async def test_a_pose_robot_gets_a_map_capability(self, hass: Any) -> None:
        ctx = _ctx(hass, _STATE_POSE_ONLY)
        await _phase_spatial(ctx)
        assert ctx.map_capability is not MapCapability.NONE

    @pytest.mark.asyncio
    async def test_a_smart_map_robot_without_pose_also_does(
        self, hass: Any
    ) -> None:
        """The regression this gate exists for."""
        ctx = _ctx(hass, _STATE_SMART_MAP_NO_POSE)
        await _phase_spatial(ctx)
        assert ctx.map_capability is not MapCapability.NONE

    @pytest.mark.asyncio
    async def test_a_robot_with_neither_gets_none(self, hass: Any) -> None:
        ctx = _ctx(hass, _STATE_NEITHER)
        await _phase_spatial(ctx)
        assert ctx.map_capability is MapCapability.NONE

    @pytest.mark.asyncio
    async def test_the_user_can_turn_it_off(self, hass: Any) -> None:
        """map_enabled is an opt-out; a robot that could map must not
        when the user said no."""
        ctx = _ctx(hass, _STATE_POSE_ONLY, map_enabled=False)
        await _phase_spatial(ctx)
        assert ctx.map_capability is MapCapability.NONE


class TestPhaseSpatialWiresTheStoresItPromises:
    """Its own docstring says it populates map_capability, renderer,
    geometry_store, grid_store and room_seg_store. A store created but
    never assigned to the context is a feature that does nothing — the
    exact shape `check_never_filled.py` exists for, four instances of it
    in one day.
    """

    @pytest.mark.asyncio
    async def test_a_pose_robot_gets_the_pose_derived_stores(
        self, hass: Any
    ) -> None:
        ctx = _ctx(hass, _STATE_POSE_ONLY)
        await _phase_spatial(ctx)

        assert ctx.geometry_store is not None
        assert ctx.grid_store is not None

    @pytest.mark.asyncio
    async def test_a_robot_without_a_map_gets_no_stores(
        self, hass: Any
    ) -> None:
        """The other half: stores for a capability the robot lacks are
        files written for nothing."""
        ctx = _ctx(hass, _STATE_NEITHER)
        await _phase_spatial(ctx)

        assert ctx.geometry_store is None
        assert ctx.grid_store is None
        assert ctx.renderer is None


class TestPhaseDataLoadsTheStoresItPromises:
    """Phase 3 populates maintenance_store, mission_store and
    mission_archive. All three are among the four whose contents cannot
    be rebuilt from anywhere else, so a phase that quietly failed to
    attach one would lose a user's maintenance dates or mission history
    on the next save — with nothing in the log.
    """

    @pytest.mark.asyncio
    async def test_the_unrecoverable_stores_are_attached(
        self, hass: Any
    ) -> None:
        from custom_components.roomba_plus import _phase_data

        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}

        await _phase_data(ctx)

        assert ctx.maintenance_store is not None
        assert ctx.mission_store is not None

    @pytest.mark.asyncio
    async def test_it_starts_clean_on_a_fresh_install(self, hass: Any) -> None:
        """No persisted file yet: the stores load empty rather than
        raising, which is what a first start actually looks like."""
        from custom_components.roomba_plus import _phase_data

        ctx = _ctx(hass, _STATE_NEITHER)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_NEITHER}}

        await _phase_data(ctx)

        assert ctx.last_error_code is None
        assert ctx.mission_store is not None
        assert ctx.mission_store.records == []


class TestBuildRuntimeDataCarriesEveryPhaseResult:
    """`_build_runtime_data` is the seam where the phases' work becomes
    the object every entity reads.

    A field the phases populated on the context but which this function
    forgets to pass on is invisible: no error, no log line, and the
    feature that depends on it simply never works. `check_never_filled.py`
    exists because that shape hit this project four times in one day —
    `cleaned_in_room`, `_zone_polygons`, `prime_map_versions`,
    `last_known_map_id`.

    Rather than list fields by hand (a list that rots), this walks the
    context: every attribute the phases set to something non-None must
    arrive on RoombaData under the same name.
    """

    @pytest.mark.asyncio
    async def test_no_populated_context_field_is_dropped(
        self, hass: Any
    ) -> None:
        import dataclasses

        from custom_components.roomba_plus import _build_runtime_data, _phase_data

        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
        await _phase_spatial(ctx)
        await _phase_data(ctx)

        data = _build_runtime_data(ctx)
        ziel = {f.name for f in dataclasses.fields(data)}

        verloren = [
            name
            for name, value in vars(ctx).items()
            if value is not None
            and name in ziel
            and getattr(data, name, None) is None
        ]
        assert not verloren, (
            f"the phases populated {verloren} but RoombaData received "
            "None — the field is set on the context and dropped here, "
            "which is silent at runtime"
        )

    @pytest.mark.asyncio
    async def test_the_spatial_stores_reach_runtime_data(
        self, hass: Any
    ) -> None:
        """Named explicitly because these are the ones a Prime robot
        deliberately does NOT get — so a blanket check could not tell a
        deliberate omission from a dropped field."""
        from custom_components.roomba_plus import _build_runtime_data, _phase_data

        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
        await _phase_spatial(ctx)
        await _phase_data(ctx)

        data = _build_runtime_data(ctx)

        assert data.geometry_store is ctx.geometry_store
        assert data.maintenance_store is ctx.maintenance_store
        assert data.mission_store is ctx.mission_store


class TestPhaseCloudGatesOnCredentialsAndCapability:
    """Phase 4 creates the cloud coordinator and everything downstream of
    it — UMF aligner, outline store, trajectory store.

    THE GATE IS AN AND. Map capability AND a username AND a password.
    Getting it wrong in either direction is expensive: too strict and a
    user's valid credentials sit unused with no map and area figures
    falling back to the unreliable bbrun.sqft; too loose and setup tries
    to talk to iRobot for a robot that has nothing to fetch.
    """

    def _ctx_with(self, hass: Any, *, state: dict, creds: bool) -> _SetupContext:
        ctx = _ctx(hass, state)
        if creds:
            ctx.config_entry.data = {
                "blid": "BLID_TEST",
                "irobot_username": "user@example.invalid",
                "irobot_password": "secret",
            }
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": state}}
        return ctx

    @pytest.mark.asyncio
    async def test_no_credentials_means_no_coordinator(self, hass: Any) -> None:
        ctx = self._ctx_with(hass, state=_STATE_POSE_ONLY, creds=False)
        await _phase_spatial(ctx)

        from custom_components.roomba_plus import _phase_cloud

        await _phase_cloud(ctx)

        assert ctx.cloud_coordinator is None

    @pytest.mark.asyncio
    async def test_no_map_capability_means_no_coordinator(
        self, hass: Any
    ) -> None:
        """Credentials alone are not enough — there would be nothing to
        fetch for a robot that cannot map."""
        ctx = self._ctx_with(hass, state=_STATE_NEITHER, creds=True)
        await _phase_spatial(ctx)

        from custom_components.roomba_plus import _phase_cloud

        await _phase_cloud(ctx)

        assert ctx.cloud_coordinator is None

    @pytest.mark.asyncio
    async def test_pose_derived_stores_are_attached_either_way(
        self, hass: Any
    ) -> None:
        """Outline and trajectory come from the position stream, not the
        cloud. A robot with poses and no credentials must still get
        them — they are what the map is drawn from."""
        ctx = self._ctx_with(hass, state=_STATE_POSE_ONLY, creds=False)
        await _phase_spatial(ctx)

        from custom_components.roomba_plus import _phase_cloud

        await _phase_cloud(ctx)

        assert ctx.outline_store is not None
        assert ctx.trajectory_store is not None


class TestUnloadFlushesAndDisconnects:
    """`async_unload_entry` is where a reload either keeps a user's data
    or loses it.

    The delayed-save path is the reason: `Store.async_delay_save` holds
    a write for a few seconds, and unload cancels the timer. Without an
    explicit flush here, a mission record written just before a reload
    is simply gone. The comment in the source says so; nothing executed
    it until now.
    """

    def _entry_with_runtime(self, hass: Any) -> Any:
        from unittest.mock import AsyncMock

        entry = _entry()
        data = entry.runtime_data
        data.map_capability = MapCapability.NONE
        data.blocking_manager = MagicMock()
        data.presence_manager = MagicMock()
        data.mission_timer_store = MagicMock()
        data.mission_timer_store.async_save = AsyncMock()
        data.roomba = MagicMock()
        # disconnect() is awaited under an asyncio.timeout — a plain
        # MagicMock returns something unawaitable and the teardown dies
        # before it reaches the stores.
        data.roomba.disconnect = AsyncMock()
        data.prime_robot = None
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        return entry

    @pytest.mark.asyncio
    async def test_the_timer_store_is_flushed_before_the_entry_goes(
        self, hass: Any
    ) -> None:
        from custom_components.roomba_plus import async_unload_entry

        entry = self._entry_with_runtime(hass)

        await async_unload_entry(hass, entry)

        entry.runtime_data.mission_timer_store.async_save.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_blocking_queue_is_cancelled(self, hass: Any) -> None:
        """A queued smart_start that survives the unload would fire
        against a robot the entry no longer owns."""
        from custom_components.roomba_plus import async_unload_entry

        entry = self._entry_with_runtime(hass)

        await async_unload_entry(hass, entry)

        entry.runtime_data.blocking_manager.cancel_queue.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_refused_platform_unload_stops_the_teardown(
        self, hass: Any
    ) -> None:
        """If Home Assistant could not unload the platforms, the entities
        are still live — tearing down the stores under them would be
        worse than leaving the entry loaded."""
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import async_unload_entry

        entry = self._entry_with_runtime(hass)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(hass, entry)

        assert result is False
        entry.runtime_data.mission_timer_store.async_save.assert_not_awaited()


class TestPhaseFinalizeWiresTheRobotUp:
    """Phase 5 registers the MQTT callbacks, forwards the platforms and
    publishes the REST views.

    REGISTRATION ORDER IS LOad-BEARING here, and the source says so: the
    MQTT stamp callback must be registered BEFORE the platforms, so it
    runs before any entity's own on_message. Otherwise the staleness
    sensor evaluates the very message that ends a silence against a
    not-yet-refreshed timestamp — the false "Problem" blip at
    Zwischenladung resume.
    """

    def _ctx(self, hass: Any) -> Any:
        from unittest.mock import AsyncMock

        ctx = _ctx_for_finalize(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.http = MagicMock()
        return ctx

    @staticmethod
    def _teardown(ctx: Any) -> None:
        """Run what the entry collected through `async_on_unload`.

        The phase registers a 30-second interval that rechecks a stuck
        end state, and a real config entry cancels it on unload. A mock
        entry just records the callback, so without this the timer
        outlives the test and HA's harness fails it as lingering —
        correctly: a timer nobody cancels is a timer that fires against
        a torn-down entry.
        """
        for aufruf in ctx.config_entry.async_on_unload.call_args_list:
            if aufruf.args and callable(aufruf.args[0]):
                aufruf.args[0]()

    @pytest.mark.asyncio
    async def test_the_platforms_are_forwarded(self, hass: Any) -> None:
        from custom_components.roomba_plus import _phase_finalize

        ctx = self._ctx(hass)
        await _phase_finalize(ctx)
        self._teardown(ctx)

        hass.config_entries.async_forward_entry_setups.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_mqtt_stamp_callback_is_registered_before_them(
        self, hass: Any
    ) -> None:
        """The order guard. Recorded as a source-order comment in
        __init__.py since v3.2.1; nothing executed it."""
        from custom_components.roomba_plus import _phase_finalize

        reihenfolge: list[str] = []
        ctx = self._ctx(hass)
        ctx.roomba.register_on_message_callback = (
            lambda *_a, **_k: reihenfolge.append("callback")
        )
        hass.config_entries.async_forward_entry_setups.side_effect = (
            lambda *_a, **_k: reihenfolge.append("platforms")
        )

        await _phase_finalize(ctx)
        self._teardown(ctx)

        assert "callback" in reihenfolge and "platforms" in reihenfolge
        assert reihenfolge.index("callback") < reihenfolge.index("platforms")

    @pytest.mark.asyncio
    async def test_the_rest_views_are_published(self, hass: Any) -> None:
        """The Lovelace card reads these; an entry that sets up without
        them looks healthy and serves nothing."""
        from custom_components.roomba_plus import _phase_finalize

        ctx = self._ctx(hass)
        await _phase_finalize(ctx)
        self._teardown(ctx)

        assert hass.http.register_view.called


class TestPhaseConnectFailureIsRetryable:
    """Phase 1 opens the local connection.

    WHAT MATTERS HERE IS THE FAILURE, not the success. A robot that is
    off, asleep or on a changed IP must raise `ConfigEntryNotReady` —
    Home Assistant then retries with backoff and the entry recovers by
    itself. Any other exception, or a silent return, leaves the entry
    permanently broken and the user restarting Home Assistant to fix
    something that would have fixed itself.
    """

    def _ctx(self, hass: Any) -> _SetupContext:
        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.config_entry.data = {
            "blid": "BLID_TEST",
            "password": "pw",
            "host": "10.0.0.2",
        }
        # The phase migrates options out of data for a fresh entry, and
        # `async_update_entry` refuses an entry hass does not know. The
        # mock entry is deliberate here — registering a real one would
        # drag in the whole entry lifecycle for a test about the
        # connection's failure mode.
        hass.config_entries.async_update_entry = MagicMock()
        return ctx

    @pytest.mark.asyncio
    async def test_a_connection_error_is_retryable(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CannotConnect becomes ConfigEntryNotReady — HA retries with
        backoff and the entry recovers on its own when the robot comes
        back. Anything else would leave it permanently broken."""
        from unittest.mock import AsyncMock

        from homeassistant import exceptions

        import custom_components.roomba_plus as pkg
        from custom_components.roomba_plus import CannotConnect

        monkeypatch.setattr(pkg, "RoombaClient", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr(
            pkg,
            "async_connect_or_timeout",
            AsyncMock(side_effect=CannotConnect("no route")),
        )

        with pytest.raises(exceptions.ConfigEntryNotReady):
            await pkg._phase_connect(self._ctx(hass))

    @pytest.mark.asyncio
    async def test_a_refused_connection_returns_rather_than_raising(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A falsy result is NOT an error path: the helper already
        logged, and the phase returns so setup can decide. Pinned
        because the two outcomes look interchangeable and are not."""
        from unittest.mock import AsyncMock

        import custom_components.roomba_plus as pkg

        monkeypatch.setattr(pkg, "RoombaClient", lambda *_a, **_kw: MagicMock())
        monkeypatch.setattr(
            pkg, "async_connect_or_timeout", AsyncMock(return_value=False)
        )

        ergebnis = await pkg._phase_connect(self._ctx(hass))

        assert ergebnis is False

    @pytest.mark.asyncio
    async def test_a_successful_connection_attaches_the_client(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        import custom_components.roomba_plus as pkg

        from unittest.mock import AsyncMock as _AsyncMock

        client = MagicMock()
        client.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
        # The phase registers a shutdown listener that disconnects; the
        # HA test harness runs it when it stops the loop.
        client.disconnect = _AsyncMock()
        monkeypatch.setattr(pkg, "RoombaClient", lambda *_a, **_kw: client)
        monkeypatch.setattr(
            pkg, "async_connect_or_timeout", AsyncMock(return_value=True)
        )

        ctx = self._ctx(hass)
        await pkg._phase_connect(ctx)

        assert ctx.roomba is client


class TestPhaseCloudWithCredentials:
    """The other half of phase 4: what happens when the gate opens.

    The coordinator's first refresh is a network call, so it is stubbed.
    What is NOT stubbed is everything the phase does with the result —
    seeding the pmap id from local state, handing raw records to the
    mission store, attaching the UMF aligner. Those are the steps that
    turn a successful login into a map, and each of them was reachable
    only through a real cloud until now.
    """

    def _ctx(self, hass: Any) -> _SetupContext:
        ctx = _ctx(hass, _STATE_POSE_ONLY)
        ctx.config_entry.data = {
            "blid": "BLID_TEST",
            "irobot_username": "user@example.invalid",
            "irobot_password": "secret",
        }
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": _STATE_POSE_ONLY}}
        return ctx

    def _coordinator(self, *, raw_records=None):
        from unittest.mock import AsyncMock

        coordinator = MagicMock()
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.data = {"pmaps": [{"abc": "v1"}]}
        coordinator.raw_records = raw_records or []
        coordinator.regions = []
        coordinator.last_update_success = True
        return coordinator

    @pytest.mark.asyncio
    async def test_valid_credentials_attach_a_coordinator(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import custom_components.roomba_plus as pkg

        coordinator = self._coordinator()
        monkeypatch.setattr(
            pkg, "IrobotCloudCoordinator", lambda *_a, **_kw: coordinator
        )

        ctx = self._ctx(hass)
        await _phase_spatial(ctx)
        await pkg._phase_cloud(ctx)

        assert ctx.cloud_coordinator is coordinator

    @pytest.mark.asyncio
    async def test_the_local_pmap_id_seeds_the_coordinator(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The robot knows which map it is on before the cloud answers;
        seeding avoids a window where the two disagree."""
        import custom_components.roomba_plus as pkg

        coordinator = self._coordinator()
        monkeypatch.setattr(
            pkg, "IrobotCloudCoordinator", lambda *_a, **_kw: coordinator
        )

        ctx = self._ctx(hass)
        await _phase_spatial(ctx)
        await pkg._phase_cloud(ctx)

        coordinator.seed_pmap_id_from_local.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_failing_first_refresh_does_not_break_setup(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cloud enrichment is not a dependency. An outage at setup time
        must leave a working local integration behind, not a robot that
        refuses to load."""
        from unittest.mock import AsyncMock

        import custom_components.roomba_plus as pkg

        coordinator = self._coordinator()
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=OSError("cloud unreachable")
        )
        monkeypatch.setattr(
            pkg, "IrobotCloudCoordinator", lambda *_a, **_kw: coordinator
        )

        ctx = self._ctx(hass)
        await _phase_spatial(ctx)
        await pkg._phase_cloud(ctx)

        assert ctx.outline_store is not None


class TestSetupEntryOrchestration:
    """`async_setup_entry` decides which chain runs and stops the right
    way when a phase refuses.

    THE GENERATION SPLIT IS THE FIRST DECISION. A Prime entry must never
    enter the Classic phase chain — it has no local connection to make,
    and `_phase_connect` would raise ConfigEntryNotReady forever against
    a robot that is perfectly reachable through the cloud.
    """

    @pytest.mark.asyncio
    async def test_a_prime_entry_takes_the_prime_path(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import AsyncMock

        import custom_components.roomba_plus as pkg
        from custom_components.roomba_plus.models import ConnectionType

        prime = AsyncMock(return_value=True)
        klassisch = AsyncMock(return_value=True)
        monkeypatch.setattr(pkg, "_async_setup_entry_prime", prime)
        monkeypatch.setattr(pkg, "_phase_connect", klassisch)
        monkeypatch.setattr(
            pkg, "_connection_type", lambda _e: ConnectionType.CLOUD_ONLY
        )

        await pkg.async_setup_entry(hass, _entry())

        prime.assert_awaited_once()
        klassisch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_connection_stops_the_chain(
        self, hass: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_phase_connect` returning False means the later phases must
        not run — they would build stores around a robot that is not
        there and leave the entry half-set-up."""
        from unittest.mock import AsyncMock

        import custom_components.roomba_plus as pkg
        from custom_components.roomba_plus.models import ConnectionType

        spatial = AsyncMock()
        monkeypatch.setattr(
            pkg, "_phase_connect", AsyncMock(return_value=False)
        )
        monkeypatch.setattr(pkg, "_phase_spatial", spatial)
        monkeypatch.setattr(
            pkg, "_connection_type", lambda _e: ConnectionType.LOCAL_PUSH
        )

        result = await pkg.async_setup_entry(hass, _entry())

        assert result is False
        spatial.assert_not_awaited()


class TestPhaseDataSeedsAndWiresManagers:
    """Two branches of phase 3 that only run for particular installs.

    THE SEED IS FOR A ROBOT THAT ALREADY HAS HOURS ON IT. Installing
    Roomba+ on a robot used for months through the official app would
    otherwise treat its entire prior lifetime as overdue maintenance —
    "0h remaining" the moment the integration is added. The seed says
    "assume current as of now" instead, and must NOT fire again on later
    restarts or it would erase a real reset the user performed.
    """

    def _ctx(self, hass: Any, *, hours: int = 0, **options: Any) -> _SetupContext:
        state = dict(_STATE_POSE_ONLY)
        if hours:
            state["bbrun"] = {"hr": hours}
        ctx = _ctx(hass, state, **options)
        ctx.roomba = MagicMock()
        ctx.roomba.master_state = {"state": {"reported": state}}
        return ctx

    @pytest.mark.asyncio
    async def test_a_used_robot_starts_with_its_counters_assumed_current(
        self, hass: Any
    ) -> None:
        from custom_components.roomba_plus import _phase_data

        ctx = self._ctx(hass, hours=400)
        await _phase_data(ctx)

        store = ctx.maintenance_store
        assert store.filter_reset_hr == 400
        assert store.filter_baseline_seeded is True

    @pytest.mark.asyncio
    async def test_a_brand_new_robot_is_not_seeded(self, hass: Any) -> None:
        """Zero hours means nothing to assume."""
        from custom_components.roomba_plus import _phase_data

        ctx = self._ctx(hass, hours=0)
        await _phase_data(ctx)

        assert ctx.maintenance_store.filter_baseline_seeded is False

    @pytest.mark.asyncio
    async def test_the_blocking_manager_is_only_built_when_configured(
        self, hass: Any
    ) -> None:
        from custom_components.roomba_plus import _phase_data

        ohne = self._ctx(hass)
        await _phase_data(ohne)
        assert ohne.blocking_manager is None

        mit = self._ctx(hass, blocking_sensors=["binary_sensor.door"])
        await _phase_data(mit)
        assert mit.blocking_manager is not None

    @pytest.mark.asyncio
    async def test_the_presence_manager_is_only_built_when_enabled(
        self, hass: Any
    ) -> None:
        from custom_components.roomba_plus import _phase_data

        ohne = self._ctx(hass)
        await _phase_data(ohne)
        assert ohne.presence_manager is None

        mit = self._ctx(hass, presence_scheduling_enabled=True)
        await _phase_data(mit)
        assert mit.presence_manager is not None


class TestRobotProfileArrivesLate:
    """`_set_robot_profile_on_sku` fills in the robot's profile when the
    SKU shows up after setup.

    A robot that was asleep during setup reports its SKU on the first
    real message, not before. Without this callback the profile stays
    empty for the whole session — and with it the battery capacity every
    self-calibrating feature uses as its prior.

    It must also stop once filled: re-resolving on every message would
    overwrite a profile the user's own history has since refined.
    """

    def _callback(self, hass: Any):
        from unittest.mock import AsyncMock

        ctx = _ctx_for_finalize(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.http = MagicMock()
        ctx.config_entry.runtime_data.robot_profile = None

        registriert: list = []
        ctx.roomba.register_on_message_callback = (
            lambda fn, *_a, **_k: registriert.append(fn)
        )
        return ctx, registriert

    @pytest.mark.asyncio
    async def test_a_late_sku_fills_the_profile(self, hass: Any) -> None:
        from custom_components.roomba_plus import _phase_finalize

        ctx, registriert = self._callback(hass)
        ctx.roomba.master_state = {"state": {"reported": {}}}
        await _phase_finalize(ctx)
        for aufruf in ctx.config_entry.async_on_unload.call_args_list:
            if aufruf.args and callable(aufruf.args[0]):
                aufruf.args[0]()

        ctx.roomba.master_state = {
            "state": {"reported": {"sku": "i755840", "batteryType": "lith"}}
        }
        for fn in registriert:
            fn({"state": {"reported": {"sku": "i755840"}}})

        assert ctx.config_entry.runtime_data.robot_profile is not None
