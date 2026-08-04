"""Choosing which map the Rooms Map shows (issue #45, @chairstacker).

He has two maps -- "Whole House" with seven rooms and "Master_Bathroom"
with one -- and only one was ever reachable. The image renders the map
the robot reports standing on and falls back to the first.

A SELECT RATHER THAN ONE IMAGE ENTITY PER MAP, decided by the person who
would live with it. Entity-per-map looks better on paper, because two
dashboards could then show different floors. His case inverts that: he
uses one map constantly and the other rarely, and an entity per map
gives no way to keep the rare one off a dashboard at all.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _select(*, versions=None, selected=None, restore=None):
    from custom_components.roomba_plus.select_prime import PrimeMapSelect

    entity = object.__new__(PrimeMapSelect)
    entity._blid = "BLID"
    entity._names = {}
    robot = AsyncMock()
    robot.get_active_map_versions.return_value = versions or []
    entry = MagicMock()
    entry.runtime_data = SimpleNamespace(
        prime_robot=robot, prime_selected_map_id=selected
    )
    entity._config_entry = entry
    entity.async_write_ha_state = MagicMock()
    if restore is not None:
        entity._restore = restore
    return entity


_MAPS = [
    {"p2map_id": "M_HOUSE", "name": "Whole House"},
    {"p2map_id": "M_BATH", "name": "Master_Bathroom"},
]


class TestTheOptionsAreMapNames:
    @pytest.mark.asyncio
    async def test_names_not_ids(self):
        """`hh_..._1752720067` tells a user nothing."""
        entity = _select(versions=_MAPS)

        await entity._async_load_names()

        assert entity.options == [
            entity.FOLLOW_ROBOT, "Whole House", "Master_Bathroom"
        ]

    @pytest.mark.asyncio
    async def test_an_unnamed_map_falls_back_to_its_id(self):
        """Ugly and honest. Inventing "Map 2" would put a label on screen
        that matches nothing in the iRobot app."""
        entity = _select(versions=[{"p2map_id": "M_X"}])

        await entity._async_load_names()

        assert "M_X" in entity.options

    @pytest.mark.asyncio
    async def test_a_failed_read_leaves_follow_the_robot(self):
        """The account keeps working; only the choice is unavailable."""
        entity = _select()
        entity._config_entry.runtime_data.prime_robot.get_active_map_versions = (
            AsyncMock(side_effect=TimeoutError())
        )

        await entity._async_load_names()

        assert entity.options == [entity.FOLLOW_ROBOT]


class TestFollowingTheRobotIsTheDefault:
    """A user who never touches this sees exactly what they saw before it
    existed."""

    def test_no_selection_reads_as_follow(self):
        assert _select().current_option == "follow_robot"

    @pytest.mark.asyncio
    async def test_choosing_follow_clears_the_selection(self):
        entity = _select(versions=_MAPS, selected="M_BATH")
        await entity._async_load_names()

        await entity.async_select_option(entity.FOLLOW_ROBOT)

        assert entity._config_entry.runtime_data.prime_selected_map_id is None


class TestPickingAMap:
    @pytest.mark.asyncio
    async def test_the_name_is_translated_back_to_an_id(self):
        entity = _select(versions=_MAPS)
        await entity._async_load_names()

        await entity.async_select_option("Master_Bathroom")

        assert entity._config_entry.runtime_data.prime_selected_map_id == "M_BATH"

    @pytest.mark.asyncio
    async def test_an_unknown_name_is_refused(self):
        from homeassistant.exceptions import ServiceValidationError

        entity = _select(versions=_MAPS)
        await entity._async_load_names()

        with pytest.raises(ServiceValidationError):
            await entity.async_select_option("Upstairs")

    @pytest.mark.asyncio
    async def test_a_map_deleted_in_the_app_falls_back(self):
        """A selection pointing at nothing would otherwise report an
        option outside the list, which Home Assistant logs as an error on
        every state write."""
        entity = _select(versions=_MAPS, selected="M_GONE")
        await entity._async_load_names()

        assert entity.current_option == "follow_robot"


class TestTheChoiceSurvivesARestart:
    @pytest.mark.asyncio
    async def test_a_restored_name_becomes_a_selection(self):
        entity = _select(versions=_MAPS, restore="Master_Bathroom")

        await entity._async_load_names()

        assert entity._config_entry.runtime_data.prime_selected_map_id == "M_BATH"

    @pytest.mark.asyncio
    async def test_a_restored_name_that_no_longer_exists_is_ignored(self):
        entity = _select(versions=_MAPS, restore="Guest Floor")

        await entity._async_load_names()

        assert entity._config_entry.runtime_data.prime_selected_map_id is None
