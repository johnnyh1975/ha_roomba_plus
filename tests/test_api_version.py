"""The `X-Roomba-Plus-Api-Version` header and its documented rule (I6).

The card will compare this number before relying on a behaviour, so the
number, the history table in docs/API.md and what the views send must
agree. Nothing tested the header at all before.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.roomba_plus import api_views

API_MD = Path(__file__).parent.parent / "docs" / "API.md"


def test_the_history_table_ends_at_the_current_version():
    text = API_MD.read_text(encoding="utf-8")
    section = text[text.index("## API version"):text.index("## Endpoints")]
    versions = [int(v) for v in re.findall(r"^\| (\d+) \| ", section, re.M)]
    assert versions == list(range(1, len(versions) + 1)), "one row per version, in order"
    assert versions[-1] == api_views.ROOMBA_PLUS_API_VERSION


@pytest.mark.asyncio
async def test_every_json_response_carries_it():
    hass = MagicMock()
    hass.config_entries.async_get_entry.return_value = None
    request = MagicMock()
    request.app = {"hass": hass}
    request.query = {}
    resp = await api_views.MissionHistoryView().get(request, "nope")

    assert resp.status == 404
    assert resp.headers[api_views.API_VERSION_HEADER] == str(
        api_views.ROOMBA_PLUS_API_VERSION
    )


def test_every_view_inherits_the_stamping_base():
    """A view built on HomeAssistantView directly would send no header."""
    from homeassistant.components.http import HomeAssistantView

    from custom_components.roomba_plus import naming_map

    views = [
        obj
        for module in (api_views, naming_map)
        for obj in vars(module).values()
        if isinstance(obj, type) and issubclass(obj, HomeAssistantView)
        and obj.__module__ == module.__name__
        and obj is not api_views.RoombaPlusView
    ]
    assert len(views) >= 9
    for view in views:
        assert issubclass(view, api_views.RoombaPlusView), view.__name__
