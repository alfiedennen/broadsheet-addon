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

Plus the **things-first custom page builder** (added 0.9.1+) at
**Settings → Pages → + New page**. Browse the controllable things
in your house — grouped by room then by Lights / TV / Heating / …
— and tap or drag to add them to a wall surface. Composed recipes
("Living Room lights — panel", "Living Room media — panel") drop
single composite blocks; one-tap macros across N entities; plugins
contribute their own blocks (TMDB rows next to a TV remote when
`@broadsheet/tmdb-tv` is enabled).

For wall tablets, pick a **Wall device** preset (Fire HD 10,
Galaxy Tab A9, iPad, Pixel Tablet, etc.) in page meta — the editor
previews at the device's native dimensions + offers a copyable
**Kiosk URL** with Fully Kiosk Browser hints.

Full walkthrough:
[WALL-BUILDER-GUIDE](https://github.com/alfiedennen/broadsheet/blob/main/docs/WALL-BUILDER-GUIDE.md)
+
[CUSTOM-PAGES-GUIDE](https://github.com/alfiedennen/broadsheet/blob/main/docs/CUSTOM-PAGES-GUIDE.md).

## Configuration

| Option | Default | Notes |
|---|---|---|
| `log_level` | `info` | broadsheet's own logging verbosity |
| `curation_path` | `/data/broadsheet.json` | Where your settings persist |
| `region` | `GB` | For TMDB streamer-provider filtering (TMDB plugin only) |
| `tmdb_api_key` | `""` | Optional; only needed for the @broadsheet/tmdb-tv plugin |
| `read_only` | `false` | `false` = broadsheet can control your house. `true` = look-but-don't-touch viewer. |

Most users won't need to change any of these.

### `read_only`

Off by default — broadsheet *is* your dashboard, so it can turn lights
on, set the heating, run scenes, drive the TV. Flip it on if you want
broadsheet purely as an ambient display with no control.

One thing is **always** off-limits from broadsheet regardless of this
setting: **door locks**. broadsheet will never call `lock.lock` /
`lock.unlock` — a UI bug there is too expensive. Lock state is shown;
lock *control* stays in HA proper.

## The broadsheet look, applied to Home Assistant itself

On first start, broadsheet drops a Home Assistant **theme** into your
`/config/themes/` directory — `broadsheet.yaml`. It restyles HA's own
chrome (sidebar, header, the config pages broadsheet doesn't replace)
into the same warm editorial register as the broadsheet panel itself.

The point: when you step out of broadsheet into HA's native
automation editor or integrations page, it shouldn't feel like a
context switch.

**It is entirely opt-in.** Installing the add-on changes nothing
about how HA looks. To turn it on:

> **Settings → (your profile, bottom-left) → Theme → broadsheet**

To revert: same place, pick any other theme. You can then delete
`/config/themes/broadsheet.yaml` if you want it gone.

**Theme updates + your own edits.** The theme file carries a
`# broadsheet-theme-version:` marker line. While that line is present,
broadsheet treats the file as its own and will refresh it on add-on
updates (so theme *fixes* reach you). The moment you want to make the
theme yours — delete that one marker line. broadsheet then never
touches the file again; it's entirely yours to edit.

### Optional: the full font register

The theme falls back to system serifs (Iowan / Georgia) out of the
box, which keeps the serif-forward feel. For the exact four-font
editorial register — Instrument Serif, Newsreader, IBM Plex Sans,
JetBrains Mono — add this to your `configuration.yaml`:

```yaml
frontend:
  extra_module_url:
    - /local/broadsheet-fonts.js
```

…and copy `broadsheet-fonts.js` (shipped alongside the theme, in the
add-on's `/usr/share/broadsheet/theme/`) into your `/config/www/`
folder. This step is optional and the theme works well without it.

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
