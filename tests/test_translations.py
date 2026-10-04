"""Ensure strings.json and translations/en.json never drift apart."""

from __future__ import annotations

import json
import os

COMPONENT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "glinet_comet"
)


def test_strings_and_en_translations_are_identical():
    with open(os.path.join(COMPONENT_DIR, "strings.json"), encoding="utf-8") as f:
        strings = json.load(f)
    with open(
        os.path.join(COMPONENT_DIR, "translations", "en.json"), encoding="utf-8"
    ) as f:
        translations = json.load(f)

    assert strings == translations


def test_exception_translations_present_in_both_files():
    for name in ("strings.json", os.path.join("translations", "en.json")):
        with open(os.path.join(COMPONENT_DIR, name), encoding="utf-8") as f:
            exceptions = json.load(f)["exceptions"]
        for key in ("device_not_found", "not_comet_device", "command_failed"):
            assert "message" in exceptions[key]


def _load(*parts):
    with open(os.path.join(COMPONENT_DIR, *parts), encoding="utf-8") as f:
        return json.load(f)


def _code_translation_keys() -> dict[str, set[str]]:
    import re

    keys: dict[str, set[str]] = {}
    for platform in (
        "sensor", "binary_sensor", "switch", "button", "select", "camera", "update"
    ):
        with open(os.path.join(COMPONENT_DIR, platform + ".py"), encoding="utf-8") as f:
            src = f.read()
        found = set(re.findall(r'translation_key[ =]+"(\w+)"', src))
        keys[platform] = found
    return keys


def test_every_translation_key_has_name_in_both_files():
    used = _code_translation_keys()
    for name in (("strings.json",), ("translations", "en.json")):
        entity = _load(*name)["entity"]
        for platform, keys in used.items():
            for key in keys:
                assert entity[platform][key]["name"], (platform, key)


def test_icons_json_references_existing_keys():
    icons = _load("icons.json")["entity"]
    entity = _load("strings.json")["entity"]
    for platform, mapping in icons.items():
        for key, value in mapping.items():
            assert key in entity[platform], (platform, key)
            assert value["default"].startswith("mdi:")
