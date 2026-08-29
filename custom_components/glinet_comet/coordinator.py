"""WebSocket-push coordinator for the GL.iNet Comet integration.

Fast state (``atx``/``hid``/``msd``/``streamer``/``gpio``/``info``) is pushed
over the WebSocket event stream (``/api/ws``); a slow HTTP-poll tier
(``update_interval``) covers GL.iNet-specific reads the socket doesn't carry
(hostname/network/upgrade info) and re-polls ``atx``, ``hid``, ``msd``, and
``streamer`` every cycle as a fallback source of truth -- MSD writes in
particular push nothing back over the WebSocket, so those subsystems can't
rely on WS push alone. ``gpio`` is still fetched once, on the first run
only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CometApiClient,
    CometApiError,
    CometAuthError,
    CometConnectionError,
    CometRateLimitError,
)
from .const import (
    CONF_KEEP_VIDEO_ACTIVE,
    CONF_SCAN_INTERVAL,
    CONF_WS_RECONNECT_DELAY,
    DEFAULT_KEEP_VIDEO_ACTIVE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WS_RECONNECT_DELAY,
    DOMAIN,
    SLOW_READ_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _deep_merge(dest: dict[str, Any], src: dict[str, Any]) -> None:
    """Recursively merge ``src`` into ``dest`` in place.

    A partial update (e.g. a WS frame carrying only ``{"busy": true}``) must
    never wipe sibling keys already present in ``dest``.
    """
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dest.get(key), dict):
            _deep_merge(dest[key], value)
        else:
            dest[key] = value


class CometDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator combining a slow HTTP-poll tier with WebSocket push."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: CometApiClient
    ) -> None:
        """Initialize the coordinator and its empty state skeleton."""
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry = entry
        self._keep_video_active = entry.options.get(
            CONF_KEEP_VIDEO_ACTIVE, DEFAULT_KEEP_VIDEO_ACTIVE
        )
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_task: asyncio.Task[None] | None = None
        self._first_run = True
        self._ws_has_connected_once = False
        self._last_upgrade_compare: float | None = None
        self._state: dict[str, Any] = {
            "atx": {},
            "hid": {},
            "msd": {},
            "streamer": {},
            "gpio": {"inputs": {}, "outputs": {}},
            "gpio_model": {"inputs": {}, "outputs": {}},
            "gpio_labels": {},
            "info": {},
            "glinet": {},
            "unsupported": [],
            "ws_connected": False,
        }
        self._ws_handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "atx": self._process_atx_event,
            "hid": self._process_hid_event,
            "msd": self._process_msd_event,
            "streamer": self._process_streamer_event,
            "gpio": self._process_gpio_event,
            "info": self._process_info_event,
        }

    # --- Public properties --------------------------------------------------

    @property
    def atx_enabled(self) -> bool:
        """Return True only when the device confirms ATX hardware is enabled."""
        return self._state["atx"].get("enabled") is True

    @property
    def ws_connected(self) -> bool:
        """Return whether the WebSocket event stream is currently connected."""
        return bool(self._state.get("ws_connected"))

    # --- Slow HTTP poll tier -------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll the slow HTTP tier; bootstrap WS-only subsystems on the first run."""
        # Read (don't consume) the flag: if this cycle raises, the bootstrap
        # reads below are discarded along with everything else, so the next
        # cycle must retry them rather than skip them forever. It's only
        # marked done once this cycle actually succeeds (see the `return`).
        first_run = self._first_run
        unsupported: list[str] = self._state["unsupported"]

        core: dict[str, Any] = {
            "atx": self.client.get_atx(),
            "info_system": self.client.get_info("system"),
            # hid/msd/streamer are read every cycle, not just bootstrapped on
            # first_run: no WS event carries hid/msd/streamer writes back
            # (MSD in particular pushes nothing), so this poll is the only
            # way state resyncs after one.
            "hid": self.client.get_hid(),
            "msd": self.client.get_msd(),
            "streamer": self.client.get_streamer(),
        }
        optional: dict[str, Any] = {}

        def add_optional(key: str, factory: Callable[[], Any]) -> None:
            # `factory` is only called when the key isn't already known-unsupported,
            # so a skipped read never creates (and leaks) an unawaited coroutine.
            if key not in unsupported:
                optional[key] = factory()

        add_optional("hw", lambda: self.client.get_info("hw"))
        add_optional("hostname", lambda: self.client.get_hostname())
        add_optional("network", lambda: self.client.get_network_config())
        add_optional("upgrade_version", lambda: self.client.get_upgrade_version())

        now = monotonic()
        due_compare = (
            self._last_upgrade_compare is None
            or now - self._last_upgrade_compare >= SLOW_READ_INTERVAL
        )
        if due_compare:
            add_optional("upgrade_compare", lambda: self.client.get_upgrade_compare())

        if first_run:
            add_optional("gpio", lambda: self.client.get_gpio())

        keys = [*core.keys(), *optional.keys()]
        results = await asyncio.gather(
            *core.values(), *optional.values(), return_exceptions=True
        )
        by_key = dict(zip(keys, results))

        # Systemic failures (auth/rate-limit/connection) take priority over
        # any single optional-field failure, regardless of which read hit them.
        for result in by_key.values():
            if isinstance(result, CometAuthError):
                raise ConfigEntryAuthFailed(str(result)) from result
        for result in by_key.values():
            if isinstance(result, CometRateLimitError):
                raise UpdateFailed(
                    f"Comet login rate-limited; retry in {result.remaining_time}s"
                ) from result
        for result in by_key.values():
            if isinstance(result, CometConnectionError):
                raise UpdateFailed(str(result)) from result
        for key in core:
            result = by_key[key]
            if isinstance(result, CometApiError):
                raise UpdateFailed(f"{key}: {result}") from result
        for key in optional:
            result = by_key[key]
            if isinstance(result, CometApiError):
                if result.status in (400, 404):
                    _LOGGER.debug(
                        "Comet: %s not supported by this firmware, skipping: %s",
                        key,
                        result,
                    )
                    unsupported.append(key)
                else:
                    # A transient failure (5xx, ok:false, unparseable body):
                    # don't give up on this field forever, just retry it
                    # next cycle.
                    _LOGGER.debug(
                        "%s read failed (HTTP %s); will retry next cycle",
                        key,
                        result.status,
                    )

        for key, result in by_key.items():
            if isinstance(result, BaseException):
                continue
            self._apply_read(key, result)

        if (
            due_compare
            and "upgrade_compare" in by_key
            and not isinstance(by_key["upgrade_compare"], BaseException)
        ):
            self._last_upgrade_compare = now

        self._first_run = False
        return dict(self._state)

    def _apply_read(self, key: str, result: dict[str, Any]) -> None:
        """Route one successful slow-tier read into coordinator state."""
        if key in ("info_system", "hw"):
            self._process_info_event(result)
        elif key == "atx":
            self._process_atx_event(result)
        elif key == "hid":
            self._process_hid_event(result)
        elif key == "msd":
            self._process_msd_event(result)
        elif key == "streamer":
            self._process_streamer_event(result)
        elif key == "gpio":
            self._process_gpio_full(result)
        elif key in ("hostname", "network", "upgrade_version", "upgrade_compare"):
            self._state["glinet"][key] = result

    # --- WebSocket push tier --------------------------------------------------

    async def async_start(self) -> None:
        """Start the background WebSocket loop, if not already running."""
        if self._ws_task is not None:
            return
        self._ws_task = self.hass.async_create_background_task(
            self._ws_loop(), f"{DOMAIN}_ws_{self.entry.entry_id}"
        )

    async def async_stop(self) -> None:
        """Stop the WebSocket loop. Idempotent; cancels+awaits the task before closing the socket."""
        task, self._ws_task = self._ws_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._ws is not None:
            ws, self._ws = self._ws, None
            if not ws.closed:
                await ws.close()

    async def _ws_loop(self) -> None:
        """Connect to the WebSocket event stream and reconnect on any drop."""
        while True:
            try:
                await self._ws_connect_and_listen()
            except asyncio.CancelledError:
                raise
            except CometAuthError as err:
                _LOGGER.error("Comet WebSocket authentication failed: %s", err)
                self._set_ws_connected(False)
                self.entry.async_start_reauth(self.hass)
                return
            except CometRateLimitError as err:
                self._set_ws_connected(False)
                _LOGGER.warning(
                    "Comet WebSocket login rate-limited; retrying in %ds",
                    err.remaining_time,
                )
                await asyncio.sleep(err.remaining_time)
                continue
            except Exception as err:  # noqa: BLE001 - any other drop triggers reconnect
                self._set_ws_connected(False)
                delay = self.entry.options.get(
                    CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
                )
                _LOGGER.warning(
                    "Comet WebSocket disconnected (%s); reconnecting in %ds",
                    type(err).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            else:
                self._set_ws_connected(False)
                delay = self.entry.options.get(
                    CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
                )
                await asyncio.sleep(delay)

    async def _ws_connect_and_listen(self) -> None:
        """Connect once and process events until the socket drops.

        ``async_request_refresh`` is triggered on every connect after the
        first one ever made by this coordinator instance -- tracked via
        ``_ws_has_connected_once``, flipped right after a successful
        ``connect_ws()`` (not after the message loop exits), so a connection
        that dies mid-listen from a raised exception still counts as "had a
        previous connection" for the next reconnect attempt.
        """
        self._ws = await self.client.connect_ws(stream=self._keep_video_active)
        reconnect = self._ws_has_connected_once
        self._ws_has_connected_once = True
        self._set_ws_connected(True)
        if reconnect:
            await self.async_request_refresh()
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._process_ws_message(msg.json())
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
        finally:
            ws, self._ws = self._ws, None
            if ws is not None and not ws.closed:
                await ws.close()

    def _set_ws_connected(self, connected: bool) -> None:
        """Update the ``ws_connected`` flag and push it to listeners."""
        self._state["ws_connected"] = connected
        self._push_state()

    def _process_ws_message(self, data: dict[str, Any]) -> None:
        """Dispatch one WebSocket frame to its merge handler.

        Gotcha: never call ``async_set_updated_data`` here -- it reschedules
        ``update_interval`` and would starve the slow HTTP tier. Push state
        via ``self.data``/``async_update_listeners`` instead (``_push_state``).
        """
        event_type = data.get("event_type", "")
        event = data.get("event")
        if event_type == "loop":
            _LOGGER.debug("Comet WebSocket: initial state burst complete")
            return
        handler = self._ws_handlers.get(event_type)
        if handler is None or not isinstance(event, dict):
            # Includes hid_keymaps/ocr/recorder/fingerbot/serial/rndis/switch/
            # turn/ap/repeater/modem and any other frame we don't model yet.
            return
        handler(event)
        self._push_state()

    def _push_state(self) -> None:
        """Publish the current state to listeners without touching update_interval."""
        self.data = dict(self._state)
        self.async_update_listeners()

    # --- Merge handlers (all merge; never wholesale-replace a subsystem) -----

    def _process_atx_event(self, event: dict[str, Any]) -> None:
        """Merge an ATX GET result or WS ``atx`` event."""
        _deep_merge(self._state["atx"], event)

    def _process_hid_event(self, event: dict[str, Any]) -> None:
        """Merge an HID GET result or WS ``hid`` event."""
        _deep_merge(self._state["hid"], event)

    def _process_streamer_event(self, event: dict[str, Any]) -> None:
        """Merge a streamer GET result or WS ``streamer`` event."""
        _deep_merge(self._state["streamer"], event)

    def _process_msd_event(self, event: dict[str, Any]) -> None:
        """Merge an MSD GET result or WS ``msd`` event.

        ``storage`` is replaced wholesale (not merged) when present, since a
        recursive merge could never notice a removed image.
        """
        msd = self._state["msd"]
        storage = event.get("storage")
        rest = {k: v for k, v in event.items() if k != "storage"}
        _deep_merge(msd, rest)
        if storage is not None:
            msd["storage"] = storage

    def _process_gpio_event(self, event: dict[str, Any]) -> None:
        """Merge a WS ``gpio`` event's channel states, per-channel.

        Handles both the full ``{"state": {"inputs", "outputs"}}`` shape
        (identical to the GET result, as observed live) and a bare
        ``{"inputs", "outputs"}`` delta, without ever dropping an
        unmentioned channel.
        """
        state = event.get("state", event)
        if not isinstance(state, dict):
            return
        gpio = self._state["gpio"]
        if isinstance(state.get("inputs"), dict):
            _deep_merge(gpio.setdefault("inputs", {}), state["inputs"])
        if isinstance(state.get("outputs"), dict):
            _deep_merge(gpio.setdefault("outputs", {}), state["outputs"])

    def _process_gpio_full(self, event: dict[str, Any]) -> None:
        """Replace GPIO model/state/labels from a full ``GET /api/gpio`` result."""
        model = event.get("model") or {}
        scheme = model.get("scheme") or {}
        state = event.get("state") or {}

        self._state["gpio_model"] = {
            "inputs": scheme.get("inputs") or {},
            "outputs": scheme.get("outputs") or {},
        }
        self._state["gpio"] = {
            "inputs": state.get("inputs") or {},
            "outputs": state.get("outputs") or {},
        }

        # Parse view.table for human-readable channel labels: each row can
        # carry a label cell plus one or more input/output channel cells, all
        # of which share that row's label.
        labels: dict[str, str] = {}
        table = (model.get("view") or {}).get("table") or []
        for row in table:
            if not isinstance(row, list):
                continue
            label_text = None
            channel_names: list[str] = []
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cell.get("type") == "label" and "text" in cell:
                    label_text = cell["text"]
                elif cell.get("type") in ("input", "output") and "channel" in cell:
                    channel_names.append(cell["channel"])
            if label_text:
                for ch in channel_names:
                    labels[ch] = label_text
        self._state["gpio_labels"] = labels

    def _process_info_event(self, event: dict[str, Any]) -> None:
        """Merge one or more ``info`` sections (``{"system": {...}}``, ...)."""
        _deep_merge(self._state["info"], event)
