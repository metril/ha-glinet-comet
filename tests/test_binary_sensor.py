"""Tests for the binary_sensor platform: ATX HDD activity and GPIO inputs."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet.binary_sensor import (
    BINARY_SENSORS,
    CometAtxBinarySensor,
    CometGpioInputBinarySensor,
    async_setup_entry,
)
from custom_components.glinet_comet.const import DOMAIN
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class FakeClient:
    pass


class FakeEntry:
    def __init__(self) -> None:
        self.entry_id = "test_entry"
        self.title = "Comet Test"
        self.data = {"host": "10.0.0.5"}
        self.options: dict = {}


class FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}


def make_coordinator(data: dict) -> CometDataUpdateCoordinator:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    return coord


def _description(key: str):
    for description in BINARY_SENSORS:
        if description.key == key:
            return description
    raise AssertionError(f"no description for {key}")


async def _setup(data: dict) -> list:
    coord = make_coordinator(data)
    hass = FakeHass()
    hass.data[DOMAIN] = {coord.entry.entry_id: {"coordinator": coord}}
    added: list = []
    await async_setup_entry(hass, coord.entry, added.extend)
    return added


def test_atx_hdd_activity_description_is_atx_gated():
    description = _description("atx_hdd_activity")
    assert description.requires_atx is True


def test_atx_hdd_activity_value_when_led_lit():
    data = _load("state_gpio.json")
    data["atx"] = {"enabled": True, "leds": {"hdd": True, "power": False}}
    coord = make_coordinator(data)
    entity = CometAtxBinarySensor(coord, coord.entry, _description("atx_hdd_activity"))
    assert entity.is_on is True


def test_atx_hdd_activity_value_when_led_off():
    data = _load("state_gpio.json")
    data["atx"] = {"enabled": True, "leds": {"hdd": False, "power": False}}
    coord = make_coordinator(data)
    entity = CometAtxBinarySensor(coord, coord.entry, _description("atx_hdd_activity"))
    assert entity.is_on is False


def test_atx_hdd_activity_unavailable_when_atx_not_enabled():
    data = _load("state_gpio.json")
    data["atx"] = {"enabled": False, "leds": {"hdd": False, "power": False}}
    coord = make_coordinator(data)
    coord._state["atx"] = {"enabled": False}
    entity = CometAtxBinarySensor(coord, coord.entry, _description("atx_hdd_activity"))
    assert entity.available is False


def test_atx_hdd_activity_available_when_atx_enabled():
    data = _load("state_gpio.json")
    data["atx"] = {"enabled": True, "leds": {"hdd": False, "power": False}}
    coord = make_coordinator(data)
    coord._state["atx"] = {"enabled": True}
    entity = CometAtxBinarySensor(coord, coord.entry, _description("atx_hdd_activity"))
    assert entity.available is True


def test_gpio_input_binary_sensor_shape():
    data = _load("state_gpio.json")
    coord = make_coordinator(data)

    in_1 = CometGpioInputBinarySensor(coord, coord.entry, "in_1")
    assert in_1._attr_unique_id == "test_entry_gpio_in_in_1"
    assert in_1._attr_name == "Door Sensor"
    assert in_1.is_on is True
    assert in_1.available is True

    in_2 = CometGpioInputBinarySensor(coord, coord.entry, "in_2")
    assert in_2._attr_unique_id == "test_entry_gpio_in_in_2"
    assert in_2._attr_name == "In 2"
    assert in_2.is_on is False
    assert in_2.available is False


@pytest.mark.asyncio
async def test_setup_entry_creates_two_gpio_inputs_from_gpio_fixture():
    added = await _setup(_load("state_gpio.json"))
    gpio_inputs = [e for e in added if isinstance(e, CometGpioInputBinarySensor)]
    assert {e._channel for e in gpio_inputs} == {"in_1", "in_2"}


@pytest.mark.asyncio
async def test_setup_entry_creates_no_gpio_inputs_from_live_fixture():
    added = await _setup(_load("state_live.json"))
    gpio_inputs = [e for e in added if isinstance(e, CometGpioInputBinarySensor)]
    assert gpio_inputs == []
