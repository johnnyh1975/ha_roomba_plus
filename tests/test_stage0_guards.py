"""The Stage 0 guards, and that they still catch what they were built for.

Each of these scripts was written after a real bug of its own shape, and
each found another one on its first run. A guard that silently stops
working is worse than no guard, so these tests check the detection
rather than just that the script exits zero.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def _run(name: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        capture_output=True, text=True, cwd=ROOT,
    )


class TestGenerationParity:
    """Twenty modules branch on CLOUD_ONLY and nearly all return early
    from the Prime side. Anything added to the Classic path afterwards is
    not inherited, and nothing flagged it.

    Five gaps of that shape shipped in one session. On its first run this
    check found a sixth: services were never removed when the last entry
    was a Prime robot, so uninstalling left eighteen registered actions
    pointing at nothing."""

    def test_the_repository_passes(self):
        result = _run("check_generation_parity.py")

        assert result.returncode == 0, result.stdout

    def test_every_deliberate_difference_has_a_reason(self):
        """An entry without a reason is how an unexamined gap gets
        silenced. "Not needed" is what somebody writes while overlooking
        a real one."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "parity", SCRIPTS / "check_generation_parity.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        for key, entries in module.DELIBERATE.items():
            for call, reason in entries.items():
                assert len(reason) > 20, f"{key}::{call} has no real reason"

    def test_platform_setups_are_skipped(self):
        """A platform's async_setup_entry exists to create different
        entities per generation. Reporting that difference is noise --
        89 of the first run's 141 findings were exactly that."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "parity", SCRIPTS / "check_generation_parity.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module._difference_expected("async_setup_entry")
        assert not module._difference_expected("async_unload_entry")


class TestRequestBudget:
    """Two bugs came from asking what an entity costs per day, not from
    anything failing: schedule switches at roughly 8,600 cloud requests a
    day, and a duplicated map-metadata call per refresh.

    Both were functionally correct and every test passed."""

    def test_the_repository_passes(self):
        result = _run("check_request_budget.py")

        assert result.returncode == 0, result.stdout

    def test_it_follows_one_hop_into_helpers(self):
        """THE thing that made it useful. The schedule switch's
        async_update does not call the cloud directly -- it calls
        async_read_schedule_containers(), which does. Looking only at the
        method body reported the entity as free."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "budget", SCRIPTS / "check_request_budget.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        import ast

        tree = ast.parse(
            "async def wrapper(robot):\n"
            "    return await robot.get_schedules('h')\n"
        )

        assert "wrapper" in module._cloud_wrappers(tree)

    def test_polling_exceptions_state_a_number(self):
        """An exception is a claim that the cost is acceptable. That
        claim needs the interval and the resulting daily figure, not "it
        is fine"."""
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(
            "budget", SCRIPTS / "check_request_budget.py"
        )
        module = module_from_spec(spec)
        spec.loader.exec_module(module)

        for key, reason in module._POLLING_EXCEPTIONS.items():
            assert any(c.isdigit() for c in reason), f"{key} states no figure"


class TestAssumedInventory:
    """4,564 tests pass. That says nothing about how many assert a fact
    and how many preserve a guess.

    `assert call.body_json == {"assetId": "BLID123"}` was green for
    months on a wire key nobody had confirmed. The real key is
    `robot_id`. The test made the assumption harder to question, because
    questioning it meant arguing with a green test."""

    def test_the_repository_passes(self):
        result = _run("list_assumed_tests.py")

        assert result.returncode == 0, result.stdout

    def test_the_marker_is_registered(self):
        """An unregistered marker is silently ignored by pytest, which
        would make the whole convention decorative."""
        content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        assert "assumed(reason)" in content

    def test_an_empty_inventory_is_a_valid_state(self):
        """An earlier version of this test required at least one marked
        test, on the grounds that an empty inventory meant an unused
        convention.

        It failed the day the last assumption was confirmed: a tester's
        capture settled the padWetness spelling, the marker came off, and
        a guard that was supposed to encourage honesty started demanding
        that something stay uncertain.

        The inventory being empty is the goal, not a fault."""
        result = _run("list_assumed_tests.py")

        assert result.returncode == 0
