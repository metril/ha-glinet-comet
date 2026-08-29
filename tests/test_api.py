"""Tests for the GL.iNet Comet API client (login, auth retry, errors)."""

from __future__ import annotations

import json
import os
import sys
import traceback
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiohttp
import pytest

from custom_components.glinet_comet.api import (
    CometApiClient,
    CometApiError,
    CometAuthError,
    CometConnectionError,
    CometRateLimitError,
)


class _FakeResp:
    """Fake aiohttp response usable as an async context manager."""

    def __init__(self, status: int, payload=None, *, is_json: bool = True):
        self.status = status
        self._payload = payload
        self._is_json = is_json
        self.headers: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if not self._is_json:
            raise ValueError("not JSON")
        return self._payload

    async def text(self):
        if self._is_json:
            return json.dumps(self._payload)
        return self._payload

    async def read(self):
        if isinstance(self._payload, bytes):
            return self._payload
        if self._is_json:
            return json.dumps(self._payload).encode()
        return str(self._payload).encode()


def _ok(result: dict) -> _FakeResp:
    return _FakeResp(200, {"ok": True, "result": result})


def _login_ok(token: str = "TOK1") -> _FakeResp:
    return _ok({"token": token})


class _FakeSession:
    """Records every request/ws_connect call and returns scripted responses."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.requests: list[tuple[str, str, dict]] = []
        self._ws_responses: list = []
        self.ws_calls: list[tuple[str, dict]] = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def queue_ws(self, item) -> None:
        self._ws_responses.append(item)

    async def ws_connect(self, url, **kwargs):
        self.ws_calls.append((url, kwargs))
        item = self._ws_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(session, **kwargs) -> CometApiClient:
    kwargs.setdefault("verify_ssl", False)
    return CometApiClient(session, "10.0.0.5", "admin", "hunter2", **kwargs)


# --- Login form / TOTP ---


@pytest.mark.asyncio
async def test_login_appends_fresh_totp_code(monkeypatch):
    monkeypatch.setattr("pyotp.TOTP.now", lambda self: "123456")
    session = _FakeSession([_login_ok()])
    client = _client(session, totp_secret="JBSWY3DPEHPK3PXP")

    await client.async_login()

    method, url, kwargs = session.requests[0]
    assert method == "POST"
    assert url == "https://10.0.0.5/api/auth/login"
    assert kwargs["data"] == {"user": "admin", "passwd": "hunter2123456", "expire": "0"}


@pytest.mark.asyncio
async def test_login_without_totp_secret_sends_plain_password():
    session = _FakeSession([_login_ok()])
    client = _client(session, totp_secret="")

    await client.async_login()

    _, _, kwargs = session.requests[0]
    assert kwargs["data"]["passwd"] == "hunter2"


@pytest.mark.asyncio
async def test_login_computes_fresh_code_every_call(monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr("pyotp.TOTP.now", lambda self: next(codes))
    session = _FakeSession([_login_ok("A"), _login_ok("B")])
    client = _client(session, totp_secret="JBSWY3DPEHPK3PXP")

    await client.async_login()
    await client.async_login()

    assert session.requests[0][2]["data"]["passwd"] == "hunter2111111"
    assert session.requests[1][2]["data"]["passwd"] == "hunter2222222"


# --- Envelope / status-code error handling ---


@pytest.mark.asyncio
async def test_200_ok_false_raises_api_error():
    session = _FakeSession(
        [
            _login_ok(),
            _FakeResp(
                200,
                {"ok": False, "result": {"error": "Busy", "error_msg": "device busy"}},
            ),
        ]
    )
    client = _client(session)

    with pytest.raises(CometApiError, match="Busy: device busy"):
        await client.get_atx()


@pytest.mark.asyncio
async def test_500_plain_text_raises_api_error_without_json_decode_error():
    session = _FakeSession(
        [_login_ok(), _FakeResp(500, "internal server error", is_json=False)]
    )
    client = _client(session)

    with pytest.raises(CometApiError):
        await client.atx_click("power")


@pytest.mark.asyncio
async def test_two_step_required_raises_auth_error():
    session = _FakeSession(
        [_ok({"token": "T", "two_step_required": True})]
    )
    client = _client(session)

    with pytest.raises(CometAuthError, match="two-step"):
        await client.async_login()


@pytest.mark.asyncio
async def test_login_403_raises_auth_error():
    session = _FakeSession([_FakeResp(403, {"ok": False, "result": {}})])
    client = _client(session)

    with pytest.raises(CometAuthError):
        await client.async_login()


@pytest.mark.asyncio
async def test_login_429_raises_rate_limit_error_with_remaining_time():
    session = _FakeSession(
        [_FakeResp(429, {"ok": False, "result": {"remaining_time": 42}})]
    )
    client = _client(session)

    with pytest.raises(CometRateLimitError) as exc_info:
        await client.async_login()
    assert exc_info.value.remaining_time == 42
    # No retry: exactly one request was made.
    assert len(session.requests) == 1


# --- 401/403 re-login-once-and-retry ---


@pytest.mark.asyncio
async def test_401_triggers_one_relogin_and_retry_then_succeeds():
    session = _FakeSession(
        [
            _login_ok("TOK1"),
            _FakeResp(401, {"ok": False, "result": {}}),
            _login_ok("TOK2"),
            _ok({"connected": True}),
        ]
    )
    client = _client(session)

    result = await client.get_hid()

    assert result == {"connected": True}
    # First auth call used the stale token, retry used the refreshed one.
    assert session.requests[1][2]["headers"]["Token"] == "TOK1"
    assert session.requests[3][2]["headers"]["Token"] == "TOK2"


@pytest.mark.asyncio
async def test_second_401_after_retry_raises_auth_error():
    session = _FakeSession(
        [
            _login_ok("TOK1"),
            _FakeResp(401, {"ok": False, "result": {}}),
            _login_ok("TOK2"),
            _FakeResp(403, {"ok": False, "result": {}}),
        ]
    )
    client = _client(session)

    with pytest.raises(CometAuthError):
        await client.get_hid()


@pytest.mark.asyncio
async def test_rate_limit_during_relogin_propagates_untouched():
    session = _FakeSession(
        [
            _login_ok("TOK1"),
            _FakeResp(401, {"ok": False, "result": {}}),
            _FakeResp(429, {"ok": False, "result": {"remaining_time": 99}}),
        ]
    )
    client = _client(session)

    with pytest.raises(CometRateLimitError) as exc_info:
        await client.get_hid()
    assert exc_info.value.remaining_time == 99


# --- Token reuse across concurrent re-auth (lock semantics) ---


@pytest.mark.asyncio
async def test_reauth_reuses_token_refreshed_by_another_caller():
    session = _FakeSession([_login_ok("SHOULD_NOT_BE_USED")])
    client = _client(session)
    client._token = "NEW"  # another coroutine already refreshed it

    token = await client._reauth("OLD")

    assert token == "NEW"
    assert session.requests == []  # no login request was made


@pytest.mark.asyncio
async def test_reauth_logs_in_when_token_still_stale():
    session = _FakeSession([_login_ok("FRESH")])
    client = _client(session)
    client._token = "OLD"

    token = await client._reauth("OLD")

    assert token == "FRESH"
    assert len(session.requests) == 1


# --- Upgrade actions ---


@pytest.mark.asyncio
async def test_reboot_uses_get_not_post():
    # glkvm registers /upgrade/reboot as @exposed_http("GET", ...); POST 405s.
    # This must stay GET even though it's a destructive, state-changing call.
    session = _FakeSession([_login_ok(), _ok({})])
    client = _client(session)

    await client.reboot()

    method, url, _ = session.requests[-1]
    assert method == "GET"
    assert urlsplit(url).path == "/api/upgrade/reboot"


# --- Snapshot ---


@pytest.mark.asyncio
async def test_non_jpeg_snapshot_returns_none():
    session = _FakeSession([_login_ok(), _FakeResp(200, b"not a jpeg", is_json=False)])
    client = _client(session)

    assert await client.get_snapshot() is None


@pytest.mark.asyncio
async def test_jpeg_snapshot_returns_bytes():
    jpeg = b"\xff\xd8\xff\xe0rest-of-jpeg"
    session = _FakeSession([_login_ok(), _FakeResp(200, jpeg, is_json=False)])
    client = _client(session)

    assert await client.get_snapshot() == jpeg


@pytest.mark.asyncio
async def test_snapshot_url_never_sends_preview_param():
    session = _FakeSession([_login_ok(), _FakeResp(200, b"\xff\xd8", is_json=False)])
    client = _client(session)

    await client.get_snapshot()

    _, url, _ = session.requests[-1]
    query = parse_qs(urlsplit(url).query)
    assert "preview" not in query
    assert query["allow_offline"] == ["1"]


# --- Header / ssl kwarg assertions ---


@pytest.mark.asyncio
async def test_token_header_sent_on_authenticated_calls():
    session = _FakeSession([_login_ok("ABC"), _ok({"state": True})])
    client = _client(session)

    await client.get_atx()

    _, _, kwargs = session.requests[-1]
    assert kwargs["headers"]["Token"] == "ABC"


@pytest.mark.asyncio
async def test_ssl_false_when_verify_ssl_disabled():
    session = _FakeSession([_login_ok(), _ok({})])
    client = _client(session, verify_ssl=False)

    await client.get_atx()

    for _, _, kwargs in session.requests:
        assert kwargs["ssl"] is False


@pytest.mark.asyncio
async def test_ssl_none_when_verify_ssl_enabled():
    session = _FakeSession([_login_ok(), _ok({})])
    client = _client(session, verify_ssl=True)

    await client.get_atx()

    for _, _, kwargs in session.requests:
        assert kwargs["ssl"] is None


# --- Host normalization ---


def test_host_scheme_and_trailing_slash_are_stripped():
    session = _FakeSession([])
    client = _client(session)
    client._host = client._normalize_host("https://10.0.0.5/")
    assert client._host == "10.0.0.5"
    assert CometApiClient._normalize_host("http://foo.bar/") == "foo.bar"
    assert CometApiClient._normalize_host("foo.bar") == "foo.bar"


# --- WebSocket ---


@pytest.mark.asyncio
async def test_connect_ws_uses_token_and_no_preview():
    fake_ws = object()
    session = _FakeSession([_login_ok("WSTOK")])
    session.queue_ws(fake_ws)
    client = _client(session)

    ws = await client.connect_ws()

    assert ws is fake_ws
    url, kwargs = session.ws_calls[0]
    assert url == "wss://10.0.0.5/api/ws?stream=0&auth_token=WSTOK"
    assert kwargs["ssl"] is False
    assert kwargs["heartbeat"] == 30


@pytest.mark.asyncio
async def test_connect_ws_401_relogins_and_retries():
    fake_ws = object()
    session = _FakeSession([_login_ok("TOK1"), _login_ok("TOK2")])
    handshake_err = aiohttp.WSServerHandshakeError(
        request_info=None, history=(), status=401
    )
    session.queue_ws(handshake_err)
    session.queue_ws(fake_ws)
    client = _client(session)

    ws = await client.connect_ws()

    assert ws is fake_ws
    assert session.ws_calls[0][0].endswith("auth_token=TOK1")
    assert session.ws_calls[1][0].endswith("auth_token=TOK2")


def _ws_handshake_error(url: str, status: int) -> aiohttp.WSServerHandshakeError:
    """Build a WSServerHandshakeError whose request_info.real_url carries the URL.

    Mirrors real aiohttp: ClientResponseError.__str__ renders
    ``url={self.request_info.real_url!r}``, so if a wss:// URL with
    ``auth_token=<token>`` in its query string is ever chained via
    ``raise ... from err``, the token leaks into any logged traceback.
    """
    request_info = aiohttp.RequestInfo(url=url, method="GET", headers={}, real_url=url)
    return aiohttp.WSServerHandshakeError(
        request_info=request_info, history=(), status=status, message="Unauthorized"
    )


@pytest.mark.asyncio
async def test_connect_ws_auth_error_does_not_leak_token_via_traceback():
    token = "SUPER-SECRET-WS-TOKEN"  # noqa: S105 - test fixture value, not a real secret
    url = f"wss://10.0.0.5/api/ws?stream=0&auth_token={token}"
    session = _FakeSession([_login_ok(token), _login_ok(token)])
    session.queue_ws(_ws_handshake_error(url, 401))
    session.queue_ws(_ws_handshake_error(url, 401))
    client = _client(session)

    with pytest.raises(CometAuthError) as exc_info:
        await client.connect_ws()

    exc = exc_info.value
    assert exc.__cause__ is None
    assert token not in str(exc)
    assert token not in repr(exc)
    full_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    assert token not in full_traceback


@pytest.mark.asyncio
async def test_connect_ws_non_auth_handshake_error_does_not_leak_token():
    token = "SUPER-SECRET-WS-TOKEN"  # noqa: S105 - test fixture value, not a real secret
    url = f"wss://10.0.0.5/api/ws?stream=0&auth_token={token}"
    session = _FakeSession([_login_ok(token)])
    session.queue_ws(_ws_handshake_error(url, 500))
    client = _client(session)

    with pytest.raises(CometConnectionError) as exc_info:
        await client.connect_ws()

    exc = exc_info.value
    assert exc.__cause__ is None
    assert token not in str(exc)
    assert token not in repr(exc)
    full_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    assert token not in full_traceback


# --- test_connection ---


@pytest.mark.asyncio
async def test_connection_logs_in_and_returns_info_result():
    session = _FakeSession([_login_ok(), _ok({"model": "Comet"})])
    client = _client(session)

    result = await client.test_connection()

    assert result == {"model": "Comet"}
    method, url, _ = session.requests[-1]
    assert method == "GET"
    assert url.startswith("https://10.0.0.5/api/info")
    query = parse_qs(urlsplit(url).query)
    assert query["fields"] == ["system"]
