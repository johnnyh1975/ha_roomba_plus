"""Unit tests for the SmartZoneNamingRepairFlow zone name parser.

Covers both input styles supported after v1.4.4.9:
  - Newline-separated  (canonical, one entry per line)
  - Comma-separated    (fallback for when the textarea collapses to one line)

Also tests edge cases: mixed whitespace, duplicate IDs, names containing
commas, names containing equals signs, unknown IDs, and empty input.

No HA or roombapy installation required — uses the stubs from conftest.py.
"""
from __future__ import annotations

import sys
import os
import re
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


# ── Extract the parser logic as a standalone helper for unit testing ──────────
# Rather than instantiating the full RepairsFlow (which needs HA internals),
# we extract the exact parsing algorithm from repairs.py into a function that
# takes (raw, unlabelled) and returns the parsed dict.  Any change to the
# production parser must be reflected here.

def _parse_zone_input(raw: str, unlabelled: list[str]) -> dict[str, str]:
    """Mirror of the parser in SmartZoneNamingRepairFlow.async_step_init."""
    parsed: dict[str, str] = {}
    raw = raw.strip()
    if not raw:
        return parsed

    _comma_delim = re.compile(r",\s*\d")
    if _comma_delim.search(raw):
        tokens = re.split(r",(?=\s*\d)", raw)
    else:
        tokens = raw.splitlines()

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            continue
        rid_part, _, name_part = token.partition("=")
        rid = rid_part.strip()
        name = name_part.strip()
        if rid in unlabelled and name:
            parsed[rid] = name

    return parsed


# ── Shared fixtures ───────────────────────────────────────────────────────────

UNLABELLED = ["1", "4", "17", "19", "23", "25", "26"]


# ── Newline-separated input ───────────────────────────────────────────────────

class TestNewlineSeparated:
    """Canonical format: one id=Name per line."""

    def test_single_zone(self):
        raw = "1=Cucina"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Cucina"}

    def test_all_zones(self):
        raw = (
            "1=Cucina\n"
            "17=CabinaArmadio\n"
            "19=Bagno\n"
            "23=BagnoStudio\n"
            "26=Soggiorno\n"
            "4=Studio\n"
            "25=Camera"
        )
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {
            "1": "Cucina",
            "17": "CabinaArmadio",
            "19": "Bagno",
            "23": "BagnoStudio",
            "26": "Soggiorno",
            "4": "Studio",
            "25": "Camera",
        }

    def test_trailing_newline_ignored(self):
        raw = "1=Cucina\n17=Armadio\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert len(result) == 2

    def test_blank_lines_skipped(self):
        raw = "1=Cucina\n\n17=Armadio\n\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_leading_trailing_whitespace_stripped(self):
        raw = "  1 = Cucina  \n  17 = Armadio  "
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_empty_name_after_equals_skipped(self):
        """A zone with id= but no name is silently skipped."""
        raw = "1=\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" not in result
        assert result["17"] == "Armadio"

    def test_unknown_id_ignored(self):
        """An id not in unlabelled is silently dropped."""
        raw = "1=Cucina\n99=Unknown"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "99" not in result
        assert result["1"] == "Cucina"

    def test_name_with_spaces(self):
        raw = "1=Camera da Letto"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Camera da Letto"}

    def test_name_with_equals_sign(self):
        """Only the first '=' is treated as the delimiter."""
        raw = "1=Room=A"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Room=A"

    def test_malformed_line_no_equals_skipped(self):
        raw = "1=Cucina\nJustText\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" in result
        assert "17" in result
        assert len(result) == 2


# ── Comma-separated input ─────────────────────────────────────────────────────

class TestCommaSeparated:
    """Fallback format: id=Name,id=Name on a single line."""

    def test_single_zone(self):
        raw = "1=Cucina"
        assert _parse_zone_input(raw, UNLABELLED) == {"1": "Cucina"}

    def test_all_zones_comma_separated(self):
        """This is the exact input from the bug report."""
        raw = "1=Cucina,17=Cabina_Armadio,19=Bagno,23=Bagno_Studio,26=Soggiorno,4=Studio,25=Camera_da_Letto"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {
            "1": "Cucina",
            "17": "Cabina_Armadio",
            "19": "Bagno",
            "23": "Bagno_Studio",
            "26": "Soggiorno",
            "4": "Studio",
            "25": "Camera_da_Letto",
        }

    def test_spaces_around_commas(self):
        raw = "1=Cucina, 17=Armadio, 19=Bagno"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}

    def test_unknown_id_in_comma_list_ignored(self):
        raw = "1=Cucina,99=Unknown,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "99" not in result
        assert result["1"] == "Cucina"
        assert result["17"] == "Armadio"

    def test_empty_name_in_comma_list_skipped(self):
        raw = "1=,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert "1" not in result
        assert result["17"] == "Armadio"

    def test_name_with_spaces_comma_list(self):
        raw = "1=Camera da Letto,17=Sala da Pranzo"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Camera da Letto"
        assert result["17"] == "Sala da Pranzo"

    def test_two_zones_no_trailing_comma(self):
        raw = "1=Cucina,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert len(result) == 2


# ── Ambiguous / mixed input ───────────────────────────────────────────────────

class TestAmbiguousInput:
    """Cases where comma-in-name vs comma-as-delimiter could be confused."""

    def test_name_containing_comma_without_digit_after(self):
        """'1=Living room, open plan' — comma not followed by digit, so no split."""
        raw = "1=Living room, open plan\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Living room, open plan"
        assert result["17"] == "Armadio"

    def test_comma_followed_by_non_digit_treated_as_name(self):
        """Comma not followed by a digit should not trigger comma-splitting."""
        raw = "1=Office, West wing\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Office, West wing"

    def test_comma_delimiter_detected_by_digit_lookahead(self):
        """Comma followed immediately by a digit is the delimiter signal."""
        raw = "1=Cucina,17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        # Correctly split into two entries
        assert result == {"1": "Cucina", "17": "Armadio"}

    def test_mixed_newline_and_comma_newline_wins(self):
        """If there are real newlines but no comma-before-digit, use newlines."""
        raw = "1=Cucina\n17=Armadio"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "17": "Armadio"}


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Boundary and error-tolerance tests."""

    def test_empty_input_returns_empty(self):
        assert _parse_zone_input("", UNLABELLED) == {}

    def test_whitespace_only_returns_empty(self):
        assert _parse_zone_input("   \n  \n  ", UNLABELLED) == {}

    def test_all_ids_left_blank(self):
        """Pre-filled default text with no names filled in."""
        raw = "1=\n17=\n19=\n23=\n25=\n26=\n4="
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {}

    def test_partial_fill(self):
        """Only some zones named — the rest are left blank."""
        raw = "1=Cucina\n17=\n19=Bagno"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina", "19": "Bagno"}
        assert "17" not in result

    def test_duplicate_id_last_value_wins(self):
        """If the same id appears twice, the last non-empty name wins."""
        raw = "1=First\n1=Second"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Second"

    def test_single_unlabelled(self):
        result = _parse_zone_input("5=Kitchen", ["5"])
        assert result == {"5": "Kitchen"}

    def test_unicode_names(self):
        raw = "1=Küche\n17=Wohnzimmer\n19=Schlafzimmer"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result["1"] == "Küche"

    def test_default_prefill_newline_format_parses_correctly(self):
        """Simulate the pre-filled textarea after the user fills in names."""
        unlabelled = ["1", "17", "19"]
        prefilled = "\n".join(f"{rid}=" for rid in unlabelled)
        # User fills in names:
        filled = prefilled.replace("1=", "1=Cucina").replace("17=", "17=Armadio").replace("19=", "19=Bagno")
        result = _parse_zone_input(filled, unlabelled)
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}


# ── Regression: the exact bug report scenario ─────────────────────────────────

class TestBugReportScenario:
    """Exact inputs from the bug report must now parse correctly."""

    def test_bug_report_comma_input(self):
        """v1.4.4.9 incorrectly stored the entire value under zone 1.
        This must now produce seven separate zone entries.
        """
        raw = "1=Cucina,17=Cabina_Armadio,19=Bagno,23=Bagno_Studio,26=Soggiorno,4=Studio,25=Camera_da_Letto"
        unlabelled = ["1", "17", "19", "23", "26", "4", "25"]
        result = _parse_zone_input(raw, unlabelled)

        # Must NOT store everything under zone 1
        assert result.get("1") == "Cucina", (
            "Zone 1 must be 'Cucina', not the entire comma-separated string"
        )
        # Must produce all 7 zones
        assert len(result) == 7

    def test_original_single_zone_still_works(self):
        """The fix that landed in v1.4.4.9 (single zone, newline) must still work."""
        raw = "1=Cucina\n"
        result = _parse_zone_input(raw, UNLABELLED)
        assert result == {"1": "Cucina"}

    def test_concatenated_prefill_was_the_root_cause(self):
        """When all IDs appear on one line with no names (the buggy pre-fill),
        entering comma-separated values should now parse correctly.
        """
        # Buggy pre-fill rendered as: "1=17=19=23=26=4=25="
        # User then types comma-separated names for everything:
        raw = "1=Cucina,17=Armadio,19=Bagno"
        result = _parse_zone_input(raw, ["1", "17", "19"])
        assert result == {"1": "Cucina", "17": "Armadio", "19": "Bagno"}
