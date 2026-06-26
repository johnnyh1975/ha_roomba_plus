"""Consolidated domain test file (TEST-REORG).

Merged by the v2.8.x test reorganisation from multiple version-named
test files; see git history for provenance.
"""


from __future__ import annotations



import pytest
import tests.conftest
from custom_components.roomba_plus.select import resolve_zone_name
import sys
from unittest.mock import MagicMock
import homeassistant.helpers.entity_platform as _ep
import math
from unittest.mock import AsyncMock
from unittest.mock import patch
from custom_components.roomba_plus.umf_aligner import UmfAligner
import asyncio


def _mission_sensor(cycle="none", phase=""):
    """Build a minimal RoombaMissionActive with stubbed vacuum state."""
    from custom_components.roomba_plus.binary_sensor import RoombaMissionActive
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": {
        "cleanMissionStatus": {"cycle": cycle, "phase": phase}
    }}}
    s = RoombaMissionActive.__new__(RoombaMissionActive)
    s.vacuum = roomba
    return s


def _boost_entity(carpet_boost=None, vac_high=None):
    """Build a minimal CarpetBoostSelect with stubbed vacuum state."""
    from custom_components.roomba_plus.select import CarpetBoostSelect
    state = {}
    if carpet_boost is not None:
        state["carpetBoost"] = carpet_boost
    if vac_high is not None:
        state["vacHigh"] = vac_high
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": state}}
    s = CarpetBoostSelect.__new__(CarpetBoostSelect)
    s.vacuum = roomba
    # vacuum_state is a property reading from self.vacuum — pre-compute it
    s.vacuum_state = state
    s._blid = "test_blid"
    return s


def _make_aligner(aligned: bool = True, confidence: float = 0.85) -> UmfAligner:
    """Return a minimal UmfAligner with controlled aligned/confidence state."""
    a = UmfAligner([], [], MagicMock())
    a._aligned    = aligned
    a._confidence = confidence
    a._transform  = (0.0, 0.0, 0.0)
    a.pmap_version_id = "v1"
    return a


def _make_runtime_data(
    *,
    aligner: UmfAligner | None = None,
    has_cloud: bool = True,
    regions: list | None = None,
    keepout_zones: list | None = None,
    mission_store=None,
    grid_store=None,
    map_capability=None,
    geometry_store=None,
):
    data = MagicMock()
    data.umf_aligner    = aligner
    data.has_cloud      = has_cloud
    data.mission_store  = mission_store
    data.grid_store     = grid_store
    data.geometry_store = geometry_store

    cc = MagicMock()
    cc.regions      = regions or []
    cc.keepout_zones = keepout_zones or []
    cc.observed_zone_centroids = []
    cc.last_update_success = True
    data.cloud_coordinator = cc if has_cloud else None

    if map_capability is not None:
        data.map_capability = map_capability

    return data


class TestResolveZoneName:
    def test_alias_wins_over_all(self):
        assert resolve_zone_name(
            "5",
            aliases={"5": "My Alias"},
            cloud_name="Cloud Name",
            local_name="Local Name",
            labels={"5": "Old Label"},
        ) == "My Alias"

    def test_cloud_name_wins_without_alias(self):
        assert resolve_zone_name(
            "5",
            aliases={},
            cloud_name="Cloud Name",
            local_name="Local Name",
            labels={"5": "Old Label"},
        ) == "Cloud Name"

    def test_local_name_wins_without_cloud(self):
        assert resolve_zone_name(
            "5",
            aliases={},
            cloud_name=None,
            local_name="Local Name",
            labels={"5": "Old Label"},
        ) == "Local Name"

    def test_labels_fallback(self):
        assert resolve_zone_name(
            "5",
            aliases={},
            cloud_name=None,
            local_name=None,
            labels={"5": "Old Label"},
        ) == "Old Label"

    def test_auto_generated_fallback(self):
        assert resolve_zone_name(
            "42",
            aliases={},
            cloud_name=None,
            local_name=None,
            labels={},
        ) == "Zone 42"

    def test_empty_alias_string_falls_through(self):
        """Empty string alias must not shadow a valid cloud name."""
        assert resolve_zone_name(
            "5",
            aliases={"5": ""},  # empty = falsy
            cloud_name="Cloud Name",
            local_name=None,
            labels={},
        ) == "Cloud Name"

    def test_none_cloud_name_falls_through(self):
        assert resolve_zone_name(
            "3",
            aliases={},
            cloud_name=None,
            local_name="Kitchen",
            labels={},
        ) == "Kitchen"

    def test_different_region_id_not_matched(self):
        """Aliases and labels must be keyed on the correct region_id."""
        assert resolve_zone_name(
            "7",
            aliases={"5": "Wrong Zone"},
            cloud_name=None,
            local_name=None,
            labels={"5": "Also Wrong"},
        ) == "Zone 7"


class TestZoneSelectHiddenFilter:
    """Verify the logic used in ZoneSelect.options (hidden filtering).

    ROOM-SEG Stage 3 — ZoneSelect now reads from RoomSegStore/SegRoom, not
    ZoneStore/Zone (the gap heuristic proved unreliable — see
    ROOM_SEGMENTATION_NOTES.md). unique_id/entity_id are unchanged.
    """

    def test_hidden_room_excluded_from_options(self):
        from custom_components.roomba_plus.room_seg_store import SegRoom

        rooms = [
            SegRoom(id="room_1", name="Kitchen", confirmed=True, hidden=False),
            SegRoom(id="room_2", name="Bedroom", confirmed=True, hidden=True),
        ]
        visible = [r.name for r in rooms if r.confirmed and not r.hidden]
        assert visible == ["Kitchen"]

    def test_unconfirmed_room_excluded_from_options(self):
        from custom_components.roomba_plus.room_seg_store import SegRoom

        rooms = [
            SegRoom(id="room_1", name="Confirmed", confirmed=True, hidden=False),
            SegRoom(id="room_2", name="Unconfirmed", confirmed=False, hidden=False),
        ]
        visible = [r.name for r in rooms if r.confirmed and not r.hidden]
        assert visible == ["Confirmed"]

    def test_all_visible_rooms_in_options(self):
        from custom_components.roomba_plus.room_seg_store import SegRoom

        rooms = [
            SegRoom(id="room_1", name="Kitchen", confirmed=True, hidden=False),
            SegRoom(id="room_2", name="Living room", confirmed=True, hidden=False),
        ]
        visible = [r.name for r in rooms if r.confirmed and not r.hidden]
        assert len(visible) == 2

    def test_real_entity_options_reads_room_seg_store(self):
        """Exercises the actual ZoneSelect.options property, not just a
        mirrored expression -- catches a wrong attribute name or wrong
        store reference that the logic-mirror tests above cannot."""
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.select import ZoneSelect
        from custom_components.roomba_plus.room_seg_store import RoomSegStore, SegRoom

        rss = RoomSegStore()
        rss.rooms = {
            "room_1": SegRoom(id="room_1", name="Kitchen", confirmed=True, hidden=False),
            "room_2": SegRoom(id="room_2", name="Bedroom", confirmed=True, hidden=True),
            "room_3": SegRoom(id="room_3", name="", confirmed=False, hidden=False),
        }
        config_entry = MagicMock()
        config_entry.runtime_data.room_seg_store = rss

        entity = ZoneSelect.__new__(ZoneSelect)
        entity._config_entry = config_entry

        assert entity.options == ["Kitchen"]

    def test_real_entity_options_empty_without_room_seg_store(self):
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.select import ZoneSelect

        config_entry = MagicMock()
        config_entry.runtime_data.room_seg_store = None

        entity = ZoneSelect.__new__(ZoneSelect)
        entity._config_entry = config_entry

        assert entity.options == []


class TestUnlabelledRegionIdsHiddenExclusion:
    """Verify hidden IDs are excluded from unlabelled list (repair issue gate)."""

    def _unlabelled(self, all_ids, named_ids, hidden_ids):
        """Mirror the _unlabelled_region_ids logic."""
        return [rid for rid in all_ids if rid not in named_ids and rid not in hidden_ids]

    def test_hidden_id_not_in_unlabelled(self):
        result = self._unlabelled(
            all_ids=["1", "2", "3"],
            named_ids={"1"},
            hidden_ids=["2"],
        )
        assert "2" not in result
        assert "3" in result

    def test_unlabelled_not_hidden_included(self):
        result = self._unlabelled(
            all_ids=["1", "2"],
            named_ids=set(),
            hidden_ids=[],
        )
        assert result == ["1", "2"]

    def test_all_hidden_returns_empty(self):
        result = self._unlabelled(
            all_ids=["1", "2"],
            named_ids=set(),
            hidden_ids=["1", "2"],
        )
        assert result == []


class TestAliasClearOnMatch:
    """Verify alias-clear-on-match logic from _save_zone_edits_atomic."""

    def _apply_alias_logic(self, region_id, display_name, cloud_name, existing_aliases):
        """Mirror the alias update logic from _save_zone_edits_atomic."""
        aliases = dict(existing_aliases)
        if display_name and display_name != cloud_name:
            aliases[region_id] = display_name
        elif region_id in aliases:
            del aliases[region_id]
        return aliases

    def test_alias_set_when_name_differs_from_cloud(self):
        aliases = self._apply_alias_logic("5", "Küche", "Kitchen", {})
        assert aliases["5"] == "Küche"

    def test_alias_cleared_when_name_matches_cloud(self):
        """Saving a name that equals the cloud name must delete the alias."""
        aliases = self._apply_alias_logic("5", "Kitchen", "Kitchen", {"5": "Old Alias"})
        assert "5" not in aliases

    def test_no_alias_created_when_name_matches_cloud(self):
        aliases = self._apply_alias_logic("5", "Kitchen", "Kitchen", {})
        assert "5" not in aliases

    def test_empty_display_name_clears_existing_alias(self):
        """Empty display name (falsy) clears the alias if one exists."""
        aliases = self._apply_alias_logic("5", "", "Kitchen", {"5": "Old Alias"})
        assert "5" not in aliases

    def test_different_region_ids_independent(self):
        """Alias logic for one region_id must not affect others."""
        initial = {"5": "Alias5", "6": "Alias6"}
        aliases = self._apply_alias_logic("5", "Kitchen", "Kitchen", initial)
        assert "5" not in aliases
        assert aliases.get("6") == "Alias6"


class TestCarpetBoostSelect:
    """Card fix P2 — select.*_carpet_boost_select."""

    def test_current_option_automatic(self):
        assert _boost_entity(carpet_boost=True, vac_high=False).current_option == "Automatic"

    def test_current_option_performance(self):
        assert _boost_entity(carpet_boost=False, vac_high=True).current_option == "Performance"

    def test_current_option_eco(self):
        assert _boost_entity(carpet_boost=False, vac_high=False).current_option == "Eco"

    def test_current_option_none_when_state_absent(self):
        assert _boost_entity().current_option is None

    def test_options_list_contains_all_three(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        from custom_components.roomba_plus.const import FAN_SPEEDS
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_options = FAN_SPEEDS
        assert "Automatic" in s._attr_options
        assert "Eco" in s._attr_options
        assert "Performance" in s._attr_options

    def test_state_filter_carpet_boost(self):
        s = _boost_entity()
        assert s.new_state_filter({"carpetBoost": True}) is True

    def test_state_filter_vac_high(self):
        s = _boost_entity()
        assert s.new_state_filter({"vacHigh": False}) is True

    def test_state_filter_rejects_unrelated(self):
        s = _boost_entity()
        assert s.new_state_filter({"cleanMissionStatus": {}}) is False

    def test_translation_key(self):
        e = _boost_entity()
        tk = (type(e).__dict__.get("_attr_translation_key") or
              getattr(getattr(e, "entity_description", None), "translation_key", None))
        if isinstance(tk, property):
            tk = tk.fget(_boost_entity())
        assert tk == "carpet_boost_select"

    def test_unique_id_suffix(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_unique_id = "test_blid_carpet_boost_select"
        assert s._attr_unique_id.endswith("_carpet_boost_select")


class TestSelectKeeputAttrs:
    def _entity_with_keepout(self, keepout_zones):
        from custom_components.roomba_plus.select import CloudSmartZoneSelect
        entity = object.__new__(CloudSmartZoneSelect)
        entity._regions = [{"id": "r1", "name": "Kitchen",
                             "region_type": "default", "pmap_id": "p1"}]
        entity._zones         = []
        entity._map_name      = "Home"
        entity._pmap_id       = "p1"
        entity._is_active_map = True
        entity._selected      = "Kitchen"

        cc = MagicMock()
        cc.keepout_zones = keepout_zones
        cc.data = {"pmaps": []}
        entry = MagicMock()
        entry.runtime_data.cloud_coordinator = cc
        entity._config_entry = entry
        return entity

    def test_no_keepout_zones_count_zero(self):
        entity = self._entity_with_keepout([])
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 0
        assert "keepout_zone_names" not in attrs

    def test_keepout_zones_with_names(self):
        zones = [{"name": "Sofa Area"}, {"name": "Dog Bed"}]
        entity = self._entity_with_keepout(zones)
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 2
        assert attrs.get("keepout_zone_names") == ["Sofa Area", "Dog Bed"]

    def test_keepout_zones_without_names(self):
        zones = [{"cx": 100, "cy": 200}, {"cx": 300, "cy": 400}]
        entity = self._entity_with_keepout(zones)
        attrs = entity.extra_state_attributes
        assert attrs.get("keepout_zone_count") == 2
        assert "keepout_zone_names" not in attrs


class TestR2SmartZonesEntryId:
    """R2: Issue ID encodes entry_id for correct fix flow targeting."""

    def test_issue_id_contains_entry_id(self):
        entry_id = "ABCD1234"
        issue_id = f"smart_zones_need_naming_{entry_id}"
        assert issue_id.startswith("smart_zones_need_naming_")
        extracted = issue_id[len("smart_zones_need_naming_"):]
        assert extracted == entry_id

    def test_fix_flow_resolves_correct_entry(self):
        """get_fix_flow parses entry_id from prefixed issue_id."""
        _PREFIX = "smart_zones_need_naming_"
        issue_id = "smart_zones_need_naming_MYENTRYID"
        assert issue_id.startswith(_PREFIX)
        entry_id = issue_id[len(_PREFIX):]
        assert entry_id == "MYENTRYID"
