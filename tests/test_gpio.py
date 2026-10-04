"""Tests for gpio.py's dynamic-add helper."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from types import SimpleNamespace

from custom_components.glinet_comet.gpio import async_setup_gpio_entities


class _Coord:
    def __init__(self, data) -> None:
        self.data = data
        self.listeners: list = []

    def async_add_listener(self, fn):
        self.listeners.append(fn)
        return lambda: None


def _model(outputs):
    return {"gpio_model": {"inputs": {}, "outputs": outputs}}


def test_skipped_channel_never_gets_second_entity_kind():
    coord = _Coord(_model({"ch0": {"pulse": {"max_delay": 0}}}))
    entry = SimpleNamespace(async_on_unload=lambda fn: None)
    added: list = []
    calls: list[str] = []

    def factory(channel, config):
        calls.append(channel)
        # Skipped at first (no switch); would become a switch later.
        return object() if config.get("switch") else None

    async_setup_gpio_entities(entry, coord, added.extend, "outputs", factory)
    assert added == [] and calls == ["ch0"]

    coord.data = _model({"ch0": {"switch": True}})
    coord.listeners[0]()
    assert added == [] and calls == ["ch0"]  # factory not consulted again


def test_added_channel_deduped_across_fresh_models():
    coord = _Coord(_model({"ch0": {"switch": True}}))
    entry = SimpleNamespace(async_on_unload=lambda fn: None)
    added: list = []
    async_setup_gpio_entities(
        entry, coord, added.extend, "outputs", lambda c, cfg: object()
    )
    coord.data = _model({"ch0": {"switch": True}, "ch1": {"switch": True}})
    coord.listeners[0]()
    assert len(added) == 2
