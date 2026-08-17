"""Why a capability is not being offered.

@connormxy's `vacuum.clean_area` did not appear. Home Assistant filtered
his robot out of its own service picker, because our `supported_features`
did not include the flag -- **no error, no log line, nothing to search
for**. He uninstalled three integrations and reconfigured from scratch
to find out that one word in our code was wrong.

The decision to withhold a capability is usually right: a Braava has no
region segments to clean, an older Home Assistant has no `CLEAN_AREA` to
advertise. What is wrong is that a correct decision and a bug look
identical from outside -- both are simply an absent button.

SO THIS EXPLAINS RATHER THAN WARNS. It goes in the diagnostics download,
which comes with every report anyway and bothers nobody who never wanted
the capability. A repair issue would be louder and worse: a Braava owner
would carry a permanent notice about a feature their robot cannot have.

Each entry says what is missing and which condition withheld it. A
reader who disagrees with the reason has found either a bug in the
condition or a robot we misjudged, and both are worth a report.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.vacuum import VacuumEntityFeature

from .const import is_braava
from .room_cleaning import async_get_room_cleaning_backend

_LOGGER = logging.getLogger(__name__)


def clean_area_status(config_entry: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Whether `vacuum.clean_area` is offered, and if not, why.

    The conditions are read in the same order the vacuum entity applies
    them, and only the FIRST failing one is reported -- a robot that is
    both a Braava and short of room data is a Braava, and saying so
    twice would suggest two problems.
    """
    if not hasattr(VacuumEntityFeature, "CLEAN_AREA"):
        return {
            "offered": False,
            "reason": "home_assistant_too_old",
            "detail": (
                "VacuumEntityFeature.CLEAN_AREA arrived in Home Assistant "
                "2026.3; this version does not have it."
            ),
        }
    if is_braava(state):
        return {
            "offered": False,
            "reason": "braava",
            "detail": (
                "A Braava targets rooms through pad wetness rather than "
                "region segments, so there is nothing for clean_area to "
                "address."
            ),
        }
    try:
        backend = async_get_room_cleaning_backend(config_entry)
    except Exception:  # noqa: BLE001
        # A failure to decide is itself worth reporting, and reporting
        # it is the whole point of this module.
        _LOGGER.debug("roomba_plus: could not evaluate room cleaning", exc_info=True)
        backend = None
    if backend is None:
        return {
            "offered": False,
            "reason": "no_room_data",
            "detail": (
                "No room list is available. On a locally-connected Classic "
                "robot this comes from the iRobot cloud login or from stored "
                "smart-zone data; the robot knowing its own rooms is not "
                "enough."
            ),
        }
    return {"offered": True}


def withheld_features(config_entry: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Every capability this robot is not being offered, with its reason.

    Capabilities that ARE offered are left out. A list of everything
    working is a statistic; a list of what is missing is a lead.
    """
    result: dict[str, Any] = {}
    status = clean_area_status(config_entry, state)
    if not status.get("offered"):
        result["clean_area"] = status
    return result
