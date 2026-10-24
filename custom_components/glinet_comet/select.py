"""Select platform for the GL.iNet Comet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import CometError
from .coordinator import CometDataUpdateCoordinator
from .data import CometConfigEntry
from .entity import CometEntity
from .switch_ports import async_setup_switch_port_entities

# Sentinel option meaning "no image mounted". Never sent to the device --
# async_select_option maps it to image="" before calling set_msd_params.
MSD_IMAGE_NONE = "(none)"

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CometConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet select entities."""
    coordinator: CometDataUpdateCoordinator = entry.runtime_data.coordinator
    entities: list[SelectEntity] = [CometMsdImageSelect(coordinator, entry)]
    if parsers.mouse_outputs_available(coordinator.data or {}):
        entities.append(CometMouseOutputSelect(coordinator, entry))
    async_add_entities(entities)

    created = False

    def _port_factory(port: dict[str, Any]) -> list[SelectEntity]:
        nonlocal created
        if created:
            return []
        created = True
        return [CometActivePortSelect(coordinator, entry)]

    async_setup_switch_port_entities(entry, coordinator, async_add_entities, _port_factory)


class CometMsdImageSelect(CometEntity, SelectEntity):
    """Select entity for choosing which image is mounted via MSD."""

    _attr_translation_key = "msd_image"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
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
        ``image=""``. Applies an optimistic update to ``coordinator.data``
        afterward -- MSD writes push nothing back over the WebSocket, so
        without this the image/connected state would go stale until the
        next slow-tier poll.
        """
        data = self.coordinator.data or {}
        image = "" if option == MSD_IMAGE_NONE else option
        was_connected = parsers.msd_connected(data) is True
        try:
            if was_connected:
                await self.coordinator.client.set_msd_connected(False)
            await self.coordinator.client.set_msd_params(
                image=image, cdrom=True, rw=False
            )
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
        if self.coordinator.data is not None:
            drive: dict[str, Any] = {"image": image or None}
            if was_connected:
                drive["connected"] = False
            self.coordinator.push_partial("msd", {"drive": drive})



class CometActivePortSelect(CometEntity, SelectEntity):
    """Select the active target port on the multiport switch (Comet X).

    No optimistic update -- the device pushes ``switch`` WebSocket frames.
    """

    _attr_translation_key = "active_port"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
    ) -> None:
        """Initialize the active port select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_switch_active_port"

    @property
    def options(self) -> list[str]:
        """Return the live port display names."""
        return list(parsers.switch_port_options(self.coordinator.data or {}))

    @property
    def current_option(self) -> str | None:
        """Return the display name of the active port (``None`` if none/unknown)."""
        data = self.coordinator.data or {}
        active = parsers.switch_active_port_id(data)
        for name, port_id in parsers.switch_port_options(data).items():
            if port_id == active:
                return name
        return None

    async def async_select_option(self, option: str) -> None:
        """Switch the active port."""
        port_id = parsers.switch_port_options(self.coordinator.data or {}).get(option)
        if port_id is None:
            raise HomeAssistantError(f"Unknown port: {option}")
        try:
            await self.coordinator.client.switch_set_active(port_id)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err


class CometMouseOutputSelect(CometEntity, SelectEntity):
    """Select the HID mouse output mode (e.g. usb, usb_rel).

    No optimistic update -- the device pushes ``hid`` WebSocket frames.
    """

    _attr_translation_key = "mouse_output"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: CometConfigEntry,
    ) -> None:
        """Initialize the mouse output select."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_hid_mouse_output"

    @property
    def options(self) -> list[str]:
        """Return the available mouse outputs."""
        return parsers.mouse_outputs_available(self.coordinator.data or {})

    @property
    def current_option(self) -> str | None:
        """Return the active mouse output."""
        return parsers.mouse_output_active(self.coordinator.data or {})

    async def async_select_option(self, option: str) -> None:
        """Set the mouse output."""
        try:
            await self.coordinator.client.set_hid_mouse_output(option)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
