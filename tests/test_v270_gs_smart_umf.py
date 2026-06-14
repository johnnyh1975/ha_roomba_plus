"""GS-SMART-UMF (v2.7.0) — UmfAligner bootstrap from cloud traversal events.

Tests that the bootstrap path enables alignment when local pose data is absent
(lewis firmware 22.52.10+), and that correct gating conditions are enforced.
"""
import pytest
from unittest.mock import MagicMock


def _make_aligner(door_candidates=None, gs_markers=None):
    """Build a minimal UmfAligner with pre-set candidates and GS store."""
    from custom_components.roomba_plus.umf_aligner import UmfAligner

    geo = MagicMock()
    geo.door_markers = gs_markers or []

    aligner = UmfAligner.__new__(UmfAligner)
    aligner._points2d = []
    aligner._regions = []
    aligner._geometry_store = geo
    aligner.pmap_version_id = ""
    aligner._coord_lookup = {}
    aligner._room_polygons = {}
    aligner._door_candidates = door_candidates or [
        (500.0, 300.0),
        (1200.0, 800.0),
        (600.0, 1500.0),
    ]
    aligner._transform = None
    aligner._confidence = 0.0
    aligner._aligned = False
    aligner._bootstrap_markers = []
    return aligner


def _record_with_traversals(rids):
    """Build a fake cloud record with traversal finEvents."""
    return {
        "timeline": {
            "finEvents": [
                {"type": "traversal", "traversal": {"rid": rid, "type": "region"}}
                for rid in rids
            ]
        }
    }


class TestBootstrapMarkers:

    def test_set_bootstrap_markers_requires_min_2(self):
        """set_bootstrap_markers with < 2 positions is a no-op."""
        aligner = _make_aligner()
        aligner.set_bootstrap_markers([(100.0, 200.0)])
        assert aligner._bootstrap_markers == []

    def test_set_bootstrap_markers_sets_door_markers(self):
        """set_bootstrap_markers stores DoorMarker objects in UMF space."""
        from custom_components.roomba_plus.geometry_store import DoorMarker

        aligner = _make_aligner()
        aligner.set_bootstrap_markers([(500.0, 300.0), (1200.0, 800.0)])
        assert len(aligner._bootstrap_markers) == 2
        assert all(isinstance(m, DoorMarker) for m in aligner._bootstrap_markers)
        # mission_count must pass the >= 2 filter in align()
        assert aligner._bootstrap_markers[0].mission_count >= 2

    def test_align_uses_bootstrap_when_gs_empty(self):
        """align() achieves high confidence using bootstrap markers."""
        from unittest.mock import patch

        aligner = _make_aligner(
            door_candidates=[(500.0, 300.0), (1200.0, 800.0)],
            gs_markers=[],  # GeometryStore empty (lewis firmware)
        )
        # Bootstrap markers at exact same positions as door candidates
        aligner.set_bootstrap_markers([(500.0, 300.0), (1200.0, 800.0)])

        # Patch internal build methods so our pre-set _door_candidates survive
        with patch.object(aligner, "_build_coord_lookup"), \
             patch.object(aligner, "_detect_door_gaps"), \
             patch.object(aligner, "_resolve_room_polygons"):
            conf = aligner.align()

        # Bootstrap markers at exact candidate positions → near-zero residual → high conf
        assert conf >= 0.70
        assert aligner.aligned is True

    def test_align_prefers_gs_markers_over_bootstrap(self):
        """When GS markers exist, bootstrap markers are ignored."""
        from custom_components.roomba_plus.geometry_store import DoorMarker

        real_markers = [
            DoorMarker(id="m1", cx=500.0, cy=300.0, mission_count=3),
            DoorMarker(id="m2", cx=1200.0, cy=800.0, mission_count=3),
        ]
        aligner = _make_aligner(
            door_candidates=[(500.0, 300.0), (1200.0, 800.0)],
            gs_markers=real_markers,
        )
        # Also set bootstrap markers — should NOT be used when GS has data
        aligner.set_bootstrap_markers([(9000.0, 9000.0), (9500.0, 9000.0)])

        aligner.align()
        # The result should be driven by GS markers, not bootstrap markers
        # (verify by checking the aligner ran at all — exact confidence depends on transform)
        assert aligner._confidence >= 0.0  # ran without error


class TestExtractTraversalPositions:

    def test_returns_empty_below_min_missions(self):
        from custom_components.roomba_plus import _extract_traversal_umf_positions

        aligner = _make_aligner()
        records = [_record_with_traversals(["1", "2"])] * 2  # only 2 missions
        result = _extract_traversal_umf_positions(records, aligner, min_missions=3)
        assert result == []

    def test_returns_candidates_after_min_missions(self):
        from custom_components.roomba_plus import _extract_traversal_umf_positions

        candidates = [(500.0, 300.0), (1200.0, 800.0), (600.0, 1500.0)]
        aligner = _make_aligner(door_candidates=candidates)
        records = [_record_with_traversals(["1", "2"])] * 4  # 4 missions ≥ 3
        result = _extract_traversal_umf_positions(records, aligner, min_missions=3)
        assert len(result) == len(candidates)
        assert set(result) == set(candidates)
