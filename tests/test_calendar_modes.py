"""Recognising a cleaning mode in free text a user typed.

Home Assistant does not translate calendar event summaries -- `Cleaning`
is hardcoded English -- so the label written back is English whatever
the user's language. Reading only English would make the recognition
English too, which would be a step backwards from the room matcher,
where names come from the robot and therefore work everywhere.

So: recognised in eight languages, displayed in one.
"""

import pytest


def _match(text):
    from custom_components.roomba_plus.calendar_modes import match_mode

    return match_mode(text)


class TestTheEightLanguages:
    """The set is closed and tiny -- four modes -- which is what makes
    keyword matching defensible here. This does not interpret a
    sentence; it looks for words from a list."""

    @pytest.mark.parametrize("text", [
        "vacuum the kitchen", "Salon saugen", "aspiration du salon",
        "aspirar la cocina", "aspirare il salotto", "stofzuigen",
        "odkurzanie kuchni", "aspirar a cozinha",
    ])
    def test_vacuum_in_every_language(self, text):
        from custom_components.roomba_plus.calendar_modes import MODE_VACUUM

        assert _match(text) == MODE_VACUUM

    @pytest.mark.parametrize("text", [
        "mop the hall", "Küche wischen", "lavage du sol",
        "fregar el suelo", "lavare il pavimento", "dweilen",
        "mopowanie", "esfregar a cozinha",
    ])
    def test_mop_in_every_language(self, text):
        from custom_components.roomba_plus.calendar_modes import MODE_MOP

        assert _match(text) == MODE_MOP

    def test_accents_and_case_do_not_matter(self):
        """A French or Polish user should not be punished for typing
        their own language properly."""
        assert _match("ASPIRATION") == _match("aspiration")

    def test_conjugations_are_caught(self):
        """Stems rather than full forms: someone writing a conjugated
        verb should not fall through to a different mode."""
        from custom_components.roomba_plus.calendar_modes import MODE_VACUUM

        assert _match("der Roboter saugt") == MODE_VACUUM
        assert _match("aspirer") == MODE_VACUUM


class TestTheTwoCombinedModes:
    """A word for "then" between the verbs means vacuum-then-mop; both
    verbs without one means both at once. That mirrors how the modes
    actually differ."""

    def _mode(self, text):
        return _match(text)

    @pytest.mark.parametrize("text", [
        "vacuum then mop", "saugen dann wischen", "aspiration puis lavage",
        "stofzuigen dan dweilen", "aspirare poi lavare",
    ])
    def test_sequential(self, text):
        from custom_components.roomba_plus.calendar_modes import (
            MODE_VACUUM_THEN_MOP,
        )

        assert self._mode(text) == MODE_VACUUM_THEN_MOP

    @pytest.mark.parametrize("text", [
        "vacuum and mop", "saugen und wischen", "aspiration et lavage",
    ])
    def test_simultaneous(self, text):
        from custom_components.roomba_plus.calendar_modes import (
            MODE_VACUUM_AND_MOP,
        )

        assert self._mode(text) == MODE_VACUUM_AND_MOP


class TestSayingNothingChangesNothing:
    """None means "leave it alone" -- the caller keeps whatever the
    schedule it derived from was using. Guessing would change what the
    robot does to the floor on the strength of a word that might not
    have been meant that way."""

    @pytest.mark.parametrize("text", [
        "Küche", "Tuesday clean", "Salon, Salle à manger", "", None,
    ])
    def test_no_mode_word_yields_none(self, text):
        assert _match(text) is None


class TestTheModeIsWrittenToEveryRegion:
    """A schedule with mixed modes is possible on paper and has never
    been seen. A user who writes "mop" means the whole run -- leaving
    some rooms on the old mode would be a schedule nobody asked for."""

    def _applied(self, commands, mode=4):
        from custom_components.roomba_plus.prime_schedule_services import (
            _set_operating_mode,
        )

        return _set_operating_mode(commands, mode)

    def test_every_region_gets_it(self):
        commands = [{"command": {"regions": [
            {"region_id": "1", "params": {"operatingMode": 2}},
            {"region_id": "2", "params": {"operatingMode": 2}},
        ]}}]

        result = self._applied(commands)

        modes = [
            r["params"]["operatingMode"]
            for r in result[0]["command"]["regions"]
        ]
        assert modes == [4, 4]

    def test_a_region_without_params_gets_them(self):
        """It inherits the robot's global settings otherwise, and adding
        a mode there is exactly the intent."""
        commands = [{"command": {"regions": [{"region_id": "1"}]}}]

        result = self._applied(commands)

        assert result[0]["command"]["regions"][0]["params"]["operatingMode"] == 4

    def test_other_params_survive(self):
        commands = [{"command": {"regions": [
            {"region_id": "1", "params": {"operatingMode": 2, "suctionLevel": 3}},
        ]}}]

        result = self._applied(commands)

        assert result[0]["command"]["regions"][0]["params"]["suctionLevel"] == 3

    def test_the_original_is_not_mutated(self):
        commands = [{"command": {"regions": [
            {"region_id": "1", "params": {"operatingMode": 2}},
        ]}}]

        self._applied(commands)

        assert commands[0]["command"]["regions"][0]["params"]["operatingMode"] == 2
