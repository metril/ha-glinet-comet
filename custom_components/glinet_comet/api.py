"""GL.iNet Comet API client for HTTP and WebSocket communication.

Clean-room implementation against the documented kvmd-derived contract
(``{"ok": bool, "result": {...}}`` envelope, ``Token:`` header auth obtained
from ``POST /api/auth/login``). No code is copied from GL.iNet's GPL
``glkvm``/``glkvm-comet`` sources.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from collections.abc import Awaitable, Callable
from typing import Any, Final, TypeVar
from urllib.parse import quote, urlencode

import aiohttp
import pyotp

_LOGGER = logging.getLogger(__name__)

# Seconds during which a failed login is shared with callers whose stale token
# matches, so one burst of 401s never costs more than one login attempt.
LOGIN_FAILURE_SHARE_WINDOW: Final = 30.0

_T = TypeVar("_T")

# Statuses that mean "the token is no longer good, try to re-auth".
_AUTH_FAILURE_STATUSES = (401, 403)


class CometError(Exception):
    """Base exception for all Comet API errors."""


class CometAuthError(CometError):
    """Raised when authentication with the Comet fails."""


class CometConnectionError(CometError):
    """Raised when the Comet cannot be reached."""


class CometApiError(CometError):
    """Raised when the Comet API returns an error response.

    ``status`` is the HTTP status for a non-200 response, ``200`` for a
    200 response with an ``ok: false`` envelope (or a missing token on
    login), or ``None`` when the body couldn't be parsed as JSON at all.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        """Initialize with the error message and the originating HTTP status."""
        self.status = status
        super().__init__(message)


class CometRateLimitError(CometError):
    """Raised when the Comet's login endpoint is rate-limiting us."""

    def __init__(self, remaining_time: int) -> None:
        """Initialize with the number of seconds to wait before retrying."""
        self.remaining_time = remaining_time
        super().__init__(f"login rate-limited; retry after {remaining_time}s")


class CometApiClient:
    """Client for the GL.iNet Comet HTTP API and WebSocket event stream."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        totp_secret: str = "",
        verify_ssl: bool = False,
        http_timeout: int = 10,
    ) -> None:
        """Initialize the Comet API client."""
        self._session = session
        self._host = self._normalize_host(host)
        self._base_url = f"https://{self._host}"
        self._username = username
        self._password = password
        self._totp_secret = totp_secret
        self._verify_ssl = verify_ssl
        self._http_timeout = http_timeout
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._login_gen: int = 0
        self._last_login_error: CometError | None = None
        self._login_failed_for: str | None = None
        self._login_failed_at: float = 0.0

    @staticmethod
    def _normalize_host(host: str) -> str:
        """Strip any scheme and trailing slash from a user-provided host."""
        host = host.strip()
        for prefix in ("https://", "http://"):
            if host.lower().startswith(prefix):
                host = host[len(prefix) :]
                break
        return host.rstrip("/")

    @property
    def _ssl(self) -> bool | None:
        """aiohttp ssl kwarg: False disables verification, None verifies."""
        return False if not self._verify_ssl else None

    # --- Login / logout ---

    def _login_password(self) -> str:
        """Build the login password, appending a fresh TOTP code if configured."""
        if self._totp_secret:
            return f"{self._password}{pyotp.TOTP(self._totp_secret).now()}"
        return self._password

    async def async_login(self) -> None:
        """Log in to the Comet, storing a fresh token.

        A new TOTP code is computed for every call (never cached/reused).
        """
        passwd = self._login_password()
        method, path = "POST", "/api/auth/login"
        _LOGGER.debug("Comet API: %s %s", method, path)
        # allow_redirects=False: a 307/308 would silently re-send the login
        # form (credentials included) to another URL -- one extra attempt
        # that counts toward the device's 10-failure lockout for no reason.
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                data={"user": self._username, "passwd": passwd, "expire": "0"},
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                allow_redirects=False,
            ) as resp:
                _LOGGER.debug(
                    "Comet API response: %s %s -> HTTP %s", method, path, resp.status
                )
                if resp.status == 429:
                    raise CometRateLimitError(await self._read_remaining_time(resp))
                if resp.status == 403:
                    raise CometAuthError("password or 2FA code rejected")
                if resp.status == 401:
                    raise CometAuthError("authentication failed")
                if resp.status != 200:
                    await self._raise_generic_error(method, path, resp)
                body = await self._read_json(method, path, resp)
                if not isinstance(body, dict) or not body.get("ok", False):
                    raise CometApiError(self._envelope_error_message(body), status=200)
                result = body.get("result")
                if result is None:
                    result = {}
                if not isinstance(result, dict):
                    raise CometApiError("unexpected result shape", status=200)
                if result.get("two_step_required"):
                    raise CometAuthError(
                        "two-step approval is enabled on the Comet; disable it"
                    )
                token = result.get("token") or body.get("token")
                if not token:
                    raise CometApiError(
                        "login response did not include a token", status=200
                    )
                self._token = token
                self._last_login_error = None
        except CometError:
            raise
        except asyncio.TimeoutError as err:
            raise CometConnectionError(f"{method} {path}: timed out") from err
        except aiohttp.ClientConnectorError as err:
            raise CometConnectionError(f"{method} {path}: failed to connect") from err
        except aiohttp.ClientError as err:
            raise CometConnectionError(f"{method} {path}: connection error") from err

    async def async_logout(self) -> None:
        """Log out from the Comet, invalidating the token. Errors are ignored."""
        token = self._token
        self._token = None
        if token is None:
            return
        method, path = "POST", "/api/auth/logout"
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers={"Token": token},
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                allow_redirects=False,
            ):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            _LOGGER.debug("Comet API: %s %s failed (ignored)", method, path)

    async def test_connection(self) -> dict[str, Any]:
        """Verify credentials/connectivity; returns the system info result.

        ``allow_reauth=False``: a 401/403 immediately after a fresh
        ``async_login()`` is a real failure, not a stale token -- retrying
        would burn a second login attempt against the device's lockout
        counter for one validation.
        """
        if self._token is None:
            await self.async_login()
        return await self.get_info(fields="system", allow_reauth=False)

    # --- Token lifecycle ---

    async def _get_token(self) -> str:
        """Return the current token, logging in first if there isn't one."""
        if self._token is not None:
            return self._token
        return await self._reauth(None)

    async def _reauth(self, stale_token: str | None) -> str:
        """Re-login under a lock, reusing a token another caller already got.

        A burst of concurrent requests that all 401 on the same stale token
        must cost at most ONE login attempt against the device's lockout
        counter: waiters share the winner's token, and callers arriving after
        a login that just failed for the same stale token (within
        ``LOGIN_FAILURE_SHARE_WINDOW``) re-raise that failure instead of
        trying again.
        """
        gen = self._login_gen
        async with self._login_lock:
            if self._token is not None and self._token != stale_token:
                return self._token
            if (
                self._last_login_error is not None
                and self._login_failed_for == stale_token
                and (
                    self._login_gen != gen
                    or monotonic() - self._login_failed_at < LOGIN_FAILURE_SHARE_WINDOW
                )
            ):
                raise self._last_login_error
            try:
                await self.async_login()
            except CometError as err:
                self._last_login_error = err
                self._login_failed_for = stale_token
                self._login_failed_at = monotonic()
                self._login_gen += 1
                raise
            self._last_login_error = None
            self._login_failed_for = None
            self._login_gen += 1
        assert self._token is not None  # noqa: S101 - async_login always sets it
        return self._token

    # --- Request plumbing ---

    async def _send(
        self,
        method: str,
        path: str,
        reader: Callable[[aiohttp.ClientResponse], Awaitable[_T]],
        *,
        allow_reauth: bool = True,
        **kwargs: Any,
    ) -> _T:
        """Issue an authenticated request, re-login-and-retry once on 401/403.

        ``allow_reauth=False`` raises ``CometAuthError`` on the first 401/403
        instead of retrying -- for call sites (like ``test_connection``)
        where a failure right after a fresh login is a real auth error, not
        a stale token, and a blind retry would just be a second login
        attempt against the device's lockout counter.
        """
        extra_headers = kwargs.pop("headers", None) or {}
        url = f"{self._base_url}{path}"
        attempts = 2 if allow_reauth else 1
        for attempt in range(attempts):
            token = await self._get_token()
            headers = {**extra_headers, "Token": token}
            _LOGGER.debug("Comet API: %s %s", method, path)
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    ssl=self._ssl,
                    timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                    **kwargs,
                ) as resp:
                    _LOGGER.debug(
                        "Comet API response: %s %s -> HTTP %s",
                        method,
                        path,
                        resp.status,
                    )
                    if resp.status in _AUTH_FAILURE_STATUSES:
                        if allow_reauth and attempt == 0:
                            await self._reauth(token)
                            continue
                        raise CometAuthError(f"{method} {path}: authentication failed")
                    if resp.status != 200:
                        await self._raise_generic_error(method, path, resp)
                    return await reader(resp)
            except CometError:
                raise
            except asyncio.TimeoutError as err:
                raise CometConnectionError(f"{method} {path}: timed out") from err
            except aiohttp.ClientConnectorError as err:
                raise CometConnectionError(f"{method} {path}: failed to connect") from err
            except aiohttp.ClientError as err:
                raise CometConnectionError(f"{method} {path}: connection error") from err
        raise CometAuthError(f"{method} {path}: authentication failed")

    async def _request(
        self, method: str, path: str, *, allow_reauth: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        """Make an authenticated request, returning the parsed ``result`` dict."""

        async def _reader(resp: aiohttp.ClientResponse) -> dict[str, Any]:
            return await self._parse_ok_body(method, path, resp)

        return await self._send(
            method, path, _reader, allow_reauth=allow_reauth, **kwargs
        )

    async def _request_raw(self, method: str, path: str, **kwargs: Any) -> bytes:
        """Make an authenticated request, returning the raw response bytes."""

        async def _reader(resp: aiohttp.ClientResponse) -> bytes:
            return await resp.read()

        return await self._send(method, path, _reader, **kwargs)

    @staticmethod
    async def _read_json(method: str, path: str, resp: aiohttp.ClientResponse) -> Any:
        """Parse a response body as JSON, raising CometApiError on bad bodies."""
        try:
            return await resp.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError) as err:
            raise CometApiError(
                f"{method} {path}: invalid response body", status=None
            ) from err

    @staticmethod
    def _envelope_error_message(body: Any) -> str:
        """Build a CometApiError message from an ``{"ok": false, ...}`` envelope."""
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, dict):
            result = {}
        error = result.get("error", "error")
        error_msg = result.get("error_msg", "")
        return f"{error}: {error_msg}"

    async def _parse_ok_body(
        self, method: str, path: str, resp: aiohttp.ClientResponse
    ) -> dict[str, Any]:
        """Parse a 200 response body, raising CometApiError on ``ok: false``."""
        body = await self._read_json(method, path, resp)
        if not isinstance(body, dict) or not body.get("ok", False):
            raise CometApiError(self._envelope_error_message(body), status=200)
        result = body.get("result")
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise CometApiError("unexpected result shape", status=200)
        return result

    async def _raise_generic_error(
        self, method: str, path: str, resp: aiohttp.ClientResponse
    ) -> None:
        """Raise CometApiError for a non-200 response (JSON envelope or plain text)."""
        try:
            body = await resp.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            body = None
        if isinstance(body, dict):
            result = body.get("result")
            if not isinstance(result, dict):
                result = {}
            error = result.get("error", "")
            error_msg = result.get("error_msg", "")
            if error or error_msg:
                raise CometApiError(f"{error}: {error_msg}", status=resp.status)
        text = await resp.text()
        raise CometApiError(
            f"{method} {path}: HTTP {resp.status}: {text[:200]}", status=resp.status
        )

    @staticmethod
    async def _read_remaining_time(resp: aiohttp.ClientResponse) -> int:
        """Best-effort extraction of ``result.remaining_time`` from a 429 body."""
        try:
            body = await resp.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            return 600
        if isinstance(body, dict):
            result = body.get("result")
            if isinstance(result, dict):
                try:
                    return max(1, int(float(result.get("remaining_time"))))
                except (TypeError, ValueError, OverflowError):
                    return 600
        return 600

    # --- Reads ---

    async def get_info(
        self, fields: str = "system,health", *, allow_reauth: bool = True
    ) -> dict[str, Any]:
        """Fetch system/health info."""
        return await self._request(
            "GET",
            f"/api/info?{urlencode({'fields': fields})}",
            allow_reauth=allow_reauth,
        )

    async def get_atx(self) -> dict[str, Any]:
        """Fetch ATX power state."""
        return await self._request("GET", "/api/atx")

    async def get_hid(self) -> dict[str, Any]:
        """Fetch HID device state."""
        return await self._request("GET", "/api/hid")

    async def get_msd(self) -> dict[str, Any]:
        """Fetch mass-storage-device state."""
        return await self._request("GET", "/api/msd")

    async def get_streamer(self) -> dict[str, Any]:
        """Fetch video streamer state."""
        return await self._request("GET", "/api/streamer")

    async def get_gpio(self) -> dict[str, Any]:
        """Fetch GPIO state and model."""
        return await self._request("GET", "/api/gpio")

    async def get_hostname(self) -> dict[str, Any]:
        """Fetch the device hostname."""
        return await self._request("GET", "/api/system/get_hostname")

    async def get_network_config(self) -> dict[str, Any]:
        """Fetch the network configuration."""
        return await self._request("GET", "/api/system/get_network_config")

    async def get_upgrade_version(self) -> dict[str, Any]:
        """Fetch the available upgrade version info."""
        return await self._request("GET", "/api/upgrade/version")

    async def get_upgrade_compare(self) -> dict[str, Any]:
        """Fetch the upgrade version comparison."""
        return await self._request("GET", "/api/upgrade/compare")

    # --- ATX actions ---

    async def atx_click(self, button: str) -> None:
        """Simulate an ATX button press (power, power_long, reset)."""
        await self._request("POST", f"/api/atx/click?{urlencode({'button': button})}")

    async def atx_power(self, action: str) -> None:
        """Control ATX power (on, off, off_hard, reset_hard)."""
        await self._request("POST", f"/api/atx/power?{urlencode({'action': action})}")

    # --- HID actions ---

    async def set_hid_jiggler(self, enabled: bool) -> None:
        """Enable or disable the HID jiggler."""
        value = "1" if enabled else "0"
        await self._request("POST", f"/api/hid/set_params?jiggler={value}")

    async def set_hid_connected(self, connected: bool) -> None:
        """Connect or disconnect the HID device."""
        value = "1" if connected else "0"
        await self._request("POST", f"/api/hid/set_connected?connected={value}")

    async def reset_hid(self) -> None:
        """Reset HID to its default state."""
        await self._request("POST", "/api/hid/reset")

    async def type_text(self, text: str, keymap: str = "en") -> None:
        """Type text on the remote system."""
        await self._request(
            "POST",
            f"/api/hid/print?{urlencode({'keymap': keymap})}",
            data=text,
        )

    async def send_shortcut(self, keys: str) -> None:
        """Send a keyboard shortcut (comma-separated key names)."""
        await self._request(
            "POST", f"/api/hid/events/send_shortcut?{urlencode({'keys': keys})}"
        )

    # --- MSD actions ---

    async def set_msd_connected(self, connected: bool) -> None:
        """Connect or disconnect the mass storage device."""
        value = "1" if connected else "0"
        await self._request("POST", f"/api/msd/set_connected?connected={value}")

    async def set_msd_params(
        self, image: str, cdrom: bool = True, rw: bool = False
    ) -> None:
        """Set MSD parameters. MSD must be disconnected before calling this."""
        query = urlencode(
            {"image": image, "cdrom": "1" if cdrom else "0", "rw": "1" if rw else "0"}
        )
        await self._request("POST", f"/api/msd/set_params?{query}")

    # --- GPIO actions ---

    async def gpio_switch(self, channel: str, state: bool) -> None:
        """Switch a GPIO output channel on or off."""
        value = "1" if state else "0"
        query = urlencode({"channel": channel, "state": value})
        await self._request("POST", f"/api/gpio/switch?{query}")

    async def gpio_pulse(self, channel: str, delay: float = 0) -> None:
        """Pulse a GPIO output channel for ``delay`` seconds."""
        query = urlencode({"channel": channel, "delay": delay})
        await self._request("POST", f"/api/gpio/pulse?{query}")

    # --- Upgrade actions ---

    async def reboot(self) -> None:
        """Reboot the Comet.

        DESTRUCTIVE: this is a GET-triggered route on the device (glkvm
        registers it as ``@exposed_http("GET", "/upgrade/reboot")``; POST
        gets a 405). The handler replies immediately and reboots ~1s later.
        Never call this from a read/poll path, and never call it against a
        real device in a test.
        """
        await self._request("GET", "/api/upgrade/reboot")

    # --- Snapshot ---

    async def get_snapshot(self) -> bytes | None:
        """Fetch a JPEG snapshot from the video streamer, or None if unavailable."""
        data = await self._request_raw("GET", "/api/streamer/snapshot?allow_offline=1")
        if data.startswith(b"\xff\xd8"):
            return data
        return None

    # --- WebSocket ---

    async def connect_ws(
        self, stream: bool = True
    ) -> aiohttp.ClientWebSocketResponse:
        """Open the WebSocket event stream, re-login-and-retry once on 401/403.

        ``stream=True`` (default) requests ``stream=1``: kvmd only runs video
        capture while a ``stream=1`` WS client is connected — otherwise
        ``streamer.streamer`` stays null and snapshots 503. Pass
        ``stream=False`` for a state-only connection (``stream=0``) that
        doesn't need to keep the video pipeline active.
        """
        path = "/api/ws"
        stream_flag = "1" if stream else "0"
        for attempt in range(2):
            token = await self._get_token()
            url = (
                f"wss://{self._host}{path}"
                f"?stream={stream_flag}&auth_token={quote(token, safe='')}"
            )
            try:
                return await self._session.ws_connect(
                    url, ssl=self._ssl, heartbeat=30
                )
            except aiohttp.WSServerHandshakeError as err:
                # `from None`: err (and its request_info.real_url) embeds the
                # wss:// URL, which carries auth_token=<token> in its query
                # string. Chaining it would leak the token into any logged
                # traceback. The messages below already carry err.status.
                if err.status in _AUTH_FAILURE_STATUSES:
                    if attempt == 0:
                        await self._reauth(token)
                        continue
                    raise CometAuthError("WebSocket authentication failed") from None
                raise CometConnectionError(
                    f"WebSocket handshake failed: HTTP {err.status}"
                ) from None
            except asyncio.TimeoutError:
                raise CometConnectionError("WebSocket connection timed out") from None
            except aiohttp.ClientError:
                raise CometConnectionError("WebSocket connection failed") from None
        raise CometAuthError("WebSocket authentication failed")
