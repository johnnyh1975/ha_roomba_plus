"""iRobot's parts catalogue and the guide link per part (I4)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import parts_catalog as pc

CATALOGUE = {
    "buyPartsUrl": "https://store.example/parts",
    "parts": [
        {"part_id": 67, "part_name": "Filter",
         "guide_url": "https://help.irobot.com/filter", "buy_url": "https://x"},
        {"part_id": "71", "part_name": "Brushes", "guide_url": ""},
        {"partId": 147, "partName": "Dirt bag", "guideUrl": "https://help.irobot.com/bag"},
        {"part_name": "No id"},
        {"part_id": 9, "guide_url": "http://insecure.example/guide"},
    ],
}


class TestParse:
    def test_links_join_by_part_id_and_only_real_ones_count(self):
        parsed = pc.parse_catalogue(CATALOGUE)
        assert parsed["67"] == {"guide_url": "https://help.irobot.com/filter",
                                "part_name": "Filter"}
        assert parsed["71"] == {"part_name": "Brushes"}, "an empty guide is no guide"
        assert parsed["147"]["guide_url"] == "https://help.irobot.com/bag"
        assert "guide_url" not in parsed.get("9", {}), "https only"

    @pytest.mark.parametrize("payload", [None, [], {"parts": None}, "x"])
    def test_anything_else_is_empty(self, payload):
        assert pc.parse_catalogue(payload) == {}


class TestLocale:
    @pytest.mark.parametrize(
        ("language", "country", "expected"),
        [
            ("de", "DE", ("de-DE", "DE")),
            ("de", "AT", ("de-AT", "AT")),
            ("en-GB", None, ("en-GB", "GB")),
            ("pt-BR", "BR", ("pt-BR", "BR")),
            ("fr", None, ("en-US", "US")),
        ],
    )
    def test_the_path_wants_language_with_region_and_a_country(self, language, country, expected):
        hass = SimpleNamespace(config=SimpleNamespace(language=language, country=country))
        assert pc.catalogue_locale(hass) == expected


class TestFetch:
    def _session(self, status=200, payload=CATALOGUE, raises=None):
        resp = MagicMock(status=status)
        resp.json = AsyncMock(return_value=payload)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        if raises:
            session.get.side_effect = raises
        else:
            session.get.return_value = ctx
        return session

    @pytest.mark.asyncio
    async def test_the_url_carries_locale_and_sku(self):
        hass = SimpleNamespace(config=SimpleNamespace(language="de", country="DE"))
        session = self._session()
        with patch.object(pc, "async_get_clientsession", return_value=session):
            parsed = await pc.async_fetch_catalogue(hass, "i755840")
        assert session.get.call_args.args[0] == (
            "https://content-prod.iot.irobotapi.com/v2/de-DE/DE/i755840/parts"
        )
        assert parsed["67"]["guide_url"].endswith("/filter")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [{"status": 404}, {"raises": OSError("down")}])
    async def test_a_failed_read_costs_the_links_only(self, kwargs):
        hass = SimpleNamespace(config=SimpleNamespace(language="en", country="US"))
        with patch.object(pc, "async_get_clientsession", return_value=self._session(**kwargs)):
            assert await pc.async_fetch_catalogue(hass, "i755840") == {}

    @pytest.mark.asyncio
    async def test_no_sku_no_request(self):
        session = self._session()
        with patch.object(pc, "async_get_clientsession", return_value=session):
            assert await pc.async_fetch_catalogue(MagicMock(), None) == {}
        session.get.assert_not_called()


class TestTheSensorsShowTheGuide:
    def test_a_prime_part(self):
        from custom_components.roomba_plus.sensor_prime import PrimeConsumablePartSensor

        entry = MagicMock()
        part = SimpleNamespace(part_id="67", count_type="minutes", count_used=10,
                               minutes_remaining=100, count_remaining=100,
                               counter_category="replacement")
        entry.runtime_data.prime_parts_coordinator.data = {"67": part}
        entry.runtime_data.parts_catalogue = pc.parse_catalogue(CATALOGUE)
        sensor = PrimeConsumablePartSensor("B", "67", entry)
        assert sensor.extra_state_attributes["guide_url"] == "https://help.irobot.com/filter"

        entry.runtime_data.parts_catalogue = {}
        assert "guide_url" not in sensor.extra_state_attributes

    def test_a_classic_consumable_through_its_cloud_part(self):
        from custom_components.roomba_plus.sensor_core import SENSORS, RoombaSensor

        description = next(d for d in SENSORS if d.consumable_role == "filter")
        sensor = RoombaSensor.__new__(RoombaSensor)
        sensor.entity_description = description
        entry = MagicMock()
        entry.options = {}
        store = MagicMock()
        store.cloud_part_by_role.return_value = {"part_id": 67}
        store.cloud_full_life_hours.return_value = 150
        entry.runtime_data.maintenance_store = store
        entry.runtime_data.parts_catalogue = pc.parse_catalogue(CATALOGUE)
        sensor._config_entry = entry
        sensor.vacuum_state = {}

        attrs = sensor.extra_state_attributes
        assert attrs["guide_url"] == "https://help.irobot.com/filter"
        store.cloud_part_by_role.assert_called_with("filter")
