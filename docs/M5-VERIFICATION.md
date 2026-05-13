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

## First-pass verification log (2026-05-13)

What got fixed during the first attempt + what's still outstanding.

### Fixed and shipped

**1. Visibility trinity** (commit `20e9a4e` — see "Visibility requirements" above). Real install requires `broadsheet-addon` repo public **and** both `broadsheet-{amd64,aarch64}` GHCR packages public. SPA repo can stay private.

**2. `image:` field in `config.yaml`** (commit `76a4243`). Without it, Supervisor sees the Dockerfile in the addon dir and tries to **build locally** on the user's HAOS — which fails because `BUILD_FROM` is injected by HA's CI builder action, not present at install time. Symptom: install returns `unknown_error` with empty error message. Fix: explicit `image: ghcr.io/alfiedennen/broadsheet-{arch}` in config.yaml.

**3. s6-overlay service-discovery layout** (commit `8425de5`). Old pattern of `COPY run.sh /` + `CMD ["/run.sh"]` makes our script PID 1, breaking s6-overlay's init chain. New pattern: `COPY run.sh /etc/services.d/broadsheet/run` + no CMD — the hass-base ENTRYPOINT (`/init` = s6-overlay) auto-discovers and supervises scripts in that path. (NB: per HA addon docs the legacy `CMD ["/run.sh"]` pattern *should* also work via /init; the service-layout pattern is more idiomatic regardless.)

**4. `.gitattributes` for LF line endings** (commit `57f3592`). Belt-and-braces — Windows git checkouts default to autocrlf, which would have produced CRLF in the addon container and broken the `#!/usr/bin/with-contenv bashio` shebang at runtime. Verified the actual git blobs were already LF this time, so it wasn't the active cause — but a future Windows contributor without this file would have walked straight into it.

### Still outstanding (next session)

- Despite all four fixes above, addon 0.1.1 logs still show `s6-overlay-suexec: fatal: can only run as pid 1` after Supervisor reports it as installed at v0.1.1. Two possibilities: (a) Supervisor's `install` command silently kept the old 0.1.0 image and only updated the manifest version (the `update` endpoint may force a real GHCR pull); (b) there's a fifth bug not yet diagnosed.
- Recommended next-session diagnostic path:
  1. Boot Env 2 fresh (cached, ~90s)
  2. Add repo + install via the **HA UI** (not WS) — eliminates the question of whether WS install vs UI install behaves differently
  3. If install fails the same way: `ssh root@<vm-ip>` and `docker exec -it addon_68fa04fc_broadsheet ls -la /etc/services.d/broadsheet/` to confirm file is there + executable + correct shebang
  4. Compare against a known-good HA community addon (e.g. `hassio-addons/addon-mosquitto`) — verify our pattern matches theirs exactly
- Not blocking M5 in principle — the foundation (auth, ingress, curation persistence, multi-arch build, GHCR distribution) is all in place. What's blocking is one specific runtime startup issue inside the container.

### What to NOT redo next session

- Don't re-download HAOS (it's at `D:\broadsheet-test-env\haos.vdi`, ~3GB).
- Don't re-create the VM (`broadsheet-test` exists, NAT'd to localhost:8123, GUI mode, serial → `D:\broadsheet-test-env\serial.log`).
- Don't re-onboard (refresh-token preserved, see ha_token.env).
- Don't re-flip visibilities (all three resources are already public).
