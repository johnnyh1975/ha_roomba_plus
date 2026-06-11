"""Tests for v2.5.0 REST API — format=export (F15) and import endpoint (F16).

Covers F15:
  - export shape has all required keys
  - record_count matches records length
  - exported_at is a valid ISO 8601 UTC string
  - blid present from entry.data
  - empty store returns record_count=0 and empty records list
  - "export" present in _VALID_FORMATS

Covers F16:
  - happy path: N imported, 0 skipped, async_save called
  - dedup: existing IDs counted as skipped, not imported
  - missing export_version → 400
  - wrong export_version → 400
  - empty records list → imported=0, skipped=0
  - 404 on unknown entry
  - 503 when runtime_data not yet set
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roomba_plus.api_views import (
    MissionHistoryImportView,
    MissionHistoryView,
    _VALID_FORMATS,
)
from custom_components.roomba_plus.const import DOMAIN


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(id_: str, started_at: str = "2026-05-01T08:00:00+00:00") -> dict:
    return {
        "id": id_,
        "started_at": started_at,
        "ended_at": "2026-05-01T09:00:00+00:00",
        "duration_min": 60,
        "area_sqft": 200.0,
        "result": "completed",
        "initiator": "schedule",
        "zones": [],
        "error_code": None,
        "bbrun_hr": 100,
    }


def _make_mission_store(records: list[dict]) -> MagicMock:
    ms = MagicMock()
    ms._records = list(records)
    ms.async_save = AsyncMock()
    return ms


def _make_request(hass: MagicMock, fmt: str = "export") -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    req.query = {"format": fmt}
    return req


def _make_post_request(hass: MagicMock, body: dict) -> MagicMock:
    req = MagicMock()
    req.app = {"hass": hass}
    encoded = json.dumps(body).encode()
    req.json = AsyncMock(return_value=body)
    return req


def _make_hass_with_entry(
    entry_id: str = "abc123",
    records: list[dict] | None = None,
    entry_present: bool = True,
    runtime_data_set: bool = True,
) -> tuple[MagicMock, MagicMock]:
    """Return (hass, entry) pair for view tests."""
    hass = MagicMock()
    if not entry_present:
        hass.config_entries.async_get_entry.return_value = None
        return hass, None

    entry = MagicMock()
    entry.domain = DOMAIN
    entry.data = {"blid": "abc_blid_123"}
    entry.entry_id = entry_id

    if not runtime_data_set:
        # Simulate the pre-ready state — no runtime_data attribute
        del entry.runtime_data
    else:
        ms = _make_mission_store(records or [])
        data = MagicMock()
        data.mission_store = ms
        entry.runtime_data = data

    hass.config_entries.async_get_entry.return_value = entry
    return hass, entry


# ── F15: format=export ────────────────────────────────────────────────────────

class TestExportEndpoint:
    def test_export_in_valid_formats(self):
        assert "export" in _VALID_FORMATS

    @pytest.mark.asyncio
    async def test_export_shape_has_required_keys(self):
        records = [_make_record("m_1"), _make_record("m_2")]
        hass, _ = _make_hass_with_entry(records=records)
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        required = {"export_version", "exported_at", "blid", "record_count", "records"}
        assert required.issubset(body.keys())

    @pytest.mark.asyncio
    async def test_record_count_matches_records(self):
        records = [_make_record("m_1"), _make_record("m_2"), _make_record("m_3")]
        hass, _ = _make_hass_with_entry(records=records)
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["record_count"] == 3
        assert len(body["records"]) == 3

    @pytest.mark.asyncio
    async def test_exported_at_is_iso_utc(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        ts = body["exported_at"]
        # Must parse as a valid datetime with UTC offset
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    @pytest.mark.asyncio
    async def test_blid_from_entry_data(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["blid"] == "abc_blid_123"

    @pytest.mark.asyncio
    async def test_empty_store_returns_zero_count(self):
        hass, _ = _make_hass_with_entry(records=[])
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["record_count"] == 0
        assert body["records"] == []

    @pytest.mark.asyncio
    async def test_export_version_is_1(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryView()
        req = _make_request(hass)
        resp = await view.get(req, "abc123")
        body = json.loads(resp.body)
        assert body["export_version"] == 1


# ── F16: import endpoint ──────────────────────────────────────────────────────

class TestImportEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_imports_records(self):
        hass, entry = _make_hass_with_entry(records=[])
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [_make_record("m_new_1"), _make_record("m_new_2")],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_async_save_called_after_import(self):
        hass, entry = _make_hass_with_entry(records=[])
        ms = entry.runtime_data.mission_store
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [_make_record("m_new_1")],
        }
        req = _make_post_request(hass, body)
        await view.post(req, "abc123")
        ms.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dedup_skips_existing_ids(self):
        existing = [_make_record("m_exists")]
        hass, _ = _make_hass_with_entry(records=existing)
        view = MissionHistoryImportView()
        body = {
            "export_version": 1,
            "records": [
                _make_record("m_exists"),   # duplicate — must be skipped
                _make_record("m_new"),      # new — must be imported
            ],
        }
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 1
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    async def test_missing_export_version_returns_400(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        body = {"records": []}   # no export_version
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_wrong_export_version_returns_400(self):
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        body = {"export_version": 99, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_empty_records_returns_zero(self):
        hass, entry = _make_hass_with_entry()
        ms = entry.runtime_data.mission_store
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        result = json.loads(resp.body)
        assert result["imported"] == 0
        assert result["skipped"] == 0
        ms.async_save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_oversized_payload_returns_400(self):
        """Bug 4 fix: payloads larger than 2×MAX_RECORDS must be rejected."""
        from custom_components.roomba_plus.mission_store import MAX_RECORDS
        hass, _ = _make_hass_with_entry()
        view = MissionHistoryImportView()
        # Build a list slightly over the size cap
        oversized = [_make_record(f"m_{i}") for i in range(MAX_RECORDS * 2 + 1)]
        body = {"export_version": 1, "records": oversized}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_entry_returns_404(self):
        hass, _ = _make_hass_with_entry(entry_present=False)
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "nonexistent")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_unready_entry_returns_503(self):
        hass, _ = _make_hass_with_entry(runtime_data_set=False)
        view = MissionHistoryImportView()
        body = {"export_version": 1, "records": []}
        req = _make_post_request(hass, body)
        resp = await view.post(req, "abc123")
        assert resp.status == 503
