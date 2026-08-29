"""Switch platform for the GL.iNet Comet integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import parsers
from .api import CometApiClient, CometError
from .const import DOMAIN
from .coordinator import CometDataUpdateCoordinator
from .entity import CometEntity


def _set_jiggler_enabled(data: dict[str, Any], value: bool) -> None:
    """Optimistically set ``hid.jiggler.enabled`` in coordinator state."""
    hid = data.setdefault("hid", {})
    jiggler = hid.get("jiggler")
    if not isinstance(jiggler, dict):
        jiggler = {}
        hid["jiggler"] = jiggler
    jiggler["enabled"] = value


def _set_hid_connected(data: dict[str, Any], value: bool) -> None:
    """Optimistically set ``hid.connected`` in coordinator state."""
    data.setdefault("hid", {})["connected"] = value


def _set_msd_drive_connected(data: dict[str, Any], value: bool) -> None:
    """Optimistically set ``msd.drive.connected`` in coordinator state."""
    data.setdefault("msd", {}).setdefault("drive", {})["connected"] = value


@dataclass(frozen=True, kw_only=True)
class CometSwitchDescription(SwitchEntityDescription):
    """Describes a GL.iNet Comet switch."""

    value_fn: Callable[[dict[str, Any]], bool | None] = lambda data: None
    turn_on_fn: Callable[[CometApiClient], Coroutine[Any, Any, None]]
    turn_off_fn: Callable[[CometApiClient], Coroutine[Any, Any, None]]
    # Applied to coordinator.data right after a successful write, then pushed via
    # async_set_updated_data -- needed for state the device doesn't push back over
    # the WebSocket event stream.
    optimistic_fn: Callable[[dict[str, Any], bool], None] | None = None


SWITCHES: tuple[CometSwitchDescription, ...] = (
    CometSwitchDescription(
        key="hid_jiggler",
        name="Mouse Jiggler",
        icon="mdi:mouse-move-vertical",
        value_fn=parsers.jiggler_enabled,
        turn_on_fn=lambda client: client.set_hid_jiggler(True),
        turn_off_fn=lambda client: client.set_hid_jiggler(False),
        optimistic_fn=_set_jiggler_enabled,
    ),
    CometSwitchDescription(
        key="hid_connected",
        name="HID Connected",
        icon="mdi:usb",
        value_fn=parsers.hid_connected,
        turn_on_fn=lambda client: client.set_hid_connected(True),
        turn_off_fn=lambda client: client.set_hid_connected(False),
        optimistic_fn=_set_hid_connected,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GL.iNet Comet switches."""
    coordinator: CometDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    entities: list[SwitchEntity] = [
        CometSwitch(coordinator, entry, desc) for desc in SWITCHES
    ]
    entities.append(CometMsdConnectedSwitch(coordinator, entry))

    data = coordinator.data or {}
    for channel, config in parsers.gpio_model_channels(data, "outputs").items():
        if config.get("switch") is True:
            entities.append(CometGpioSwitch(coordinator, entry, channel))

    async_add_entities(entities)


class CometSwitch(CometEntity, SwitchEntity):
    """A GL.iNet Comet switch driven by a ``CometSwitchDescription``."""

    entity_description: CometSwitchDescription

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
        description: CometSwitchDescription,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the switch state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._set(False)

    async def _set(self, value: bool) -> None:
        """Issue the write, then apply the optimistic update if one is defined."""
        fn = (
            self.entity_description.turn_on_fn
            if value
            else self.entity_description.turn_off_fn
        )
        try:
            await fn(self.coordinator.client)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
        optimistic_fn = self.entity_description.optimistic_fn
        if optimistic_fn is not None and self.coordinator.data is not None:
            optimistic_fn(self.coordinator.data, value)
            self.coordinator.async_set_updated_data(self.coordinator.data)


class CometMsdConnectedSwitch(CometEntity, SwitchEntity):
    """Switch to connect/disconnect the virtual mass-storage device.

    Unavailable whenever no MSD image is currently selected (the ``MSD Image``
    select entity must be used first).
    """

    _attr_name = "Virtual Media"
    _attr_icon = "mdi:usb-flash-drive"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the MSD connected switch."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_msd_connected"

    @property
    def is_on(self) -> bool | None:
        """Return whether the virtual drive is connected to the target host."""
        if self.coordinator.data is None:
            return None
        return parsers.msd_connected(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True only if the coordinator is available and an image is set."""
        if not super().available or self.coordinator.data is None:
            return False
        return bool(parsers.msd_image(self.coordinator.data))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Connect the virtual drive; refuses if no image is selected."""
        data = self.coordinator.data or {}
        if not parsers.msd_image(data):
            raise HomeAssistantError("Select an MSD image first")
        try:
            await self.coordinator.client.set_msd_connected(True)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
        if self.coordinator.data is not None:
            _set_msd_drive_connected(self.coordinator.data, True)
            self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disconnect the virtual drive."""
        try:
            await self.coordinator.client.set_msd_connected(False)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
        if self.coordinator.data is not None:
            _set_msd_drive_connected(self.coordinator.data, False)
            self.coordinator.async_set_updated_data(self.coordinator.data)


class CometGpioSwitch(CometEntity, SwitchEntity):
    """A GPIO output channel exposed as a switch.

    No optimistic update -- the device pushes ``gpio`` WebSocket frames.
    """

    _attr_icon = "mdi:electric-switch"

    def __init__(
        self,
        coordinator: CometDataUpdateCoordinator,
        entry: ConfigEntry,
        channel: str,
    ) -> None:
        """Initialize the GPIO switch."""
        super().__init__(coordinator, entry)
        self._channel = channel
        self._attr_unique_id = f"{entry.entry_id}_gpio_out_{channel}"
        labels = (coordinator.data or {}).get("gpio_labels")
        self._attr_name = parsers.gpio_display_name(channel, labels)

    @property
    def is_on(self) -> bool | None:
        """Return the GPIO output state."""
        if self.coordinator.data is None:
            return None
        channel = parsers.gpio_channel(self.coordinator.data, "outputs", self._channel)
        return channel.get("state")

    @property
    def available(self) -> bool:
        """Return True only if the coordinator is available and the channel is online."""
        if not super().available or self.coordinator.data is None:
            return False
        channel = parsers.gpio_channel(self.coordinator.data, "outputs", self._channel)
        return channel.get("online") is True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the GPIO output on."""
        try:
            await self.coordinator.client.gpio_switch(self._channel, True)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the GPIO output off."""
        try:
            await self.coordinator.client.gpio_switch(self._channel, False)
        except CometError as err:
            raise HomeAssistantError(str(err)) from err
