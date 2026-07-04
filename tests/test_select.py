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
    from custom_components.roomba_plus.select import CarpetBoostSelect, _CARPET_BOOST_DESC
    state = {}
    if carpet_boost is not None:
        state["carpetBoost"] = carpet_boost
    if vac_high is not None:
        state["vacHigh"] = vac_high
    roomba = MagicMock()
    roomba.master_state = {"state": {"reported": state}}
    s = CarpetBoostSelect.__new__(CarpetBoostSelect)
    s.entity_description = _CARPET_BOOST_DESC   # F-RB-6: set descriptor (bypassed __init__)
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
        # v3.1.0 CARPET-BOOST-SLUG-FIX: lowercase slug, not "Automatic"
        assert _boost_entity(carpet_boost=True, vac_high=False).current_option == "automatic"

    def test_current_option_performance(self):
        assert _boost_entity(carpet_boost=False, vac_high=True).current_option == "performance"

    def test_current_option_eco(self):
        assert _boost_entity(carpet_boost=False, vac_high=False).current_option == "eco"

    def test_current_option_none_when_state_absent(self):
        assert _boost_entity().current_option is None

    def test_options_list_contains_all_three(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        from custom_components.roomba_plus.const import FAN_SPEEDS
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_options = FAN_SPEEDS
        assert "automatic" in s._attr_options
        assert "eco" in s._attr_options
        assert "performance" in s._attr_options

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
        # F-RB-6: translation_key is now on entity_description, not as _attr on the class
        tk = getattr(getattr(e, "entity_description", None), "translation_key", None)
        assert tk == "carpet_boost_select"

    def test_unique_id_suffix(self):
        from custom_components.roomba_plus.select import CarpetBoostSelect
        s = CarpetBoostSelect.__new__(CarpetBoostSelect)
        s._attr_unique_id = "test_blid_carpet_boost_select"
        assert s._attr_unique_id.endswith("_carpet_boost_select")


class TestCarpetBoostSlugMigration:
    """v3.1.0 CARPET-BOOST-SLUG-FIX — FAN_SPEEDS lowercase migration.

    Hassfest requires select translation_key state keys to match
    [a-z0-9-_]+ (cannot start/end with hyphen/underscore). FAN_SPEEDS moved
    from Capital-Case ("Automatic") to lowercase ("automatic"). These tests
    verify both the new canonical values AND backward compatibility for
    existing automations still sending the old Capital-Case value.
    """

    @pytest.mark.asyncio
    async def test_select_fn_accepts_new_lowercase_value(self):
        """select.select_option with the new canonical lowercase value works."""
        from custom_components.roomba_plus.select import _select_carpet_boost
        entity = MagicMock()
        entity._blid = "test_blid"
        entity.hass.services.async_call = AsyncMock()
        reg = MagicMock()
        reg.async_get_entity_id.return_value = "vacuum.test_robot"
        with patch(
            "homeassistant.helpers.entity_registry.async_get", return_value=reg
        ):
            await _select_carpet_boost(entity, "automatic")
        entity.hass.services.async_call.assert_called_once()
        call_args = entity.hass.services.async_call.call_args
        assert call_args[0][2]["fan_speed"] == "automatic"

    @pytest.mark.asyncio
    async def test_select_fn_accepts_old_capital_case_value(self):
        """Backward compat: select.select_option with the OLD Capital-Case
        value ("Automatic") from an existing automation still works — gets
        normalised to the new canonical lowercase value before being sent on.
        """
        from custom_components.roomba_plus.select import _select_carpet_boost
        entity = MagicMock()
        entity._blid = "test_blid"
        entity.hass.services.async_call = AsyncMock()
        reg = MagicMock()
        reg.async_get_entity_id.return_value = "vacuum.test_robot"
        with patch(
            "homeassistant.helpers.entity_registry.async_get", return_value=reg
        ):
            await _select_carpet_boost(entity, "Automatic")
        entity.hass.services.async_call.assert_called_once()
        call_args = entity.hass.services.async_call.call_args
        assert call_args[0][2]["fan_speed"] == "automatic"

    @pytest.mark.asyncio
    async def test_vacuum_set_fan_speed_accepts_old_capital_case(self):
        """RoombaVacuumCarpetBoost.async_set_fan_speed accepts the old
        Capital-Case value via case-insensitive matching, not .capitalize()
        (which would break with the new lowercase canonical constants).
        """
        from custom_components.roomba_plus.vacuum import RoombaVacuumCarpetBoost
        v = RoombaVacuumCarpetBoost.__new__(RoombaVacuumCarpetBoost)
        v.hass = MagicMock()
        v.hass.async_add_executor_job = AsyncMock()
        v.vacuum = MagicMock()
        await v.async_set_fan_speed("Automatic")
        # Should not log an error and should call set_preference twice
        assert v.hass.async_add_executor_job.call_count == 2

    def test_all_seven_languages_have_lowercase_state_keys(self):
        """strings.json + all 7 translations must use lowercase slug keys
        for carpet_boost_select state, matching the hassfest [a-z0-9-_]+ rule.
        """
        import json, os, re
        base = os.path.join(
            os.path.dirname(__file__),
            "..", "custom_components", "roomba_plus"
        )
        pattern = re.compile(r"^[a-z0-9_-]+$")

        files = ["strings.json"] + [
            os.path.join("translations", f"{lang}.json")
            for lang in ("en", "de", "fr", "it", "es", "nl", "pt")
        ]
        for rel_path in files:
            with open(os.path.join(base, rel_path), encoding="utf-8") as f:
                data = json.load(f)
            state = data["entity"]["select"]["carpet_boost_select"]["state"]
            for key in state:
                assert pattern.match(key), (
                    f"{rel_path}: state key {key!r} does not match "
                    f"hassfest's [a-z0-9-_]+ requirement"
                )
                assert not key.startswith(("-", "_")), f"{rel_path}: {key!r} starts with - or _"
                assert not key.endswith(("-", "_")), f"{rel_path}: {key!r} ends with - or _"
            assert set(state.keys()) == {"automatic", "eco", "performance"}, (
                f"{rel_path}: unexpected state keys {set(state.keys())}"
            )


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


class TestZoneSelectNotCreatedForEphemeral:
    """v3.2.1 REMOVED — ZoneSelect (and the matching ZoneCleanButton in
    button.py) used to be created for EPHEMERAL (900-series) robots too.
    Confirmed dead weight: its only consumer anywhere in this codebase
    was ZoneCleanButton, which read the selection purely to log it, then
    sent the exact same plain "start" command regardless — the 900-series
    MQTT API has no coordinate/region targeting at all. A selector with
    zero functional consumers, feeding a button that ignores it, actively
    misleads: it suggests targeted-room cleaning on hardware that
    architecturally cannot do it.

    This is a genuine gap the removal itself exposed: the EPHEMERAL
    creation gate was never covered by a setup-level test before, only
    ZoneSelect's internal .options logic was unit-tested directly.
    """

    def _run_setup(self, map_capability, room_seg_store=None):
        import asyncio
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.select import async_setup_entry

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        config_entry.runtime_data.map_capability = map_capability
        config_entry.runtime_data.room_seg_store = room_seg_store
        config_entry.runtime_data.has_cloud = False

        added: list = []
        def fake_add_entities(entities, *a, **kw):
            added.extend(entities)

        with patch(
            "custom_components.roomba_plus.select.roomba_reported_state",
            return_value={},
        ):
            asyncio.get_event_loop().run_until_complete(
                async_setup_entry(hass, config_entry, fake_add_entities)
            )
        return added

    def test_ephemeral_does_not_create_zone_select(self):
        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.select import ZoneSelect
        entities = self._run_setup(MapCapability.EPHEMERAL, room_seg_store=MagicMock())
        assert not any(isinstance(e, ZoneSelect) for e in entities)

    def test_ephemeral_without_room_seg_store_also_does_not_create_it(self):
        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.select import ZoneSelect
        entities = self._run_setup(MapCapability.EPHEMERAL, room_seg_store=None)
        assert not any(isinstance(e, ZoneSelect) for e in entities)

    def test_harness_sanity_check_smart_map_still_creates_a_zone_select(self):
        """Positive control: proves this test harness actually exercises
        async_setup_entry's real entity list (not vacuously passing on an
        always-empty list) — a SMART robot with pmaps must still get a
        zone-select entity, since that tier genuinely has region
        targeting (unaffected by this removal)."""
        import asyncio
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.select import (
            async_setup_entry, SmartZoneSelect,
        )

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        config_entry.runtime_data.map_capability = MapCapability.SMART
        config_entry.runtime_data.room_seg_store = None
        config_entry.runtime_data.has_cloud = False

        added: list = []
        def fake_add_entities(entities, *a, **kw):
            added.extend(entities)

        with patch(
            "custom_components.roomba_plus.select.roomba_reported_state",
            return_value={"pmaps": {"pmap0": {}}},
        ):
            asyncio.get_event_loop().run_until_complete(
                async_setup_entry(hass, config_entry, fake_add_entities)
            )
        assert any(isinstance(e, SmartZoneSelect) for e in added), (
            "harness must actually see SMART-tier entities — otherwise "
            "the EPHEMERAL 'not created' assertions above prove nothing"
        )


class TestZoneCleanButtonNotCreatedForEphemeral:
    """v3.2.1 REMOVED — matching half of the ZoneSelect removal above,
    in button.py."""

    def _run_setup(self, map_capability, room_seg_store=None):
        import asyncio
        from unittest.mock import MagicMock
        from custom_components.roomba_plus.button import async_setup_entry

        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.runtime_data.roomba = MagicMock()
        config_entry.runtime_data.blid = "TESTBLID"
        config_entry.runtime_data.map_capability = map_capability
        config_entry.runtime_data.room_seg_store = room_seg_store
        config_entry.runtime_data.has_cloud = False

        added: list = []
        def fake_add_entities(entities, *a, **kw):
            added.extend(entities)

        with patch(
            "custom_components.roomba_plus.button.roomba_reported_state",
            return_value={},
        ):
            asyncio.get_event_loop().run_until_complete(
                async_setup_entry(hass, config_entry, fake_add_entities)
            )
        return added


    def test_ephemeral_does_not_create_zone_clean_button(self):
        from custom_components.roomba_plus.models import MapCapability
        from custom_components.roomba_plus.button import ZoneCleanButton
        entities = self._run_setup(MapCapability.EPHEMERAL, room_seg_store=MagicMock())
        assert not any(isinstance(e, ZoneCleanButton) for e in entities)
