"""The GL.iNet Comet integration."""

from __future__ import annotations

import contextlib

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import CometApiClient, CometError
from .const import (
    CONF_HOST,
    CONF_HTTP_TIMEOUT,
    CONF_PASSWORD,
    CONF_TOTP_SECRET,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .coordinator import CometDataUpdateCoordinator
from .data import CometConfigEntry, CometRuntimeData
from .services import async_setup_services

__all__ = ["CometConfigEntry", "CometRuntimeData"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CometConfigEntry) -> bool:
    """Set up GL.iNet Comet from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    http_timeout = entry.options.get(CONF_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT)

    client = CometApiClient(
        session,
        entry.data[CONF_HOST],
        entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
        entry.data[CONF_PASSWORD],
        totp_secret=entry.data.get(CONF_TOTP_SECRET, ""),
        verify_ssl=verify_ssl,
        http_timeout=http_timeout,
    )

    coordinator = CometDataUpdateCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = CometRuntimeData(coordinator, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: CometConfigEntry) -> bool:
    """Unload a GL.iNet Comet config entry.

    Teardown (stopping the WS loop, logging out) only runs once the platforms actually unloaded --
    otherwise HA still holds live entities pointing at a client/coordinator
    we'd have just torn down. Services are integration-level and stay
    registered.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = entry.runtime_data
        await data.coordinator.async_stop()
        with contextlib.suppress(CometError):
            await data.client.async_logout()

    return unload_ok
