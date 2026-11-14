# Working on Roomba+

For people and coding agents alike. Roomba+ is a Home Assistant custom
integration for iRobot robots: Classic models over the local connection
(roombapy 2.x) and Prime models over the iRobot cloud (roombapy-prime).

## Layout

| What | Where |
|---|---|
| The integration | `custom_components/roomba_plus/` |
| Its texts (English source) | `custom_components/roomba_plus/strings.json` |
| Translations, 8 languages | `custom_components/roomba_plus/translations/` |
| Tests | `tests/test_<module>.py`, one file per module |
| Repository checks | `scripts/check_*.py` |
| Release notes, one file per version | `release-notes/` |
| Docs: guides, reference, explanations | `docs/guides/`, `docs/reference/`, `docs/explanations/` |
| Upgrade notes | `docs/UPGRADING.md` |

## Before you open a pull request

```bash
bash scripts/check_all.sh
```

It runs every check the CI runs: the repository checks, ruff, strict
mypy against its baseline, the test suite and the 95 % line coverage
each module must keep. CI then repeats the tests on two Home Assistant
versions, the supported minimum (2025.5, Python 3.13) and a current one
(Python 3.14); code has to work on both.

## Rules

1. **Every fix comes with a test that fails without it.** Run the test
   with the fix reverted and say in the pull request that it failed.
   Keep tests to the core of the change.
2. **One test file per module**: tests for `presence_manager.py` go in
   `tests/test_presence_manager.py`.
3. **Build objects through their constructors in tests.** Setting
   private attributes by hand on an object made without `__init__` hid a
   missing attribute that broke presence scheduling on every Prime robot.
4. **A task started with `async_create_task` catches what it can
   expect**, such as an unreachable robot. Otherwise the error surfaces only
   as "Task exception was never retrieved".
5. **No private data**, not in code, tests, fixtures or docs. Use
   placeholders: `192.0.2.x` for addresses, `aa:bb:cc:dd:ee:ff` for MACs,
   `0123456789ABCDEF` for robot ids, `user@example.com` for accounts. The
   CI checks every added line (`scripts/check_private_data.py`).
6. **Never log passwords or tokens.** Diagnostics contain no address,
   password, hash or coordinates.
7. **Texts go in `strings.json` and all eight translations.** Keys are
   `[a-z0-9-_]`, and do not start or end with `-` or `_`; Home Assistant
   rejects anything else (`scripts/check_translations.py`).
8. **No text or images from the iRobot apps.** Explanations and drawings
   here are written for this project. iRobot's error texts come from
   roombapy-prime.
9. **Docs say how things are now.** A visible change updates the one
   guide for its task (`docs/guides/`) and, where an entity, action or
   option changes, `python scripts/build_reference.py`: the pages under
   `docs/reference/` named `entities`, `actions` and `options` are
   generated, never edited by hand, and a test fails when they are out of
   date. Every section starts with an **Applies to** line. History goes in
   the release notes, what a user must do in `docs/UPGRADING.md`.
   `scripts/check_doc_names.py` checks every name and menu path in the
   docs against the code.
10. **Leave versions to the maintainer.** No version bump in
   `manifest.json`, no new release-notes file; describe the change in the
   pull request and it is written up at release.

## Pull requests

Say which robot you tested on (model, firmware, Classic or Prime) and
whether a coding assistant helped. Both are fine; the maintainer reviews
and tests either way.
