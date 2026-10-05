"""`hass` comes from runtime data, never from the config entry.

A ConfigEntry has no `hass` attribute. Three places read it with
`getattr(entry, "hass", None)`, got None and quietly did nothing; the
tests passed because a MagicMock entry answers every attribute.

The visible one: the signal that announces Prime room names was never
sent. @mrsnyds (Roomba 105) ticked *Separate sensor per room and zone*,
reloaded, restarted, and got no sensor at all, while the diagnostics
listed all nine names -- and his room history showed region numbers
until a command made the robot push something.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.helpers.dispatcher import async_dispatcher_connect

from custom_components.roomba_plus.prime_room_map import (
    SIGNAL_PRIME_ROOM_NAMES,
    async_build_prime_floor_plan,
)

_ROOMS = {"features": [
    {"id": "15", "properties": {"name": "Guest Bath"}},
    {"id": "17", "properties": {"name": "Kitchen"}},
]}


def _entry(hass):
    """A config entry the way Home Assistant builds one: no `hass`."""
    robot = MagicMock()
    robot.get_map_geojson_link = AsyncMock(return_value={"map_url": "https://x"})
    robot.download_map_bundle = AsyncMock(return_value=b"tgz")
    runtime = SimpleNamespace(
        prime_robot=robot,
        prime_room_names={},
        prime_room_map_ids={},
        hass_ref=hass,
    )
    return SimpleNamespace(entry_id="E1", runtime_data=runtime)


class TestTheRoomNamesAnnounceThemselves:
    async def test_the_map_build_sends_the_signal(self, hass) -> None:
        heard: list[bool] = []
        async_dispatcher_connect(
            hass, SIGNAL_PRIME_ROOM_NAMES.format("E1"), lambda: heard.append(True)
        )
        entry = _entry(hass)

        with patch(
            "roombapy_prime.models.map_bundle.parse_map_bundle",
            return_value={"rooms": _ROOMS},
        ):
            await async_build_prime_floor_plan(entry, "MAP-1", "V1")
        await hass.async_block_till_done()

        assert entry.runtime_data.prime_room_names == {
            "15": "Guest Bath", "17": "Kitchen",
        }
        assert heard == [True]

    async def test_the_history_redraws_when_the_names_arrive(self, hass) -> None:
        """A robot at rest pushes nothing, so the coordinator listener
        alone left the history on region numbers."""
        from custom_components.roomba_plus.entity import IRobotEntity
        from custom_components.roomba_plus.sensor_rooms import (
            PrimeRoomCleaningHistorySensor,
            PrimeRoomsOverdueSensor,
        )

        for cls in (PrimeRoomCleaningHistorySensor, PrimeRoomsOverdueSensor):
            sensor = cls.__new__(cls)
            sensor.hass = hass
            sensor._config_entry = SimpleNamespace(
                entry_id="E1",
                runtime_data=SimpleNamespace(
                    prime_status_coordinator=None, cloud_coordinator=None
                ),
            )
            sensor.async_write_ha_state = MagicMock()
            with patch.object(IRobotEntity, "async_added_to_hass", AsyncMock()):
                await sensor.async_added_to_hass()

            from homeassistant.helpers.dispatcher import async_dispatcher_send

            async_dispatcher_send(hass, SIGNAL_PRIME_ROOM_NAMES.format("E1"))
            await hass.async_block_till_done()

            sensor.async_write_ha_state.assert_called_once()
