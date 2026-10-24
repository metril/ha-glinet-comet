"""Tests for the MSD Image select entity (optimistic state after writes)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

import pytest
from unittest.mock import Mock

from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.select import MSD_IMAGE_NONE, CometMsdImageSelect
from homeassistant.exceptions import HomeAssistantError


class FakeClient:
    """Records set_msd_connected/set_msd_params calls; scriptable failures."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self._raise: Exception | None = None

    def fail_next(self, exc: Exception) -> None:
        self._raise = exc

    async def set_msd_connected(self, connected: bool) -> None:
        self.calls.append(("set_msd_connected", (connected,), {}))
        if self._raise is not None:
            exc, self._raise = self._raise, None
            raise exc

    async def set_msd_params(self, image: str, cdrom: bool = True, rw: bool = False) -> None:
        self.calls.append(("set_msd_params", (), {"image": image, "cdrom": cdrom, "rw": rw}))
        if self._raise is not None:
            exc, self._raise = self._raise, None
            raise exc


class FakeEntry:
    def __init__(self) -> None:
        self.entry_id = "test_entry"
        self.title = "Comet Test"
        self.data = {"host": "10.0.0.5"}
        self.options: dict = {}


class FakeHass:
    def __init__(self) -> None:
        self.tasks: list = []

    def async_create_background_task(self, coro, name):
        return None


def make_select(
    client: FakeClient, data: dict
) -> tuple[CometDataUpdateCoordinator, CometMsdImageSelect]:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = data
    coord._state.update(data)
    entity = CometMsdImageSelect(coord, coord.entry)
    return coord, entity


@pytest.mark.asyncio
async def test_select_image_sets_optimistic_drive_image_and_pushes():
    client = FakeClient()
    coord, entity = make_select(
        client, {"msd": {"drive": {"connected": False, "image": None}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = Mock(side_effect=AssertionError("no async_set_updated_data"))
    coord.async_update_listeners = lambda: pushed.append(dict(coord.data))

    await entity.async_select_option("ubuntu.iso")

    assert client.calls == [
        ("set_msd_params", (), {"image": "ubuntu.iso", "cdrom": True, "rw": False})
    ]
    assert coord.data["msd"]["drive"]["image"] == "ubuntu.iso"
    assert pushed and pushed[-1]["msd"]["drive"]["image"] == "ubuntu.iso"


@pytest.mark.asyncio
async def test_select_image_disconnects_first_when_connected_and_marks_disconnected():
    client = FakeClient()
    coord, entity = make_select(
        client, {"msd": {"drive": {"connected": True, "image": "old.iso"}}}
    )
    coord.async_set_updated_data = Mock(side_effect=AssertionError("no async_set_updated_data"))
    coord.async_update_listeners = lambda: None

    await entity.async_select_option("new.iso")

    assert client.calls[0] == ("set_msd_connected", (False,), {})
    assert client.calls[1][0] == "set_msd_params"
    assert coord.data["msd"]["drive"]["connected"] is False
    assert coord.data["msd"]["drive"]["image"] == "new.iso"


@pytest.mark.asyncio
async def test_select_none_sentinel_maps_to_empty_image_and_none_optimistic_state():
    client = FakeClient()
    coord, entity = make_select(
        client, {"msd": {"drive": {"connected": False, "image": "old.iso"}}}
    )
    coord.async_set_updated_data = Mock(side_effect=AssertionError("no async_set_updated_data"))
    coord.async_update_listeners = lambda: None

    await entity.async_select_option(MSD_IMAGE_NONE)

    assert client.calls == [
        ("set_msd_params", (), {"image": "", "cdrom": True, "rw": False})
    ]
    assert coord.data["msd"]["drive"]["image"] is None


@pytest.mark.asyncio
async def test_select_image_uses_setdefault_when_msd_drive_absent():
    client = FakeClient()
    coord, entity = make_select(client, {})
    coord.async_set_updated_data = Mock(side_effect=AssertionError("no async_set_updated_data"))
    coord.async_update_listeners = lambda: None

    await entity.async_select_option("ubuntu.iso")

    assert coord.data["msd"]["drive"]["image"] == "ubuntu.iso"


@pytest.mark.asyncio
async def test_select_image_error_raises_home_assistant_error_and_skips_optimistic_update():
    client = FakeClient()
    client.fail_next(CometApiError("busy", status=500))
    coord, entity = make_select(
        client, {"msd": {"drive": {"connected": False, "image": None}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = Mock(side_effect=AssertionError("no async_set_updated_data"))
    coord.async_update_listeners = lambda: pushed.append(dict(coord.data))

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("ubuntu.iso")

    assert coord.data["msd"]["drive"]["image"] is None
    assert pushed == []


def test_options_include_none_sentinel_and_storage_images():
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = {
        "msd": {
            "drive": {"image": None},
            "storage": {"images": {"a.iso": {}, "b.iso": {}}},
        }
    }
    entity = CometMsdImageSelect(coord, coord.entry)

    assert entity.options == [MSD_IMAGE_NONE, "a.iso", "b.iso"]
    assert entity.current_option == MSD_IMAGE_NONE


# --- Comet X active port + mouse output selects ---

import copy
import json

from custom_components.glinet_comet.const import DOMAIN
from custom_components.glinet_comet.select import (
    CometActivePortSelect,
    CometMouseOutputSelect,
    async_setup_entry,
)

_FX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fx(name: str) -> dict:
    with open(os.path.join(_FX, name)) as f:
        return json.load(f)


class _Entry(FakeEntry):
    def __init__(self) -> None:
        super().__init__()
        self.unload_callbacks: list = []

    def async_on_unload(self, fn) -> None:
        self.unload_callbacks.append(fn)


class _Hass(FakeHass):
    def __init__(self) -> None:
        super().__init__()
        self.data: dict = {}


class _PortClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.err: Exception | None = None

    async def switch_set_active(self, port_id: str) -> None:
        if self.err:
            raise self.err
        self.calls.append(("active", port_id))

    async def set_hid_mouse_output(self, output: str) -> None:
        self.calls.append(("mouse", output))


def _setup(data: dict, client=None):
    import asyncio

    coord = CometDataUpdateCoordinator(_Hass(), _Entry(), client or _PortClient())
    coord.data = data
    hass = _Hass()
    coord.entry.runtime_data = SimpleNamespace(coordinator=coord, client=None)
    added: list = []
    asyncio.run(async_setup_entry(hass, coord.entry, added.extend))
    return added, coord


def test_rm4pe_creates_both_selects():
    added, _ = _setup(_fx("state_live_rm4pe.json"))
    assert len([e for e in added if isinstance(e, CometActivePortSelect)]) == 1
    assert len([e for e in added if isinstance(e, CometMouseOutputSelect)]) == 1


def test_poe_without_switch_has_mouse_select_only():
    data = _fx("state_live.json")
    data["unsupported"] = ["switch"]
    added, _ = _setup(data)
    assert not [e for e in added if isinstance(e, CometActivePortSelect)]
    assert len([e for e in added if isinstance(e, CometMouseOutputSelect)]) == 1


def test_mouse_select_skipped_when_available_empty():
    data = _fx("state_live_rm4pe.json")
    data["hid"]["mouse"]["outputs"]["available"] = []
    added, _ = _setup(data)
    assert not [e for e in added if isinstance(e, CometMouseOutputSelect)]


def test_late_switch_model_adds_active_select_once():
    data = _fx("state_live.json")
    added, coord = _setup(data)
    rm4 = _fx("state_live_rm4pe.json")
    coord.data = {**data, "switch": rm4["switch"], "switch_model": copy.deepcopy(rm4["switch_model"])}
    coord.async_update_listeners()
    coord.data = {**coord.data, "switch_model": copy.deepcopy(rm4["switch_model"])}
    coord.async_update_listeners()
    assert len([e for e in added if isinstance(e, CometActivePortSelect)]) == 1


@pytest.mark.asyncio
async def test_active_port_select_options_current_and_write():
    import asyncio

    client = _PortClient()
    data = _fx("state_live_rm4pe.json")
    coord = CometDataUpdateCoordinator(_Hass(), _Entry(), client)
    coord.data = data
    ent = CometActivePortSelect(coord, coord.entry)
    assert ent.options == ["Port 1", "Port 2", "Port 3", "Port 4"]
    assert ent.current_option == "Port 2"
    await ent.async_select_option("Port 3")
    assert client.calls == [("active", "1.3")]
    client.err = CometApiError("x", status=500)
    with pytest.raises(HomeAssistantError):
        await ent.async_select_option("Port 1")
    with pytest.raises(HomeAssistantError):
        await ent.async_select_option("Nope")


@pytest.mark.asyncio
async def test_mouse_select_options_current_and_write():
    client = _PortClient()
    coord = CometDataUpdateCoordinator(_Hass(), _Entry(), client)
    coord.data = _fx("state_live_rm4pe.json")
    ent = CometMouseOutputSelect(coord, coord.entry)
    assert ent.options == ["usb", "usb_rel", "usb_hybrid", "usb_touch"]
    assert ent.current_option == "usb"
    await ent.async_select_option("usb_rel")
    assert client.calls == [("mouse", "usb_rel")]
