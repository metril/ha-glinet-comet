"""Tests for the screen camera entity."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.camera import CometScreenCamera
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator

JPEG = b"\xff\xd8\xff\xe0data"


class FakeClient:
    def __init__(self, result=JPEG, exc=None) -> None:
        self.result = result
        self.exc = exc

    async def get_snapshot(self):
        if self.exc:
            raise self.exc
        return self.result


class FakeEntry:
    entry_id = "test_entry"
    title = "Comet Test"
    data = {"host": "10.0.0.5"}
    options: dict = {}


class FakeHass:
    def async_create_background_task(self, coro, name):
        return None


def make(client):
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), client)
    coord.data = {}
    return coord, CometScreenCamera(coord, coord.entry)


def test_ids():
    _, cam = make(FakeClient())
    assert cam._attr_unique_id == "test_entry_screen"
    assert cam._attr_translation_key == "screen"


@pytest.mark.asyncio
async def test_returns_bytes():
    _, cam = make(FakeClient())
    assert await cam.async_camera_image(640, 480) == JPEG


@pytest.mark.asyncio
async def test_none_on_comet_error():
    _, cam = make(FakeClient(exc=CometApiError("503", status=503)))
    assert await cam.async_camera_image() is None


@pytest.mark.asyncio
async def test_none_when_not_jpeg():
    _, cam = make(FakeClient(result=None))
    assert await cam.async_camera_image() is None


def test_availability_follows_coordinator():
    coord, cam = make(FakeClient())
    coord.last_update_success = True
    assert cam.available is True
    coord.last_update_success = False
    coord.data = {"ws_connected": False}
    assert cam.available is False
    coord.data = {"ws_connected": True}
    assert cam.available is True
