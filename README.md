# broadsheet

> *Home Assistant, rendered as a magazine.*

A front-end for Home Assistant shaped like a publication. Italic
display serif. Newsreader body. Pages, not screens. Prose, not specs.

Adapts to whatever you already have in HA — areas, lights, climates,
locks, media players, sensors. No `house.yaml` to write before it
works. Install, point at your HA, see your house.

> **🛠 Early-tester soak.** broadsheet is in a small private soak —
> a handful of trusted HA users running it on real installs while it
> bakes. If you're here, please skim
> [docs/EARLY-TESTERS.md](https://github.com/alfiedennen/broadsheet/blob/main/docs/EARLY-TESTERS.md)
> before you dig in. Bug reports go to
> [Issues](https://github.com/alfiedennen/broadsheet/issues);
> conversation lives in
> [Discussions](https://github.com/alfiedennen/broadsheet-addon/discussions).

---

## Install

broadsheet ships as a **Home Assistant add-on**. One install path,
zero credentials to handle.

### Requirements

- Home Assistant **OS** or **Supervised** (the kinds that have the
  add-on store). HA Container / HA Core support is on the roadmap.
- HA 2024.4 or newer.
- **amd64 is the tested, supported architecture.** The `aarch64`
  image builds in CI and is published, but hasn't yet been verified
  on real ARM hardware — treat it as experimental for v0.1.

### Two-minute install

1. Add this repository to your HA add-on store:

   [![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Falfiedennen%2Fbroadsheet-addon)

   Or manually: **Settings → Add-ons → Add-on Store → ⋮ menu →
   Repositories → paste**:
   ```
   https://github.com/alfiedennen/broadsheet-addon
   ```

2. Find **broadsheet** in the store, click **Install**.
3. Click **Start**, then **Open Web UI**.

That's it. No long-lived access token, no `.env` to write. The
add-on receives credentials from HA's Supervisor automatically;
broadsheet appears in your sidebar and stays there.

By default broadsheet can control your house (lights, climate,
scenes, etc.). Set the add-on's `read_only` option to `true` to
make it a read-only viewer. `lock.*` writes are hard-banned
regardless of that setting.

---

## What you get

### Eight pages, each shaped for what it's for

Every page **discovers what's relevant from your HA at boot** — a
new room added in HA tonight is on the right pages tomorrow, no
editing.

| Page | What it is |
|---|---|
| **`/`** the moment | Live painting (or ambient gradient) of who's home + a single-line manifest of the day |
| **`/lights`** | Prose state ("library and office are on"), scene chips, per-area reveal with sliders + per-bulb sub-reveal |
| **`/heat`** | Three macros (Boost / All warm / All frost), per-room TRV reveal with ±0.5° nudges |
| **`/door`** | Lock-state hero + one Unlock action, paired camera image below |
| **`/tv`** | Remote + a launch button per app the TV exposes. With the `tmdb-tv` plugin: Trending / New content rows |
| **`/body`** | Health-data panels with honest sub-labels — empty panels explain themselves |
| **`/wall`** | A deliberately dense action grid for a hallway tablet |
| **`/settings`** | In-app curation: House, People, Voice, Plugins, Integrations, Add-ons, Devices, Logs — broadsheet-native UIs over HA's WS APIs |

### Three first-class plugins (bundled, opt-in via `/settings/plugins`)

- **emanations** — multi-person presence painting. Live imagery of
  who's in which room of your house.
- **ghost-cloud** — *The Long Take*. 24-hour radar event playback
  as a translucent water-membrane time-tube (Three.js + WebGL2 +
  pentatonic water-drop synth). Ships with bundled demo data.
- **tmdb-tv** — Trending + New content rows on `/tv`, droppable as
  a block on any wall surface. Needs a free TMDB API key.

### When translation isn't enough: the `lovelace-embed` escape hatch

For dashboards built with card-mod / mushroom / custom HACS
components that broadsheet's static translator can't reproduce, the
`lovelace-embed` block iframes the source dashboard chrome-free.
Perfect fidelity to whatever you already built. Same-origin proxy
inside the add-on bypasses `X-Frame-Options`; auth is auto-injected;
HA's sidebar + header are hidden via CSS. Paste a path, get a clean
embed.

### One frontend, not two

On install broadsheet **takes over the HA frontend** — the HA
sidebar collapses, broadsheet becomes your landing surface, and the
eight to ten settings broadsheet renders natively read like prose,
not a config tree. A single "Open HA settings" affordance in the
kebab nav drops you into HA's own UI for the unusual flows. HA
stays whole; broadsheet just stops being a peer panel.

---

## Architecture

- Pure-static SvelteKit 2 + Svelte 5 runes SPA bundled inside an
  nginx container
- Supervisor token auto-injected at the nginx layer — the SPA never
  sees a token
- Sidecar Python service for `broadsheet.json` curation persistence
- Multi-arch images (amd64 + aarch64) published via GitHub Actions
  to GHCR
- Same-origin reverse proxy routes `/api/*` and `/api/websocket` to
  HA Core via the Supervisor; the `lovelace-embed` block uses the
  same plumbing for its iframe

---

## Source

- **Add-on packaging** (this repo): `alfiedennen/broadsheet-addon`
  — Dockerfile, nginx config, sidecar, CI.
- **SPA + plugin packages + docs**:
  [`alfiedennen/broadsheet`](https://github.com/alfiedennen/broadsheet)
  — the actual app code, the design/architecture docs, the renderer
  contract for plugin authors.

Both repos are MIT licensed.

---

## License

MIT. See [`LICENSE`](LICENSE).
