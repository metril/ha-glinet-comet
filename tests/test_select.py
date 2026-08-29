"""Tests for the MSD Image select entity (optimistic state after writes)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

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
    entity = CometMsdImageSelect(coord, coord.entry)
    return coord, entity


@pytest.mark.asyncio
async def test_select_image_sets_optimistic_drive_image_and_pushes():
    client = FakeClient()
    coord, entity = make_select(
        client, {"msd": {"drive": {"connected": False, "image": None}}}
    )
    pushed: list[dict] = []
    coord.async_set_updated_data = lambda data: pushed.append(dict(data))

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
    coord.async_set_updated_data = lambda data: None

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
    coord.async_set_updated_data = lambda data: None

    await entity.async_select_option(MSD_IMAGE_NONE)

    assert client.calls == [
        ("set_msd_params", (), {"image": "", "cdrom": True, "rw": False})
    ]
    assert coord.data["msd"]["drive"]["image"] is None


@pytest.mark.asyncio
async def test_select_image_uses_setdefault_when_msd_drive_absent():
    client = FakeClient()
    coord, entity = make_select(client, {})
    coord.async_set_updated_data = lambda data: None

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
    coord.async_set_updated_data = lambda data: pushed.append(dict(data))

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
