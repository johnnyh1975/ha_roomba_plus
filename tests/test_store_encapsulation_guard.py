"""v3.3.0 STORE-ENCAP — encapsulation guard.

Scans every production module's AST and fails when cross-module code
touches a store-private attribute. This is the structural fix for the
tech-debt finding "14+ private accesses, invariants rebuilt ad hoc by
every consumer" (the import endpoint's self-built MAX_RECORDS trimming
was a proven bug source).

Pattern mirrors tests/test_locale_slug_guard.py: a rule that failed
silently once is enforced by AST inspection forever after — including
against the three new v3.3.0 store consumers (DIRT-VEL, ROOM-SCHED,
SMART-ORDER).

Scope: production code only. Whitebox access from tests remains
legitimate and is deliberately not scanned.
"""
from __future__ import annotations

import ast
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).parent.parent / "custom_components" / "roomba_plus"
)

# Store-private names and the single module allowed to touch each.
# obj.<name> anywhere else in production code is a violation — the
# public replacements are noted for the error message.
_GUARDED: dict[str, tuple[str, str]] = {
    "_records": ("mission_store.py", "MissionStore.records / append_validated()"),
    "_record_ids": ("mission_store.py", "MissionStore.append_validated()"),
    "_extract_rid": ("mission_store.py", "MissionStore.extract_rid()"),
    "_stuck": ("grid_store.py", "GridStore.stuck_count() / stuck_pattern()"),
    "_furniture_dismissed_at": (
        "grid_store.py",
        "GridStore.furniture_dismissed_cells() / is_furniture_dismissed()",
    ),
    "_schedule_save": ("mission_timer_store.py", "MissionTimerStore.schedule_save()"),
    "_last_phase_ts": ("mission_timer_store.py", "MissionTimerStore.last_phase_ts"),
}


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        rule = _GUARDED.get(node.attr)
        if rule is None:
            continue
        owner_file, replacement = rule
        if path.name == owner_file:
            continue  # the owning module may use its own privates
        # self.<attr> in a foreign module is that module's OWN private
        # attribute (name coincidence), not a store access.
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            continue
        found.append(
            f"{path.name}:{node.lineno}: .{node.attr} — use {replacement}"
        )
    return found


class TestStoreEncapsulationGuard:
    def test_no_cross_module_store_private_access(self):
        violations: list[str] = []
        for path in sorted(COMPONENT_DIR.glob("*.py")):
            violations.extend(_violations_in(path))
        assert not violations, (
            "Cross-module access to store-private attributes "
            "(v3.3.0 STORE-ENCAP):\n" + "\n".join(violations)
        )

    def test_guard_actually_detects(self):
        """Self-test: the scanner must flag a synthetic violation —
        guards that can never fire are the v3.2.0 dispatch-bug lesson."""
        import tempfile
        with tempfile.NamedTemporaryFile(
            "w", suffix=".py", dir=COMPONENT_DIR.parent, delete=False
        ) as fh:
            fh.write("x = data.mission_store._records\n")
            tmp = Path(fh.name)
        try:
            hits = _violations_in(tmp)
        finally:
            tmp.unlink()
        assert len(hits) == 1 and "._records" in hits[0]
