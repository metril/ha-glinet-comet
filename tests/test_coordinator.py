"""Tests for the GL.iNet Comet coordinator (slow HTTP tier + WS push) and entity base."""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiohttp
import pytest

from custom_components.glinet_comet.api import (
    CometApiError,
    CometAuthError,
    CometConnectionError,
    CometRateLimitError,
)
from custom_components.glinet_comet.const import CONF_WS_RECONNECT_DELAY
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.entity import CometAtxEntity, CometEntity
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

# --- Live-verified shapes (fw V1.9.1 release1, model RM1PE; see task-0-report.md) ---

ATX_RESULT = {
    "busy": False,
    "enabled": False,
    "leds": {"hdd": False, "power": False},
    "power": "off",
}
HID_RESULT = {
    "busy": False,
    "connected": True,
    "enabled": True,
    "jiggler": {"active": False, "enabled": True, "interval": 20, "schedule": []},
    "keyboard": {
        "leds": {"caps": False, "num": False, "scroll": False},
        "online": True,
        "outputs": {"active": "", "available": []},
    },
    "mouse": {
        "absolute": True,
        "online": True,
        "outputs": {"active": "usb", "available": ["usb", "usb_rel"]},
    },
    "online": True,
}
MSD_RESULT = {
    "available_devices": {},
    "busy": False,
    "drive": {"cdrom": True, "connected": False, "image": None, "rw": False},
    "drive_partition": {"cdrom": False, "connected": False, "image": None, "rw": True},
    "enabled": True,
    "online": False,
    "storage": {"downloading": None, "images": {}, "parts": {}, "uploading": None},
}
STREAMER_RESULT = {
    "features": {"h264": True, "h265": True},
    "limits": {},
    "params": {"desired_fps": 40, "h264_bitrate": 2000, "quality": 80},
    "streamer": {"source": {"online": True}},
}
GPIO_RESULT = {
    "model": {
        "scheme": {"inputs": {}, "outputs": {"out_1": {"switch": True}}},
        "view": {"header": {"title": [{"text": "GPIO", "type": "label"}]}, "table": []},
    },
    "state": {"inputs": {}, "outputs": {"out_1": {"online": True, "state": False}}},
}
SYSTEM_SECTION = {
    "kernel": {"machine": "aarch64", "release": "6.1.141"},
    "kvmd": {"version": "4.82"},
    "platform": {
        "base": "Rockchip RV1126B-P EVB V14 Board",
        "board": "rpi4",
        "model": "v3",
        "serial": "SN-LIVE-1",
        "type": "rpi",
        "video": "hdmi",
    },
}
HOSTNAME_RESULT = {"hostname": "comet-kvm", "success": True}
NETWORK_RESULT = {
    "config": {
        "dns_servers": ["10.0.0.1"],
        "gateway": "10.0.0.1",
        "interface": "eth0",
        "ip_address": "10.0.0.50",
        "is_dhcp": True,
        "mac_address": "AA:BB:CC:DD:EE:FF",
        "state": "online",
    },
    "success": True,
}
UPGRADE_VERSION_RESULT = {"model": "RM1PE", "version": "V1.9.1 release1"}
UPGRADE_COMPARE_RESULT = {
    "beta_version": "",
    "error": None,
    "local_model": "RM1PE",
    "local_version": "V1.9.1 release1",
    "server_model": "RM1PE",
    "server_version": "V1.9.1 release1",
}


class FakeClient:
    """Fake CometApiClient: records call counts, scriptable failures."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self._raise_always: dict[str, Exception] = {}
        self.ws_to_return: object | None = None
        self.ws_sequence: list = []

    def fail_always(self, name: str, exc: Exception) -> None:
        self._raise_always[name] = exc

    async def _call(self, name, result):
        self.calls[name] = self.calls.get(name, 0) + 1
        if name in self._raise_always:
            raise self._raise_always[name]
        return result

    async def get_atx(self):
        return await self._call("get_atx", dict(ATX_RESULT))

    async def get_info(self, fields):
        if fields == "system":
            return await self._call("get_info_system", {"system": dict(SYSTEM_SECTION)})
        if fields == "hw":
            return await self._call(
                "get_info_hw", {"hw": {"health": {}, "platform": {}}}
            )
        raise AssertionError(f"unexpected fields={fields!r}")

    async def get_hid(self):
        return await self._call("get_hid", dict(HID_RESULT))

    async def get_msd(self):
        return await self._call("get_msd", dict(MSD_RESULT))

    async def get_streamer(self):
        return await self._call("get_streamer", dict(STREAMER_RESULT))

    async def get_gpio(self):
        return await self._call("get_gpio", dict(GPIO_RESULT))

    async def get_hostname(self):
        return await self._call("get_hostname", dict(HOSTNAME_RESULT))

    async def get_network_config(self):
        return await self._call("get_network_config", dict(NETWORK_RESULT))

    async def get_upgrade_version(self):
        return await self._call("get_upgrade_version", dict(UPGRADE_VERSION_RESULT))

    async def get_upgrade_compare(self):
        return await self._call("get_upgrade_compare", dict(UPGRADE_COMPARE_RESULT))

    async def connect_ws(self, stream: bool = True):
        ws = self.ws_sequence.pop(0) if self.ws_sequence else self.ws_to_return
        return await self._call("connect_ws", ws)


class FakeEntry:
    def __init__(self) -> None:
        self.entry_id = "test_entry"
        self.title = "Comet Test"
        self.data = {"host": "10.0.0.5"}
        self.options: dict = {}
        self.async_start_reauth_calls = 0

    def async_start_reauth(self, hass) -> None:
        self.async_start_reauth_calls += 1


class FakeHass:
    def __init__(self) -> None:
        self.tasks: list = []

    def async_create_background_task(self, coro, name):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class FakeWSMsg:
    def __init__(self, type_, data=None) -> None:
        self.type = type_
        self._data = data

    def json(self):
        return self._data


class FakeWS:
    """Async-iterable fake aiohttp.ClientWebSocketResponse."""

    def __init__(self, messages: list[FakeWSMsg]) -> None:
        self._messages = list(messages)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeWSRaisingAfterOne:
    """Fake WS that yields one message, then raises mid-iteration (a drop)."""

    def __init__(self, message: FakeWSMsg, exc: Exception) -> None:
        self._message = message
        self._exc = exc
        self._yielded = False
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._yielded:
            self._yielded = True
            return self._message
        raise self._exc

    async def close(self) -> None:
        self.closed = True


def make_coordinator(client: FakeClient) -> CometDataUpdateCoordinator:
    return CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)


# --- (a) first update bootstraps all subsystems -----------------------------


@pytest.mark.asyncio
async def test_first_update_bootstraps_all_subsystems_and_returns_schema_keys():
    client = FakeClient()
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert set(state) == {
        "atx",
        "hid",
        "msd",
        "streamer",
        "gpio",
        "gpio_model",
        "gpio_labels",
        "info",
        "glinet",
        "unsupported",
        "ws_connected",
    }
    assert state["atx"] == ATX_RESULT
    assert state["hid"]["connected"] is True
    assert state["msd"]["drive"]["cdrom"] is True
    assert state["streamer"]["params"]["desired_fps"] == 40
    assert state["info"]["system"]["platform"]["serial"] == "SN-LIVE-1"
    assert state["glinet"]["hostname"] == HOSTNAME_RESULT
    assert state["glinet"]["network"] == NETWORK_RESULT
    assert state["glinet"]["upgrade_version"] == UPGRADE_VERSION_RESULT
    assert state["glinet"]["upgrade_compare"] == UPGRADE_COMPARE_RESULT
    for key in ("get_atx", "get_info_system", "get_hid", "get_msd", "get_streamer", "get_gpio"):
        assert client.calls[key] == 1


@pytest.mark.asyncio
async def test_second_update_does_not_repeat_gpio_bootstrap_only_read():
    client = FakeClient()
    coord = make_coordinator(client)
    await coord._async_update_data()
    await coord._async_update_data()

    # gpio is the only subsystem still fetched just once -- its bootstrap
    # read succeeded on cycle 1, so `_gpio_bootstrapped` suppresses cycle 2.
    assert client.calls["get_gpio"] == 1
    # atx/info/hid/msd/streamer are polled every cycle -- hid/msd/streamer
    # get no WS-pushed writes back (MSD in particular pushes nothing), so
    # they can't rely on WS push alone and are re-polled every cycle too.
    assert client.calls["get_atx"] == 2
    assert client.calls["get_info_system"] == 2
    assert client.calls["get_hid"] == 2
    assert client.calls["get_msd"] == 2
    assert client.calls["get_streamer"] == 2


@pytest.mark.asyncio
async def test_gpio_bootstrap_retried_after_first_cycle_failure():
    """A core failure on cycle 1 must not consume the gpio bootstrap.

    Regression for: `_first_run` (the old gate) used to be flipped to False
    up front, before the gather/error checks. If the very first cycle
    raised (any core read failing, rate limit, auth), the gpio bootstrap
    read from that cycle was discarded along with everything else, but the
    flag was already spent -- so gpio would never be fetched again for the
    life of the coordinator. `_gpio_bootstrapped` is only set once a gpio
    read actually succeeds, so it can't be spent by an unrelated failure.
    hid/msd/streamer are unaffected either way since they're core reads
    fetched every cycle regardless of the gpio bootstrap.
    """
    client = FakeClient()
    client.fail_always("get_atx", CometConnectionError("down"))
    coord = make_coordinator(client)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # All core reads ran (gather is concurrent), but the cycle raised before
    # anything was committed to state.
    assert client.calls["get_hid"] == 1
    assert coord._state["hid"] == {}

    client._raise_always.pop("get_atx")  # let the next cycle succeed

    state = await coord._async_update_data()

    # gpio's bootstrap was retried, not silently skipped forever.
    assert client.calls["get_hid"] == 2
    assert client.calls["get_msd"] == 2
    assert client.calls["get_streamer"] == 2
    assert client.calls["get_gpio"] == 2
    assert state["hid"]["connected"] is True


@pytest.mark.asyncio
async def test_gpio_bootstrap_retried_after_transient_read_failure():
    """A retryable gpio-read failure (5xx) must not stall the bootstrap forever.

    Regression for: gpio used to be fetched only on the very first cycle
    (the old `first_run` flag), which was consumed once that cycle as a
    whole succeeded -- even if the gpio read itself failed with a transient
    (non-400/404) status. `gpio_model` would then stay empty for the life
    of the entry. Now gpio is retried every cycle until its own read
    succeeds or is marked unsupported (see the 400/404 test below).
    """
    client = FakeClient()
    client.fail_always("get_gpio", CometApiError("HTTP 500: boom", status=500))
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert client.calls["get_gpio"] == 1
    assert state["gpio_model"] == {"inputs": {}, "outputs": {}}
    assert "gpio" not in state["unsupported"]
    assert state["hid"]["connected"] is True  # rest of the cycle still succeeded

    client._raise_always.pop("get_gpio")  # let the retry succeed

    state = await coord._async_update_data()

    assert client.calls["get_gpio"] == 2  # retried, not skipped
    assert state["gpio_model"]["outputs"] == GPIO_RESULT["model"]["scheme"]["outputs"]

    await coord._async_update_data()

    assert client.calls["get_gpio"] == 2  # bootstrap succeeded, not fetched again


@pytest.mark.asyncio
async def test_gpio_bootstrap_400_marks_unsupported_and_is_skipped_next_cycle():
    client = FakeClient()
    client.fail_always("get_gpio", CometApiError("ValidatorError: nope", status=400))
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert "gpio" in state["unsupported"]
    assert client.calls["get_gpio"] == 1
    assert state["gpio_model"] == {"inputs": {}, "outputs": {}}

    await coord._async_update_data()

    assert client.calls["get_gpio"] == 1  # not called again
    assert state["msd"]["drive"]["cdrom"] is True


# --- (b) optional read failure -> unsupported (400/404) vs retried (other) --


@pytest.mark.asyncio
async def test_optional_read_400_marks_unsupported_and_is_skipped_next_cycle():
    client = FakeClient()
    client.fail_always(
        "get_hostname", CometApiError("ValidatorError: nope", status=400)
    )
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert "hostname" in state["unsupported"]
    assert "hostname" not in state["glinet"]
    assert client.calls["get_hostname"] == 1
    # core state intact despite the optional failure
    assert state["atx"] == ATX_RESULT
    assert state["hid"]["connected"] is True
    assert state["glinet"]["network"] == NETWORK_RESULT

    await coord._async_update_data()
    assert client.calls["get_hostname"] == 1  # not called again


@pytest.mark.asyncio
async def test_optional_read_404_marks_unsupported_and_is_skipped_next_cycle():
    client = FakeClient()
    client.fail_always("get_hostname", CometApiError("not found", status=404))
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert "hostname" in state["unsupported"]
    assert client.calls["get_hostname"] == 1

    await coord._async_update_data()
    assert client.calls["get_hostname"] == 1  # not called again


@pytest.mark.asyncio
async def test_optional_read_transient_500_is_not_unsupported_and_retries():
    """A transient HTTP error must not permanently disable an optional read.

    Regression for: any CometApiError (500/503/ok:false) used to mark a
    field unsupported forever. Only status 400/404 (the firmware genuinely
    doesn't support the field) should do that -- anything else must be
    retried next cycle.
    """
    client = FakeClient()
    client.fail_always("get_hostname", CometApiError("HTTP 500: boom", status=500))
    coord = make_coordinator(client)

    state = await coord._async_update_data()

    assert "hostname" not in state["unsupported"]
    assert client.calls["get_hostname"] == 1

    client._raise_always.pop("get_hostname")
    state = await coord._async_update_data()

    assert client.calls["get_hostname"] == 2  # retried, not skipped
    assert state["glinet"]["hostname"] == HOSTNAME_RESULT
    assert "hostname" not in state["unsupported"]


# --- (c) core auth / rate-limit errors ---------------------------------------


@pytest.mark.asyncio
async def test_core_auth_error_raises_config_entry_auth_failed():
    client = FakeClient()
    client.fail_always("get_atx", CometAuthError("bad creds"))
    coord = make_coordinator(client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_core_rate_limit_raises_update_failed_mentioning_seconds():
    client = FakeClient()
    client.fail_always("get_atx", CometRateLimitError(77))
    coord = make_coordinator(client)

    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "77" in str(exc_info.value)


@pytest.mark.asyncio
async def test_core_connection_error_raises_update_failed():
    client = FakeClient()
    client.fail_always("get_info_system", CometConnectionError("timed out"))
    coord = make_coordinator(client)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


# --- (d) WS message merge handlers -------------------------------------------


def test_partial_atx_frame_keeps_power():
    coord = make_coordinator(FakeClient())
    coord._state["atx"] = dict(ATX_RESULT)

    coord._process_ws_message({"event_type": "atx", "event": {"busy": True}})

    assert coord._state["atx"]["busy"] is True
    assert coord._state["atx"]["power"] == "off"  # untouched


def test_info_section_frames_merge_into_info():
    coord = make_coordinator(FakeClient())

    coord._process_ws_message({"event_type": "info", "event": {"auth": {"enabled": True}}})
    coord._process_ws_message(
        {"event_type": "info", "event": {"system": {"kernel": {"machine": "aarch64"}}}}
    )

    assert coord._state["info"]["auth"] == {"enabled": True}
    assert coord._state["info"]["system"]["kernel"]["machine"] == "aarch64"


def test_msd_frame_with_storage_replaces_it_wholesale():
    coord = make_coordinator(FakeClient())
    coord._state["msd"] = {"enabled": True, "storage": {"images": {"old.iso": {}}}}

    coord._process_ws_message({"event_type": "msd", "event": {"storage": {"images": {}}}})

    assert coord._state["msd"]["storage"] == {"images": {}}
    assert coord._state["msd"]["enabled"] is True  # untouched sibling key


def test_gpio_event_merges_channels_without_dropping_others():
    coord = make_coordinator(FakeClient())
    coord._state["gpio"] = {"inputs": {"ch0": {"state": False}}, "outputs": {}}

    coord._process_ws_message(
        {"event_type": "gpio", "event": {"state": {"inputs": {"ch1": {"state": True}}}}}
    )

    assert coord._state["gpio"]["inputs"]["ch0"] == {"state": False}
    assert coord._state["gpio"]["inputs"]["ch1"] == {"state": True}


def test_unknown_event_type_is_ignored():
    coord = make_coordinator(FakeClient())
    coord.async_update_listeners = Mock()
    before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in coord._state.items()}

    coord._process_ws_message({"event_type": "ocr", "event": {"enabled": False}})

    assert coord._state == before
    coord.async_update_listeners.assert_not_called()


def test_loop_event_is_ignored():
    coord = make_coordinator(FakeClient())
    coord.async_update_listeners = Mock()

    coord._process_ws_message({"event_type": "loop", "event": {"version": {"major": 4}}})

    coord.async_update_listeners.assert_not_called()


# --- (e) gotcha #1: WS path never calls async_set_updated_data --------------


@pytest.mark.asyncio
async def test_ws_path_uses_update_listeners_not_set_updated_data():
    client = FakeClient()
    client.ws_to_return = FakeWS(
        [
            FakeWSMsg(aiohttp.WSMsgType.TEXT, {"event_type": "atx", "event": {"busy": True}}),
            FakeWSMsg(aiohttp.WSMsgType.CLOSED),
        ]
    )
    coord = make_coordinator(client)
    update_listener_calls: list[dict] = []
    coord.async_update_listeners = lambda: update_listener_calls.append(dict(coord._state))
    coord.async_set_updated_data = Mock(
        side_effect=AssertionError("must never call async_set_updated_data from the WS path")
    )

    await coord._ws_connect_and_listen()

    assert len(update_listener_calls) >= 2  # ws_connected=True push + atx event push
    assert coord.data["atx"]["busy"] is True
    coord.async_set_updated_data.assert_not_called()


# --- (f) 6h upgrade/compare throttle -----------------------------------------


@pytest.mark.asyncio
async def test_upgrade_compare_throttled_across_immediate_cycles():
    client = FakeClient()
    coord = make_coordinator(client)

    await coord._async_update_data()
    assert client.calls["get_upgrade_compare"] == 1

    await coord._async_update_data()
    assert client.calls["get_upgrade_compare"] == 1  # not re-fetched immediately


# --- (g) atx_enabled reflects the live (no ATX board) shape ------------------


@pytest.mark.asyncio
async def test_atx_enabled_false_for_live_shape():
    client = FakeClient()
    coord = make_coordinator(client)

    await coord._async_update_data()

    assert coord.atx_enabled is False


# --- WS loop: auth / rate-limit / generic-reconnect behavior -----------------


@pytest.mark.asyncio
async def test_ws_loop_auth_error_stops_forever_and_starts_reauth(monkeypatch):
    client = FakeClient()
    client.fail_always("connect_ws", CometAuthError("bad token"))
    coord = make_coordinator(client)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await coord._ws_loop()

    assert coord.entry.async_start_reauth_calls == 1
    assert client.calls["connect_ws"] == 1  # no retry after auth failure
    assert sleep_calls == []
    assert coord.ws_connected is False


@pytest.mark.asyncio
async def test_ws_loop_rate_limit_waits_remaining_time_not_reconnect_delay(monkeypatch):
    client = FakeClient()
    client.fail_always("connect_ws", CometRateLimitError(42))
    coord = make_coordinator(client)
    coord.entry.options[CONF_WS_RECONNECT_DELAY] = 5

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_loop()

    assert sleep_calls == [42]  # remaining_time, NOT the configured reconnect delay (5)
    assert coord.entry.async_start_reauth_calls == 0


@pytest.mark.asyncio
async def test_ws_loop_generic_error_uses_configured_reconnect_delay(monkeypatch):
    client = FakeClient()
    client.fail_always("connect_ws", CometConnectionError("down"))
    coord = make_coordinator(client)
    coord.entry.options[CONF_WS_RECONNECT_DELAY] = 9

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_loop()

    assert sleep_calls == [9]
    assert coord.entry.async_start_reauth_calls == 0


@pytest.mark.asyncio
async def test_ws_reconnect_after_mid_listen_error_triggers_refresh(monkeypatch):
    """A connection that dies mid-listen (not via a clean CLOSE) still counts
    as "had a previous connection" -- the next reconnect must still trigger
    `async_request_refresh`. Regression for `had_connection` only being set
    in the loop's clean-exit branch.
    """
    client = FakeClient()
    first_ws = FakeWSRaisingAfterOne(
        FakeWSMsg(aiohttp.WSMsgType.TEXT, {"event_type": "atx", "event": {"busy": True}}),
        CometConnectionError("dropped"),
    )
    second_ws = FakeWS([FakeWSMsg(aiohttp.WSMsgType.CLOSED)])
    client.ws_sequence = [first_ws, second_ws]
    coord = make_coordinator(client)

    refresh_calls: list[bool] = []

    async def fake_refresh():
        refresh_calls.append(True)

    coord.async_request_refresh = fake_refresh

    sleep_calls: list[float] = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await coord._ws_loop()

    assert client.calls["connect_ws"] == 2
    # Exactly once: not on the first-ever connect, but on the reconnect that
    # follows the mid-listen drop.
    assert refresh_calls == [True]
    assert first_ws.closed is True
    assert second_ws.closed is True


# --- async_start / async_stop --------------------------------------------------


@pytest.mark.asyncio
async def test_async_stop_awaits_cancelled_task_before_closing_and_is_idempotent():
    client = FakeClient()
    coord = make_coordinator(client)
    order: list[str] = []

    async def _never_ending():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Only recorded once cancellation is actually delivered and
            # handled -- i.e. once `async_stop` has awaited the task.
            order.append("task_cancelled")
            raise

    class _WS:
        def __init__(self):
            self.closed = False

        async def close(self):
            order.append("close")
            self.closed = True

    task = asyncio.ensure_future(_never_ending())
    # Let the task actually start and reach `await asyncio.sleep(3600)` --
    # cancelling a task before its first step never runs its body at all
    # (no try/except), which would make this test pass for the wrong reason.
    await asyncio.sleep(0)
    coord._ws_task = task
    coord._ws = _WS()

    await coord.async_stop()

    assert order == ["task_cancelled", "close"]  # cancelled+awaited BEFORE the socket closes
    assert task.cancelled()
    assert coord._ws_task is None
    assert coord._ws is None

    # Idempotent: calling again is a no-op, no error.
    await coord.async_stop()
    assert order == ["task_cancelled", "close"]


# --- entity.py ----------------------------------------------------------------


def test_comet_entity_device_info_from_parsers():
    client = FakeClient()
    coord = make_coordinator(client)
    coord.data = {
        "info": {"system": {"platform": {"model": "v3", "serial": "SN-LIVE-1"}}},
        "glinet": {
            "upgrade_version": UPGRADE_VERSION_RESULT,
            "network": NETWORK_RESULT,
        },
    }

    entity = CometEntity(coord, coord.entry)
    info = entity._attr_device_info

    assert info["model"] == "RM1PE"
    assert info["sw_version"] == "V1.9.1 release1"
    assert info["serial_number"] == "SN-LIVE-1"
    assert info["connections"] == {("mac", "AA:BB:CC:DD:EE:FF")}
    assert info["configuration_url"] == "https://10.0.0.5"
    assert info["manufacturer"] == "GL.iNet"


def test_comet_atx_entity_available_requires_atx_enabled():
    client = FakeClient()
    coord = make_coordinator(client)
    coord.data = {}
    coord._state["atx"] = {"enabled": False}

    entity = CometAtxEntity(coord, coord.entry)
    assert entity.available is False

    coord._state["atx"] = {"enabled": True}
    assert entity.available is True


def test_gpio_entities_are_comet_gpio_entity_subclasses():
    """Proof for item 1's dedupe: all three GPIO entity classes share one base."""
    from custom_components.glinet_comet.binary_sensor import (
        CometGpioInputBinarySensor,
    )
    from custom_components.glinet_comet.button import CometGpioPulseButton
    from custom_components.glinet_comet.entity import CometGpioEntity
    from custom_components.glinet_comet.switch import CometGpioSwitch

    assert issubclass(CometGpioInputBinarySensor, CometGpioEntity)
    assert issubclass(CometGpioSwitch, CometGpioEntity)
    assert issubclass(CometGpioPulseButton, CometGpioEntity)
