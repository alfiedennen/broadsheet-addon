# broadsheet

> *Home Assistant, rendered as a magazine.*

An editorial frontend for Home Assistant. Italic display serif. Pages,
not screens. Prose, not specs.

## Install

You're reading this because you've already added the repository.
Just click **Install**, then **Start**, then **Open Web UI** — or
click **broadsheet** in your sidebar.

No long-lived access token to paste. No `.env` to fill in. The add-on
receives credentials from Supervisor automatically.

## What you get

Eight pages that adapt to whatever your HA install already has:

- **`/`** the moment — landing manifest with procedural ambient
  gradient + presence dots
- **`/lights`** — prose state ("library and office are on"), scene
  chips, per-area reveal with light controls
- **`/heat`** — three macros (Boost / All warm / All frost), per-room
  TRV nudges
- **`/door`** — lock state hero + Unlock action + paired camera
- **`/tv`** — D-pad remote + power + content slot
- **`/body`** — Health Connect panels (Pixel sensors)
- **`/wall`** — dense action grid for hallway tablets
- **`/settings`** — opinionated curation: areas, devices, entities,
  people. With smart auto-hide for system noise + duplicates +
  iBeacon environmental advertisements.

## Configuration

| Option | Default | Notes |
|---|---|---|
| `log_level` | `info` | broadsheet's own logging verbosity |
| `curation_path` | `/data/broadsheet.json` | Where your settings persist |
| `region` | `GB` | For TMDB streamer-provider filtering (TMDB plugin only) |
| `tmdb_api_key` | `""` | Optional; only needed for the @broadsheet/tmdb-tv plugin |

Most users won't need to change any of these.

## How curation persists

Your renames, hides, voice overrides, and per-person presence sensor
picks all live in `/data/broadsheet.json` — the file is in HA's
backup map, so it travels with your snapshots.

To export your curation: open broadsheet → Settings → About →
"Export current config" (or just copy `/data/broadsheet.json` from
your HA host).

To reset: Settings → About → "Reset all curation to discovery
defaults" (with confirmation).

## Support

Issues: https://github.com/alfiedennen/broadsheet/issues

Source code:
- Add-on packaging: https://github.com/alfiedennen/broadsheet-addon
- SPA + curation logic: https://github.com/alfiedennen/broadsheet
