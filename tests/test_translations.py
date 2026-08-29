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
