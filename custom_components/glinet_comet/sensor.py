"""Sensor platform for the GL.iNet Comet integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
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


SENSORS: tuple[CometSensorDescription, ...] = (
    CometSensorDescription(
        key="resolution",
        name="Resolution",
        icon="mdi:monitor",
        value_fn=parsers.video_resolution,
    ),
    CometSensorDescription(
        key="msd_image",
        name="MSD Image",
        icon="mdi:disc",
        value_fn=parsers.msd_image,
    ),
    CometSensorDescription(
        key="kvmd_version",
        name="KVMD Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:information-outline",
        value_fn=parsers.kvmd_version,
    ),
    CometSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=parsers.firmware_version,
    ),
    CometSensorDescription(
        key="ip_address",
        name="IP Address",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ip-network",
        value_fn=parsers.ip_address,
    ),
    CometSensorDescription(
        key="hostname",
        name="Hostname",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:dns",
        value_fn=parsers.hostname,
    ),
)


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
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor's current value (None, never an empty string)."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
