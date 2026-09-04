"""Telling a failure that happens once from one that always happens.

SIX FAULTS IN FOUR DAYS SHARED ONE SHAPE. A name or a call signature
disagreed across a boundary, an `except Exception` filed the result at
DEBUG, and the visible symptom read as "there is nothing here" rather
than "something failed":

    missing blid on the history call      four sensors blank
    _trail_mission_id on the wrong class  no trail clearing, no dock
    _container_lock without a container   calendar edits impossible
    events with no uid                    no save button
    favourite ids under another spelling  no favourite buttons
    a four-way unpack of a six-tuple      an empty calendar

Every one failed on the FIRST call and on every call after. Not one was
found by us; each took a user reporting a missing feature.

THE `except` IS NOT THE MISTAKE. Swallowing a transient failure is
right: a cloud call that times out once should not take an entity down,
and a warning per hiccup trains people to ignore warnings.

What it could not distinguish is a failure that has NEVER succeeded.
That is not a hiccup, it is a defect, and it deserves to be loud exactly
once.

WHY "NEVER SUCCEEDED" IS THE RIGHT TEST. It needs no threshold anyone
has to justify and no timer. A site that has worked before is having a
bad moment; a site that has worked never is broken. The count only
guards against calling a cold start a defect -- the first attempt of
anything can fail for reasons that resolve themselves.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Failures at a site that has never succeeded, before it is called out.
#: Two rather than one, because a first attempt can fail during startup
#: for reasons that clear -- and rather than five, because a defect
#: should not need five chances to be noticed.
_ESCALATE_AFTER = 2

#: site -> [failures since the last success, ever succeeded, reported]
_STATE: dict[str, list[Any]] = {}


def reset_for_tests() -> None:
    """Forgets everything. Only for tests, which must not leak into
    each other through module state."""
    _STATE.clear()


def record_success(site: str) -> None:
    """This code path works, so future failures here are transient."""
    entry = _STATE.setdefault(site, [0, False, False])
    entry[0] = 0
    entry[1] = True


def record_failure(site: str, detail: str = "") -> bool:
    """Records a failure and says whether it looks structural.

    Returns True exactly once per site, on the failure that crosses the
    threshold while the site has never succeeded. Once is deliberate:
    the point is to be noticed, not to fill a log.
    """
    entry = _STATE.setdefault(site, [0, False, False])
    entry[0] += 1
    if entry[1] or entry[2] or entry[0] < _ESCALATE_AFTER:
        return False
    entry[2] = True
    _LOGGER.warning(
        "roomba_plus: %s has failed %d times and has never once succeeded. "
        "That is not a hiccup -- it is a code path that cannot work, and it "
        "will look like missing data rather than an error. Please report this "
        "with a diagnostics download.%s",
        site, entry[0], f" Detail: {detail}" if detail else "",
    )
    return True


@contextlib.contextmanager
def swallow(site: str, detail: str = "") -> Iterator[None]:
    """Swallows an exception, but not silently forever.

    Drop-in for the `try/except Exception: _LOGGER.debug(...)` this
    project has thirty-eight of. Transient failures stay at DEBUG;
    a path that has never worked says so once, at WARNING.

    Deliberately catches Exception rather than a narrow set. The point
    is not to be selective about what goes wrong -- it is to notice that
    something ALWAYS goes wrong here.
    """
    try:
        yield
    except Exception:  # noqa: BLE001
        # The traceback goes to DEBUG either way -- the warning says
        # THAT something is structurally broken, the debug line says
        # what. Someone acting on the warning needs both.
        record_failure(site, detail)
        _LOGGER.debug("roomba_plus: %s failed", site, exc_info=True)
    else:
        record_success(site)


def diagnostic_info() -> dict[str, dict[str, Any]]:
    """What has never worked, for a diagnostics download.

    Only the sites that have failed and never succeeded -- a healthy
    install produces an empty dict, and anything listed here is a lead.
    """
    return {
        site: {"failures": entry[0], "ever_succeeded": entry[1]}
        for site, entry in _STATE.items()
        if entry[0] and not entry[1]
    }
