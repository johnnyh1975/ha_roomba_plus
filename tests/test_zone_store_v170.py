"""Unit tests for ZoneStore v1.7.0 additions.

Covers:
  - Zone.hidden field (default False)
  - hide_zone / unhide_zone methods
  - hidden_zones property
  - unconfirmed_zones excludes hidden zones
  - Serialisation backward compatibility (d.get("hidden", False))
"""
import pytest
import tests.conftest  # noqa: F401

from custom_components.roomba_plus.zone_store import Zone, ZoneStore


def _make_zone(id: int, name: str, confirmed: bool = True, hidden: bool = False) -> Zone:
    """Factory for minimal Zone instances."""
    return Zone(id=id, name=name, confirmed=confirmed, hidden=hidden)


class TestZoneHiddenField:
    def test_hidden_defaults_to_false(self):
        z = Zone(id=1, name="Kitchen")
        assert z.hidden is False

    def test_hidden_can_be_set_true(self):
        z = Zone(id=1, name="Kitchen", hidden=True)
        assert z.hidden is True


class TestHideUnhideMethods:
    def _store_with_zones(self) -> ZoneStore:
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen", confirmed=True),
            _make_zone(2, "Living room", confirmed=True),
            _make_zone(3, "Bedroom", confirmed=False),
        ]
        return store

    def test_hide_zone_sets_hidden_true(self):
        store = self._store_with_zones()
        result = store.hide_zone(1)
        assert result is True
        assert store.zones[0].hidden is True

    def test_hide_zone_unknown_id_returns_false(self):
        store = self._store_with_zones()
        assert store.hide_zone(999) is False

    def test_unhide_zone_sets_hidden_false(self):
        store = self._store_with_zones()
        store.hide_zone(2)
        result = store.unhide_zone(2)
        assert result is True
        assert store.zones[1].hidden is False

    def test_unhide_zone_unknown_id_returns_false(self):
        store = self._store_with_zones()
        assert store.unhide_zone(999) is False

    def test_hide_is_idempotent(self):
        store = self._store_with_zones()
        store.hide_zone(1)
        store.hide_zone(1)
        assert store.zones[0].hidden is True

    def test_unhide_already_visible_zone(self):
        store = self._store_with_zones()
        # Zone is already visible — unhide should succeed without error
        result = store.unhide_zone(1)
        assert result is True
        assert store.zones[0].hidden is False


class TestHiddenZonesProperty:
    def test_hidden_zones_empty_by_default(self):
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen"),
            _make_zone(2, "Living room"),
        ]
        assert store.hidden_zones == []

    def test_hidden_zones_returns_only_hidden(self):
        store = ZoneStore()
        z1 = _make_zone(1, "Kitchen", hidden=True)
        z2 = _make_zone(2, "Living room", hidden=False)
        z3 = _make_zone(3, "Bedroom", hidden=True)
        store.zones = [z1, z2, z3]
        hidden = store.hidden_zones
        assert len(hidden) == 2
        hidden_names = {z.name for z in hidden}
        assert "Kitchen" in hidden_names
        assert "Bedroom" in hidden_names
        assert "Living room" not in hidden_names


class TestUnconfirmedZonesExcludesHidden:
    """unconfirmed_zones must not include hidden zones (no repair issue for hidden)."""

    def test_unconfirmed_visible_zone_included(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=False)]
        assert len(store.unconfirmed_zones) == 1

    def test_unconfirmed_hidden_zone_excluded(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=True)]
        assert store.unconfirmed_zones == []

    def test_confirmed_zone_not_in_unconfirmed(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Kitchen", confirmed=True, hidden=False)]
        assert store.unconfirmed_zones == []

    def test_mixed_zones(self):
        store = ZoneStore()
        store.zones = [
            _make_zone(1, "Kitchen", confirmed=True, hidden=False),
            _make_zone(2, "Raum 2", confirmed=False, hidden=False),   # should appear
            _make_zone(3, "Raum 3", confirmed=False, hidden=True),    # hidden: excluded
            _make_zone(4, "Living room", confirmed=True, hidden=True),
        ]
        unconfirmed = store.unconfirmed_zones
        assert len(unconfirmed) == 1
        assert unconfirmed[0].name == "Raum 2"

    def test_has_unconfirmed_zones_respects_hidden(self):
        store = ZoneStore()
        store.zones = [_make_zone(1, "Raum 1", confirmed=False, hidden=True)]
        assert store.has_unconfirmed_zones is False


class TestZoneSerialisationWithHidden:
    """Serialisation round-trip must preserve hidden and be backward-compatible."""

    def test_zone_to_dict_includes_hidden(self):
        store = ZoneStore()
        z = _make_zone(1, "Kitchen", hidden=True)
        d = store._zone_to_dict(z)
        assert "hidden" in d
        assert d["hidden"] is True

    def test_zone_from_dict_reads_hidden(self):
        d = {
            "id": 1, "name": "Kitchen",
            "confirmed": True, "hidden": True,
            "x_min": 0.0, "x_max": 100.0,
            "y_min": 0.0, "y_max": 100.0,
            "confidence": 0.8,
            "observations": [],
        }
        z = ZoneStore._zone_from_dict(d)
        assert z.hidden is True

    def test_zone_from_dict_backward_compat_no_hidden_key(self):
        """Old data without 'hidden' key must default to False."""
        d = {
            "id": 1, "name": "Kitchen",
            "confirmed": True,
            # No "hidden" key — pre-v1.7 data
            "x_min": 0.0, "x_max": 100.0,
            "y_min": 0.0, "y_max": 100.0,
            "confidence": 0.8,
            "observations": [],
        }
        z = ZoneStore._zone_from_dict(d)
        assert z.hidden is False

    def test_round_trip_hidden_false(self):
        store = ZoneStore()
        z = _make_zone(2, "Living room", confirmed=True, hidden=False)
        d = store._zone_to_dict(z)
        z2 = ZoneStore._zone_from_dict(d)
        assert z2.hidden is False
        assert z2.name == "Living room"

    def test_round_trip_hidden_true(self):
        store = ZoneStore()
        z = _make_zone(3, "Bedroom", confirmed=True, hidden=True)
        d = store._zone_to_dict(z)
        z2 = ZoneStore._zone_from_dict(d)
        assert z2.hidden is True
