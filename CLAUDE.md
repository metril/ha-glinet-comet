# ha-glinet-comet

Home Assistant custom integration for the GL.iNet Comet PoE KVM (GL-RM1PE),
which runs `glkvm` — GL.iNet's fork of PiKVM's `kvmd`.

## Architecture

WebSocket push (`/api/ws`) drives fast state; a slow HTTP-poll tier covers
info/version/network reads the socket doesn't carry. Auth is a bearer token
from `/api/auth/login`, with a TOTP code appended to the password when 2FA
is enabled.

## Auth contract

- `POST /api/auth/login`, form-encoded (`data={...}`): `user`, `passwd`,
  `expire=0`. When a `totp_secret` is configured: `passwd = password +
  pyotp.TOTP(secret).now()` (code appended, no separator).
- Success: HTTP 200 JSON `{"ok": true, "result": {"token": "..."}}` — token
  may also appear top-level; check both.
- `403` = password/TOTP rejected. **Do not retry** — the device locks out
  after 10 failures / 600s. `429` = rate-limited, body carries
  `remaining_time`. A `two_step_required` field means two-step device
  approval is enabled (separate from TOTP).
- Authenticated requests: header `Token: <token>`, plus cookie
  `auth_token=<token>` for safety. WebSocket:
  `wss://<host>/api/ws?stream=0&auth_token=<token>`.
- Self-signed cert: aiohttp `ssl=False`.
- Every kvmd response is `{"ok": bool, "result": {...}}`.
- `POST /api/auth/logout` is the only other permitted POST.

## Verified shapes (live, 2026-08-29)

**BLOCKED — not yet captured.** `tools/dump_api.py --probe-writes` was run
once against the live device, with the credentials in `.comet_pass`, and
got back HTTP 403 ("password or TOTP code rejected") on the very first
(and, per policy, only) login attempt of that run. Per the no-retry rule,
the tool stopped immediately rather than trying again — a second
consecutive failure would put the device two-thirds of the way to its
10-attempt/600s lockout.

The device is reachable over HTTPS and the host's clock is NTP-synced, so
this is not a network or clock-skew issue on this end. The `.comet_pass`
parsing was verified offline: it produces a
5-char user, a 32-char password, and a 32-char valid-base32 TOTP secret that
generates a well-formed 6-digit code — so the failure is either a stale/
incorrect credential in `.comet_pass`, or a mismatch between the assumed
login contract (form fields `user`/`passwd`/`expire`, code appended to the
password) and this firmware's actual behavior.

**Before the next run:** verify `.comet_pass` against the device's actual
admin password and current 2FA secret (re-pair if needed), confirm the
device's own clock is correct, then re-run
`uv run python tools/dump_api.py --probe-writes`. Once it succeeds, this
section should be replaced with the real redacted shapes per route and per
WS event type, and the tables in the task-0 report should be filled in.

## Still unverified (everything — pending a successful login)

`info`, `atx`, `hid`, `msd`, `streamer`, `gpio`, `system/get_hostname`,
`system/get_network_config`, `system/get_param`, `upgrade/version`,
`upgrade/compare`, `custom_screen/status`, `auth/check`, `2fa/is_enabled`,
snapshot headers/magic, WS event types and shapes (`atx`, `hid`, `msd`,
`gpio`, `info`, `streamer`, expected first frame `loop`), and the
`--probe-writes` status table for `atx/click`, `hid/events/send_shortcut`,
`hid/set_params`, `msd/set_connected`, `upgrade/reboot`, `hid/print`,
`gpio/switch`.
