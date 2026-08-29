"""Tests for the button platform's GPIO pulse buttons."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.button import CometGpioPulseButton, async_setup_entry
from custom_components.glinet_comet.const import DOMAIN
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class FakeClient:
    """Records gpio_pulse calls; scriptable failure."""

    def __init__(self) -> None:
        self.gpio_pulse_calls: list[tuple[str, float]] = []
        self._raise: Exception | None = None

    def fail_next(self, exc: Exception) -> None:
        self._raise = exc

    async def gpio_pulse(self, channel: str, delay: float = 0) -> None:
        self.gpio_pulse_calls.append((channel, delay))
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


def make_pulse_button(
    client: FakeClient, data: dict, channel: str = "out_pulse", delay: float = 0.5
) -> tuple[CometDataUpdateCoordinator, CometGpioPulseButton]:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = data
    entity = CometGpioPulseButton(coord, coord.entry, channel, delay)
    return coord, entity


def test_gpio_pulse_button_shape():
    data = _load_fixture("state_gpio.json")
    coord, entity = make_pulse_button(FakeClient(), data)

    assert entity._attr_unique_id == "test_entry_gpio_pulse_out_pulse"
    assert entity._attr_name == "Out Pulse"
    assert entity.available is True


@pytest.mark.asyncio
async def test_gpio_pulse_button_press_calls_client_with_delay():
    client = FakeClient()
    coord, entity = make_pulse_button(client, _load_fixture("state_gpio.json"))

    await entity.async_press()

    assert client.gpio_pulse_calls == [("out_pulse", 0.5)]


@pytest.mark.asyncio
async def test_gpio_pulse_button_error_raises_home_assistant_error():
    client = FakeClient()
    client.fail_next(CometApiError("busy", status=500))
    coord, entity = make_pulse_button(client, _load_fixture("state_gpio.json"))

    with pytest.raises(HomeAssistantError):
        await entity.async_press()


async def _setup(data: dict) -> list:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    hass = FakeHass()
    hass.data[DOMAIN] = {coord.entry.entry_id: {"coordinator": coord}}
    added: list = []
    await async_setup_entry(hass, coord.entry, added.extend)
    return added


@pytest.mark.asyncio
async def test_setup_entry_creates_exactly_one_pulse_button_from_gpio_fixture():
    added = await _setup(_load_fixture("state_gpio.json"))
    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons) == 1
    assert pulse_buttons[0]._channel == "out_pulse"
    assert pulse_buttons[0]._attr_name == "Out Pulse"
    assert pulse_buttons[0]._delay == 0.5


@pytest.mark.asyncio
async def test_setup_entry_creates_no_pulse_buttons_from_live_fixture():
    added = await _setup(_load_fixture("state_live.json"))
    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert pulse_buttons == []
