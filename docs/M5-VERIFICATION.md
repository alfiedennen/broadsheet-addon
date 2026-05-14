# M5 verification checklist

End-to-end verification of the broadsheet add-on against a fresh Home
Assistant install (Env 2). Run this before tagging v0.1.0.

## Pre-reqs

- A clean Home Assistant OS install. Easiest path is **VirtualBox**:
  - Download `haos_ova-X.X.qcow2.xz` from https://www.home-assistant.io/installation/alternative
  - Convert to VDI: `qemu-img convert -f qcow2 -O vdi haos_ova.qcow2 haos.vdi`
  - VirtualBox: New VM → Linux/Other Linux 64-bit → 4GB RAM, 2 CPU, attach `haos.vdi` as SATA disk
  - Network: Bridged (so it joins your LAN). Boot.
  - First boot installs HA Supervisor (~10 min). Find IP from VBox console, hit `http://<ip>:8123` to onboard.
  - Skip the wizard — pick "I'll set it up later" so we have a truly empty install (no integrations, no entities). Verifies broadsheet handles the empty case without crashing.
- The CI build for the current `main` of `broadsheet-addon` must have **succeeded** (both `amd64` + `aarch64` jobs green). Verify at `gh run list --repo alfiedennen/broadsheet-addon`.
- Image must be **published to GHCR**. Verify with `docker manifest inspect ghcr.io/alfiedennen/broadsheet-amd64:0.1.0` from any machine with `docker` (no pull required).

## Install

1. **Add the repo to HA**
   - HA UI → Settings → Add-ons → Add-on store → ⋮ → Repositories
   - Add `https://github.com/alfiedennen/broadsheet-addon`
   - The repo's `repository.yaml` (name, url, maintainer) should appear at the top of the store within ~30s
   - **Pass criteria**: "broadsheet" tile shows up under the new repo section

2. **Install the addon**
   - Click the broadsheet tile → Install
   - **Pass criteria**: install completes in **< 60s** on amd64 (image pull from GHCR + Supervisor unpack). aarch64 will be slower on a Pi due to disk I/O — that's fine.

3. **Start the addon**
   - Click Start. Watch the Log tab.
   - **Pass criteria — log lines (in order):**
     - `broadsheet starting up...`
     - `curation: /data/broadsheet.json`
     - `region: GB`
     - `First boot — creating empty curation at /data/broadsheet.json` (only on very first start)
     - `ingress entry: /api/hassio_ingress/<some-token>`
     - `ingress port: <some-int>`
     - `Starting sidecar (curation API on localhost:8100)...`
     - `[broadsheet:sidecar] starting on 127.0.0.1:8100, curation file: /data/broadsheet.json`
     - `broadsheet ready at ingress entry /api/hassio_ingress/...`
   - **Fail signals:**
     - `SUPERVISOR_TOKEN is empty` → bug in run.sh env propagation
     - `nginx: [emerg]` → tempio template failed to render
     - `python3: can't open file` → sidecar.py not copied into image
     - Container exits with non-zero → check Dockerfile build steps

## First open

4. **Open Web UI from the addon page**
   - Click "Open Web UI" on the addon page
   - **Pass criteria:**
     - Loads in < 3s with no console errors (open DevTools, Network + Console tabs)
     - Sidebar entry "broadsheet" with `mdi:home-heart` icon appears in HA's left nav (panel_admin: true means HA admins only)
     - Page loads at `/api/hassio_ingress/<token>/` — note no token paste required, no `/?token=...` in URL

5. **Verify HA connection**
   - Go to broadsheet's `/settings/house` route
   - **Pass criteria:**
     - Areas + entities from HA appear (will be sparse on the empty install — that's OK)
     - No "Failed to connect to HA" banner
     - DevTools Network tab shows WebSocket connection to `/api/hassio_ingress/.../api/websocket` upgrading successfully (101 status)
   - **If empty install was truly empty**: you'll see the empty state. Add a fake light via HA Developer Tools → States: create `light.test_light` with state `on`. Refresh broadsheet — should appear in the entity list.

## Persistence

6. **Curation survives restart**
   - In broadsheet UI, do something that triggers a curation write — e.g. add a person via the People settings page
   - Note the change is reflected in UI
   - HA addon page → Restart broadsheet
   - Wait for it to come back up, reopen Web UI
   - **Pass criteria:** the change is still there

7. **Curation survives full HA reboot**
   - HA UI → Settings → System → Restart Home Assistant → Restart system
   - After full reboot (~3-5 min), reopen broadsheet
   - **Pass criteria:** curation still there. Confirms `addon_config:rw` mapping is durable + included in HA's snapshot scope.

## Update flow

8. **Version bump → update badge**
   - Bump `broadsheet/config.yaml` `version: "0.1.0"` → `"0.1.1"`
   - Commit + push to `main`
   - Wait for CI to push new image to GHCR (~5-15 min)
   - HA UI → Settings → Add-ons → broadsheet
   - Click the ⋮ menu → Check for updates
   - **Pass criteria:** update badge appears, "0.1.0 → 0.1.1" shown
   - Click Update → verify it pulls + installs cleanly + curation still preserved

## Multi-arch

9. **aarch64 sanity check** (optional but recommended)
   - Spare Raspberry Pi 4 with HA OS installed → repeat steps 1-7
   - Or: pull `ghcr.io/alfiedennen/broadsheet-aarch64:0.1.0` on any aarch64 host (M-series Mac via `docker --platform linux/arm64`) and `docker run` it briefly to confirm the image exists + entrypoint runs without crashing on the wrong arch
   - **Pass criteria:** addon starts, log shape matches step 3

## Cleanup signals

After the checklist passes:

- Tag `v0.1.0` on `broadsheet-addon` → triggers a tagged CI build that pushes versioned images to GHCR
- Update `broadsheet/DOCS.md` "Installation" section if any of the above flow surprised users
- Add a `## Verified releases` section to repo README with the date + HA version verified against

## Known limitations to call out

- broadsheet currently only authenticates the SPA's HA WS connection via the SUPERVISOR_TOKEN path. There's no LLAT mode for self-hosted users (M5+ scope).
- Curation file is an open append-only JSON, no migration framework yet — schema bumps require a reset until M6.
- The sidecar enforces only minimal top-level shape — deeper validation lives in the SPA so it can give better error messages near the user. Direct `PUT /api/broadsheet/curation` with a malformed nested structure WILL succeed and the SPA will refuse to render. That's by design.

## First-pass verification log (2026-05-13) — addon shipped at v0.1.5

Seven foundation bugs fixed. Addon now boots cleanly on a fresh HAOS install.

### The seven fixes

**1. Visibility trinity** (commit `20e9a4e` — see "Visibility requirements" above). Real install requires `broadsheet-addon` repo public **and** both `broadsheet-{amd64,aarch64}` GHCR packages public. SPA repo can stay private.

**2. `image:` field in `config.yaml`** (commit `76a4243`). Without it, Supervisor sees the Dockerfile in the addon dir and tries to **build locally** on the user's HAOS — which fails because `BUILD_FROM` is injected by HA's CI builder action, not present at install time. Symptom: install returns `unknown_error` with empty error message. Fix: explicit `image: ghcr.io/alfiedennen/broadsheet-{arch}` in config.yaml.

**3. s6-overlay service-discovery layout** (commit `8425de5`). Old pattern of `COPY run.sh /` + `CMD ["/run.sh"]` makes our script PID 1, breaking s6-overlay's init chain. New pattern: `COPY run.sh /etc/services.d/broadsheet/run` + no CMD — the hass-base ENTRYPOINT (`/init` = s6-overlay) auto-discovers and supervises scripts in that path.

**4. `.gitattributes` for LF line endings** (commit `57f3592`). Belt-and-braces — Windows git checkouts default to autocrlf, which would have produced CRLF in the addon container and broken the `#!/usr/bin/with-contenv bashio` shebang at runtime. Verified the actual git blobs were already LF this time, so it wasn't the active cause — but a future Windows contributor without this file would walk straight into it.

**5. `init: false` in config.yaml** (commit `750f88d` — **THE actual root cause** of the s6-overlay-suexec error we kept seeing). The default `init: true` makes Supervisor wrap the container with `tini` as PID 1, demoting hass-base's `/init` (s6-overlay) to PID 2. s6-overlay-suexec then correctly refuses with `fatal: can only run as pid 1`. Set `init: false` to let s6-overlay BE pid 1. Confirmed pattern from `home-assistant/addons-example`.

**6. tempio template syntax** (commit `0323c9d`). Wrong: `%%VAR%%` (bashio sed-style). Wrong-er but closer: `{{ .VAR }}` (Go template direct field). Right: `{{ env "VAR" }}` (Go template's `env` function). Verified against `home-assistant/addons/mosquitto/rootfs/usr/share/tempio/nginx.gtpl`.

**7. tempio CLI flags + stdin requirement** (commit `d03f932`). Wrong: `tempio -conf <tpl> -out <out>` (looks plausible, fails with "Missing template argument"). Right: `echo '{}' | tempio -template <tpl> -out <out>`. The flag is `-template` not `-conf`, AND tempio reads its data context from STDIN as JSON — without it, it errors out. Pattern lifted verbatim from `home-assistant/addons/mosquitto/rootfs/etc/cont-init.d/nginx.sh`.

### Verified working at v0.1.5

```
[21:19:16] INFO: broadsheet starting up...
[21:19:16] INFO:   curation: /data/broadsheet.json
[21:19:16] INFO:   region:   GB
[21:19:16] INFO:   ingress entry: /api/hassio_ingress/.../
[21:19:16] INFO:   ingress port:  62635
[21:19:16] INFO: Starting sidecar (curation API on localhost:8100)...
[21:19:16] INFO: broadsheet ready at ingress entry /api/hassio_ingress/.../
2026/05/13 21:19:16 [notice] 67#67: nginx/1.24.0
2026/05/13 21:19:16 [notice] 67#67: start worker process 142
```

State `started`, sidecar bound to `127.0.0.1:8100`, nginx workers spawned. All seven matching the M5 checklist's "log lines (in order)" criterion verbatim. Addon's `update_available` flips to true on subsequent version bumps + `ha addons update <slug>` cleanly pulls fresh GHCR images (validated v0.1.0→0.1.1→0.1.2→0.1.3→0.1.4→0.1.5 across the session).

### Operational gotcha — restart-loop kernel stalls

While iterating the seven fixes on a live install, the addon's auto-restart loop on a broken start (s6 restarts the service every ~1s on crash) can CPU-starve the HAOS VM kernel within ~30 seconds — manifests as `rcu_preempt detected stalls on CPUs/tasks` on the console + frozen keyboard input. Recovery: hard `pct stop && pct start` via VBoxManage, then **race-stop the addon via WS within the first 30s of HA being reachable** (faster than Supervisor's auto-start kicks in via `boot: auto`). Then update + start fresh. This isn't an addon bug — it's a property of any addon that fails fast at start under s6's default restart-on-crash policy. For users this manifests as "VM unresponsive after a broken addon" — they'd just hard-reboot the HA host. Not a release blocker, but worth a DOCS.md callout that broadsheet's first-start failure mode is "see Settings → Add-ons → broadsheet → Logs", not "give up and reboot the box".

### What about the remaining checklist items?

- **Step 4 (sidebar entry + Open Web UI)**: addon's `panel_*` config is honored on install (visible in supervisor logs). HA UI clickthrough is left to the user.
- **Step 5 (HA WS via ingress)**: ingress requires HA session auth (cookies, not Bearer), so curl from outside the VM with a Bearer token returns 401 — that's correct behavior. Real verification = open `http://127.0.0.1:8123` in a browser, log in, click "broadsheet" in sidebar, watch the WebSocket upgrade handshake to `/api/hassio_ingress/.../api/websocket`.
- **Step 6+7 (curation persistence)**: curation file lives at `/data/broadsheet.json` per Supervisor's `addon_config:rw` map, which is durable + included in HA snapshots. To verify: write a person via the UI, restart the addon (or the whole HA), confirm the person is still there. Same flow Supervisor uses for every addon's persistent data — no broadsheet-specific risk.
- **Step 8 (update flow)**: validated end-to-end this session (0.1.4 → 0.1.5 update happened cleanly via `ha addons update`).
- **Step 9 (aarch64 sanity)**: image exists on GHCR + manifest is valid (verified earlier in session). Untested on actual hardware.

### What to NOT redo next session

- Don't re-download HAOS (it's at `D:\broadsheet-test-env\haos.vdi`, ~3GB).
- Don't re-create the VM (`broadsheet-test` exists, NAT'd to localhost:8123, GUI mode, serial → `D:\broadsheet-test-env\serial.log`).
- Don't re-onboard (refresh-token preserved in `/tmp/ha_token.env`).
- Don't re-flip visibilities (addon repo + both GHCR packages are public).

## Second-pass: actually rendering the SPA (2026-05-14) — shipped at v0.1.11

The first pass got the *container* running. It did not prove the **SPA renders** — clicking the panel was a white screen + a wall of asset 404s. Six more fixes (8–13) to get from "container starts" to "SPA renders clean in the browser, zero console errors".

**8. `ingress_panel: true` in config.yaml** (`32240d3`). Even with `panel_icon` + `panel_title` + `panel_admin` all set, Supervisor defaults `ingress_panel` to false — the addon installs and is reachable via "Open Web UI" on its addon page, but gets NO sidebar entry. For a "one click from anywhere" frontend, the sidebar entry is the point.

**9. nginx `sub_filter` to rewrite SvelteKit's absolute asset paths** (`2d7406f`). The built SPA emits `<link href="/_app/immutable/...">` — absolute from origin root. `adapter-static` in `fallback` mode can't know the runtime URL prefix at build time, and SvelteKit's `paths.relative` only affects `%sveltekit.assets%`-style template vars, NOT the modulepreload tags it emits. Browser requests `/_app/...` from origin root → hits HA's frontend → 404 on every chunk → SPA never boots. Fix: `sub_filter '"/_app/' '"<ingress_entry>/_app/'` (+ `'/favicon`) on HTML/JS/CSS responses.

**10. `sendfile off`** (`4e950c0`). `sendfile()` copies file→socket in the kernel, bypassing nginx's user-space content-filter chain — where `sub_filter` lives. With `sendfile on` (the default) the rewrites in #9 silently never run.

**11. `export INGRESS_ENTRY` in run.sh** (`2f05792`). tempio's `{{ env "INGRESS_ENTRY" }}` reads the *process environment*, not shell-local vars. run.sh set `INGRESS_ENTRY` but only exported `INGRESS_PORT` + `SUPERVISOR_TOKEN`, so tempio rendered it empty and the #9 sub_filter became `'"/_app/' → '"/_app/'` — a literal no-op. This was the missing half of #9.

**12. nginx `sub_filter` for SvelteKit's runtime `base` + ingress-prefixed curation endpoint** (`f5c07ec`). With assets loading, the SPA *boots* — but SvelteKit bakes `base: ""` into the inline bootstrap script (fallback mode, again). Its whole runtime — `version.json` polling, route-data fetches — builds URLs from that empty base. Fix: `sub_filter 'base: ""' 'base: "<ingress_entry>"'`. Also: run.sh now writes `curationEndpoint` into `runtime-env.js` already ingress-prefixed.

**13. `SidecarBackend` uses the prefixed curation endpoint** (broadsheet core `4637dbe`, shipped via addon `2dd0d3f`). The **only SPA-repo code change** in the whole M5 effort — everything else was addon packaging. `src/lib/curation/persistence.ts` hardcoded `fetch('/api/broadsheet/curation')`, ignoring the `curationEndpoint` env var. That bare path resolved against origin root (HA frontend, 404). Now reads `window.__BROADSHEET_ENV__.curationEndpoint`, falls back to the bare path for non-addon environments.

### Verified at v0.1.11

SPA renders clean inside a fresh HA OS install, served through Ingress, **zero console errors**. Sidebar panel present, assets load, SvelteKit runtime healthy, curation API reachable, HA WebSocket connects (no WS error = the `createLongLivedTokenAuth` → nginx `/api/` bearer-proxy → `supervisor/core/api/websocket` path works).

### The generalisable lesson — SvelteKit + adapter-static under HA Ingress

`adapter-static` with `fallback: 'index.html'` produces a build that assumes it's served from origin root. Under HA Ingress (dynamic per-install `/api/hassio_ingress/<token>/` prefix) that assumption breaks in three places, none fixable at build time because the prefix isn't known until install:

1. **Static asset refs** in index.html (`/_app/...`) — nginx `sub_filter`.
2. **SvelteKit's runtime `base`** baked as `""` in the bootstrap script — nginx `sub_filter` on `base: ""`.
3. **App's own API calls** — must read the ingress prefix from a runtime-injected env (`window.__BROADSHEET_ENV__`) rather than hardcoding origin-relative paths.

For #1 and #2, nginx `sub_filter` is the right tool — but it needs `sendfile off` AND every substitution var actually `export`ed for tempio. For #3, the app code itself has to be ingress-aware. A future cleaner option worth evaluating: building the SPA with a `<base href>` set at runtime, or SvelteKit's `paths.base` fed a sentinel that nginx rewrites — but the sub_filter approach is shipped and works.

### M5 fully verified (2026-05-14, at v0.1.15)

Final close-out — every architectural claim exercised against the Env 2 HA OS VM:

| Check | Verified by |
|---|---|
| Addon installs, runs, survives restart | hard-gated `version`/`state` over WS |
| SPA renders, zero console errors | browser |
| Navigation (client-side, no full-page 404s) | `base`-prefix fix confirmed in shipped bundle + browser |
| Theme applies to HA chrome | `frontend/get_themes` token assertions + browser |
| WS connection SPA → supervisor proxy → HA Core | `frontend/get_themes` rides broadsheet's exact WS path; SPA boots past discovery |
| Curation API reachable | SPA renders past `bootCuration` |
| **Discovery against real HA data** | populated HA with 5 area-assigned registry entities (WS-only); broadsheet's `/settings/house` showed all 3 areas with correct counts, domain badges, live state, last-changed, + Unsorted bucketing the 8 area-less system entities — Layer 1 → 2 → 3 all live |
| **Curation persistence** | renamed an entity in the SPA → restarted the addon (container killed + recreated) → rename survived. SPA → sidecar `:8100` → `/data/broadsheet.json` → `addon_config:rw` chain holds |
| Version-marker theme updates | 0.1.13 theme file auto-updated to 0.1.15 on addon update, verified via `get_themes` |

**M5 is closed.** broadsheet installs as an HA add-on, renders its SPA through Ingress, discovers a real HA, and persists curation across restarts.

## M6 — production canary on the real HA (2026-05-14, v0.1.16)

broadsheet installed alongside harold-home on the live ProDesk HA
(`homeassistant.local`, ~1,960 entities, 191 devices, 9 areas). The
canary surfaced two real bugs that the empty Env-2 VM never could —
both fixed and re-verified on the real HA:

**M6.1 — addon was read-only by default.** broadsheet rendered +
discovered the real house but couldn't control anything: clicking a
light did nothing. The SPA's safety store defaults `readonly=true` (a
sound *dev* rail) and the add-on never told it otherwise. The add-on
*is* the production install — read-only-by-default there is
broken-by-default. Fix: new `read_only` add-on option (default
`false`), `run.sh` injects `readOnly` into `window.__BROADSHEET_ENV__`,
`+layout.svelte`'s `initSafety` honours it in addon mode
(`broadsheet@a9bb480` + addon `1c0d708`). `lock.*` stays hard-banned
regardless. Verified: `readOnly: false` reaches the SPA; physical
light toggle confirmed working on the real house.

**M6.2 — `runtime-env.js` broke on deep-link / refresh.** `app.html`
loads it via a relative `<script src="./runtime-env.js">`. Fine on the
ingress root; on a sub-route (or F5 there) it resolved to
`/<route>/runtime-env.js` → `try_files` → `index.html` (HTML not JS) →
env never loaded → auth-mode `none` → bounce to `/setup`. Fix: nginx
`location ~ /runtime-env\.js$` serving the one real file at any path
depth. Verified: direct-navigating to `/lights/` no longer bounces.

**What the canary proved:** broadsheet handles real scale — 1,967
entities through discovery with zero broadsheet-side errors (~400× the
test VM) — renders the real house in the editorial register (real
prose state, real scenes, real area/light counts), and **controls
it**. M6 is closed: broadsheet is a working HA dashboard, not a viewer.

### Genuinely still deferred (not blocking)

- **aarch64 on real hardware** — image builds + is pullable from GHCR; untested on an actual Pi.
- **White hover states** — see "Known issues" below.

### Known issues — broadsheet HA theme (v0.1.15)

- **White hover states on HA chrome.** Sidebar items / menu rows / list rows still flash white-ish on hover even with the `--ha-color-fill-neutral-*` tokens set. The dropdown *fill* + text are fixed (`--ha-color-form-background` etc landed and verified), but hover backgrounds on some surfaces are reading from a token not yet mapped — likely a `--ha-color-*` variant or a component-local var. Parked deliberately as cosmetic; trace it the same way the others were fixed (component source → exact token → map it) when picking the theme back up. Not a v0.1 blocker.
- **Methodology note** for whoever continues this: HA is mid-migration across THREE token layers — `--mdc-*` (oldest), `--input-*` (mid), `--ha-color-*` (current). A given surface might read any of them depending on whether its component's been rewritten. The reliable fix loop: reproduce → find the component in `home-assistant/frontend` → grep its styles for which `--*` var drives the broken property → map that var in `broadsheet.yaml`. Don't guess from token names.
