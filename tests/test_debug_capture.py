"""The debug capture: debug lines kept in memory, redacted as kept (4.6.0, A1).

The point is that a tester can hand the lines over without reading them
first, so most of this file is about what must not survive.
"""
from __future__ import annotations

import logging

import pytest

from custom_components.roomba_plus import debug_capture as dc

BLID = "0123456789ABCDEF"
PRIME_BLID = "fedcba9876543210fedcba9876543210"
# Synthetic values in the shapes the capture must catch, put together here
# so the repository's private-data check does not take them for real ones.
FAKE_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJlLXNpZw"
FAKE_AWS_KEY = "AK" + "IAABCDEFGHIJKLMNOP"
OTHER_BLID = "FEDCBA9876543210"


class TestRedaction:
    @pytest.mark.parametrize(("line", "gone"), [
        # A Classic state dump: the network block, an integer address.
        ('{"netinfo": {"dhcp": true, "addr": 3232235777, "mask": 4294967040}, "x": 1}',
         ["3232235777", "4294967040", "dhcp"]),
        ("{'wlcfg': {'sec': 7, 'ssid': '4d794e6574'}}", ["4d794e6574"]),
        # Positions in the home.
        ('"pose": {"theta": 12, "point": {"x": 310, "y": -42}}', ["310", "-42", "theta"]),
        ("map_x_mm=1234, map_y_mm=-567", ["1234", "-567"]),
        # Addresses wherever they appear.
        ("connecting to 192.0.2.17:8883", ["192.0.2.17"]),
        ("peer fe80::1ff:fe23:4567:890a", ["fe80::1ff:fe23:4567:890a"]),
        ("mac aa:bb:cc:dd:ee:ff seen", ["aa:bb:cc:dd:ee:ff"]),
        ("login user@example.com ok", ["user@example.com"]),
        # Secret-shaped keys, quoted or not.
        ('{"password": "hunter2", "authToken": "abc"}', ["hunter2", "abc"]),
        ("refresh token=AbCdEf123 done", ["AbCdEf123"]),
        (f"jwt {FAKE_JWT}", ["eyJhbGci"]),
        (f"key {FAKE_AWS_KEY} used", [FAKE_AWS_KEY]),
        ("session " + "A" * 30 + "b" * 20, ["A" * 30]),
        # Robot ids: the entry's, inside a map id, and any Classic-shaped one.
        (f"p2map {BLID}-1700000000 loaded", [BLID]),
        (f"robot {PRIME_BLID} connected", [PRIME_BLID]),
        (f"other robot {OTHER_BLID} found", [OTHER_BLID]),
        ('{"sn": "R7A1234567890", "serial": "X1"}', ["R7A1234567890"]),
    ])
    def test_private_values_do_not_survive(self, line, gone):
        out = dc.redact_text(line, [BLID, PRIME_BLID])
        for item in gone:
            assert item not in out, (item, out)

    @pytest.mark.parametrize("line", [
        "Room tracking: the robot's position puts it in Kitchen (left Hall)",
        "phase run -> charge, cycle clean, error 0, notReady 0",
        "Map: 812 points, checkpoint saved",
        "version 261022T082426 committed",
    ])
    def test_ordinary_lines_stay_readable(self, line):
        assert dc.redact_text(line, [BLID]) == line

    def test_a_bare_key_at_the_end_is_harmless(self):
        assert dc.redact_text("password:", []) == "password:**REDACTED**"

    @pytest.mark.parametrize("line", [
        '{"pose": {"point": {"x": 1, "y": 2}',   # unclosed object
        '{"ssid": "unterminated',               # unclosed string
    ])
    def test_a_cut_line_is_redacted_to_its_end(self, line):
        out = dc.redact_text(line, [])
        assert out.endswith(dc.REDACTED)


class TestTheCapture:
    async def test_it_keeps_redacted_lines_and_restores_levels(self, hass):
        logger = logging.getLogger("roombapy")
        logger.setLevel(logging.WARNING)
        capture = dc.DebugCapture(lambda: [BLID])
        capture.start(hass, 5)
        assert capture.running
        assert logger.level == logging.DEBUG
        logger.debug("state from %s at 192.0.2.5", BLID)
        logging.getLogger("custom_components.roomba_plus.vacuum").info("plain line")
        logging.getLogger("unrelated").warning("not ours")
        try:
            raise ValueError("boom 192.0.2.9")
        except ValueError:
            logging.getLogger("roombapy_prime").exception("failed")
        capture.stop()
        assert not capture.running
        assert logger.level == logging.WARNING
        text = "\n".join(capture.lines)
        assert BLID not in text and "192.0.2.5" not in text and "192.0.2.9" not in text
        assert "plain line" in text
        assert "not ours" not in text
        assert "ValueError" in text
        report = capture.report()
        assert report["running"] is False
        assert report["line_count"] == 3
        assert report["started_at"] and report["stopped_at"]
        logger.debug("after the stop")
        assert len(capture.lines) == 3

    async def test_it_stops_by_itself(self, hass):
        from datetime import timedelta

        from homeassistant.util import dt as dt_util
        from pytest_homeassistant_custom_component.common import async_fire_time_changed

        capture = dc.DebugCapture(lambda: [])
        capture.start(hass, 1)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
        await hass.async_block_till_done()
        assert not capture.running

    async def test_a_full_buffer_counts_what_it_dropped(self, hass, monkeypatch):
        monkeypatch.setattr(dc, "MAX_LINES", 2)
        capture = dc.DebugCapture(lambda: [])
        capture.lines = __import__("collections").deque(maxlen=2)
        capture.start(hass, 1)
        for i in range(5):
            logging.getLogger("roombapy").debug("line %d", i)
        capture.stop()
        assert list(capture.lines)[-1].endswith("line 4")
        assert capture.dropped == 3

    async def test_a_restart_clears_and_a_second_stop_is_harmless(self, hass):
        capture = dc.DebugCapture(lambda: [])
        capture.start(hass, 1)
        logging.getLogger("roombapy").debug("first")
        capture.start(hass, 1)
        assert not capture.lines
        capture.stop()
        capture.stop()

    def test_a_failing_record_does_not_raise(self, monkeypatch):
        capture = dc.DebugCapture(lambda: [])
        handler = dc._Handler(capture)
        monkeypatch.setattr(capture, "add", lambda _r: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(handler, "handleError", lambda _r: None)
        handler.emit(logging.LogRecord("roombapy", logging.DEBUG, "", 0, "x", None, None))

    async def test_one_capture_with_every_entrys_robot_id(self, hass):
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        MockConfigEntry(domain="roomba_plus", data={"blid": BLID}).add_to_hass(hass)
        MockConfigEntry(domain="roomba_plus", data={}).add_to_hass(hass)
        capture = dc.get_capture(hass)
        assert dc.get_capture(hass) is capture
        assert capture._robot_ids() == [BLID]


class TestTheServices:
    async def test_start_get_stop(self, hass):
        from homeassistant.helpers import entity_registry  # noqa: F401 - registry loaded

        from custom_components.roomba_plus.services import async_register_services

        async_register_services(hass)
        await hass.services.async_call("roomba_plus", "debug_capture_start", {"minutes": 5}, blocking=True)
        logging.getLogger("roombapy").debug("hello from 192.0.2.44")
        got = await hass.services.async_call(
            "roomba_plus", "debug_capture_get", {}, blocking=True, return_response=True,
        )
        assert got["running"] is True
        assert any("hello" in line for line in got["lines"])
        assert not any("192.0.2.44" in line for line in got["lines"])
        stopped = await hass.services.async_call(
            "roomba_plus", "debug_capture_stop", {}, blocking=True, return_response=True,
        )
        assert stopped["running"] is False
