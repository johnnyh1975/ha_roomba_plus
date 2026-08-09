"""Telling a failure that happens once from one that always happens.

Six faults in four days shared one shape: a name or signature that
disagreed across a boundary, an `except Exception` filing the result at
DEBUG, and a symptom that read as "there is nothing here" rather than
"something failed". Not one was found by us; each took a user reporting
a missing feature.

The `except` is not the mistake. Swallowing a transient failure is
right. What it could not distinguish is a failure that has NEVER
succeeded -- and that is a defect, not a hiccup.
"""

import pytest


@pytest.fixture(autouse=True)
def _clean():
    from custom_components.roomba_plus.structural_failures import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


def _swallow(site, fail=True):
    from custom_components.roomba_plus.structural_failures import swallow

    with swallow(site):
        if fail:
            raise TypeError("missing 1 required positional argument")


class TestAPathThatNeverWorksBecomesLoud:
    def test_one_failure_is_not_enough(self):
        """A first attempt can fail during startup for reasons that
        clear. Calling that a defect would train people to ignore the
        warning."""
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        _swallow("history import")

        assert "history import" in diagnostic_info()

    def test_the_second_failure_reports_it(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            _swallow("history import")
            _swallow("history import")

        assert "never once succeeded" in caplog.text

    def test_it_reports_once_and_not_again(self, caplog):
        """The point is to be noticed, not to fill a log. A path broken
        on an hourly coordinator would otherwise warn twenty-four times a
        day."""
        import logging

        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                _swallow("history import")

        assert caplog.text.count("never once succeeded") == 1


class TestAPathThatHasWorkedStaysQuiet:
    def test_failures_after_a_success_are_transient(self, caplog):
        """A cloud call that times out should not take an entity down,
        and a warning per hiccup is how warnings become noise."""
        import logging

        with caplog.at_level(logging.WARNING):
            _swallow("cloud read", fail=False)
            for _ in range(10):
                _swallow("cloud read")

        assert "never once succeeded" not in caplog.text

    def test_a_working_path_is_not_in_the_diagnostics(self):
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        _swallow("cloud read", fail=False)
        _swallow("cloud read")
        _swallow("cloud read")

        assert diagnostic_info() == {}

    def test_a_later_success_clears_the_count(self):
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        _swallow("cloud read")
        _swallow("cloud read", fail=False)

        assert diagnostic_info() == {}


class TestSitesAreIndependent:
    def test_one_broken_path_does_not_implicate_another(self, caplog):
        import logging

        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        with caplog.at_level(logging.WARNING):
            _swallow("broken", fail=True)
            _swallow("broken", fail=True)
            _swallow("healthy", fail=False)

        assert list(diagnostic_info()) == ["broken"]


class TestTheExceptionStillDoesNotEscape:
    """Whatever else changes, the caller must not start seeing
    exceptions it never saw before -- that would turn a quiet defect into
    a broken entity."""

    def test_nothing_propagates(self):
        _swallow("anything")
        _swallow("anything")
        _swallow("anything")

    def test_a_success_path_runs_to_the_end(self):
        from custom_components.roomba_plus.structural_failures import swallow

        reached = []
        with swallow("fine"):
            reached.append(1)

        assert reached == [1]


class TestTheSitesThatWereActuallyBroken:
    """The two that cost users a feature now record both outcomes.
    Recording only failures would report a working path as broken the
    first two times a cloud call timed out."""

    def test_a_broken_history_import_is_reported(self):
        """Observed, not read out of the source: a call that always
        raises must end up in the diagnostics."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus import prime_mission_sync
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        robot = AsyncMock()
        robot.blid = "B"
        robot.get_mission_history.side_effect = TypeError("missing argument")

        for _ in range(2):
            asyncio.run(prime_mission_sync._async_sync_locked(
                MagicMock(), robot, MagicMock()
            ))

        assert "mission history import" in diagnostic_info()

    def test_a_working_history_import_is_not(self):
        """The success side, which matters as much: without it a healthy
        path would be reported broken after two slow cloud calls."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus import prime_mission_sync
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        robot = AsyncMock()
        robot.blid = "B"
        robot.get_mission_history.return_value = []

        asyncio.run(prime_mission_sync._async_sync_locked(
            MagicMock(), robot, MagicMock()
        ))

        assert diagnostic_info() == {}

    def test_a_broken_favourite_read_is_reported(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        from custom_components.roomba_plus.button_prime import (
            async_favorites_attribute,
        )
        from custom_components.roomba_plus.structural_failures import (
            diagnostic_info,
        )

        robot = AsyncMock()
        robot.get_favorites.side_effect = KeyError("favoriteid")
        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_robot=robot, blid="B")

        for _ in range(2):
            asyncio.run(async_favorites_attribute(entry))

        assert "favourite list" in diagnostic_info()

    def test_diagnostics_carries_the_block(self):
        """Read from the produced dict rather than from the source."""
        from custom_components.roomba_plus.diagnostics import (
            _structural_diagnostics,
        )

        _swallow("some path")
        _swallow("some path")

        assert "some path" in _structural_diagnostics()
