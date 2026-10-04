"""Tests for GL.iNet Comet setup/teardown (__init__.py)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import custom_components.glinet_comet as integration
from custom_components.glinet_comet import (
    CONFIG_SCHEMA,
    CometRuntimeData,
    async_setup,
    async_unload_entry,
)
from custom_components.glinet_comet.const import DOMAIN
from custom_components.glinet_comet.services import (
    SERVICE_SEND_SHORTCUT,
    SERVICE_TYPE_TEXT,
)


class FakeConfigEntries:
    def __init__(self, unload_ok: bool) -> None:
        self._unload_ok = unload_ok
        self.unload_calls = 0

    async def async_unload_platforms(self, entry, platforms) -> bool:
        self.unload_calls += 1
        return self._unload_ok


class FakeServices:
    def __init__(self) -> None:
        self._registered: set[tuple[str, str]] = set()

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self._registered

    def async_register(self, domain, service, handler, schema=None) -> None:
        self._registered.add((domain, service))

    def async_remove(self, domain: str, service: str) -> None:
        self._registered.discard((domain, service))


class FakeHass:
    def __init__(self, unload_ok: bool) -> None:
        self.data: dict = {}
        self.config_entries = FakeConfigEntries(unload_ok)
        self.services = FakeServices()


class FakeEntry:
    entry_id = "test_entry"
    runtime_data = None


class FakeCoordinator:
    def __init__(self) -> None:
        self.stopped = False

    async def async_stop(self) -> None:
        self.stopped = True


class FakeClient:
    def __init__(self) -> None:
        self.logged_out = False

    async def async_logout(self) -> None:
        self.logged_out = True


@pytest.mark.asyncio
async def test_unload_entry_skips_teardown_when_platforms_fail_to_unload():
    """Regression: teardown used to run unconditionally.

    If `async_unload_platforms` returns False, HA still holds live entities
    pointing at the coordinator/client -- tearing those down anyway (WS
    stop, logout, dropping hass.data, unloading services) would leave those
    entities broken while HA still thinks the entry is loaded.
    """
    hass = FakeHass(unload_ok=False)
    entry = FakeEntry()
    coordinator = FakeCoordinator()
    client = FakeClient()
    entry.runtime_data = CometRuntimeData(coordinator, client)

    result = await async_unload_entry(hass, entry)

    assert result is False
    assert coordinator.stopped is False
    assert client.logged_out is False


@pytest.mark.asyncio
async def test_unload_entry_runs_teardown_when_platforms_unload_cleanly():
    hass = FakeHass(unload_ok=True)
    entry = FakeEntry()
    coordinator = FakeCoordinator()
    client = FakeClient()
    entry.runtime_data = CometRuntimeData(coordinator, client)

    result = await async_unload_entry(hass, entry)

    assert result is True
    assert coordinator.stopped is True
    assert client.logged_out is True
    assert hass.data == {}


@pytest.mark.asyncio
async def test_async_setup_registers_services_and_unload_keeps_them():
    hass = FakeHass(unload_ok=True)
    assert await async_setup(hass, {}) is True
    for svc in (SERVICE_TYPE_TEXT, SERVICE_SEND_SHORTCUT):
        assert hass.services.has_service(DOMAIN, svc)

    entry = FakeEntry()
    entry.runtime_data = CometRuntimeData(FakeCoordinator(), FakeClient())
    assert await async_unload_entry(hass, entry) is True
    for svc in (SERVICE_TYPE_TEXT, SERVICE_SEND_SHORTCUT):
        assert hass.services.has_service(DOMAIN, svc)


def test_no_update_listener_or_hass_data_usage():
    assert not hasattr(integration, "_async_update_listener")
    assert CONFIG_SCHEMA is not None
    import inspect

    assert "add_update_listener" not in inspect.getsource(integration)
    assert "hass.data" not in inspect.getsource(integration)
