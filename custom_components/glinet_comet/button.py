"""Button platform for the GL.iNet Comet integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .data import CometConfigEntry
from .api import CometApiClient, CometError
from .const import CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX
from .coordinator import CometDataUpdateCoordinator
from .entity import CometAtxEntity, CometEntity, CometGpioEntity
from .gpio import async_setup_gpio_entities


async def _press_reboot(client: CometApiClient) -> None:
    """Reboot the Comet.

    DESTRUCTIVE: this presses through to ``CometApiClient.reboot``, a
    GET-triggered route on the device that reboots it almost immediately
    once the request lands. There is no confirmation step.
    """
    await client.reboot()


@dataclass(frozen=True, kw_only=True)
class CometButtonDescription(ButtonEntityDescription):
    """Describes a GL.iNet Comet button."""

    press_fn: Callable[[CometApiClient], Coroutine[Any, Any, None]]
    # Only created when the enable_atx option is on; served by CometAtxButton so
    # it also goes unavailable whenever the device reports no ATX board attached.
    requires_atx: bool = False


BUTTONS: tuple[CometButtonDescription, ...] = (
    CometButtonDescription(
        key="atx_power",
        translation_key="atx_power",
        press_fn=lambda client: client.atx_click("power"),
        requires_atx=True,
    ),
    CometButtonDescription(
        key="atx_power_long",
        translation_key="atx_power_long",
        press_fn=lambda client: client.atx_click("power_long"),
        requires_atx=True,
    ),
    CometButtonDescription(
        key="atx_reset",
        translation_key="atx_reset",
        press_fn=lambda client: client.atx_click("reset"),
        requires_atx=True,
    ),
    CometButtonDescription(
        key="hid_reset",
        translation_key="hid_reset",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.reset_hid(),
    ),
    CometButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=_press_reboot,
    ),
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet buttons."""
    coordinator: CometDataUpdateCoordinator = entry.runtime_data.coordinator
    enable_atx = entry.options.get(CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX)

    entities: list[ButtonEntity] = []
    for description in BUTTONS:
        if description.requires_atx:
            if not enable_atx:
                continue
            entities.append(CometAtxButton(coordinator, entry, description))
        else:
            entities.append(CometButton(coordinator, entry, description))

    async_add_entities(entities)

    def _gpio_pulse_factory(channel: str, config: object) -> ButtonEntity | None:
        if not isinstance(config, dict) or config.get("switch") is True:
            return None
        pulse = config.get("pulse")
        if not parsers.gpio_pulse_capable(pulse):
            return None
        return CometGpioPulseButton(coordinator, entry, channel)

    async_setup_gpio_entities(
        entry, coordinator, async_add_entities, "outputs", _gpio_pulse_factory
    )


class _CometButtonMixin:
    """Shared unique_id/press wiring for description-driven buttons."""

    entity_description: CometButtonDescription

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
        description: CometButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    async def async_press(self) -> None:
        """Invoke the button's action."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err


class CometButton(_CometButtonMixin, CometEntity, ButtonEntity):
    """A GL.iNet Comet button."""


class CometAtxButton(_CometButtonMixin, CometAtxEntity, ButtonEntity):
    """An ATX-gated GL.iNet Comet button.

    Unavailable (in addition to the usual coordinator-unavailable case)
    whenever the device reports no ATX board attached.
    """


class CometGpioPulseButton(CometGpioEntity, ButtonEntity):
    """A GPIO output channel with a pulse config, exposed as a button.

    Only created for outputs that have a ``pulse`` config and aren't already
    switch-capable (a switch-capable channel is served by ``CometGpioSwitch``
    instead).
    """

    _attr_icon = "mdi:gesture-tap-button"
    _gpio_kind = "outputs"
    _gpio_id_kind = "pulse"

    async def async_press(self) -> None:
        """Pulse the GPIO output channel.

        The pulse delay is read live from the current ``gpio_model`` (not
        frozen at construction -- see ``gpio.py``'s module docstring), so a
        changed ``delay`` on the device reaches the next press without a
        reload; a vanished channel presses with ``delay=0``.
        """
        config = parsers.gpio_model_channels(
            self.coordinator.data or {}, self._gpio_kind
        ).get(self._channel)
        pulse = config.get("pulse") if isinstance(config, dict) else None
        delay = parsers.gpio_pulse_delay(pulse)
        try:
            await self.coordinator.client.gpio_pulse(self._channel, delay)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
