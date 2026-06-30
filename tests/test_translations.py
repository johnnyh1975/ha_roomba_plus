"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import json
import re
from pathlib import Path
import pytest
import importlib
from unittest.mock import MagicMock


_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_STRINGS = _ROOT / "strings.json"
_TRANSLATIONS = _ROOT / "translations"
_TRANSLATION_LOCALES = ["de", "en", "es", "fr", "it", "nl", "pt"]
_SOURCE_FILES = [
    _ROOT / "services.py",
    _ROOT / "vacuum.py",
]
TRANS_DIR = Path(__file__).parent.parent / "custom_components" / "roomba_plus" / "translations"
ASCII_KEY_RE = re.compile(r'^[a-z0-9_]+$')
LANG_NEUTRAL = {"SNR", "Status", "Mission – ID", "{name}"}
TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components" / "roomba_plus" / "translations"
)


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


def _slugify(text: str) -> str:
    """Minimal slug — mirrors HA's homeassistant.util.slugify behaviour."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[\s_-]+", "_", text)


def _load_translations() -> dict[str, dict]:
    data = {}
    for path in TRANS_DIR.glob("*.json"):
        data[path.stem] = json.loads(path.read_text())
    return data


def _entity_sensor_keys(translations: dict) -> dict[str, dict[str, str]]:
    """Return {lang: {translation_key: name}} for sensor domain."""
    result = {}
    for lang, blob in translations.items():
        names = {}
        for domain in ("sensor", "binary_sensor", "switch", "select", "button", "image"):
            for key, entry in blob.get("entity", {}).get(domain, {}).items():
                if "name" in entry:
                    names[key] = entry["name"]
        result[lang] = names
    return result


def _make_entity(vacuum_state: dict):
    """Build a minimal IRobotEntity-like object with the given MQTT state."""
    from custom_components.roomba_plus.entity import IRobotEntity
    entity = object.__new__(IRobotEntity)
    entity._blid = "test"
    entity._roomba = MagicMock()
    # vacuum_state is normally set in __init__ via roomba_reported_state(roomba).
    # Set it directly since we bypass __init__ with object.__new__.
    entity.vacuum_state = vacuum_state
    return entity


def _find_desc(key: str):
    """Return the RoombaSensorDescription with the given key."""
    from custom_components.roomba_plus.sensor import SENSORS
    for desc in SENSORS:
        if desc.key == key:
            return desc
    return None


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


class TestTranslationNullValues:
    """Guard against null values in translation files that crash HA 2026.6+.

    HA 2026.6 tightened _validate_placeholders to call string.Formatter().parse()
    on every translation value. A null/None value causes:
      TypeError: expected str, got NoneType
    crashing the entire integration on startup before async_setup_entry runs.

    Root cause in v2.4.0: schedule_suboptimal issue had "fix_flow": null.
    Fix: never store null values in translation files.
    """

    @pytest.mark.parametrize("locale", _TRANSLATION_LOCALES)
    def test_no_null_values_in_translation_file(self, locale: str):
        """Every value in every translation file must be a non-null string."""
        path = _TRANSLATIONS / f"{locale}.json"
        with open(path) as f:
            d = json.load(f)

        def _check_no_nulls(obj, path: str) -> list[str]:
            nulls = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    nulls.extend(_check_no_nulls(v, f"{path}.{k}"))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    nulls.extend(_check_no_nulls(v, f"{path}[{i}]"))
            elif obj is None:
                nulls.append(path)
            return nulls

        nulls = _check_no_nulls(d, f"{locale}")
        assert not nulls, (
            f"translations/{locale}.json contains null values that will crash "
            f"HA 2026.6+ translation loader: {nulls}"
        )

    def test_no_null_values_in_strings_json(self):
        """strings.json must contain no null values."""
        with open(_STRINGS) as f:
            d = json.load(f)

        def _check(obj, path):
            nulls = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    nulls.extend(_check(v, f"{path}.{k}"))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    nulls.extend(_check(v, f"{path}[{i}]"))
            elif obj is None:
                nulls.append(path)
            return nulls

        nulls = _check(d, "strings.json")
        assert not nulls, (
            f"strings.json contains null values that will crash "
            f"HA 2026.6+ translation loader: {nulls}"
        )

    def test_ha_translation_validator_simulation(self):
        """Simulate exactly what HA 2026.6 _validate_placeholders does."""
        import string as _string

        with open(_STRINGS) as f:
            d = json.load(f)

        def _flatten(obj, prefix=""):
            items = {}
            if isinstance(obj, dict):
                for k, v in obj.items():
                    items.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    items.update(_flatten(v, f"{prefix}[{i}]"))
            else:
                items[prefix] = obj
            return items

        flat = _flatten(d)
        errors = []
        for key, val in flat.items():
            if val is not None:  # HA skips None? — No, it doesn't in 2026.6
                try:
                    list(_string.Formatter().parse(str(val) if not isinstance(val, str) else val))
                except (TypeError, ValueError) as e:
                    errors.append(f"{key}={val!r}: {e}")
            else:
                errors.append(f"{key}=None: would raise TypeError in HA 2026.6 translation loader")
        assert not errors, f"strings.json has values that crash HA translation loader: {errors}"


class TestTranslationKeyFormat:
    """All translation keys must be ASCII a-z/0-9/underscore."""

    def test_sensor_description_keys_are_ascii(self):
        from custom_components.roomba_plus.sensor import SENSORS as SENSOR_DESCRIPTIONS
        bad = []
        for desc in SENSOR_DESCRIPTIONS:
            tk = getattr(desc, "translation_key", None) or desc.key
            if not ASCII_KEY_RE.match(tk):
                bad.append(f"sensor/{tk!r}")
        assert not bad, f"Non-ASCII translation keys:\n" + "\n".join(bad)

    def test_all_translation_file_keys_are_ascii(self):
        translations = _load_translations()
        bad = []
        for lang, blob in translations.items():
            for domain in ("sensor", "binary_sensor", "switch", "select", "button"):
                for key in blob.get("entity", {}).get(domain, {}):
                    if not ASCII_KEY_RE.match(key):
                        bad.append(f"{lang}/{domain}/{key!r}")
        assert not bad, f"Non-ASCII keys in translation files:\n" + "\n".join(bad)


class TestSlugStability:
    """Slugifying any translated name must not produce a different result
    across languages — i.e. the English slug must equal the non-English slug
    when both are slugified.  This catches cases where HA would generate
    locale-specific entity_ids."""

    def test_translated_names_slugify_consistently(self):
        translations = _load_translations()
        by_lang = _entity_sensor_keys(translations)
        en_names = by_lang.get("en", {})
        bad = []
        for lang, names in by_lang.items():
            if lang == "en":
                continue
            for key, name in names.items():
                if name in LANG_NEUTRAL:
                    continue
                en_name = en_names.get(key, "")
                en_slug = _slugify(en_name)
                loc_slug = _slugify(name)
                # They SHOULD differ (that's the point of translation), but
                # neither slug must contain non-ASCII after slugify — that
                # would indicate a character that survives NFKD but is not
                # ASCII, which HA drops silently and may produce collisions.
                if not ASCII_KEY_RE.match(loc_slug.replace(" ", "_")):
                    bad.append(
                        f"{lang}/{key}: {name!r} → slug {loc_slug!r} contains non-ASCII"
                    )
                # The translation key itself (= entity_id suffix) is always
                # the EN key, never the translated slug.  Verify the key is
                # not accidentally the translated slug.
                if loc_slug == key and en_slug != key:
                    bad.append(
                        f"{lang}/{key}: translated slug matches key — possible locale collision"
                    )
        assert not bad, "Slug stability issues:\n" + "\n".join(bad)


class TestNoHardcodedNonEnglishNames:
    """No translation file for a non-English language should use the
    English string verbatim (except known-neutral abbreviations).
    This catches missing translations early."""

    def test_no_missing_translations_in_de(self):
        translations = _load_translations()
        en_names = _entity_sensor_keys(translations).get("en", {})
        de_names = _entity_sensor_keys(translations).get("de", {})
        bad = []
        for key, en_name in en_names.items():
            de_name = de_names.get(key, "")
            if de_name and de_name == en_name and de_name not in LANG_NEUTRAL:
                bad.append(f"  {key}: DE name identical to EN ({en_name!r})")
        assert not bad, (
            "These keys have no German translation (DE == EN):\n" + "\n".join(bad)
        )

    def test_sensor_description_translation_keys_exist_in_strings(self):
        """Every translation_key in SENSORS + CLOUD_HISTORY_SENSORS must be in en.json.

        SC1 (v3.0): CLOUD_RAW_SENSORS removed — deprecated sensors deactivated.
        """
        from custom_components.roomba_plus.sensor import (
            SENSORS as SENSOR_DESCRIPTIONS,
            CLOUD_HISTORY_SENSORS,
        )
        translations = _load_translations()
        en_sensor = translations.get("en", {}).get("entity", {}).get("sensor", {})
        bad = []
        for desc in list(SENSOR_DESCRIPTIONS) + list(CLOUD_HISTORY_SENSORS):
            tk = getattr(desc, "translation_key", None)
            if tk and tk not in en_sensor:
                bad.append(f"  sensor/{tk!r} used in code but missing from en.json")
        assert not bad, "Missing translation entries:\n" + "\n".join(bad)

    def test_strings_json_matches_en_json_for_sensors(self):
        """strings.json entity.sensor must be a superset of en.json entity.sensor.

        TRN6 (v2.7.0): strings.json is the HA-facing contract; en.json is the
        English translation. Any key present in en.json must also be present in
        strings.json to ensure the card editor and translation tools see it.
        """
        import json
        from pathlib import Path
        root = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
        strings = json.loads((root / "strings.json").read_text())
        en = json.loads((root / "translations" / "en.json").read_text())

        strings_keys = set(strings.get("entity", {}).get("sensor", {}).keys())
        en_keys = set(en.get("entity", {}).get("sensor", {}).keys())
        missing = sorted(en_keys - strings_keys)
        assert not missing, (
            f"Keys in en.json but missing from strings.json: {missing}\n"
            "Add them to strings.json entity.sensor."
        )


class TestTranslationKeys:
    NEW_KEYS = [
        "nav_landmark_quality",
        "nav_good_landmarks",
        "optical_dirt_detections",
        "piezo_dirt_detections",
        "nav_orientations",
    ]

    def test_keys_in_en_json(self):
        path = TRANSLATIONS_DIR / "en.json"
        data = json.loads(path.read_text())
        sensor_keys = data["entity"]["sensor"].keys()
        for key in self.NEW_KEYS:
            assert key in sensor_keys, f"Missing {key} in en.json"

    def test_keys_in_strings_json(self):
        path = TRANSLATIONS_DIR.parent / "strings.json"
        data = json.loads(path.read_text())
        sensor_keys = data["entity"]["sensor"].keys()
        for key in self.NEW_KEYS:
            assert key in sensor_keys, f"Missing {key} in strings.json"

    def test_all_7_languages_have_keys(self):
        langs = ["de", "es", "fr", "it", "nl", "pt"]
        for lang in langs:
            path = TRANSLATIONS_DIR / f"{lang}.json"
            data = json.loads(path.read_text())
            sensor_keys = data["entity"]["sensor"].keys()
            for key in self.NEW_KEYS:
                assert key in sensor_keys, f"Missing {key} in {lang}.json"


# ═══════════════════════════════════════════════════════════════════════
# Systemic translation_key guard (added after the v2.9.1 bug hunt found
# translations/fr.json had "error_reçurrence"/"cancellation_reçurrence"
# instead of the ASCII "error_recurrence"/"cancellation_recurrence" used
# everywhere else — a typo TestTranslationKeys above would NOT have
# caught, since it only checks entity.sensor and a hardcoded key list.
# This scans every translation_key="..." literal across the entire
# integration source and verifies it exists SOMEWHERE in strings.json and
# every one of the 7 language files (existence anywhere in the tree, not
# scoped to one section — translation_key is used for entities AND
# Repair Issues, which live under different top-level JSON keys).
# ═══════════════════════════════════════════════════════════════════════

def _all_translation_keys_used_in_source() -> set[str]:
    keys: set[str] = set()
    pattern = re.compile(r'translation_key=["\']([a-zA-Z0-9_]+)["\']')
    for py_file in _ROOT.glob("*.py"):
        keys.update(pattern.findall(py_file.read_text()))
    return keys


def _json_contains_key_anywhere(data, key: str) -> bool:
    if isinstance(data, dict):
        if key in data:
            return True
        return any(_json_contains_key_anywhere(v, key) for v in data.values())
    if isinstance(data, list):
        return any(_json_contains_key_anywhere(v, key) for v in data)
    return False


class TestAllTranslationKeysExistEverywhere:
    """Every translation_key="..." used in source must exist in strings.json
    and in all 7 language files — anywhere in the JSON tree."""

    @pytest.fixture(scope="class")
    def used_keys(self):
        keys = _all_translation_keys_used_in_source()
        assert keys, "Expected to find at least one translation_key= in source"
        return keys

    def test_keys_exist_in_strings_json(self, used_keys):
        data = json.loads(_STRINGS.read_text())
        missing = {k for k in used_keys if not _json_contains_key_anywhere(data, k)}
        assert not missing, f"translation_key(s) missing from strings.json: {sorted(missing)}"

    @pytest.mark.parametrize("locale", _TRANSLATION_LOCALES)
    def test_keys_exist_in_every_locale(self, used_keys, locale):
        path = _TRANSLATIONS / f"{locale}.json"
        data = json.loads(path.read_text())
        missing = {k for k in used_keys if not _json_contains_key_anywhere(data, k)}
        assert not missing, f"translation_key(s) missing from {locale}.json: {sorted(missing)}"

    @pytest.mark.parametrize("locale", _TRANSLATION_LOCALES)
    def test_locale_keys_are_ascii_slugs(self, locale):
        """Catches the exact fr.json class of bug: a non-ASCII character
        smuggled into what must be an ASCII English slug (e.g. 'reçurrence'
        instead of 'recurrence'), regardless of whether the source file
        still references the correct spelling."""
        used_keys = _all_translation_keys_used_in_source()
        path = _TRANSLATIONS / f"{locale}.json"
        data = json.loads(path.read_text())

        def collect_keys(node, acc):
            if isinstance(node, dict):
                for k, v in node.items():
                    acc.add(k)
                    collect_keys(v, acc)
            elif isinstance(node, list):
                for v in node:
                    collect_keys(v, acc)

        all_keys_in_file: set[str] = set()
        collect_keys(data, all_keys_in_file)

        # Any key in this locale file containing a non-ASCII character is a
        # smuggled-typo candidate (e.g. 'reçurrence' instead of 'recurrence').
        # Mixed-case English keys (state/option value labels like "Automatic",
        # "Dirty Pause") are legitimate and plain ASCII, so they pass.
        suspects = {k for k in all_keys_in_file if not k.isascii()}
        stray = suspects - used_keys
        assert not stray, (
            f"{locale}.json has non-ASCII dict key(s) that don't match any "
            f"translation_key in source — likely a typo'd key: {sorted(stray)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# PRIVACY-DOC (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

class TestPrivacyDoc:
    """PRIVACY-DOC (v3.1.0) — cloud_credentials step must contain privacy statement."""

    LANGS = ["en", "de", "fr", "it", "es", "nl", "pt"]
    PRIVACY_KEYWORDS = ["MQTT", "locally", "local"]  # EN anchor words

    def _load(self, lang: str) -> dict:
        import json, os
        base = os.path.join(
            os.path.dirname(__file__),
            "..", "custom_components", "roomba_plus", "translations"
        )
        with open(os.path.join(base, f"{lang}.json")) as f:
            return json.load(f)

    def test_strings_json_cloud_credentials_contains_mqtt(self):
        """strings.json cloud_credentials description must mention MQTT."""
        import json, os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "custom_components", "roomba_plus", "strings.json"
        )
        with open(path) as f:
            data = json.load(f)
        desc = data["config"]["step"]["cloud_credentials"]["description"]
        assert "MQTT" in desc, "strings.json privacy statement missing MQTT keyword"

    def test_all_languages_cloud_credentials_description_updated(self):
        """Every translation must have an updated cloud_credentials description."""
        for lang in self.LANGS:
            data = self._load(lang)
            desc = data["config"]["step"]["cloud_credentials"]["description"]
            # Must be substantially longer than the old one-liner (>100 chars)
            assert len(desc) > 100, (
                f"{lang}: cloud_credentials description too short ({len(desc)} chars) "
                "— privacy statement may be missing"
            )


# ─────────────────────────────────────────────────────────────────────────────
# LIFECYCLE-DOC (v3.1.0)
# ─────────────────────────────────────────────────────────────────────────────

class TestLifecycleDoc:
    """LIFECYCLE-DOC (v3.1.0) — verify lifecycle section exists in TROUBLESHOOTING."""

    def test_troubleshooting_contains_lifecycle_section(self):
        """TROUBLESHOOTING.md must contain the robot replacement section."""
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "..", "docs", "TROUBLESHOOTING.md"
        )
        with open(path) as f:
            content = f.read()
        assert "Replacing or selling your robot" in content
        assert "Factory reset" in content
        assert "format=export" in content
