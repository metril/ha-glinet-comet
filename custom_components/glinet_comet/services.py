"""Device-scoped HID services for the GL.iNet Comet integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import CometApiClient, CometError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPE_TEXT = "type_text"
SERVICE_SEND_SHORTCUT = "send_shortcut"

ATTR_DEVICE_ID = "device_id"
ATTR_TEXT = "text"
ATTR_KEYMAP = "keymap"
ATTR_KEYS = "keys"

DEFAULT_KEYMAP = "en"

TYPE_TEXT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_TEXT): cv.string,
        vol.Optional(ATTR_KEYMAP, default=DEFAULT_KEYMAP): cv.string,
    }
)

SEND_SHORTCUT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_KEYS): cv.string,
    }
)


def _client_for_device(hass: HomeAssistant, device_id: str) -> CometApiClient:
    """Resolve a device_id to its GL.iNet Comet API client."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Unknown device: {device_id}")

    for entry_id in device.config_entries:
        data = hass.data.get(DOMAIN, {}).get(entry_id)
        if data:
            return data["client"]

    raise HomeAssistantError(f"Device {device_id} is not a GL.iNet Comet")


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the GL.iNet Comet HID services once per Home Assistant instance."""

    async def _handle_type_text(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            await client.type_text(call.data[ATTR_TEXT], call.data[ATTR_KEYMAP])
        except CometError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_send_shortcut(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            await client.send_shortcut(call.data[ATTR_KEYS])
        except CometError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_TYPE_TEXT):
        hass.services.async_register(
            DOMAIN, SERVICE_TYPE_TEXT, _handle_type_text, schema=TYPE_TEXT_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_SHORTCUT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_SHORTCUT,
            _handle_send_shortcut,
            schema=SEND_SHORTCUT_SCHEMA,
        )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the GL.iNet Comet services once no config entries remain."""
    if hass.data.get(DOMAIN):
        return
    for service in (SERVICE_TYPE_TEXT, SERVICE_SEND_SHORTCUT):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
