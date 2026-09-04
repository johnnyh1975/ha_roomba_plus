"""The dock gate is re-read on every shadow, and never removes.

WHY THIS EXISTS. Dock-derived entities were created once, in
`async_setup_entry`, from whatever `ro-currentstate.dock` happened to say
at that instant. The gate reads `dock.known`, and @AlakazipLabs showed
that field is not stable: across 24,900 telemetry messages they logged 15
true-to-false flips, 11 of them within seconds of a user `dock` command
(median 4 s), returning on their own after 95 s, 18 min and 34 min with
no dock contact. 0 of 35 self-docks flipped it.

So a restart or config-entry reload landing inside one of those windows
cost a robot with a real dock its pad-wash and pad-dry entities until the
next reload happened to fall elsewhere.

THE GATE RULE IS NOT WHAT CHANGED -- an evidence-based rule was tried and
withdrawn, because no capture in this repo shows `pwState`/`pdState`
arriving at rest and it would have removed the sensors from every dock
that had not washed yet. What changed is that the question is asked again
on every shadow, and that the answer can only ever ADD.

NEGATIVE CONTROLS INCLUDED. Each test here was run against the old
setup-only code path first; the recovery tests fail there, which is what
makes them worth keeping.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roomba_plus.prime_coordinator import (
    add_prime_entities_when_available,
)


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
