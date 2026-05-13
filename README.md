# broadsheet — Home Assistant add-on repository

> *Home Assistant, rendered as a magazine.*

This is the Home Assistant add-on packaging for
[broadsheet](https://github.com/alfiedennen/broadsheet) — an editorial
frontend that adapts to whatever your Home Assistant install already
has, with no `lovelace.yaml` required.

## Installing

1. Add this repository to your Home Assistant add-on store:

   [![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Falfiedennen%2Fbroadsheet-addon)

   Or manually: **Settings → Add-ons → Add-on Store → ⋮ menu →
   Repositories → paste**:
   ```
   https://github.com/alfiedennen/broadsheet-addon
   ```

2. Find **broadsheet** in the add-on store, click **Install**.

3. Click **Start**, then **Open Web UI**.

That's it. No long-lived access token to paste, no config to write.
The add-on receives credentials from Home Assistant's Supervisor
automatically; broadsheet appears in your sidebar and stays there.

## Requirements

- Home Assistant **OS** or **Supervised** (the kinds that have the
  add-on store). HA Container and HA Core support is on the roadmap.
- HA 2024.4 or newer.

## What you get

Eight pages that adapt to your HA install:

- `/` — landing manifest with a procedural ambient gradient + presence
- `/lights` — prose state, scenes, per-area light reveal
- `/heat` — three macros (Boost / All warm / All frost), per-room TRVs
- `/door` — lock state hero + Unlock action + paired camera
- `/tv` — D-pad remote + content slot
- `/body` — Health Connect panels (Pixel + Apple Health roadmap)
- `/wall` — dense action grid for hallway tablets
- `/settings` — opinionated curation for your areas, devices,
  entities, and people

## Architecture

- Pure-static SvelteKit SPA bundled inside an nginx container
- Supervisor token auto-injected at the nginx layer — the SPA never
  sees a token
- Sidecar Python service for `broadsheet.json` curation persistence
- Multi-arch images (amd64 + aarch64) published via GitHub Actions
- Same-origin reverse proxy routes `/api/*` and `/api/websocket` to
  HA Core via the Supervisor

## Source

Add-on packaging: this repo (`alfiedennen/broadsheet-addon`).
SPA + curation logic: [`alfiedennen/broadsheet`](https://github.com/alfiedennen/broadsheet).

## License

MIT.
