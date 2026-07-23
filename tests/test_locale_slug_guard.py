"""Locale-slug guard: enforces Roomba+ entity naming conventions.

Two rules are checked at collection time (no HA fixtures needed):

RULE 1 — No _attr_name alongside _attr_translation_key at class level.
    In HA 2024+, a class-level _attr_name string overrides _attr_translation_key.
    The entity always shows the English hardcoded string regardless of locale.
    Root cause of 15 locale-slug regressions fixed in v3.0.0.

RULE 2 — Every entity class with _attr_translation_key must have
    suggested_object_id available (own or inherited from IRobotEntity).
    Without it HA slugifies the translated name at first registration,
    producing locale-specific entity_ids (e.g. 'akkualter' vs 'battery_age_days').

Adding new entities:
  1. Set  _attr_translation_key = "english_key"
  2. Set  _attr_unique_id = f"{self.robot_unique_id}_{english_key}"  in __init__
  3. Do NOT set _attr_name at class level
  4. Inherit from IRobotEntity — suggested_object_id fires automatically.
     EntityDescription-based classes: override to return entity_description.key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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


# ── Rule 1 violations ─────────────────────────────────────────────────────────

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


# ── Rule 2 violations ─────────────────────────────────────────────────────────

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


# ── Edge case: entities that intentionally return None ───────────────────────

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
