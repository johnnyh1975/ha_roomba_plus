"""What WE sent, so the next report does not start with a guess.

THE QUESTION THAT KEPT COMING BACK. Three testers in one week reported
a command that produced nothing at all -- no error, no mission, robot
on its dock. Every time, the first thing to establish was whether the
command had gone out and with what payload. Every time, nothing could
say.

The robot keeps `lastCommand`, and it is genuinely useful -- but only
for commands it RECEIVED. When the doubt is whether it received one,
reading its record answers the wrong question. @mrsnyds' robot showed a
`dock` from three hours before his attempt: consistent with "we never
sent one", with "we sent one and it never arrived", and with "it
arrived and was ignored". Three possibilities, one record, no way to
tell them apart.

This is the other side of that wire.

DELIBERATELY SMALL. A ring of the last few entries, in memory, with the
payload reduced to what identifies it. Not a log: a log would need
rotation, redaction review and a place to live. What this has to do is
answer one question in a diagnostics download.

WHAT `ok` MEANS. Whether the publish succeeded, which the libraries
return and every one of our call sites used to discard. It does NOT
mean the robot acted: roombapy-prime records a robot that ignored
`start`, `stop`, `dock` and `find` for 61 hours with every one
broker-confirmed. A False here is proof of failure; a True is only the
absence of that particular failure.
"""

from __future__ import annotations

import time
from typing import Any

#: Enough to see a pattern, short enough to read at a glance.
MAX_ENTRIES = 12

#: Payload keys worth keeping. Everything else is dropped rather than
#: filtered, so a field added upstream cannot leak in unreviewed.
_KEEP = (
    "command", "pmap_id", "p2map_id", "user_pmapv_id", "map_id",
    "regions", "region_ids", "ordered", "initiator", "favorite_id",
)


def _summarise(payload: Any) -> dict[str, Any]:
    """The identifying parts of a payload, and nothing else.

    An allow-list rather than a deny-list: the cloud and the robot both
    put credentials in structures this code also handles, and a deny-list
    protects only against the fields somebody thought of. A password in a
    debug line already cost this project one release.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _KEEP:
        if key not in payload:
            continue
        value = payload[key]
        if key in ("regions", "region_ids") and isinstance(value, list):
            # Count and ids only -- the per-region params are long and
            # add nothing to "was it sent".
            out[key] = [
                r.get("region_id") if isinstance(r, dict) else r
                for r in value
            ]
        else:
            out[key] = value
    return out


def record_command(
    entry: Any, verb: str, payload: Any = None, *, ok: bool | None = None
) -> None:
    """Note one outgoing command. Never raises."""
    try:
        data = getattr(entry, "runtime_data", None)
        log = getattr(data, "sent_commands", None)
        if log is None:
            return
        log.append({
            "at": time.time(),
            "verb": verb,
            "ok": ok,
            "payload": _summarise(payload),
        })
        # Ring: keep the newest, drop from the front.
        del log[:-MAX_ENTRIES]
    except Exception:  # noqa: BLE001
        # A diagnostic aid must never be the reason a command fails.
        return
