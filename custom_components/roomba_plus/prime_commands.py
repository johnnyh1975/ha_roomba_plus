"""Sending Prime commands so that a failure to send is visible.

Lives in its own module because both the vacuum entity and the Prime
buttons need it, and a late import from one into the other would be a
cycle-avoidance dodge where no cycle exists.
"""

from __future__ import annotations

from typing import Any

from homeassistant.exceptions import HomeAssistantError


async def _send_confirmed(robot: Any, command: str) -> None:
    """Sends a command and refuses to pretend it worked.

    `send_simple_command()` returns whether the broker acknowledged the
    publish, and every call site in this integration threw that away. A
    command that never reached iRobot therefore looked exactly like a
    command the robot chose to ignore: the button press succeeded, the
    service returned, and nothing happened.

    A field report described it as "no reaction from the logo" for start,
    locate and dry pad alike -- three controls, one silent transport, and
    four days spent looking at the controls.

    False here is not an error condition on the robot's side; it means we
    could not hand the command over. Saying so is the whole point.
    """
    published = await robot.send_simple_command(command)
    if published is False:
        raise HomeAssistantError(
            f"The '{command}' command was not accepted for delivery -- it never "
            "reached iRobot's cloud, so the robot has not seen it. This is a "
            "connection problem rather than a refusal by the robot."
        )
