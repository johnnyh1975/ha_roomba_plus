"""Tests for custom_components.roomba_plus.models.

NEW (V4/Prime prep). Previously models.py had no dedicated test file --
MapCapability was only ever exercised inline within other test files.
ConnectionType gets its own small file since it's genuinely new, not
yet referenced anywhere else to piggyback tests onto."""

from __future__ import annotations

from custom_components.roomba_plus.models import ConnectionType, MapCapability


class TestConnectionType:
    """ConnectionType — NEW (V4/Prime prep, July 2026). Deliberately not
    yet referenced by RoombaData or any runtime code -- see the enum's
    own docstring for why that's a separate, later step."""

    def test_local_push_value(self):
        assert ConnectionType.LOCAL_PUSH.value == "local_push"

    def test_cloud_only_value(self):
        assert ConnectionType.CLOUD_ONLY.value == "cloud_only"

    def test_exactly_two_members(self):
        """Guard against an accidental third value being added without
        updating the places that will eventually branch on this enum."""
        assert set(ConnectionType) == {ConnectionType.LOCAL_PUSH, ConnectionType.CLOUD_ONLY}

    def test_is_independent_of_map_capability(self):
        """Orthogonal dimensions, deliberately -- a value here says
        nothing about map/room richness, and vice versa."""
        assert not issubclass(ConnectionType, MapCapability)
        assert not issubclass(MapCapability, ConnectionType)
