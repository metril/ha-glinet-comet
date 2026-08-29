"""Config flow for the GL.iNet Comet integration."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import (
    CometApiClient,
    CometAuthError,
    CometConnectionError,
    CometError,
    CometRateLimitError,
)
from .const import (
    CONF_ENABLE_ATX,
    CONF_HOST,
    CONF_HTTP_TIMEOUT,
    CONF_KEEP_VIDEO_ACTIVE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOTP_SECRET,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    CONF_WS_RECONNECT_DELAY,
    DEFAULT_ENABLE_ATX,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_KEEP_VIDEO_ACTIVE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USERNAME,
    DEFAULT_VERIFY_SSL,
    DEFAULT_WS_RECONNECT_DELAY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_host(host: str) -> str:
    """Strip any scheme and trailing slash from a user-provided host."""
    host = host.strip()
    for prefix in ("https://", "http://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix) :]
            break
    return host.rstrip("/")


def _unique_id_from_info(info: dict[str, Any], host: str) -> str:
    """Return a stable unique id: the device serial, falling back to host."""
    try:
        serial = info["system"]["platform"]["serial"]
    except (KeyError, TypeError):
        serial = None
    return str(serial) if serial else host


def _step_user_schema() -> vol.Schema:
    """Schema for the initial user step (no pre-filled defaults)."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(CONF_TOTP_SECRET, default=""): str,
            vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        }
    )


def _step_reconfigure_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for reconfigure, pre-filled from the existing entry data."""
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults[CONF_HOST]): str,
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
            ): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(
                CONF_TOTP_SECRET, default=defaults.get(CONF_TOTP_SECRET, "")
            ): str,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


def _step_reauth_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Schema for reauth confirmation, pre-filled from the existing entry data."""
    return vol.Schema(
        {
            vol.Required(CONF_PASSWORD): str,
            vol.Optional(
                CONF_TOTP_SECRET, default=defaults.get(CONF_TOTP_SECRET, "")
            ): str,
        }
    )


class CometConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GL.iNet Comet."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_HOST] = _normalize_host(user_input[CONF_HOST])
            error, extra = await self._async_validate(user_input)
            if error:
                errors["base"] = error
                description_placeholders = extra
            else:
                host = user_input[CONF_HOST]
                await self.async_set_unique_id(_unique_id_from_info(extra, host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Comet ({host})", data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_step_user_schema(),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth when credentials become invalid."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth with a new password/2FA secret."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            updated_data = {**reauth_entry.data, **user_input}
            error, extra = await self._async_validate(updated_data)
            if error:
                errors["base"] = error
                description_placeholders = extra
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data=updated_data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_step_reauth_schema(reauth_entry.data),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            user_input[CONF_HOST] = _normalize_host(user_input[CONF_HOST])
            error, extra = await self._async_validate(user_input)
            if error:
                errors["base"] = error
                description_placeholders = extra
            else:
                host = user_input[CONF_HOST]
                await self.async_set_unique_id(_unique_id_from_info(extra, host))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reconfigure_entry, data=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_step_reconfigure_schema(reconfigure_entry.data),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    async def _async_validate(
        self, data: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        """Attempt exactly one login against the Comet; never retry.

        Returns ``(error_code, extra)``. On success ``extra`` is the
        ``system`` info dict from ``test_connection()`` (used for the unique
        id). On a rate-limit error, ``extra`` is ``{"seconds": ...}`` for use
        as ``description_placeholders``. Otherwise ``extra`` is empty.
        """
        verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)
        client = CometApiClient(
            session,
            data[CONF_HOST],
            data.get(CONF_USERNAME, DEFAULT_USERNAME),
            data[CONF_PASSWORD],
            totp_secret=data.get(CONF_TOTP_SECRET, ""),
            verify_ssl=verify_ssl,
        )
        try:
            info = await client.test_connection()
        except CometAuthError:
            return "invalid_auth", {}
        except CometRateLimitError as err:
            return "rate_limited", {"seconds": str(err.remaining_time)}
        except CometConnectionError:
            return "cannot_connect", {}
        except Exception:  # noqa: BLE001 - guard against any other flow-breaking error
            _LOGGER.exception("Unexpected error validating Comet connection")
            return "unknown", {}
        else:
            return None, info
        finally:
            with contextlib.suppress(CometError):
                await client.async_logout()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CometOptionsFlow:
        """Return the options flow handler."""
        return CometOptionsFlow()


class CometOptionsFlow(OptionsFlow):
    """Handle GL.iNet Comet options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=60, max=3600, step=30, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_WS_RECONNECT_DELAY,
                        default=options.get(
                            CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=60, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_HTTP_TIMEOUT,
                        default=options.get(
                            CONF_HTTP_TIMEOUT, DEFAULT_HTTP_TIMEOUT
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=5, max=60, step=1, mode=NumberSelectorMode.SLIDER
                        )
                    ),
                    vol.Required(
                        CONF_ENABLE_ATX,
                        default=options.get(CONF_ENABLE_ATX, DEFAULT_ENABLE_ATX),
                    ): bool,
                    vol.Required(
                        CONF_KEEP_VIDEO_ACTIVE,
                        default=options.get(
                            CONF_KEEP_VIDEO_ACTIVE, DEFAULT_KEEP_VIDEO_ACTIVE
                        ),
                    ): bool,
                }
            ),
        )
