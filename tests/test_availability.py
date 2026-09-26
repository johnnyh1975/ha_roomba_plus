"""Live state follows the local connection (@mdarocha).

A Classic robot ran its battery flat and dropped off the network. The
Connected sensor went off; everything else kept reporting "cleaning,
ready, 99 %, battery 1 %" for three and a half hours, because no entity
ever tied its availability to the connection.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.roomba_plus import availability as av
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

BLID = "BLID_AVAIL"


class _Client:
    """Just enough of roombapy's RoombaClient: the callback and the flag."""

    def __init__(self) -> None:
        self.connected = True
        self._cb: Any = None
        self.unsubscribed = False

    def register_on_connection_state_callback(self, cb):
        self._cb = cb

        def _unsub() -> None:
            self.unsubscribed = True

        return _unsub

    def drop(self) -> None:
        self.connected = False
        self._cb("disconnected", "battery")

    def restore(self) -> None:
        self.connected = True
        self._cb("connected", None)


@pytest.fixture
def watched(hass):
    client = _Client()
    watcher = av.LocalAvailabilityWatcher(hass, client, BLID)
    watcher.start()
    signals: list[bool] = []
    unsub = async_dispatcher_connect(hass, av.local_availability_signal(BLID), signals.append)
    yield client, watcher, signals
    unsub()
    watcher.stop()


def _advance(hass, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))


class TestTheWatcher:

    @pytest.mark.asyncio
    async def test_a_drop_shorter_than_the_grace_changes_nothing(self, hass, watched):
        """A Wi-Fi blip: roombapy reconnects within the minute."""
        client, _w, signals = watched

        client.drop()
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC - 10)
        await hass.async_block_till_done()
        client.restore()
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 10)
        await hass.async_block_till_done()

        assert av.is_locally_available(BLID) is True
        assert signals == []

    @pytest.mark.asyncio
    async def test_a_drop_that_outlasts_the_grace_makes_live_state_unavailable(
        self, hass, watched
    ):
        """@mdarocha's case: the battery died and the robot stayed away."""
        client, _w, signals = watched

        client.drop()
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()

        assert av.is_locally_available(BLID) is False
        assert signals == [False]

    @pytest.mark.asyncio
    async def test_reconnecting_makes_it_available_again(self, hass, watched):
        """The reconnect must be signalled too — many entities re-render
        only when their own key arrives, and would otherwise stay
        unavailable after the robot came back."""
        client, _w, signals = watched

        client.drop()
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()
        client.restore()
        await hass.async_block_till_done()

        assert av.is_locally_available(BLID) is True
        assert signals == [False, True]

    @pytest.mark.asyncio
    async def test_a_second_disconnect_notice_does_not_restart_the_grace(
        self, hass, watched
    ):
        """Otherwise a chatty disconnect could postpone it forever."""
        client, _w, signals = watched

        client.drop()
        _advance(hass, 40)
        await hass.async_block_till_done()
        client._cb("disconnected", "again")
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()

        assert signals == [False]

    @pytest.mark.asyncio
    async def test_stop_cancels_the_timer_and_forgets_the_robot(self, hass):
        client = _Client()
        watcher = av.LocalAvailabilityWatcher(hass, client, BLID)
        watcher.start()
        client.drop()
        watcher.stop()
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()

        assert client.unsubscribed is True
        assert av.is_locally_available(BLID) is True, "no stale 'unavailable' after unload"


class TestTheEntityRule:
    """Only LIVE state of a CLASSIC robot goes unavailable."""

    def _entity(self, cls, *, vacuum: Any = "classic"):
        e = cls.__new__(cls)
        e.vacuum = MagicMock() if vacuum == "classic" else vacuum
        e._blid = BLID
        e._attr_available = True
        return e

    def test_live_classic_state_follows_the_connection(self, monkeypatch):
        from custom_components.roomba_plus.binary_sensor import RoombaMissionActive

        monkeypatch.setitem(av._AVAILABLE, BLID, False)
        assert self._entity(RoombaMissionActive).available is False

    def test_history_keeps_its_value_while_the_robot_is_away(self, monkeypatch):
        from custom_components.roomba_plus.binary_sensor import RoombaMaintenanceDue

        monkeypatch.setitem(av._AVAILABLE, BLID, False)
        assert self._entity(RoombaMaintenanceDue).available is True

    def test_the_sensors_that_report_the_outage_stay_available(self, monkeypatch):
        """Connected and MQTT-stale are how the user SEES the outage.
        Making them unavailable would hide exactly what they report."""
        from custom_components.roomba_plus.binary_sensor import (
            RoombaConnectionStatus,
            RoombaMqttStale,
        )

        monkeypatch.setitem(av._AVAILABLE, BLID, False)
        assert self._entity(RoombaConnectionStatus).available is True
        assert self._entity(RoombaMqttStale).available is True

    def test_a_prime_live_entity_follows_its_robot_too(self, monkeypatch):
        """Since 4.2.11 (quality scale, entity-unavailable): a Prime robot
        that goes offline no longer keeps showing its last state. The rule
        on the entity is the same for both generations; which signals feed
        it is the watcher's business (PrimeAvailabilityWatcher)."""
        from custom_components.roomba_plus.sensor_prime import PrimeBatterySensor

        monkeypatch.setitem(av._AVAILABLE, BLID, False)
        assert self._entity(PrimeBatterySensor, vacuum=None).available is False

    def test_a_prime_entity_that_is_not_live_is_never_affected(self, monkeypatch):
        from custom_components.roomba_plus.sensor_prime import PrimeTotalMissionsSensor

        monkeypatch.setitem(av._AVAILABLE, BLID, False)
        assert self._entity(PrimeTotalMissionsSensor, vacuum=None).available is True

    def test_sensor_descriptions_carry_the_flag(self):
        from custom_components.roomba_plus.sensor_core import SENSORS as SENSOR_TYPES

        by_key = {d.key: d for d in SENSOR_TYPES}
        assert by_key["battery"].live_state is True
        assert by_key["phase"].live_state is True
        assert by_key["total_missions"].live_state is False
        assert by_key["last_error_code"].live_state is False, (
            "'last' error is history, not live state"
        )


_PKG = pathlib.Path("custom_components/roomba_plus")

#: THE CLASSIFICATION, spelled out so that changing it is a visible act.
EXPECTED_LIVE_CLASSES = {
    # Prime, since 4.2.11 — the robot's state now; not the connection
    # sensor (it reports the outage), counters, history, settings, maps,
    # or controls.
    "PrimeBinPresentSensor", "PrimeTankPresentSensor", "PrimeDockErrorSensor",
    "PrimeStartBlockedSensor", "PrimeBatterySensor", "PrimeDetectedPadSensor",
    "PrimeDockStatusSensor", "PrimePadWashStatusSensor", "PrimeDockTankLevelSensor",
    "PrimePadDryStatusSensor", "PrimeErrorSensor", "PrimePhaseSensor",
    "PrimeReadinessSensor", "PrimeJobInitiatorSensor",
    # Controls that SHOW a setting the robot reports, both generations —
    # their value is device data (quality scale, entity-unavailable).
    # Zone and map choosers show the user's choice, and buttons have no
    # state; they stay available.
    "EdgeCleanSwitch", "AlwaysFinishSwitch", "ScheduleHoldSwitch", "ChildLockSwitch",
    "EcoChargeSwitch", "GentleModeSwitch", "PrimeCarpetBoostSwitch",
    "PrimeSettingSwitch", "PrimeQuietHoursSwitch", "PrimePadDrySwitch",
    "SimpleRoombaSelect", "PrimeSettingSelect", "PrimeCleaningModeSelect",
    # Classic
    "IRobotVacuum", "RoombaDeviceTracker", "RoombaMissionProgress",
    "RoombaBinStatus", "RoombaBinPresentStatus",
    "RoombaMopReadyStatus", "RoombaMopTankPresentStatus",
    "RoombaMopLidClosedStatus", "RoombaMopLidOpen", "RoombaMopTankPresentDirect",
    "RoombaMapSavingStatus", "RoombaStartBlocked",
    "RoombaMidMissionRecharge", "RoombaMissionActive",
    # Buttons that send a command to the robot: a press it cannot receive
    # would do nothing. Maintenance resets write local stores and stay.
    "RoombaCommandButton", "ZoneCleanButton", "RepeatLastMissionButton",
    "SmartZoneButton", "FavoriteButton",
    "PrimeFavoriteButton", "PrimeDockButton", "PrimeLocateButton", "PrimeZoneCleanButton",
}
EXPECTED_LIVE_SENSOR_KEYS = {
    "battery", "phase", "error", "readiness", "job_initiator",
    "rssi", "snr", "signal_noise", "nav_quality",
    "mission_start_time", "mission_elapsed_time", "mission_recharge_time",
    "mission_expire_time", "mission_recharge_minutes", "mission_expire_minutes",
    "mission_id",
    "clean_base_status", "dock_tank_level", "tank_level",
    "mop_pad", "mop_tank_level", "mop_tank_status",
}


def _classes() -> dict[str, ast.ClassDef]:
    out = {}
    for f in _PKG.glob("*.py"):
        for n in ast.parse(f.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.ClassDef):
                out[n.name] = n
    return out


class TestTheClassificationIsDeliberate:

    def test_exactly_the_expected_classes_are_live(self):
        live = {
            name for name, n in _classes().items()
            if any(
                isinstance(s, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_live_state" for t in s.targets)
                and isinstance(s.value, ast.Constant) and s.value.value is True
                for s in n.body
            )
        }
        assert live == EXPECTED_LIVE_CLASSES

    def test_exactly_the_expected_sensor_keys_are_live(self):
        from custom_components.roomba_plus.sensor_core import SENSORS as SENSOR_TYPES

        assert {d.key for d in SENSOR_TYPES if d.live_state} == EXPECTED_LIVE_SENSOR_KEYS

    def test_no_live_class_bypasses_the_base_availability(self):
        """An `available` override without super() anywhere in a live
        class's hierarchy would silently disable the whole rule for it."""
        klassen = _classes()

        def kette(name: str, seen: list[str]) -> list[str]:
            if name not in klassen or name in seen:
                return seen
            seen.append(name)
            for b in klassen[name].bases:
                kette(ast.unparse(b).split(".")[-1], seen)
            return seen

        umgangen = [
            (k, c)
            for k in EXPECTED_LIVE_CLASSES | {"RoombaSensor"}
            for c in kette(k, [])
            if c != "IRobotEntity"
            for s in klassen[c].body
            if isinstance(s, ast.FunctionDef) and s.name == "available"
            and "super().available" not in ast.unparse(s)
        ]
        assert not umgangen, umgangen


class TestLiveEntitiesListenForTheSignal:
    """`available` answering correctly is not enough: with no polling,
    nothing re-reads it unless the entity re-renders. A live entity must
    subscribe to the signal when it is added; a non-live one need not."""

    async def _added(self, hass, cls, monkeypatch):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import entity as entity_mod

        verbunden: list[str] = []

        def _connect(_hass, signal, _target):
            verbunden.append(signal)
            return lambda: None

        monkeypatch.setattr(entity_mod, "async_dispatcher_connect", _connect)
        e = cls.__new__(cls)
        e.vacuum = MagicMock()
        e._blid = BLID
        e.hass = hass
        e._async_update_device_name = AsyncMock()
        e.schedule_update_ha_state = MagicMock()
        e.async_on_remove = MagicMock()
        await entity_mod.IRobotEntity.async_added_to_hass(e)
        return verbunden, e

    @pytest.mark.asyncio
    async def test_a_live_entity_subscribes(self, hass, monkeypatch):
        from custom_components.roomba_plus.binary_sensor import RoombaMissionActive

        verbunden, e = await self._added(hass, RoombaMissionActive, monkeypatch)

        from custom_components.roomba_plus.const import maintenance_changed_signal, mission_store_changed_signal

        # The availability signal, plus the two store signals every entity
        # of the robot listens to (a reset or a cloud merge re-renders them all).
        assert verbunden == [av.local_availability_signal(BLID), maintenance_changed_signal(BLID),
                            mission_store_changed_signal(BLID)]
        # One removal per subscription, plus the message callback
        # (entity-event-setup).
        assert e.async_on_remove.call_count == 4

    @pytest.mark.asyncio
    async def test_a_non_live_entity_does_not(self, hass, monkeypatch):
        from custom_components.roomba_plus.binary_sensor import RoombaMaintenanceDue

        verbunden, _e = await self._added(hass, RoombaMaintenanceDue, monkeypatch)

        from custom_components.roomba_plus.const import maintenance_changed_signal, mission_store_changed_signal

        # Not the availability signal -- only the two store signals, which
        # every entity of the robot listens to.
        assert verbunden == [maintenance_changed_signal(BLID),
                            mission_store_changed_signal(BLID)]

    def test_the_signal_makes_the_entity_re_render(self):
        from custom_components.roomba_plus.binary_sensor import RoombaMissionActive

        e = RoombaMissionActive.__new__(RoombaMissionActive)
        e.async_write_ha_state = MagicMock()
        e._on_local_availability(False)

        e.async_write_ha_state.assert_called_once()


class TestEntitiesUnsubscribeOnRemoval:
    """Quality scale, entity-event-setup: subscribe in async_added_to_hass,
    unsubscribe on removal. Both registrations returned an Unsubscribe that
    was discarded — an entity removed or disabled at runtime stayed on the
    client and kept receiving every message."""

    @pytest.mark.asyncio
    async def test_the_message_callback_is_handed_to_async_on_remove(self, hass):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus import entity as entity_mod
        from custom_components.roomba_plus.binary_sensor import RoombaMaintenanceDue

        unsub = MagicMock(name="unsubscribe")
        e = RoombaMaintenanceDue.__new__(RoombaMaintenanceDue)
        e.vacuum = MagicMock()
        e.vacuum.register_on_message_callback.return_value = unsub
        e._blid = BLID
        e.hass = hass
        e._async_update_device_name = AsyncMock()
        e.schedule_update_ha_state = MagicMock()
        e.async_on_remove = MagicMock()

        await entity_mod.IRobotEntity.async_added_to_hass(e)

        e.async_on_remove.assert_any_call(unsub)

    @pytest.mark.asyncio
    async def test_the_connection_sensor_also_unsubscribes_its_disconnect_callback(
        self, hass
    ):
        from unittest.mock import AsyncMock

        from custom_components.roomba_plus.binary_sensor import RoombaConnectionStatus

        unsub_msg, unsub_disc = MagicMock(name="msg"), MagicMock(name="disc")
        e = RoombaConnectionStatus.__new__(RoombaConnectionStatus)
        e.vacuum = MagicMock()
        e.vacuum.register_on_message_callback.return_value = unsub_msg
        e.vacuum.register_on_disconnect_callback.return_value = unsub_disc
        e._blid = BLID
        e.hass = hass
        e._async_update_device_name = AsyncMock()
        e.schedule_update_ha_state = MagicMock()
        e.async_on_remove = MagicMock()

        await e.async_added_to_hass()

        e.async_on_remove.assert_any_call(unsub_msg)
        e.async_on_remove.assert_any_call(unsub_disc)


class _Coordinator:
    """Just enough of a DataUpdateCoordinator: listeners, data, success."""

    def __init__(self, data=None, ok=True):
        self.data = data
        self.last_update_success = ok
        self.listeners = []

    def async_add_listener(self, cb):
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    def push(self, *, data=None, ok=None):
        if data is not None:
            self.data = data
        if ok is not None:
            self.last_update_success = ok
        for cb in list(self.listeners):
            cb()


def _constatus(connected: bool) -> dict:
    return {"rw-constatus": {"connected": connected}}


class TestPrimeAvailabilityWatcher:
    """Quality scale, entity-unavailable and log-when-unavailable, for Prime.
    Same grace period, signal and logging as Classic; the source is our own
    connection plus the robot's rw-constatus."""

    def _start(self, hass, *, constatus=True, ok=True):
        prime = _Coordinator(ok=ok)
        status = _Coordinator(data=_constatus(constatus) if constatus is not None else {})
        w = av.PrimeAvailabilityWatcher(hass, BLID, prime, status)
        w.start()
        return w, prime, status

    @pytest.mark.asyncio
    async def test_a_connected_robot_is_available(self, hass):
        w, _p, _s = self._start(hass)
        assert av.is_locally_available(BLID) is True
        w.stop()

    @pytest.mark.asyncio
    async def test_the_robot_going_offline_makes_it_unavailable_after_the_grace(self, hass, caplog):
        w, _p, status = self._start(hass)
        status.push(data=_constatus(False))
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC - 10)
        await hass.async_block_till_done()
        assert av.is_locally_available(BLID) is True, "not before the grace period"
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 10)
        await hass.async_block_till_done()
        assert av.is_locally_available(BLID) is False
        assert "unreachable" in caplog.text
        w.stop()

    @pytest.mark.asyncio
    async def test_losing_our_own_connection_counts_even_if_the_robot_says_connected(self, hass):
        """The case rw-constatus alone would miss: no messages arrive, so
        it keeps its last 'connected' for ever."""
        w, prime, _s = self._start(hass)
        prime.push(ok=False)
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()
        assert av.is_locally_available(BLID) is False
        w.stop()

    @pytest.mark.asyncio
    async def test_coming_back_is_immediate_and_logged(self, hass, caplog):
        w, _p, status = self._start(hass)
        status.push(data=_constatus(False))
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()
        status.push(data=_constatus(True))
        assert av.is_locally_available(BLID) is True
        assert "reconnected" in caplog.text
        w.stop()

    @pytest.mark.asyncio
    async def test_no_constatus_yet_is_not_a_failure(self, hass):
        w, _p, _s = self._start(hass, constatus=None)
        _advance(hass, av.LOCAL_UNAVAILABLE_GRACE_SEC + 1)
        await hass.async_block_till_done()
        assert av.is_locally_available(BLID) is True
        w.stop()

    @pytest.mark.asyncio
    async def test_stop_detaches_from_both_coordinators(self, hass):
        w, prime, status = self._start(hass)
        assert len(prime.listeners) == 1 and len(status.listeners) == 1
        w.stop()
        assert prime.listeners == [] and status.listeners == []
        assert BLID not in av._AVAILABLE


# ── formerly tests/test_coverage_small_gaps.py ──────────────────────────────────
#
# Small gaps in eight modules — quality scale, test-coverage (Silver).
#
# Mostly error branches and edge cases: a malformed input must not crash,
# must not return something wrong, and where the code logs, it must log.
# Each test pins what the branch is for, not only that it ran.

BLID_m = "SMALLGAPS01"


class TestAvailabilityEdges:

    def test_a_client_without_connection_callbacks_is_tolerated(self, hass, caplog):
        """An older roombapy without the callback: no crash, the robot just
        stays available, and the debug log says why."""
        import logging

        from custom_components.roomba_plus import availability as av

        caplog.set_level(logging.DEBUG)
        w = av.LocalAvailabilityWatcher(hass, SimpleNamespace(), BLID_m)
        w.start()
        assert av.is_locally_available(BLID_m) is True
        assert "no connection-state callback" in caplog.text
        w.stop()

    def test_a_stopped_watcher_ignores_late_transitions(self, hass):
        from custom_components.roomba_plus import availability as av

        roomba = MagicMock()
        w = av.LocalAvailabilityWatcher(hass, roomba, BLID_m)
        w.start()
        w.stop()
        w._on_connection_state("disconnected")   # must not start a timer
        assert w._cancel_timer is None
        w._grace_expired(None)                    # must not mark anything
        assert BLID_m not in av._AVAILABLE

    def test_a_malformed_constatus_counts_as_connected(self, hass):
        """Unreadable data is not evidence of an outage."""
        from custom_components.roomba_plus import availability as av

        status = SimpleNamespace(data={"rw-constatus": 12345})
        w = av.PrimeAvailabilityWatcher(hass, BLID_m, None, status)
        assert w._currently_connected() is True


class TestAnUnreadableConstatusIsNotAnOutage:
    """`ConnectionStatusShadow.from_json` returns connected=None for any
    unreadable shadow instead of raising. Read through `bool()`, that was
    'disconnected' — found by the coverage work on the watcher."""

    @pytest.mark.parametrize("raw", [12345, {}, {"something": 1}, "text"])
    def test_the_watcher_keeps_the_robot_available(self, hass, raw):
        from custom_components.roomba_plus import availability as av

        w = av.PrimeAvailabilityWatcher(hass, BLID_m, None, SimpleNamespace(data={"rw-constatus": raw}))
        assert w._currently_connected() is True

    def test_an_explicit_false_is_still_an_outage(self, hass):
        from custom_components.roomba_plus import availability as av

        w = av.PrimeAvailabilityWatcher(
            hass, BLID_m, None, SimpleNamespace(data={"rw-constatus": {"connected": False}}))
        assert w._currently_connected() is False

    @pytest.mark.parametrize("raw,erwartet", [({}, None), ({"connected": True}, True),
                                             ({"connected": False}, False)])
    def test_the_connectivity_sensor_shows_unknown_not_off(self, raw, erwartet):
        from custom_components.roomba_plus.binary_sensor import PrimeRobotConnectivitySensor

        e = PrimeRobotConnectivitySensor.__new__(PrimeRobotConnectivitySensor)
        e._config_entry = MagicMock()
        e._config_entry.runtime_data.prime_status_coordinator.data = {"rw-constatus": raw}
        assert e.is_on is erwartet
