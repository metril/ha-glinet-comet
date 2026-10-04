"""Sensor platform for the GL.iNet Comet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .data import CometConfigEntry
from .coordinator import CometDataUpdateCoordinator
from .entity import CometEntity


@dataclass(frozen=True, kw_only=True)
class CometSensorDescription(SensorEntityDescription):
    """Describes a GL.iNet Comet sensor."""

    value_fn: Callable[[dict[str, Any]], Any] = lambda data: None
    ws_backed: bool = True


SENSORS: tuple[CometSensorDescription, ...] = (
    CometSensorDescription(
        key="resolution",
        translation_key="resolution",
        value_fn=parsers.video_resolution,
    ),
    CometSensorDescription(
        key="msd_image",
        translation_key="msd_image",
        value_fn=parsers.msd_image,
    ),
    CometSensorDescription(
        key="kvmd_version",
        translation_key="kvmd_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.kvmd_version,
    ),
    CometSensorDescription(
        key="firmware_version",
        ws_backed=False,
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.firmware_version,
    ),
    CometSensorDescription(
        key="ip_address",
        ws_backed=False,
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.ip_address,
    ),
    CometSensorDescription(
        key="hostname",
        ws_backed=False,
        translation_key="hostname",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=parsers.hostname,
    ),
    CometSensorDescription(
        key="mac_address",
        translation_key="mac_address",
        ws_backed=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=parsers.mac_address,
    ),
    CometSensorDescription(
        key="gateway",
        translation_key="gateway",
        ws_backed=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=parsers.gateway,
    ),
    CometSensorDescription(
        key="dhcp",
        translation_key="dhcp",
        ws_backed=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=parsers.dhcp_mode,
    ),
    CometSensorDescription(
        key="captured_fps",
        translation_key="captured_fps",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="fps",
        value_fn=parsers.captured_fps,
    ),
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet sensors."""
    coordinator: CometDataUpdateCoordinator = entry.runtime_data.coordinator
    async_add_entities(CometSensor(coordinator, entry, desc) for desc in SENSORS)


class CometSensor(CometEntity, SensorEntity):
    """A GL.iNet Comet sensor."""

    entity_description: CometSensorDescription

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
        description: CometSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._ws_backed = description.ws_backed
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor's current value (None, never an empty string)."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
