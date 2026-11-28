"""Put fixed defects back, one at a time, and see whether a test notices.

WHY (plan 4.7-3). The independent review of 28.11. built eight defects
into a copy of the integration -- shapes the release notes had already
seen once -- and two survived the whole suite. A suite that passes says
the code agrees with the tests; this says whether the tests would catch
the mistakes this project has actually made.

HOW. The repository is copied to a temporary directory (never changed in
place). For each mutation: one exact piece of source is replaced, the
test files closest to it run, and the mutation counts as KILLED when a
test fails. The source is put back before the next one. Run it before
each minor version:

    python scripts/mutation_probe.py                 every mutation
    python scripts/mutation_probe.py M2 R3           some of them
    PYTHON=/path/to/python python scripts/mutation_probe.py

Not part of CI or `check_all.sh`: it takes minutes, and a survivor is a
question for a person -- which test is missing -- not a red build. What
CI does check is that every mutation still applies
(tests/test_guards_can_fail.py): a refactor that removes a pattern has
to bring the mutation along.

ADDING ONE. Every fix in a release adds the defect it fixed, with the
version, the smallest piece of source that brings it back, and the test
files that should notice. Leave out a mutation that changes nothing a
robot could notice: the Prime path-turn flush of 4.6.13 is one -- Prime
never loads that learner, so the flush has nothing to write, and its
survival says nothing about the tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = Path("custom_components") / "roomba_plus"


@dataclass(frozen=True)
class Mutation:
    key: str
    why: str
    module: str
    old: str
    new: str
    tests: tuple[str, ...]


MUTATIONS: tuple[Mutation, ...] = (
    # ── The independent review of 28.11. (M8 replaced: its code is gone) ──
    Mutation(
        "M1", "vacuum class chosen by is_mop instead of is_braava (Combo, 4.4)",
        "vacuum.py",
        "    if is_braava(state):\n        constructor = BraavaJet",
        "    if is_mop(state):\n        constructor = BraavaJet",
        ("tests/test_vacuum.py",),
    ),
    Mutation(
        "M2", "battery contact repair id shared by every robot (sibling of 4.6.12)",
        "repairs.py",
        'issue_id = f"battery_contact_suspect_{config_entry.entry_id}"',
        'issue_id = "battery_contact_suspect"',
        ("tests/test_repairs.py",),
    ),
    Mutation(
        "M3", "Prime unload no longer flushes the mission timer (4.6.13)",
        "__init__.py",
        "        mts = config_entry.runtime_data.mission_timer_store\n"
        "        if mts is not None:\n            try:\n"
        "                await mts.async_save(hass, config_entry.entry_id)\n"
        "            except Exception:  # noqa: BLE001\n                _LOGGER.debug(\n"
        "                    # NOT INSTRUMENTED",
        "        mts = None\n"
        "        if mts is not None:\n            try:\n"
        "                await mts.async_save(hass, config_entry.entry_id)\n"
        "            except Exception:  # noqa: BLE001\n                _LOGGER.debug(\n"
        "                    # NOT INSTRUMENTED",
        ("tests/test_init.py", "tests/test_prime_setup.py"),
    ),
    Mutation(
        "M4", "Prime whole-home start sends a mode for the first room only (4.5.4)",
        "vacuum.py",
        "operating_mode=[mode] * len(rooms)",
        "operating_mode=[mode]",
        ("tests/test_vacuum.py",),
    ),
    Mutation(
        "M5", "diagnostics no longer redact the host (AGENTS rule 6)",
        "const.py",
        '    "host",\n    "password",',
        '    "password",',
        ("tests/test_diagnostics.py",),
    ),
    Mutation(
        "M6", "one cloud login shared across different credentials (4.3)",
        "cloud_account.py",
        'raw = "\\0".join((username.strip().casefold(), password, country.upper()))',
        'raw = "shared"',
        ("tests/test_cloud_account.py",),
    ),
    Mutation(
        "M7", "withheld_features stops explaining Braava (one rule, two copies)",
        "withheld_features.py",
        '    if is_braava(state):\n        return {\n            "offered": False,\n'
        '            "reason": "braava",',
        '    if False:\n        return {\n            "offered": False,\n'
        '            "reason": "braava",',
        ("tests/test_withheld_features.py",),
    ),
    Mutation(
        "M8", "presence events without the robot again (4.6.13)",
        "presence_manager.py",
        '        return {"entry_id": self._entry.entry_id, "name": getattr(self._entry, "title", None)}',
        "        return {}",
        ("tests/test_presence_manager.py",),
    ),
    # ── 4.6.13 ──
    Mutation(
        "R1", "diagnostics carry the robot's position again (4.6.13)",
        "diagnostics.py",
        '"position": _withheld_unless_debug(state.get("pose")),',
        '"position": state.get("pose"),',
        ("tests/test_diagnostics.py",),
    ),
    # ── 4.7.0b1 / b2 ──
    Mutation(
        "R3", "a mission recording keeps room names again (4.7.0b2)",
        "mission_recorder.py",
        "if _is_secret_key(name) or _is_name_key(name) or name in _DROPPED_KEYS:",
        "if _is_secret_key(name):",
        ("tests/test_mission_recorder.py",),
    ),
    Mutation(
        "R4", "a reload leaves the mission recording running (4.7.0b1)",
        "__init__.py",
        "    mission_recorder.async_stop_for_entry(hass, config_entry.entry_id)\n",
        "",
        ("tests/test_mission_recorder.py",),
    ),
    Mutation(
        "R5", "Prime timeline deltas are not recorded (4.7.0b2)",
        "prime_coordinator.py",
        '                    mission_recorder.note(\n'
        '                        self.hass, self.entry.entry_id, "timeline", delta.payload,\n'
        "                    )\n",
        "",
        ("tests/test_mission_recorder.py",),
    ),
    Mutation(
        "R6", "a failing start-condition listener is silent again (4.7-10)",
        "blocking_manager.py",
        '                robot_logger(_LOGGER, self._entry).debug(\n'
        '                    "BlockingManager: a state listener failed", exc_info=True,\n'
        "                )",
        "                pass",
        ("tests/test_blocking_manager.py",),
    ),
)


def applies(mutation: Mutation, root: Path = ROOT) -> int:
    """How often the mutation's piece of source occurs: it must be once."""
    return (root / PACKAGE / mutation.module).read_text(encoding="utf-8").count(mutation.old)


def _copy(dest: Path) -> Path:
    def _skip(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in {".git", "__pycache__", ".pytest_cache", ".mypy_cache",
                                          ".ruff_cache", "htmlcov", "node_modules"}}
    target = dest / "repo"
    shutil.copytree(ROOT, target, ignore=_skip)
    return target


def run(selected: list[str], python: str) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="roomba_plus_mutation_") as tmp:
        repo = _copy(Path(tmp))
        for mutation in MUTATIONS:
            if selected and mutation.key not in selected:
                continue
            path = repo / PACKAGE / mutation.module
            source = path.read_text(encoding="utf-8")
            count = source.count(mutation.old)
            if count != 1:
                results.append({"key": mutation.key, "status": f"does not apply ({count}x)"})
                print(f"{mutation.key}  DOES NOT APPLY ({count}x)  {mutation.why}", flush=True)
                continue
            path.write_text(source.replace(mutation.old, mutation.new), encoding="utf-8")
            started = time.time()
            try:
                done = subprocess.run(
                    [python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider",
                     "--no-header", *mutation.tests],
                    cwd=repo, capture_output=True, text=True, timeout=900,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
            finally:
                path.write_text(source, encoding="utf-8")
            killed = done.returncode != 0
            first = next((line for line in done.stdout.splitlines() if line.startswith("FAILED")), "")
            results.append({"key": mutation.key, "status": "killed" if killed else "SURVIVED",
                            "first_failure": first, "seconds": round(time.time() - started, 1)})
            print(f"{mutation.key}  {'killed  ' if killed else 'SURVIVED'}  {mutation.why}"
                  + (f"\n      {first}" if first else ""), flush=True)
    return results


def main(argv: list[str]) -> int:
    python = os.environ.get("PYTHON", sys.executable)
    results = run([a for a in argv if not a.startswith("-")], python)
    bad = [r for r in results if r["status"] != "killed"]
    print(f"\n{len(results) - len(bad)} of {len(results)} killed.")
    if bad:
        print("A survivor means no test notices that defect: add the test that would.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
