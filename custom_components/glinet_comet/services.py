"""Device-scoped HID services for the GL.iNet Comet integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import CometApiClient, CometError
from .const import DOMAIN

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
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )

    loaded = {e.entry_id for e in hass.config_entries.async_loaded_entries(DOMAIN)}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not any(
            d.id == device_id
            for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        ):
            continue
        if entry.entry_id in loaded:
            return entry.runtime_data.client
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"device_id": device_id},
        )

    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="not_comet_device",
        translation_placeholders={"device_id": device_id},
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the GL.iNet Comet HID services from the integration-level ``async_setup``."""

    async def _handle_type_text(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            await client.type_text(call.data[ATTR_TEXT], call.data[ATTR_KEYMAP])
        except CometError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _handle_send_shortcut(call: ServiceCall) -> None:
        client = _client_for_device(hass, call.data[ATTR_DEVICE_ID])
        try:
            await client.send_shortcut(call.data[ATTR_KEYS])
        except CometError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

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
