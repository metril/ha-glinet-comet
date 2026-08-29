"""Tests for the GL.iNet Comet config flow (user/reauth/reconfigure/options)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet import config_flow
from custom_components.glinet_comet.api import (
    CometAuthError,
    CometConnectionError,
    CometRateLimitError,
)
from custom_components.glinet_comet.const import (
    CONF_ENABLE_ATX,
    CONF_HOST,
    CONF_HTTP_TIMEOUT,
    CONF_KEEP_VIDEO_ACTIVE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOTP_SECRET,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    CONF_WS_RECONNECT_DELAY,
)
from homeassistant.data_entry_flow import AbortFlow


class FakeEntry:
    """Stand-in for a Home Assistant ConfigEntry."""

    def __init__(self, data, *, unique_id=None, options=None) -> None:
        self.data = dict(data)
        self.unique_id = unique_id
        self.options = dict(options) if options is not None else {}


class _Spy:
    """Records what the fake CometApiClient saw; tuned per-test."""

    result = {"system": {"platform": {"serial": "SN-1"}}}
    error = None
    instances: list
    logout_calls: int


@pytest.fixture
def fake_client(monkeypatch):
    """Patch config_flow.CometApiClient with a fake; tune via the returned spy."""

    spy = _Spy()
    spy.result = {"system": {"platform": {"serial": "SN-1"}}}
    spy.error = None
    spy.instances = []
    spy.logout_calls = 0

    class _FakeApiClient:
        def __init__(
            self,
            session,
            host,
            username,
            password,
            *,
            totp_secret="",
            verify_ssl=False,
        ) -> None:
            self.session = session
            self.host = host
            self.username = username
            self.password = password
            self.totp_secret = totp_secret
            self.verify_ssl = verify_ssl
            spy.instances.append(self)

        async def test_connection(self):
            if spy.error is not None:
                raise spy.error
            return spy.result

        async def async_logout(self):
            spy.logout_calls += 1

    monkeypatch.setattr(config_flow, "CometApiClient", _FakeApiClient)
    return spy


def _user_input(**overrides):
    data = {
        CONF_HOST: "10.0.0.5",
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "secret",
    }
    data.update(overrides)
    return data


# --- async_step_user ---


async def test_user_step_shows_form():
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_user_step_creates_entry_with_serial_unique_id(fake_client):
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input())
    assert flow.unique_id == "SN-1"
    assert result["data"][CONF_HOST] == "10.0.0.5"


async def test_user_step_normalizes_host(fake_client):
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input(**{CONF_HOST: "https://10.0.0.5/"}))
    assert result["data"][CONF_HOST] == "10.0.0.5"


async def test_user_step_falls_back_to_host_unique_id_without_serial(fake_client):
    fake_client.result = {"system": {"platform": {}}}
    flow = config_flow.CometConfigFlow()
    await flow.async_step_user(_user_input())
    assert flow.unique_id == "10.0.0.5"


async def test_user_step_defaults_username_and_verify_ssl(fake_client):
    flow = config_flow.CometConfigFlow()
    await flow.async_step_user({CONF_HOST: "10.0.0.5", CONF_PASSWORD: "secret"})
    client = fake_client.instances[-1]
    assert client.username == "admin"
    assert client.verify_ssl is False


async def test_user_step_invalid_auth(fake_client):
    fake_client.error = CometAuthError("nope")
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input())
    assert result["errors"]["base"] == "invalid_auth"
    # Pins single-shot login + logout-in-`finally` on the failure path: the
    # device locks logins for 10 minutes after 10 failures, so this must
    # never construct a second client or retry the login itself.
    assert fake_client.logout_calls == 1
    assert len(fake_client.instances) == 1


async def test_user_step_cannot_connect(fake_client):
    fake_client.error = CometConnectionError("no route")
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input())
    assert result["errors"]["base"] == "cannot_connect"
    assert fake_client.logout_calls == 1
    assert len(fake_client.instances) == 1


async def test_user_step_unknown(fake_client):
    fake_client.error = ValueError("boom")
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input())
    assert result["errors"]["base"] == "unknown"


async def test_user_step_rate_limited_sets_seconds_placeholder(fake_client):
    fake_client.error = CometRateLimitError(77)
    flow = config_flow.CometConfigFlow()
    result = await flow.async_step_user(_user_input())
    assert result["errors"]["base"] == "rate_limited"
    assert result["description_placeholders"] == {"seconds": "77"}


async def test_user_step_duplicate_aborts(fake_client):
    flow = config_flow.CometConfigFlow()
    flow._configured_unique_ids = {"SN-1"}
    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_user(_user_input())
    assert exc_info.value.reason == "already_configured"


async def test_user_step_logs_out_after_validation(fake_client):
    flow = config_flow.CometConfigFlow()
    await flow.async_step_user(_user_input())
    assert fake_client.logout_calls == 1


# --- async_step_reauth_confirm ---


async def test_reauth_confirm_updates_password_and_totp(fake_client):
    entry = FakeEntry(
        {
            CONF_HOST: "10.0.0.5",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "old",
            CONF_TOTP_SECRET: "",
        },
        unique_id="SN-1",
    )
    flow = config_flow.CometConfigFlow()
    flow._reauth_entry = entry
    await flow.async_step_reauth_confirm(
        {CONF_PASSWORD: "newpass", CONF_TOTP_SECRET: "NEWSECRET"}
    )
    assert entry.data[CONF_PASSWORD] == "newpass"
    assert entry.data[CONF_TOTP_SECRET] == "NEWSECRET"
    assert entry.data[CONF_HOST] == "10.0.0.5"


async def test_reauth_confirm_invalid_auth_reshows_form(fake_client):
    fake_client.error = CometAuthError("nope")
    entry = FakeEntry(
        {CONF_HOST: "10.0.0.5", CONF_USERNAME: "admin", CONF_PASSWORD: "old"},
        unique_id="SN-1",
    )
    flow = config_flow.CometConfigFlow()
    flow._reauth_entry = entry
    result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "wrong"})
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


# --- async_step_reconfigure ---


async def test_reconfigure_host_change_same_serial(fake_client):
    entry = FakeEntry(
        {CONF_HOST: "10.0.0.5", CONF_USERNAME: "admin", CONF_PASSWORD: "old"},
        unique_id="SN-1",
    )
    flow = config_flow.CometConfigFlow()
    flow._reconfigure_entry = entry
    await flow.async_step_reconfigure(
        {CONF_HOST: "10.0.0.9", CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
    )
    assert entry.unique_id == "SN-1"
    assert entry.data[CONF_HOST] == "10.0.0.9"


async def test_reconfigure_host_change_different_serial_aborts(fake_client):
    fake_client.result = {"system": {"platform": {"serial": "SN-2"}}}
    entry = FakeEntry(
        {CONF_HOST: "10.0.0.5", CONF_USERNAME: "admin", CONF_PASSWORD: "old"},
        unique_id="SN-1",
    )
    flow = config_flow.CometConfigFlow()
    flow._reconfigure_entry = entry
    with pytest.raises(AbortFlow) as exc_info:
        await flow.async_step_reconfigure(
            {CONF_HOST: "10.0.0.9", CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
        )
    assert exc_info.value.reason == "unique_id_mismatch"


async def test_reconfigure_migrates_host_fallback_unique_id(fake_client):
    """A host-derived unique id was never a device identity: a host change
    on such an entry must migrate the id instead of aborting as mismatched.
    """
    fake_client.result = {"system": {"platform": {}}}
    entry = FakeEntry(
        {CONF_HOST: "10.0.0.5", CONF_USERNAME: "admin", CONF_PASSWORD: "old"},
        unique_id="10.0.0.5",
    )
    flow = config_flow.CometConfigFlow()
    flow._reconfigure_entry = entry
    result = await flow.async_step_reconfigure(
        {CONF_HOST: "10.0.0.9", CONF_USERNAME: "admin", CONF_PASSWORD: "new"}
    )
    assert result["type"] == "abort"
    assert entry.unique_id == "10.0.0.9"


# --- options flow ---


async def test_options_flow_round_trip():
    entry = FakeEntry({}, unique_id="SN-1", options={})
    flow = config_flow.CometOptionsFlow()
    flow.config_entry = entry

    user_input = {
        CONF_SCAN_INTERVAL: 120,
        CONF_WS_RECONNECT_DELAY: 5,
        CONF_HTTP_TIMEOUT: 10,
        CONF_ENABLE_ATX: True,
        CONF_KEEP_VIDEO_ACTIVE: True,
    }
    result = await flow.async_step_init(user_input)
    assert result["data"] == user_input

    result = await flow.async_step_init(None)
    assert result["type"] == "form"
