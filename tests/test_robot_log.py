"""robot_log: log lines that say which robot they are about (4.6.13)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from custom_components.roomba_plus.robot_log import robot_logger, robot_name


class TestRobotLogger:
    """@lloydbayley has several robots, and warnings named none of them."""

    def test_the_message_starts_with_the_robot(self, caplog):
        logger = logging.getLogger("custom_components.roomba_plus.test_robot_log")
        entry = SimpleNamespace(title="Upstairs", entry_id="0123456789ABCDEF")
        with caplog.at_level(logging.WARNING, logger=logger.name):
            robot_logger(logger, entry).warning("map failed: %s", "disk full")
        assert caplog.records[-1].getMessage() == "Upstairs: map failed: disk full"

    def test_a_percent_in_the_name_takes_no_argument(self, caplog):
        logger = logging.getLogger("custom_components.roomba_plus.test_robot_log")
        entry = SimpleNamespace(title="100% clean", entry_id="e")
        with caplog.at_level(logging.WARNING, logger=logger.name):
            robot_logger(logger, entry).warning("phase %r", "run")
        assert caplog.records[-1].getMessage() == "100% clean: phase 'run'"

    def test_without_a_title_the_end_of_the_entry_id(self):
        assert robot_name(SimpleNamespace(title="", entry_id="0123456789ABCDEF")) == "entry …CDEF"
        assert robot_name(SimpleNamespace(title=None, entry_id=None)) == "unknown robot"
        assert robot_name(None) == "unknown robot"
