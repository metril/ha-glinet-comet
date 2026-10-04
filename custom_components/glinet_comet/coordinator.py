"""WebSocket-push coordinator for the GL.iNet Comet integration.

Fast state (``atx``/``hid``/``msd``/``streamer``/``gpio``/``info``) is pushed
over the WebSocket event stream (``/api/ws``); a slow HTTP-poll tier
(``update_interval``) covers GL.iNet-specific reads the socket doesn't carry
(hostname/network/upgrade info) and re-polls ``atx``, ``hid``, ``msd``, and
``streamer`` every cycle as a fallback source of truth -- MSD writes in
particular push nothing back over the WebSocket, so those subsystems can't
rely on WS push alone. ``gpio`` is polled every cycle too, just like
``atx``: ``_process_gpio_event`` deliberately merges only channel *state*
from a WS ``gpio`` frame and ignores any ``model`` it carries, so this
slow-tier poll is the only path by which ``gpio_model`` is ever
reassigned; ``_process_gpio_full`` re-derives
``gpio``/``gpio_model``/``gpio_labels`` from scratch on every successful
read.

Failure tiers: systemic failures (auth/rate-limit/connection) always fail
the whole cycle, regardless of which read hit them. A per-subsystem
``CometApiError`` on a *core* read (``atx``, ``info_system``, ``hid``,
``msd``, ``streamer``) is soft -- that subsystem just keeps its last known
state, a warn-once/then-debug message is logged, and the cycle still
succeeds -- unless every core read failed in the same cycle (nothing left
to report) or this is the first-ever cycle, which stays strict because
``CometEntity.__init__`` snapshots device serial/model/sw_version from it.
Core reads are never added to ``unsupported``: that list means "skip
forever", which a core read must not do. Optional reads keep the older,
simpler rule: a 400/404 marks the field ``unsupported`` (skipped on later
cycles), any other status is retried next cycle. ``_read_warned`` tracks
warn-once state across both tiers, keyed by read name.
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
    CometError,
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

# Rarely-changing reads, re-read at most once per SLOW_READ_INTERVAL.
# hostname/network are deliberately NOT gated: they are the GL.iNet-specific
# reads the slow tier exists for, and an IP/DHCP change must show within
# one update_interval.
_SLOW_GATED = (
    "info_system",
    "upgrade_version",
    "upgrade_compare",
)
_WS_BACKOFF_CAP = 300
# A WS connection must survive this long before the reconnect backoff resets,
# so a socket that accepts the handshake and drops at once still backs off.
_WS_STABLE_SECONDS = 60


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
            config_entry=entry,
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
        self._read_warned: set[str] = set()
        self._had_successful_cycle = False
        self._ws_has_connected_once = False
        # Last successful read time per slow-gated key (see _SLOW_GATED).
        self._slow_last: dict[str, float] = {}
        self._ws_failures = 0
        self._ws_connected_at: float | None = None
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

    def _log_read_failure(self, key: str, err: CometApiError) -> None:
        """Log one soft-failed read: WARNING the first time, DEBUG on repeats.

        Repeats are downgraded to debug so a flapping subsystem doesn't spam
        the log every cycle. ``_read_warned`` is cleared for ``key`` as soon
        as a read for it succeeds again (see the reset in
        ``_async_update_data``), so a *new* outage always warns once more.
        """
        first_time = key not in self._read_warned
        self._read_warned.add(key)
        log = _LOGGER.warning if first_time else _LOGGER.debug
        log("%s read failed (HTTP %s); keeping last known state", key, err.status)

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll the slow HTTP tier for every WS-unreliable/WS-only subsystem."""
        unsupported: list[str] = self._state["unsupported"]

        now = monotonic()

        def due(key: str) -> bool:
            last = self._slow_last.get(key)
            return last is None or now - last >= SLOW_READ_INTERVAL

        core: dict[str, Any] = {
            "atx": self.client.get_atx(),
            # hid/msd/streamer are read every cycle, not just bootstrapped
            # once: no WS event carries hid/msd/streamer writes back (MSD in
            # particular pushes nothing), so this poll is the only way state
            # resyncs after one.
            "hid": self.client.get_hid(),
            "msd": self.client.get_msd(),
            "streamer": self.client.get_streamer(),
        }
        if due("info_system"):
            core["info_system"] = self.client.get_info("system")
        optional: dict[str, Any] = {}

        def add_optional(key: str, factory: Callable[[], Any]) -> None:
            # `factory` is only called when the key isn't already known-unsupported,
            # so a skipped read never creates (and leaks) an unawaited coroutine.
            if key not in unsupported and (key not in _SLOW_GATED or due(key)):
                optional[key] = factory()

        add_optional("hw", lambda: self.client.get_info("hw"))
        add_optional("hostname", lambda: self.client.get_hostname())
        add_optional("network", lambda: self.client.get_network_config())
        add_optional("upgrade_version", lambda: self.client.get_upgrade_version())
        add_optional("upgrade_compare", lambda: self.client.get_upgrade_compare())
        # gpio is polled every cycle, same as atx/hid/msd/streamer: it's the
        # only source of new or changed channels (see the module docstring),
        # so a one-shot bootstrap would never notice a channel added later.
        add_optional("gpio", lambda: self.client.get_gpio())

        keys = [*core.keys(), *optional.keys()]
        results = await asyncio.gather(
            *core.values(), *optional.values(), return_exceptions=True
        )
        by_key = dict(zip(keys, results))

        # Reset before any raise, so a recovery isn't lost to an unrelated
        # failure in the same cycle.
        self._read_warned -= {
            k for k, v in by_key.items() if not isinstance(v, BaseException)
        }

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

        # A per-subsystem CometApiError on a core read is soft: keep that
        # subsystem's last state and let the cycle succeed, UNLESS every
        # core read failed, or this is the first-ever cycle (CometEntity
        # snapshots device serial/model/sw_version from it, so it must stay
        # strict). Core reads are never marked `unsupported` -- that means
        # "skip forever", which a core read must not do.
        core_failures = {
            key: by_key[key] for key in core if isinstance(by_key[key], CometApiError)
        }
        if core_failures:
            first_key = next(iter(core_failures))
            first_err = core_failures[first_key]
            if not self._had_successful_cycle:
                raise UpdateFailed(f"{first_key}: {first_err}") from first_err
            if len(core_failures) == len(core):
                raise UpdateFailed(
                    f"all core reads failed; {first_key}: {first_err}"
                ) from first_err
            for key, err in core_failures.items():
                self._log_read_failure(key, err)

        for key, result in by_key.items():
            if isinstance(result, BaseException) and not isinstance(
                result, CometError
            ):
                # Unexpected (non-Comet) failure: never drop it silently.
                first = key not in self._read_warned
                self._read_warned.add(key)
                (_LOGGER.warning if first else _LOGGER.debug)(
                    "%s read raised unexpectedly; keeping last known state: %r",
                    key,
                    result,
                )

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
                    self._log_read_failure(key, result)

        for key, result in by_key.items():
            if isinstance(result, BaseException):
                continue
            self._apply_read(key, result)

        for key in _SLOW_GATED:
            if key in by_key and not isinstance(by_key[key], BaseException):
                self._slow_last[key] = now

        # Only relax the first-cycle gate if a core read actually landed in
        # state -- a cycle where every core read raised a non-CometError
        # exception (silently skipped by the apply loop above, since it's
        # neither a systemic failure nor a CometApiError core_failure) must
        # not count as "the first successful cycle".
        if any(not isinstance(by_key[key], BaseException) for key in core):
            self._had_successful_cycle = True
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
                await asyncio.sleep(max(1, err.remaining_time))
                continue
            except Exception as err:  # noqa: BLE001 - any other drop triggers reconnect
                self._set_ws_connected(False)
                first = self._ws_failures == 0
                delay = self._next_ws_delay()
                (_LOGGER.warning if first else _LOGGER.debug)(
                    "Comet WebSocket disconnected (%s); reconnecting in %ds",
                    type(err).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            else:
                self._set_ws_connected(False)
                first = self._ws_failures == 0
                delay = self._next_ws_delay()
                (_LOGGER.warning if first else _LOGGER.debug)(
                    "Comet WebSocket closed; reconnecting in %ds", delay
                )
                await asyncio.sleep(delay)

    def _next_ws_delay(self) -> float:
        """Return the next reconnect delay: base * 2**failures, capped at 300s.

        The failure count only resets once a connection has stayed up for
        ``_WS_STABLE_SECONDS``, never on the handshake alone.
        """
        base = self.entry.options.get(
            CONF_WS_RECONNECT_DELAY, DEFAULT_WS_RECONNECT_DELAY
        )
        if (
            self._ws_connected_at is not None
            and monotonic() - self._ws_connected_at >= _WS_STABLE_SECONDS
        ):
            self._ws_failures = 0  # the last connection was stable
        self._ws_connected_at = None
        delay = min(base * 2**self._ws_failures, max(_WS_BACKOFF_CAP, base))
        self._ws_failures += 1
        return delay

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
        self._ws_connected_at = monotonic()
        self._set_ws_connected(True)
        if reconnect:
            # Static reads may have changed while we were away (reboot/upgrade).
            for key in ("info_system", "upgrade_version"):
                self._slow_last.pop(key, None)
            coro = self.async_request_refresh()
            name = f"{DOMAIN}_refresh_{self.entry.entry_id}"
            create = getattr(
                getattr(self, "config_entry", None) or self.entry,
                "async_create_background_task",
                None,
            )
            if callable(create):
                create(self.hass, coro, name)
            else:
                self.hass.async_create_background_task(coro, name)
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = msg.json()
                        if not isinstance(payload, dict):
                            continue
                        self._process_ws_message(payload)
                    except (ValueError, TypeError, KeyError, AttributeError) as err:
                        _LOGGER.debug("Comet WebSocket: skipping bad frame: %r", err)
                        continue
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
        if self._state.get("ws_connected") == connected:
            return
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

    def push_partial(self, subsystem: str, patch: dict[str, Any]) -> None:
        """Deep-merge ``patch`` into ``_state[subsystem]`` and publish (optimistic updates)."""
        current = self._state.get(subsystem)
        if not isinstance(current, dict):
            current = self._state[subsystem] = {}
        _deep_merge(current, patch)
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
        """Replace GPIO model/state/labels from a full ``GET /api/gpio`` result.

        Every level is type-guarded: firmware sending the wrong type anywhere
        in this shape (a list where a dict is expected, etc.) must not raise
        -- that piece is just treated as empty, and the well-formed pieces
        still land.

        A WS ``gpio`` frame (``_process_gpio_event``) landing between this
        read and this method's apply is momentarily overwritten by the
        wholesale replacement below -- harmless, since it self-corrects on
        the next WS frame or the next slow-tier gpio poll.
        """
        if not isinstance(event, dict):
            # The HTTP path (_apply_read) can hand this a non-dict `result`
            # on odd firmware -- treat it as "nothing to apply" rather than
            # raising out of the update cycle.
            return
        model = event.get("model")
        if not isinstance(model, dict):
            model = {}
        scheme = model.get("scheme")
        if not isinstance(scheme, dict):
            scheme = {}
        state = event.get("state")
        if not isinstance(state, dict):
            state = {}

        scheme_inputs = scheme.get("inputs")
        scheme_outputs = scheme.get("outputs")
        self._state["gpio_model"] = {
            "inputs": scheme_inputs if isinstance(scheme_inputs, dict) else {},
            "outputs": scheme_outputs if isinstance(scheme_outputs, dict) else {},
        }
        state_inputs = state.get("inputs")
        state_outputs = state.get("outputs")
        self._state["gpio"] = {
            "inputs": state_inputs if isinstance(state_inputs, dict) else {},
            "outputs": state_outputs if isinstance(state_outputs, dict) else {},
        }

        # Parse view.table for human-readable channel labels: each row can
        # carry a label cell plus one or more input/output channel cells, all
        # of which share that row's label.
        labels: dict[str, str] = {}
        view = model.get("view")
        if not isinstance(view, dict):
            view = {}
        table = view.get("table")
        if not isinstance(table, list):
            table = []
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
