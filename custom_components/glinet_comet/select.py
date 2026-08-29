"""Select platform for the GL.iNet Comet integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import CometError
from .const import DOMAIN
from .coordinator import CometDataUpdateCoordinator
from .entity import CometEntity

# Sentinel option meaning "no image mounted". Never sent to the device --
# async_select_option maps it to image="" before calling set_msd_params.
MSD_IMAGE_NONE = "(none)"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet select entities."""
    coordinator: CometDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities([CometMsdImageSelect(coordinator, entry)])


class CometMsdImageSelect(CometEntity, SelectEntity):
    """Select entity for choosing which image is mounted via MSD."""

    _attr_name = "MSD Image"
    _attr_icon = "mdi:disc"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the MSD image select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_msd_image"

    @property
    def options(self) -> list[str]:
        """Return the "none" sentinel plus every image in storage.

        If the currently-mounted image isn't in storage (e.g. it was
        removed from disk after being selected), it's appended so it can
        still be displayed as the current option.
        """
        data = self.coordinator.data or {}
        options = [MSD_IMAGE_NONE, *parsers.msd_images(data)]
        current = parsers.msd_image(data)
        if current and current not in options:
            options.append(current)
        return options

    @property
    def current_option(self) -> str | None:
        """Return the currently mounted image, or the "none" sentinel."""
        data = self.coordinator.data or {}
        return parsers.msd_image(data) or MSD_IMAGE_NONE

    async def async_select_option(self, option: str) -> None:
        """Select an image: disconnect first if connected, then set params.

        MSD must be disconnected before ``set_msd_params`` is called. The
        "(none)" sentinel is never sent to the device -- it maps to
        ``image=""``.
        """
        data = self.coordinator.data or {}
        image = "" if option == MSD_IMAGE_NONE else option
        try:
            if parsers.msd_connected(data) is True:
                await self.coordinator.client.set_msd_connected(False)
            await self.coordinator.client.set_msd_params(
                image=image, cdrom=True, rw=False
            )
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
