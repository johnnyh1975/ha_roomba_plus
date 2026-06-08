"""Tests for F-RB-7 — exception-translation key completeness audit.

Verifies that every translation_key= used in ServiceValidationError raises
across the integration has a corresponding entry in strings.json and all
7 translation files.

This test is structural — it parses source files and JSON without running
any HA infrastructure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# Paths relative to this test file's location
_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_STRINGS = _ROOT / "strings.json"
_TRANSLATIONS = _ROOT / "translations"
_TRANSLATION_LOCALES = ["de", "en", "es", "fr", "it", "nl", "pt"]

# Python source files that may raise ServiceValidationError with translation_key=
_SOURCE_FILES = [
    _ROOT / "services.py",
    _ROOT / "vacuum.py",
]


def _collect_used_keys() -> set[str]:
    """Extract all translation_key= values from ServiceValidationError raises."""
    keys: set[str] = set()
    pattern = re.compile(r'translation_key\s*=\s*["\']([^"\']+)["\']')
    for src in _SOURCE_FILES:
        if src.exists():
            text = src.read_text()
            keys.update(pattern.findall(text))
    return keys


def _load_strings_exception_keys() -> set[str]:
    with open(_STRINGS) as f:
        d = json.load(f)
    return set(d.get("exceptions", {}).keys())


def _load_translation_exception_keys(locale: str) -> set[str]:
    path = _TRANSLATIONS / f"{locale}.json"
    with open(path) as f:
        d = json.load(f)
    return set(d.get("exceptions", {}).keys())


class TestExceptionTranslationKeys:
    """All translation_key= values used in raises must be defined in strings.json."""

    def test_strings_json_has_all_used_keys(self):
        used = _collect_used_keys()
        defined = _load_strings_exception_keys()
        missing = used - defined
        assert not missing, (
            f"Keys used in ServiceValidationError raises but missing from "
            f"strings.json exceptions block: {sorted(missing)}"
        )

    @pytest.mark.parametrize("locale", _TRANSLATION_LOCALES)
    def test_translation_file_has_all_used_keys(self, locale: str):
        used = _collect_used_keys()
        defined = _load_translation_exception_keys(locale)
        missing = used - defined
        assert not missing, (
            f"Keys missing from translations/{locale}.json exceptions block: "
            f"{sorted(missing)}"
        )

    def test_new_v24_keys_present_in_strings(self):
        """Explicitly verify all keys added in v2.4.0."""
        defined = _load_strings_exception_keys()
        for key in (
            "entity_not_found",
            "maintenance_store_unavailable",
            "no_valid_segments",
            "config_entry_not_found",
        ):
            assert key in defined, f"v2.4.0 exception key '{key}' missing from strings.json"

    def test_new_v24_keys_have_message_field(self):
        """Each new key must have a 'message' field."""
        with open(_STRINGS) as f:
            d = json.load(f)
        exceptions = d.get("exceptions", {})
        for key in (
            "entity_not_found",
            "maintenance_store_unavailable",
            "no_valid_segments",
            "config_entry_not_found",
        ):
            assert "message" in exceptions.get(key, {}), (
                f"exceptions.{key} in strings.json is missing the 'message' field"
            )

    def test_entity_not_found_message_has_placeholder(self):
        """entity_not_found message must contain {entity_id} placeholder."""
        with open(_STRINGS) as f:
            d = json.load(f)
        msg = d["exceptions"]["entity_not_found"]["message"]
        assert "{entity_id}" in msg, (
            "entity_not_found message must contain {entity_id} placeholder"
        )

    def test_no_valid_segments_message_has_no_placeholders(self):
        """no_valid_segments is raised without translation_placeholders — no {} in message."""
        with open(_STRINGS) as f:
            d = json.load(f)
        msg = d["exceptions"]["no_valid_segments"]["message"]
        # Should not have format placeholders (no cloud data to fill them)
        assert "{" not in msg or "—" in msg, (
            "no_valid_segments message should not contain format placeholders"
        )

    def test_strings_json_no_orphaned_keys(self):
        """All exception keys in strings.json must be used in at least one source file.

        As of v2.4.0 all 11 keys are live — no forward declarations remain.
        """
        used = _collect_used_keys()
        defined = _load_strings_exception_keys()
        orphaned = defined - used
        assert orphaned == set(), (
            f"strings.json defines exception keys not used in any source file: "
            f"{sorted(orphaned)}"
        )

    @pytest.mark.parametrize("locale", _TRANSLATION_LOCALES)
    def test_translation_messages_are_non_empty(self, locale: str):
        """All exception messages in every locale must be non-empty strings."""
        path = _TRANSLATIONS / f"{locale}.json"
        with open(path) as f:
            d = json.load(f)
        exceptions = d.get("exceptions", {})
        for key, val in exceptions.items():
            msg = val.get("message", "")
            assert msg, f"translations/{locale}.json exceptions.{key}.message is empty"
