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
