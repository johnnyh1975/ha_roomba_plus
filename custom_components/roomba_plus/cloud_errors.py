"""Why the iRobot cloud failed, in the user's language.

UNTIL 4.3 THE CAUSE REACHED THE USER IN ENGLISH. Every translated cloud
message had an `{error}` slot, filled with the library's English text:
a German user read "Die iRobot-Cloud hat die Anmeldung abgelehnt: Do not
re-enter your password: this account is temporarily locked ...". The
part that said what to do was the part nobody translated.

roombapy-prime 0.4.0 names the cause instead: every CloudError carries a
`reason` from a closed set (CloudErrorReason). The library states the
cause and does not translate; this module turns it into a translation
key, one per reason, and the translations say it in eight languages.

TWO FAMILIES OF KEYS, both named after the reason:

  * `exceptions.cloud_<reason>` for what Home Assistant shows on the
    integration card when setup fails (ConfigEntryNotReady,
    ConfigEntryAuthFailed). Every reason has one.
  * `config.error.cloud_<reason>` and `options.error.cloud_<reason>`
    for the login forms. Only the reasons a login can produce: the MQTT
    ones cannot happen before there is a connection.

A test holds both families to CloudErrorReason, so a reason added in
the library without a text here fails the build instead of reaching a
user as a bare key.
"""
from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations
from roombapy_prime import CloudError, CloudErrorReason

from .const import DOMAIN

#: Reasons only an open MQTT connection can produce. A login form never
#: sees them, so the forms carry no text for them.
MQTT_REASONS: frozenset[CloudErrorReason] = frozenset({
    CloudErrorReason.NOT_CONNECTED,
    CloudErrorReason.CONNECT_REFUSED,
    CloudErrorReason.PUBLISH_NOT_DELIVERED,
    CloudErrorReason.SUBSCRIPTION_NOT_SENT,
    CloudErrorReason.SUBSCRIPTION_REJECTED,
    CloudErrorReason.SHADOW_REJECTED,
})

#: Reasons a login form can show.
FLOW_REASONS: frozenset[CloudErrorReason] = frozenset(CloudErrorReason) - MQTT_REASONS

def reason_of(exc: BaseException) -> CloudErrorReason:
    """The reason of a CloudError. The library wraps its own transport
    failures, but a caller's own `asyncio.timeout()` or a bare aiohttp
    error can still arrive here, and those have an obvious reason too.
    UNKNOWN for anything else, and for a reason this integration has no
    text for yet (a newer library)."""
    if isinstance(exc, CloudError):
        try:
            return CloudErrorReason(exc.reason)
        except ValueError:
            return CloudErrorReason.UNKNOWN
    if isinstance(exc, TimeoutError):
        return CloudErrorReason.TIMEOUT
    if isinstance(exc, aiohttp.ClientError):
        return CloudErrorReason.CONNECTION_FAILED
    return CloudErrorReason.UNKNOWN


def translation_key(exc: BaseException) -> str:
    """`exceptions.<key>` for this error."""
    return f"cloud_{reason_of(exc).value}"


def translation_placeholders(exc: BaseException) -> dict[str, str]:
    """What the message for this error needs: nothing, today.

    NOT THE HTTP STATUS, although two texts once named it. Login errors
    carry the reason of a status but not the status itself, so the
    number would have read "HTTP ?" in exactly the messages a user sees
    at setup. The status stays in the English log message. Kept as a
    function so a raise states its placeholders in one place, and a
    test holds text and placeholders to each other."""
    del exc
    return {}


def flow_error(exc: BaseException) -> str:
    """`config.error.<key>` / `options.error.<key>` for a failed login."""
    reason = reason_of(exc)
    if reason not in FLOW_REASONS:
        reason = CloudErrorReason.UNKNOWN
    return f"cloud_{reason.value}"


async def async_reason_text(hass: HomeAssistant, exc: BaseException) -> str:
    """The reason's text in Home Assistant's language, for a message that
    says more than the reason alone.

    A translated message cannot contain another translation, so the one
    message that needs both -- the part-reset button's "the cloud did not
    reset the counter, the local one is unchanged" -- gets the reason as
    a finished sentence in its `{reason}` placeholder. Until 4.3 that
    placeholder held the exception's class name, in every language.

    Falls back to the reason's name if no text loads; never raises."""
    key = f"component.{DOMAIN}.exceptions.{translation_key(exc)}.message"
    try:
        table = await async_get_translations(
            hass, hass.config.language, "exceptions", {DOMAIN}
        )
    except Exception:  # noqa: BLE001 -- a message must not fail over its own text
        table = {}
    return table.get(key) or reason_of(exc).value
