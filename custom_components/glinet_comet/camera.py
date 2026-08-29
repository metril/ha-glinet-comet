"""Camera platform for the GL.iNet Comet integration.

Snapshot-only: the Comet exposes a single still-image endpoint with no
width/height resizing, so ``async_camera_image`` ignores those arguments
and there is no streaming support (``_attr_supported_features`` stays 0).
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CometError
from .const import DOMAIN
from .coordinator import CometDataUpdateCoordinator
from .entity import CometEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet Comet screen camera entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CometScreenCamera(data["coordinator"], entry)])


class CometScreenCamera(CometEntity, Camera):
    """Camera entity exposing a JPEG snapshot of the captured screen."""

    _attr_name = "Screen"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the screen camera."""
        Camera.__init__(self)
        CometEntity.__init__(self, coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_screen"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current screen snapshot.

        ``width``/``height`` are accepted for the base ``Camera`` API but
        ignored -- the Comet's snapshot endpoint has no preview/resize
        support. Any error fetching the snapshot is logged and swallowed
        (returning ``None``) rather than raised, matching how Home Assistant
        expects a camera to report "no image right now".
        """
        try:
            return await self.coordinator.client.get_snapshot()
        except CometError:
            _LOGGER.debug("Failed to fetch Comet snapshot", exc_info=True)
            return None
