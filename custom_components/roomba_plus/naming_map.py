"""The map the naming forms show (4.2.19).

THE NUMBERS NEED A PICTURE. Both naming forms ask for a name per number,
and nothing in Home Assistant showed which room a number is: the iRobot
app shows no ids, and a 980 keeps no map at all (@liblit). Sending the
user to the iRobot app to find out is not an answer either -- everything
needed to name a room has to be visible in the integration itself.

Home Assistant renders Markdown images in a flow's description (Homematic
IP, webOS and the HomeKit pairing code all do this). An `<img>` cannot
send an auth header, so the form gets a SIGNED path to an authenticated
view -- `async_sign_path`, the same mechanism Home Assistant uses for
media and camera snapshots -- and the view renders the robot's own map:

- Smart Map robots: the rooms map, every room labelled, unnamed ones
  with their number.
- Robots without a Smart Map (900-series): the Cleaning path map, whose
  areas carry the number their naming field is labelled with.

No map, no form: a form asking for names that cannot be told apart is
the thing this replaces.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiohttp import web
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.image import DATA_COMPONENT as IMAGE_COMPONENT
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api_views import RoombaPlusView
from .const import DOMAIN
from .models import MapCapability

NAMING_MAP_URL = "/api/roomba_plus/{entry_id}/naming_map.png"

#: Long enough to read the form and type a dozen names; a dialog left
#: open longer shows a broken image, and reopening it signs a new link.
LINK_LIFETIME = timedelta(minutes=30)


def _map_unique_id(entry: Any) -> str | None:
    data = getattr(entry, "runtime_data", None)
    blid = getattr(data, "blid", None)
    if not blid:
        return None
    capability = getattr(data, "map_capability", None)
    if capability == MapCapability.SMART:
        return f"roomba_plus_{blid}_rooms_map"
    if capability == MapCapability.EPHEMERAL:
        return f"roomba_plus_{blid}_map"
    return None


def _map_entity(hass: HomeAssistant, entry: Any) -> Any | None:
    """The robot's image entity for naming, if it exists and is running."""
    unique_id = _map_unique_id(entry)
    if unique_id is None:
        return None
    entity_id = er.async_get(hass).async_get_entity_id("image", DOMAIN, unique_id)
    if entity_id is None:
        return None
    component = hass.data.get(IMAGE_COMPONENT)
    # A disabled entity is in the registry but was never added.
    return component.get_entity(entity_id) if component is not None else None


def naming_map_markdown(hass: HomeAssistant, entry: Any) -> str | None:
    """A Markdown image of the robot's map for a form, or None."""
    if _map_entity(hass, entry) is None:
        return None
    path = async_sign_path(
        hass, NAMING_MAP_URL.format(entry_id=entry.entry_id), LINK_LIFETIME
    )
    return f"![map]({path})"


async def async_naming_map_png(hass: HomeAssistant, entry: Any) -> bytes | None:
    entity = _map_entity(hass, entry)
    if entity is None:
        return None
    naming = getattr(entity, "async_naming_image", None)
    if naming is not None:
        result: bytes | None = await naming()
        return result
    image: bytes | None = await entity.async_image()
    return image


class NamingMapView(RoombaPlusView):
    """GET /api/roomba_plus/{entry_id}/naming_map.png

    Authenticated. The naming forms reach it through a signed path, so
    no access token ever appears in a form.
    """

    url = NAMING_MAP_URL
    name = "api:roomba_plus:naming_map"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            return self.json_message("Unknown entry", status_code=404)
        png = await async_naming_map_png(hass, entry)
        if png is None:
            return self.json_message("No map for this robot", status_code=404)
        return web.Response(
            body=png,
            content_type="image/png",
            headers={"Cache-Control": "no-store"},
        )
