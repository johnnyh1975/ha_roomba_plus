"""The map the naming forms show, and which rooms they ask about (4.2.19).

@liblit was asked to name numbered zones with nothing in Home Assistant
saying which room a number was -- on a 980 with no map at all, and on an
i7 whose rooms all had names in his iRobot account. Everything needed to
answer the form must be in the form.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import naming_map
from custom_components.roomba_plus.models import MapCapability
from custom_components.roomba_plus.room_cleaning import smart_rooms_to_name


def _entry(capability=MapCapability.SMART, blid="BLID1"):
    return SimpleNamespace(
        entry_id="E1",
        domain="roomba_plus",
        runtime_data=SimpleNamespace(blid=blid, map_capability=capability),
    )


def _hass(entity=None, entity_id="image.roomba_rooms_map"):
    hass = MagicMock()
    component = MagicMock()
    component.get_entity.return_value = entity
    hass.data = {naming_map.IMAGE_COMPONENT: component}
    registry = MagicMock()
    registry.async_get_entity_id.return_value = entity_id
    return hass, registry, component


class TestWhichMapIsShown:
    @pytest.mark.parametrize(
        ("capability", "unique_id"),
        [
            (MapCapability.SMART, "roomba_plus_BLID1_rooms_map"),
            (MapCapability.EPHEMERAL, "roomba_plus_BLID1_map"),
        ],
    )
    def test_the_robots_own_map(self, capability, unique_id):
        hass, registry, _ = _hass(entity=MagicMock())
        with patch.object(naming_map.er, "async_get", return_value=registry), \
             patch.object(naming_map, "async_sign_path", return_value="/signed"):
            md = naming_map.naming_map_markdown(hass, _entry(capability))
        registry.async_get_entity_id.assert_called_once_with(
            "image", "roomba_plus", unique_id
        )
        assert md == "![map](/signed)"

    def test_the_link_is_signed_for_the_entrys_view(self):
        hass, registry, _ = _hass(entity=MagicMock())
        with patch.object(naming_map.er, "async_get", return_value=registry), \
             patch.object(naming_map, "async_sign_path", return_value="/s") as sign:
            naming_map.naming_map_markdown(hass, _entry())
        path, lifetime = sign.call_args.args[1:]
        assert path == "/api/roomba_plus/E1/naming_map.png"
        assert lifetime == naming_map.LINK_LIFETIME

    def test_a_robot_without_a_map_gets_none(self):
        hass, registry, _ = _hass(entity=MagicMock())
        with patch.object(naming_map.er, "async_get", return_value=registry):
            assert naming_map.naming_map_markdown(
                hass, _entry(MapCapability.NONE)
            ) is None

    def test_a_disabled_map_entity_gets_none(self):
        """In the registry, never added: no picture, so no form."""
        hass, registry, _ = _hass(entity=None)
        with patch.object(naming_map.er, "async_get", return_value=registry):
            assert naming_map.naming_map_markdown(hass, _entry()) is None


class TestTheView:
    @pytest.mark.asyncio
    async def test_the_rooms_map_is_rendered_with_labels(self):
        entity = MagicMock()
        entity.async_naming_image = AsyncMock(return_value=b"\x89PNG-labels")
        entity.async_image = AsyncMock(return_value=b"\x89PNG-plain")
        hass, registry, _ = _hass(entity=entity)
        entry = _entry()
        entry.state = naming_map.ConfigEntryState.LOADED
        hass.config_entries.async_get_entry.return_value = entry
        request = MagicMock()
        request.app = {"hass": hass}
        with patch.object(naming_map.er, "async_get", return_value=registry):
            resp = await naming_map.NamingMapView().get(request, "E1")
        assert resp.status == 200
        assert resp.body == b"\x89PNG-labels"
        assert resp.content_type == "image/png"
        assert resp.headers["Cache-Control"] == "no-store"

    @pytest.mark.asyncio
    async def test_the_cleaning_path_map_is_served_as_is(self):
        entity = SimpleNamespace(async_image=AsyncMock(return_value=b"\x89PNG-path"))
        hass, registry, _ = _hass(entity=entity)
        entry = _entry(MapCapability.EPHEMERAL)
        entry.state = naming_map.ConfigEntryState.LOADED
        hass.config_entries.async_get_entry.return_value = entry
        request = MagicMock()
        request.app = {"hass": hass}
        with patch.object(naming_map.er, "async_get", return_value=registry):
            resp = await naming_map.NamingMapView().get(request, "E1")
        assert resp.body == b"\x89PNG-path"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("problem", ["missing", "other_domain", "not_loaded"])
    async def test_only_a_loaded_roomba_plus_entry(self, problem):
        hass, _, _ = _hass()
        entry = _entry()
        entry.state = naming_map.ConfigEntryState.LOADED
        if problem == "missing":
            entry = None
        elif problem == "other_domain":
            entry.domain = "roomba"
        else:
            entry.state = naming_map.ConfigEntryState.SETUP_ERROR
        hass.config_entries.async_get_entry.return_value = entry
        request = MagicMock()
        request.app = {"hass": hass}
        resp = await naming_map.NamingMapView().get(request, "E1")
        assert resp.status == 404

    def test_it_requires_auth(self):
        """The form reaches it through a signed path; nothing else may."""
        assert naming_map.NamingMapView.requires_auth is True


class TestWhichRoomsAreAskedAbout:
    """`smart_rooms_to_name`: on the map, and named nowhere."""

    def _data(self, polygons, *, account=None, on_map=None):
        coordinator = MagicMock()
        coordinator.regions = [{"id": k, "name": v} for k, v in (account or {}).items()]
        coordinator.zones = []
        coordinator.regions_by_pmap = {}
        aligner = MagicMock()
        aligner.room_polygons_umf = {rid: [] for rid in polygons}
        aligner.rid_to_name.return_value = dict(on_map or {})
        return SimpleNamespace(cloud_coordinator=coordinator, umf_aligner=aligner)

    def test_named_in_the_account(self):
        data = self._data(["1", "2"], account={"1": "Kitchen"})
        assert smart_rooms_to_name(data, {}) == ["2"]

    def test_named_on_the_map_or_by_the_user(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_ALIASES

        data = self._data(["1", "2", "3", "4"], on_map={"1": "Kitchen", "2": "2"})
        options = {
            CONF_SMART_ZONE_ALIASES: {"3": "Den"},
            "smart_zone_labels": {"4": "Hall"},
        }
        assert smart_rooms_to_name(data, options) == ["2"]

    def test_hidden_rooms_are_left_out(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN

        data = self._data(["1", "2"])
        assert smart_rooms_to_name(data, {CONF_SMART_ZONE_HIDDEN: ["2"]}) == ["1"]

    def test_numeric_order(self):
        data = self._data(["12", "3", "21"])
        assert smart_rooms_to_name(data, {}) == ["3", "12", "21"]

    def test_no_map_no_rooms(self):
        """Ids known only from schedules or earlier cleans are on no map:
        nothing can show the user where they are."""
        data = SimpleNamespace(cloud_coordinator=MagicMock(), umf_aligner=None)
        assert smart_rooms_to_name(data, {}) == []


class TestTheGuards:
    """The ways there is no map, each answered without one."""

    def test_an_entry_without_a_blid(self):
        entry = _entry()
        entry.runtime_data.blid = None
        hass, registry, _ = _hass(entity=MagicMock())
        with patch.object(naming_map.er, "async_get", return_value=registry):
            assert naming_map.naming_map_markdown(hass, entry) is None
        registry.async_get_entity_id.assert_not_called()

    def test_a_map_entity_not_in_the_registry(self):
        hass, registry, component = _hass(entity=MagicMock(), entity_id=None)
        with patch.object(naming_map.er, "async_get", return_value=registry):
            assert naming_map.naming_map_markdown(hass, _entry()) is None
        component.get_entity.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_view_answers_404_when_the_map_is_gone(self):
        hass, registry, _ = _hass(entity=None)
        entry = _entry()
        entry.state = naming_map.ConfigEntryState.LOADED
        hass.config_entries.async_get_entry.return_value = entry
        request = MagicMock()
        request.app = {"hass": hass}
        with patch.object(naming_map.er, "async_get", return_value=registry):
            resp = await naming_map.NamingMapView().get(request, "E1")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_no_entity_no_png(self):
        hass, registry, _ = _hass(entity=None)
        with patch.object(naming_map.er, "async_get", return_value=registry):
            assert await naming_map.async_naming_map_png(hass, _entry()) is None
