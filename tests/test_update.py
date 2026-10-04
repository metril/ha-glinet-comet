"""Tests for the firmware update entity."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet_comet.coordinator import CometDataUpdateCoordinator
from custom_components.glinet_comet.update import CometFirmwareUpdate


class FakeEntry:
    entry_id = "test_entry"
    title = "Comet Test"
    data = {"host": "10.0.0.5"}
    options: dict = {}


class FakeHass:
    def async_create_background_task(self, coro, name):
        return None


def make(data):
    coord = CometDataUpdateCoordinator(FakeHass(), FakeEntry(), object())
    coord.data = data
    return CometFirmwareUpdate(coord, coord.entry)


DATA = {
    "glinet": {
        "upgrade_version": {"version": "V1.9.1"},
        "upgrade_compare": {
            "local_version": "V1.9.1",
            "server_version": "V2.0.0",
            "release_note": "## New\n\n  Stuff   here",
            "beta_version": "V2.1b",
        },
    }
}


def test_versions_and_notes():
    e = make(DATA)
    assert e._attr_unique_id == "test_entry_firmware"
    assert e.installed_version == "V1.9.1"
    assert e.latest_version == "V2.0.0"
    assert e.release_summary == "## New Stuff here"
    assert e.extra_state_attributes == {"beta_version": "V2.1b"}
    assert e.in_progress is False
    assert e._ws_backed is False


def test_latest_falls_back_to_installed_and_empty():
    e = make({"glinet": {"upgrade_version": {"version": "V1.9.1"}}})
    assert e.latest_version == "V1.9.1"
    assert e.release_summary is None
    assert e.extra_state_attributes is None
    e = make({})
    assert e.installed_version is None


def test_no_install_support():
    # Install happens from the Comet's own UI; no async_install override.
    assert "async_install" not in CometFirmwareUpdate.__dict__
