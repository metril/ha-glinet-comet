"""Update platform for the GL.iNet Comet integration.

Read-only in v0.1: firmware install happens from the Comet's own web UI, so
this entity reports the installed/latest version and release notes only --
no ``UpdateEntityFeature.INSTALL`` and no ``async_install`` override.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .data import CometConfigEntry
from .coordinator import CometDataUpdateCoordinator
from .entity import CometEntity

_RELEASE_URL = "https://dl.gl-inet.com/"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet Comet firmware update entity."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([CometFirmwareUpdate(coordinator, entry)])


class CometFirmwareUpdate(CometEntity, UpdateEntity):
    """Reports the Comet's installed/available firmware version."""

    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_title = "GL.iNet Comet firmware"
    _attr_release_url = _RELEASE_URL

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
    ) -> None:
        """Initialize the firmware update entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_firmware"

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        return parsers.firmware_version(self.coordinator.data or {})

    @property
    def latest_version(self) -> str | None:
        """Return the latest known firmware version.

        ``parsers.firmware_latest_version`` already falls back to the
        installed version when the server hasn't reported a newer one, so
        this is never ``None`` while ``installed_version`` is set.
        """
        return parsers.firmware_latest_version(self.coordinator.data or {})

    @property
    def release_summary(self) -> str | None:
        """Return the release notes for the available update, if any."""
        return parsers.firmware_release_notes(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the available beta firmware version, if any."""
        beta = parsers.firmware_beta_version(self.coordinator.data or {})
        if beta is None:
            return None
        return {"beta_version": beta}
