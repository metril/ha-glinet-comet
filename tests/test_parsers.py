"""Tests for ``parsers.py`` field extraction against live-shaped fixtures.

Fixtures (``tests/fixtures/state_live.json`` and
``tests/fixtures/state_streamer_active.json``) are coordinator-state
assemblies of the redacted live dump / CLAUDE.md shapes -- see
``tests/fixtures/README.md``.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet_comet import parsers

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def live() -> dict:
    return _load("state_live.json")


@pytest.fixture(scope="module")
def active() -> dict:
    return _load("state_streamer_active.json")


@pytest.fixture(scope="module")
def gpio() -> dict:
    return _load("state_gpio.json")


# --- Device identity (pre-existing; sanity-check fixtures wire up cleanly) --


def test_device_model(live, active):
    assert parsers.device_model(live) == "RM1PE"
    assert parsers.device_model(active) == "RM1PE"


def test_device_model_empty():
    assert parsers.device_model({}) is None


def test_device_model_empty_string_is_none():
    state = {"info": {"system": {"platform": {"model": ""}}}}
    assert parsers.device_model(state) is None


def test_firmware_version(live):
    assert parsers.firmware_version(live) == "V1.9.1 release1"


def test_firmware_version_empty():
    assert parsers.firmware_version({}) is None


def test_firmware_version_empty_string_is_none():
    state = {"glinet": {"upgrade_version": {"version": ""}}}
    assert parsers.firmware_version(state) is None


def test_serial_empty():
    assert parsers.serial({}) is None


def test_serial_empty_string_is_none():
    assert parsers.serial({"info": {"system": {"platform": {"serial": ""}}}}) is None


def test_mac_address_empty():
    assert parsers.mac_address({}) is None


def test_mac_address_empty_string_is_none():
    state = {"glinet": {"network": {"mac_address": ""}}}
    assert parsers.mac_address(state) is None


# --- ATX ---------------------------------------------------------------


def test_atx_enabled(live):
    assert parsers.atx_enabled(live) is False


def test_atx_enabled_empty():
    assert parsers.atx_enabled({}) is None


def test_atx_enabled_wrong_type():
    assert parsers.atx_enabled({"atx": {"enabled": "yes"}}) is None


def test_atx_busy(live):
    assert parsers.atx_busy(live) is False


def test_atx_busy_empty():
    assert parsers.atx_busy({}) is None


def test_atx_busy_wrong_type():
    assert parsers.atx_busy({"atx": "garbage"}) is None


def test_atx_power_on(live):
    assert parsers.atx_power_on(live) is False


def test_atx_power_on_missing():
    assert parsers.atx_power_on({"atx": {}}) is None


def test_atx_power_on_empty():
    assert parsers.atx_power_on({}) is None


def test_atx_power_on_wrong_type():
    assert parsers.atx_power_on({"atx": {"power": True}}) is None


# --- Video / streamer -------------------------------------------------------


def test_streamer_running_active(active):
    assert parsers.streamer_running(active) is True


def test_streamer_running_null(live):
    assert parsers.streamer_running(live) is False


def test_streamer_running_empty():
    assert parsers.streamer_running({}) is False


def test_streamer_running_wrong_type():
    assert parsers.streamer_running({"streamer": {"streamer": "garbage"}}) is False


def test_hdmi_signal_active(active):
    assert parsers.hdmi_signal(active) is True


def test_hdmi_signal_null(live):
    assert parsers.hdmi_signal(live) is None


def test_hdmi_signal_empty():
    assert parsers.hdmi_signal({}) is None


def test_hdmi_signal_fallback_to_source_online():
    state = {"streamer": {"streamer": {"source": {"online": True}}}}
    assert parsers.hdmi_signal(state) is True


def test_hdmi_signal_wrong_type():
    assert parsers.hdmi_signal({"streamer": {"streamer": "garbage"}}) is None


def test_hdmi_out_signal_active(active):
    assert parsers.hdmi_out_signal(active) is False


def test_hdmi_out_signal_null(live):
    assert parsers.hdmi_out_signal(live) is None


def test_hdmi_out_signal_empty():
    assert parsers.hdmi_out_signal({}) is None


def test_hdmi_out_signal_wrong_type():
    assert parsers.hdmi_out_signal({"streamer": {"streamer": {"hdmi": "garbage"}}}) is None


def test_video_resolution_active(active):
    assert parsers.video_resolution(active) == "1920x1080"


def test_video_resolution_null(live):
    assert parsers.video_resolution(live) is None


def test_video_resolution_empty():
    assert parsers.video_resolution({}) is None


def test_video_resolution_zero_dims_falls_back_to_real_resolution():
    state = {
        "streamer": {
            "streamer": {
                "source": {
                    "resolution": {"width": 0, "height": 0},
                    "real_resolution": "1920x1080@60",
                }
            }
        }
    }
    assert parsers.video_resolution(state) == "1920x1080"


def test_video_resolution_wrong_type():
    state = {"streamer": {"streamer": {"source": {"resolution": "garbage"}}}}
    assert parsers.video_resolution(state) is None


def test_video_fps_target(live):
    assert parsers.video_fps_target(live) == 40


def test_video_fps_target_active(active):
    assert parsers.video_fps_target(active) == 60


def test_video_fps_target_empty():
    assert parsers.video_fps_target({}) is None


def test_video_fps_target_wrong_type():
    assert parsers.video_fps_target({"streamer": {"params": {"desired_fps": "40"}}}) is None


# --- HID ----------------------------------------------------------------


def test_keyboard_online(live):
    assert parsers.keyboard_online(live) is True


def test_keyboard_online_empty():
    assert parsers.keyboard_online({}) is None


def test_keyboard_online_wrong_type():
    assert parsers.keyboard_online({"hid": {"keyboard": "garbage"}}) is None


def test_mouse_online(live):
    assert parsers.mouse_online(live) is True


def test_mouse_online_empty():
    assert parsers.mouse_online({}) is None


def test_mouse_online_wrong_type():
    assert parsers.mouse_online({"hid": {"mouse": {"online": 1}}}) is None


def test_hid_online(live):
    assert parsers.hid_online(live) is True


def test_hid_online_empty():
    assert parsers.hid_online({}) is None


def test_hid_online_wrong_type():
    assert parsers.hid_online({"hid": "garbage"}) is None


def test_hid_connected(live):
    assert parsers.hid_connected(live) is True


def test_hid_connected_empty():
    assert parsers.hid_connected({}) is None


def test_hid_connected_wrong_type():
    assert parsers.hid_connected({"hid": {"connected": "yes"}}) is None


def test_jiggler_enabled(live):
    assert parsers.jiggler_enabled(live) is True


def test_jiggler_enabled_bare_bool():
    assert parsers.jiggler_enabled({"hid": {"jiggler": True}}) is True
    assert parsers.jiggler_enabled({"hid": {"jiggler": False}}) is False


def test_jiggler_enabled_empty():
    assert parsers.jiggler_enabled({}) is None


def test_jiggler_enabled_wrong_type():
    assert parsers.jiggler_enabled({"hid": {"jiggler": "garbage"}}) is None


def test_jiggler_active(live):
    assert parsers.jiggler_active(live) is False


def test_jiggler_active_empty():
    assert parsers.jiggler_active({}) is None


def test_jiggler_active_wrong_type():
    assert parsers.jiggler_active({"hid": {"jiggler": True}}) is None


def test_jiggler_interval(live):
    assert parsers.jiggler_interval(live) == 20


def test_jiggler_interval_empty():
    assert parsers.jiggler_interval({}) is None


def test_jiggler_interval_wrong_type():
    assert parsers.jiggler_interval({"hid": {"jiggler": {"interval": "20"}}}) is None


# --- MSD ------------------------------------------------------------------


def test_msd_enabled(live):
    assert parsers.msd_enabled(live) is True


def test_msd_enabled_empty():
    assert parsers.msd_enabled({}) is None


def test_msd_enabled_wrong_type():
    assert parsers.msd_enabled({"msd": "garbage"}) is None


def test_msd_online(live):
    assert parsers.msd_online(live) is False


def test_msd_online_active(active):
    assert parsers.msd_online(active) is True


def test_msd_online_empty():
    assert parsers.msd_online({}) is None


def test_msd_online_wrong_type():
    assert parsers.msd_online({"msd": {"online": 0}}) is None


def test_msd_busy(live):
    assert parsers.msd_busy(live) is False


def test_msd_busy_empty():
    assert parsers.msd_busy({}) is None


def test_msd_busy_wrong_type():
    assert parsers.msd_busy({"msd": {"busy": "no"}}) is None


def test_msd_connected_null(live):
    assert parsers.msd_connected(live) is False


def test_msd_connected_active(active):
    assert parsers.msd_connected(active) is True


def test_msd_connected_empty():
    assert parsers.msd_connected({}) is None


def test_msd_connected_top_level_fallback():
    assert parsers.msd_connected({"msd": {"connected": True}}) is True


def test_msd_connected_wrong_type():
    assert parsers.msd_connected({"msd": {"drive": {"connected": "yes"}}}) is None


def test_msd_image_null(live):
    assert parsers.msd_image(live) is None


def test_msd_image_active(active):
    assert parsers.msd_image(active) == "ubuntu-22.04.4-desktop-amd64.iso"


def test_msd_image_empty():
    assert parsers.msd_image({}) is None


def test_msd_image_empty_string():
    assert parsers.msd_image({"msd": {"drive": {"image": ""}}}) is None


def test_msd_image_dict_with_name():
    state = {"msd": {"drive": {"image": {"name": "test.iso"}}}}
    assert parsers.msd_image(state) == "test.iso"


def test_msd_image_wrong_type():
    assert parsers.msd_image({"msd": {"drive": {"image": 123}}}) is None


def test_msd_cdrom(live):
    assert parsers.msd_cdrom(live) is True


def test_msd_cdrom_empty():
    assert parsers.msd_cdrom({}) is None


def test_msd_cdrom_wrong_type():
    assert parsers.msd_cdrom({"msd": {"drive": {"cdrom": "yes"}}}) is None


def test_msd_rw_null(live):
    assert parsers.msd_rw(live) is False


def test_msd_rw_active(active):
    assert parsers.msd_rw(active) is True


def test_msd_rw_empty():
    assert parsers.msd_rw({}) is None


def test_msd_rw_wrong_type():
    assert parsers.msd_rw({"msd": {"drive": {"rw": "yes"}}}) is None


def test_msd_images_empty_dict(live):
    assert parsers.msd_images(live) == []


def test_msd_images_active(active):
    assert parsers.msd_images(active) == [
        "ubuntu-22.04.4-desktop-amd64.iso",
        "windows11.iso",
    ]


def test_msd_images_missing():
    assert parsers.msd_images({}) == []


def test_msd_images_wrong_type():
    assert parsers.msd_images({"msd": {"storage": {"images": None}}}) == []


# --- System ---------------------------------------------------------------


def test_kvmd_version(live):
    assert parsers.kvmd_version(live) == "4.82"


def test_kvmd_version_empty():
    assert parsers.kvmd_version({}) is None


def test_kvmd_version_wrong_type():
    assert parsers.kvmd_version({"info": {"system": {"kvmd": {"version": 4.82}}}}) is None


def test_hostname(live):
    assert parsers.hostname(live) == "<redacted>"


def test_hostname_empty():
    assert parsers.hostname({}) is None


def test_hostname_bare_string_fallback():
    assert parsers.hostname({"glinet": {"hostname": "comet-kvm"}}) == "comet-kvm"


def test_hostname_wrong_type():
    assert parsers.hostname({"glinet": {"hostname": {"hostname": 123}}}) is None


def test_ip_address(live):
    assert parsers.ip_address(live) == "<redacted>"


def test_ip_address_empty():
    assert parsers.ip_address({}) is None


def test_ip_address_fallback():
    assert parsers.ip_address({"glinet": {"network": {"ip_address": "10.0.0.5"}}}) == "10.0.0.5"


def test_ip_address_wrong_type():
    state = {"glinet": {"network": {"config": {"ip_address": 12345}}}}
    assert parsers.ip_address(state) is None


def test_network_state(live):
    assert parsers.network_state(live) == "online"


def test_network_state_empty():
    assert parsers.network_state({}) is None


def test_network_state_wrong_type():
    state = {"glinet": {"network": {"config": {"state": 1}}}}
    assert parsers.network_state(state) is None


def test_is_dhcp(live):
    assert parsers.is_dhcp(live) is True


def test_is_dhcp_empty():
    assert parsers.is_dhcp({}) is None


def test_is_dhcp_wrong_type():
    state = {"glinet": {"network": {"config": {"is_dhcp": "yes"}}}}
    assert parsers.is_dhcp(state) is None


# --- Firmware ---------------------------------------------------------------


def test_firmware_latest_version_no_update(live):
    assert parsers.firmware_latest_version(live) == "V1.9.1 release1"


def test_firmware_latest_version_update_available(active):
    assert parsers.firmware_latest_version(active) == "V1.9.2 release1"


def test_firmware_latest_version_falls_back_to_installed():
    state = {"glinet": {"upgrade_version": {"version": "V1.9.1 release1"}}}
    assert parsers.firmware_latest_version(state) == "V1.9.1 release1"


def test_firmware_latest_version_empty():
    assert parsers.firmware_latest_version({}) is None


def test_firmware_latest_version_wrong_type():
    state = {"glinet": {"upgrade_compare": {"server_version": 123}}}
    assert parsers.firmware_latest_version(state) is None


def test_firmware_update_available_true(active):
    assert parsers.firmware_update_available(active) is True


def test_firmware_update_available_false(live):
    assert parsers.firmware_update_available(live) is False


def test_firmware_update_available_none_missing_compare():
    assert parsers.firmware_update_available({}) is None


def test_firmware_update_available_none_missing_version():
    state = {"glinet": {"upgrade_compare": {"local_version": "V1.0"}}}
    assert parsers.firmware_update_available(state) is None


def test_firmware_update_available_wrong_type():
    state = {"glinet": {"upgrade_compare": "garbage"}}
    assert parsers.firmware_update_available(state) is None


def test_firmware_beta_version(active):
    assert parsers.firmware_beta_version(active) == "V1.9.3 beta1"


def test_firmware_beta_version_empty_string_is_none(live):
    assert parsers.firmware_beta_version(live) is None


def test_firmware_beta_version_missing():
    assert parsers.firmware_beta_version({}) is None


def test_firmware_beta_version_wrong_type():
    state = {"glinet": {"upgrade_compare": {"beta_version": 123}}}
    assert parsers.firmware_beta_version(state) is None


def test_firmware_release_notes(live):
    assert parsers.firmware_release_notes(live) == "## New Features - Delay mode configuration."


def test_firmware_release_notes_empty():
    assert parsers.firmware_release_notes({}) is None


def test_firmware_release_notes_wrong_type():
    state = {"glinet": {"upgrade_compare": {"release_note": None}}}
    assert parsers.firmware_release_notes(state) is None


def test_firmware_release_notes_truncated_to_255():
    long_note = "x" * 300
    state = {"glinet": {"upgrade_compare": {"release_note": long_note}}}
    result = parsers.firmware_release_notes(state)
    assert result is not None
    assert len(result) == 255


def test_firmware_release_notes_whitespace_collapsed():
    state = {"glinet": {"upgrade_compare": {"release_note": "line one\n\n  line   two\t\tend"}}}
    assert parsers.firmware_release_notes(state) == "line one line two end"


def test_firmware_compare_error_none_when_null(live):
    assert parsers.firmware_compare_error(live) is None


def test_firmware_compare_error_present():
    state = {"glinet": {"upgrade_compare": {"error": "network timeout"}}}
    assert parsers.firmware_compare_error(state) == "network timeout"


def test_firmware_compare_error_empty():
    assert parsers.firmware_compare_error({}) is None


def test_firmware_compare_error_wrong_type():
    state = {"glinet": {"upgrade_compare": {"error": 123}}}
    assert parsers.firmware_compare_error(state) is None


# --- ATX HDD LED ------------------------------------------------------------


def test_atx_hdd_active(live):
    assert parsers.atx_hdd_active(live) is False


def test_atx_hdd_active_empty():
    assert parsers.atx_hdd_active({}) is None


def test_atx_hdd_active_wrong_type():
    assert parsers.atx_hdd_active({"atx": {"leds": {"hdd": "yes"}}}) is None


# --- GPIO ---------------------------------------------------------------


def test_gpio_channel_input(gpio):
    assert parsers.gpio_channel(gpio, "inputs", "in_1") == {
        "online": True,
        "state": True,
    }


def test_gpio_channel_output(gpio):
    assert parsers.gpio_channel(gpio, "outputs", "out_switch") == {
        "online": True,
        "state": True,
    }


def test_gpio_channel_empty_on_live(live):
    assert parsers.gpio_channel(live, "inputs", "in_1") == {}


def test_gpio_channel_unknown_channel(gpio):
    assert parsers.gpio_channel(gpio, "inputs", "nope") == {}


def test_gpio_channel_empty():
    assert parsers.gpio_channel({}, "inputs", "in_1") == {}


def test_gpio_channel_wrong_type(gpio):
    state = {"gpio": {"inputs": {"in_1": "garbage"}}}
    assert parsers.gpio_channel(state, "inputs", "in_1") == {}


def test_gpio_model_channels_inputs(gpio):
    assert parsers.gpio_model_channels(gpio, "inputs") == {"in_1": {}, "in_2": {}}


def test_gpio_model_channels_outputs(gpio):
    channels = parsers.gpio_model_channels(gpio, "outputs")
    assert set(channels) == {"out_switch", "out_pulse", "out_pulse_disabled", "out_none"}
    assert channels["out_switch"]["switch"] is True


def test_gpio_model_channels_empty_on_live(live):
    assert parsers.gpio_model_channels(live, "outputs") == {}


def test_gpio_model_channels_empty():
    assert parsers.gpio_model_channels({}, "inputs") == {}


def test_gpio_model_channels_wrong_type():
    state = {"gpio_model": {"inputs": "garbage"}}
    assert parsers.gpio_model_channels(state, "inputs") == {}


def test_gpio_display_name_labeled(gpio):
    labels = gpio["gpio_labels"]
    assert parsers.gpio_display_name("in_1", labels) == "Door Sensor"
    assert parsers.gpio_display_name("out_switch", labels) == "Relay 1"


def test_gpio_display_name_falls_back_to_title_case(gpio):
    labels = gpio["gpio_labels"]
    assert parsers.gpio_display_name("in_2", labels) == "In 2"
    assert parsers.gpio_display_name("out_pulse", labels) == "Out Pulse"


def test_gpio_display_name_empty_labels():
    assert parsers.gpio_display_name("out_none", {}) == "Out None"


def test_gpio_display_name_missing_labels_dict():
    assert parsers.gpio_display_name("out_none", None) == "Out None"


def test_gpio_state_true(gpio):
    assert parsers.gpio_state(gpio, "inputs", "in_1") is True


def test_gpio_state_false(gpio):
    assert parsers.gpio_state(gpio, "inputs", "in_2") is False


def test_gpio_state_non_bool_value():
    state = {"gpio": {"inputs": {"in_1": {"state": "on"}}}}
    assert parsers.gpio_state(state, "inputs", "in_1") is None


def test_gpio_state_missing_channel(gpio):
    assert parsers.gpio_state(gpio, "inputs", "nope") is None


def test_gpio_pulse_capable_missing_max_delay_is_capable():
    assert parsers.gpio_pulse_capable({"delay": 0.5}) is True


def test_gpio_pulse_capable_none_max_delay_is_capable():
    assert parsers.gpio_pulse_capable({"delay": 0.5, "max_delay": None}) is True


def test_gpio_pulse_capable_positive_max_delay():
    assert parsers.gpio_pulse_capable({"delay": 0.5, "max_delay": 5.0}) is True


def test_gpio_pulse_capable_zero_max_delay_disabled():
    assert parsers.gpio_pulse_capable({"delay": 0.1, "max_delay": 0}) is False


def test_gpio_pulse_capable_negative_max_delay_disabled():
    assert parsers.gpio_pulse_capable({"delay": 0.1, "max_delay": -1}) is False


def test_gpio_pulse_capable_non_numeric_max_delay_disabled():
    assert parsers.gpio_pulse_capable({"delay": 0.1, "max_delay": "5"}) is False


def test_gpio_pulse_capable_bool_max_delay_disabled():
    assert parsers.gpio_pulse_capable({"delay": 0.1, "max_delay": True}) is False


def test_gpio_pulse_capable_non_dict_config():
    assert parsers.gpio_pulse_capable(None) is False
    assert parsers.gpio_pulse_capable("garbage") is False


def test_gpio_pulse_delay_present():
    assert parsers.gpio_pulse_delay({"delay": 0.5, "max_delay": 5.0}) == 0.5


def test_gpio_pulse_delay_missing_defaults_to_zero():
    assert parsers.gpio_pulse_delay({"max_delay": 5.0}) == 0.0


def test_gpio_pulse_delay_non_dict_config():
    assert parsers.gpio_pulse_delay(None) == 0.0
    assert parsers.gpio_pulse_delay("garbage") == 0.0
