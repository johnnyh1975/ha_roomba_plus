"""Service actions registered once, refusing targets that are not loaded.

QUALITY SCALE, action-setup. Services used to be registered when an entry
loaded and removed when the last one unloaded, so an automation calling
one could not even be validated while the integration was down. They are
now registered in `async_setup` and exist for the whole run.

That moves a check into the handlers: a call can now name a robot whose
config entry is not loaded — disabled, failed to set up, still retrying.
Its `runtime_data` does not exist then, and every handler reaches for it.
Rather than add the same test to fifteen inline entry lookups — the twin
pattern the architecture review kept finding — every handler is wrapped
once, here, at registration. The wrapper refuses the call with a
translated ServiceValidationError before the handler runs.

Targets that are not Roomba+ entities, or not found at all, are left to
the handlers, which already reject them with their own messages.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Coroutine
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

Handler = Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse]]


def _entity_ids(call: ServiceCall) -> list[str]:
    raw = call.data.get("entity_id")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [e for e in raw if isinstance(e, str)]
    return []


def raise_if_target_not_loaded(hass: HomeAssistant, call: ServiceCall) -> None:
    """Refuse a call aimed at a Roomba+ robot whose entry is not loaded."""
    ent_reg = er.async_get(hass)
    for entity_id in _entity_ids(call):
        reg_entry = ent_reg.async_get(entity_id)
        if reg_entry is None or reg_entry.config_entry_id is None:
            continue  # the handler reports unknown entities itself
        entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                f"{call.service}: the Roomba+ entry for {entity_id} is not "
                f"loaded ({entry.state.value})",
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entity_id": entity_id},
            )


def guarded(handler: Handler) -> Handler:
    """Wrap a service handler with the loaded-entry check."""

    @functools.wraps(handler)
    async def _wrapper(call: ServiceCall) -> ServiceResponse:
        raise_if_target_not_loaded(call.hass, call)
        return await handler(call)

    return _wrapper


def register(
    hass: HomeAssistant, domain: str, service: str, handler: Handler, **kwargs: Any
) -> None:
    """`hass.services.async_register`, with the handler guarded."""
    hass.services.async_register(domain, service, guarded(handler), **kwargs)
