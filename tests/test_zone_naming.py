"""v3.4.0 TODO — tests for zone_naming.py's collect_region_ids()/
unlabelled_zone_ids(), extracted from select.py's SmartZoneSelect.

The behaviour itself (via SmartZoneSelect's public interface) is
already covered in test_select.py; this file targets the extracted
pure functions directly since todo.py will call them without going
through any entity at all.
"""
from __future__ import annotations

from custom_components.roomba_plus.zone_naming import (
    collect_region_ids,
    unlabelled_zone_ids,
)


class TestCollectRegionIds:
    def test_from_clean_schedule2(self):
        state = {
            "cleanSchedule2": [
                {"cmd": {"regions": [{"rid": "7", "type": "rid"}]}},
            ],
        }
        assert collect_region_ids(state, {}) == ["7"]

    def test_from_last_command(self):
        state = {"lastCommand": {"regions": [{"region_id": "9", "type": "rid"}]}}
        assert collect_region_ids(state, {}) == ["9"]

    def test_from_persisted_discovered_zone_ids(self):
        options = {"discovered_zone_ids": ["3"]}
        assert collect_region_ids({}, options) == ["3"]

    def test_merges_and_dedupes_all_three_sources(self):
        state = {
            "cleanSchedule2": [{"cmd": {"regions": [{"rid": "7"}]}}],
            "lastCommand": {"regions": [{"rid": "7"}, {"rid": "9"}]},
        }
        options = {"discovered_zone_ids": ["9", "12"]}
        assert collect_region_ids(state, options) == ["7", "9", "12"]

    def test_plain_string_region_format(self):
        """Some firmware sends regions as plain strings, not dicts —
        extract_region_id() must handle both (const.py's own contract)."""
        state = {"lastCommand": {"regions": ["19", "21"]}}
        assert collect_region_ids(state, {}) == ["19", "21"]

    def test_empty_everything_returns_empty(self):
        assert collect_region_ids({}, {}) == []

    def test_sort_order_is_zero_padded_numeric(self):
        """zfill(4) sort key: '9' before '12' as numbers, not '12' before
        '9' as plain strings."""
        options = {"discovered_zone_ids": ["12", "9", "3"]}
        assert collect_region_ids({}, options) == ["3", "9", "12"]


class TestUnlabelledZoneIds:
    def test_all_unlabelled_when_no_names_at_all(self):
        options = {"discovered_zone_ids": ["7", "9"]}
        assert unlabelled_zone_ids({}, options) == ["7", "9"]

    def test_smart_zone_data_excludes_from_unlabelled(self):
        options = {
            "discovered_zone_ids": ["7", "9"],
            "smart_zone_data": {"7": {"name": "Kitchen"}},
        }
        assert unlabelled_zone_ids({}, options) == ["9"]

    def test_legacy_smart_zone_labels_also_excludes(self):
        """Legacy fallback — existing installs with only smart_zone_labels
        (pre-smart_zone_data) must not be re-prompted."""
        options = {
            "discovered_zone_ids": ["7", "9"],
            "smart_zone_labels": {"7": "Kitchen"},
        }
        assert unlabelled_zone_ids({}, options) == ["9"]

    def test_hidden_zones_excluded_from_unlabelled(self):
        """A user who chose to hide a zone should not be prompted to
        name it — matches the smart_zones_need_naming repair issue's
        own exclusion."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN
        options = {
            "discovered_zone_ids": ["7", "9"],
            CONF_SMART_ZONE_HIDDEN: ["9"],
        }
        assert unlabelled_zone_ids({}, options) == ["7"]

    def test_all_labelled_returns_empty(self):
        options = {
            "discovered_zone_ids": ["7", "9"],
            "smart_zone_data": {"7": {"name": "Kitchen"}, "9": {"name": "Hall"}},
        }
        assert unlabelled_zone_ids({}, options) == []

    def test_no_zones_at_all_returns_empty(self):
        assert unlabelled_zone_ids({}, {}) == []


class TestMalformedInputResilience:
    """v3.4.0 bug hunt — every field here is untrusted MQTT/options
    data. Two distinct failure classes found: malformed shapes
    (entries that aren't dicts, non-list "regions") and — more subtly
    — options with an EXPLICIT None value under a key, which
    dict.get(key, default) does not guard against (only a missing key
    does). This is the exact dict.get() pitfall already documented as
    a standing lesson elsewhere in this project; it recurred here."""

    def test_clean_schedule2_entries_not_dicts(self):
        state = {"cleanSchedule2": ["garbage", 123, None]}
        assert collect_region_ids(state, {}) == []
        assert unlabelled_zone_ids(state, {}) == []

    def test_cmd_is_not_a_dict(self):
        state = {"cleanSchedule2": [{"cmd": "garbage"}]}
        assert collect_region_ids(state, {}) == []

    def test_cmd_regions_not_a_list(self):
        state = {"cleanSchedule2": [{"cmd": {"regions": "not_a_list"}}]}
        assert collect_region_ids(state, {}) == []

    def test_last_command_is_not_a_dict(self):
        state = {"lastCommand": "garbage"}
        assert collect_region_ids(state, {}) == []

    def test_last_command_regions_is_none(self):
        state = {"lastCommand": {"regions": None}}
        assert collect_region_ids(state, {}) == []

    def test_clean_schedule2_is_explicit_none(self):
        assert collect_region_ids({"cleanSchedule2": None}, {}) == []

    def test_discovered_zone_ids_explicit_none_in_options(self):
        """The key exists with value None — dict.get(key, []) would
        return None here, not the default []."""
        options = {"discovered_zone_ids": None}
        assert collect_region_ids({}, options) == []

    def test_discovered_zone_ids_not_a_list(self):
        options = {"discovered_zone_ids": "not_a_list"}
        assert collect_region_ids({}, options) == []

    def test_smart_zone_data_explicit_none_in_options(self):
        options = {"discovered_zone_ids": ["7"], "smart_zone_data": None}
        assert unlabelled_zone_ids({}, options) == ["7"]

    def test_smart_zone_labels_explicit_none_in_options(self):
        options = {"discovered_zone_ids": ["7"], "smart_zone_labels": None}
        assert unlabelled_zone_ids({}, options) == ["7"]

    def test_hidden_ids_explicit_none_in_options(self):
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN
        options = {"discovered_zone_ids": ["7"], CONF_SMART_ZONE_HIDDEN: None}
        assert unlabelled_zone_ids({}, options) == ["7"]

    def test_all_none_options_at_once(self):
        """The realistic worst case: every optional options key present
        but explicitly None (e.g. after a corrupted/partial write)."""
        from custom_components.roomba_plus.const import CONF_SMART_ZONE_HIDDEN
        options = {
            "discovered_zone_ids": None,
            "smart_zone_data": None,
            "smart_zone_labels": None,
            CONF_SMART_ZONE_HIDDEN: None,
        }
        assert collect_region_ids({}, options) == []
        assert unlabelled_zone_ids({}, options) == []
