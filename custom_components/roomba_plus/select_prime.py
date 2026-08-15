"""Multi-step Prime settings, as select entities.

WHY SELECTS AND NOT MORE SWITCHES.

The four setting switches cover the booleans -- child lock, eco charging,
two-pass, extra suction. Suction LEVEL and pad wetness are graduated, and
a switch cannot express three or four steps.

It also lights up something in the xiaomi-vacuum-map-card: its menu icons
read a select entity's `options` attribute and offer them as a map-side
menu. So a select is not just the right entity type, it is the one that
card knows how to use.

WHAT IS OFFERED AND WHAT IS NOT.

`suctionLevel` is offered. Real values 2, 3 and 4 appear in two testers'
per-room operating-mode defaults, mapped to the profiles the app calls
light, normal and deep. 1 and 0 have never been observed, so they are not
offered: a level the robot rejects would fail silently, and a level that
means something else entirely would be worse.

`padWetness` is NOT offered, and that is the more interesting decision.
It is not a single value -- the model is three separate levels, one per
pad type (`disposable`, `pad_plate`, `reusable`). Which one applies
depends on the pad currently fitted, and the robot decides that. Exposing
one select that writes "the wetness" would mean guessing which of the
three the user meant.

Classic sidesteps this by offering two selects, one per pad type, because
its wire format has two separate fields. Prime's has three and a
fitted-pad concept on top. Until somebody establishes how the robot picks
between them, a select here would be a control whose effect depends on
hardware state the user cannot see.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import IRobotEntity
from .structural_failures import record_failure, record_success

if TYPE_CHECKING:
    from .models import RoombaConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Suction levels, keyed by the value the robot uses.
#:
#: CONFIRMED from two independent captures of `operating_mode_defaults`,
#: where each room stores a suctionLevel alongside a profile name:
#:
#:     2 -> "light"    3 -> "normal"    4 -> "deep"
#:
#: The option strings are the profile names rather than the numbers,
#: because that is what the iRobot app shows and what a user recognises.
#: Automations should target them by name for the same reason.
SUCTION_LEVELS: dict[int, str] = {2: "light", 3: "normal", 4: "deep"}


#: THE SIX CONTROLS FROM ISSUE #46, and the value sets are the vendor's
#: rather than this project's.
#:
#: Every set below comes from an enum in app 3.0.0, not from the
#: per-SKU picker lists. That distinction cost real work to find and it
#: matters: `getListBySKU` returns what one product mode's picker shows,
#: and a robot can sit outside it. @chairstacker's reports
#: `autoevacFreq = 1` and `pwReturn = 2` -- neither value appears in the
#: SKU list for his model, and both are correct.
#:
#: A picker built from the narrow list could not have represented his
#: robot's own settings, and his first tap would have changed a setting
#: he never chose to change.

#: `padDryDur` -- how long the dock runs the dryer, in hours.
#:
#: `DryDurType` (app 3.0.0): two=2, three=3, four=4, five=5, six=6.
#:
#: AN EARLIER VERSION OF THIS NOTE SAID NO VENDOR ENUM EXISTED, and
#: that the range was inferred from two field captures. It was written
#: while the Dart snapshot was still considered unreadable. It is
#: readable, `DryDurType` is in it, and the inferred range happened to
#: be exactly right -- which is luck, not method, and would not have
#: been worth relying on.
PAD_DRY_DURATIONS: dict[int, str] = {
    2: "2_hours", 3: "3_hours", 4: "4_hours", 5: "5_hours", 6: "6_hours",
}

#: `pwHeat` -- heated water for pad washing.
#:
#: `HeatType` (app 3.0.0): noHeat=0, defaultHeat=1, highHeat=2.
#:
#: THE MIDDLE ONE IS "DEFAULT", NOT "LOW". An earlier version of this
#: table labelled them off/low/high, which reads as three intensities
#: with the middle one below normal. The vendor's own name says the
#: opposite: 1 is the standard heated setting and 2 is the elevated one.
PAD_WASH_HEAT_LEVELS: dict[int, str] = {
    0: "no_heat", 1: "default_heat", 2: "high_heat",
}

#: `dock.cap.pw` level -> which `HeatType` values that dock offers.
#:
#: `DockPadWashingType` (app 3.0.0) turns what this project treated as
#: an opaque graduated flag into four named states:
#:
#:     0  notSupported        no pad washing at all
#:     1  supported           washing, no heat        <- @chairstacker
#:     2  heatedSupported     adds the default heat
#:     3  highHeatSupported   adds high heat
#:
#: TWO HEAT SETTINGS EXIST AND THIS IS THE GLOBAL ONE.
#:
#: @ratpic83 found `heated_water` per room and per operating mode, under
#: `rooms_metadata[].operating_mode_defaults`, and reasonably asked
#: whether an account-wide select can represent it at all.
#:
#: It can, because they are different fields. `pwHeat` is one of the 24
#: writable rw-settings; `heatedWater` is a field of `CommandParamsDTO`,
#: sent WITH a cleaning command. What he saw are the per-room DEFAULTS
#: for that command parameter -- all `None` on his robot, meaning
#: nothing has been set away from the default.
#:
#: SAME SHAPE AS `swScrub`: a household setting and a per-region
#: parameter for the same idea, and this project exposes the household
#: one. The per-region parameter is not offered anywhere, which is a
#: gap rather than an error.
#:
#: SO THE OPTION SET PLAUSIBLY DEPENDS ON THE DOCK -- and "plausibly"
#: is the honest word, because this is an inference from the enum's own
#: member names and not a gate anyone has read in the app.
#:
#: THE RESEARCH WARNS ABOUT EXACTLY THIS GUESS. Its correction table
#: records "Vermutung: Gate über dock.cap.pw" as **wrong** for the
#: neighbouring wash-frequency screen: `setWashFreq` calls
#: `ProductMode::getModeBySku()` first, so the SKU decides which UI
#: appears, not a capability field. Nobody has read what gates `pwHeat`.
#:
#: KEPT ANYWAY, and the reason is the direction of the risk. A dock at
#: level 2 offered high heat would accept the write and not produce it
#: -- silent. The fallback is fail-open, so an unreported `pw` still
#: offers everything, and key presence catches the common case on its
#: own. If this turns out to be the wrong gate, the symptom is a
#: missing option on a capable dock, which someone reports.
#:
#: chairstacker's dock reads `pw: 1`, which is why he has no `pwHeat`
#: key at all and why this control will not appear for him. Key presence
#: catches that case; this table catches the one key presence cannot --
#: a dock that HAS the key but not every level.
_PAD_WASH_HEAT_LEVELS_BY_CAP: dict[int, tuple[int, ...]] = {
    0: (),
    1: (),
    2: (0, 1),
    3: (0, 1, 2),
}


def _pad_wash_heat_options(dock_cap: Any) -> dict[int, str]:
    """The heat levels this dock offers.

    UNKNOWN MEANS OFFER EVERYTHING, the same fail-open contract used
    throughout this file: a dock that has not reported `pw` gets the
    full set rather than an empty control."""
    level = getattr(dock_cap, "pad_wash", None) if dock_cap is not None else None
    allowed = _PAD_WASH_HEAT_LEVELS_BY_CAP.get(level) if isinstance(level, int) else None
    if allowed is None:
        return dict(PAD_WASH_HEAT_LEVELS)
    return {v: label for v, label in PAD_WASH_HEAT_LEVELS.items() if v in allowed}

#: `pwAreaInterval` -- wash the pad every N units of area.
#:
#: `ReturnByArea` (app 3.0.0). Only meaningful while `pwReturn` is 2.
PAD_WASH_AREA_INTERVALS: dict[int, str] = {
    6: "6", 8: "8", 10: "10", 15: "15", 20: "20",
}

#: `pwTimeInterval` -- wash the pad every N minutes.
#:
#: `ReturnByTime` (app 3.0.0). Only meaningful while `pwReturn` is 1.
PAD_WASH_TIME_INTERVALS: dict[int, str] = {
    10: "10", 15: "15", 20: "20", 25: "25",
}

#: `pwReturn` -- TWO RANGES IN ONE FIELD, and that is the vendor's
#: design, not a modelling choice here.
#:
#: `ReturnByMode` (app 3.0.0) declares six values:
#:
#:     0/1/2       evRoom, byTime, byArea -- WHEN to return
#:     100/101/102 mission, refill, refillAndRoom -- WHAT ELSE the
#:                 return is for
#:
#: THE UPPER THREE WERE LABELLED Standard/Medium/High AND THAT WAS
#: INVENTED. `ReturnByMode` names them `mission`, `refill` and
#: `refillAndRoom`; nothing in the extract calls them levels, and
#: nothing grades them. The labels came from an assumption that a
#: three-value range must be a scale.
#:
#: WHAT IT COST, and this is why it is worth the paragraph.
#: @ratpic83 opened the select, read "standard | medium | high" beside
#: a dock reporting `pw: 3`, and reported that the three heat levels
#: were present and confirmed -- a completely reasonable reading of
#: what we showed him. He then went into the app, found no heat control
#: anywhere, and retracted his own correct observation to apologise for
#: "noise". The noise was ours.
#:
#: A WRONG LABEL DOES NOT STAY INSIDE THE CODE. It becomes a field
#: report, and then a retraction of a field report.
#:
#: ONE ENTITY, NOT TWO. `_updateWashFreqByType` branches on the value's
#: type and writes this single field; splitting the ranges into two
#: entities would mean two controls fighting over one wire key.
#:
#: THE APP SHOWS A THIRD THING AGAIN. @ratpic83's AutoWash screen has
#: exactly two entries -- wash frequency and dry duration -- and the
#: frequency picker offers three AREA presets (5/10/15 m²), not modes.
#: So the vendor's own UI collapses `byArea` plus an interval into one
#: three-way choice, which is neither of this field's ranges.
PAD_WASH_RETURN_MODES: dict[int, str] = {
    0: "after_each_room",
    1: "by_time",
    2: "by_area",
    100: "mission",
    101: "refill",
    102: "refill_and_room",
}

#: `autoevacFreq` -- how often the dock empties itself.
#:
#: `ClearFreqType` (app 3.0.0). WHICH SUBSET APPLIES DEPENDS ON
#: `cap.autoevac`, so this full map is filtered per robot at setup --
#: see _autoevac_options().
AUTOEVAC_FREQUENCIES: dict[int, str] = {
    0: "every_routine",
    1: "every_2_routines",
    2: "every_3_routines",
    4: "on_dock_return",
    10: "10", 15: "15", 25: "25", 30: "30", 50: "50",
}

#: `CapAutoEvac` level -> which `ClearFreqType` values that level offers.
#:
#: CONFIRMED AGAINST HARDWARE AND A SCREENSHOT. @chairstacker reports
#: `cap.autoevac = 1` and `autoevacFreq = 1`; his Auto-Empty Frequency
#: screen shows exactly three options. Level 1 is `freqModes`, and
#: `freqModes` is 0/1/2. The two numbers and the picture agree.
#:
#: @utkjmitch reports `cap.autoevac = 1` with NO `autoevacFreq` key and
#: no picker anywhere in the app. So the capability says which values
#: apply IF there is a control; key presence says whether there is one.
#: Both rules are needed and neither replaces the other.
_AUTOEVAC_LEVELS: dict[int, tuple[int, ...]] = {
    0: (),                                    # taskEndOnly: no choice
    1: (0, 1, 2),                             # freqModes
    2: (0, 1, 2, 10, 15, 25, 30, 50),         # freqWithArea
    3: (0, 1, 2, 4, 10, 15, 25, 30, 50),      # taskEndOrDockReturn
}


#: SKU-narrowed option lists (`getListBySKU`, app 3.0.0).
#:
#: FIVE PRODUCT MODES SEE FEWER OPTIONS than the enums declare, and the
#: vendor narrows per ProductMode index rather than per capability:
#:
#:     G2 robot_415_combo   R2 robot_575_combo   Z1 robot_875
#:
#: N2 (robot_515_combo) and V1 (robot_615_combo) appear in the vendor's
#: branch table with NO overrides of their own -- they take the standard
#: lists. An earlier version of this comment listed all five as narrowed
#: because the report's prose names five special cases; two of them are
#: special only in having their own branch.
#:
#: NONE OF THE CURRENT TESTERS IS ONE OF THESE -- G1, N1, W1, Y3 and Y4
#: all take the standard lists. But V1 and Z1 were added to
#: `PRIME_SKU_PREFIXES` in the same session as these controls, so a
#: robot that only just became recognisable would have been offered
#: intervals its own app does not show.
#:
#: THE BRANCH ASSIGNMENT IS PARTLY INFERRED, and the source says so: the
#: assembler does not separate the branches cleanly, and the mapping
#: rests on agreement with `product_profile.json` from 2.2.4. Where the
#: two agree it is solid; N2 and V1 have no list of their own and share
#: a branch with a neighbour.
#:
#: SO NARROWING IS APPLIED ONLY WHERE BOTH SOURCES AGREE. A wrong
#: narrowing hides an option a robot supports, which is worse than
#: offering one it does not -- the second gets rejected, the first is
#: invisible.
#: CORRECTED AGAINST `v3_sku_value_lists.json`. The first version of
#: this table was written from the research report's PROSE and had
#: seven of its ten entries wrong -- padDryDur overrides invented for
#: G2, N2 and R2, a pwTimeInterval override invented for V1 and Z1, a
#: pwAreaInterval override invented for N2, and Z1's real
#: `autoevacFreq` narrowing missed entirely.
#:
#: The data file was in the same package the whole time. Reading the
#: summary of a table instead of the table is the same failure this
#: project keeps finding, one level up: the summary said five SKUs are
#: narrowed, which is true, and said nothing reliable about which
#: fields.
_SKU_VALUE_LISTS: dict[str, dict[str, tuple[int, ...]]] = {
    "G2": {"pwAreaInterval": (6, 8, 10)},
    "R2": {"pwAreaInterval": (10, 15, 20)},
    "Z1": {"padDryDur": (4, 5, 6), "autoevacFreq": (0, 15, 30)},
}


def _sku_narrowed(wire_key: str, sku: str | None, values: dict[int, str]) -> dict[int, str]:
    """The options this robot's product mode offers.

    Unknown SKU means offer everything, the same fail-open contract used
    for capabilities: a robot whose SKU has not arrived should get the
    full list rather than an arbitrary subset.
    """
    if not sku:
        return values
    allowed = _SKU_VALUE_LISTS.get(sku[:2].upper(), {}).get(wire_key)
    if not allowed:
        return values
    return {v: label for v, label in values.items() if v in allowed}


def _autoevac_options(cap: Any) -> dict[int, str]:
    """The auto-evacuation values this robot's capability level offers.

    UNKNOWN MEANS OFFER EVERYTHING, the same contract the rest of this
    file uses: a robot that has not reported `cap.autoevac` gets the
    full set rather than an empty control. Only a level the vendor
    defines narrows it, and only `taskEndOnly` empties it entirely.

    NOT NARROWED BY SKU, AND A SCREENSHOT SAYS WHY. `getListBySKU`
    reports a standard `autoevacFreq` list of [0, 10, 15, 25, 30] --
    area-based values only. @chairstacker's robot is a G1, which takes
    that standard list, and his Auto-Empty Frequency screen shows
    exactly three options: every routine, every 2, every 3. That is
    0/1/2, and none of them is in the SKU list.

    His `cap.autoevac` reads 1 (`freqModes`), which predicts 0/1/2
    exactly. So the capability decides this control and the SKU list
    does not -- and the SKU extraction says of itself that its branch
    assignment is partly inferred. A photograph outranks it.
    """
    level = getattr(cap, "autoevac", None) if cap is not None else None
    allowed = _AUTOEVAC_LEVELS.get(level) if isinstance(level, int) else None
    if allowed is None:
        return dict(AUTOEVAC_FREQUENCIES)
    return {v: label for v, label in AUTOEVAC_FREQUENCIES.items() if v in allowed}


@dataclass(frozen=True, kw_only=True)
class PrimeSelectDescription(SelectEntityDescription):
    """One graduated rw-settings value."""

    #: The wire key set_setting() takes.
    wire_key: str
    #: The RobotSettings attribute the value reads back from. Named
    #: separately because the two differ, and assuming they match has
    #: produced false "field missing" reports in this project before.
    model_attr: str
    #: Robot value -> option string.
    values: dict[int, str]
    #: cap flag that must not be explicitly 0. None means always offer --
    #: unknown is not absent, only an explicit 0 is.
    cap_attr: str | None = None


#: `padWetness.padPlate` — how wet the mopping pad is kept.
#:
#: THE ONLY VALUE SET IN THIS FILE WITH NO DOCUMENTED SOURCE. The key,
#: its dot notation and its capability gate are all confirmed; the
#: RANGE is not. It appears in no vendor enum, no settings-key type, no
#: locale string and no capability table.
#:
#: WHAT IS ACTUALLY KNOWN:
#:   - `cap.ppWetLvl` gates it (`padPlateWetnessLevel`, THING shadow)
#:   - app 3.0.0 addresses `padWetness.padPlate` as a single field,
#:     not the whole map -- so a read-modify-write is not required
#:   - @chairstacker's robot reports `padPlate: 4` with `ppWetLvl: 3`
#:   - the app labels it "Liquid Amount" and its values "Level N",
#:     which says a number rather than named settings
#:
#: SO ppWetLvl IS A TIER, NOT A COUNT. Every other `cap.*` field in this
#: vendor's model is a tier (evac, pd, pw, scrub, mc, dSpot), and a
#: count would make `padPlate: 4` illegal on a robot reporting 3.
#:
#: FOUR IS THE HIGHEST VALUE ANYONE HAS SEEN, and that is the guess.
#: A robot offering five would show four options here and its fifth
#: would be unreachable -- which is why `_wetness_options()` widens the
#: set to include whatever the robot actually reports, rather than
#: telling the owner their own setting does not exist.
PAD_WETNESS_LEVELS: Final[dict[int, str]] = {
    1: "1", 2: "2", 3: "3", 4: "4",
}


def _reported_wetness(config_entry: Any) -> int | None:
    """This robot's current `padWetness.padPlate`, or None.

    Read from the shadow rather than from the model, because the model
    exposes the map and the interesting value is a sub-key -- and the
    caller only needs it to widen a guessed ceiling.
    """
    coordinator = getattr(
        config_entry.runtime_data, "prime_status_coordinator", None
    )
    if coordinator is None or coordinator.data is None:
        return None
    raw = coordinator.data.get("rw-settings")
    if not isinstance(raw, dict):
        return None
    reported = raw.get("state", {}).get("reported") if "state" in raw else raw
    if not isinstance(reported, dict):
        return None
    wetness = reported.get("padWetness")
    if not isinstance(wetness, dict):
        return None
    value = wetness.get("padPlate")
    return value if isinstance(value, int) else None


def _wetness_options(current: Any) -> dict[int, str]:
    """The wetness levels to offer, widened to include the robot's own.

    A GUESSED CEILING MUST NOT HIDE A REAL VALUE. If a robot reports a
    level above the highest anyone has seen, the honest response is to
    offer it rather than to render the owner's current setting as
    invalid -- the guess is ours, and the robot is the authority.

    Narrowing never happens here: a robot reporting 2 still gets 1-4,
    because nothing says which levels are unavailable, only which one is
    selected.
    """
    values = dict(PAD_WETNESS_LEVELS)
    if isinstance(current, int) and 1 <= current <= 20:
        for level in range(1, current + 1):
            values.setdefault(level, str(level))
    return dict(sorted(values.items()))


#: WHICH VENDOR ENUM EACH VALUE SET COMES FROM, declared so a guard can
#: check it rather than a reader having to remember.
#:
#: Two of these were built from recall while the extract sat unread:
#: `padDryDur` shipped claiming no enum existed, and `pwHeat` shipped
#: with wrong labels and no dock gate. Both were caught by somebody
#: asking. This table is what replaces asking.
#:
#: DECLARED, NOT DISCOVERED. Matching value sets against the extract by
#: shape produces false hits -- `{0, 1, 2}` matches four different
#: vendor enums. A match only means something when the name came first.
#:
#: A set with no vendor enum belongs here with None and a reason, not
#: absent from the table.
VENDOR_ENUM_SOURCES: dict[str, str | None] = {
    "SUCTION_LEVELS": None,  # from operating_mode_defaults captures, not an enum
    # NO SOURCE ANYWHERE, unlike SUCTION_LEVELS which at least came from
    # field captures. The key, its dot notation and its gate are all
    # confirmed; the range is in no enum, no settings-key type, no locale
    # string and no capability table. Four is the highest value seen, and
    # `_wetness_options()` widens the set to whatever the robot reports
    # so the guess cannot hide a real value.
    "PAD_WETNESS_LEVELS": None,
    "PAD_DRY_DURATIONS": "DryDurType",
    "PAD_WASH_HEAT_LEVELS": "HeatType",
    "PAD_WASH_AREA_INTERVALS": "ReturnByArea",
    "PAD_WASH_TIME_INTERVALS": "ReturnByTime",
    "PAD_WASH_RETURN_MODES": "ReturnByMode",
    "AUTOEVAC_FREQUENCIES": "ClearFreqType",
    # Capability LEVELS rather than setting values: the keys are
    # cap values and the payload is which setting values that level
    # permits. Checked against the cap enum's own value set.
    "_AUTOEVAC_LEVELS": "CapAutoEvac",
    "_PAD_WASH_HEAT_LEVELS_BY_CAP": "DockPadWashingType",
}


PRIME_SELECTS: tuple[PrimeSelectDescription, ...] = (
    PrimeSelectDescription(
        key="prime_suction_level",
        translation_key="prime_suction_level",
        wire_key="suctionLevel",
        model_attr="suction_level",
        values=SUCTION_LEVELS,
        cap_attr="suction_lvl",
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_wetness",
        translation_key="prime_pad_wetness",
        wire_key="padWetness.padPlate",
        model_attr="pad_wetness",
        values=PAD_WETNESS_LEVELS,
        # EXPLICIT 0 MEANS NO WETNESS CONTROL. @ratpic83's 405 Combo
        # reports `ppWetLvl: 0` while @chairstacker's reports 3 -- same
        # robot model, different dock, and the first robot this project
        # has seen that should not get this control.
        cap_attr="pp_wet_lvl",
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_dry_duration",
        translation_key="prime_pad_dry_duration",
        wire_key="padDryDur",
        model_attr="pad_dry_duration",
        values=PAD_DRY_DURATIONS,
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_wash_return",
        translation_key="prime_pad_wash_return",
        wire_key="pwReturn",
        model_attr="pad_wash_return",
        values=PAD_WASH_RETURN_MODES,
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_wash_area_interval",
        translation_key="prime_pad_wash_area_interval",
        wire_key="pwAreaInterval",
        model_attr="pad_wash_area_interval",
        values=PAD_WASH_AREA_INTERVALS,
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_wash_time_interval",
        translation_key="prime_pad_wash_time_interval",
        wire_key="pwTimeInterval",
        model_attr="pad_wash_time_interval",
        values=PAD_WASH_TIME_INTERVALS,
        entity_category=EntityCategory.CONFIG,
    ),
    PrimeSelectDescription(
        key="prime_pad_wash_heat",
        translation_key="prime_pad_wash_heat",
        wire_key="pwHeat",
        model_attr="pad_wash_heat",
        values=PAD_WASH_HEAT_LEVELS,
        entity_category=EntityCategory.CONFIG,
    ),
    #: LAST, because its options are not fixed. `values` here is the
    #: full ClearFreqType map; the setup narrows it per robot through
    #: _autoevac_options() before constructing the entity.
    PrimeSelectDescription(
        key="prime_autoevac_frequency",
        translation_key="prime_autoevac_frequency",
        wire_key="autoevacFreq",
        model_attr="autoevac_freq",
        values=AUTOEVAC_FREQUENCIES,
        entity_category=EntityCategory.CONFIG,
    ),
)


class PrimeSettingSelect(IRobotEntity, SelectEntity):
    """A graduated setting on the rw-settings shadow.

    Same mechanism as the setting switches: set_setting() to write,
    PrimeStatusCoordinator to read back.
    """

    _attr_has_entity_name = True
    entity_description: PrimeSelectDescription

    def __init__(
        self,
        blid: str,
        config_entry: RoombaConfigEntry,
        description: PrimeSelectDescription,
        values: dict[int, str] | None = None,
    ) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self.entity_description = description
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_{description.key}"
        #: PER-ROBOT OPTIONS, not per-description.
        #:
        #: `autoevacFreq` offers a different set depending on
        #: `cap.autoevac` -- three values on a freqModes dock, nine on a
        #: taskEndOrDockReturn one. Everything else passes its full map
        #: through unchanged.
        self._values = values or description.values
        self._attr_options = list(self._values.values())

    @property
    def suggested_object_id(self) -> str:
        """Locale-independent slug. has_entity_name plus a
        translation_key otherwise has HA derive the entity_id from the
        TRANSLATED name, producing different ids per language."""
        return self.entity_description.key

    @property
    def current_option(self) -> str | None:
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is None or coordinator.data is None:
            return None
        raw = coordinator.data.get("rw-settings")
        if raw is None:
            return None

        from roombapy_prime.models import RobotSettings  # noqa: PLC0415

        value = getattr(
            RobotSettings.from_json(raw), self.entity_description.model_attr, None
        )
        if value is None:
            return None
        # A value outside the known set is reported as None rather than
        # guessed at. The robot may support levels nobody has observed,
        # and showing "normal" for an unknown number would be a lie the
        # user cannot detect.
        return self._values.get(int(value))

    @property
    def available(self) -> bool:
        """Unknown is not a default.

        A setting whose value has never been read must not render as
        whatever happens to be first in the list -- someone would see
        "light" and believe that is what the robot is set to.
        """
        return super().available and self.current_option is not None

    async def async_select_option(self, option: str) -> None:
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        wire_value = next(
            (v for v, name in self._values.items() if name == option),
            None,
        )
        if wire_value is None:
            _LOGGER.warning(
                "roomba_plus: %r is not a known option for %s",
                option, self.entity_description.key,
            )
            return
        await robot.set_setting(self.entity_description.wire_key, wire_value)
        # Optimistic, then corrected by the coordinator: the shadow delta
        # arrives within a second or two, and leaving the UI on the old
        # value until then reads as a failed command.
        self._attr_current_option = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await IRobotEntity.async_added_to_hass(self)
        coordinator = self._config_entry.runtime_data.prime_status_coordinator
        if coordinator is not None:
            self.async_on_remove(
                coordinator.async_add_listener(self.async_write_ha_state)
            )


class PrimeMapSelect(IRobotEntity, RestoreEntity, SelectEntity):
    """Which of several maps the Rooms Map image shows.

    WHY A SELECT AND NOT ONE IMAGE PER MAP. The other design looks better
    on paper -- two dashboards could show different floors -- and the
    person who would live with it asked for this one instead
    (@chairstacker, issue #45). He has two maps, uses one constantly and
    the other rarely, and an entity per map gives no way to keep the rare
    one off a dashboard at all.

    A user with two equally-used floors would answer differently. Nobody
    is asking for that, and the design can change when somebody does.

    "Follow the robot" IS THE DEFAULT and stays the default. A user who
    never touches this sees exactly what they saw before it existed: the
    map the robot reports standing on, falling back to the first.

    ONE ENTITY EVEN WITH ONE MAP. Gating it on map count would make the
    entity appear and disappear as maps are added and removed in the app,
    which is worse than a select with a single option -- an automation
    referencing a vanished entity fails loudly for a reason that has
    nothing to do with automations.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "prime_map"
    _attr_icon = "mdi:layers-outline"

    #: Not a map id, so it cannot collide with one.
    FOLLOW_ROBOT = "follow_robot"

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        # Same call shape as PrimeSettingSelect: roomba=None, because a
        # Prime robot has no roombapy object behind it.
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_map"
        self._names: dict[str, str] = {}

    @property
    def suggested_object_id(self) -> str:
        return "prime_map"

    @property
    def options(self) -> list[str]:
        """Map names, with "follow the robot" first.

        Names rather than ids: `hh_..._1752720067` tells a user nothing.
        A map with no name falls back to its id, which is ugly and
        honest -- inventing "Map 2" would put a label on screen that
        matches nothing in the iRobot app.
        """
        return [self.FOLLOW_ROBOT, *self._names.values()]

    @property
    def current_option(self) -> str | None:
        selected = getattr(
            self._config_entry.runtime_data, "prime_selected_map_id", None
        )
        if selected is None:
            return self.FOLLOW_ROBOT
        # A map deleted in the app leaves a selection pointing at
        # nothing. Falling back to following the robot is better than
        # reporting an option that is not in the list, which Home
        # Assistant logs as an error on every state write.
        return self._names.get(selected, self.FOLLOW_ROBOT)

    async def async_select_option(self, option: str) -> None:
        data = self._config_entry.runtime_data
        if option == self.FOLLOW_ROBOT:
            data.prime_selected_map_id = None
        else:
            match = next(
                (mid for mid, name in self._names.items() if name == option), None
            )
            if match is None:
                raise ServiceValidationError(
                    f"{option} is not one of this account's maps"
                )
            data.prime_selected_map_id = match

        # The image caches its render against a map id, so it notices the
        # change on the next request without being told. Writing state
        # here is what moves the select itself.
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # RESTORED, NOT PERSISTED IN OPTIONS. Writing the choice into the
        # config entry would reload the integration on every change,
        # which is a heavy price for a dropdown.
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            self._restore = last.state
        await self._async_load_names()

    async def _async_load_names(self) -> None:
        """Map ids to display names, once per setup.

        Map names change when somebody renames one in the app, which is
        rare enough that a cloud call per image request would be the
        wrong trade -- the same reasoning the rooms map itself uses for
        its version check.
        """
        robot = self._config_entry.runtime_data.prime_robot
        if robot is None:
            return
        try:
            versions = await robot.get_active_map_versions()
            record_success("map name read")
        except Exception:  # noqa: BLE001
            record_failure("map name read", "listing map names")
            _LOGGER.debug(
                "roomba_plus: could not read map names for %s -- the select "
                "will offer only 'follow the robot'", self._blid, exc_info=True,
            )
            return

        names: dict[str, str] = {}
        for entry in versions or []:
            if not isinstance(entry, dict):
                continue
            map_id = entry.get("p2map_id")
            if not map_id:
                continue
            names[str(map_id)] = str(entry.get("name") or map_id)
        self._names = names

        restore = getattr(self, "_restore", None)
        if restore and restore != self.FOLLOW_ROBOT:
            match = next((m for m, n in names.items() if n == restore), None)
            if match is not None:
                self._config_entry.runtime_data.prime_selected_map_id = match
        self.async_write_ha_state()


class PrimeCleaningModeSelect(IRobotEntity, RestoreEntity, SelectEntity):
    """What a cleaning started from Home Assistant will do.

    ASKED FOR BY A USER (@arielgr): the iRobot app offers vacuum, mop, or
    both when starting a clean, and this integration had a suction-level
    control and nothing for the mode. The values had been confirmed for
    weeks; nobody had asked until he did.

    NOT A ROBOT SETTING. Suction level lives in `rw-settings` and the app
    shows it; the mode is sent per command, in the regions of a start.
    So this is a preference of ours -- and rather than leave it a claim
    nobody can check, it is REFLECTED FROM THE ROBOT'S OWN LAST START.

    WHY THE STATUS FIELD IS NOT THE SOURCE, though it looks like one.
    `cleanMissionStatus.operatingMode` uses a different vocabulary from
    the command:

        command 32  (vacuum and mop)   ->  status 6
        command 512 (vacuum then mop)  ->  status 4
        pad wash, no command at all    ->  status 6

    A 6 cannot be told apart from a pad wash, so reading the status would
    make this jump to "vacuum and mop" while the dock cleaned a pad --
    and an automation reading it would get an answer about maintenance.

    `rw-software.lastCommand` carries the real thing, in the command's
    own vocabulary. **Only a `start` counts**: `drypad` and `washpad`
    also carry regions and a mode, and neither is a cleaning choice.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "prime_cleaning_mode"
    _attr_icon = "mdi:auto-mode"

    #: The four the app offers, in the command's vocabulary.
    MODES: dict[str, int] = {
        "vacuum": 2,
        "mop": 4,
        "vacuum_and_mop": 32,
        "vacuum_then_mop": 512,
    }

    def __init__(self, blid: str, config_entry: RoombaConfigEntry) -> None:
        IRobotEntity.__init__(
            self, roomba=None, blid=blid, config_entry=config_entry
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{self.robot_unique_id}_prime_cleaning_mode"
        self._restored: str | None = None

    @property
    def suggested_object_id(self) -> str:
        return "prime_cleaning_mode"

    @property
    def options(self) -> list[str]:
        return list(self.MODES)

    def _mode_from_last_start(self) -> str | None:
        """The mode of the robot's last START command, if it had one.

        Reads the first region's params rather than checking every one:
        a mixed-mode command is possible on paper, has never been seen,
        and the app shows a single mode per mission.
        """
        coordinator = getattr(
            self._config_entry.runtime_data, "prime_status_coordinator", None
        )
        shadows = getattr(coordinator, "data", None)
        if not isinstance(shadows, dict):
            return None
        software = shadows.get("rw-software")
        last = software.get("lastCommand") if isinstance(software, dict) else None
        if not isinstance(last, dict) or last.get("command") != "start":
            return None
        for region in last.get("regions") or []:
            if not isinstance(region, dict):
                continue
            mode = (region.get("params") or {}).get("operatingMode")
            for name, value in self.MODES.items():
                if mode == value:
                    return name
            # A mode outside the four is left unreported rather than
            # rounded to the nearest one -- the select would then claim
            # something the robot is not doing.
            return None
        return None

    @property
    def current_option(self) -> str | None:
        """What the robot last started with, or what the user chose.

        The robot wins when it has something to say. A user who picks a
        mode and then starts a different one from the app should see the
        app's choice, because that is what the machine actually did.
        """
        return self._mode_from_last_start() or self._restored

    async def async_select_option(self, option: str) -> None:
        if option not in self.MODES:
            raise ServiceValidationError(f"{option} is not a cleaning mode")
        self._restored = option
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # RESTORED, NOT PERSISTED IN OPTIONS -- writing it into the config
        # entry would reload the integration on every change.
        last = await self.async_get_last_state()
        if last is not None and last.state in self.MODES:
            self._restored = last.state

    @property
    def selected_operating_mode(self) -> int | None:
        """The value to send with a start, or None to leave it alone."""
        option = self.current_option
        return self.MODES.get(option) if option else None


def _robot_sku(config_entry: RoombaConfigEntry) -> str | None:
    """This robot's SKU, for the per-product-mode option lists.

    Read from the unnamed THING shadow, which is where `sku` arrives --
    the same place `_set_robot_profile_on_sku` reads it in __init__.py.
    None when the shadow has not landed, and the caller offers the full
    list in that case."""
    coordinator = config_entry.runtime_data.prime_status_coordinator
    if coordinator is None or coordinator.data is None:
        return None
    for key in ("", "thing"):
        raw = coordinator.data.get(key) if key else coordinator.data.get("state")
        if isinstance(raw, dict):
            reported = raw.get("state", {}).get("reported") if "state" in raw else raw
            if isinstance(reported, dict) and reported.get("sku"):
                return str(reported["sku"])
    profile = getattr(config_entry.runtime_data, "robot_profile", None)
    return str(getattr(profile, "sku", "") or "") or None


def _settings_keys(config_entry: RoombaConfigEntry) -> set[str] | None:
    """The rw-settings keys this robot actually reports, or None.

    THE KEY SET IS THE AUTHORITY ON WHICH CONTROLS EXIST, and no
    capability flag substitutes for it.

    @utkjmitch's Y351020 has an auto-empty dock with a bag in it and
    reports `cap.autoevac = 1`. It has no `autoevacFreq` key, and the
    iRobot app offers him no auto-empty frequency control anywhere. So
    the hardware is present and the setting is not.

    His own reading is the one that fits: the key set tracks what is
    USER-CONFIGURABLE on the SKU, not what is installed. Building a
    control from a capability flag alone would have given him a picker
    for a setting his robot does not have.

    None means the shadow has not arrived yet -- the caller offers
    everything in that case, matching the fail-open rule used for
    capabilities. An empty set on a slow first connection would hide
    every control and read as a broken integration.
    """
    coordinator = config_entry.runtime_data.prime_status_coordinator
    if coordinator is None or coordinator.data is None:
        return None
    raw = coordinator.data.get("rw-settings")
    if not isinstance(raw, dict):
        return None
    reported = raw.get("state", {}).get("reported") if "state" in raw else raw
    if not isinstance(reported, dict):
        return None
    return set(reported)
