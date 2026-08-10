"""Why a capability is not being offered.

@connormxy's `vacuum.clean_area` did not appear. Home Assistant filtered
his robot out of its own service picker because our `supported_features`
did not include the flag -- no error, no log line, nothing to search
for. He uninstalled three integrations and reconfigured from scratch to
find out that one word in our code was wrong.

The decision to withhold is usually right. What is wrong is that a
correct decision and a bug look identical from outside: both are simply
an absent button.
"""

from unittest.mock import MagicMock, patch

import pytest


def _status(state, backend=object(), has_flag=True):
    from custom_components.roomba_plus import withheld_features as wf

    with patch.object(wf, "async_get_room_cleaning_backend", return_value=backend):
        if not has_flag:
            with patch.object(wf, "VacuumEntityFeature", MagicMock(spec=[])):
                return wf.clean_area_status(MagicMock(), state)
        return wf.clean_area_status(MagicMock(), state)


class TestAWorkingRobotExplainsNothing:
    """A list of everything working is a statistic; a list of what is
    missing is a lead."""

    def test_a_capable_robot_is_simply_offered_it(self):
        assert _status({"sku": "c755020"}) == {"offered": True}

    def test_and_appears_nowhere_in_the_block(self):
        from custom_components.roomba_plus import withheld_features as wf

        with patch.object(wf, "async_get_room_cleaning_backend", return_value=object()):
            assert wf.withheld_features(MagicMock(), {"sku": "c755020"}) == {}


class TestEachReasonIsNamed:
    def test_a_braava_is_told_it_is_a_braava(self):
        status = _status({"sku": "m613840", "detectedPad": "reusableWet"})

        assert status["reason"] == "braava"
        assert "pad wetness" in status["detail"]

    def test_a_missing_room_list_says_where_it_comes_from(self):
        """@connormxy's actual case, had the Combo bug not been there:
        the robot knowing its own rooms is not enough, and the detail
        says so rather than leaving him to guess."""
        status = _status({"sku": "i755840"}, backend=None)

        assert status["reason"] == "no_room_data"
        assert "iRobot cloud login" in status["detail"]

    def test_an_old_home_assistant_says_which_version(self):
        status = _status({"sku": "i755840"}, has_flag=False)

        assert status["reason"] == "home_assistant_too_old"
        assert "2026.3" in status["detail"]


class TestOnlyTheFirstReasonIsGiven:
    """A robot that is both a Braava and short of room data is a Braava.
    Saying so twice would suggest two problems."""

    def test_a_braava_without_room_data_reports_only_the_braava(self):
        status = _status(
            {"sku": "m613840", "detectedPad": "reusableWet"}, backend=None
        )

        assert status["reason"] == "braava"


class TestTheBlockNeverBreaksTheDownload:
    """A diagnostics download that fails because of its own explanatory
    block would be worse than one without it."""

    def test_a_failure_is_reported_rather_than_swallowed(self):
        from custom_components.roomba_plus.diagnostics import _withheld_features

        entry = MagicMock()
        with patch(
            "custom_components.roomba_plus.diagnostics.withheld_features",
            side_effect=RuntimeError("boom"),
        ):
            result = _withheld_features(entry, {})

        assert "RuntimeError" in result["error"]

    def test_an_unevaluable_backend_still_produces_a_reason(self):
        """A failure to decide is itself worth reporting."""
        from custom_components.roomba_plus import withheld_features as wf

        with patch.object(
            wf, "async_get_room_cleaning_backend", side_effect=TypeError("x")
        ):
            status = wf.clean_area_status(MagicMock(), {"sku": "i755840"})

        assert status["reason"] == "no_room_data"


class TestItIsInTheDiagnostics:
    def test_the_key_is_present(self):
        import inspect

        from custom_components.roomba_plus import diagnostics

        assert '"withheld_features"' in inspect.getsource(diagnostics)
