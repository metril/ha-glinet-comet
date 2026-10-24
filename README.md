# GL.iNet Comet

A Home Assistant custom integration for the [GL.iNet Comet / Comet PoE KVM](https://www.gl-inet.com/products/gl-rm1/)
(GL-RM1 / GL-RM1PE) and Comet X (GL-RM4PE) — a network KVM running GL.iNet's `glkvm` firmware, a fork
of [PiKVM's](https://pikvm.org/) `kvmd`.

Supported models: Comet (GL-RM1), Comet PoE (GL-RM1PE), Comet X (GL-RM4PE,
4-port switch). Tested against a GL-RM1PE on firmware **V1.9.1 release1**
(2FA enabled) and a GL-RM4PE on **V1.10.1 release4**. Port-switch and
mouse-output *writes* are unverified on live hardware.

State is pushed from the device over a WebSocket (local push) with a slow
HTTP-poll tier for the handful of reads (hostname, network, firmware) the
socket doesn't carry.

## What's new in 0.4.0

Comet X (GL-RM4PE) support: Active Port select, per-port video/USB link and
ATX entities, CPU/memory/temperature/network health sensors (firmware
V1.10.1+), and a Mouse Output select.

Efficiency: fewer slow-tier reads (`hid`/`streamer` are skipped while the
WebSocket is up, `hostname` is throttled, the unused `hw` read is gone), no
state fan-out on unchanged WebSocket frames, firmware upgrades now reach the
device registry, and video capture is off by default (enable "Keep video
capture active" for the HDMI/resolution/FPS sensors and camera snapshot). The
Captured FPS sensor is disabled by default.

## What's new in 0.3.0

New diagnostic entities (WebSocket Connected, Captured FPS, MAC Address,
Gateway, DHCP), `runtime_data`-based setup, integration-level services, and
entities that stay available while the WebSocket is up. Requires Home
Assistant **2025.8** or newer.

## Entities

| Platform | Entity | Notes |
|---|---|---|
| binary_sensor | ATX Power | Requires GL.iNet's ATX board; unavailable without one |
| binary_sensor | ATX HDD Activity | Requires the ATX board |
| binary_sensor | HDMI Signal | |
| binary_sensor | WebSocket Connected | Diagnostic |
| binary_sensor | Keyboard Online | |
| binary_sensor | Mouse Online | |
| binary_sensor | GPIO inputs | Only if GPIO channels are configured on the Comet (none on the tested unit); channels configured later appear within one poll interval, no reload needed |
| binary_sensor | Port N Video Link / USB Link | Comet X only; one pair per port |
| binary_sensor | Port N Power LED / HDD Activity | Comet X only; needs the ATX option and GL.iNet's ATX board (unavailable otherwise) |
| sensor | Resolution | |
| sensor | MSD Image | Currently mounted virtual-media image |
| sensor | KVMD Version | |
| sensor | Firmware Version | |
| sensor | IP Address | |
| sensor | Hostname | |
| sensor | MAC Address | Diagnostic |
| sensor | Gateway | Diagnostic |
| sensor | DHCP | Diagnostic |
| sensor | Captured FPS | Needs an active video stream |
| sensor | CPU Usage / Memory Usage / CPU Temperature | Diagnostic; firmware V1.10.1+ (skipped where unsupported) |
| sensor | Network RX Rate / TX Rate | Diagnostic; disabled by default |
| switch | Mouse Jiggler | |
| switch | HID Connected | |
| switch | Virtual Media | Connects/disconnects the mounted MSD image; unavailable until an image is selected |
| switch | GPIO outputs | Only if GPIO channels are configured on the Comet (none on the tested unit); channels configured later appear within one poll interval, no reload needed |
| button | ATX Power | Requires the ATX board |
| button | ATX Power (Long Press) | Requires the ATX board |
| button | ATX Reset | Requires the ATX board |
| button | Reset HID | |
| button | Reboot | Reboots the Comet — see [Notes](#notes--gotchas) |
| button | GPIO pulse outputs | Only if GPIO channels are configured on the Comet (none on the tested unit); channels configured later appear within one poll interval, no reload needed |
| button | Port N ATX Power / Power (Long Press) / Reset | Comet X only; needs the ATX option |
| select | MSD Image | Choose which uploaded image is mounted |
| select | Active Port | Comet X only; switches the active target port |
| select | Mouse Output | Config; HID mouse output mode (usb, usb_rel, ...) |
| camera | Screen | JPEG snapshot of the captured video, not a live stream |
| update | Firmware | Read-only — see [Notes](#notes--gotchas) |

### Services

- `glinet_comet.type_text` — type a text string via the virtual keyboard.
- `glinet_comet.send_shortcut` — send a keyboard shortcut (comma-separated
  kvmd key names).

```yaml
service: glinet_comet.type_text
target:
  device_id: <your Comet device_id>
data:
  text: "hello world"
  keymap: en

service: glinet_comet.send_shortcut
target:
  device_id: <your Comet device_id>
data:
  keys: "ControlLeft,AltLeft,Delete"
```

## Installation

### HACS (custom repository)

1. HACS → Integrations → menu → **Custom repositories**.
2. Add `metril/ha-glinet-comet` as category **Integration**.
3. Install **GL.iNet Comet** and restart Home Assistant.

(This integration isn't in the default HACS list — that requires a
[`home-assistant/brands`](https://github.com/home-assistant/brands) PR
adding `glinet_comet`, which hasn't been submitted yet.)

### Manual

Copy `custom_components/glinet_comet` into your Home Assistant `custom_components`
directory and restart.

## Configuration

Set up via **Settings → Devices & Services → Add Integration → GL.iNet Comet**.

| Field | Notes |
|---|---|
| Host | IP address or hostname. Connects over HTTPS; the Comet uses a self-signed certificate, so **Verify SSL** defaults to off. |
| Username | `admin` — the Comet has a single admin account. |
| Password | The Comet's admin password. |
| 2FA secret (base32) | Only if 2FA is enabled on the Comet — see below. Leave empty if 2FA is off. |
| Verify SSL certificate | Off by default; enable only if you've replaced the Comet's certificate. |

### Finding the 2FA secret

The integration needs the raw base32 TOTP secret, not a 6-digit code:

- **Enrolling 2FA for the first time**: the Comet's web UI shows the base32
  secret (and a QR code) at enrollment time — copy it then.
- **2FA already enabled**: while logged into the Comet's web UI, open
  `https://<comet-ip>/api/2fa/show` in the same browser and copy the value
  of the `secret=` parameter from the `otpauth://` URI it returns.

The Home Assistant host's clock must be NTP-synced — TOTP codes are only
valid for a ±30 second window.

## Options

**Settings → Devices & Services → GL.iNet Comet → Configure**

| Option | Default | Notes |
|---|---|---|
| Slow poll interval (seconds) | 300 | How often the slow HTTP tier (hostname, network, firmware) is polled. |
| WebSocket reconnect delay (seconds) | 5 | Delay before reconnecting a dropped WebSocket. |
| HTTP request timeout (seconds) | 10 | |
| Enable ATX power control | On | Creates the ATX entities. Requires GL.iNet's ATX board — the entities exist but stay unavailable without one. |
| Keep video capture active | Off | Keeps the Comet's video pipeline running so the HDMI Signal, Resolution, and FPS sensors and the Screen camera snapshot stay live, at the cost of Comet CPU. Off (default): those only update while someone is viewing the Comet's own web UI. **Known limitation:** on the Comet PoE (GL-RM1PE, fw V1.10.1) this is not enough: the device keeps its streamer process alive but reports no HDMI signal and the snapshot returns 503 until a real web-UI (WebRTC) viewer has been open; the capture drops again ~40 s after the viewer closes. The Comet X (GL-RM4PE) does not have this problem. |

## Notes / gotchas

- **Login lockout**: the Comet locks logins for 10 minutes after 10 failed
  attempts. This integration never retries a failed login — a wrong
  password or 2FA secret surfaces immediately as `invalid_auth` instead of
  being retried.
- **Reboot really reboots**: the `Reboot` button issues an immediate reboot
  with no confirmation step.
- **No ATX board**: the device this integration was developed and tested
  against has no GL.iNet ATX board attached, so the ATX entities are
  present but unavailable on it — ATX behavior is unverified live.
- **Health sensors**: CPU / memory / temperature / network rates need
  firmware V1.10.1+ (V1.9.1 400s on them; they're then not created).
- **Comet X ports**: port entities appear only where the device exposes
  `/api/switch` (the Comet PoE on V1.10.1 release2 404s it).
- **MSD images**: images must be uploaded to the Comet through its own web
  UI first; the `MSD Image` select only chooses among images already there.
- **Firmware**: the Firmware update entity is read-only — it shows when
  GL.iNet publishes a newer version; install it from the Comet's own web UI
  (System → Upgrade).
- **Resilient polling**: a single subsystem's API error no longer makes
  every entity unavailable — that subsystem just keeps its last value until
  the next successful poll (a connection or auth failure, or every core
  read failing in the same cycle, still fails the cycle).

## Not yet supported

- OLED screen (`custom_screen`)
- Wake-on-LAN
- Fingerbot
- Installing firmware updates from Home Assistant
- Uploading MSD images from Home Assistant

## Development

```bash
uv venv
uv pip install pytest pytest-asyncio aiohttp pyotp
uv run pytest -q
```

`tools/dump_api.py` is a standalone, strictly read-only discovery tool used to
map the device's API shapes — it never calls Home Assistant code:

```bash
COMET_HOST=<device-ip-or-hostname> uv run python tools/dump_api.py
```

Credentials are read from a gitignored `.comet_pass` file in the repo root
(`user:`, `password:`, `totp_secret:` lines). **Never** point any tooling at
`/api/upgrade/reboot` or `/api/upgrade/reset_default` — both are registered
as `GET` routes on the device and execute (reboot / factory-reset)
immediately on GET, with no confirmation.

## License

MIT — see [LICENSE](LICENSE).

## Brand assets

Brand icons live in `custom_components/glinet_comet/brand/` for HACS's
`render_readme`. Getting this integration's icon/logo into Home Assistant's
default brands catalog would require a separate PR to
[`home-assistant/brands`](https://github.com/home-assistant/brands).
