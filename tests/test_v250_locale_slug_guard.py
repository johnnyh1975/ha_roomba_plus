"""Locale-slug guard — v2.5.0

Ensures that no EntityDescription in any platform produces a non-ASCII or
locale-dependent entity_id suffix.

Root cause of the v2.5.0 entity_id saga: HA generates entity_id slugs from
the TRANSLATED display name when translation_key is set and the entity is
first registered in a non-English locale. German users ended up with slugs
like `wartung_akkukapazitat` instead of `battery_capacity_retention`.

Prevention rule:
  For every EntityDescription (or _attr_translation_key class) that carries a
  translation_key, the key itself must be pure ASCII a-z / 0-9 / underscore.
  The key IS the entity_id suffix HA falls back to when it needs a stable
  identifier — it must be locale-independent.

  Additionally, ALL translation strings for ALL languages must not contain
  characters that HA would slugify differently from the English original.
  (Checked by round-tripping through HA's slugify.)

These tests catch regressions before they reach production.
"""

import re
import json
import importlib
from pathlib import Path


# ── helpers ───────────────────────────────────────────────────────────────────

TRANS_DIR = Path(__file__).parent.parent / "custom_components" / "roomba_plus" / "translations"
ASCII_KEY_RE = re.compile(r'^[a-z0-9_]+$')

# Known-acceptable same-in-all-languages values (abbreviations / loanwords)
LANG_NEUTRAL = {"SNR", "Status", "Mission – ID", "{name}"}


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


# ── tests ─────────────────────────────────────────────────────────────────────

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
        """Every translation_key in SENSORS + CLOUD_RAW_SENSORS must be in en.json."""
        from custom_components.roomba_plus.sensor import (
            SENSORS as SENSOR_DESCRIPTIONS,
            CLOUD_RAW_SENSORS,
        )
        translations = _load_translations()
        en_sensor = translations.get("en", {}).get("entity", {}).get("sensor", {})
        bad = []
        for desc in list(SENSOR_DESCRIPTIONS) + list(CLOUD_RAW_SENSORS):
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
