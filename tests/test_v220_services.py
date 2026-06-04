"""Tests for F10c (CONF_CLEAN_DELAY_MIN) and F10d (clean_sequence) — v2.2.0.

Pure unit tests — no HA hass fixture required.
"""
from __future__ import annotations

import pytest


# ── F10c — CONF_CLEAN_DELAY_MIN constant ─────────────────────────────────────

class TestConfCleanDelayMin:
    def test_constant_defined(self):
        from custom_components.roomba_plus.const import CONF_CLEAN_DELAY_MIN
        assert CONF_CLEAN_DELAY_MIN == "clean_delay_min"

    def test_default_is_zero(self):
        from custom_components.roomba_plus.const import DEFAULT_CLEAN_DELAY_MIN
        assert DEFAULT_CLEAN_DELAY_MIN == 0


# ── F10d — clean_sequence schema ──────────────────────────────────────────────

class TestCleanSequenceSchema:
    def test_schema_requires_entity_id(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({"target_entity_id": "vacuum.b"})

    def test_schema_requires_target_entity_id(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({"entity_id": "vacuum.a"})

    def test_schema_defaults_require_completed_true(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
        })
        assert result["require_completed"] is True

    def test_schema_defaults_delay_zero(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
        })
        assert result["delay_minutes"] == 0

    def test_schema_accepts_require_completed_false(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "require_completed": False,
        })
        assert result["require_completed"] is False

    def test_schema_accepts_delay_minutes(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "delay_minutes": 15,
        })
        assert result["delay_minutes"] == 15

    def test_delay_clamped_at_60(self):
        import voluptuous as vol
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        with pytest.raises(vol.error.MultipleInvalid):
            _CLEAN_SEQUENCE_SCHEMA({
                "entity_id": "vacuum.a",
                "target_entity_id": "vacuum.b",
                "delay_minutes": 61,
            })

    def test_delay_min_is_zero(self):
        from custom_components.roomba_plus.services import _CLEAN_SEQUENCE_SCHEMA
        result = _CLEAN_SEQUENCE_SCHEMA({
            "entity_id": "vacuum.a",
            "target_entity_id": "vacuum.b",
            "delay_minutes": 0,
        })
        assert result["delay_minutes"] == 0


# ── F10d — service constant ───────────────────────────────────────────────────

class TestCleanSequenceConstant:
    def test_service_constant_defined(self):
        from custom_components.roomba_plus.const import SERVICE_CLEAN_SEQUENCE
        assert SERVICE_CLEAN_SEQUENCE == "clean_sequence"

    def test_service_in_services_module(self):
        # Verify the handler and schema are importable
        from custom_components.roomba_plus.services import (
            _CLEAN_SEQUENCE_SCHEMA,
            async_handle_clean_sequence,
        )
        assert callable(async_handle_clean_sequence)
        assert _CLEAN_SEQUENCE_SCHEMA is not None
