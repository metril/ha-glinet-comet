"""Shared dynamic-add wiring for GPIO entities.

GPIO channels aren't fixed on a running Comet -- more can be configured on
the device after this integration has already set up its entities. The
coordinator polls ``GET /api/gpio`` every slow cycle (see coordinator.py's
module docstring), so a newly-appeared channel shows up in
``coordinator.data["gpio_model"]`` on its own, without a config entry
reload. ``async_setup_gpio_entities`` is the one place that watches for
that and calls each platform's ``async_add_entities`` for the new channels
-- binary_sensor/switch/button each just supply a ``factory`` and call this
once, right *after* their own static entity list has already been added
(so the static batch and the dynamic GPIO batch are always two separate
``async_add_entities`` calls, never merged into one).

Dedup / identity guard
-----------------------
``_process_gpio_full`` replaces ``gpio_model`` wholesale on every
successful GPIO read (never merges it), so a fresh dict object shows up on
the coordinator every time that read succeeds, whether or not the model
actually changed. Comparing ``gpio_model is last_model`` (object identity,
not equality) lets the per-WS-frame listener skip the channel scan below
with a single cheap ``is`` compare on the overwhelming majority of frames:
``_process_gpio_event`` (the WS ``gpio`` handler) deliberately merges only
channel *state* from the frame and ignores any ``model`` section it
carries, so `gpio_model` only ever becomes a new object right after an
HTTP gpio poll actually lands (or on the first read) -- never from a plain
WS state push. A fresh-but-equal ``gpio_model`` dict (e.g. two back-to-back
GETs of an unchanged config) still triggers one scan -- the
``added: set[str]`` of channel ids already handed to ``async_add_entities``
is what actually prevents a duplicate entity in that case.

Vanished channels
------------------
A channel that disappears from ``gpio_model`` (unplugged/reconfigured) is
never removed here -- its entity is simply left in place and goes
unavailable, exactly like any GPIO channel that stops reporting
``online: true`` (see ``CometGpioEntity.available``). A future cleanup pass
would enumerate ``entity_registry.async_get(hass)`` entries for this
``config_entry_id``, filter by the ``_gpio_in_``/``_gpio_out_``/
``_gpio_pulse_`` unique_id prefixes, and ``async_remove`` any whose channel
is confirmed gone by a *successful* gpio read -- never gated on a
failed/retried one, which would remove entities for channels that are
still there but just transiently unreadable. Not built here.

Listener lifetime
------------------
Each call registers one permanent listener via
``coordinator.async_add_listener`` (removed only on unload via
``entry.async_on_unload``), so real HA's ``DataUpdateCoordinator`` keeps
``update_interval`` scheduled for the config entry's entire lifetime --
it only stops scheduling once the *last* listener goes. That's harmless
here: the coordinator's WS loop (``async_start``) runs independently of
whether anything is listening, and the slow-tier poll needs to keep
running anyway for atx/hid/msd/streamer/gpio, so this doesn't change
actual polling behavior.
"""

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


def async_setup_gpio_entities(
    entry: ConfigEntry,
    coordinator: CometDataUpdateCoordinator,
    async_add_entities: AddEntitiesCallback,
    kind: str,
    factory: Callable[[str, Any], Entity | None],
) -> None:
    """Add GPIO entities for ``kind`` (``"inputs"``/``"outputs"``) as they appear.

    ``factory(channel, config)`` returns the entity for that channel, or
    ``None`` to skip it (e.g. an output that isn't switch-capable, skipped
    by the switch platform's factory). Runs once synchronously (for
    whatever channels already exist at setup time), then again on every
    coordinator update thereafter.

    ``factory`` is called (and its result cached in ``added``) at most
    once per channel id: which entity kind a channel gets -- switch vs.
    pulse button, or skipped entirely (e.g. a ``pulse``-disabled output) --
    is decided from whatever ``config`` looked like the first time that
    channel was seen, so a later change to that *capability-affecting*
    shape (``switch``/``pulse`` appearing or disappearing, ``max_delay``
    going to zero, etc.) doesn't reach the already-created entity until a
    reload -- the same freeze v0.2.0's setup-time-only loop already had for
    every channel; this only extends it to channels seen after setup
    instead of just the ones seen at setup. It does *not* freeze the
    channel's label or (for a pulse button) its ``delay`` -- both are read
    live from ``coordinator.data`` on every access/press, so a relabel or a
    changed ``delay`` reaches the entity without a reload.
    """
    added: set[str] = set()
    last_model: Any = _UNSET

    @callback
    def _check_for_new_channels() -> None:
        nonlocal last_model
        data = coordinator.data or {}
        model = data.get("gpio_model")
        if model is last_model:
            return
        last_model = model

        new: list[Entity] = []
        for channel, config in parsers.gpio_model_channels(data, kind).items():
            if channel in added:
                continue
            entity = factory(channel, config)
            if entity is None:
                continue
            new.append(entity)
            added.add(channel)

        if new:
            async_add_entities(new)

    _check_for_new_channels()
    entry.async_on_unload(coordinator.async_add_listener(_check_for_new_channels))
