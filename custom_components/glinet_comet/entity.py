"""Base entities for the GL.iNet Comet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import parsers
from .const import CONF_HOST, DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import CometDataUpdateCoordinator


class CometEntity(CoordinatorEntity[CometDataUpdateCoordinator]):
    """Base entity for GL.iNet Comet devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the entity and its device info."""
        super().__init__(coordinator)
        data = coordinator.data or {}
        connections = set()
        mac = parsers.mac_address(data)
        if mac:
            connections = {(CONNECTION_NETWORK_MAC, mac)}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=parsers.device_model(data) or DEFAULT_MODEL,
            sw_version=parsers.firmware_version(data),
            serial_number=parsers.serial(data),
            configuration_url=f"https://{entry.data[CONF_HOST]}",
            connections=connections,
        )


class CometAtxEntity(CometEntity):
    """Base entity for ATX-dependent entities.

    Unavailable whenever the coordinator is unavailable OR the device
    reports no ATX board attached (``atx.enabled`` is not True).
    """

    @property
    def available(self) -> bool:
        """Return True only if the coordinator is available and ATX is enabled."""
        return super().available and self.coordinator.atx_enabled


class CometGpioEntity(CometEntity):
    """Base entity for a single GPIO channel (input, output switch, or pulse button).

    Unavailable whenever the coordinator is unavailable OR the channel isn't
    reporting ``online: true``. No optimistic update -- the device pushes
    ``gpio`` WebSocket frames.
    """

    # Overridden by subclasses: which ``gpio.<kind>`` state section this
    # channel lives in, and the unique_id segment identifying the entity kind.
    _gpio_kind: str
    _gpio_id_kind: str

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
        channel: str,
    ) -> None:
        """Initialize the GPIO entity."""
        super().__init__(coordinator, entry)
        self._channel = channel
        self._attr_unique_id = (
            f"{entry.entry_id}_gpio_{self._gpio_id_kind}_{channel}"
        )
        labels = (coordinator.data or {}).get("gpio_labels")
        self._attr_name = parsers.gpio_display_name(channel, labels)

    @property
    def _channel_state(self) -> dict[str, Any]:
        """Return the live state dict for this GPIO channel (``{}`` on miss)."""
        return parsers.gpio_channel(
            self.coordinator.data or {}, self._gpio_kind, self._channel
        )

    @property
    def available(self) -> bool:
        """Return True only if the coordinator is available and the channel is online."""
        return super().available and self._channel_state.get("online") is True
