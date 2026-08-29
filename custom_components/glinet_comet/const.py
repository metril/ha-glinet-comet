"""Constants for the GL.iNet Comet integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "glinet_comet"

MANUFACTURER: Final = "GL.iNet"
DEFAULT_MODEL: Final = "Comet"

CONF_HOST: Final = "host"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_TOTP_SECRET: Final = "totp_secret"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_WS_RECONNECT_DELAY: Final = "ws_reconnect_delay"
CONF_HTTP_TIMEOUT: Final = "http_timeout"
CONF_ENABLE_ATX: Final = "enable_atx"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_SCAN_INTERVAL: Final = 300
DEFAULT_WS_RECONNECT_DELAY: Final = 5
DEFAULT_HTTP_TIMEOUT: Final = 10
DEFAULT_ENABLE_ATX: Final = True
DEFAULT_VERIFY_SSL: Final = False

SLOW_READ_INTERVAL: Final = 6 * 3600
