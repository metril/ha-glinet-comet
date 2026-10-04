"""Tests for the switch platform, focused on the MSD-connected optimistic update."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

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
        self.unload_callbacks: list = []

    def async_on_unload(self, fn) -> None:
        self.unload_callbacks.append(fn)


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
    assert entity.name == "Relay 1"
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
    coord.entry.runtime_data = SimpleNamespace(coordinator=coord, client=None)
    added: list = []
    await async_setup_entry(hass, coord.entry, added.extend)
    return added


@pytest.mark.asyncio
async def test_setup_entry_creates_exactly_one_gpio_switch_from_gpio_fixture():
    added = await _setup(_load_fixture("state_gpio.json"))
    gpio_switches = [e for e in added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches) == 1
    assert gpio_switches[0]._channel == "out_switch"
    assert gpio_switches[0].name == "Relay 1"


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
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    hass = FakeHass()
    coord.entry.runtime_data = SimpleNamespace(coordinator=coord, client=None)
    add = RecordingAddEntities()
    await async_setup_entry(hass, coord.entry, add)
    return coord, add


# --- Dynamic GPIO add: channels can appear after setup, without a reload ---


@pytest.mark.asyncio
async def test_gpio_switches_added_dynamically_when_model_appears():
    coord, add = await _setup_recording({})
    assert [e for e in add.added if isinstance(e, CometGpioSwitch)] == []
    batches_before = len(add.batches)

    coord.data = _load_fixture("state_gpio.json")
    coord.async_update_listeners()

    gpio_switches = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert {e._channel for e in gpio_switches} == {"out_switch"}
    assert len(add.batches) == batches_before + 1


@pytest.mark.asyncio
async def test_gpio_switches_not_duplicated_on_same_model_object():
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    gpio_switches = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert {e._channel for e in gpio_switches} == {"out_switch"}
    batches_before = len(add.batches)

    coord.async_update_listeners()  # same gpio_model object, nothing changed

    assert len(add.batches) == batches_before  # async_add_entities not called again
    gpio_switches_after = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches_after) == 1


@pytest.mark.asyncio
async def test_gpio_switch_channel_vanishing_leaves_entity_unavailable_not_removed():
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    gpio_switches = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    out_switch = next(e for e in gpio_switches if e._channel == "out_switch")
    assert out_switch.available is True

    new_data = dict(data)
    new_data["gpio_model"] = {
        "inputs": data["gpio_model"]["inputs"],
        "outputs": {},
    }
    new_data["gpio"] = {"inputs": data["gpio"]["inputs"], "outputs": {}}
    coord.data = new_data
    batches_before = len(add.batches)

    coord.async_update_listeners()

    gpio_switches_after = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches_after) == 1  # out_switch's entity is never removed
    assert len(add.batches) == batches_before  # no new channel to add
    assert out_switch.available is False  # channel gone -> not online -> unavailable


@pytest.mark.asyncio
async def test_gpio_switches_fresh_but_equal_model_adds_nothing():
    """A new dict object with identical content must not re-add entities --
    proves the per-channel `set` dedupes, not just the object-identity guard."""
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    batches_before = len(add.batches)

    coord.data = dict(data) | {
        "gpio_model": json.loads(json.dumps(data["gpio_model"])),
    }
    coord.async_update_listeners()

    gpio_switches = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert len(gpio_switches) == 1  # still just the original one
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

    coord.data = _load_fixture("state_gpio.json")  # a brand-new gpio_model
    coord.async_update_listeners()

    gpio_switches = [e for e in add.added if isinstance(e, CometGpioSwitch)]
    assert gpio_switches == []  # listener was removed -- nothing added
