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
import ast
import pathlib


_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_STRINGS = _ROOT / "strings.json"
_TRANSLATIONS = _ROOT / "translations"
_TRANSLATION_LOCALES = ["de", "en", "es", "fr", "it", "nl", "pt", "pl"]
# The modules that raise translated EXCEPTIONS. Not every module, and
# not a glob: entity modules use translation_key= for entity names,
# which live in a different block of strings.json entirely, so globbing
# mixes two unrelated namespaces and reports 190 false positives.
#
# room_cleaning.py was added when the Classic send path moved there
# (this session) -- the list had gone stale the moment a fourth module
# started raising translated errors, and reported its key as orphaned
# while it was in active use.
#
# Still hand-maintained, and that is a real weakness. The alternative
# tried here was worse.
# EVERY MODULE, NOT FOUR NAMED ONES.
#
# This was a hardcoded list, and a hardcoded list of source files is a
# list that goes stale silently: a `ServiceValidationError` raised in
# any other module made its key look unused, so the orphan check
# reported a live key as dead. That happened the first time an
# exception was raised from switch.py.
#
# The failure mode is the wrong way round, which is what makes it
# worth fixing rather than extending: the check fires on correct code
# and stays quiet about a genuinely orphaned key in an unlisted file.
_SOURCE_FILES = sorted(_ROOT.glob("*.py"))
# EVERY MODULE, BUT ONLY EXCEPTION RAISES.
#
# This list used to name four files by hand, which made any
# ServiceValidationError raised elsewhere invisible in BOTH directions:
# its key counted as undefined when missing from strings.json, and as
# ORPHANED when present. A correctly translated `prime_not_connected` in
# switch.py came back as an unused key.
#
# Globbing alone is worse, not better: `translation_key=` is what every
# ENTITY uses too, so a plain regex over all files finds 243 keys and
# demands they all live in the `exceptions` block. The four-file list
# was a crude way of restricting the scan to files that raise.
#
# So the restriction moves from WHICH FILES to WHICH CALLS: parse each
# module and take `translation_key` only from arguments to an exception
# constructor. 19 ms across 78 files, once at collection.
TRANS_DIR = Path(__file__).parent.parent / "custom_components" / "roomba_plus" / "translations"
ASCII_KEY_RE = re.compile(r'^[a-z0-9_]+$')
# Words that are genuinely identical across languages. "Filter" is the
# German, Dutch and English spelling of the same word -- flagging it as
# untranslated would push someone towards inventing a worse German word
# to satisfy the check.
LANG_NEUTRAL = {"SNR", "Status", "Mission – ID", "{name}", "Filter", "Filtro", "Filtr"}
TRANSLATIONS_DIR = (
    Path(__file__).parent.parent
    / "custom_components" / "roomba_plus" / "translations"
)


#: Which constructors count as "an exception the user will read".
#:
#: Kept explicit rather than "anything ending in Error": a list that
#: matched by suffix would pull in library exceptions this project only
#: catches, and the point of the check is our own translated messages.
#:
#: Two were missing on the first pass and showed up as orphaned keys
#: that were in fact live -- `ConfigEntryAuthFailed` and `UpdateFailed`
#: both carry translated messages in cloud_coordinator.py.
_EXCEPTION_CALLS = {
    "ServiceValidationError",
    "HomeAssistantError",
    "ConfigEntryNotReady",
    "ConfigEntryAuthFailed",
    "UpdateFailed",
}


def _collect_used_keys() -> set[str]:
    """`translation_key` values passed to an exception constructor."""
    import ast

    keys: set[str] = set()
    for src in _SOURCE_FILES:
        if not src.exists():
            continue
        for node in ast.walk(ast.parse(src.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in _EXCEPTION_CALLS:
                continue
            for kw in node.keywords:
                if kw.arg == "translation_key" and isinstance(kw.value, ast.Constant):
                    keys.add(kw.value.value)
                elif (
                    kw.arg == "translation_key"
                    and isinstance(kw.value, ast.Call)
                    and ast.unparse(kw.value.func) == "cloud_errors.translation_key"
                ):
                    # One key per reason, chosen at runtime (4.3). A raise
                    # through cloud_errors can produce any of them.
                    from roombapy_prime import CloudErrorReason

                    keys |= {f"cloud_{r.value}" for r in CloudErrorReason}
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


# hassfest's own rule (script/hassfest/translations.py,
# RE_PLACEHOLDER_IN_SINGLE_QUOTES). The frontend formats strings as ICU
# messages, where an apostrophe directly before "{" starts a quoted literal:
# "'{command}'" renders the braces instead of the value. hassfest only
# checks strings.json and en.json; the other locales break the same way, so
# all of them are held to it.
_HASSFEST_SINGLE_QUOTED_PLACEHOLDER = re.compile(r"'{\w+}'")


def _single_quoted_placeholders(obj, prefix: str = "") -> list[str]:
    if isinstance(obj, dict):
        return [
            hit
            for k, v in obj.items()
            for hit in _single_quoted_placeholders(v, f"{prefix}.{k}" if prefix else k)
        ]
    if isinstance(obj, str) and _HASSFEST_SINGLE_QUOTED_PLACEHOLDER.search(obj):
        return [f"{prefix}: {obj}"]
    return []


class TestNoPlaceholderInSingleQuotes:
    """hassfest rejected two exception messages in CI because they
    wrapped their placeholder in single quotes."""

    @pytest.mark.parametrize(
        "path",
        [_STRINGS] + [_TRANSLATIONS / f"{loc}.json" for loc in _TRANSLATION_LOCALES],
        ids=lambda p: p.name,
    )
    def test_no_placeholder_in_single_quotes(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert not _single_quoted_placeholders(data)

    def test_the_rule_catches_the_original_message(self) -> None:
        original = {"exceptions": {"command_not_delivered": {"message": (
            "The '{command}' command was not accepted for delivery"
        )}}}
        assert _single_quoted_placeholders(original)


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

    def test_all_8_languages_have_keys(self):
        langs = ["de", "es", "fr", "it", "nl", "pt", "pl"]
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


class TestLocalesAreActuallyTranslated:
    """@dixi83 sent a one-line PR fixing a Dutch label. Looking at the
    file around it turned up **36 long strings still in English** across
    five locales — config dialogs, service descriptions, and six
    exception messages a user sees when something goes wrong.

    Nothing caught them, because the key check passes: every locale had
    every key. Structural completeness and translation are different
    properties, and only one was being measured.
    """

    def test_no_locale_leaves_long_strings_in_english(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/check_translations.py"],
            capture_output=True, text=True,
        )

        assert "Untranslated values found" not in result.stdout, result.stdout
        assert result.returncode == 0

    def test_the_allow_list_stays_short(self):
        """A long allow-list turns this check off by attrition, which is
        how the originals survived."""
        from importlib import util
        import pathlib

        spec = util.spec_from_file_location(
            "ct", pathlib.Path("scripts/check_translations.py")
        )
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert len(module.IDENTICAL_IS_FINE) <= 20

    def test_the_threshold_is_justified(self):
        """26 characters, set from the shortest real miss found when
        this was written — not picked to make the check pass."""
        from importlib import util
        import pathlib

        spec = util.spec_from_file_location(
            "ct", pathlib.Path("scripts/check_translations.py")
        )
        module = util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.UNTRANSLATED_MIN_LENGTH <= 26


def _is_tier_pair(keys: list[str]) -> bool:
    """`x` and `prime_x` may share a name, and should.

    They are the Classic and Prime forms of one concept, and a robot
    only ever has one of them -- so a user sees a single "Firmware
    version" either way. Giving them different names would be the bug.

    This is deliberately narrow: it collapses the `prime_` prefix and
    nothing else, so two genuinely different entities that happen to
    collide still fail. That is how the Spanish bin/tank pair was
    found -- `bin_present` and `mop_tank_present_direct` both read
    "Depósito presente", on Combo robots that carry both.
    """
    stripped = {k.removeprefix("prime_") for k in keys}
    return len(stripped) == 1


class TestEntityNamesAreDistinctWithinAPlatform:
    """Two entities on one platform must not share a display name.

    `image.map` and `image.cleaning_map` were BOTH called "Cleaning
    map" -- in seven of the eight locales. @pk-1966 reported that "the
    cleaning map just shows a dot in a blank white space" and was
    right to call it that: it is the name the interface gave him. He
    then spent a round trying to work out how it differed from the
    coverage map, which is a question the names made unanswerable.

    A duplicate name is worse than a bad name. A bad one teaches you
    something once; a duplicate makes two things indistinguishable in
    every dropdown, automation picker and dashboard editor.
    """

    def test_no_platform_has_two_entities_with_the_same_name(self):
        offenders = []
        for path in sorted((_ROOT / "translations").glob("*.json")):
            entities = json.loads(path.read_text(encoding="utf-8")).get("entity", {})
            for platform, keys in entities.items():
                names: dict[str, list[str]] = {}
                for key, value in keys.items():
                    name = value.get("name") if isinstance(value, dict) else None
                    if name:
                        names.setdefault(name, []).append(key)
                for name, sharing in names.items():
                    if len(sharing) > 1 and not _is_tier_pair(sharing):
                        offenders.append(
                            f"{path.stem}/{platform}: {sharing} all called {name!r}"
                        )

        assert not offenders, "Duplicate entity names:\n  " + "\n  ".join(offenders)

    def test_every_locale_names_the_same_image_entities(self):
        """A locale missing one is how a duplicate hides: the entity
        falls back to its key and looks distinct in English only."""
        reference = set(
            json.loads((_ROOT / "translations" / "en.json").read_text(encoding="utf-8"))
            ["entity"]["image"]
        )
        for path in sorted((_ROOT / "translations").glob("*.json")):
            keys = set(
                json.loads(path.read_text(encoding="utf-8"))["entity"]["image"]
            )
            assert keys == reference, f"{path.stem} image keys differ from en"


class TestImageNamesMatchWhatEachTierGets:
    """The five image keys are not evenly split between the tiers, and
    a name has to be honest for every tier that uses it.

    Prime (CLOUD_ONLY):  raw_map, rooms_map, cleaning_map
    Classic:             map, coverage_map, rooms_map

    Only `rooms_map` is shared -- and it does NOT behave the same on
    both. Classic's is a static room-polygon image; Prime's carries
    the live coverage/trajectory layers too, because the map card
    takes one raster source rather than a stack of entities. So it was
    briefly called "Room layout", which promised a staticness only one
    tier has.
    """

    @staticmethod
    def _names(locale: str = "en") -> dict[str, str]:
        path = _ROOT / "translations" / f"{locale}.json"
        return {
            key: value["name"]
            for key, value in json.loads(path.read_text(encoding="utf-8"))
            ["entity"]["image"].items()
        }

    def test_the_shared_key_does_not_promise_static(self):
        """`rooms_map` renders on both tiers and is live on one."""
        for locale in ("en", "de"):
            name = self._names(locale)["rooms_map"].lower()
            for promise in ("layout", "static", "aufteilung", "statisch"):
                assert promise not in name, (
                    f"{locale}: rooms_map is called {name!r}, which claims a "
                    "staticness Prime's version does not have"
                )

    def test_coverage_says_what_it_measures(self):
        """"Coverage map" read as "which rooms are done". It is a
        heatmap of driven-over cells -- traffic, not completion --
        which is what @pk-1966 could not reconcile against the room
        map. The word has to be in the name."""
        for locale, word in (("en", "heatmap"), ("de", "heatmap")):
            assert word in self._names(locale)["coverage_map"].lower()

    def test_the_two_live_images_are_not_called_the_same_thing(self):
        """`map` (Classic) and `cleaning_map` (Prime) were both
        "Cleaning map" in seven of eight locales. No robot has both,
        but every document, dashboard and support answer had to."""
        for path in sorted((_ROOT / "translations").glob("*.json")):
            names = self._names(path.stem)
            assert names["map"] != names["cleaning_map"], path.stem


class TestStringsJsonAgreesWithEnglish:
    """`strings.json` and `translations/en.json` must not disagree.

    Home Assistant serves `translations/en.json` to English users;
    `strings.json` is the source the other locales are generated from
    and what a developer reads. When they diverge, the file everyone
    looks at is not the file anyone sees.

    Found by @mdarocha (PR #100) while settling a naming question: he
    assumed `prime_part_dirt_bag` and `part_dirt_bag` disagreed with
    each other. They did not — `strings.json` disagreed with all eight
    shipped locales, which already said "Dust Bag".

    The duplicate-name check added in a43 could not see this: it
    compares entities within one platform in one file, and this is the
    same entity across two files.
    """

    @staticmethod
    def _mismatches():
        import json

        strings = json.loads(_STRINGS.read_text(encoding="utf-8"))
        english = json.loads(
            (_ROOT / "translations" / "en.json").read_text(encoding="utf-8")
        )

        found: list[tuple[str, str, str]] = []

        def walk(a, b, path=""):
            if isinstance(a, dict) and isinstance(b, dict):
                for key in a:
                    if key in b:
                        walk(a[key], b[key], f"{path}.{key}")
            elif isinstance(a, str) and isinstance(b, str) and a != b:
                found.append((path, a, b))

        walk(strings, english)
        return found

    def test_no_entity_name_disagrees(self):
        """Names are what a user sees, so a divergence here is visible
        in a way a description or a flow string is not."""
        offenders = [
            (p, a, b) for p, a, b in self._mismatches()
            if p.endswith(".name") and ".entity." in p
        ]

        assert not offenders, (
            "strings.json and translations/en.json give different names:\n"
            + "\n".join(f"  {p}\n    strings: {a}\n    en:      {b}"
                        for p, a, b in offenders)
        )


# ── formerly tests/test_guard_exception_translations.py ─────────────────────────
#
# Guard: quality scale Gold, exception-translations.
#
# Every Home Assistant exception the integration raises carries a
# translation key; every key exists in strings.json and in all eight
# translations; the placeholders the code passes match the message exactly,
# in both directions; and Home Assistant itself can format every message.
#
# The placeholder check exists because eight call sites once named a key
# whose text needed {entity_id} and passed nothing — the user saw a broken
# message instead of which entity was meant.

PKG = pathlib.Path("custom_components/roomba_plus")


_HA_EXCEPTIONS = {"HomeAssistantError", "ServiceValidationError", "ConfigEntryNotReady",
                  "ConfigEntryAuthFailed", "ConfigEntryError", "UpdateFailed", "IntegrationError"}


_LANGS = ["de", "en", "es", "fr", "it", "nl", "pl", "pt"]


def _messages(path: pathlib.Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v["message"] for k, v in data.get("exceptions", {}).items()}


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def _raises():
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)):
                continue
            func = n.exc.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in _HA_EXCEPTIONS or name == "Invalid":
                yield f.name, n.lineno, name, {k.arg: k.value for k in n.exc.keywords}


RAISES = list(_raises())


STRINGS = _messages(PKG / "strings.json")


def _issue_calls():
    for f in sorted(PKG.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) == "async_create_issue":
                yield f.name, n.lineno, {k.arg: k.value for k in n.keywords}


def _issues(lang_file):
    return json.loads(lang_file.read_text(encoding="utf-8")).get("issues", {})


def test_raises_were_found():
    assert len(RAISES) > 100


def test_no_voluptuous_invalid_is_raised_from_handlers():
    """vol.Invalid is for schemas; from a handler it reaches the user
    untranslated. ServiceValidationError carries a translation."""
    assert [(f, l) for f, l, n, _k in RAISES if n == "Invalid"] == []


def test_every_raise_is_translatable():
    missing = [f"{f}:{l} {n}" for f, l, n, kw in RAISES
               if n != "Invalid" and not ("translation_key" in kw and "translation_domain" in kw)]
    assert not missing, missing


def test_every_key_exists_and_its_placeholders_match_exactly():
    problems = []
    for f, l, _n, kw in RAISES:
        key_node = kw.get("translation_key")
        if not isinstance(key_node, ast.Constant):
            continue
        key = key_node.value
        if key not in STRINGS:
            problems.append(f"{f}:{l} key {key!r} not in strings.json")
            continue
        needed = _placeholders(STRINGS[key])
        ph = kw.get("translation_placeholders")
        if ph is None:
            given = set()
        elif isinstance(ph, ast.Dict):
            given = {k.value for k in ph.keys if isinstance(k, ast.Constant)}
        else:
            continue   # built dynamically: checked at runtime by the formatting test
        if needed - given:
            problems.append(f"{f}:{l} {key}: text needs {sorted(needed - given)}")
        if given - needed:
            problems.append(f"{f}:{l} {key}: passes unused {sorted(given - needed)}")
    assert not problems, problems


@pytest.mark.parametrize("lang", _LANGS)
def test_every_translation_has_every_key_with_the_same_placeholders(lang):
    tr = _messages(PKG / "translations" / f"{lang}.json")
    assert set(tr) == set(STRINGS), sorted(set(STRINGS) ^ set(tr))
    wrong = [k for k in STRINGS if _placeholders(tr[k]) != _placeholders(STRINGS[k])]
    assert not wrong, wrong


def test_english_translation_equals_strings_json_entirely():
    """Home Assistant shows translations/en.json at runtime; strings.json
    is the source. They drifted twice in opposite directions: once the
    runtime text was newer, once the source was — English users missed a
    hint the flow actually supports."""
    src = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((PKG / "translations" / "en.json").read_text(encoding="utf-8"))
    assert en == src


@pytest.mark.parametrize("lang", _LANGS)
@pytest.mark.asyncio
async def test_home_assistant_can_format_every_message(hass, enable_custom_integrations, lang):
    """Through Home Assistant's own translation loader, not a copy of it."""
    from homeassistant.helpers.translation import async_get_translations

    from custom_components.roomba_plus.const import DOMAIN

    table = await async_get_translations(hass, lang, "exceptions", {DOMAIN})
    prefix = f"component.{DOMAIN}.exceptions."
    loaded = {k[len(prefix):-len(".message")]: v for k, v in table.items() if k.startswith(prefix)}
    assert set(loaded) == set(STRINGS)
    for key, text in loaded.items():
        filled = text.format(**{p: "X" for p in _placeholders(text)})
        assert "{" not in filled, (lang, key)


@pytest.mark.parametrize("lang", _LANGS)
def test_every_constant_issue_key_exists_with_its_placeholders(lang):
    """observed_zones_detected once carried the zone-NAMING text: it asked
    for input the notice has no field for, and for placeholders the code
    never passed."""
    issues = _issues(PKG / "translations" / f"{lang}.json")
    problems = []
    for f, l, kw in _issue_calls():
        key = kw.get("translation_key")
        if not isinstance(key, ast.Constant):
            continue
        entry = issues.get(key.value)
        if entry is None:
            problems.append(f"{f}:{l} {key.value} missing")
            continue
        needed = _placeholders(entry.get("title", "") + entry.get("description", ""))
        ph = kw.get("translation_placeholders")
        if ph is None or isinstance(ph, ast.Dict):
            given = {k.value for k in ph.keys} if isinstance(ph, ast.Dict) else set()
            if needed - given:
                problems.append(f"{f}:{l} {key.value}: needs {sorted(needed - given)}")
    assert not problems, problems


# ── formerly tests/test_guard_gold_entities.py ──────────────────────────────────
#
# Guards for three Gold rules on entities.
#
# icon-translations: icons live in icons.json, not in code. One documented
# exception: the cloud room picker's icon follows the region TYPE of the
# selected room, and the options are the household's own room names.
#
# entity-device-class: a sensor measuring a span of time carries the
# DURATION device class, so Home Assistant formats it and lets users pick
# the unit. Seventeen sensors lacked it.
#
# entity-translations: every entity translation key exists for its platform
# in all eight languages.

def _trees():
    for f in sorted(PKG.glob("*.py")):
        yield f.name, ast.parse(f.read_text(encoding="utf-8"))


def _platform(fname):
    stem = fname[:-3]
    for p in ("binary_sensor", "device_tracker", "sensor", "button", "select", "switch",
              "image", "vacuum", "calendar", "todo", "number", "event", "update"):
        if stem == p or stem.startswith(p + "_"):
            return p
    return None


@pytest.mark.parametrize("lang", _LANGS)
def test_every_entity_translation_key_exists(lang):
    entity = json.loads((PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8")).get("entity", {})
    missing = []
    for fname, tree in _trees():
        plat = _platform(fname)
        if plat is None:
            continue
        for n in ast.walk(tree):
            key = None
            if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) != "async_create_issue":
                kw = {k.arg: k.value for k in n.keywords}
                if "translation_domain" not in kw and isinstance(kw.get("translation_key"), ast.Constant):
                    key = kw["translation_key"].value
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                tgs = [n.target] if isinstance(n, ast.AnnAssign) else n.targets
                if any(ast.unparse(t).endswith("_attr_translation_key") for t in tgs) \
                   and isinstance(getattr(n, "value", None), ast.Constant):
                    key = n.value.value
            if key and key not in entity.get(plat, {}):
                missing.append(f"{fname}:{getattr(n, 'lineno', '?')} {plat}.{key}")
    assert not missing, missing


@pytest.mark.parametrize("lang", _LANGS)
def test_every_flow_field_has_a_description(lang):
    """26 fields had none: five in setup, 21 in the options."""
    data = json.loads((PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
    missing = []
    for area in ("config", "options"):
        for step, v in data.get(area, {}).get("step", {}).items():
            for field in v.get("data", {}):
                if field not in v.get("data_description", {}):
                    missing.append(f"{area}.{step}.{field}")
    assert not missing, missing


def test_no_select_option_label_is_hard_coded():
    """Two option lists were English in every language. Selector labels
    come from strings.json -> selector.<translation_key>."""
    found = []
    for fname, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "SelectOptionDict":
                if any(k.arg == "label" and isinstance(k.value, ast.Constant) for k in n.keywords):
                    found.append(f"{fname}:{n.lineno}")
    assert not found, found


@pytest.mark.parametrize("lang", _LANGS)
def test_every_selector_translation_key_has_its_options(lang):
    selectors = json.loads((PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8")).get("selector", {})
    missing = []
    for fname, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "SelectSelectorConfig":
                kw = {k.arg: k.value for k in n.keywords}
                tk, opts = kw.get("translation_key"), kw.get("options")
                if isinstance(tk, ast.Constant) and isinstance(opts, ast.List):
                    want = {e.value for e in opts.elts if isinstance(e, ast.Constant)}
                    have = set(selectors.get(tk.value, {}).get("options", {}))
                    if want - have:
                        missing.append(f"{fname}:{n.lineno} {tk.value}: {sorted(want - have)}")
    assert not missing, missing


def test_no_entity_translation_is_orphaned():
    """Thirteen sensor translations outlived their sensors, which had been
    merged into consolidated ones. migrations.py does not count as a use:
    it holds old names on purpose."""
    used = set()
    for fname, tree in _trees():
        if fname == "migrations.py":
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                used.add(n.value)
            if isinstance(n, ast.JoinedStr):
                used.update(v.value for v in n.values if isinstance(v, ast.Constant))
    entity = json.loads((PKG / "translations" / "en.json").read_text(encoding="utf-8"))["entity"]
    orphaned = [f"{p}.{k}" for p, keys in entity.items() for k in keys
                if k not in used and not any(len(u) >= 6 and u in k for u in used if "_" in u)]
    assert not orphaned, orphaned


# ── formerly tests/test_locale_slug_guard.py ──────────────────────────
#
# Locale-slug guard: enforces Roomba+ entity naming conventions.
#
# Two rules are checked at collection time (no HA fixtures needed):
#
# RULE 1 — No _attr_name alongside _attr_translation_key at class level.
#     In HA 2024+, a class-level _attr_name string overrides _attr_translation_key.
#     The entity always shows the English hardcoded string regardless of locale.
#     Root cause of 15 locale-slug regressions fixed in v3.0.0.
#
# RULE 2 — Every entity class with _attr_translation_key must have
#     suggested_object_id available (own or inherited from IRobotEntity).
#     Without it HA slugifies the translated name at first registration,
#     producing locale-specific entity_ids (e.g. 'akkualter' vs 'battery_age_days').
#
# Adding new entities:
#   1. Set  _attr_translation_key = "english_key"
#   2. Set  _attr_unique_id = f"{self.robot_unique_id}_{english_key}"  in __init__
#   3. Do NOT set _attr_name at class level
#   4. Inherit from IRobotEntity — suggested_object_id fires automatically.
#      EntityDescription-based classes: override to return entity_description.key.

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "roomba_plus"


# AUTO-DISCOVERED (this session) rather than a manually maintained list --
# a manually maintained list is EXACTLY what caused two real gaps: calendar.py
# and sensor_prime.py both had real IRobotEntity subclasses with
# _attr_translation_key, correctly following both rules, but were simply never
# added to this list, so this guard never actually checked them at all. Scanning
# every .py file in the integration is safe even though most of them (const.py,
# models.py, schedule_parser.py, etc.) define no entity classes at all -- the
# rules below only ever fire on classes that actually match the pattern
# (_attr_translation_key present), so scanning "too many" files costs nothing,
# while the previous "too few" manually-curated list cost two real regressions.
PLATFORM_FILES = sorted(p.name for p in INTEGRATION.glob("*.py") if p.name != "__init__.py")


# FavoriteButton sets _attr_name dynamically in __init__ from iRobot app routine
# name — this is intentional and locale-independent (app names are user-defined).
RULE1_EXEMPT: set[str] = {"FavoriteButton"}


def _class_blocks(source: str) -> list[tuple[str, str, str]]:
    """Yield (class_name, bases_str, body_str) for every class in source."""
    results = []
    for m in re.finditer(
        r"^class (\w+)\(([^)]*)\)[^\n]*\n((?:(?!^class ).*\n)*)",
        source,
        re.MULTILINE,
    ):
        results.append((m.group(1), m.group(2), m.group(3)))
    return results


def _build_irobot_subclasses(sources: dict[str, str]) -> set[str]:
    """Return names of all classes that (directly or indirectly) inherit IRobotEntity."""
    direct: dict[str, set[str]] = {}  # cls → set of base class names
    for src in sources.values():
        for cls, bases, _ in _class_blocks(src):
            direct[cls] = {b.strip() for b in bases.split(",") if b.strip()}

    irobot_family: set[str] = {"IRobotEntity"}
    changed = True
    while changed:
        changed = False
        for cls, bases in direct.items():
            if cls not in irobot_family and irobot_family & bases:
                irobot_family.add(cls)
                changed = True
    return irobot_family


def _rule1_violations() -> list[str]:
    violations = []
    for fname in PLATFORM_FILES:
        src = (INTEGRATION / fname).read_text(encoding="utf-8")
        for cls, _, body in _class_blocks(src):
            if cls in RULE1_EXEMPT:
                continue
            has_name = bool(re.search(r'^\s{4}_attr_name\s*=\s*"[^"]+"', body, re.MULTILINE))
            has_tk   = bool(re.search(r'_attr_translation_key\s*=\s*"', body))
            if has_name and has_tk:
                val = re.search(r'_attr_name\s*=\s*"([^"]+)"', body).group(1)
                violations.append(f"{fname}:{cls}: _attr_name={val!r} + _attr_translation_key")
    return violations


def _rule2_violations() -> list[str]:
    sources = {f: (INTEGRATION / f).read_text(encoding="utf-8") for f in PLATFORM_FILES}
    irobot_family = _build_irobot_subclasses(sources)

    violations = []
    for fname, src in sources.items():
        for cls, bases, body in _class_blocks(src):
            if not re.search(r'_attr_translation_key\s*=\s*"', body):
                continue
            base_set = {b.strip() for b in bases.split(",") if b.strip()}
            inherits_irobot = bool(irobot_family & base_set)
            has_soid = "suggested_object_id" in body
            if not inherits_irobot and not has_soid:
                violations.append(
                    f"{fname}:{cls}: has _attr_translation_key but neither "
                    "inherits IRobotEntity nor defines suggested_object_id"
                )
    return violations


@pytest.mark.parametrize("v", _rule1_violations() or [None], ids=lambda v: v or "ok")
def test_no_attr_name_with_translation_key(v: str | None) -> None:
    """RULE 1: No class may set both _attr_name (string) and _attr_translation_key.

    Fix: remove _attr_name; rely on _attr_translation_key + translations/*.json.
    Exempt: add class name to RULE1_EXEMPT with a justification comment.
    """
    if v is None:
        return
    pytest.fail(
        f"\n\nLocale-slug RULE 1 violation:\n  {v}\n\n"
        "Remove the class-level _attr_name. HA 2024+ ignores _attr_translation_key\n"
        "when _attr_name is set, so the entity always shows English text.\n"
    )


@pytest.mark.parametrize("v", _rule2_violations() or [None], ids=lambda v: v or "ok")
def test_suggested_object_id_covered(v: str | None) -> None:
    """RULE 2: Every entity with _attr_translation_key must provide suggested_object_id.

    IRobotEntity.suggested_object_id covers all subclasses automatically.
    For non-IRobotEntity classes add:

        @property
        def suggested_object_id(self) -> str:
            return self.entity_description.key  # or the English key literal
    """
    if v is None:
        return
    pytest.fail(
        f"\n\nLocale-slug RULE 2 violation:\n  {v}\n\n"
        "Inherit from IRobotEntity (preferred), or add suggested_object_id.\n"
    )


def test_device_tracker_suggested_object_id_returns_none():
    """RoombaDeviceTracker uses device-name-only entity_id (no _position suffix)."""
    import re
    src = (INTEGRATION / "device_tracker.py").read_text(encoding="utf-8")
    # Must override suggested_object_id and return None
    assert "def suggested_object_id" in src, \
        "RoombaDeviceTracker must override suggested_object_id"
    # The override body must return None (not the unique_id suffix)
    m = re.search(
        r"def suggested_object_id.*?return None",
        src, re.DOTALL,
    )
    assert m is not None, \
        "RoombaDeviceTracker.suggested_object_id must return None"


def test_vacuum_suggested_object_id_returns_none():
    """RoombaVacuum is the primary entity — entity_id = device name only."""
    import re
    src = (INTEGRATION / "vacuum.py").read_text(encoding="utf-8")
    assert "def suggested_object_id" in src, \
        "RoombaVacuum must override suggested_object_id"
    m = re.search(
        r"def suggested_object_id.*?return None",
        src, re.DOTALL,
    )
    assert m is not None, \
        "RoombaVacuum.suggested_object_id must return None"


def test_base_suggested_object_id_strips_robot_prefix():
    """IRobotEntity.suggested_object_id returns the English key for prefixed uids."""
    from unittest.mock import MagicMock
    from custom_components.roomba_plus.entity import IRobotEntity

    obj = MagicMock(spec=IRobotEntity)
    obj.robot_unique_id = "roomba_plus_ABC123"
    obj._attr_unique_id = "roomba_plus_ABC123_battery_age_days"
    # Call the real property getter against the mock
    result = IRobotEntity.suggested_object_id.fget(obj)
    assert result == "battery_age_days"


def test_base_suggested_object_id_none_when_no_prefix_match():
    """Returns None when unique_id has no robot prefix (e.g. vacuum primary)."""
    from unittest.mock import MagicMock
    from custom_components.roomba_plus.entity import IRobotEntity

    obj = MagicMock(spec=IRobotEntity)
    obj.robot_unique_id = "roomba_plus_ABC123"
    obj._attr_unique_id = "roomba_plus_ABC123"  # exact match, no trailing key
    result = IRobotEntity.suggested_object_id.fget(obj)
    assert result is None


def test_base_suggested_object_id_handles_fav_id_with_underscores():
    """fav_id containing underscores is returned intact (prefix strip, not rfind)."""
    from unittest.mock import MagicMock
    from custom_components.roomba_plus.entity import IRobotEntity

    obj = MagicMock(spec=IRobotEntity)
    obj.robot_unique_id = "roomba_plus_ABC123"
    obj._attr_unique_id = "roomba_plus_ABC123_fav_my_fav_routine"
    result = IRobotEntity.suggested_object_id.fget(obj)
    # Must return the WHOLE suffix "fav_my_fav_routine", not just "routine"
    assert result == "fav_my_fav_routine"
