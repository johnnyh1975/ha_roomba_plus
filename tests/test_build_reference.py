"""The generated reference pages (scripts/build_reference.py, 4.6.0).

The pages under docs/reference/ are written from the code. These tests
fail when the code changed and the pages were not written again, and
check the few places where the generator reads the code's structure
rather than a value.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_reference", ROOT / "scripts" / "build_reference.py")
build_reference = importlib.util.module_from_spec(_spec)
sys.modules["build_reference"] = build_reference
_spec.loader.exec_module(build_reference)


def test_pages_are_current() -> None:
    """Run `python scripts/build_reference.py` after changing an entity,
    an action, an option or their texts."""
    for name, build in build_reference.PAGES.items():
        path = ROOT / "docs" / "reference" / name
        assert path.read_text(encoding="utf-8") == build(), (
            f"docs/reference/{name} is out of date: run python scripts/build_reference.py"
        )


def _rows(key: str) -> list[dict[str, str]]:
    return [r for r in build_reference.entity_rows() if r["key"] == key]


def test_one_name_two_ids_by_generation() -> None:
    """`Status` is `sensor.<robot>_phase` on Classic and `_prime_phase` on
    Prime: same translation, different entity, each with its robots."""
    ids = {(r["suffix"], r["robots"]) for r in _rows("phase") + _rows("prime_phase")}
    assert ("phase", "Classic") in ids
    assert ("prime_phase", "Prime") in ids


def test_prime_takes_classic_descriptions_by_key() -> None:
    """The Prime setup takes some of the shared sensor descriptions by
    key. Those are on both, the rest stay Classic."""
    by_suffix = {r["suffix"]: r["robots"] for r in build_reference.entity_rows()}
    assert by_suffix["last_mission_result"] == "Both"  # _PRIME_MISSION_SENSOR_KEYS
    assert by_suffix["mission_id"] == "Both"  # _PRIME_SHADOW_SENSOR_FIELDS
    assert by_suffix["nav_panics"] == "Classic"


def test_starts_disabled_and_category_come_from_the_class() -> None:
    raw = {(r["robots"], r["enabled"], r["category"]) for r in _rows("raw_state")}
    assert raw == {("Both", "Off", "Diagnostic")}  # Prime inherits the Classic class
    reset = _rows("reset_filter")
    assert reset and all(r["category"] == "Configuration" for r in reset)


def test_a_run_time_id_that_left_the_code_stops_the_build() -> None:
    gone = {("sensor", "area_last_vacuumed"): ("zzqx_gone_<area id>", "Both", "")}
    with patch.dict(build_reference.DYNAMIC, gone), pytest.raises(SystemExit, match="zzqx_gone"):
        build_reference.entity_rows()


def test_actions_say_which_return_data() -> None:
    page = build_reference.actions_page()
    assert "| [`roomba_plus.get_rooms`](#roomba_plusget_rooms) |" in page
    line = next(l for l in page.splitlines() if l.startswith("| [`roomba_plus.get_rooms`]"))
    assert line.endswith("| Always |")
