"""Tests for diagnostics redaction (host must be redacted alongside credentials)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

import pytest

from custom_components.glinet_comet.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


class FakeCoordinator:
    def __init__(self, data: dict) -> None:
        self.data = data


class FakeEntry:
    def __init__(self) -> None:
        self.data = {
            "host": "10.0.0.5",
            "username": "admin",
            "password": "hunter2",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        }
        self.options: dict = {}


class FakeHass:
    def __init__(self, entry: FakeEntry, coordinator: FakeCoordinator) -> None:
        self.data = {}
        entry.runtime_data = SimpleNamespace(coordinator=coordinator, client=None)


def test_host_is_in_to_redact():
    assert "host" in TO_REDACT


@pytest.mark.asyncio
async def test_host_and_credentials_are_redacted_in_entry_data():
    entry = FakeEntry()
    entry.entry_id = "test_entry"
    coordinator = FakeCoordinator({"info": {"system": {"platform": {"serial": "SN1"}}}})
    hass = FakeHass(entry, coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["data"]["host"] == "**REDACTED**"
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["data"]["totp_secret"] == "**REDACTED**"
    assert result["entry"]["data"]["username"] == "**REDACTED**"


@pytest.mark.asyncio
async def test_coordinator_data_serial_is_redacted():
    entry = FakeEntry()
    entry.entry_id = "test_entry"
    coordinator = FakeCoordinator({"info": {"system": {"platform": {"serial": "SN1"}}}})
    hass = FakeHass(entry, coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"]["info"]["system"]["platform"]["serial"] == "**REDACTED**"
