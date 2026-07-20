"""Guards against a real CI failure this project already hit once:
manifest.json's "requirements" entries must not contain a literal space,
or Home Assistant's own hassfest validator rejects the whole integration
with `[REQUIREMENTS] Requirement "..." contains a space`.

The PEP 508 direct-reference syntax ("package @ git+https://...") that
Home Assistant's OWN developer docs recommend for a git-based
requirement is exactly what triggers this -- a known, documented
inconsistency between HA's docs and hassfest's own validator (see
home-assistant/core#123339 and #149833). The confirmed workaround is
to drop the space immediately around the package-name/"@" separator
(package@git+https://...), keeping the "@<ref>" tag/branch pin at the
end untouched. Caught once already in this project (the actual CI
error is reproduced almost verbatim in the test below) -- this test
exists so it can't quietly come back."""
from __future__ import annotations

import json
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent.parent / "custom_components" / "roomba_plus" / "manifest.json"


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_no_requirement_contains_a_space() -> None:
    """Mirrors hassfest's own [REQUIREMENTS] check directly -- a space
    anywhere in a requirements entry is rejected outright, regardless
    of where it appears."""
    manifest = _load_manifest()
    for requirement in manifest["requirements"]:
        assert " " not in requirement, (
            f'Requirement "{requirement}" contains a space -- this is exactly the error '
            "hassfest raised in CI once already. See this test's own module docstring."
        )


def test_roombapy_prime_requirement_is_pinned_to_a_tag() -> None:
    """A separate, earlier real gap in this project: the roombapy-prime
    requirement was unpinned for a while (installing whatever the
    default branch happened to be at install time), and separately,
    entirely absent from requirements-test-frozen.txt. This test only
    guards the pinning half directly checkable from manifest.json --
    it asserts an "@<something>" tag reference exists after the git
    URL, not that requirements-test-frozen.txt is in sync (a plain
    text file, not something with an obvious single source of truth
    to compare against automatically)."""
    manifest = _load_manifest()
    roombapy_prime_reqs = [r for r in manifest["requirements"] if r.startswith("roombapy-prime")]
    assert len(roombapy_prime_reqs) == 1, "expected exactly one roombapy-prime requirement entry"
    requirement = roombapy_prime_reqs[0]
    assert "git+" in requirement, "expected a git-based requirement"
    # The git URL itself always ends in ".git" -- a tag/ref pin, if present,
    # is a second "@" AFTER that, e.g. "....git@v0.1.11a6".
    assert ".git@" in requirement, (
        f'"{requirement}" has no "@<ref>" pin after the .git URL -- this is exactly the '
        "unpinned-dependency gap this project already hit once. Every install would pull "
        "whatever the default branch happens to be at install time, not a specific, "
        "reproducible version."
    )
