"""Tests for the WebRTC viewer session and its manager."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiohttp
import pytest

from custom_components.glinet_comet import video_session as vs
from custom_components.glinet_comet.api import CometUnsupportedError


class Msg:
    def __init__(self, type, data=None):
        self.type = type
        self.data = data


class FakeWS:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def send_str(self, s):
        self.sent.append(json.loads(s))

    def push(self, obj):
        self.q.put_nowait(Msg(aiohttp.WSMsgType.TEXT, json.dumps(obj)))

    def drop(self):
        self.q.put_nowait(Msg(aiohttp.WSMsgType.CLOSED))

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m.type == aiohttp.WSMsgType.CLOSED:
            raise StopAsyncIteration
        return m

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, exc=None):
        self.exc = exc
        self.connects = 0
        self.wss: list[FakeWS] = []

    async def connect_lws(self):
        self.connects += 1
        await asyncio.sleep(0)
        if self.exc:
            raise self.exc
        ws = FakeWS()
        self.wss.append(ws)
        return ws


def deps():
    return vs._import_deps()


async def spin(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


async def started():
    client = FakeClient()
    s = vs.CometVideoSession(client, deps())
    await s.start()
    return client, client.wss[0], s


ANSWER = (
    "v=0\r\na=ice-ufrag:RUF\r\na=ice-pwd:RPW\r\na=setup:active\r\n"
    "a=fingerprint:sha-256 AA:BB\r\na=candidate:7 1 udp 1 9.9.9.9 9 typ host\r\n"
)


async def test_signalling_order():
    _, ws, s = await started()
    assert ws.sent == [{"type": "create", "orientation": 0}]
    ws.push({"type": "id", "id": "S1"})
    await spin()
    assert [m["type"] for m in ws.sent[1:]] == ["offer", "ice", "ice"]
    assert all(m["id"] == "S1" for m in ws.sent[1:])
    assert ws.sent[2]["candidate"].startswith("candidate:1 ")
    assert ws.sent[2]["sdpMid"] == "0" and ws.sent[2]["sdpMLineIndex"] == 0
    sdp = ws.sent[1]["sdp"]
    assert "a=ice-ufrag:LUF" in sdp and "a=candidate:1 " in sdp and "H264/90000" in sdp
    assert "a=setup:actpass" in sdp and "a=end-of-candidates" in sdp
    await s.stop()


async def test_answer_connects_and_dtls_handshake(monkeypatch):
    monkeypatch.setattr(vs, "POST_ANSWER_DELAY", 0)
    _, ws, s = await started()
    ice = s._ice
    ws.push({"type": "id", "id": "S1"})
    ws.push({"type": "answer", "sdp": ANSWER})
    ws.push({"type": "ice", "candidate": "candidate:8 1 udp 1 8.8.8.8 8 typ host"})
    await spin(10)
    assert (ice.remote_username, ice.remote_password) == ("RUF", "RPW")
    assert [getattr(c, "sdp", None) for c in ice.remote][:2] == [
        "candidate:7 1 udp 1 9.9.9.9 9 typ host",
        "candidate:8 1 udp 1 8.8.8.8 8 typ host",
    ]
    assert ice.remote[-1] is None and ice.connected
    ssl = s._ssl
    assert ssl.state == "accept"
    ice.rx.put_nowait(b"\x16dtls-flight")  # drives the handshake loop
    await spin(20)
    assert ssl.written == [b"\x16dtls-flight"]
    assert ssl.handshakes == 2 and s.connected
    assert b"\x16hello" in ice.sent  # flushed handshake bytes
    await s.stop()


async def test_non_dtls_dropped_without_touching_ssl(monkeypatch):
    monkeypatch.setattr(vs, "POST_ANSWER_DELAY", 0)
    _, ws, s = await started()
    ws.push({"type": "answer", "sdp": ANSWER.replace("active", "passive")})
    await spin(10)
    ice = s._ice
    ice.rx.put_nowait(b"\x16x")
    await spin(20)
    assert s._ssl.state == "connect" and s.connected
    before = list(s._ssl.written)
    for pkt in (b"\x80rtp", b"\x81rtcp", b"\x00stun"):
        ice.rx.put_nowait(pkt)
    await spin(10)
    assert s._ssl.written == before
    await s.stop()


async def test_ice_failure_closes(monkeypatch):
    monkeypatch.setattr(vs, "POST_ANSWER_DELAY", 0)
    _, ws, s = await started()

    async def boom():
        raise ConnectionError("ice")

    s._ice.connect = boom
    ws.push({"type": "answer", "sdp": ANSWER})
    await asyncio.wait_for(s.wait_closed(), 1)
    await s.stop()


async def test_ping_interval(monkeypatch):
    monkeypatch.setattr(vs, "PING_INTERVAL", 0.01)
    _, ws, s = await started()
    await asyncio.sleep(0.05)
    assert {"event_type": "ping", "event": {}} in ws.sent
    await s.stop()


async def test_stop_idempotent_closes_ice_and_ws():
    _, ws, s = await started()
    ice = s._ice
    await s.stop()
    await s.stop()
    assert ws.closed and ice.closed and s._tasks == []
    await asyncio.wait_for(s.wait_closed(), 1)
    assert not s.connected


async def test_ws_close_sets_closed():
    _, ws, s = await started()
    ws.drop()
    await asyncio.wait_for(s.wait_closed(), 1)
    await s.stop()


# --- manager ---


class FakeCoord:
    def __init__(self, client, ws_connected=True):
        self.client = client
        self.data = {}
        self.ws_connected = ws_connected
        self.applied = []

    def _apply_read(self, key, result):
        self.applied.append((key, result))

    def _push_state(self):
        pass


class FakeHass:
    async def async_add_executor_job(self, fn, *a):
        return fn(*a)


ONLINE = {"streamer": {"streamer": {"source": {"online": True}}}}


def mgr(client=None, **kw):
    client = client or FakeClient()
    coord = FakeCoord(client, **kw)
    return vs.CometVideoManager(FakeHass(), coord), client, coord


async def test_concurrent_wake_single_connect(monkeypatch):
    monkeypatch.setattr(vs, "ONLINE_BUDGET", 0.05)
    m, client, _ = mgr()
    await asyncio.gather(m.wake(), m.wake())
    assert client.connects == 1
    await m.stop()


async def test_wake_returns_true_when_online(monkeypatch):
    m, _, _ = mgr()
    m.notify_streamer(ONLINE)
    assert await m.wake() is True
    await m.stop()


async def test_second_wake_extends_deadline(monkeypatch):
    monkeypatch.setattr(vs, "ONLINE_BUDGET", 0.05)
    m, _, _ = mgr()
    await m.wake(hold=1)
    d1 = m._deadline
    await m.wake(hold=100)
    assert m._deadline > d1
    await m.stop()


async def test_session_stops_after_hold(monkeypatch):
    monkeypatch.setattr(vs, "ONLINE_BUDGET", 0.05)
    m, client, _ = mgr()
    await m.wake(hold=0.3)
    assert m._session is not None
    await asyncio.sleep(0.5)
    assert m._session is None and m._hold_task is None
    assert client.wss[0].closed


@pytest.mark.parametrize("status", [404, 502])
async def test_unsupported_logged_once(status, caplog):
    m, client, _ = mgr(FakeClient(exc=CometUnsupportedError("x", status=status)))
    with caplog.at_level(logging.WARNING):
        assert await m.wake() is False
        assert await m.wake() is False
    assert client.connects == 1
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_continuous_noop_when_online(monkeypatch):
    monkeypatch.setattr(vs, "CONT_DELAY", 0.01)
    m, client, coord = mgr()
    coord.data = ONLINE
    m.async_on_ws_connected()
    await asyncio.sleep(0.1)
    assert client.connects == 0 and m._cont_task is None and not m._continuous


async def test_continuous_starts_and_reconnects(monkeypatch):
    monkeypatch.setattr(vs, "CONT_DELAY", 0.01)
    monkeypatch.setattr(vs, "BACKOFF_MIN", 0.01)
    m, client, _ = mgr()
    m.async_on_ws_connected()
    await asyncio.sleep(0.1)
    assert client.connects == 1 and m._continuous
    client.wss[0].drop()
    await asyncio.sleep(0.2)
    assert client.connects == 2
    await m.stop()


async def test_stop_cancels_all(monkeypatch):
    monkeypatch.setattr(vs, "CONT_DELAY", 0.01)
    monkeypatch.setattr(vs, "ONLINE_BUDGET", 0.05)
    m, client, _ = mgr()
    await m.wake()
    m.async_on_ws_connected()
    await asyncio.sleep(0.05)
    await m.stop()
    assert m._cont_task is None and m._hold_task is None and m._session is None
    assert client.wss[0].closed


from custom_components.glinet_comet.api import CometConnectionError  # noqa: E402


@pytest.mark.parametrize("exc", [CometConnectionError("down"), OSError("boom")])
async def test_wake_swallows_start_errors(exc):
    m, client, _ = mgr(FakeClient(exc=exc))
    assert await m.wake() is False
    assert m._unsupported is False


@pytest.mark.parametrize("exc", [CometConnectionError("down"), OSError("boom")])
async def test_continuous_backs_off_and_retries(monkeypatch, exc):
    monkeypatch.setattr(vs, "CONT_DELAY", 0.01)
    monkeypatch.setattr(vs, "BACKOFF_MIN", 0.01)
    m, client, _ = mgr(FakeClient(exc=exc))
    m.async_on_ws_connected()
    await asyncio.sleep(0.2)
    assert client.connects >= 2
    assert m._cont_task is not None
    client.exc = None
    await asyncio.sleep(0.3)
    assert m._session is not None
    await m.stop()


async def test_continuous_stops_session_before_backoff(monkeypatch):
    monkeypatch.setattr(vs, "CONT_DELAY", 0.01)
    monkeypatch.setattr(vs, "BACKOFF_MIN", 5)
    m, client, _ = mgr()
    m.async_on_ws_connected()
    await asyncio.sleep(0.1)
    ws = client.wss[0]
    ws.drop()
    await asyncio.sleep(0.1)
    assert m._session is None and ws.closed
    await m.stop()


async def test_start_timeout_returns_false(monkeypatch):
    monkeypatch.setattr(vs, "START_TIMEOUT", 0.05)

    class Slow(FakeClient):
        async def connect_lws(self):
            await asyncio.sleep(10)

    m, _, _ = mgr(Slow())
    assert await m.wake() is False
    assert m._session is None


async def test_stop_during_concurrent_wake(monkeypatch):
    monkeypatch.setattr(vs, "ONLINE_BUDGET", 0.05)
    m, client, _ = mgr()
    w = asyncio.create_task(m.wake())
    await asyncio.sleep(0)
    await m.stop()
    assert await w is False
    await spin(5)
    assert m._session is None and m._hold_task is None and m._cont_task is None
    assert all(ws.closed for ws in client.wss)
    assert await m.wake() is False
    m.async_on_ws_connected()
    assert m._cont_task is None
