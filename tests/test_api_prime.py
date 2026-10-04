"""The REST API on a Prime-only household (I1 of the card plan).

The views were registered by the Classic setup alone, so a household
with only Prime robots had no `/api/roomba_plus/*` routes -- the card's
history, household and digest views had nothing to call. Registering
them for Prime is one line; what needs proving is that every view
answers sensibly on a Prime entry's runtime data, which has a mission
store and no cloud coordinator, grid store, archive or local robot.

Built on a REAL RoombaData shaped like the one Prime setup builds, and
a real MissionStore with a record the way the Prime sync writes it. A
MagicMock runtime data answers every attribute with something truthy,
which is how the Prime room sensors stayed uncreated behind a passing
test (I2).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.roomba_plus import api_views
from custom_components.roomba_plus.const import DOMAIN
from custom_components.roomba_plus.mission_store import MissionStore
from custom_components.roomba_plus.models import ConnectionType, RoombaData

ENTRY_ID = "prime_entry"


def _prime_record(mission_id="M1", day="2026-10-02"):
    return {
        "id": f"p_{mission_id}",
        "started_at": f"{day}T09:00:00+00:00",
        "ended_at": f"{day}T09:42:00+00:00",
        "result": "completed",
        "duration_min": 42,
        "area_sqft": 430,
        "dirt": 3,
        "initiator": "schedule",
        "evacuations": 1,
        "room_durations_sec": {"7": 600.0, "9": 900.0, "12": 300.0},
    }


def _prime_entry(records=()):
    store = MissionStore()
    for rec in records:
        assert store.append_validated(rec)
    store.async_save = AsyncMock()
    entry = MagicMock()
    entry.domain = DOMAIN
    entry.entry_id = ENTRY_ID
    entry.title = "Combo 505"
    entry.options = {}
    entry.data = {"blid": "PRIMEBLID"}
    entry.runtime_data = RoombaData(
        blid="PRIMEBLID",
        roomba=None,
        connection_type=ConnectionType.CLOUD_ONLY,
        prime_robot=MagicMock(),
        mission_store=store,
    )
    entry.runtime_data.prime_room_names = {"7": "Kitchen", "9": "Hall"}
    return entry


def _hass(entry):
    hass = MagicMock()
    hass.config_entries.async_get_entry.side_effect = (
        lambda eid: entry if eid == ENTRY_ID else None
    )
    hass.config_entries.async_entries.return_value = [entry]
    return hass


def _req(hass, **query):
    request = MagicMock()
    request.app = {"hass": hass}
    request.query = query
    return request


def _body(resp):
    return json.loads(resp.body) if resp.body else None


@pytest.fixture
def entry():
    return _prime_entry([_prime_record("M1"), _prime_record("M2", "2026-10-03")])


class TestEveryViewAnswersOnAPrimeEntry:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fmt", ["summary", "records", "hazards", "export", "zone_coverage_health"]
    )
    async def test_mission_history_every_format(self, entry, fmt):
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format=fmt, days="90"), ENTRY_ID
        )
        assert resp.status == 200, _body(resp)
        assert _body(resp) is not None

    @pytest.mark.asyncio
    async def test_records_carry_the_prime_missions(self, entry):
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format="records", days="90"), ENTRY_ID
        )
        ids = {r.get("id") for r in _body(resp)}
        assert {"p_M1", "p_M2"} <= ids

    @pytest.mark.asyncio
    async def test_records_name_the_rooms_and_count_the_emptyings(self, entry):
        """A Prime record has no `zones` and writes `evacuations`, not
        the Classic merge's `evacs`: every mission came back roomless
        and without its bin emptyings."""
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format="records", days="90"), ENTRY_ID
        )
        record = next(r for r in _body(resp) if r["id"] == "p_M1")
        assert record["zones"] == ["Kitchen", "Hall", "12"]
        assert record["evacuations"] == 1

    @pytest.mark.asyncio
    async def test_only_the_cleaned_rooms_are_named(self):
        """4.2.21: `rooms_cleaned` decides, as for the room history. A
        region with time but no cleaning is not one of the mission's
        rooms."""
        rec = {**_prime_record("M3"), "rooms_cleaned": ["9", "7"]}
        entry = _prime_entry([rec])
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format="records", days="90"), ENTRY_ID
        )
        record = next(r for r in _body(resp) if r["id"] == "p_M3")
        assert record["zones"] == ["Hall", "Kitchen"]

    @pytest.mark.asyncio
    async def test_export_carries_them_too(self, entry):
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format="export"), ENTRY_ID
        )
        assert _body(resp)["record_count"] == 2

    @pytest.mark.asyncio
    async def test_hazards_are_empty_rather_than_an_error(self, entry):
        """No grid store on Prime: there are no stuck pins to give."""
        resp = await api_views.MissionHistoryView().get(
            _req(_hass(entry), format="hazards"), ENTRY_ID
        )
        assert _body(resp) == []

    @pytest.mark.asyncio
    async def test_digest(self, entry):
        resp = await api_views.DailyDigestView().get(
            _req(_hass(entry), date="2026-10-02"), ENTRY_ID
        )
        assert resp.status == 200
        assert _body(resp)["missions"] == 1

    @pytest.mark.asyncio
    async def test_household_lists_the_prime_robot(self, entry):
        resp = await api_views.HouseholdSummaryView().get(
            _req(_hass(entry), days="90")
        )
        assert resp.status == 200
        robots = _body(resp)["robots"]
        assert [r["name"] for r in robots] == ["Combo 505"]
        assert robots[0]["missions"] == 2

    @pytest.mark.asyncio
    async def test_explain_latest(self, entry):
        resp = await api_views.ExplainMissionView().get(
            _req(_hass(entry)), ENTRY_ID, "latest"
        )
        assert resp.status in (200, 404), _body(resp)

    @pytest.mark.asyncio
    async def test_mission_path_has_no_archive(self, entry):
        resp = await api_views.MissionPathView().get(
            _req(_hass(entry)), ENTRY_ID, "12"
        )
        assert resp.status == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("view", ["MissionMapJsonView", "MissionMapPngView"])
    async def test_mission_map_says_it_has_none(self, entry, view):
        resp = await getattr(api_views, view)().get(
            _req(_hass(entry)), ENTRY_ID, "p_M1"
        )
        assert 400 <= resp.status < 500, _body(resp)

    @pytest.mark.asyncio
    async def test_import(self, entry):
        request = _req(_hass(entry))
        request.json = AsyncMock(return_value={
            "export_version": 1, "records": [_prime_record("M3", "2026-10-01")],
        })
        resp = await api_views.MissionHistoryImportView().post(request, ENTRY_ID)
        assert _body(resp)["imported"] == 1
        entry.runtime_data.mission_store.async_save.assert_awaited_once()


class TestPrimeSetupRegistersTheViews:
    """The registration itself, through the real helper."""

    def test_the_helper_registers_every_view_once(self):
        from custom_components.roomba_plus import _async_register_views

        hass = MagicMock()
        hass.data = {}
        _async_register_views(hass)
        _async_register_views(hass)
        registered = {
            type(c.args[0]).__name__ for c in hass.http.register_view.call_args_list
        }
        assert hass.http.register_view.call_count == len(registered) == 9
        assert {"MissionHistoryView", "HouseholdSummaryView", "DailyDigestView"} <= registered


class TestHouseholdMaintenanceOnPrime:
    """`maintenance_due` in the household API read Classic's local hour
    counters, which a Prime robot never reports: a Prime robot was never
    due there. It now asks the maintenance list's own rule."""

    @staticmethod
    def _with_parts(entry, parts, dock_cap=None):
        from types import SimpleNamespace

        entry.runtime_data.prime_parts_coordinator = SimpleNamespace(data={
            pid: SimpleNamespace(counter_category=cat, count_remaining=left)
            for pid, (cat, left) in parts.items()
        })
        if dock_cap is not None:
            entry.runtime_data.prime_status_coordinator = SimpleNamespace(
                data={"ro-currentstate": {"dock": {"cap": dock_cap}}}
            )
        return entry

    async def _robot(self, entry):
        resp = await api_views.HouseholdSummaryView().get(_req(_hass(entry), days="90"))
        return _body(resp)["robots"][0], _body(resp)["fleet_health"]

    @pytest.mark.asyncio
    async def test_a_used_up_part_is_due(self, entry):
        self._with_parts(entry, {"147": ("replacement", 0), "3": ("replacement", 40)})
        robot, fleet = await self._robot(entry)
        assert robot["maintenance_due"] is True
        assert robot["needs_attention"] is True
        assert fleet["robots_needing_attention"] == ["Combo 505"]

    @pytest.mark.asyncio
    async def test_a_maintenance_counter_at_zero_is_just_done(self, entry):
        """The list's rule: on a `maintenance` part zero means freshly
        done (@DaRealGuGu's pad wash)."""
        self._with_parts(entry, {"202": ("maintenance", 0), "3": ("replacement", 40)})
        robot, _ = await self._robot(entry)
        assert robot["maintenance_due"] is False

    @pytest.mark.asyncio
    async def test_a_part_the_dock_does_not_have_is_not_due(self, entry):
        self._with_parts(entry, {"147": ("replacement", 0)}, dock_cap={"evac": 0})
        robot, _ = await self._robot(entry)
        assert robot["maintenance_due"] is False

    @pytest.mark.asyncio
    async def test_no_parts_read_yet_is_not_due(self, entry):
        robot, _ = await self._robot(entry)
        assert robot["maintenance_due"] is False


class TestPrimePartsDue:
    def test_the_list_and_the_api_share_one_rule(self):
        from custom_components.roomba_plus import prime_parts, todo_prime

        assert todo_prime._needs_attention is prime_parts.needs_attention
        assert (
            todo_prime._parts_the_robot_cannot_have
            is prime_parts.parts_the_robot_cannot_have
        )
