"""Camera platform for the GL.iNet Comet integration.

Snapshot-only: the Comet exposes a single still-image endpoint with no
width/height resizing, so ``async_camera_image`` ignores those arguments
and there is no streaming support (``_attr_supported_features`` stays 0).
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import CometError
from .coordinator import CometDataUpdateCoordinator
from .data import CometConfigEntry
from .entity import CometEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the GL.iNet Comet screen camera entity."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([CometScreenCamera(coordinator, entry)])


class CometScreenCamera(CometEntity, Camera):
    """Camera entity exposing a JPEG snapshot of the captured screen."""

    _attr_name = "Screen"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
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
