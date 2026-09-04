"""Removing entities for things the robot no longer has.

@ScenicSystemsLLC: five stale favourite entities on each of three
robots, `unavailable` with `restored: true`, deleted in the iRobot app
and still listed in Home Assistant. Nothing in this integration had
ever removed an entity.

The risk is the opposite mistake: a robot that is offline reports
nothing, and treating silence as "these are obsolete" would delete a
user's dashboard on every disconnection.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _entry(unique_id, entity_id):
    return SimpleNamespace(unique_id=unique_id, entity_id=entity_id)


def _run(entries, *, prefix, live):
    from custom_components.roomba_plus.entity_cleanup import (
        async_remove_stale_entities,
    )

    registry = MagicMock()
    config_entry = MagicMock()
    config_entry.entry_id = "abc"

    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        return_value=registry,
    ), patch(
        "homeassistant.helpers.entity_registry."
        "async_entries_for_config_entry",
        return_value=entries,
    ):
        removed = async_remove_stale_entities(
            MagicMock(), config_entry, prefix=prefix, live_unique_ids=live
        )

    gone = {c.args[0] for c in registry.async_remove.call_args_list}
    return removed, gone


class TestOnlyTheOnesNoLongerReported:
    def test_a_deleted_favourite_is_removed(self):
        removed, gone = _run(
            [
                _entry("roomba_plus_B1_favorite_aaa", "button.fav_a"),
                _entry("roomba_plus_B1_favorite_bbb", "button.fav_b"),
            ],
            prefix="_favorite_",
            live={"roomba_plus_B1_favorite_aaa"},
        )

        assert removed == 1
        assert gone == {"button.fav_b"}

    def test_a_part_the_robot_stopped_reporting_goes(self):
        removed, gone = _run(
            [
                _entry("roomba_plus_B1_prime_part_71", "sensor.p71"),
                _entry("roomba_plus_B1_prime_part_72", "sensor.p72"),
            ],
            prefix="_prime_part_",
            live={"roomba_plus_B1_prime_part_71"},
        )

        assert gone == {"sensor.p72"}


class TestWhatMustNotBeTouched:
    def test_an_empty_list_removes_nothing(self):
        """A robot with no favourites and a favourites fetch that failed
        look identical from here. Deleting on the second would cost a
        user their dashboard on every disconnection."""
        removed, gone = _run(
            [_entry("roomba_plus_B1_favorite_aaa", "button.fav_a")],
            prefix="_favorite_",
            live=set(),
        )

        assert removed == 0
        assert gone == set()

    def test_other_kinds_are_left_alone(self):
        """A robot with no favourites must not lose its sensors because
        the favourites list came back empty of the ids they carry."""
        removed, gone = _run(
            [
                _entry("roomba_plus_B1_prime_part_71", "sensor.p71"),
                _entry("roomba_plus_B1_battery", "sensor.battery"),
                _entry("roomba_plus_B1_favorite_ccc", "button.fav_c"),
            ],
            prefix="_favorite_",
            live={"roomba_plus_B1_favorite_aaa"},
        )

        assert gone == {"button.fav_c"}

    def test_an_entity_with_no_unique_id_is_skipped(self):
        removed, gone = _run(
            [_entry(None, "sensor.odd")],
            prefix="_favorite_",
            live={"roomba_plus_B1_favorite_aaa"},
        )

        assert removed == 0
