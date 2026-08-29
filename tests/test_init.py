"""Tests for GL.iNet Comet setup/teardown (__init__.py)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet import async_unload_entry
from custom_components.glinet_comet.const import DOMAIN


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
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    result = await async_unload_entry(hass, entry)

    assert result is False
    assert coordinator.stopped is False
    assert client.logged_out is False
    assert entry.entry_id in hass.data[DOMAIN]  # never popped


@pytest.mark.asyncio
async def test_unload_entry_runs_teardown_when_platforms_unload_cleanly():
    hass = FakeHass(unload_ok=True)
    entry = FakeEntry()
    coordinator = FakeCoordinator()
    client = FakeClient()
    hass.data[DOMAIN] = {entry.entry_id: {"coordinator": coordinator, "client": client}}

    result = await async_unload_entry(hass, entry)

    assert result is True
    assert coordinator.stopped is True
    assert client.logged_out is True
    assert entry.entry_id not in hass.data[DOMAIN]
