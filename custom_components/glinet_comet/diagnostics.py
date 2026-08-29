"""Diagnostics support for the GL.iNet Comet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PASSWORD, CONF_TOTP_SECRET, CONF_USERNAME, DOMAIN

TO_REDACT = {
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TOTP_SECRET,
    CONF_USERNAME,
    "token",
    "serial",
    "otg_serial",
    "mac_address",
    "ip_address",
    "gateway",
    "dns_servers",
    "hostname",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Only the config entry's data/options and the coordinator's cached state
    are serialized -- the API client object (and its live token) is never
    included.
    """
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "coordinator": coordinator.data,
        },
        TO_REDACT,
    )
