#!/usr/bin/env python3
"""Dump the GL.iNet Comet kvmd-style HTTPS API (redacted) to map field shapes.

Standalone: uses aiohttp + pyotp (installed via ``uv pip install``), no Home
Assistant. Strictly read-only against the device: the only POSTs made are
``/api/auth/login`` (exactly once, never retried) and ``/api/auth/logout``
(harmless, at the end). ``--probe-writes`` sends GET — never POST — to the
write-only routes, to record whether they exist (405) or not (404).

Credentials are read from ``.comet_pass`` in the repo root and are never
printed, logged, or written to the dump.

DESTRUCTIVE GET ROUTES — NEVER PROBE: ``/api/upgrade/reboot`` and
``/api/upgrade/reset_default`` are registered as GET handlers in glkvm and
actually execute (reboot / factory-reset) on GET — there is no method
gating to rely on. A prior GET probe of ``/api/upgrade/reboot`` really did
reboot the device, despite returning a plain 200. ``/api/upgrade/start``
and ``/api/msd/partition_format`` are treated the same way out of caution.
These four paths are listed in ``NEVER_TOUCH`` below and are refused by
every GET helper in this file, regardless of ``--probe-writes`` or the
``READS``/``PROBE_PATHS`` lists — do not remove or work around that guard.

Usage:

    uv run python tools/dump_api.py --host <device-ip-or-hostname> [--probe-writes]
    # or: COMET_HOST=<device-ip-or-hostname> uv run python tools/dump_api.py

Writes redacted JSON to ``comet_api_dump.json`` in the current directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import aiohttp
import pyotp

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PASS_FILE = os.path.join(HERE, "..", ".comet_pass")

# GET routes to dump: (label, path).
READS = [
    # "health" is not a valid field on this fork (confirmed 400 in a prior
    # run); the WS `info` event stream showed the valid sections are
    # system/auth/meta/extras.
    ("info", "/api/info?fields=system,auth,meta,extras"),
    # Upstream kvmd's legacy field, carrying hw.health.{temp,cpu,mem,net} +
    # hw.platform on stock kvmd/PiKVM. Unconfirmed on this fork — if this
    # 400s, that is itself the finding (no hw/health metrics available).
    ("info_hw", "/api/info?fields=hw"),
    ("atx", "/api/atx"),
    ("hid", "/api/hid"),
    ("msd", "/api/msd"),
    ("streamer", "/api/streamer"),
    ("gpio", "/api/gpio"),
    ("system_hostname", "/api/system/get_hostname"),
    ("system_network_config", "/api/system/get_network_config"),
    ("system_param", "/api/system/get_param"),
    ("upgrade_version", "/api/upgrade/version"),
    ("upgrade_compare", "/api/upgrade/compare"),
    ("custom_screen_status", "/api/custom_screen/status"),
    ("auth_check", "/api/auth/check"),
    ("twofa_is_enabled", "/api/2fa/is_enabled"),
]

# Write-only routes probed with GET only (never POST) to see if they exist.
# NOTE: "/api/upgrade/reboot" is deliberately NOT in this list — see
# NEVER_TOUCH below. It is GET-registered in glkvm and a prior GET probe
# really did reboot the device despite returning a plain 200; there is no
# method gating to rely on for it.
PROBE_PATHS = [
    "/api/atx/click",
    "/api/hid/events/send_shortcut",
    "/api/hid/set_params",
    "/api/msd/set_connected",
    "/api/hid/print",
    "/api/gpio/switch",
    "/api/gpio/pulse",
]

# Routes that execute a destructive action on GET (no method gating) —
# confirmed for /api/upgrade/reboot by a real, unintended reboot from a
# prior GET probe; the other three are treated the same way out of caution
# since glkvm registers them the same way. Every GET helper in this file
# checks against this list and refuses to request any of them, regardless
# of --probe-writes or what's in READS/PROBE_PATHS. Do not remove a path
# from this list without confirming (via source, not by testing live) that
# it is safe.
NEVER_TOUCH = (
    "/api/upgrade/reboot",
    "/api/upgrade/reset_default",
    "/api/upgrade/start",
    "/api/msd/partition_format",
)


class ForbiddenPathError(Exception):
    """Raised when something tries to GET a path in NEVER_TOUCH. Never
    catch-and-continue this — remove the caller instead."""


def _guard_path(path: str) -> None:
    bare = path.split("?", 1)[0]
    if bare in NEVER_TOUCH:
        raise ForbiddenPathError(
            f"refusing to GET {bare!r}: this route executes destructively "
            "on GET (no method gating) and must never be requested by this "
            "tool, with or without --probe-writes"
        )


# --- Redaction -------------------------------------------------------------

# Whole-segment match only: a key is split on separators and each segment is
# compared for exact equality against this set. This is deliberately NOT a
# substring match — "description"/"chip"/"machine"/"script"/"clipboard"/
# "keyboard"/"keymap"/"platform" must survive untouched even though they
# contain "ip"/"mac"/"key" as substrings. "hostname" doesn't split into a
# "host" segment on its own, so it's listed explicitly.
_SENSITIVE_TERMS = {
    "serial", "token", "passwd", "password", "mac", "ip", "address",
    "host", "hostname", "ssid", "key", "secret", "email", "imei", "uuid",
}
_SEGMENT_SPLIT_RE = re.compile(r"[_.\-]")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _is_sensitive_key(key: str) -> bool:
    segments = _SEGMENT_SPLIT_RE.split(str(key).lower())
    return any(seg in _SENSITIVE_TERMS for seg in segments)


def redact(obj, token: str | None = None):
    """Recursively redact sensitive keys/values. Also masks IPv4s and the
    literal login token wherever they show up inside string values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _is_sensitive_key(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, token)
        return out
    if isinstance(obj, list):
        return [redact(v, token) for v in obj]
    if isinstance(obj, str):
        s = obj
        if token:
            s = s.replace(token, "<redacted>")
        s = IPV4_RE.sub("<redacted>", s)
        return s
    return obj


# --- Credentials -------------------------------------------------------------

_KV_LINE = re.compile(r"^(user|password|passwd|totp_secret|secret)\s*:\s*(.*)$", re.IGNORECASE)
_NORMALIZE_KEY = {"passwd": "password", "secret": "totp_secret"}


def _unquote(value: str) -> str:
    """Strip a single matching pair of surrounding quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_comet_pass(path: str) -> tuple[str, str, str]:
    """Parse ``.comet_pass``. Supports ``key: value`` lines (user/password/
    totp_secret) or a single ``user:pass:secret`` line; values may optionally
    be wrapped in matching quotes. Never prints contents.
    """
    if not os.path.exists(path):
        print(f"credentials file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r") as fh:
        raw_lines = fh.read().splitlines()
    lines = [ln.strip() for ln in raw_lines if ln.strip() and not ln.strip().startswith("#")]

    data: dict[str, str] = {}
    kv_format = False
    for line in lines:
        m = _KV_LINE.match(line)
        if m:
            kv_format = True
            key = _NORMALIZE_KEY.get(m.group(1).lower(), m.group(1).lower())
            data[key] = _unquote(m.group(2).strip())

    if kv_format:
        return data.get("user", ""), data.get("password", ""), data.get("totp_secret", "")

    if not lines:
        return "", "", ""
    parts = lines[0].split(":")
    user = _unquote(parts[0]) if len(parts) > 0 else ""
    password = _unquote(parts[1]) if len(parts) > 1 else ""
    totp_secret = _unquote(parts[2]) if len(parts) > 2 else ""
    return user, password, totp_secret


# --- HTTP helpers ------------------------------------------------------------


async def login(session: aiohttp.ClientSession, host: str, user: str, password: str,
                 totp_secret: str, ssl_param) -> tuple[str, str]:
    """Log in exactly once. Never retries — every failure counts toward the
    device's 10 attempts / 600s lockout."""
    passwd = password
    if totp_secret:
        passwd = password + pyotp.TOTP(totp_secret).now()

    url = f"https://{host}/api/auth/login"
    try:
        async with session.post(
            url,
            data={"user": user, "passwd": passwd, "expire": "0"},
            ssl=ssl_param,
            allow_redirects=False,
        ) as resp:
            status = resp.status
            text_body = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        print(f"login request failed: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        body = json.loads(text_body)
    except (ValueError, TypeError):
        body = None

    if status == 403:
        print("password or TOTP code rejected; check .comet_pass and clock")
        sys.exit(2)

    if status == 429:
        remaining = None
        if isinstance(body, dict):
            result = body.get("result") if isinstance(body.get("result"), dict) else body
            remaining = result.get("remaining_time")
        print(f"rate-limited; remaining_time={remaining}")
        sys.exit(2)

    if isinstance(body, dict):
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        if result.get("two_step_required") or body.get("two_step_required"):
            print("two-step approval enabled")
            sys.exit(2)

    if status != 200 or not isinstance(body, dict):
        print(f"login failed: status={status}")
        sys.exit(2)

    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    token = None
    token_location = None
    if result.get("token"):
        token = result["token"]
        token_location = "result.token"
    elif body.get("token"):
        token = body["token"]
        token_location = "top-level"

    if not token:
        print("login succeeded but no token found in response")
        sys.exit(2)

    return token, token_location


def _auth_kwargs(token: str) -> dict:
    return {"headers": {"Token": token}, "cookies": {"auth_token": token}}


async def api_get(session: aiohttp.ClientSession, host: str, path: str, token: str, ssl_param) -> dict:
    _guard_path(path)
    url = f"https://{host}{path}"
    try:
        async with session.get(url, ssl=ssl_param, **_auth_kwargs(token)) as resp:
            status = resp.status
            raw = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return {"status": None, "error": str(exc)}

    text = raw.decode("utf-8", errors="replace")
    try:
        return {"status": status, "body": json.loads(text)}
    except (ValueError, TypeError):
        return {"status": status, "body_text": text[:200]}


async def get_snapshot(session: aiohttp.ClientSession, host: str, token: str, ssl_param) -> dict:
    path = "/api/streamer/snapshot?allow_offline=1"
    _guard_path(path)
    url = f"https://{host}{path}"
    try:
        async with session.get(url, ssl=ssl_param, **_auth_kwargs(token)) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            raw = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return {"status": None, "error": str(exc)}
    return {
        "status": status,
        "content_type": content_type,
        "body_length": len(raw),
        "first_bytes_hex": raw[:4].hex(),
    }


async def run_probes(session: aiohttp.ClientSession, host: str, token: str, ssl_param) -> dict:
    """GET (never POST) the write-only routes; record status plus the first
    200 chars of the response body."""
    results = {}
    for path in PROBE_PATHS:
        _guard_path(path)  # PROBE_PATHS is a fixed literal list, but never trust that alone
        url = f"https://{host}{path}"
        try:
            async with session.get(url, ssl=ssl_param, **_auth_kwargs(token)) as resp:
                status = resp.status
                raw = await resp.read()
            body_preview = raw.decode("utf-8", errors="replace")[:200]
            results[path] = {"status": status, "body_preview": body_preview}
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            results[path] = {"status": None, "error": str(exc)}
    return results


async def capture_ws(host: str, token: str, ssl_param, duration: float) -> dict:
    """Record WS frames for ``duration`` seconds: first full frame per
    event_type, up to 3 later deltas per type, and a count per type."""
    url = f"wss://{host}/api/ws?stream=0&auth_token={token}"
    events: dict = {}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, ssl=ssl_param, heartbeat=30) as ws:
                start = time.monotonic()
                while True:
                    remaining = duration - (time.monotonic() - start)
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            frame = json.loads(msg.data)
                        except (ValueError, TypeError):
                            continue
                        event_type = frame.get("event_type")
                        event = frame.get("event")
                        if event_type is None:
                            continue
                        entry = events.setdefault(
                            event_type, {"count": 0, "first": None, "deltas": []}
                        )
                        entry["count"] += 1
                        if entry["first"] is None:
                            entry["first"] = event
                        elif len(entry["deltas"]) < 3:
                            entry["deltas"].append(event)
                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSING,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        events["_error"] = str(exc)
    return events


async def logout(session: aiohttp.ClientSession, host: str, token: str, ssl_param) -> None:
    if not token:
        return
    url = f"https://{host}/api/auth/logout"
    try:
        async with session.post(url, ssl=ssl_param, **_auth_kwargs(token)) as resp:
            await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass


# --- Summary -----------------------------------------------------------------


def _body_of(dump: dict, label: str):
    entry = dump.get("reads", {}).get(label, {})
    return entry.get("body") if isinstance(entry, dict) else None


def _result_of(body) -> dict:
    if isinstance(body, dict):
        result = body.get("result")
        if isinstance(result, dict):
            return result
        return body
    return {}


def summarize(dump: dict) -> str:
    lines = []

    upgrade = _result_of(_body_of(dump, "upgrade_version"))
    lines.append(f"upgrade/version (fw/model): {json.dumps(upgrade)[:200]}")

    atx = _result_of(_body_of(dump, "atx"))
    lines.append(f"atx.enabled: {atx.get('enabled') if isinstance(atx, dict) else 'n/a'}")

    for label in ("hid", "msd", "streamer"):
        r = _result_of(_body_of(dump, label))
        keys = sorted(r.keys()) if isinstance(r, dict) else []
        lines.append(f"{label} top-level keys: {keys}")

    gpio = _result_of(_body_of(dump, "gpio"))
    scheme = None
    if isinstance(gpio, dict) and isinstance(gpio.get("model"), dict):
        scheme = gpio["model"].get("scheme")
    scheme_keys = sorted(scheme.keys()) if isinstance(scheme, dict) else scheme
    lines.append(f"gpio.model.scheme: {scheme_keys}")

    ws_events = dump.get("ws_events", {})
    if isinstance(ws_events, dict):
        counts = {k: v.get("count") for k, v in ws_events.items() if isinstance(v, dict)}
        lines.append(f"WS event types (counts): {counts}")
        first_type = None
        if ws_events:
            first_type = next(iter(ws_events))
        lines.append(f"first WS event_type observed: {first_type}")

    probes = dump.get("probe_writes", {})
    if probes:
        lines.append(f"probe statuses: {probes}")

    snap = dump.get("snapshot", {})
    lines.append(
        "snapshot: status={} content_type={} first_bytes_hex={}".format(
            snap.get("status"), snap.get("content_type"), snap.get("first_bytes_hex")
        )
    )

    return "\n".join(lines)


# --- Main ----------------------------------------------------------------


async def run(host: str, user: str, password: str, totp_secret: str, ssl_param,
              probe_writes: bool, ws_duration: float) -> dict:
    dump: dict = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "probe_writes_run": probe_writes,
        },
        "reads": {},
        "probe_writes": {},
        "snapshot": {},
        "ws_events": {},
    }

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        token, token_location = await login(session, host, user, password, totp_secret, ssl_param)
        try:
            for label, path in READS:
                dump["reads"][label] = await api_get(session, host, path, token, ssl_param)

            dump["snapshot"] = await get_snapshot(session, host, token, ssl_param)

            if probe_writes:
                dump["probe_writes"] = await run_probes(session, host, token, ssl_param)

            dump["ws_events"] = await capture_ws(host, token, ssl_param, ws_duration)
        finally:
            await logout(session, host, token, ssl_param)

    # Redact each substructure's *content* independently, rather than one
    # blanket redact(dump, token) over the whole tree. A blanket pass would
    # run _is_sensitive_key() over our own bookkeeping keys too — the route
    # labels in READS and the WS event_type names — not just the API's own
    # field names. That's how a route we named "system_hostname" (the label
    # is ours, chosen for READS) previously vanished entirely: the label
    # contains the segment "hostname", not just the value. Route/event-type
    # names are fixed literals we control and are never sensitive by
    # themselves; only recurse the sensitive-key filter into actual payload
    # content.
    redacted_meta = redact(dump["meta"], token)
    redacted_meta["token_location"] = token_location  # set after redact(); see above
    redacted = {
        "meta": redacted_meta,
        "reads": {label: redact(entry, token) for label, entry in dump["reads"].items()},
        # Each probe result is {"status": int, "body_preview": str} (or an
        # "error" dict on a request failure) — redact() only touches its
        # nested string content (IPs/token), never the path key itself.
        "probe_writes": {
            path: redact(value, token) for path, value in dump["probe_writes"].items()
        },
        "snapshot": redact(dump["snapshot"], token),
        "ws_events": {
            event_type: (
                {
                    "count": entry.get("count", 0),
                    "first": redact(entry.get("first"), token),
                    "deltas": [redact(d, token) for d in entry.get("deltas", [])],
                }
                if isinstance(entry, dict)
                else redact(entry, token)  # e.g. "_error": "<exception text>"
            )
            for event_type, entry in dump["ws_events"].items()
        },
    }
    return redacted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("COMET_HOST"),
        help="Device LAN IP or hostname (or set COMET_HOST). Required — no default.",
    )
    parser.add_argument("--pass-file", default=DEFAULT_PASS_FILE)
    parser.add_argument(
        "--probe-writes",
        action="store_true",
        help="GET (never POST) write-only routes to record 404 vs 405",
    )
    parser.add_argument(
        "--verify-ssl",
        action="store_true",
        help="Verify the TLS certificate (default: insecure, self-signed cert)",
    )
    parser.add_argument("--ws-duration", type=float, default=30.0)
    args = parser.parse_args()

    if not args.host:
        print(
            "no device host given: pass --host <ip-or-hostname> or set COMET_HOST",
            file=sys.stderr,
        )
        sys.exit(1)

    ssl_param = None if args.verify_ssl else False

    user, password, totp_secret = parse_comet_pass(args.pass_file)
    if not user or not password:
        print("`.comet_pass` missing user/password", file=sys.stderr)
        sys.exit(1)

    dump = asyncio.run(
        run(args.host, user, password, totp_secret, ssl_param, args.probe_writes, args.ws_duration)
    )

    out_path = os.path.join(os.getcwd(), "comet_api_dump.json")
    with open(out_path, "w") as fh:
        json.dump(dump, fh, indent=2, sort_keys=True)

    print(summarize(dump))
    print(f"\nWrote redacted dump to: {out_path}")


if __name__ == "__main__":
    main()
