# ha-glinet-comet

Home Assistant custom integration for the GL.iNet Comet PoE KVM (GL-RM1PE),
which runs `glkvm` — GL.iNet's fork of PiKVM's `kvmd`.

## Architecture

- `api.py`: HTTP + WebSocket client. `coordinator.py`:
  `CometDataUpdateCoordinator` combines a WebSocket push tier (fast state)
  with a slow HTTP-poll tier (`update_interval`, default 300s via
  `CONF_SCAN_INTERVAL`) for reads the socket doesn't carry (hostname,
  network, firmware/upgrade info; `atx` is also polled here — see Gotchas).
- State schema (`coordinator._state`): `atx`, `hid`, `msd`, `streamer`,
  `gpio`, `gpio_model`, `gpio_labels`, `info`, `glinet` (hostname/network/
  upgrade_*), `unsupported` (fields the firmware 400'd on, skipped on later
  cycles), `ws_connected`.
- WS-pushed state is applied via `self.data = dict(self._state)` +
  `self.async_update_listeners()` (`_push_state`) — **not**
  `async_set_updated_data`, which reschedules `update_interval` on every
  call and would starve the slow HTTP tier if called per WS frame.
- Soft-core failure tier: a per-subsystem `CometApiError` (any status,
  including 400/404) on a CORE read (`atx`, `info_system`, `hid`, `msd`,
  `streamer`) keeps that subsystem's last state, logs warn-once/then-debug
  (`_log_read_failure`, tracked via `_read_warned`), and the cycle still
  succeeds — core reads are never added to `unsupported` (that means "skip
  forever"). Exceptions: if ALL core reads fail in the same cycle, or this
  is the first-ever cycle (`_had_successful_cycle` still `False` —
  `CometEntity.__init__` snapshots device serial/model/sw_version from that
  first cycle), the cycle raises `UpdateFailed` instead. Auth/rate-limit/
  connection errors and optional-read 400/404-vs-retry rules are unchanged.
- Subsystem merges are deep-merges (`_deep_merge`), never wholesale
  replacement, except `msd.storage` (a merge could never notice a removed
  image) and `gpio`/`gpio_model` on a full `GET /api/gpio`.
- GPIO entities (v0.2.0, dynamic since v0.2.1): one `binary_sensor` per
  input channel, plus a `switch` per output channel whose model has
  `switch: true` or a `button` per output channel with a `pulse` config
  (`max_delay` missing/`None` or `> 0`; explicit `max_delay: 0` means
  "pulse disabled" per upstream kvmd) and no switch capability. Only
  which entity kind a channel gets (switch vs. pulse button vs. skipped)
  is decided once, at setup; a channel's label and, for a pulse button,
  its `delay` are read live from `coordinator.data` on every access/press
  (falling back to a title-cased channel id for the label), so a relabel
  or a changed delay reaches the entity without a reload. Entities are
  unavailable unless the channel reports `online: true`. No optimistic
  update — the device pushes `gpio` WS frames. `api.py` gained
  `gpio_switch(channel, state)` / `gpio_pulse(channel, delay)`;
  `parsers.py` gained `atx_hdd_active`,
  `gpio_channel`, `gpio_model_channels`, `gpio_display_name`. `gpio` is
  polled every slow cycle, same as `atx` (see Gotchas); each platform adds
  new channels dynamically via `gpio.py`'s `async_setup_gpio_entities`,
  guarded by a `gpio_model` object-identity check plus a per-channel
  dedupe set. A channel that vanishes from a later `gpio_model` is never
  removed — its entity is just left unavailable.

## Auth

- `POST /api/auth/login` once, form-encoded: `user`, `passwd`, `expire=0`.
  When `totp_secret` is configured, `passwd = password +
  pyotp.TOTP(secret).now()` (appended, no separator, fresh every call).
  Token read from `result.token` (top-level `token` also checked).
  Self-signed cert: aiohttp `ssl=False` unless `verify_ssl` is set.
- Authenticated HTTP requests send header `Token: <token>`. WebSocket:
  `wss://<host>/api/ws?stream=<0|1>&auth_token=<token>` — `stream=1` is the
  default (`keep_video_active` option), since kvmd only keeps its video
  pipeline running while a `stream=1` client is connected.
- On `401`/`403` (HTTP or WS handshake), re-login **once** and retry; a
  second failure raises `CometAuthError` → `ConfigEntryAuthFailed` (HA
  reauth). `429` raises `CometRateLimitError(remaining_time)` →
  `UpdateFailed`; the WS loop sleeps `remaining_time` before reconnecting.
- **Never loop on login failure.** The device locks logins for 10 minutes
  after 10 failures, every extra attempt counts toward that —
  `async_login()` and `tools/dump_api.py`'s `login()` are single-shot by
  design; don't add a retry loop around either.
- `.comet_pass` (dev tool only, gitignored) values may be wrapped in one
  matching pair of quotes (`user: "admin"`); `_unquote()` strips exactly
  one such pair, no more.

## Gotchas learned

- `CometApiError.status` (HTTP status; `200` for an `ok:false` envelope;
  `None` for an unparseable body) is what the coordinator uses to decide
  whether an optional read is unsupported: only `400`/`404` mark a field
  `unsupported` (skipped thereafter), any other status just retries next
  cycle.
- `info?fields=hw` and `info?fields=health` both 400 on V1.9.1 — this
  firmware exposes no CPU/mem/temp/network metrics at all; don't
  reintroduce those sensors without re-verifying on other firmware.
- `streamer.streamer` can be `null` and the snapshot endpoint can 503 when
  no one has an active video viewer — always null-check / JPEG-magic-check
  (`\xff\xd8`, see `get_snapshot`) rather than assuming a shape.
- No `atx` WebSocket event was ever observed (0/2 live captures), unlike
  other absent/disabled features (`fingerbot`, `ocr`) which still pushed a
  status once — hence `atx` is polled on the slow HTTP tier as a fallback
  instead of relying on WS push alone.
- ATX write routes (`atx/click`, `atx/power`) return HTTP 500 with a
  plain-text body (not the usual JSON envelope) when no ATX board is
  attached — handled as a generic `CometApiError`, not JSON-parsed.
- `/api/upgrade/reboot` and `/api/upgrade/reset_default` are **GET**
  handlers in glkvm that execute for real on GET, with no method gating.
  Never probe them; `tools/dump_api.py` hard-refuses them via
  `NEVER_TOUCH` regardless of flags. `api.py`'s `reboot()` uses GET
  deliberately (POST 405s there) — it's only ever invoked by the user
  pressing the Reboot button.
- On serial-less firmware, `_unique_id_from_info` falls back to `host` as
  the unique id — so `config_flow.py`'s reconfigure step migrates that id
  when the host changes (comparing the entry's pre-update `unique_id` to
  its pre-update `host`, since a host-derived id was never a real device
  identity) instead of aborting with `unique_id_mismatch`. The same branch
  also fires when the new host turns out to be a genuinely different device
  that reports a serial — a host-fallback entry then migrates straight to
  that device's serial-derived id; accepted for the same reason, since a
  host-derived id never carried real device identity in the first place.
  Deliberately skips `_abort_if_unique_id_configured` on that path —
  pointing a host-fallback entry at an already-configured serial is an
  accepted, unhandled edge case.

## Conventions

- GitHub identity: `metril`. Commit messages are plain, imperative,
  present-tense summaries ("Add X", "Fix Y") — no AI/assistant mentions, no
  Co-Authored-By trailers.
- Dev tooling via `uv` (`uv venv`, `uv run pytest -q`,
  `uv run python tools/dump_api.py`).
- `api.py` is a clean-room implementation against the documented kvmd-style
  contract (endpoints/shapes only) — no code copied from GL.iNet's GPL
  `glkvm`/`glkvm-comet` sources; this integration itself ships under MIT.
- Tests run offline against fixtures/stubs in `tests/` (no device, no real
  Home Assistant). CI's `import-smoke` job is the only place platform
  modules are imported against a real `homeassistant` package.

## Release process

Bump `manifest.json`'s `version` in the commit that ships the release, tag
`vX.Y.Z` to match, and cut a GitHub release — `release.yml` re-derives the
version from the tag and zips `custom_components/glinet_comet` as the asset.

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
`upgrade/reboot` incident (`gpio/pulse` is from the later 2026-08-29 run):

| route | status | route | status |
|---|---|---|---|
| `atx/click` | 405 | `hid/print` | 405 |
| `hid/events/send_shortcut` | 405 | `gpio/switch` | 405 |
| `hid/set_params` | 405 | `upgrade/reboot` | **200 — reboots; NEVER_TOUCH, not probed again** |
| `msd/set_connected` | 405 | `gpio/pulse` | 405 |

Snapshot: `GET /api/streamer/snapshot?allow_offline=1` → **200** JPEG
(`ffd8ffe0` magic) in one run; **503** JSON error body in another, at the
same time `streamer.streamer` was `null` — the streamer subsystem isn't
always up; consumers must handle both. Login token was found at
`result.token` (confirmed across both runs).

## Still unverified

- Whether `atx` ever emits a WS event on this hardware (0/2 runs so far).
- `custom_screen/status` with hardware present; `gpio.model.scheme` with real channels (both empty/absent on this unit).
- Actual write behavior of `atx/click`, `hid/*`, `msd/set_connected`, `gpio/switch`/`gpio/pulse` (no GPIO channels configured on this unit; GET-probed only, per the read-only mandate — see NEVER_TOUCH above for why POST-probing isn't done at all).
