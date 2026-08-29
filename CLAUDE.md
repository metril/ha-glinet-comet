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
- `streamer` [200]: `{"features":{...},"limits":{...},"params":{"desired_fps":40,"h264_bitrate":20000,"h264_gop":60,"quality":80,"venc_mode":"smart",...},"streamer":{"encoder":{...},"hdmi":{"out_signal":false,"signal":true},"source":{"captured_fps":60,"online":true,"real_resolution":"1920x1080@60","resolution":{"height":1080,"width":1920}},...}}` — `streamer.streamer.source.online`/`.resolution` both exist.
- `gpio` [200]: `{"model":{"scheme":{"inputs":{},"outputs":{}},"view":{"header":{...},"table":[]}},"state":{"inputs":{},"outputs":{}}}` — no GPIO channels configured on this unit.
- `info?fields=system,health,auth,meta` → **400** `ValidatorError`, "Failed sub-validator on one of the item of ['system','health','auth','meta']" — `health` is not a valid field on this fork; `system`/`auth`/`meta`/`extras` are (see WS `info` below).
- `system/get_hostname`: **lost this run** — a (now-fixed) bug redacted the whole entry via its own route-label name; needs a re-run.
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
- `hid`, `msd`, `gpio`, `streamer` (×1 each): **identical shape to their GET counterpart** (the unwrapped `result`).
- `hid_keymaps` (×1): `{"keymaps":{"available":["ar","bepo","...34 layouts"],"default":"en-us"}}`.
- `ocr` (×1): `{"enabled":false,"engine":"tesseract","langs":{"available":[],"default":[]}}`.
- `recorder` (×1): `{"elapsed_seconds":0,"file":"","recording":false}`.
- `fingerbot` (×1): `{"exist":false}`.
- **No `atx` event fired in 30s.** Unlike `fingerbot`/`ocr` (absent/disabled features that still pushed a status once), ATX pushed nothing — needs a longer capture or an ATX state change to confirm if/when it ever does.

`--probe-writes` (GET only, never POST):

| route | status | route | status |
|---|---|---|---|
| `atx/click` | 405 | `upgrade/reboot` | **200** |
| `hid/events/send_shortcut` | 405 | `hid/print` | 405 |
| `hid/set_params` | 405 | `gpio/switch` | 405 |
| `msd/set_connected` | 405 | | |

`upgrade/reboot` returning 200 on GET is notable: it did **not** actually
reboot — the 30s WS capture and snapshot right after both succeeded on the
same token, and this fork's tokens are in-memory-only (a reboot would
invalidate them). Treat this route as not method-gated like the others.

Snapshot: `GET /api/streamer/snapshot?allow_offline=1` → 200,
`Content-Type: image/jpeg`, first 4 bytes `ffd8ffe0` (valid JPEG magic).
Login token was found at `result.token`.

## Still unverified

- `system/get_hostname` shape (lost to a since-fixed redaction bug; needs a re-run).
- Whether `atx` ever emits a WS event on this hardware.
- Valid `fields=` for a single `GET /api/info` (WS implies `system,auth,meta,extras`; untried as one GET).
- CPU/memory/temp/network-rate metrics: no `health` section exists here; the plan's `system.health.*` sensors need another source or should be dropped.
- `custom_screen/status` with hardware present; `gpio.model.scheme` with real channels (both empty/absent on this unit).
- Actual write behavior of `atx/click`, `hid/*`, `msd/set_connected`, `gpio/switch` (GET-probed only, per the read-only mandate).
