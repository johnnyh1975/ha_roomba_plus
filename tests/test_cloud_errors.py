"""Cloud errors reach the user by reason, in their language (4.3).

Until 4.2 every translated cloud message had an `{error}` slot filled
with the library's English text, so a German user read German around an
English sentence -- and the English part was the one that said what to
do. roombapy-prime names the cause (CloudError.reason); cloud_errors.py
turns it into one translation key per reason.

These tests hold the TEXTS to the library's closed set of reasons: a
reason added upstream without a text here must fail the build, not
reach a user as a bare key.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

import aiohttp
import pytest
from roombapy_prime import (
    AuthCredentialsError,
    CloudError,
    CloudErrorReason,
    RestClientError,
    RestServerError,
)

from custom_components.roomba_plus.cloud_errors import (
    FLOW_REASONS,
    MQTT_REASONS,
    flow_error,
    reason_of,
    translation_key,
    translation_placeholders,
)

PKG = pathlib.Path("custom_components/roomba_plus")
LANGS = ["de", "en", "es", "fr", "it", "nl", "pl", "pt"]


def _load(lang: str | None) -> dict:
    path = PKG / "strings.json" if lang is None else PKG / "translations" / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


@pytest.mark.parametrize("lang", [None, *LANGS])
def test_every_reason_has_an_exception_text(lang):
    exceptions = _load(lang)["exceptions"]
    missing = [r.value for r in CloudErrorReason if f"cloud_{r.value}" not in exceptions]
    assert not missing, (lang, missing)


@pytest.mark.parametrize("lang", [None, *LANGS])
@pytest.mark.parametrize("section", ["config", "options"])
def test_every_login_reason_has_a_form_text_and_no_mqtt_one(lang, section):
    errors = _load(lang)[section]["error"]
    missing = [r.value for r in FLOW_REASONS if f"cloud_{r.value}" not in errors]
    extra = [r.value for r in MQTT_REASONS if f"cloud_{r.value}" in errors]
    assert not missing, (lang, section, missing)
    assert not extra, (lang, section, extra)


@pytest.mark.parametrize("lang", [None, *LANGS])
@pytest.mark.parametrize("section", ["config", "options"])
def test_form_texts_need_no_placeholder(lang, section):
    """A form error cannot take placeholders the form does not pass, and
    no login form passes a status."""
    errors = _load(lang)[section]["error"]
    with_ph = {k: v for k, v in errors.items() if k.startswith("cloud_") and _placeholders(v)}
    assert not with_ph, (lang, section, with_ph)


@pytest.mark.parametrize("reason", list(CloudErrorReason))
def test_the_placeholders_given_are_exactly_the_ones_the_text_needs(reason):
    """For every reason, what translation_placeholders() hands over and
    what the English text asks for are the same set -- the other
    languages are held to English by test_translations."""
    exc = CloudError("x", reason=reason)
    text = _load(None)["exceptions"][translation_key(exc)]["message"]
    assert set(translation_placeholders(exc)) == _placeholders(text)


class TestReasonOf:

    def test_a_cloud_error_gives_its_reason(self):
        locked = AuthCredentialsError("x", reason=CloudErrorReason.ACCOUNT_LOCKED)
        assert reason_of(locked) is CloudErrorReason.ACCOUNT_LOCKED
        assert translation_key(locked) == "cloud_account_locked"

    def test_an_http_error_gives_the_reason_of_its_status(self):
        assert translation_key(RestServerError("x", 503)) == "cloud_server_error"
        assert translation_key(RestClientError("x", 404)) == "cloud_request_refused"

    def test_no_text_names_the_status(self):
        """A login error carries the reason of a status but not the
        status: a login refused with a 500 would have read "HTTP ?" in
        the message shown at setup."""
        from roombapy_prime import AuthError
        from roombapy_prime.errors import reason_for_status

        login_500 = AuthError("discovery failed", reason=reason_for_status(500))
        assert not hasattr(login_500, "status")
        assert translation_key(login_500) == "cloud_server_error"
        assert translation_placeholders(login_500) == {}
        for lang in [None, *LANGS]:
            texts = _load(lang)["exceptions"]
            assert not [k for k, v in texts.items()
                        if k.startswith("cloud_") and "{status}" in v["message"]], lang

    def test_the_callers_own_timeout_is_a_timeout(self):
        """The coordinator wraps its fetches in asyncio.timeout(30); that
        TimeoutError is not the library's, and still has an obvious
        reason."""
        assert reason_of(TimeoutError()) is CloudErrorReason.TIMEOUT

    def test_a_bare_aiohttp_error_is_a_failed_connection(self):
        assert reason_of(aiohttp.ClientError()) is CloudErrorReason.CONNECTION_FAILED

    def test_anything_else_is_unknown(self):
        assert reason_of(ValueError()) is CloudErrorReason.UNKNOWN

    def test_a_reason_from_a_newer_library_is_unknown_not_a_crash(self):
        exc = CloudError("x")
        exc.reason = "from_the_future"  # type: ignore[assignment]
        assert translation_key(exc) == "cloud_unknown"

    def test_a_form_never_gets_an_mqtt_key(self):
        exc = CloudError("x", reason=CloudErrorReason.SHADOW_REJECTED)
        assert flow_error(exc) == "cloud_unknown"
        assert flow_error(AuthCredentialsError("x")) == "cloud_credentials_rejected"


def _dynamic_raises():
    """Every raise whose translation_key is chosen at runtime."""
    for path in sorted(PKG.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            kws = {k.arg: k.value for k in node.exc.keywords}
            key = kws.get("translation_key")
            if key is not None and not isinstance(key, ast.Constant):
                yield path.name, node.lineno, kws


def test_every_runtime_key_comes_from_cloud_errors_with_its_placeholders():
    """test_translations checks a constant key against its placeholders.
    A key chosen at runtime escapes that check, so this one holds every
    such raise to the pair that belongs together: the key AND the
    placeholders from cloud_errors, for the same exception."""
    raises = list(_dynamic_raises())
    assert raises, "none found -- the check would pass vacuously"
    wrong = []
    for name, line, kws in raises:
        key, ph = kws["translation_key"], kws.get("translation_placeholders")
        if not (
            isinstance(key, ast.Call)
            and ast.unparse(key.func) == "cloud_errors.translation_key"
            and isinstance(ph, ast.Call)
            and ast.unparse(ph.func) == "cloud_errors.translation_placeholders"
            and ast.unparse(key.args[0]) == ast.unparse(ph.args[0])
        ):
            wrong.append(f"{name}:{line}")
    assert not wrong, wrong


def test_the_login_and_connect_failures_are_all_translated_by_reason():
    """The sites that show on the integration card. Each used to pass
    the English text as `{error}`."""
    sites = {(name, ast.unparse(kws["translation_key"].args[0])) for name, _l, kws in _dynamic_raises()}
    assert ("__init__.py", "exc") in sites            # Prime login
    assert ("prime_coordinator.py", "exc") in sites   # MQTT connect
    assert ("prime_coordinator.py", "last_exc") in sites  # named shadows
    assert ("cloud_coordinator.py", "exc") in sites   # Classic login and refresh


def test_no_cloud_message_carries_the_english_error_any_more():
    """The old `{error}` slot is what put English into every language."""
    exceptions = _load(None)["exceptions"]
    cloud = {f"cloud_{r.value}": exceptions[f"cloud_{r.value}"]["message"] for r in CloudErrorReason}
    assert not [k for k, v in cloud.items() if "{error}" in v]


def test_mqtt_reasons_are_the_ones_only_mqtt_raises():
    """MQTT_REASONS is hand-written, and it decides which reasons the
    login forms leave out. Read from the library's own source: a reason
    raised by the MQTT client but by neither login nor REST belongs in
    it, and one that login or REST can raise must not be in it -- the
    form would then show `cloud_unknown` for a cause it has a text for."""
    import roombapy_prime

    lib = pathlib.Path(roombapy_prime.__file__).parent

    def raised(module: str) -> set[CloudErrorReason]:
        text = (lib / module).read_text(encoding="utf-8")
        return {CloudErrorReason[m] for m in re.findall(r"CloudErrorReason\.([A-Z_]+)", text)}

    only_mqtt = raised("mqtt_client.py") - raised("auth.py") - raised("rest_client.py")
    assert only_mqtt, "nothing found -- the check would pass vacuously"
    assert only_mqtt <= MQTT_REASONS, only_mqtt - MQTT_REASONS
    assert not (raised("auth.py") | raised("rest_client.py")) & MQTT_REASONS
    assert FLOW_REASONS | MQTT_REASONS == frozenset(CloudErrorReason)


class TestTheReasonAsASentence:
    """The part-reset message needs its own words AND the reason. A
    translation cannot contain another, so the reason arrives as a
    finished sentence in `{reason}` -- until 4.3 it was the exception's
    class name, in every language."""

    @pytest.mark.parametrize("lang,expected", [
        ("en", "iRobot's cloud has a problem of its own."),
        ("de", "Die iRobot-Cloud hat ein eigenes Problem."),
    ])
    @pytest.mark.asyncio
    async def test_it_is_in_home_assistants_language(self, hass, enable_custom_integrations, lang, expected):
        from custom_components.roomba_plus.cloud_errors import async_reason_text

        hass.config.language = lang
        text = await async_reason_text(hass, RestServerError("x", 503))
        assert text.startswith(expected)

    @pytest.mark.asyncio
    async def test_without_texts_it_is_the_reasons_name_not_an_error(self, hass):
        from unittest.mock import patch

        from custom_components.roomba_plus import cloud_errors

        with patch.object(cloud_errors, "async_get_translations", side_effect=RuntimeError("x")):
            text = await cloud_errors.async_reason_text(hass, RestServerError("x", 503))
        assert text == "server_error"

    def test_no_message_names_an_exception_class_any_more(self):
        texts = _load(None)["exceptions"]
        assert "{error}" not in texts["cloud_part_reset_failed"]["message"]
        assert "{reason}" in texts["cloud_part_reset_failed"]["message"]
