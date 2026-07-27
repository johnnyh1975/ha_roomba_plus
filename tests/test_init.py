

class TestUnknownSkuIsReportedAfterSetup:
    """An unrecognised SKU produces a report request AFTER the account
    setup succeeds, not at discovery time.

    The timing is the whole point. At discovery all that exists is an
    SKU string, and whether the robot is really Prime is a guess. By the
    time setup has succeeded it is a fact -- account-based setup only
    works for a Prime-generation robot -- and a diagnostics download
    exists carrying the capability flags and shadow structure.

    That is what makes a new model worth reporting. Every capability gap
    this project has found came from exactly that data, and each
    surfaced only because a tester pasted raw output nobody had thought
    to ask for.

    Asking earlier would also have asked more people: an unrecognised
    SKU at discovery is sometimes just a Classic robot the table missed."""

    def _warn_for(self, sku):
        from unittest.mock import patch

        from custom_components.roomba_plus import _async_note_unknown_sku

        with patch("custom_components.roomba_plus._LOGGER") as logger:
            _async_note_unknown_sku(sku, "BLID123")
        return logger.warning

    def test_a_known_prime_sku_produces_no_request(self):
        """The overwhelmingly common case. A warning here would train
        users to ignore the log, and the real one would go unread."""
        for sku in ("G185020", "N185240", "Y414040"):
            self._warn_for(sku).assert_not_called()

    def test_an_unknown_sku_produces_one(self):
        assert self._warn_for("Z995020").called

    def test_a_missing_sku_produces_one(self):
        """Absent is not the same as recognised."""
        assert self._warn_for(None).called

    def test_the_message_makes_clear_nothing_is_broken(self):
        """The robot works. Without saying so, an unexplained warning
        reads as a fault the user is expected to fix."""
        message = self._warn_for("Z995020").call_args.args[0]

        assert "Everything should work" in message

    def test_the_message_asks_for_the_diagnostics_download(self):
        """The reason for reporting at this moment rather than earlier:
        the file exists now, and it holds the capability data."""
        message = self._warn_for("Z995020").call_args.args[0]

        assert "diagnostics" in message.lower()

    def test_the_url_carries_the_sku_but_never_the_blid(self):
        """An SKU identifies a product. A BLID identifies one specific
        robot, adds nothing to classifying a model, and belongs to the
        user rather than a public issue."""
        from urllib.parse import parse_qs, urlparse

        from custom_components.roomba_plus import _unknown_sku_issue_url

        query = parse_qs(urlparse(_unknown_sku_issue_url("Z995020")).query)

        assert "Z995020" in query["title"][0]
        assert "Z995020" in query["body"][0]
        assert "BLID123" not in query["body"][0]

    def test_the_url_points_at_the_real_tracker(self):
        from urllib.parse import urlparse

        from custom_components.roomba_plus import _unknown_sku_issue_url
        from custom_components.roomba_plus.const import ISSUE_TRACKER_URL

        parsed = urlparse(_unknown_sku_issue_url("Z995020"))

        assert parsed.scheme == "https"
        assert ISSUE_TRACKER_URL.startswith(f"https://{parsed.netloc}")
        assert parsed.path.endswith("/issues/new")
