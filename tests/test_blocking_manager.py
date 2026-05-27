"""Unit tests for BlockingManager (v1.7.0 L5).

Tests the pure synchronous logic that doesn't require a live HA event loop.
Async paths (queue, timeout) are tested with AsyncMock where needed.
"""
import asyncio
import pytest
import types
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.blocking_manager import (
    BlockingManager,
    EVENT_START_BLOCKED,
    EVENT_START_TIMEOUT,
    _ACTIVE_STATES,
    _UNUSABLE_STATES,
)
from custom_components.roomba_plus.const import (
    CONF_BLOCKING_BEHAVIOR,
    CONF_BLOCKING_SENSORS,
    CONF_BLOCKING_TIMEOUT_MIN,
    DEFAULT_BLOCKING_BEHAVIOR,
    DEFAULT_BLOCKING_TIMEOUT_MIN,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeState:
    def __init__(self, state_str: str):
        self.state = state_str


class _FakeStates:
    def __init__(self, mapping: dict[str, str]):
        self._m = mapping

    def get(self, entity_id: str):
        s = self._m.get(entity_id)
        return _FakeState(s) if s is not None else None


class _FakeHass:
    def __init__(self, sensor_states: dict[str, str] | None = None):
        self._sensor_states = sensor_states or {}
        self.states = _FakeStates(self._sensor_states)
        self._fired_events: list[tuple[str, dict]] = []
        self._bus_listeners: list = []

    def bus_fire(self, event_type: str, data: dict) -> None:
        self._fired_events.append((event_type, data))

    @property
    def bus(self):
        class _Bus:
            def async_fire(inner_self, event_type, data=None):
                self._fired_events.append((event_type, data or {}))
            def async_listen(inner_self, event_type, callback, event_filter=None):
                unsub = lambda: None
                self._bus_listeners.append(unsub)
                return unsub
        return _Bus()

    def async_create_task(self, coro, name=None):
        # In tests, wrap coro so it can be inspected without running
        coro.close()
        return types.SimpleNamespace(done=lambda: True, cancel=lambda: None)

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


def _make_entry(sensors: list[str], behavior: str = "abort", timeout: int = 30):
    class _Entry:
        options = {
            CONF_BLOCKING_SENSORS: sensors,
            CONF_BLOCKING_BEHAVIOR: behavior,
            CONF_BLOCKING_TIMEOUT_MIN: timeout,
        }
        entry_id = "test_entry"

        class runtime_data:
            class roomba:
                @staticmethod
                def start():
                    pass
            blid = "TEST1234"
    return _Entry()


# ── Tests: blocking_entities property ────────────────────────────────────────

class TestBlockingEntities:
    def test_no_sensors_configured_returns_empty(self):
        hass = _FakeHass({})
        entry = _make_entry([])
        bm = BlockingManager(hass, entry)
        assert bm.blocking_entities == []

    def test_sensor_on_is_blocking(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"])
        bm = BlockingManager(hass, entry)
        assert "binary_sensor.door" in bm.blocking_entities

    def test_sensor_off_is_not_blocking(self):
        hass = _FakeHass({"binary_sensor.door": "off"})
        entry = _make_entry(["binary_sensor.door"])
        bm = BlockingManager(hass, entry)
        assert bm.blocking_entities == []

    def test_sensor_unavailable_is_not_blocking(self):
        """Unavailable sensors must not block starts — treat as non-blocking."""
        hass = _FakeHass({"binary_sensor.door": "unavailable"})
        entry = _make_entry(["binary_sensor.door"])
        bm = BlockingManager(hass, entry)
        assert bm.blocking_entities == []

    def test_sensor_unknown_is_not_blocking(self):
        hass = _FakeHass({"binary_sensor.door": "unknown"})
        entry = _make_entry(["binary_sensor.door"])
        bm = BlockingManager(hass, entry)
        assert bm.blocking_entities == []

    def test_missing_sensor_entity_not_blocking(self):
        hass = _FakeHass({})  # sensor not in states
        entry = _make_entry(["binary_sensor.door"])
        bm = BlockingManager(hass, entry)
        assert bm.blocking_entities == []

    def test_mixed_sensors(self):
        hass = _FakeHass({
            "binary_sensor.door": "off",
            "binary_sensor.motion": "on",
        })
        entry = _make_entry(["binary_sensor.door", "binary_sensor.motion"])
        bm = BlockingManager(hass, entry)
        blocking = bm.blocking_entities
        assert "binary_sensor.motion" in blocking
        assert "binary_sensor.door" not in blocking

    def test_home_state_counts_as_active(self):
        """Person state 'home' should block (for presence sensors)."""
        hass = _FakeHass({"person.alice": "home"})
        entry = _make_entry(["person.alice"])
        bm = BlockingManager(hass, entry)
        assert "person.alice" in bm.blocking_entities

    def test_true_string_counts_as_active(self):
        hass = _FakeHass({"input_boolean.test": "true"})
        entry = _make_entry(["input_boolean.test"])
        bm = BlockingManager(hass, entry)
        assert "input_boolean.test" in bm.blocking_entities


# ── Tests: is_queued / initial state ─────────────────────────────────────────

class TestInitialState:
    def test_not_queued_initially(self):
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        assert bm.is_queued is False

    def test_queued_since_none_initially(self):
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        assert bm.queued_since is None

    def test_timeout_at_none_initially(self):
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        assert bm.timeout_at is None


# ── Tests: cancel_queue ───────────────────────────────────────────────────────

class TestCancelQueue:
    def test_cancel_queue_is_idempotent(self):
        """cancel_queue must be safe to call multiple times."""
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        bm.cancel_queue()
        bm.cancel_queue()  # No exception
        assert bm.is_queued is False

    def test_cancel_queue_clears_state(self):
        """After cancel_queue, all queue state is cleared."""
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue")
        bm = BlockingManager(hass, entry)
        # Manually inject some queue state
        bm._queued_since = "2025-01-01T09:00:00+00:00"
        bm._timeout_at = "2025-01-01T09:30:00+00:00"
        bm._cancel_listeners = [lambda: None]
        bm.cancel_queue()
        assert bm.is_queued is False
        assert bm.queued_since is None
        assert bm.timeout_at is None

    def test_cancel_queue_calls_unsub(self):
        """cancel_queue must call all stored unsubscribe callables."""
        called = []
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        bm._cancel_listeners = [lambda: called.append(1), lambda: called.append(2)]
        bm.cancel_queue()
        assert called == [1, 2]

    def test_cancel_queue_handles_failing_unsub_gracefully(self):
        """A failing unsub callable must not prevent the rest from being called."""
        called = []
        def _bad_unsub():
            raise RuntimeError("unsub failed")
        def _good_unsub():
            called.append(1)
        hass = _FakeHass({})
        bm = BlockingManager(hass, _make_entry([]))
        bm._cancel_listeners = [_bad_unsub, _good_unsub]
        bm.cancel_queue()  # Must not raise
        assert 1 in called


# ── Tests: abort behavior ─────────────────────────────────────────────────────

class TestAbortBehavior:
    @pytest.mark.asyncio
    async def test_abort_fires_event_when_blocked(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="abort")
        bm = BlockingManager(hass, entry)
        await bm.check_and_start(rooms=None, override=False)
        assert any(evt[0] == EVENT_START_BLOCKED for evt in hass._fired_events)

    @pytest.mark.asyncio
    async def test_abort_does_not_queue(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="abort")
        bm = BlockingManager(hass, entry)
        await bm.check_and_start(rooms=None, override=False)
        assert bm.is_queued is False


# ── Tests: override ───────────────────────────────────────────────────────────

class TestOverride:
    @pytest.mark.asyncio
    async def test_override_bypasses_blocking_sensors(self):
        """override=True must start immediately even when sensors are ON."""
        started = []
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="abort")
        bm = BlockingManager(hass, entry)
        # Patch _do_start to track calls
        async def _fake_do_start(rooms):
            started.append(rooms)
        bm._do_start = _fake_do_start
        await bm.check_and_start(rooms=None, override=True)
        assert started == [None]
        assert not any(evt[0] == EVENT_START_BLOCKED for evt in hass._fired_events)


# ── Tests: queue behavior ─────────────────────────────────────────────────────

class TestQueueBehavior:
    def test_queue_sets_is_queued(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue")
        bm = BlockingManager(hass, entry)
        bm._queue_start(rooms=None)
        assert bm.is_queued is True

    def test_queue_sets_queued_since(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue")
        bm = BlockingManager(hass, entry)
        bm._queue_start(rooms=None)
        assert bm.queued_since is not None
        import datetime
        datetime.datetime.fromisoformat(bm.queued_since)

    def test_queue_sets_timeout_at(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue", timeout=15)
        bm = BlockingManager(hass, entry)
        bm._queue_start(rooms=None)
        assert bm.timeout_at is not None
        import datetime
        t_since = datetime.datetime.fromisoformat(bm.queued_since)
        t_timeout = datetime.datetime.fromisoformat(bm.timeout_at)
        delta = t_timeout - t_since
        # Should be ~15 minutes (within 1 second tolerance)
        assert abs(delta.total_seconds() - 15 * 60) < 2

    @pytest.mark.asyncio
    async def test_duplicate_queue_request_ignored(self):
        """Second check_and_start while already queued must be a no-op."""
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue")
        bm = BlockingManager(hass, entry)
        # First call — queues the start
        await bm.check_and_start(rooms=None, override=False)
        assert bm.is_queued is True
        count_after_first = len(bm._cancel_listeners)
        # Second call — must be a no-op (is_queued guard in check_and_start)
        await bm.check_and_start(rooms=None, override=False)
        assert bm.is_queued is True
        count_after_second = len(bm._cancel_listeners)
        # No new listeners registered on second call
        assert count_after_first == count_after_second

    def test_queue_rooms_stored(self):
        hass = _FakeHass({"binary_sensor.door": "on"})
        entry = _make_entry(["binary_sensor.door"], behavior="queue")
        bm = BlockingManager(hass, entry)
        bm._queue_start(rooms=["Kitchen", "Hallway"])
        assert bm._queued_rooms == ["Kitchen", "Hallway"]


# ── Tests: _ACTIVE_STATES / _UNUSABLE_STATES constants ───────────────────────

class TestStateConstants:
    def test_on_is_active(self):
        assert "on" in _ACTIVE_STATES

    def test_home_is_active(self):
        assert "home" in _ACTIVE_STATES

    def test_unavailable_is_unusable(self):
        assert "unavailable" in _UNUSABLE_STATES

    def test_unknown_is_unusable(self):
        assert "unknown" in _UNUSABLE_STATES

    def test_off_is_not_active(self):
        assert "off" not in _ACTIVE_STATES

    def test_off_is_not_unusable(self):
        assert "off" not in _UNUSABLE_STATES
