"""Dynamic setup helper for multiport-switch (Comet X) port entities."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .coordinator import CometDataUpdateCoordinator

_UNSET: Any = object()


def async_setup_switch_port_entities(
    entry: ConfigEntry,
    coordinator: CometDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[dict[str, Any]], list[Entity]],
) -> None:
    """Add per-port switch entities as ports appear in ``switch_model``.

    ``factory(port)`` returns the entities for that port dict (possibly
    empty). Runs once synchronously, then again whenever
    ``coordinator.data["switch_model"]`` is a new object (it's replaced
    wholesale on every model frame/read).

    ``factory`` is called at most once per port ``id``: which entities a
    port gets is decided from its shape the first time it's seen, and a
    port that later vanishes from the model is never removed -- its
    entities just go unavailable. Port names are read live by the entities.
    """
    added: set[str] = set()
    last_model: Any = _UNSET

    @callback
    def _check_for_new_ports() -> None:
        nonlocal last_model
        data = coordinator.data or {}
        model = data.get("switch_model")
        if model is last_model:
            return
        last_model = model

        new: list[Entity] = []
        for port in parsers.switch_ports(data):
            if port["id"] in added:
                continue
            new.extend(factory(port))
            added.add(port["id"])

        if new:
            async_add_entities(new)

    _check_for_new_ports()
    entry.async_on_unload(coordinator.async_add_listener(_check_for_new_ports))
