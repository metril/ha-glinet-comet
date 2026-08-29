"""Tests for the switch platform, focused on the MSD-connected optimistic update."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.const import DOMAIN
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.switch import (
    CometGpioSwitch,
    CometMsdConnectedSwitch,
    _set_msd_drive_connected,
    async_setup_entry,
)
from homeassistant.exceptions import HomeAssistantError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class FakeClient:
    """Records set_msd_connected/gpio_switch calls; scriptable failure."""

    def __init__(self) -> None:
        self.calls: list[bool] = []
        self.gpio_switch_calls: list[tuple[str, bool]] = []
        self._raise: Exception | None = None

    def fail_next(self, exc: Exception) -> None:
        self._raise = exc

    async def set_msd_connected(self, connected: bool) -> None:
        self.calls.append(connected)
        if self._raise is not None:
            exc, self._raise = self._raise, None
            raise exc

    async def gpio_switch(self, channel: str, state: bool) -> None:
        self.gpio_switch_calls.append((channel, state))
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
        self.data: dict = {}


def make_switch(
    client: FakeClient, data: dict
) -> tuple[CometDataUpdateCoordinator, CometMsdConnectedSwitch]:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = data
    entity = CometMsdConnectedSwitch(coord, coord.entry)
    return coord, entity


def test_set_msd_drive_connected_helper_uses_setdefault():
    data: dict = {}
    _set_msd_drive_connected(data, True)
    assert data == {"msd": {"drive": {"connected": True}}}

    data2 = {"msd": {"enabled": True}}
    _set_msd_drive_connected(data2, False)
    assert data2 == {"msd": {"enabled": True, "drive": {"connected": False}}}


@pytest.mark.asyncio
async def test_turn_on_connects_and_applies_optimistic_update():
    client = FakeClient()
    coord, entity = make_switch(
        client, {"msd": {"drive": {"connected": False, "image": "ubuntu.iso"}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = lambda data: pushed.append(dict(data))

    await entity.async_turn_on()

    assert client.calls == [True]
    assert coord.data["msd"]["drive"]["connected"] is True
    assert pushed and pushed[-1]["msd"]["drive"]["connected"] is True


@pytest.mark.asyncio
async def test_turn_on_refuses_without_an_image_selected():
    client = FakeClient()
    coord, entity = make_switch(
        client, {"msd": {"drive": {"connected": False, "image": None}}}
    )

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert client.calls == []  # never reached the device


@pytest.mark.asyncio
async def test_turn_off_disconnects_and_applies_optimistic_update():
    client = FakeClient()
    coord, entity = make_switch(
        client, {"msd": {"drive": {"connected": True, "image": "ubuntu.iso"}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = lambda data: pushed.append(dict(data))

    await entity.async_turn_off()

    assert client.calls == [False]
    assert coord.data["msd"]["drive"]["connected"] is False
    assert pushed and pushed[-1]["msd"]["drive"]["connected"] is False


@pytest.mark.asyncio
async def test_turn_on_error_raises_and_skips_optimistic_update():
    client = FakeClient()
    client.fail_next(CometApiError("busy", status=500))
    coord, entity = make_switch(
        client, {"msd": {"drive": {"connected": False, "image": "ubuntu.iso"}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = lambda data: pushed.append(dict(data))

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    assert coord.data["msd"]["drive"]["connected"] is False
    assert pushed == []


def test_is_on_reflects_msd_drive_connected():
    coord, entity = make_switch(
        FakeClient(), {"msd": {"drive": {"connected": True, "image": "x.iso"}}}
    )
    assert entity.is_on is True


def test_available_requires_an_image_selected():
    coord, entity = make_switch(
        FakeClient(), {"msd": {"drive": {"connected": False, "image": None}}}
    )
    assert entity.available is False

    coord.data["msd"]["drive"]["image"] = "x.iso"
    assert entity.available is True


def make_gpio_switch(
    client: FakeClient, data: dict, channel: str = "out_switch"
) -> tuple[CometDataUpdateCoordinator, CometGpioSwitch]:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = data
    entity = CometGpioSwitch(coord, coord.entry, channel)
    return coord, entity


def test_gpio_switch_shape():
    data = _load_fixture("state_gpio.json")
    coord, entity = make_gpio_switch(FakeClient(), data)

    assert entity._attr_unique_id == "test_entry_gpio_out_out_switch"
    assert entity._attr_name == "Relay 1"
    assert entity.is_on is True
    assert entity.available is True


@pytest.mark.asyncio
async def test_gpio_switch_turn_on_calls_client():
    client = FakeClient()
    coord, entity = make_gpio_switch(client, _load_fixture("state_gpio.json"))

    await entity.async_turn_on()

    assert client.gpio_switch_calls == [("out_switch", True)]


@pytest.mark.asyncio
async def test_gpio_switch_turn_off_calls_client():
    client = FakeClient()
    coord, entity = make_gpio_switch(client, _load_fixture("state_gpio.json"))

    await entity.async_turn_off()

    assert client.gpio_switch_calls == [("out_switch", False)]


@pytest.mark.asyncio
async def test_gpio_switch_error_raises_home_assistant_error():
    client = FakeClient()
    client.fail_next(CometApiError("busy", status=500))
    coord, entity = make_gpio_switch(client, _load_fixture("state_gpio.json"))

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


async def _setup(data: dict) -> list:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    hass = FakeHass()
    hass.data[DOMAIN] = {coord.entry.entry_id: {"coordinator": coord}}
    added: list = []
    await async_setup_entry(hass, coord.entry, added.extend)
    return added


@pytest.mark.asyncio
async def test_setup_entry_creates_exactly_one_gpio_switch_from_gpio_fixture():
    added = await _setup(_load_fixture("state_gpio.json"))
    gpio_switches = [e for e in added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches) == 1
    assert gpio_switches[0]._channel == "out_switch"
    assert gpio_switches[0]._attr_name == "Relay 1"


@pytest.mark.asyncio
async def test_setup_entry_creates_only_the_three_existing_switches_from_live_fixture():
    added = await _setup(_load_fixture("state_live.json"))
    assert len(added) == 3
    assert all(not isinstance(e, CometGpioSwitch) for e in added)


@pytest.mark.asyncio
async def test_setup_entry_skips_non_dict_gpio_output_config():
    """A non-dict member in gpio_model.outputs (odd firmware) must not crash setup."""
    data = {
        "gpio_model": {
            "outputs": {
                "bad": None,
                "out_switch": {"switch": True},
            }
        },
        "gpio": {"outputs": {"out_switch": {"online": True, "state": True}}},
        "gpio_labels": {},
    }

    added = await _setup(data)

    gpio_switches = [e for e in added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches) == 1
    assert gpio_switches[0]._channel == "out_switch"
