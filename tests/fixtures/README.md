# Fixtures

`state_live.json` is assembled from the redacted live dump in `comet_api_dump.json` (fw V1.9.1 release1, model RM1PE, `streamer.streamer` null run).
`state_streamer_active.json` is the same, with the streamer-active shape from `CLAUDE.md`'s first run, two mounted `msd.storage.images`, and a newer `upgrade_compare.server_version` substituted in.
`state_gpio.json` is `state_live.json` with synthetic `gpio`/`gpio_model`/`gpio_labels` values substituted in: two inputs (`in_1` online, `in_2` offline) and three outputs (`out_switch` switch-capable, `out_pulse` pulse-only, `out_none` neither) — no live device exposed real GPIO channels to capture.
All identifying values are `<redacted>` placeholders.
