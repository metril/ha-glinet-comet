# Fixtures

`state_live.json` is assembled from the redacted live dump in `comet_api_dump.json` (fw V1.9.1 release1, model RM1PE, `streamer.streamer` null run).
`state_streamer_active.json` is the same, with the streamer-active shape from `CLAUDE.md`'s first run, two mounted `msd.storage.images`, and a newer `upgrade_compare.server_version` substituted in.
`state_gpio.json` is `state_live.json` with synthetic `gpio`/`gpio_model`/`gpio_labels` values substituted in: two inputs (`in_1` online, `in_2` offline) and four outputs (`out_switch` switch-capable, `out_pulse` pulse-only, `out_pulse_disabled` pulse present but `max_delay: 0` -- pulse disabled, `out_none` neither) — no live device exposed real GPIO channels to capture.
All identifying values are `<redacted>` placeholders.
`state_live_rm4pe.json` is `state_live.json` for a Comet X (GL-RM4PE, fw V1.10.1 release4, 2026-10-24 dump): `glinet.upgrade_*` say RM4PE / V1.10.1 release4, `info.health` is the live WS `health` section, `hid.mouse.outputs` lists four outputs, and `switch`/`switch_model` are the first WS `switch` frame split the way the coordinator splits it (`model` -> `switch_model`; `edids`/`colors`/`beacons` dropped). Four ports, no target attached, so the link and ATX LED arrays are all `false`/`[]`.
