"""Decode-free WebRTC viewer session against the Comet's ``/lws/ws`` signalling.

On the Comet PoE a ``stream=1`` event socket alone never locks the HDMI
capture; a real WebRTC viewer does. This module connects a receive-only
peer (ICE + DTLS handshake via aioice/pyOpenSSL; SRTP is dropped unread, so
nothing is decoded) purely to keep the capture up. Dependencies are imported
lazily in an executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import re
from time import monotonic
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import aiohttp

from . import parsers
from .api import CometUnsupportedError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import CometDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PING_INTERVAL = 2.0
CONT_DELAY = 10.0
BACKOFF_MIN = 10.0
BACKOFF_MAX = 300.0
STABLE_SECONDS = 60.0
ONLINE_BUDGET = 7.0
ANSWER_TIMEOUT = 10.0
POST_ANSWER_DELAY = 0.5
ICE_TIMEOUT = 15.0
START_TIMEOUT = 15.0

_CIPHERS = (
    b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-CHACHA20-POLY1305:"
    b"ECDHE-ECDSA-AES128-SHA:ECDHE-ECDSA-AES256-SHA"
)

_DEPS: SimpleNamespace | None = None


def _import_deps() -> SimpleNamespace:
    from aioice import Candidate, Connection
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from OpenSSL import SSL

    return SimpleNamespace(
        Connection=Connection,
        Candidate=Candidate,
        SSL=SSL,
        x509=x509,
        hashes=hashes,
        ec=ec,
    )


async def _load_deps(hass: HomeAssistant) -> SimpleNamespace:
    """Import aioice/pyOpenSSL/cryptography once, off the event loop."""
    global _DEPS  # noqa: PLW0603
    if _DEPS is None:
        _DEPS = await hass.async_add_executor_job(_import_deps)
    return _DEPS


def _colon_hex(raw: bytes) -> str:
    h = raw.hex().upper()
    return ":".join(h[i : i + 2] for i in range(0, len(h), 2))


class CometVideoSession:
    """Decode-free WebRTC viewer: ICE + DTLS handshake, SRTP dropped unread."""

    def __init__(self, client: Any, deps: SimpleNamespace) -> None:
        """Initialize (no I/O)."""
        self._client = client
        self._d = deps
        self._ws: Any = None
        self._ice: Any = None
        self._ssl: Any = None
        self._sid: str | None = None
        self._offer = ""
        self._cands: list[str] = []
        self._cert: Any = None
        self._key: Any = None
        self._fp = ""
        self._remote_setup: str | None = None
        self._remote_fp: str | None = None
        self._answered = asyncio.Event()
        self._ready = False
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Return whether ICE is connected and DTLS is done."""
        return self._ready and not self._closed.is_set()

    @property
    def alive(self) -> bool:
        """Return whether the session is started and not closed (maybe handshaking)."""
        return self._ws is not None and not self._closed.is_set()

    async def wait_closed(self) -> None:
        """Block until the session dies or is stopped."""
        await self._closed.wait()

    def _make_cert(self) -> None:
        d = self._d
        key = d.ec.generate_private_key(d.ec.SECP256R1())
        name = d.x509.Name(
            [d.x509.NameAttribute(d.x509.NameOID.COMMON_NAME, os.urandom(8).hex())]
        )
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        cert = (
            d.x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(d.x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, d.hashes.SHA256())
        )
        self._key, self._cert = key, cert
        self._fp = _colon_hex(cert.fingerprint(d.hashes.SHA256()))

    def _build_offer(self) -> None:
        ice = self._ice
        cands = [c.to_sdp() for c in ice.local_candidates]
        cands = [c if c.startswith("candidate:") else "candidate:" + c for c in cands]
        lines = [
            "v=0", "o=- 1 2 IN IP4 127.0.0.1", "s=-", "t=0 0", "a=group:BUNDLE 0",
            "a=msid-semantic: WMS", "m=video 9 UDP/TLS/RTP/SAVPF 102",
            "c=IN IP4 0.0.0.0", "a=rtcp:9 IN IP4 0.0.0.0",
            f"a=ice-ufrag:{ice.local_username}", f"a=ice-pwd:{ice.local_password}",
            "a=ice-options:trickle", f"a=fingerprint:sha-256 {self._fp}",
            "a=setup:actpass", "a=mid:0", "a=recvonly", "a=rtcp-mux", "a=rtcp-rsize",
            "a=rtpmap:102 H264/90000", "a=rtcp-fb:102 nack", "a=rtcp-fb:102 nack pli",
            "a=rtcp-fb:102 ccm fir",
            "a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1;"
            "profile-level-id=42e01f",
        ]  # fmt: skip
        lines += ["a=" + c for c in cands] + ["a=end-of-candidates", ""]
        self._offer = "\r\n".join(lines)
        self._cands = cands

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._sid is not None:
            msg["id"] = self._sid
        await self._ws.send_str(json.dumps(msg))

    async def start(self) -> None:
        """Gather candidates, connect the signalling socket, begin the handshake."""
        try:
            self._make_cert()
            self._ice = self._d.Connection(ice_controlling=True)
            await self._ice.gather_candidates()
            self._build_offer()
            self._ws = await self._client.connect_lws()
            await self._ws.send_str(json.dumps({"type": "create", "orientation": 0}))
        except BaseException:
            await self.stop()
            raise
        for coro in (self._read_loop(), self._ping_loop(), self._run()):
            self._tasks.append(asyncio.create_task(coro))

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL)
                await self._ws.send_str(json.dumps({"event_type": "ping", "event": {}}))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._closed.set()

    async def _read_loop(self) -> None:
        try:
            async for m in self._ws:
                if m.type != aiohttp.WSMsgType.TEXT:
                    break
                try:
                    await self._handle(json.loads(m.data))
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("lws message error", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug("lws reader error", exc_info=True)
        self._closed.set()

    async def _handle(self, e: dict[str, Any]) -> None:
        ice = self._ice
        ty = e.get("type") or e.get("event_type")
        if ty == "id":
            self._sid = e["id"]
            await self._send({"type": "offer", "sdp": self._offer})
            for c in self._cands:
                await self._send(
                    {"type": "ice", "candidate": c, "sdpMid": "0", "sdpMLineIndex": 0}
                )
        elif ty == "answer" or (e.get("sdp") and ty != "offer"):
            sdp = e["sdp"]
            ice.remote_username = re.search(r"a=ice-ufrag:(\S+)", sdp).group(1)
            ice.remote_password = re.search(r"a=ice-pwd:(\S+)", sdp).group(1)
            self._remote_setup = re.search(r"a=setup:(\S+)", sdp).group(1)
            fp = re.search(r"a=fingerprint:sha-256 (\S+)", sdp)
            self._remote_fp = fp.group(1) if fp else None
            for line in sdp.splitlines():
                if line.startswith("a=candidate:"):
                    await ice.add_remote_candidate(self._d.Candidate.from_sdp(line[2:]))
            self._answered.set()
        elif ty == "ice" and e.get("candidate"):
            await ice.add_remote_candidate(self._d.Candidate.from_sdp(e["candidate"]))

    async def _flush(self) -> None:
        try:
            data = self._ssl.bio_read(1500)
        except self._d.SSL.Error:
            data = b""
        if data:
            await self._ice.send(data)

    async def _recv_next(self, handshaking: bool) -> None:
        ssl, SSL = self._ssl, self._d.SSL  # noqa: N806
        timeout = ssl.DTLSv1_get_timeout() if handshaking else None
        try:
            if timeout:
                data = await asyncio.wait_for(self._ice.recv(), timeout=timeout)
            else:
                data = await self._ice.recv()
        except asyncio.TimeoutError:
            ssl.DTLSv1_handle_timeout()
            await self._flush()
            return
        if data and 19 < data[0] < 64:  # DTLS record; everything else is SRTP/SRTCP
            ssl.bio_write(data)
            try:
                ssl.recv(1500)
            except (SSL.WantReadError, SSL.ZeroReturnError, SSL.Error):
                pass
            await self._flush()

    async def _run(self) -> None:
        d = self._d
        try:
            await asyncio.wait_for(self._answered.wait(), ANSWER_TIMEOUT)
            await asyncio.sleep(POST_ANSWER_DELAY)
            await self._ice.add_remote_candidate(None)
            await asyncio.wait_for(self._ice.connect(), ICE_TIMEOUT)
            SSL = d.SSL  # noqa: N806
            ctx = SSL.Context(SSL.DTLS_METHOD)
            ctx.set_verify(
                SSL.VERIFY_PEER | SSL.VERIFY_FAIL_IF_NO_PEER_CERT, lambda *a: True
            )
            ctx.use_certificate(self._cert)
            ctx.use_privatekey(self._key)
            ctx.set_cipher_list(_CIPHERS)
            ctx.set_tlsext_use_srtp(b"SRTP_AES128_CM_SHA1_80")
            self._ssl = SSL.Connection(ctx)
            if self._remote_setup == "active":
                self._ssl.set_accept_state()
            else:
                self._ssl.set_connect_state()
            while True:
                try:
                    self._ssl.do_handshake()
                    break
                except SSL.WantReadError:
                    await self._flush()
                    await self._recv_next(True)
            self._check_fingerprint()
            self._ready = True
            while True:
                await self._recv_next(False)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("video session ended: %r", err)
        self._closed.set()

    def _check_fingerprint(self) -> None:
        try:
            peer = self._ssl.get_peer_certificate()
            if peer is None or not self._remote_fp:
                return
            got = _colon_hex(peer.to_cryptography().fingerprint(self._d.hashes.SHA256()))
            if got != self._remote_fp.upper():
                _LOGGER.warning("Comet DTLS certificate fingerprint mismatch")
        except Exception:  # noqa: BLE001
            _LOGGER.debug("fingerprint check failed", exc_info=True)

    async def stop(self) -> None:
        """Tear the session down. Idempotent."""
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            if task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        ice, self._ice = self._ice, None
        if ice is not None:
            with contextlib.suppress(Exception):
                await ice.close()
        self._ready = False
        self._closed.set()


class CometVideoManager:
    """Owns the (single) viewer session: on-demand wake and continuous hold."""

    def __init__(self, hass: HomeAssistant, coordinator: CometDataUpdateCoordinator) -> None:
        """Initialize (cheap; deps are not imported here)."""
        self._hass = hass
        self._coord = coordinator
        self._session: CometVideoSession | None = None
        self._lock = asyncio.Lock()
        self._deadline = 0.0
        self._hold_task: asyncio.Task[None] | None = None
        self._continuous = False
        self._cont_task: asyncio.Task[None] | None = None
        self._unsupported = False
        self._stopped = False
        self._warned: set[str] = set()
        self._online_event = asyncio.Event()

    def notify_streamer(self, state: dict[str, Any]) -> None:
        """Track capture-source online state from coordinator state."""
        if parsers.source_online(state):
            self._online_event.set()
        else:
            self._online_event.clear()

    async def _ensure_session(self) -> bool:
        async with self._lock:
            if self._unsupported or self._stopped:
                return False
            if self._session is not None and self._session.alive:
                return True
            if self._session is not None:
                await self._session.stop()
                self._session = None
            session = None
            try:
                deps = await _load_deps(self._hass)
                session = CometVideoSession(self._coord.client, deps)
                await asyncio.wait_for(session.start(), START_TIMEOUT)
            except (CometUnsupportedError, ImportError) as err:
                self._unsupported = True
                _LOGGER.warning(
                    "WebRTC video wake unsupported on this device (%s)", err
                )
                return False
            except Exception as err:  # noqa: BLE001
                if session is not None:
                    await session.stop()
                key = type(err).__name__
                if key not in self._warned:
                    self._warned.add(key)
                    _LOGGER.warning("WebRTC video session failed to start: %r", err)
                else:
                    _LOGGER.debug("WebRTC video session failed: %r", err)
                return False
            if self._stopped:
                await session.stop()
                return False
            self._session = session
            return True

    async def _stop_session(self) -> None:
        async with self._lock:
            session, self._session = self._session, None
        if session is not None:
            await session.stop()

    async def wake(self, hold: float = 15.0) -> bool:
        """Start a viewer session for ``hold`` seconds; return whether source is online."""
        if self._unsupported or self._stopped:
            return False
        self._deadline = max(self._deadline, monotonic() + hold)
        if not await self._ensure_session():
            return False
        if self._stopped:
            await self._stop_session()
            return False
        if self._hold_task is None:
            self._hold_task = asyncio.create_task(self._hold_loop())
        end = monotonic() + ONLINE_BUDGET
        while monotonic() < end:
            try:
                await asyncio.wait_for(self._online_event.wait(), min(1.0, max(end - monotonic(), 0.001))
                )
                return True
            except asyncio.TimeoutError:
                if not self._coord.ws_connected:
                    try:
                        result = await self._coord.client.get_streamer()
                        self._coord._apply_read("streamer", result)  # noqa: SLF001
                        self._coord._push_state()  # noqa: SLF001
                    except Exception:  # noqa: BLE001
                        _LOGGER.debug("streamer poll failed", exc_info=True)
        return self._online_event.is_set()

    async def _hold_loop(self) -> None:
        try:
            while (rem := self._deadline - monotonic()) > 0:
                await asyncio.sleep(rem)
            if not self._continuous:
                await self._stop_session()
        finally:
            self._hold_task = None

    def async_on_ws_connected(self) -> None:
        """Start the continuous hold if the PoE needs it."""
        if self._unsupported or self._stopped or self._cont_task is not None:
            return
        self._cont_task = asyncio.create_task(self._continuous_loop())

    async def _continuous_loop(self) -> None:
        try:
            await asyncio.sleep(CONT_DELAY)
            if parsers.source_online(self._coord.data or {}) is True:
                return  # Comet X: stream=1 alone suffices
            self._continuous = True
            backoff = BACKOFF_MIN
            while True:
                if not await self._ensure_session():
                    if self._unsupported or self._stopped:
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    continue
                session = self._session
                if session is None:
                    return
                t0 = monotonic()
                await session.wait_closed()
                await self._stop_session()
                if monotonic() - t0 >= STABLE_SECONDS:
                    backoff = BACKOFF_MIN
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
        finally:
            self._continuous = False
            self._cont_task = None

    async def stop(self) -> None:
        """Cancel background tasks and close the session."""
        self._stopped = True
        for name in ("_cont_task", "_hold_task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._stop_session()
