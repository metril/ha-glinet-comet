"""Tests for the device-scoped HID services."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.glinet_comet import services
from custom_components.glinet_comet.api import CometApiError
from custom_components.glinet_comet.const import DOMAIN


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple] = []

    async def type_text(self, text, keymap):
        self.calls.append(("type_text", text, keymap))
        if self.error:
            raise self.error

    async def send_shortcut(self, keys):
        self.calls.append(("send_shortcut", keys))
        if self.error:
            raise self.error


class FakeServices:
    def __init__(self) -> None:
        self.handlers: dict = {}

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, schema=None):
        self.handlers[(domain, service)] = handler


def _setup(
    monkeypatch,
    *,
    known=("dev1", "other"),
    comet_devices=("dev1",),
    client=None,
    loaded=True,
):
    client = client or FakeClient()
    entry = SimpleNamespace(
        entry_id="e1", runtime_data=SimpleNamespace(client=client, coordinator=None)
    )
    hass = MagicMock()
    hass.services = FakeServices()
    hass.config_entries.async_entries = lambda domain: [entry] if domain == DOMAIN else []
    hass.config_entries.async_loaded_entries = lambda domain: (
        [entry] if domain == DOMAIN and loaded else []
    )
    registry = MagicMock()
    registry.async_get = lambda did: SimpleNamespace(id=did) if did in known else None
    monkeypatch.setattr(services.dr, "async_get", lambda h: registry)
    monkeypatch.setattr(
        services.dr,
        "async_entries_for_config_entry",
        lambda reg, eid: [SimpleNamespace(id=d) for d in comet_devices],
    )
    services.async_setup_services(hass)
    return hass, client


def _call(hass, service, **data):
    return hass.services.handlers[(DOMAIN, service)](SimpleNamespace(data=data))


@pytest.mark.asyncio
async def test_unknown_device(monkeypatch):
    hass, _ = _setup(monkeypatch)
    with pytest.raises(ServiceValidationError) as ei:
        await _call(hass, services.SERVICE_TYPE_TEXT, device_id="nope", text="a", keymap="en")
    assert ei.value.translation_key == "device_not_found"
    assert ei.value.translation_placeholders == {"device_id": "nope"}


@pytest.mark.asyncio
async def test_foreign_device(monkeypatch):
    hass, client = _setup(monkeypatch)
    with pytest.raises(ServiceValidationError) as ei:
        await _call(hass, services.SERVICE_SEND_SHORTCUT, device_id="other", keys="A")
    assert ei.value.translation_key == "not_comet_device"
    assert client.calls == []


@pytest.mark.asyncio
async def test_entry_not_loaded(monkeypatch):
    hass, client = _setup(monkeypatch, loaded=False)
    with pytest.raises(ServiceValidationError) as ei:
        await _call(hass, services.SERVICE_SEND_SHORTCUT, device_id="dev1", keys="A")
    assert ei.value.translation_key == "entry_not_loaded"
    assert client.calls == []


@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    hass, client = _setup(monkeypatch)
    await _call(hass, services.SERVICE_TYPE_TEXT, device_id="dev1", text="hi", keymap="en")
    await _call(hass, services.SERVICE_SEND_SHORTCUT, device_id="dev1", keys="A,B")
    assert client.calls == [("type_text", "hi", "en"), ("send_shortcut", "A,B")]


@pytest.mark.asyncio
async def test_comet_error_becomes_ha_error(monkeypatch):
    hass, _ = _setup(monkeypatch, client=FakeClient(CometApiError("boom", status=500)))
    with pytest.raises(HomeAssistantError) as ei:
        await _call(hass, services.SERVICE_SEND_SHORTCUT, device_id="dev1", keys="A")
    assert ei.value.translation_key == "command_failed"
    assert "boom" in ei.value.translation_placeholders["error"]
