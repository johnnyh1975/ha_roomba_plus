"""The mission archive of a Prime robot (4.6.0, A4).

WHY NOT `MissionArchive`. The Classic archive keeps iRobot's raw Classic
`/missionhistory` records and derives 26 signals from their fields
(`wlBars`, `chrgM`, finEvents …). A Prime history entry has another
shape, and most of those signals do not exist for it. What Prime lacked
was not the derivation but the length: the mission store keeps the last
`MissionStore.MAX_RECORDS` missions, and a backup carried no more.

So this keeps the records `prime_mission_sync.prime_entry_to_record()`
already builds -- the same shape the mission store holds, room visits
included -- for up to `MAX_RECORDS` missions, oldest dropped first. The
mission path (`GET /mission/{n_mssn}/path`) reads it, and the backup
includes it.

Nothing is invented: a record is stored as the history gave it, and a
mission read again replaces its earlier copy only where it adds fields.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "roomba_plus_prime_mission_archive"
MAX_RECORDS = 800  # as the Classic archive: about two years at one a day


class PrimeMissionArchive:
    """Up to `MAX_RECORDS` Prime mission records, oldest first."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._store: Store[dict[str, Any]] | None = None

    def _get_store(self, hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
        if self._store is None:
            self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        return self._store

    async def async_load(self, hass: HomeAssistant, entry_id: str) -> None:
        """Read the archive; an unreadable or missing file is an empty one."""
        raw = await self._get_store(hass, entry_id).async_load()
        records = raw.get("records") if isinstance(raw, dict) else None
        self._records = [r for r in records or [] if isinstance(r, dict) and r.get("id")]

    async def async_save(self, hass: HomeAssistant, entry_id: str) -> None:
        await self._get_store(hass, entry_id).async_save({"records": self._records})

    @property
    def records(self) -> list[dict[str, Any]]:
        """The records, oldest first. A copy of the list, not of each record."""
        return list(self._records)

    @property
    def record_count(self) -> int:
        return len(self._records)

    def add(self, records: Iterable[dict[str, Any]]) -> int:
        """Add records not yet kept and complete kept ones; how many changed.

        A record already kept takes the fields it lacked (a mission read
        again after 4.6.0 has its command fields); its own fields are not
        overwritten. Ordered by end time, trimmed to `MAX_RECORDS`.
        """
        by_id = {r["id"]: r for r in self._records}
        changed = 0
        for record in records:
            if not isinstance(record, dict) or not record.get("id"):
                continue
            kept = by_id.get(record["id"])
            if kept is None:
                copy = dict(record)
                self._records.append(copy)
                by_id[copy["id"]] = copy
                changed += 1
                continue
            missing = {k: v for k, v in record.items() if k not in kept}
            if missing:
                kept.update(missing)
                changed += 1
        if changed:
            self._records.sort(key=lambda r: str(r.get("ended_at") or ""))
            del self._records[: max(0, len(self._records) - MAX_RECORDS)]
        return changed

    def find_by_nMssn(self, n_mssn: int) -> dict[str, Any] | None:  # noqa: N802 -- the robot's name
        """The record of mission number `n_mssn`, newest first."""
        for record in reversed(self._records):
            if record.get("nMssn") == n_mssn:
                found: dict[str, Any] = record
                return found
        return None
