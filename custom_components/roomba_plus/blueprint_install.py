"""Put the shipped blueprints where Home Assistant finds them (4.6.0, B18).

The blueprints in the repository were reachable only through an import
link per blueprint, from the docs -- and HACS installs the integration
folder, not the repository, so nothing of them arrived with the update.
They now ship inside the integration (`blueprints/`, kept identical to
the repository's by scripts/check_blueprint_copies.py) and are copied to
`<config>/blueprints/automation/roomba_plus/` when Home Assistant starts.

NEVER OVERWRITTEN, NEVER BROUGHT BACK. A file that exists is left alone:
someone may have edited it. A blueprint Roomba+ copied once is not copied
again, so one the user deleted stays deleted (the names are kept in a
Store). And a blueprint imported through its link before -- Home
Assistant files those under the author's name -- is not copied a second
time beside it: same file name anywhere under blueprints/automation.

A NEW VERSION OF A SHIPPED BLUEPRINT therefore does not replace the
copy. The release notes say when one changed; re-importing it from its
link updates it, as before.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

SOURCE = Path(__file__).parent / "blueprints"
FOLDER = "roomba_plus"
_STORE_KEY = "roomba_plus_blueprints_installed"


def _copy(source: Path, root: Path, done: set[str]) -> tuple[list[str], set[str]]:
    """Copy each blueprint not copied before and not present anywhere
    under `root`; return the names copied and the names shipped.

    Runs in the executor: listing a folder is file I/O, and Home
    Assistant reports any of it on the event loop (4.6.0b1 listed the
    shipped folder there, found by the documentation demo)."""
    target_dir = root / FOLDER
    present = {p.name for p in root.rglob("*.yaml")} if root.exists() else set()
    shipped = sorted(source.glob("*.yaml"))
    copied: list[str] = []
    for blueprint in shipped:
        if blueprint.name in done or blueprint.name in present:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blueprint, target_dir / blueprint.name)
        copied.append(blueprint.name)
    return copied, {p.name for p in shipped}


async def async_install_blueprints(hass: HomeAssistant) -> list[str]:
    """Copy the shipped blueprints that are new to this installation."""
    store: Store[dict[str, Any]] = Store(hass, 1, _STORE_KEY)
    try:
        saved = await store.async_load() or {}
        done = {str(n) for n in saved.get("installed") or []}
        root = Path(hass.config.path("blueprints", "automation"))
        copied, shipped = await hass.async_add_executor_job(_copy, SOURCE, root, done)
        # Every shipped name counts as handled once seen -- copied now, or
        # already there under some folder -- so a later delete sticks.
        handled = done | shipped
        if handled != done:
            await store.async_save({"installed": sorted(handled)})
    except Exception:  # noqa: BLE001 -- a blueprint copy must never stop the integration
        _LOGGER.warning("Roomba+: could not copy the shipped blueprints", exc_info=True)
        return []
    if copied:
        _LOGGER.info(
            "Roomba+: added %d blueprint(s) to blueprints/automation/%s: %s",
            len(copied), FOLDER, ", ".join(copied),
        )
    return copied
