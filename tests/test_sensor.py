"""Tests for the sensor platform's descriptions."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet_comet.sensor import SENSORS, CometSensor
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
