"""Tests for vendor_errors.py."""

from __future__ import annotations
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
import pytest


# ── formerly tests/test_coverage_small_gaps.py ──────────────────────────────────
#
# Small gaps in eight modules — quality scale, test-coverage (Silver).
#
# Mostly error branches and edge cases: a malformed input must not crash,
# must not return something wrong, and where the code logs, it must log.
# Each test pins what the branch is for, not only that it ran.

class TestVendorErrorLookup:

    def test_a_non_numeric_code_is_no_text(self):
        from custom_components.roomba_plus.vendor_errors import vendor_error

        assert vendor_error("abc") is None
        assert vendor_error(None) is None

    def test_a_code_without_any_language_entry_is_no_text(self, monkeypatch):
        from custom_components.roomba_plus import vendor_errors

        monkeypatch.setitem(vendor_errors.VENDOR_ERROR_TEXTS, 999999, {})
        assert vendor_errors.vendor_error(999999, "de") is None
