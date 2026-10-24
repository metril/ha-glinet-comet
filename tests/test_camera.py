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
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


class FakeVideo:
    def __init__(self, ok=False) -> None:
        self.ok = ok
        self.wakes = 0

    async def wake(self):
        self.wakes += 1
        return self.ok


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
    coord.video = FakeVideo()
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


ONLINE = {"streamer": {"streamer": {"source": {"online": True}}}}


@pytest.mark.asyncio
async def test_online_failing_snapshot_does_not_wake():
    coord, cam = make(FakeClient(exc=CometApiError("503", status=503)))
    coord.data = ONLINE
    assert await cam.async_camera_image() is None
    assert coord.video.wakes == 0


@pytest.mark.asyncio
async def test_offline_503_wakes_and_retries_once():
    client = FakeClient(exc=CometApiError("503", status=503))
    coord, cam = make(client)
    coord.video = FakeVideo(ok=True)

    async def snap():
        client.calls += 1
        if client.calls == 1:
            raise CometApiError("503", status=503)
        return JPEG

    client.get_snapshot = snap
    assert await cam.async_camera_image() == JPEG
    assert coord.video.wakes == 1
    assert client.calls == 2


@pytest.mark.asyncio
async def test_wake_false_returns_none():
    client = FakeClient(result=None)
    coord, cam = make(client)
    assert await cam.async_camera_image() is None
    assert coord.video.wakes == 1
    assert client.calls == 1
