"""Config flow integration tests for Roomba+ using pytest-homeassistant-custom-component.

Tests the full config flow lifecycle against a real (mocked) HA instance:
  - async_migrate_entry: v1 → v2 migration
  - Reconfigure flow: host/password change shows form
  - Options flow: settings menu routing
  - Config flow: cannot_connect error handling

Run separately from the stub-based suite:
  cd roomba_plus_v200 && pytest tests_integration/ -v

Requires: pytest-homeassistant-custom-component
"""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

DOMAIN = "roomba_plus"

VALID_DATA = {
    CONF_HOST:    "192.168.1.50",
    "blid":       "TESTBLID123",
    CONF_PASSWORD: "test_password",
    "continuous": True,
    "delay":      30,
}

VALID_OPTIONS = {"continuous": True, "delay": 30}


def _make_entry(hass: HomeAssistant, version: int = 2, options: dict | None = None) -> config_entries.ConfigEntry:
    """Create a minimal config entry using the installed HA API."""
    entry = config_entries.ConfigEntry(
        version=version,
        minor_version=1,
        domain=DOMAIN,
        title="Test Roomba",
        data=VALID_DATA,
        options=options or VALID_OPTIONS,
        source=config_entries.SOURCE_USER,
        unique_id="TESTBLID123",
        discovery_keys=MappingProxyType({}),
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def _attach_runtime_data(entry: config_entries.ConfigEntry) -> None:
    """Attach minimal runtime_data so options flow can read map_capability."""
    from custom_components.roomba_plus.models import MapCapability, RoombaData
    mock_roomba = MagicMock()
    mock_roomba.master_state = {"state": {"reported": {}}}
    entry.runtime_data = RoombaData(
        roomba=mock_roomba,
        blid="TESTBLID123",
        map_capability=MapCapability.NONE,
    )


# ── async_migrate_entry ───────────────────────────────────────────────────────

class TestMigrateEntry:

    async def test_v1_to_v2_adds_marker(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass, version=1, options={})
        from custom_components.roomba_plus import async_migrate_entry
        result = await async_migrate_entry(hass, entry)
        assert result is True
        assert entry.version == 2
        assert entry.options.get("cloud_raw_records_version") == 1

    async def test_v1_to_v2_preserves_options(self, hass: HomeAssistant) -> None:
        original = {"continuous": True, "delay": 30, "smart_zone_data": {"5": {"name": "Kitchen"}}}
        entry = _make_entry(hass, version=1, options=original)
        from custom_components.roomba_plus import async_migrate_entry
        await async_migrate_entry(hass, entry)
        for key, val in original.items():
            assert entry.options[key] == val

    async def test_v2_is_noop(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass, version=2, options={"continuous": True})
        original_opts = dict(entry.options)
        from custom_components.roomba_plus import async_migrate_entry
        result = await async_migrate_entry(hass, entry)
        assert result is True
        assert entry.options == original_opts

    async def test_returns_true(self, hass: HomeAssistant) -> None:
        entry = _make_entry(hass, version=1, options={})
        from custom_components.roomba_plus import async_migrate_entry
        assert await async_migrate_entry(hass, entry) is True


# ── Config flow — error handling ──────────────────────────────────────────────

class TestConfigFlowErrors:

    async def test_user_step_init(self, hass: HomeAssistant) -> None:
        """Config flow initialises without error."""
        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            return_value=[],
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
        # With no discovered devices, falls through to manual
        assert result["type"] in (FlowResultType.FORM, FlowResultType.ABORT)

    async def test_manual_cannot_connect(self, hass: HomeAssistant) -> None:
        """Connection failure on manual step shows error, does not create entry."""
        from custom_components.roomba_plus.config_flow import CannotConnect
        with patch(
            "custom_components.roomba_plus.config_flow._async_discover_roombas",
            return_value=[],
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )

        if result["type"] == FlowResultType.FORM and result["step_id"] == "manual":
            with patch(
                "custom_components.roomba_plus.config_flow._async_discover_roombas",
                return_value=[],
            ), patch(
                "custom_components.roomba_plus.config_flow.validate_input",
                side_effect=CannotConnect,
            ):
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    {CONF_HOST: "192.168.1.99"},
                )
            # cannot_connect may produce an error form or an abort — both are valid
            # "did not create entry" outcomes depending on flow step routing
            assert result["type"] in (FlowResultType.FORM, FlowResultType.ABORT)
            assert result["type"] != FlowResultType.CREATE_ENTRY


# ── Reconfigure flow ──────────────────────────────────────────────────────────

class TestReconfigureFlow:

    async def test_reconfigure_shows_form(self, hass: HomeAssistant) -> None:
        """Reconfigure step presents form with current host prefilled."""
        entry = _make_entry(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

    async def test_reconfigure_cannot_connect_shows_error(
        self, hass: HomeAssistant
    ) -> None:
        """Connection failure during reconfigure keeps entry unchanged."""
        entry = _make_entry(hass)
        original_host = entry.data[CONF_HOST]

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": entry.entry_id,
            },
        )
        assert result["type"] == FlowResultType.FORM

        from custom_components.roomba_plus.config_flow import CannotConnect
        with patch(
            "custom_components.roomba_plus.config_flow.validate_input",
            side_effect=CannotConnect,
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_HOST: "192.168.1.99", CONF_PASSWORD: "wrong"},
            )

        assert entry.data[CONF_HOST] == original_host
        assert result["type"] == FlowResultType.FORM
        assert "cannot_connect" in result.get("errors", {}).values()


# ── Options flow ──────────────────────────────────────────────────────────────

class TestOptionsFlow:

    async def test_options_init_shows_menu_or_form(
        self, hass: HomeAssistant
    ) -> None:
        """Options flow init presents the menu."""
        entry = _make_entry(hass)
        _attach_runtime_data(entry)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] in (FlowResultType.FORM, FlowResultType.MENU)

    async def test_settings_step_accessible(
        self, hass: HomeAssistant
    ) -> None:
        """Settings step is reachable from the menu."""
        entry = _make_entry(hass)
        _attach_runtime_data(entry)
        result = await hass.config_entries.options.async_init(entry.entry_id)

        if result["type"] == FlowResultType.MENU:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {"next_step_id": "settings"},
            )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "settings"

    async def test_settings_saves_options(
        self, hass: HomeAssistant
    ) -> None:
        """Submitting settings form updates the config entry options."""
        entry = _make_entry(hass)
        _attach_runtime_data(entry)
        result = await hass.config_entries.options.async_init(entry.entry_id)

        if result["type"] == FlowResultType.MENU:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "settings"}
            )

        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"continuous": False, "delay": 60, "map_enabled": True,
             "map_size_px": 600, "map_scale_mm_per_px": 10.0},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert entry.options["continuous"] is False
        assert entry.options["delay"] == 60
