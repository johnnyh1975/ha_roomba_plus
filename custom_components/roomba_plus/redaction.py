"""What counts as private, shared by the diagnostics and the debug capture.

Moved out of diagnostics.py in 4.6.0 so the debug capture
(debug_capture.py) redacts by the same rules without importing the whole
diagnostics module. diagnostics.py re-exports both names.
"""
from __future__ import annotations

import re
from typing import Final

#: A MAC address in any of the usual separators.
#:
#: Matched on VALUES, not on key names. Reported by @chairstacker: the
#: dump contained several unredacted MAC addresses, because they sat
#: under keys this code had never seen -- redacting a key called `mac`
#: does nothing for one called `wlan0HwAddr`.
_MAC_PATTERN = re.compile(r"\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b")


#: Substrings that make a key a secret regardless of its exact name.
#:
#: The named set above lists fields somebody noticed. This catches the
#: ones nobody has -- `wifiPasswd`, `authToken`, `deviceSecret` and
#: whatever the next firmware invents.
#:
#: Matched case-insensitively against the whole key, so `passwordHash`
#: and `hashedPassword` both go.
_SECRET_SUBSTRINGS: Final[tuple[str, ...]] = (
    "password", "passwd", "secret", "token", "apikey", "privatekey",
    "credential", "passphrase",
    # THE CLOUD LOGIN'S OTHER HALF (4.5.0). A Prime login returns AWS
    # credentials: `SecretKey` and `SessionToken` were caught above,
    # `AccessKeyId`, the Cognito identity and the IoT endpoint were not.
    # The diagnostics do not include the login today; this is for the
    # day something does.
    "accesskeyid", "cognitoid", "identityid", "endpoint",
)


def _is_secret_key(key: str) -> bool:
    """Whether a key names something that must never leave the machine.

    DELIBERATELY BROAD. A false positive redacts a harmless field and
    costs a question in an issue thread; a false negative puts a
    credential in a file somebody attaches publicly.

    `hash` is NOT in the substring list on its own -- it would catch
    `hashedMapId` and similar. `passwordHash` is covered by "password".
    """
    # Without separators: `access_key_id`, `cognito-id` and `CognitoId`
    # are one name.
    lowered = key.lower().replace("_", "").replace("-", "")
    return any(marker in lowered for marker in _SECRET_SUBSTRINGS)
