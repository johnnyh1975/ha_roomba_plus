"""Log lines that say which robot they are about (4.6.13).

With several robots, a warning such as "Map: failed to load stored
state" names none of them, and the user has to guess which one to look
at (@lloydbayley). `robot_logger(_LOGGER, entry)` returns the module's
logger with the robot's name in front of every message, so a call site
changes from `_LOGGER.warning(...)` to
`robot_logger(_LOGGER, entry).warning(...)` and nothing else.

A leaf module: it imports nothing from the package, so any module can
use it without adding to an import cycle.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any


class _RobotAdapter(logging.LoggerAdapter):  # type: ignore[type-arg]
    """Puts "<robot>: " in front of the message."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        return f"{self.extra['robot']}: {msg}", kwargs  # type: ignore[index]


def robot_name(entry: Any) -> str:
    """The name a log line uses for the robot of `entry`.

    The entry's title, else the last four characters of its id. A `%` is
    doubled: the name goes into the format string, and a robot called
    "100% clean" must not swallow an argument.
    """
    title = getattr(entry, "title", None)
    if not isinstance(title, str) or not title:
        entry_id = getattr(entry, "entry_id", None)
        title = f"entry …{str(entry_id)[-4:]}" if entry_id else "unknown robot"
    return title.replace("%", "%%")


def robot_logger(logger: logging.Logger, entry: Any) -> logging.LoggerAdapter:  # type: ignore[type-arg]
    """`logger`, with the robot of `entry` named in every message."""
    return _RobotAdapter(logger, {"robot": robot_name(entry)})
