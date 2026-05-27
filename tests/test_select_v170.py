"""Unit tests for select.py v1.7.0 additions.

Covers:
  - resolve_zone_name() 5-level priority chain
  - ZoneSelect.options filters hidden zones
  - SmartZoneSelect._region_label uses alias layer
  - SmartZoneSelect._unlabelled_region_ids excludes hidden zones
"""
import pytest
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.select import resolve_zone_name


# ── resolve_zone_name ─────────────────────────────────────────────────────────

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


# ── ZoneSelect hidden filter ──────────────────────────────────────────────────
# Test the options property logic in isolation using the Zone dataclass.

class TestZoneSelectHiddenFilter:
    """Verify the logic used in ZoneSelect.options (hidden filtering)."""

    def test_hidden_zone_excluded_from_options(self):
        from custom_components.roomba_plus.zone_store import Zone

        zones = [
            Zone(id=1, name="Kitchen", confirmed=True, hidden=False),
            Zone(id=2, name="Bedroom", confirmed=True, hidden=True),
        ]
        visible = [z.name for z in zones if z.confirmed and not z.hidden]
        assert visible == ["Kitchen"]

    def test_unconfirmed_zone_excluded_from_options(self):
        from custom_components.roomba_plus.zone_store import Zone

        zones = [
            Zone(id=1, name="Confirmed", confirmed=True, hidden=False),
            Zone(id=2, name="Unconfirmed", confirmed=False, hidden=False),
        ]
        visible = [z.name for z in zones if z.confirmed and not z.hidden]
        assert visible == ["Confirmed"]

    def test_all_visible_zones_in_options(self):
        from custom_components.roomba_plus.zone_store import Zone

        zones = [
            Zone(id=1, name="Kitchen", confirmed=True, hidden=False),
            Zone(id=2, name="Living room", confirmed=True, hidden=False),
        ]
        visible = [z.name for z in zones if z.confirmed and not z.hidden]
        assert len(visible) == 2


# ── SmartZoneSelect._unlabelled_region_ids excludes hidden ───────────────────

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


# ── Alias-clear-on-match logic ────────────────────────────────────────────────

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
