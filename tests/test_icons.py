"""Icons registry coverage guard (live investigation #4).

icons.json must carry an entry for every entity the integration creates.
HA matches icons by platform + entity translation_key, so this scans each
platform module's descriptors/classes for translation keys and asserts a
registry entry exists — with an allowlist for entities that deliberately
set their own icon (explicit _attr_icon) or have no translation key.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "roomba_plus"
_ICONS = _ROOT / "icons.json"


def _load_icons() -> dict:
    return json.loads(_ICONS.read_text())["entity"]


def _translation_keys_in_source(module_name: str) -> set[str]:
    """All translation_key literals used for entity descriptions/classes
    in a platform module: keyword-arg form, _attr_translation_key class
    attributes, and translation_key= arguments inside dynamic
    entity_description assignments."""
    keys: set[str] = set()
    tree = ast.parse((_ROOT / module_name).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "translation_key" and isinstance(kw.value, ast.Constant):
                    keys.add(kw.value.value)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                target_is_key = (
                    (isinstance(tgt, ast.Attribute) and tgt.attr == "_attr_translation_key")
                    or (isinstance(tgt, ast.Name) and tgt.id == "_attr_translation_key")
                )
                if target_is_key and isinstance(node.value, ast.Constant):
                    keys.add(node.value.value)
    return {k for k in keys if isinstance(k, str)}


# Keys set programmatically (dynamic command keys, translation
# placeholders), not as translation_key literals in source.
# Entities that set their own _attr_icon (HA precedence over the registry)
# — a registry entry would be dead weight.
_EXPLICIT_ICON_KEYS = frozenset({
    "prime_quiet_hours", "prime_quiet_hours_active",
    "prime_cleaning_mode", "prime_map",
})


_DYNAMIC_ICON_KEYS = frozenset({
    "prime_empty_bin", "prime_wash_pad", "prime_stop_pad_dry",
    "prime_start_pad_dry", "favorite",
})


_LEGACY_ICON_KEYS = frozenset({
    # Deprecated CloudRaw sensors deactivated in SC1 (v3.0); entries kept
    # for already-registered entities' display continuity.
    "recent_completion_rate", "recent_cleaning_speed", "recent_coverage_pct",
    "cleaning_speed_trend", "recent_dirt_density", "recent_recharge_fraction",
    "recent_recharges", "recent_evacuations", "recent_dirt_events",
    "recent_error_code", "recent_error_time",
    "recent_wifi_floor", "recent_wifi_stability",
})


@pytest.mark.parametrize(
    ("module", "domain"),
    [
        ("sensor_core.py", "sensor"),
        ("sensor_cloud.py", "sensor"),
        ("sensor_rooms.py", "sensor"),
        ("sensor_diagnostics.py", "sensor"),
        ("sensor_prime.py", "sensor"),
        ("binary_sensor.py", "binary_sensor"),
        ("button.py", "button"),
        ("button_prime.py", "button"),
        ("select.py", "select"),
        ("select_prime.py", "select"),
        ("switch.py", "switch"),
        ("prime_schedule_switch.py", "switch"),
        ("image.py", "image"),
        ("device_tracker.py", "device_tracker"),
        ("calendar.py", "calendar"),
        ("todo.py", "todo"),
        ("todo_prime.py", "todo"),
    ],
)
def test_platform_translation_keys_have_icons(module: str, domain: str) -> None:
    icons = _load_icons()
    registry = icons.get(domain, {})
    used = _translation_keys_in_source(module)
    missing = sorted(k for k in used if k not in registry and k not in _EXPLICIT_ICON_KEYS)
    assert not missing, (
        f"{domain} icons.json missing entries for {missing} "
        f"(translation keys used in {module})"
    )


def test_icons_entries_refer_to_real_keys() -> None:
    """Every registry key must be findable as an entity translation_key
    somewhere in the source (stale registry entries are drift)."""
    icons = _load_icons()
    for domain, entries in icons.items():
        used: set[str] = set()
        modules = {
            "sensor": ("sensor_core.py", "sensor_cloud.py", "sensor_rooms.py",
                       "sensor_diagnostics.py", "sensor_prime.py"),
            "binary_sensor": ("binary_sensor.py",),
            "button": ("button.py", "button_prime.py"),
            "select": ("select.py", "select_prime.py"),
            "switch": ("switch.py", "prime_schedule_switch.py"),
            "image": ("image.py",),
            "device_tracker": ("device_tracker.py",),
            "calendar": ("calendar.py",),
            "todo": ("todo.py", "todo_prime.py"),
        }.get(domain, ())
        for m in modules:
            used |= _translation_keys_in_source(m)
        unknown = sorted(set(entries) - used - _LEGACY_ICON_KEYS - _DYNAMIC_ICON_KEYS)
        assert not unknown, (
            f"icons.json {domain} entries {unknown} have no matching "
            "translation_key in the platform source — remove or allowlist"
        )