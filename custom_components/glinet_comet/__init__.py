"""The GL.iNet Comet integration."""

from __future__ import annotations

import contextlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
from .services import async_setup_services, async_unload_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async_setup_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a GL.iNet Comet config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is not None:
        coordinator: CometDataUpdateCoordinator = data["coordinator"]
        client: CometApiClient = data["client"]
        await coordinator.async_stop()
        with contextlib.suppress(CometError):
            await client.async_logout()
        hass.data[DOMAIN].pop(entry.entry_id, None)

    async_unload_services(hass)

    return unload_ok
