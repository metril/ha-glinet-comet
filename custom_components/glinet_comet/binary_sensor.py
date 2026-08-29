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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .const import CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX, DOMAIN
from .coordinator import CometDataUpdateCoordinator
from .entity import CometAtxEntity, CometEntity


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
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet binary sensors."""
    coordinator: CometDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    enable_atx = entry.options.get(CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX)

    entities: list[BinarySensorEntity] = []
    for description in BINARY_SENSORS:
        if description.requires_atx:
            if not enable_atx:
                continue
            entities.append(CometAtxBinarySensor(coordinator, entry, description))
        else:
            entities.append(CometBinarySensor(coordinator, entry, description))

    data = coordinator.data or {}
    for channel in parsers.gpio_model_channels(data, "inputs"):
        entities.append(CometGpioInputBinarySensor(coordinator, entry, channel))

    async_add_entities(entities)


class _CometBinarySensorMixin:
    """Shared unique_id/is_on wiring for description-driven binary sensors."""

    entity_description: CometBinarySensorDescription

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
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


class CometGpioInputBinarySensor(CometEntity, BinarySensorEntity):
    """A GPIO input channel exposed as a binary sensor.

    No optimistic update -- the device pushes ``gpio`` WebSocket frames.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
        channel: str,
    ) -> None:
        """Initialize the GPIO input binary sensor."""
        super().__init__(coordinator, entry)
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_gpio_in_{channel}"
        labels = (coordinator.data or {}).get("gpio_labels")
        self._attr_name = parsers.gpio_display_name(channel, labels)

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO input state."""
        if self.coordinator.data is None:
            return None
        channel = parsers.gpio_channel(self.coordinator.data, "inputs", self._channel)
        return channel.get("state")

    @property
    def available(self) -> bool:
        """Return True only if the coordinator is available and the channel is online."""
        if not super().available or self.coordinator.data is None:
            return False
        channel = parsers.gpio_channel(self.coordinator.data, "inputs", self._channel)
        return channel.get("online") is True
