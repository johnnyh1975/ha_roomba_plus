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


class TestVendorCapabilitiesAreReportedNotEnforced:
    """`cwia` says whether iRobot's own "Clean While Away" exists on this
    robot. It does **not** say whether ours works — ours disables
    schedules through `enabled`, which every Prime robot can do.

    **Using the flag as a gate would hide a working feature.**
    `ddAutomation` is the same shape: it says iRobot offers Dirt
    Detective, not that `clean_score` is missing.

    What it is good for is a report: somebody comparing our presence
    scheduling against the app's can see in one line whether the app has
    one at all.
    """

    def _caps(self, shadows):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _vendor_capabilities

        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_status_coordinator=SimpleNamespace(data=shadows)
        )
        return _vendor_capabilities(entry)

    def test_all_three_families_are_reported(self):
        """App 3.0.0 gates 35 features and they sit in three places:
        `digiCap.*` for software, `cap.*` for the robot, `dock.cap.*`
        for the dock. Showing one and hiding two invites the wrong
        conclusion about the two."""
        caps = self._caps({"shadow": {
            "digiCap": {"cwia": True, "ddAutomation": False},
            "cap": {"scrub": 1, "multiPass": 2},
            "dock": {"cap": {"evac": 1, "pwo": 0}},
        }})

        assert caps["digiCap.cwia"] is True
        assert caps["digiCap.ddAutomation"] is False
        assert caps["cap.scrub"] == 1
        assert caps["dock.cap.evac"] == 1

    def test_the_prefix_keeps_the_families_apart(self):
        """`cap.matter` and `digiCap.matter` are different questions --
        hardware support and software support -- and a flat key would
        lose one."""
        caps = self._caps({"shadow": {
            "cap": {"matter": 1}, "digiCap": {"matter": False},
        }})

        assert caps["cap.matter"] == 1
        assert caps["digiCap.matter"] is False

    def test_a_robot_without_the_block_reports_nothing(self):
        """An absent block is not a robot that lacks the features."""
        assert self._caps({"shadow": {}}) == {}
        assert self._caps(None) == {}

    def test_nothing_gates_on_them(self):
        """The whole point. If a future change starts refusing a feature
        because a flag is false, this test should be the thing that
        objects."""
        import inspect

        from custom_components.roomba_plus import presence_manager
        from custom_components.roomba_plus import dirt_threshold_manager

        for module in (presence_manager, dirt_threshold_manager):
            source = inspect.getsource(module)
            assert "cwia" not in source, module.__name__
            assert "ddAutomation" not in source, module.__name__


class TestTheGatesLiveInThreeShadows:
    """`capabilityFromKey` gates 35 features and they are not all in one
    place: 28 read the unnamed THING shadow (`cap.*`, `digiCap.*`), five
    read `ro-currentstate` (`dock.cap.*`), and two read `rw-settings`
    (`detergent`, `suctionLevel`).

    The last two are plain top-level keys rather than a block, which is
    why a scan for three nested families missed them.
    """

    def _caps(self, shadows):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import _vendor_capabilities

        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(
            prime_status_coordinator=SimpleNamespace(data=shadows)
        )
        return _vendor_capabilities(entry)

    def test_the_dock_gates_are_found_in_current_state(self):
        caps = self._caps({
            "ro-currentstate": {"dock": {"cap": {"evac": 1, "pwo": 0, "fr": 1}}},
        })

        assert caps["dock.cap.evac"] == 1
        assert caps["dock.cap.pwo"] == 0

    def test_the_settings_gates_are_found_flat(self):
        """`detergent` and `suctionLevel` are top-level in `rw-settings`,
        not inside a `cap` block."""
        caps = self._caps({"rw-settings": {"detergent": 1, "suctionLevel": 3}})

        assert caps["detergent"] == 1
        assert caps["suctionLevel"] == 3

    def test_all_three_shadows_contribute_at_once(self):
        caps = self._caps({
            "shadow": {"digiCap": {"cwia": True}, "cap": {"scrub": 1}},
            "ro-currentstate": {"dock": {"cap": {"pw": 1}}},
            "rw-settings": {"detergent": 0},
        })

        assert {"digiCap.cwia", "cap.scrub", "dock.cap.pw", "detergent"} <= set(caps)

    def test_a_robot_reporting_none_of_them_gets_an_empty_block(self):
        assert self._caps({"rw-settings": {"name": "Robot"}}) == {}


class TestFavouritesAreReportedInDiagnostics:
    """@chairstacker's two favourites appear as buttons on v3.5.1 and
    not on the alpha. Everything between the fetch and the entities is
    wired correctly, so the answer is either "the option is off" or "the
    list arrived empty" — **and his report had no way to tell those
    apart.**

    Neither does a maintainer reading it, which is why this is here.
    """

    def _diag(self, favourites, option=None):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from custom_components.roomba_plus.diagnostics import (
            _vendor_capabilities,  # noqa: F401 — same module
        )
        import custom_components.roomba_plus.diagnostics as diag

        entry = MagicMock()
        entry.runtime_data = SimpleNamespace(prime_favorites=favourites)
        entry.options = {} if option is None else {
            "prime_favorite_buttons": option
        }
        return entry, diag

    # THESE USED TO ASSERT ON THE FIXTURE AND ON SOURCE TEXT, so none
    # of them called the code they were named after. That is how the
    # Classic branch got away with reading `prime_favorites` -- a
    # structural zero on every Classic robot, which @pk-1966's i7+
    # download reported next to a cloud section saying six. A source
    # check passes whether the block is right or wrong; these call it.

    @staticmethod
    def _run(data, options=None):
        import asyncio
        from types import SimpleNamespace

        from custom_components.roomba_plus.diagnostics import _favourites_diagnostics

        return asyncio.run(
            _favourites_diagnostics(data, SimpleNamespace(options=options or {}))
        )

    def test_the_count_distinguishes_empty_from_disabled(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        robot = SimpleNamespace(get_favorites_raw=AsyncMock(return_value={}))
        data = SimpleNamespace(
            prime_robot=robot,
            prime_favorites=[{"id": "F1"}, {"id": "F2"}],
            blid="B",
        )

        result = self._run(data, {"prime_favorite_buttons": False})

        # Two arrived; the buttons are off. Two separate facts, and a
        # report that shows only one cannot tell them apart.
        assert result["count"] == 2
        assert result["buttons_enabled"] is False

    def test_an_empty_list_is_reported_as_zero_not_absent(self):
        """Zero favourites and no favourites block look the same to a
        reader; zero is the one that means something."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        robot = SimpleNamespace(get_favorites_raw=AsyncMock(return_value={}))
        data = SimpleNamespace(prime_robot=robot, prime_favorites=[], blid="B")

        assert self._run(data)["count"] == 0

    def test_the_block_names_both_halves(self):
        """Count and buttons_enabled, whichever tier answered."""
        from types import SimpleNamespace

        data = SimpleNamespace(
            prime_robot=None,
            blid="B",
            cloud_coordinator=SimpleNamespace(data={"favorites": [{"n": 1}]}),
        )

        result = self._run(data)

        assert "count" in result
        assert "buttons_enabled" in result
        # And which half it came from, which neither test asked before.
        assert result["source"] == "cloud_coordinator (Classic)"
