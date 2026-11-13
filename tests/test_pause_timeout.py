"""Send a robot home that was left paused (4.6.0, B15)."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.roomba_plus import pause_timeout as pt

VACUUM = "vacuum.robbie"
_REAL_LOOKUP = pt._vacuum_entity_id


class _Entry:
    def __init__(self, minutes):
        self.entry_id = "e1"
        self.title = "Robbie"
        self.options = {"pause_timeout_minutes": minutes}
        self.unloads = []

    def async_on_unload(self, func):
        self.unloads.append(func)

    def async_create_background_task(self, hass, coro, name):
        return hass.async_create_background_task(coro, name)


@pytest.fixture(autouse=True)
def _the_vacuum():
    with patch.object(pt, "_vacuum_entity_id", return_value=VACUUM):
        yield


async def _later(hass, minutes):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
    await hass.async_block_till_done()


async def test_a_long_pause_sends_it_home(hass):
    calls = async_mock_service(hass, "vacuum", "return_to_base")
    pt.async_setup_pause_timeout(hass, _Entry(10))
    hass.states.async_set(VACUUM, "paused")
    await hass.async_block_till_done()
    await _later(hass, 9)
    assert calls == []
    await _later(hass, 11)
    assert [c.data["entity_id"] for c in calls] == [VACUUM]


async def test_a_resume_cancels_it(hass):
    calls = async_mock_service(hass, "vacuum", "return_to_base")
    pt.async_setup_pause_timeout(hass, _Entry(10))
    hass.states.async_set(VACUUM, "paused")
    await hass.async_block_till_done()
    hass.states.async_set(VACUUM, "cleaning")
    await hass.async_block_till_done()
    await _later(hass, 11)
    assert calls == []


async def test_other_entities_do_nothing(hass):
    calls = async_mock_service(hass, "vacuum", "return_to_base")
    pt.async_setup_pause_timeout(hass, _Entry(10))
    hass.states.async_set("vacuum.other", "paused")
    hass.states.async_set("light.x", "paused")
    await hass.async_block_till_done()
    await _later(hass, 11)
    assert calls == []


async def test_off_by_default_or_nonsense(hass):
    for minutes in (0, None, "x"):
        entry = _Entry(minutes)
        pt.async_setup_pause_timeout(hass, entry)
        assert entry.unloads == []


async def test_unload_cancels(hass):
    calls = async_mock_service(hass, "vacuum", "return_to_base")
    entry = _Entry(10)
    pt.async_setup_pause_timeout(hass, entry)
    hass.states.async_set(VACUUM, "paused")
    await hass.async_block_till_done()
    for func in entry.unloads:
        func()
    await _later(hass, 11)
    assert calls == []


async def test_an_unreachable_robot_stays_paused(hass, caplog):
    async def _fail(_call):
        raise RuntimeError("offline")

    hass.services.async_register("vacuum", "return_to_base", _fail)
    pt.async_setup_pause_timeout(hass, _Entry(1))
    hass.states.async_set(VACUUM, "paused")
    await hass.async_block_till_done()
    await _later(hass, 2)
    assert "could not send the paused robot home" in caplog.text


def test_the_vacuum_entity_of_the_entry():
    entries = [
        SimpleNamespace(domain="sensor", entity_id="sensor.x"),
        SimpleNamespace(domain="vacuum", entity_id=VACUUM),
    ]
    with patch.object(pt.er, "async_get"), \
            patch.object(pt.er, "async_entries_for_config_entry", return_value=entries):
        assert _REAL_LOOKUP(None, SimpleNamespace(entry_id="e1")) == VACUUM
    with patch.object(pt.er, "async_get"), \
            patch.object(pt.er, "async_entries_for_config_entry", return_value=entries[:1]):
        assert _REAL_LOOKUP(None, SimpleNamespace(entry_id="e1")) is None
