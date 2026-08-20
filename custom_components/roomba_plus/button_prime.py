"""Buttons for Prime robots: saved favourites, and locate.

WHY FAVOURITES ARE BUTTONS AND NOT A SELECT.

A favourite is a stored routine -- "clean the kitchen and hall on deep,
twice" -- and pressing it runs that. There is no state to hold and
nothing to choose between; a select would imply the robot is currently
"on" one of them, which it is not.

One button per favourite also means an automation can name the one it
wants, and a dashboard can show only the ones that matter.

WHAT ELSE THE PLATFORM COULD HOLD, AND DOES NOT.

Classic offers evacuate, power off, sleep, spot clean and map training.
Prime has a confirmed equivalent for exactly one of them: `find`, via
send_simple_command. The rest are not "not built yet" -- no command has
been identified for them, and a button that does nothing when pressed is
worse than an absent one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .const import CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
from .entity import IRobotEntity
from .structural_failures import record_failure, record_success
from .prime_commands import _send_confirmed
from .prime_coordinator import _dock_reports_itself, get_prime_capability_flags

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)


class PrimeFavoriteButton(IRobotEntity, ButtonEntity):
    """Runs one saved favourite.

    Identified by favorite_id rather than by position or name: a
    favourite renamed in the iRobot app must not break an automation, and
    one deleted must not shift every button after it onto a different
    routine.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        favorite_id: str,
        name: str,
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._favorite_id = favorite_id
        self._attr_translation_key = "prime_favorite"
        self._attr_translation_placeholders = {"favorite": name or favorite_id}
        # WITHOUT THIS THE ENTITY IS "UNMANAGEABLE" AND ALWAYS WAS.
        #
        # Home Assistant will not register an entity that has no unique
        # id, so it lives in the state machine and not in the registry:
        # it cannot be renamed, hidden, assigned to an area, or referred
        # to reliably. The UI labels that "Unmanageable".
        #
        # @chairstacker chased it for three rounds as a leftover
        # registry entry, and his last report is what ruled that out:
        # the entity tracks his favourites exactly -- it appears when he
        # creates one, updates when he adds another, and disappears when
        # he deletes it. A stale entry does none of those things. It was
        # never stale; it was never registered.
        #
        # @ratpic83's log carried the other half from a different robot:
        # "attempts to attach a device to an entity without a unique
        # ID". Two testers, two symptoms, one missing line.
        #
        # The id is the favourite's own, matching `suggested_object_id`
        # below -- a favourite renamed in the iRobot app must not break
        # an automation, and one deleted must not shift every button
        # after it onto a different routine.
        self._attr_unique_id = f"{self.robot_unique_id}_favorite_{favorite_id}"

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug, keyed on the id.

        has_entity_name plus a translation_key otherwise has HA derive
        the entity_id from the TRANSLATED name -- different ids per
        language on first registration, and a rename in the app would
        move it again.
        """
        return f"favorite_{self._favorite_id}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"favorite_id": self._favorite_id}

    async def async_press(self) -> None:
        """Runs the favourite, re-reading its commands first.

        NOT CACHED AT SETUP. A favourite edited in the iRobot app should
        run as edited, and Home Assistant may not have reloaded since.
        The read costs one request per press, which is the right trade
        against running a routine the user changed weeks ago.

        Shares the service's implementation, so a button press and a
        run_favorite call cannot diverge.
        """
        if not await async_run_favorite(self._config_entry, self._favorite_id):
            _LOGGER.warning(
                "roomba_plus: favorite %s could not be run -- deleted in the "
                "iRobot app, or carrying no commands",
                self._favorite_id,
            )


@dataclass(frozen=True, kw_only=True)
class PrimeDockCommand:
    """One dock action, its wire command and the flag that gates it."""

    key: str
    command: str
    #: Attribute on the DOCK capability object, not the robot one. A
    #: robot without a self-emptying base still reports robot caps.
    dock_cap_attr: str
    #: Dock sub-state values in which the app offers this control, taken
    #: from its own res/raw availability specs. None means no state rule
    #: was found for this control.
    ready_states: tuple[int, ...] | None = None
    #: Which DockInfo field carries that sub-state.
    state_attr: str | None = None


#: The dock controls the iRobot app shows, as confirmed wire strings.
#:
#: All three come from CommandType's @SerialName annotations -- the same
#: source as `find`, which works. Not guessed:
#:
#:     Empty Bin              -> evac
#:     Wash mop / rinse dock  -> washpad
#:     Stop mop dry           -> stoppaddry
#:
#: `drypad` IS INCLUDED, and that reverses an earlier decision.
#:
#: It was left out because the app did not offer it -- drying started on
#: its own after a wash. @DaRealGuGu pointed out what that costs: stop
#: the drying and the only way to restart it is another full wash,
#: wasting a tank of water for nothing.
#:
#: The original reasoning conflated two things. Guessing at a COMMAND is
#: what this project refuses to do; offering a WORKFLOW the app does not
#: is a different question, and here it was plainly better. `drypad` is a
#: confirmed @SerialName wire string from the same enum as `evac`,
#: `washpad` and `stoppaddry` -- all three of which a tester has now
#: pressed with the dock responding within a second.
#:
#: AND THE APP HAS SINCE CAUGHT UP. App 3.0.0's Dock Controls sheet
#: shows "Dry Mop" as one of three buttons, with a "Start mop drying?"
#: confirmation (@chairstacker, screenshots). So this is no longer a
#: workflow the app lacks -- it is the same one, reached the same way.
#:
#: WHAT STAYS DIFFERENT is the other direction: the app greys out every
#: dock control once a task begins, so a drying cycle cannot be STOPPED
#: there once started. That divergence is deliberate and tested; see
#: prime_stop_pad_dry below.
#:
#: Worst case the robot ignores it, which is visible immediately in the
#: pad-dry sensor.
#:
#: STILL ABSENT: `flushsluice`, `flrefill`, `querydock`. Nobody has
#: asked for them, none has an observable effect a user could check, and
#: `querydock` is a read dressed as a command.
#:
#: There is no `stopwashpad` in the enum at all, so washing evidently
#: runs to completion.
PRIME_DOCK_COMMANDS: tuple[PrimeDockCommand, ...] = (
    PrimeDockCommand(
        key="prime_empty_bin", command="evac", dock_cap_attr="evac",
        # spec_dock_controls_evac_status: Available at 301, 355, 305.
        # Disabled at 351-354, 360, 365, 302.
        ready_states=(301, 355, 305), state_attr="state",
    ),
    PrimeDockCommand(
        key="prime_wash_pad", command="washpad", dock_cap_attr="pad_wash",
        # spec_dock_control_pad_wash_status: Available at 601 only.
        # Disabled at 602, 603 and the whole 649-699 error family.
        ready_states=(601,), state_attr="pw_state",
    ),
    PrimeDockCommand(
        key="prime_stop_pad_dry", command="stoppaddry", dock_cap_attr="pad_dry",
        # spec_dock_control_stop_pad_dry_status: Available at 702 -- the
        # state that means drying is actually running. Stopping something
        # that is not running is the one control where the app's rule is
        # the opposite of the start button's.
        #
        # DO NOT IMPLEMENT THE APP'S LOCK HERE. This divergence is
        # deliberate, it is the behaviour a tester specifically asked us
        # to keep, and it is the kind of thing a later "align with the
        # app" pass would quietly delete.
        #
        # App 3.0.0's Dock Controls sheet applies a blanket lock on top
        # of the per-state rules: once any dock task starts, all three
        # controls grey out and tapping one answers "Dock task in
        # progress. Try again later" (@chairstacker, screenshots). So a
        # drying cycle cannot be stopped from the app once begun. He
        # calls that the big drawback of the new UI, and being able to
        # stop it from Home Assistant the reason to keep ours as it is.
        #
        # The robot disagrees with its own app: `stoppaddry` sent from
        # here during a running dry cycle works, field-confirmed on the
        # same account and the same dock. The block is client-side, not
        # a refusal from the hardware -- the same shape as the nine
        # rejection reasons in `device_view_model_clean_plan`.
        #
        # So this button stays gated on 702 alone. Copying the blanket
        # lock would remove a capability that demonstrably works, to
        # match a UI decision iRobot made for its own reasons.
        ready_states=(702,), state_attr="pd_state",
    ),
    PrimeDockCommand(
        key="prime_start_pad_dry", command="drypad", dock_cap_attr="pad_dry",
        # spec_dock_control_pad_dry_status: Available at 701, 703.
        # Disabled across 749-757.
        ready_states=(701, 703), state_attr="pd_state",
    ),
)


class PrimeDockButton(IRobotEntity, ButtonEntity):
    """A dock action -- empty the bin, wash the pad, stop drying.

    GATED ON DOCK CAPABILITIES, not robot ones. A robot without a
    self-emptying base still reports its own caps happily; the dock flags
    are what say whether a base is there and what it can do.

    A real Combo reports `{"evac": 1, "pad_dry": 2, "pad_wash": 1}`, so
    the flags are graduated rather than boolean -- 2 is not "twice as
    true". Only an explicit 0 means absent, the same contract the other
    Prime capability checks use.
    """

    _attr_has_entity_name = True

    def __init__(
        self, blid: str, config_entry: RoombaConfigEntry, command: PrimeDockCommand
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._command = command
        self._attr_translation_key = command.key
        self._attr_unique_id = f"{self.robot_unique_id}_{command.key}"

    @property
    def suggested_object_id(self) -> str:
        return self._command.key

    @property
    def available(self) -> bool:
        """Offered only when the dock could actually act on it.

        THESE RULES ARE THE APP'S OWN. `res/raw/spec_dock_control_*.json`
        in the Prime APK are availability state machines mapping dock
        state to Available/Disabled, and until now this class had no
        `available` at all -- every dock button was pressable whenever
        the capability existed.

        What that cost: @chairstacker pressed Wash Pad with a tank
        removed, the robot spoke a complaint and the dock reported 671.
        The app would not have offered the button, because pw_state was
        not 601.

        Three blanket rules apply to every control, and they are the ones
        that matter most:

          - a dock error in 500-599 disables all of them
          - so does a running cycle (clean, spot, dock)
          - so does another dock command already in flight

        UNKNOWN MEANS AVAILABLE, deliberately. A robot that does not
        report a sub-state keeps its button: taking function away from a
        working robot because a field is missing would be the worse
        mistake, and this project has made it before by gating on a
        capability flag rather than on a field being present.
        """
        if not super().available:
            return False

        state = self._current_state
        dock = getattr(state, "dock", None) if state is not None else None
        if dock is None:
            return True

        # A dock error blocks every control, whatever the sub-states say.
        error = getattr(dock, "error", None)
        if isinstance(error, int) and 500 <= error <= 599:
            return False

        # And so does a mission: the dock will not wash, dry or empty
        # while the robot is out working.
        mission = getattr(state, "mission", None)
        cycle = getattr(mission, "cycle", None) if mission is not None else None
        if cycle in ("clean", "spot", "dock"):
            return False

        rule = self._command
        if rule.ready_states is None or rule.state_attr is None:
            return True
        current = getattr(dock, rule.state_attr, None)
        # DockState is an IntEnum on the model, a plain int on older
        # payloads. Both compare correctly against the plain ints above.
        value = getattr(current, "value", current)
        if not isinstance(value, int):
            return True
        return value in rule.ready_states

    @property
    def _current_state(self) -> Any:
        """The parsed ro-currentstate shadow, or None if not seeded."""
        from roombapy_prime.models import CurrentStateShadow  # noqa: PLC0415

        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("ro-currentstate")
        if raw is None:
            return None
        return CurrentStateShadow.from_json(raw)

    async def async_added_to_hass(self) -> None:
        """Follows the status coordinator so availability keeps up.

        Without this the rules above would be evaluated once and then
        frozen -- a button correctly hidden during a mission would stay
        hidden after it, which is worse than never hiding it.
        """
        await super().async_added_to_hass()
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self.schedule_update_ha_state)
            )

    async def async_press(self) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        # THE RESULT WAS THROWN AWAY. send_simple_command() reports
        # whether the broker acknowledged the publish; ignoring it made a
        # command that never left look exactly like one the robot chose
        # to ignore. See vacuum._send_confirmed for the full note.
        await _send_confirmed(robot, self._command.command)


class PrimeLocateButton(IRobotEntity, ButtonEntity):
    """Makes the robot announce where it is.

    `find` is the one simple command confirmed for Prime. Classic's other
    buttons -- evacuate, power off, spot clean, map training -- have no
    identified Prime equivalent, so they are absent rather than
    non-functional.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "prime_locate"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_locate"

    @property
    def suggested_object_id(self) -> str:
        return "locate"

    async def async_press(self) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is not None:
            await _send_confirmed(robot, "find")


async def async_build_prime_buttons(
    config_entry: RoombaConfigEntry,
) -> list[ButtonEntity]:
    """One button per favourite, plus locate.

    Favourites are read once at setup. They change rarely -- someone has
    to create one in the app -- and re-reading on every coordinator
    update would be a cloud call per battery percent.
    """
    data = config_entry.runtime_data
    robot = getattr(data, "prime_robot", None)
    if robot is None:
        return []

    entities: list[ButtonEntity] = [PrimeLocateButton(data.blid, config_entry)]

    # DOCK CONTROLS, matching what the iRobot app offers.
    #
    # Requested by @chairstacker, who screenshotted the app's dock panel.
    # The wire strings were then confirmed from CommandType's
    # @SerialName annotations rather than guessed.
    _cap, dock_cap = get_prime_capability_flags(config_entry)
    # A ROBOT THAT SAYS IT HAS NO DOCK GETS NO DOCK BUTTONS.
    #
    # @utkjmitch's Y351020 reports `dock: {"known": false}` with no
    # `cap` object, and the rule below reads a missing cap as "unknown,
    # offer anyway". That gave him wash and dry buttons for a dock that
    # has neither. `known: false` is a statement, not a gap.
    #
    # CORRECTED: this robot is NOT on a plain charge dock. It sits on an
    # auto-empty dock with a bag in it, and `cap.autoevac = 1` says so.
    # The tester's own earlier "dockless" label came from inferring
    # hardware out of an 18-key rw-settings list, and he has since
    # retracted it.
    #
    # SO `known: false` DOES NOT MEAN "NO DOCK". A robot with real
    # docking hardware reports it. Whatever `known` describes, it is
    # narrower than presence -- the dock's identity or details, most
    # likely.
    #
    # THE GATE IS RIGHT, AND NO LONGER ON UNCERTAIN GROUND.
    #
    # It used to be justified as "the dock does not describe itself, so
    # withhold". @utkjmitch settled the underlying question, which is
    # better: there is no evidence this hardware HAS an evac command to
    # send.
    #
    # He checked the app in three states rather than one -- docked and
    # idle with a full bag, mid-mission with the robot out, and
    # discarded a fourth taken while the robot was stranded off the
    # dock, on the grounds that any app would hide dock controls there.
    # No empty-now action in any of them, and no auto-empty frequency
    # control either.
    #
    # And the robot's side agrees. He ran a full mission with the
    # coordinator logging `dock` and `bin` on every change: across two
    # dock arrivals, a mid-mission recharge and a post-mission dock,
    # **the blocks never changed once**, and no `evac` phase ever
    # appeared. Read live while docked mid-mission, `dock` was byte for
    # byte what it reports at rest -- so `known: false` is not an
    # artifact of sampling a sleeping dock.
    #
    # Beside that: `number_of_evacuations: 0` across 49 missions,
    # `evac: null` across 1,293 timeline events, no evac keys in
    # rw-settings, and a base that demonstrably empties the robot.
    # This dock empties on its own and the robot is not party to it.
    #
    # WHAT WOULD OVERTURN IT: a `dock` block on some other robot
    # carrying a `cap` object where this one carries nothing. That would
    # mean `known: false` is about identity rather than capability, and
    # this dock is merely mute rather than passive.
    if _dock_reports_itself(config_entry):
        for command in PRIME_DOCK_COMMANDS:
            # None means unknown, only an explicit 0 means absent -- so a
            # robot that has not reported its dock yet still gets the
            # buttons rather than silently losing them.
            if (dock_cap is not None
                    and getattr(dock_cap, command.dock_cap_attr, None) == 0):
                continue
            entities.append(PrimeDockButton(data.blid, config_entry, command))

    # Locate is always offered; favourite buttons are optional.
    #
    # They are the only route that needs no setup -- tappable right
    # after install, and usable by voice, which a service call is not.
    # They are also the only one costing an entity each, which is why
    # somebody with fifteen favourites can turn them off and use the
    # `favorites` attribute and run_favorite service instead.
    if not config_entry.options.get(
        CONF_PRIME_FAVORITE_BUTTONS, DEFAULT_PRIME_FAVORITE_BUTTONS
    ):
        return entities

    # ONE CLOUD READ, not three.
    #
    # Setup already fetched the favourites into runtime_data for the
    # vacuum attribute. Fetching them again here would mean two requests
    # for the same list within a second of each other, and a third every
    # time run_favorite is called.
    #
    # The commands are re-read per press instead, which is the right
    # place for a fresh look: a favourite edited in the app between
    # setup and the press should run as edited.
    entities.extend(build_prime_favorite_buttons(config_entry))

    return entities


def _raw_favorite_is_for(favorite: dict[str, Any], blid: str) -> bool:
    """Whether a raw favourite dict belongs to this robot.

    Same contract as `_favorite_is_for`, reading the wire keys: a
    favourite whose command defs carry no `robot_id` at all is kept,
    because only an explicit non-matching id excludes.

    Held to the same rule deliberately -- one behaviour, two shapes,
    and the parsed path is what the attribute uses.
    """
    attributed = [
        str(command.get("robot_id") or "")
        for command in (favorite.get("commanddefs")
                        or favorite.get("command_defs") or [])
        if isinstance(command, dict)
    ]
    attributed = [value for value in attributed if value]
    if not attributed:
        return True
    return blid in attributed


def build_prime_favorite_buttons(
    config_entry: RoombaConfigEntry,
) -> list[PrimeFavoriteButton]:
    """One button per favourite currently on `runtime_data`.

    SPLIT OUT SO IT CAN BE CALLED AGAIN. The full builder above runs
    once at setup; this half runs on every schedule-coordinator refresh,
    so a favourite created in the iRobot app gets a button and a deleted
    one loses it.

    Reads the list rather than fetching: the coordinator refreshes it,
    and a builder that fetched would turn every entity sync into a
    network call.
    """
    data = config_entry.runtime_data
    buttons: list[PrimeFavoriteButton] = []
    for favorite in getattr(data, "prime_favorites", None) or []:
        # THE FILTER NEVER REACHED THIS PATH.
        #
        # @scenicsystemsllc (#80): the same favourites appear live on
        # every robot in a household. `_favorite_is_for` was written
        # for exactly this and wired into the favourites *attribute*
        # only -- the buttons themselves were built for every
        # favourite the account has.
        #
        # His clearest case: a Braava jet m6 offering "Vacuum
        # Everywhere". No map or region argument makes that right.
        #
        # `prime_favorites` here is raw dicts, not parsed objects, so
        # the check reads `commanddefs` directly rather than
        # `command_defs`.
        if not _raw_favorite_is_for(favorite, data.blid):
            continue

        favorite_id = favorite.get("id")
        if not favorite_id:
            # An entry without an id cannot be run and cannot be
            # identified -- a button for it would be a dead one whose
            # unique_id collides with the next such entry.
            continue
        buttons.append(PrimeFavoriteButton(
            data.blid,
            config_entry,
            str(favorite_id),
            favorite.get("name") or "",
        ))
    return buttons


#: READ ONCE, AT SETUP, AND NEVER AGAIN.
#:
#: `__init__.py` calls this during entry setup and nothing refreshes it.
#: A favourite created in the iRobot app after Home Assistant started
#: therefore does not appear until the entry is reloaded -- which is not
#: obvious to anyone testing, and has already produced a report that
#: read as "favourites are broken".
#:
#: @chairstacker's sequence on a33: check (absent), delete both in the
#: app, create a new one, check again (absent). The second check could
#: not have found anything regardless of whether this path works,
#: because nothing re-read the list.
#:
#: AND THE READ PATH DOES WORK ON AT LEAST ONE ROBOT. @jouwdan's Max 705
#: returns its favourites through this same code. So "favourites are
#: broken on Prime" is too broad a claim: something about one account's
#: data differs, and a reload-then-look is the test that separates the
#: two.
#:
#: NOT FIXED BY POLLING. Schedules are refreshed by a coordinator
#: because they drive entities that appear and disappear; favourites
#: drive buttons that would do the same, so the honest fix is a
#: coordinator here too rather than a timer bolted on. That is a change
#: worth making deliberately, not as a side effect of a bug report.
def _favorite_is_for(favorite: Any, blid: str) -> bool:
    """Whether this account-level favourite belongs to this robot.

    `GET /v1/user/favorites` IS ACCOUNT-LEVEL, NOT PER ROBOT, and every
    config entry was building a button for every favourite on the
    account. A two-robot household got both robots' favourites listed
    under each of them.

    Reported by @scenicsystemsllc, whose severity analysis is the part
    worth keeping: pressing a mislabelled button commands the robot it
    is displayed under -- correctly, over that robot's own connection --
    but with a payload built from a DIFFERENT robot's map. Usually that
    no-ops or errors. In the worst case a region id happens to be valid
    on the receiving robot's map too, and it cleans somewhere nobody
    asked for.

    THE FAVOURITE SAYS WHICH ROBOT IT IS FOR. Each `commanddefs` entry
    carries `robot_id` -- `RoutineCommand.asset_id` here, same field,
    and `mission_control.py` writes it back out under the wire name. So
    this is a direct match rather than the region-id heuristic the
    report suggested: comparing against a robot's pmap would also work
    but infers from geometry what the record states outright.

    FAILS OPEN. A favourite whose command defs carry no robot id at all
    is kept, because a favourite nobody can attribute is better shown
    under every robot than hidden from all of them -- the same contract
    the capability gates use. Only an explicit, non-matching id
    excludes.
    """
    commands = getattr(favorite, "command_defs", None) or []
    attributed = [
        str(getattr(command, "asset_id", "") or "")
        for command in commands
    ]
    attributed = [value for value in attributed if value]
    if not attributed:
        return True
    return blid in attributed


async def async_favorites_attribute(
    config_entry: RoombaConfigEntry,
) -> list[dict[str, Any]]:
    """The favourites list, for the vacuum entity's attributes.

    Costs no entity and answers the cases buttons cannot: an automation
    that iterates, a template that lists them, and the
    xiaomi-vacuum-map-card's menu, which reads attributes.

    Carries the ID alongside the name deliberately. An automation
    written against the ID survives a rename in the iRobot app; one
    written against the name does not, and the name is the only thing a
    button or a select could offer.
    """
    robot = getattr(config_entry.runtime_data, "prime_robot", None)
    if robot is None:
        return []
    try:
        favorites = await robot.get_favorites()
        record_success("favourite list")
    except Exception:  # noqa: BLE001
        # A key-name mismatch dropped every favourite for weeks while
        # this reported an empty list. An empty account and a broken
        # parser looked identical.
        record_failure("favourite list", "reading favourites")
        _LOGGER.debug("roomba_plus: could not read favorites", exc_info=True)
        return []

    kept = [
        {
            "id": str(getattr(f, "favorite_id", "")),
            "name": getattr(f, "name", "") or "",
        }
        for f in favorites or []
        if getattr(f, "favorite_id", None)
        and not getattr(f, "is_deleted", False)
        and not getattr(f, "is_hidden", False)
        and _favorite_is_for(f, config_entry.runtime_data.blid)
    ]

    # SAY WHAT WAS DROPPED, because silence here is what made this bug
    # unfindable for weeks.
    #
    # A favourite with no parseable id fails the first condition and
    # vanishes. Deleted and hidden ones are meant to vanish. All three
    # produced the same visible result -- no buttons -- and the only
    # difference between "your account has none" and "seven arrived and
    # none had an id" was invisible.
    #
    # @chairstacker has seven favourites and saw no buttons across
    # several releases. Each fix along the way was plausible and none of
    # them could be confirmed or ruled out, because the log said nothing
    # either way.
    dropped = len(favorites or []) - len(kept)
    if dropped:
        _LOGGER.warning(
            "Roomba+ Prime: %d of %d favourite(s) were not offered as buttons "
            "-- deleted, hidden, or carrying no usable id. Enable debug "
            "logging for roombapy_prime to see which",
            dropped,
            len(favorites or []),
        )
    return kept


async def async_run_favorite(
    config_entry: RoombaConfigEntry, favorite_id: str
) -> bool:
    """Runs one favourite by ID. Returns whether anything was sent.

    BY ID, not by name. A name is what the user typed in the iRobot app
    and can change there at any time; an automation keyed on it breaks
    silently when it does.
    """
    robot = getattr(config_entry.runtime_data, "prime_robot", None)
    if robot is None:
        return False

    try:
        favorites = await robot.get_favorites()
        record_success("favourite list")
    except Exception:  # noqa: BLE001
        _LOGGER.debug("roomba_plus: could not read favorites", exc_info=True)
        return False

    for favorite in favorites or []:
        if str(getattr(favorite, "favorite_id", "")) != str(favorite_id):
            continue
        commands = list(getattr(favorite, "command_defs", None) or [])
        if not commands:
            return False
        for command in commands:
            await robot.send_routine_command_via_cmd_topic(command)
        return True
    return False
