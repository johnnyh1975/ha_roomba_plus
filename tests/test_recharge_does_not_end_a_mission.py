"""A recharge mid-mission is not the end of the mission.

FROM A REAL RUN, and from an independent implementation making the
opposite decision on the same data. @AlakazipLabs captured mission 519
on an i3 (daredevil 2.6.0): 79 minutes of cleaning, a self-return at
20% battery, 58 minutes on the dock with the mission still open, a
self-resume at 79%, 46 more minutes, then a genuine finish.

His own from-scratch mission logger -- not Home Assistant, a separate
SQLite table off the same shadow stream -- treated `phase: charge` as
terminal and closed the row at the recharge: **81 minutes recorded of a
189-minute run**. Two implementations, the same trap, arrived at
independently.

WHAT SEPARATES THE TWO IS `cycle`, NOT `phase`. Both moments are
`phase: charge`. The recharge carries `cycle: clean` -- the cleaning
cycle is still open -- and the real end carries `cycle: none`. `nMssn`
and `missionId` never change across the whole run, so neither can be
used to tell them apart.

This integration already reads it that way. Nothing here fixes
anything; it pins a discriminator that had no test, on a sequence where
getting it wrong costs two thirds of a mission.
"""
from __future__ import annotations

import pytest

from custom_components.roomba_plus.callbacks import _MISSION_END_PHASES
from custom_components.roomba_plus.const import ROOM_TRANSITION_CANDIDATE_PHASES

#: Mission 519, verbatim: every phase or cycle change across the run.
#: Local time, missionId constant throughout.
_MISSION_519 = [
    ("07:55:25", "charge", "clean", 518),   # previous mission still closed
    ("07:55:29", "run", "clean", 519),
    ("09:14:28", "hmMidMsn", "clean", 519), # heading home to recharge
    ("09:16:39", "charge", "clean", 519),   # ON THE DOCK, mission still open
    ("10:15:15", "run", "clean", 519),      # resumed, 58 minutes later
    ("11:01:24", "hmPostMsn", "clean", 519),
    ("11:03:54", "evac", "clean", 519),
    ("11:04:09", "hmPostMsn", "clean", 519),
    ("11:04:10", "charge", "none", 519),    # THE END
]


def _looks_like_end(phase: str, cycle: str) -> bool:
    """The integration's own test, as `make_mission_callback` applies it.

    REIMPLEMENTED, AND THAT IS A WEAKNESS WORTH NAMING. The real
    expression lives inline in a closure inside `make_mission_callback`,
    which needs a Home Assistant instance and a live coordinator to
    reach. So this mirrors it -- and a mirror cannot catch the
    production code changing underneath it.

    Two things narrow the gap. `_MISSION_END_PHASES` is imported from
    the module rather than copied, so the phase half is real. And the
    test below reads the source for the cycle half, which fails if the
    check is removed or its wording changes.
    """
    return phase in _MISSION_END_PHASES and cycle not in ("clean", "quick")


class TestTheRechargeIsNotTheEnd:
    def test_the_recharge_does_not_look_like_an_end(self) -> None:
        """09:16:39 -- on the dock, charging, 20% battery, and the
        mission has 46 more minutes of cleaning ahead of it."""
        assert not _looks_like_end("charge", "clean")

    def test_the_real_end_does(self) -> None:
        """11:04:10 -- the same phase, and this time it is over."""
        assert _looks_like_end("charge", "none")

    def test_exactly_one_moment_in_the_run_ends_it(self) -> None:
        """The whole sequence, in order. A second end would mean a
        mission recorded twice; none would mean one never closed."""
        ends = [
            (at, phase)
            for at, phase, cycle, _n in _MISSION_519
            if _looks_like_end(phase, cycle)
        ]

        assert ends == [("11:04:10", "charge")], (
            f"exactly one end expected, got {ends}"
        )

    @pytest.mark.parametrize("phase", sorted(ROOM_TRANSITION_CANDIDATE_PHASES))
    def test_no_ambiguous_phase_ends_a_mission_while_cleaning(
        self, phase: str
    ) -> None:
        """`charge` and `hmPostMsn` both appear mid-run in this capture
        -- `hmPostMsn` twice, once 46 minutes before the finish. Neither
        may end a mission while the cleaning cycle is still open."""
        assert not _looks_like_end(phase, "clean")


class TestTheCycleCheckIsStillThere:
    """Guard the half the mirror above cannot guard.

    Reading the source is a poor test and a good one here: the mirror
    would keep passing if somebody removed the cycle check from the
    production code, and removing it is exactly the mistake an
    independent implementation already made on this same data.
    """

    def test_the_callback_excludes_an_open_cleaning_cycle(self) -> None:
        import inspect

        from custom_components.roomba_plus import callbacks

        source = inspect.getsource(callbacks.make_mission_callback)

        assert '_cycle in ("clean", "quick")' in source, (
            "the mission-end test no longer excludes an open cleaning "
            "cycle -- a mid-mission recharge is `phase: charge` too, and "
            "without this it ends the mission 58 minutes early"
        )
        assert "not _is_inter_room_transition" in source
