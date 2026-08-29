"""Tests for the switch platform, focused on the MSD-connected optimistic update."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.switch import (
    CometMsdConnectedSwitch,
    _set_msd_drive_connected,
)
from homeassistant.exceptions import HomeAssistantError


class FakeClient:
    """Records set_msd_connected calls; scriptable failure."""

    def __init__(self) -> None:
        self.calls: list[bool] = []
        self._raise: Exception | None = None

    def fail_next(self, exc: Exception) -> None:
        self._raise = exc

    async def set_msd_connected(self, connected: bool) -> None:
        self.calls.append(connected)
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
    pass


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
