"""Tests for the button platform's GPIO pulse buttons."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

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
        self.unload_callbacks: list = []

    def async_on_unload(self, fn) -> None:
        self.unload_callbacks.append(fn)


class FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}


def make_pulse_button(
    client: FakeClient, data: dict, channel: str = "out_pulse"
) -> tuple[CometDataUpdateCoordinator, CometGpioPulseButton]:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = data
    entity = CometGpioPulseButton(coord, coord.entry, channel)
    return coord, entity


def test_gpio_pulse_button_shape():
    data = _load_fixture("state_gpio.json")
    coord, entity = make_pulse_button(FakeClient(), data)

    assert entity._attr_unique_id == "test_entry_gpio_pulse_out_pulse"
    assert entity.name == "Out Pulse"
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


@pytest.mark.asyncio
async def test_gpio_pulse_button_uses_current_model_delay():
    client = FakeClient()
    coord, entity = make_pulse_button(client, _load_fixture("state_gpio.json"))
    coord.data["gpio_model"]["outputs"]["out_pulse"]["pulse"]["delay"] = 2.5

    await entity.async_press()

    assert client.gpio_pulse_calls == [("out_pulse", 2.5)]


@pytest.mark.asyncio
async def test_gpio_pulse_button_delay_zero_when_channel_vanishes():
    client = FakeClient()
    coord, entity = make_pulse_button(client, _load_fixture("state_gpio.json"))
    del coord.data["gpio_model"]["outputs"]["out_pulse"]

    await entity.async_press()

    assert client.gpio_pulse_calls == [("out_pulse", 0)]


async def _setup(data: dict) -> list:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    hass = FakeHass()
    coord.entry.runtime_data = SimpleNamespace(coordinator=coord, client=None)
    added: list = []
    await async_setup_entry(hass, coord.entry, added.extend)
    return added


@pytest.mark.asyncio
async def test_setup_entry_creates_exactly_one_pulse_button_from_gpio_fixture():
    added = await _setup(_load_fixture("state_gpio.json"))
    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons) == 1
    assert pulse_buttons[0]._channel == "out_pulse"
    assert pulse_buttons[0].name == "Out Pulse"


@pytest.mark.asyncio
async def test_setup_entry_creates_no_pulse_buttons_from_live_fixture():
    added = await _setup(_load_fixture("state_live.json"))
    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert pulse_buttons == []


@pytest.mark.asyncio
async def test_setup_entry_skips_non_dict_gpio_output_config():
    """A non-dict member in gpio_model.outputs (odd firmware) must not crash setup."""
    data = {
        "gpio_model": {
            "outputs": {
                "bad": None,
                "out_pulse": {"switch": False, "pulse": {"delay": 0.5}},
            }
        },
        "gpio": {"outputs": {"out_pulse": {"online": True, "state": False}}},
        "gpio_labels": {},
    }

    added = await _setup(data)

    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons) == 1
    assert pulse_buttons[0]._channel == "out_pulse"


@pytest.mark.asyncio
async def test_setup_entry_skips_pulse_channel_with_zero_max_delay():
    """max_delay: 0 is kvmd's convention for "pulse disabled" on this channel."""
    data = {
        "gpio_model": {
            "outputs": {
                "out_pulse_disabled": {
                    "switch": False,
                    "pulse": {"delay": 0.1, "min_delay": 0, "max_delay": 0},
                },
            }
        },
        "gpio": {"outputs": {"out_pulse_disabled": {"online": True, "state": False}}},
        "gpio_labels": {},
    }

    added = await _setup(data)

    pulse_buttons = [e for e in added if isinstance(e, CometGpioPulseButton)]
    assert pulse_buttons == []


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
async def test_gpio_pulse_buttons_added_dynamically_when_model_appears():
    coord, add = await _setup_recording({})
    assert [e for e in add.added if isinstance(e, CometGpioPulseButton)] == []
    batches_before = len(add.batches)

    coord.data = _load_fixture("state_gpio.json")
    coord.async_update_listeners()

    pulse_buttons = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert {e._channel for e in pulse_buttons} == {"out_pulse"}
    assert len(add.batches) == batches_before + 1


@pytest.mark.asyncio
async def test_gpio_pulse_buttons_not_duplicated_on_same_model_object():
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    pulse_buttons = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert {e._channel for e in pulse_buttons} == {"out_pulse"}
    batches_before = len(add.batches)

    coord.async_update_listeners()  # same gpio_model object, nothing changed

    assert len(add.batches) == batches_before  # async_add_entities not called again
    pulse_buttons_after = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons_after) == 1


@pytest.mark.asyncio
async def test_gpio_pulse_button_channel_vanishing_leaves_entity_unavailable_not_removed():
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    pulse_buttons = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    out_pulse = next(e for e in pulse_buttons if e._channel == "out_pulse")
    assert out_pulse.available is True

    new_data = dict(data)
    new_data["gpio_model"] = {
        "inputs": data["gpio_model"]["inputs"],
        "outputs": {},
    }
    new_data["gpio"] = {"inputs": data["gpio"]["inputs"], "outputs": {}}
    coord.data = new_data
    batches_before = len(add.batches)

    coord.async_update_listeners()

    pulse_buttons_after = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons_after) == 1  # out_pulse's entity is never removed
    assert len(add.batches) == batches_before  # no new channel to add
    assert out_pulse.available is False  # channel gone -> not online -> unavailable


@pytest.mark.asyncio
async def test_gpio_pulse_buttons_fresh_but_equal_model_adds_nothing():
    """A new dict object with identical content must not re-add entities --
    proves the per-channel `set` dedupes, not just the object-identity guard."""
    data = _load_fixture("state_gpio.json")
    coord, add = await _setup_recording(data)
    batches_before = len(add.batches)

    coord.data = dict(data) | {
        "gpio_model": json.loads(json.dumps(data["gpio_model"])),
    }
    coord.async_update_listeners()

    pulse_buttons = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert len(pulse_buttons) == 1  # still just the original one
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

    pulse_buttons = [e for e in add.added if isinstance(e, CometGpioPulseButton)]
    assert pulse_buttons == []  # listener was removed -- nothing added
