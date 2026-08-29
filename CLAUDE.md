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

## Verified shapes (live, 2026-08-29, fw V1.9.1 release1)

Device: model `RM1PE`, kvmd fork `4.82`, kernel `aarch64`/`6.1.141`,
platform base "Rockchip RV1126B-P EVB V14 Board" (`board=rpi4`,
`type=rpi`). 2FA is enabled. ATX board **not attached** —
`atx.enabled: false` is correct/expected on this unit.

Reads (`GET /api/<path>`, shapes below unwrapped from
`{"ok":bool,"result":{...}}`):

- `atx` [200]: `{"busy":false,"enabled":false,"leds":{"hdd":false,"power":false},"power":"off"}`
- `hid` [200]: `{"busy":false,"connected":true,"enabled":true,"jiggler":{"active":false,...},"keyboard":{"leds":{...},"online":true,...},"mouse":{"absolute":true,"online":false,...},"online":true}` — `hid.connected` exists (top-level bool).
- `msd` [200]: `{"available_devices":{...},"drive":{"cdrom":true,"connected":false,"image":null,"rw":false},"drive_partition":{...},"enabled":true,"online":false,"storage":{"images":{},"parts":{...}}}` — `connected` lives at `msd.drive.connected`; `images` at `msd.storage.images` (empty here).
- `streamer` [200]: `{"features":{...},"limits":{...},"params":{"desired_fps":40,"h264_bitrate":2000-20000,"h264_gop":30-60,"quality":80,"venc_mode":"normal"|"smart",...},"streamer":{...}|null}` — **`streamer.streamer` is sometimes `null`** (seen when the streamer/snapshot was 503ing — see Snapshot below); consumers must null-check it. When populated (a separate run): `streamer.streamer.hdmi:{"out_signal":false,"signal":true}`, `.source:{"captured_fps":60,"online":true,"real_resolution":"1920x1080@60","resolution":{"height":1080,"width":1920}}` — both `source.online`/`.resolution` confirmed to exist when the subsystem is up. `params` also changed between runs (2000/30/normal vs 20000/60/smart) — it's live, mutable state, not static config.
- `gpio` [200]: `{"model":{"scheme":{"inputs":{},"outputs":{}},"view":{"header":{...},"table":[]}},"state":{"inputs":{},"outputs":{}}}` — no GPIO channels configured on this unit.
- `info?fields=system,auth,meta,extras` [200]: sections confirmed `auth`,`extras`,`meta`,`system` (all present as one combined GET). `info?fields=hw` → **400** `ValidatorError`, "Failed sub-validator on one of the item of ['hw']" — upstream kvmd's legacy `hw` field is **also invalid** on this fork, same as `health`. **No CPU/mem/temp/network health metrics are exposed via `/api/info` on this firmware at all** (neither `health` nor `hw`); the plan's `system.health.*` sensors need a different source or should be dropped for v0.1.
- `system/get_hostname` [200]: `{"hostname":"<redacted>","success":true}` — flat under `result`.
- `system/get_network_config` [200]: `{"config":{"dns_servers":["<redacted>","..."],"gateway":"<redacted>","interface":"eth0","ip_address":"<redacted>","is_dhcp":true,"mac_address":"<redacted>","state":"online"},"success":true}`.
- `system/get_param` [200]: `{"absolute_mouse":true,"cdrom_vendor":"Glinet","otg_manufacturer":"Glinet","otg_serial":"<redacted>","privacy_enable":false,"success":true}` — static hardware/OTG descriptors, not live state.
- `upgrade/version` [200]: `{"model":"RM1PE","version":"V1.9.1 release1"}`.
- `upgrade/compare` [200]: `{"local_model":"RM1PE","local_version":"...","server_model":"RM1PE","server_version":"...","release_note":"## New Features…","beta_version":"","error":null}`.
- `custom_screen/status` → 400 `BadRequestError`, "Failed to get custom screen status: Command […] exit status 4" — no OLED screen on this unit.
- `auth/check` [200]: `{}` (200 alone confirms the token).
- `2fa/is_enabled` [200]: `{"enabled":true}`.

WS events seen in 30s, first frame was `loop`:

- `loop` (×1): `{"version":{"major":4,"minor":82}}`.
- `info` (×4, **one section per frame** — merge, don't expect one shot): `auth` (`{"enabled":true}`), `meta` (`{"kvm":{},"server":{"host":"<redacted>"}}`), `system` (`kernel`, `kvmd.version`, `platform.{base,board,model,serial,type,video}` — **`serial` exists**, redacted — `streamer`), `extras` (per-daemon list: ipmi/janus/media/pion/vnc/webrtc, each `{enabled,started,...}`). No `health` section ever appeared, matching the GET 400 above.
- `hid`, `msd`, `gpio`, `streamer` (×1+ each): **identical shape to their GET counterpart** (the unwrapped `result`), including `streamer.streamer` also arriving `null` in the same run the GET showed it `null`. One run saw `hid` fire twice with `mouse.online` flipping `true`→`false` between frames — live confirmation the push events reflect real state changes.
- `hid_keymaps` (×1): `{"keymaps":{"available":["ar","bepo","...34 layouts"],"default":"en-us"}}`.
- `ocr` (×1): `{"enabled":false,"engine":"tesseract","langs":{"available":[],"default":[]}}`.
- `recorder` (×1): `{"elapsed_seconds":0,"file":"","recording":false}`.
- `fingerbot` (×1): `{"exist":false}`.
- **No `atx` event fired in 30s, confirmed across two separate runs.** Unlike `fingerbot`/`ocr` (absent/disabled features that still pushed a status once), ATX pushed nothing — needs a longer capture or an ATX state change to confirm if/when it ever does.

## Destructive GET routes — never probe

`/api/upgrade/reboot` and `/api/upgrade/reset_default` are registered as
**GET** handlers in glkvm and execute for real on GET — no method gating.
A GET probe of `/api/upgrade/reboot` in an earlier run returned a plain
200 and really did reboot the device (confirmed by source review after
the fact) — the CLAUDE.md note from that run claiming it "did not reboot"
was **wrong**; it was inferred from the WS capture completing right after,
which just means the reboot hadn't taken effect yet at that instant.
`/api/upgrade/start` and `/api/msd/partition_format` are treated the same
way out of caution (registered similarly in glkvm). `tools/dump_api.py`
hard-codes these four as `NEVER_TOUCH` and every GET helper in it refuses
to request them, independent of `--probe-writes` or the `READS`/
`PROBE_PATHS` lists. Do not remove that guard or add these paths back to
any read/probe list without confirming safety from source first.

`--probe-writes` (GET only, never POST) — from the run before the
`upgrade/reboot` incident:

| route | status | route | status |
|---|---|---|---|
| `atx/click` | 405 | `hid/print` | 405 |
| `hid/events/send_shortcut` | 405 | `gpio/switch` | 405 |
| `hid/set_params` | 405 | `upgrade/reboot` | **200 — reboots; NEVER_TOUCH, not probed again** |
| `msd/set_connected` | 405 | | |

Snapshot: `GET /api/streamer/snapshot?allow_offline=1` → **200** JPEG
(`ffd8ffe0` magic) in one run; **503** JSON error body in another, at the
same time `streamer.streamer` was `null` — the streamer subsystem isn't
always up; consumers must handle both. Login token was found at
`result.token` (confirmed across both runs).

## Still unverified

- Whether `atx` ever emits a WS event on this hardware (0/2 runs so far).
- `custom_screen/status` with hardware present; `gpio.model.scheme` with real channels (both empty/absent on this unit).
- Actual write behavior of `atx/click`, `hid/*`, `msd/set_connected`, `gpio/switch` (GET-probed only, per the read-only mandate — see NEVER_TOUCH above for why POST-probing isn't done at all).
