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
        self.unload_callbacks: list = []

    def async_on_unload(self, fn) -> None:
        self.unload_callbacks.append(fn)


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


class RecordingAddEntities:
    """Tracks every `async_add_entities` batch, so dedupe tests can assert
    both "no duplicate entity" and "no extra batch was even added"."""

    def __init__(self) -> None:
        self.batches: list[list] = []
        self.added: list = []

    def __call__(self, entities) -> None:
        entities = list(entities)
        self.batches.append(entities)
        self.added.extend(entities)


async def _setup_recording(data: dict) -> tuple[CometDataUpdateCoordinator, RecordingAddEntities]:
    coord = make_coordinator(data)
    hass = FakeHass()
    hass.data[DOMAIN] = {coord.entry.entry_id: {"coordinator": coord}}
    add = RecordingAddEntities()
    await async_setup_entry(hass, coord.entry, add)
    return coord, add


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


# --- Dynamic GPIO add: channels can appear after setup, without a reload ---


@pytest.mark.asyncio
async def test_gpio_inputs_added_dynamically_when_model_appears():
    coord, add = await _setup_recording({})
    assert [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)] == []
    batches_before = len(add.batches)

    coord.data = _load("state_gpio.json")
    coord.async_update_listeners()

    gpio_inputs = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert {e._channel for e in gpio_inputs} == {"in_1", "in_2"}
    assert len(add.batches) == batches_before + 1


@pytest.mark.asyncio
async def test_gpio_inputs_not_duplicated_on_same_model_object():
    data = _load("state_gpio.json")
    coord, add = await _setup_recording(data)
    gpio_inputs = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert {e._channel for e in gpio_inputs} == {"in_1", "in_2"}
    batches_before = len(add.batches)

    coord.async_update_listeners()  # same gpio_model object, nothing changed

    assert len(add.batches) == batches_before  # async_add_entities not called again
    gpio_inputs_after = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert len(gpio_inputs_after) == 2  # no duplicates


@pytest.mark.asyncio
async def test_gpio_input_channel_vanishing_leaves_entity_unavailable_not_removed():
    data = _load("state_gpio.json")
    coord, add = await _setup_recording(data)
    gpio_inputs = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    in_1 = next(e for e in gpio_inputs if e._channel == "in_1")
    assert in_1.available is True

    new_data = dict(data)
    new_data["gpio_model"] = {
        "inputs": {"in_2": {}},
        "outputs": data["gpio_model"]["outputs"],
    }
    new_data["gpio"] = {
        "inputs": {"in_2": data["gpio"]["inputs"]["in_2"]},
        "outputs": data["gpio"]["outputs"],
    }
    coord.data = new_data
    batches_before = len(add.batches)

    coord.async_update_listeners()

    gpio_inputs_after = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert len(gpio_inputs_after) == 2  # in_1's entity is never removed
    assert len(add.batches) == batches_before  # no new channel to add
    assert in_1.available is False  # channel gone -> not online -> unavailable


@pytest.mark.asyncio
async def test_gpio_inputs_fresh_but_equal_model_adds_nothing():
    """A new dict object with identical content must not re-add entities --
    proves the per-channel `set` dedupes, not just the object-identity guard."""
    data = _load("state_gpio.json")
    coord, add = await _setup_recording(data)
    batches_before = len(add.batches)

    coord.data = dict(data) | {
        "gpio_model": json.loads(json.dumps(data["gpio_model"])),
    }
    coord.async_update_listeners()

    gpio_inputs = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert len(gpio_inputs) == 2  # still just the original two
    assert len(add.batches) == batches_before  # nothing new, so no add_entities call


@pytest.mark.asyncio
async def test_gpio_listener_removed_on_unload_stops_further_adds():
    """The dynamic-add listener is registered via `entry.async_on_unload`;
    invoking the stored remover (as HA does on unload) must stop it from
    reacting to further coordinator updates."""
    coord, add = await _setup_recording({})
    assert len(coord.entry.unload_callbacks) == 1
    remove = coord.entry.unload_callbacks[0]
    remove()

    coord.data = _load("state_gpio.json")  # a brand-new gpio_model
    coord.async_update_listeners()

    gpio_inputs = [e for e in add.added if isinstance(e, CometGpioInputBinarySensor)]
    assert gpio_inputs == []  # listener was removed -- nothing added
