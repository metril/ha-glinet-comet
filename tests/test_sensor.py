"""Tests for the sensor platform's descriptions."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet_comet.sensor import SENSORS, CometSensor, async_setup_entry
from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from test_binary_sensor import FakeClient, FakeEntry, FakeHass


def _coord(data):
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), FakeClient())
    coord.data = data
    return coord


def _sensor(key, data):
    desc = next(d for d in SENSORS if d.key == key)
    coord = _coord(data)
    return desc, CometSensor(coord, coord.entry, desc)


NET = {
    "glinet": {
        "network": {
            "config": {
                "mac_address": "aa:bb:cc:dd:ee:ff",
                "gateway": "10.0.0.1",
                "is_dhcp": True,
            }
        }
    }
}


def test_network_sensors_values_and_defaults():
    for key, expected in (
        ("mac_address", "aa:bb:cc:dd:ee:ff"),
        ("gateway", "10.0.0.1"),
        ("dhcp", "DHCP"),
    ):
        desc, ent = _sensor(key, NET)
        assert ent.native_value == expected
        assert desc.entity_registry_enabled_default is False
        assert desc.entity_category == "diagnostic" or desc.entity_category is not None
        assert desc.ws_backed is False
        assert ent._attr_unique_id == "test_entry_" + key


def test_dhcp_static():
    data = {"glinet": {"network": {"config": {"is_dhcp": False}}}}
    assert _sensor("dhcp", data)[1].native_value == "Static"
    assert _sensor("dhcp", {})[1].native_value is None


def test_captured_fps():
    data = {"streamer": {"streamer": {"source": {"captured_fps": 60}}}}
    desc, ent = _sensor("captured_fps", data)
    assert ent.native_value == 60
    assert desc.native_unit_of_measurement == "fps"
    assert desc.state_class == "measurement"
    assert _sensor("captured_fps", {"streamer": {"streamer": None}})[1].native_value is None
    assert _sensor("captured_fps", {})[1].native_value is None


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
HEALTH = {"cpu_percent", "mem_percent", "cpu_temp", "net_rx_rate", "net_tx_rate"}


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


class _Entry:
    entry_id = "test_entry"
    title = "Comet Test"
    data = {"host": "10.0.0.5"}
    options: dict = {}


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}


async def _setup(data: dict) -> list:
    coord = CometDataUpdateCoordinator(_Hass(), _Entry(), object())
    coord.data = data
    hass = _Hass()
    coord.entry.runtime_data = SimpleNamespace(coordinator=coord, client=None)
    added: list = []
    await async_setup_entry(hass, coord.entry, lambda ents: added.extend(ents))
    return added


@pytest.mark.asyncio
async def test_health_sensors_created_with_values():
    added = await _setup(_load("state_live_rm4pe.json"))
    by_key = {e.entity_description.key: e for e in added}
    assert HEALTH <= set(by_key)
    assert isinstance(by_key["cpu_percent"].native_value, (int, float))
    assert by_key["net_rx_rate"].entity_description.entity_registry_enabled_default is False
    assert by_key["cpu_percent"].entity_description.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_health_sensors_skipped_when_unsupported():
    data = _load("state_live_rm4pe.json")
    data["unsupported"] = ["hw", "health"]
    added = await _setup(data)
    keys = {e.entity_description.key for e in added}
    assert not (HEALTH & keys)
    assert "hostname" in keys
