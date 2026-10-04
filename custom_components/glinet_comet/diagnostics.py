"""Diagnostics support for the GL.iNet Comet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .data import CometConfigEntry
from .const import CONF_HOST, CONF_PASSWORD, CONF_TOTP_SECRET, CONF_USERNAME

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
    hass: HomeAssistant, entry: CometConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Only the config entry's data/options and the coordinator's cached state
    are serialized -- the API client object (and its live token) is never
    included.
    """
    coordinator = entry.runtime_data.coordinator
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
