"""Tests for ``switch_ports.py`` dynamic setup and ``CometSwitchPortEntity``."""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.entity import CometSwitchPortEntity
from custom_components.glinet_comet.switch_ports import (
    async_setup_switch_port_entities,
)

with open(
    os.path.join(os.path.dirname(__file__), "fixtures", "state_live_rm4pe.json"),
    encoding="utf-8",
) as _f:
    RM4PE = json.load(_f)


class FakeEntry:
    def __init__(self) -> None:
        self.entry_id = "test_entry"
        self.title = "Comet Test"
        self.data = {"host": "10.0.0.5"}
        self.options: dict = {}
        self.unload_callbacks: list = []

    def async_on_unload(self, fn) -> None:
        self.unload_callbacks.append(fn)


class FakeHass:
    pass


def _coord(data: dict) -> CometDataUpdateCoordinator:
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), object())
    coord.data = data
    return coord


def _factory(port):
    return [
        CometSwitchPortEntity(
            coord_holder["c"], coord_holder["c"].entry, port["id"], port["channel"], "video", "Video"
        )
    ]


coord_holder: dict = {}


def test_setup_adds_ports_once_and_no_dupes_on_same_model():
    coord = _coord(copy.deepcopy(RM4PE))
    coord_holder["c"] = coord
    added: list = []
    async_setup_switch_port_entities(coord.entry, coord, added.extend, _factory)

    assert len(added) == 4
    coord.async_update_listeners()
    assert len(added) == 4
    # new model object, same ports: still no dupes; one new port: added once
    data = copy.deepcopy(RM4PE)
    data["switch_model"]["ports"].append({"id": "1.5", "channel": 4, "name": ""})
    coord.data = data
    coord.async_update_listeners()
    coord.async_update_listeners()
    assert len(added) == 5


def test_late_model_adds_ports_and_empty_model_adds_none():
    data = copy.deepcopy(RM4PE)
    data["switch_model"] = {}
    coord = _coord(data)
    coord_holder["c"] = coord
    added: list = []
    async_setup_switch_port_entities(coord.entry, coord, added.extend, _factory)
    assert added == []

    coord.data = copy.deepcopy(RM4PE)
    coord.async_update_listeners()
    assert len(added) == 4


def test_entity_name_live_available_and_unique_id():
    coord = _coord(copy.deepcopy(RM4PE))
    entity = CometSwitchPortEntity(coord, coord.entry, "1.2", 1, "video", "Video")

    assert entity._attr_unique_id == "test_entry_switch_port_1_video"
    assert entity.name == "Port 2 Video"
    assert entity.available is True

    coord.data["switch_model"]["ports"][1]["name"] = "NAS"
    assert entity.name == "NAS Video"

    coord.data["switch_model"] = {"ports": []}
    assert entity.available is False
    assert entity.name == "Port 2 Video"
