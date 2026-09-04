"""The confirmed shape of a schedule-create payload, pinned.

WHERE IT COMES FROM. @utkjmitch created a schedule through the library,
read it back and deleted it -- three full cycles on a real account
(issue #49). This fixture is what crossed the wire, with ids redacted
and nothing else altered.

WHY IT IS PINNED RATHER THAN DESCRIBED. Getting this shape wrong cost
four field rounds against an HTTP 500 with no field named: `initiator`
was ruled out, `is_smart_clean_fav` was ruled out, `created_time` was
dropped, and the actual cause turned out to be a missing `options`
level. A payload that once worked is worth more than any description of
one.

WHAT IT PROVES BEYOND THE ENVELOPE. Per-region `padWetness` is stored,
which was open: this server accepts-and-ignores elsewhere (`schedHold`),
so a 200 proves nothing on its own. He wrote 1 where the global setting
is 3 and the stored per-region value was 2 -- a value distinctive enough
that the read-back cannot be confused with what was already there. That
is the method this project asks for and it was followed without being
asked.
"""

import json
import pathlib

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "schedule_create_confirmed.json"


def _payload() -> dict:
    return json.loads(_FIXTURE.read_text())


class TestOurModelRoundTripsTheConfirmedPayload:
    def test_nothing_is_lost_or_added(self):
        """The one property that matters for building a create service:
        what we parse and re-serialise has to equal what the server
        accepted."""
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        sent = _payload()["sent"]
        assert ScheduleOptions.from_json(sent).to_json() == sent

    def test_the_wrapper_key_and_the_verb_share_a_name(self):
        """A trap worth pinning. `commands` entries arrive wrapped as
        `{"command": {...}}`, and the object inside ALSO has a `command`
        key -- holding the verb `"start"`.

        So after unwrapping, `commands[0]["command"]` still exists and is
        a string. Anything deciding "is this wrapped?" has to check the
        TYPE, not the presence of the key. `_schedule_region_ids` reads
        the wrapper form and the unwrapped form for exactly this reason,
        and got it wrong the first time -- room labels silently fell back
        to bare times for a whole release.
        """
        from roombapy_prime.models.schedules_dnd import ScheduleOptions

        options = ScheduleOptions.from_json(_payload()["sent"])
        inner = options.commands[0]

        assert inner["command"] == "start"
        assert isinstance(options.to_json()["commands"][0]["command"], dict)


class TestWhatTheServerSuppliesItself:
    def test_the_fields_we_must_not_send(self):
        """Each of these was once suspected of causing the 500.
        `created_time` really is server-assigned -- the response carried
        a fresh stamp, not the copied one."""
        assert set(_payload()["server_added"]) == {
            "created_time", "is_smart_clean_fav", "schedule_id"
        }

    def test_the_schedule_id_is_derived_from_the_container(self):
        """Third independent confirmation: container id plus a
        four-character suffix."""
        data = _payload()

        assert data["schedule_id_returned"].startswith(data["container_id"] + "_")


class TestPerRegionWetnessIsStorable:
    """Open until this run. The AutoWash work established that regions
    override the global setting, but not whether a per-region write
    sticks -- and this server accepts-and-ignores in at least one other
    place.
    """

    def _regions(self) -> list[dict]:
        return _payload()["sent"]["commands"][0]["command"]["regions"]

    def test_every_region_carries_its_own_wetness(self):
        regions = self._regions()

        assert len(regions) == 5
        for region in regions:
            assert region["params"]["padWetness"] == {"padPlate": 1}

    def test_the_value_was_chosen_to_be_distinguishable(self):
        """1 against a global of 3 and a stored per-region 2. A round
        number matching what was already there would have proved
        nothing."""
        assert {r["params"]["padWetness"]["padPlate"] for r in self._regions()} == {1}

    def test_the_region_parameter_set(self):
        """Four keys, and this is the full confirmed set -- anything a
        service offers beyond them would be invented."""
        for region in self._regions():
            assert set(region["params"]) == {
                "operatingMode", "suctionLevel", "twoPass", "padWetness"
            }


class TestTheCommandLevelFields:
    def _command(self) -> dict:
        return _payload()["sent"]["commands"][0]["command"]

    def test_only_routine_modified_is_passed_as_a_command_param(self):
        """Matches the app's own `onlyUserModifiableParams()`, which
        keeps exactly this one key. Everything else about the job lives
        on the regions."""
        assert self._command()["params"] == {"routine_modified": True}

    def test_the_map_is_named_twice_and_both_are_needed(self):
        command = self._command()

        assert command["p2map_id"]
        assert command["user_p2mapv_id"]

    def test_select_all_is_false_when_regions_are_listed(self):
        command = self._command()

        assert command["select_all"] is False
        assert command["regions"]
