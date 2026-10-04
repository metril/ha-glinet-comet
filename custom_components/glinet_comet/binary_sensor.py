"""Binary sensor platform for the GL.iNet Comet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .data import CometConfigEntry
from .const import CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX
from .coordinator import CometDataUpdateCoordinator
from .entity import CometAtxEntity, CometEntity, CometGpioEntity
from .gpio import async_setup_gpio_entities


@dataclass(frozen=True, kw_only=True)
class CometBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a GL.iNet Comet binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None
    # Only created when the enable_atx option is on; served by CometAtxBinarySensor
    # so it also goes unavailable whenever the device reports no ATX board attached.
    requires_atx: bool = False


BINARY_SENSORS: tuple[CometBinarySensorDescription, ...] = (
    CometBinarySensorDescription(
        key="atx_power",
        name="ATX Power",
        device_class=BinarySensorDeviceClass.POWER,
        value_fn=parsers.atx_power_on,
        requires_atx=True,
    ),
    CometBinarySensorDescription(
        key="hdmi_signal",
        name="HDMI Signal",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=parsers.hdmi_signal,
    ),
    CometBinarySensorDescription(
        key="keyboard_online",
        name="Keyboard Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.keyboard_online,
    ),
    CometBinarySensorDescription(
        key="mouse_online",
        name="Mouse Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.mouse_online,
    ),
    CometBinarySensorDescription(
        key="atx_hdd_activity",
        name="ATX HDD Activity",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=parsers.atx_hdd_active,
        requires_atx=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet binary sensors."""
    coordinator: CometDataUpdateCoordinator = entry.runtime_data.coordinator
    enable_atx = entry.options.get(CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX)

    entities: list[BinarySensorEntity] = []
    for description in BINARY_SENSORS:
        if description.requires_atx:
            if not enable_atx:
                continue
            entities.append(CometAtxBinarySensor(coordinator, entry, description))
        else:
            entities.append(CometBinarySensor(coordinator, entry, description))

    async_add_entities(entities)

    async_setup_gpio_entities(
        entry,
        coordinator,
        async_add_entities,
        "inputs",
        lambda channel, config: CometGpioInputBinarySensor(coordinator, entry, channel),
    )


class _CometBinarySensorMixin:
    """Shared unique_id/is_on wiring for description-driven binary sensors."""

    entity_description: CometBinarySensorDescription

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
        description: CometBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return whether the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class CometBinarySensor(_CometBinarySensorMixin, CometEntity, BinarySensorEntity):
    """A GL.iNet Comet binary sensor."""


class CometAtxBinarySensor(_CometBinarySensorMixin, CometAtxEntity, BinarySensorEntity):
    """An ATX-gated GL.iNet Comet binary sensor.

    Unavailable (in addition to the usual coordinator-unavailable case)
    whenever the device reports no ATX board attached.
    """


class CometGpioInputBinarySensor(CometGpioEntity, BinarySensorEntity):
    """A GPIO input channel exposed as a binary sensor.

    No optimistic update -- the device pushes ``gpio`` WebSocket frames.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _gpio_kind = "inputs"
    _gpio_id_kind = "in"

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO input state."""
        return self._channel_on
