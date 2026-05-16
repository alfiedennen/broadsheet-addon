#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# broadsheet add-on entrypoint — v0.2 architecture.
#
# v0.1 ran broadsheet inside an HA ingress iframe, with all the
# accompanying chrome (top bar + sidebar + addon-name label). v0.2
# bypasses ingress entirely: nginx listens on a dedicated host port
# (default 8124) and the browser hits it directly. No HA chrome
# wrapping any page.
#
# Sequence:
#   1. Read addon options + addon version
#   2. Ensure curation file exists at the configured path
#   3. Read SUPERVISOR_TOKEN (auto-injected by HA)
#   4. Write runtime-env.js so the SPA picks up env vars at boot
#   5. Render nginx.conf from the template
#   6. Install the broadsheet HA theme (opt-in, version-marker logic)
#   7. Install the Lovelace launcher JS to /homeassistant/www/
#   8. Register the Lovelace launcher (sidebar entry) in background
#   9. Start the sidecar (curation API on localhost:8100)
#   10. Exec nginx in the foreground
#   11. On SIGTERM: uninstall the Lovelace launcher cleanly

set -e

# Addon version — used to version the launcher resource URL so
# cache busts cleanly on upgrade. bashio doesn't expose this directly;
# we read from the runtime-injected env (HA supervisor sets ADDON_VERSION
# in some contexts but not reliably) so fall back to config.yaml's
# hardcoded version baked into image at build time.
ADDON_VERSION="${ADDON_VERSION:-0.2.4}"

# ── 1. Read add-on options ──────────────────────────────────────────
LOG_LEVEL=$(bashio::config 'log_level')
CURATION_PATH=$(bashio::config 'curation_path')
TMDB_KEY=$(bashio::config 'tmdb_api_key')
REGION=$(bashio::config 'region')
if bashio::config.true 'read_only'; then
    READ_ONLY="true"
else
    READ_ONLY="false"
fi
HOST_PORT_OVERRIDE=$(bashio::config 'host_port_override')
if [ "$HOST_PORT_OVERRIDE" -eq 0 ] 2>/dev/null; then
    HOST_PORT=8124
else
    HOST_PORT="$HOST_PORT_OVERRIDE"
fi

bashio::log.level "${LOG_LEVEL}"
bashio::log.info "broadsheet ${ADDON_VERSION} starting up..."
bashio::log.info "  curation: ${CURATION_PATH}"
bashio::log.info "  region:   ${REGION}"
bashio::log.info "  read_only: ${READ_ONLY}"
bashio::log.info "  host port: ${HOST_PORT} (launcher targets http://<host>:${HOST_PORT}/)"

# ── 2. Ensure curation directory + default file exists ──────────────
mkdir -p "$(dirname "${CURATION_PATH}")"
if [ ! -f "${CURATION_PATH}" ]; then
    bashio::log.info "First boot — creating empty curation at ${CURATION_PATH}"
    NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > "${CURATION_PATH}" <<EOF
{
  "version": 1,
  "createdAt": "${NOW}",
  "lastModifiedAt": "${NOW}",
  "people": [],
  "floors": {},
  "areas": {},
  "devices": {},
  "entities": {},
  "labels": {},
  "pagePins": {},
  "pages": {},
  "voice": {},
  "paintings": {},
  "integrations": {
    "tmdb": { "apiKey": $([ -n "${TMDB_KEY}" ] && echo "\"${TMDB_KEY}\"" || echo "null"), "region": "${REGION}", "enabledLenses": ["new", "trending"] },
    "healthConnect": { "platformDetected": false, "sleepStartHourUTC": 21, "sleepEndHourUTC": 9 },
    "appleHealth": { "enabled": false }
  },
  "plugins": {
    "emanations": {"enabled": false, "config": {}},
    "ghost-cloud": {"enabled": false, "config": {}},
    "tmdb-tv": {"enabled": false, "config": {}},
    "voice": {"enabled": false, "config": {}},
    "harold-preset": {"enabled": false, "config": {}}
  }
}
EOF
fi

# ── 3. Discover SUPERVISOR_TOKEN ────────────────────────────────────
if [ -z "${SUPERVISOR_TOKEN}" ]; then
    bashio::log.error "SUPERVISOR_TOKEN is empty — addon won't be able to talk to HA"
    exit 1
fi

# ── 4. Write runtime-env.js for the SPA ─────────────────────────────
# Same as v0.1 but smaller — no ingress prefix to inject anywhere, so
# curationEndpoint is just a relative path and ingressEntry is gone.
# supervisorToken still rides along because the SPA uses it for WS
# authentication (browser-side WS connects to /api/websocket which
# nginx proxies to supervisor/core; HA's WS auth still wants the token
# in the auth message).
mkdir -p /usr/share/broadsheet/www
cat > /usr/share/broadsheet/www/runtime-env.js <<EOF
// Injected by run.sh on every container boot.
// SUPERVISOR_TOKEN rotates per container lifetime; this file is
// re-generated every start, never persisted in the image.
window.__BROADSHEET_ENV__ = {
  supervisorToken: "${SUPERVISOR_TOKEN}",
  region: "${REGION}",
  tmdbKey: $([ -n "${TMDB_KEY}" ] && echo "\"${TMDB_KEY}\"" || echo "null"),
  // Same-origin in v0.2 (no ingress prefix to dodge).
  curationEndpoint: "/api/broadsheet/curation",
  // The add-on is the user's dashboard — writable by default. The SPA
  // reads this; true makes it a read-only viewer (lock.* hard-banned
  // either way). Bare word, not a string — it's a JS boolean.
  readOnly: ${READ_ONLY}
};
EOF

# ── 5. Render nginx config from template ────────────────────────────
echo "{}" | tempio -template /etc/nginx/nginx.conf.tpl -out /etc/nginx/nginx.conf

# ── 6. Offer / update the broadsheet HA theme ──────────────────────
# Unchanged from v0.1: opt-in theme, fully reversible, marker-pattern
# update logic preserves user-edited copies.
HA_THEMES_DIR="/homeassistant/themes"
THEME_SRC="/usr/share/broadsheet/theme/broadsheet.yaml"
THEME_DST="${HA_THEMES_DIR}/broadsheet.yaml"
THEME_MARKER="broadsheet-theme-version:"

theme_version_of() {
    grep -oE "${THEME_MARKER} *[0-9.]+" "$1" 2>/dev/null | head -1 \
        | sed -E "s/.*${THEME_MARKER} *//"
}

install_theme() {
    cp "${THEME_SRC}" "${THEME_DST}"
    bashio::log.info "  ${1} broadsheet HA theme → ${THEME_DST}"
    if curl -fsS -m 10 -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        -H "Content-Type: application/json" \
        "http://supervisor/core/api/services/frontend/reload_themes" \
        -d '{}' >/dev/null 2>&1; then
        bashio::log.info "  Themes reloaded — Settings → Profile → Theme → broadsheet"
    else
        bashio::log.notice "  Theme written; reload didn't fire — appears after next HA restart"
    fi
}

if [ -f "${THEME_SRC}" ] && mkdir -p "${HA_THEMES_DIR}" 2>/dev/null; then
    SHIPPED_VER="$(theme_version_of "${THEME_SRC}")"
    if [ ! -f "${THEME_DST}" ]; then
        install_theme "Installed"
    else
        INSTALLED_VER="$(theme_version_of "${THEME_DST}")"
        if [ -z "${INSTALLED_VER}" ]; then
            bashio::log.info "broadsheet HA theme at ${THEME_DST} has no version marker — user-owned, leaving untouched"
        elif [ "${INSTALLED_VER}" != "${SHIPPED_VER}" ]; then
            install_theme "Updated (${INSTALLED_VER} → ${SHIPPED_VER})"
        else
            bashio::log.info "broadsheet HA theme up to date (${INSTALLED_VER})"
        fi
    fi
else
    bashio::log.notice "Couldn't reach ${HA_THEMES_DIR} — skipping theme install (is homeassistant_config mapped?)"
fi

# ── 7. Install the Lovelace launcher JS ─────────────────────────────
# This file is loaded by HA's Lovelace frontend (as a registered
# resource) and defines `<broadsheet-launcher-card>` — a redirect
# element that sends the browser to broadsheet's URL on render.
#
# The version-suffix in the filename gives us cache-bust on upgrade
# without playing with Cache-Control headers (HA aggressively caches
# /local/ assets). register-launcher.py registers /local/broadsheet-
# launcher.v<VERSION>.js as a Lovelace resource and cleans up older
# versions on upgrade.
#
# The launcher template uses @@PORT@@ as a placeholder for the addon's
# host-port-override. sed substitutes it at install time.
HA_WWW_DIR="/homeassistant/www"
LAUNCHER_SRC="/usr/share/broadsheet/launcher-template.js"
LAUNCHER_VER="$(echo "${ADDON_VERSION}" | tr '.' '_')"
LAUNCHER_DST="${HA_WWW_DIR}/broadsheet-launcher.v${LAUNCHER_VER}.js"

if [ -f "${LAUNCHER_SRC}" ] && mkdir -p "${HA_WWW_DIR}" 2>/dev/null; then
    sed "s|@@PORT@@|${HOST_PORT}|g" "${LAUNCHER_SRC}" > "${LAUNCHER_DST}"
    bashio::log.info "  Installed Lovelace launcher → ${LAUNCHER_DST}"

    # Clean up stale launcher files from previous addon versions
    for f in "${HA_WWW_DIR}"/broadsheet-launcher.v*.js; do
        if [ -f "$f" ] && [ "$f" != "${LAUNCHER_DST}" ]; then
            rm "$f" && bashio::log.info "  Removed stale launcher: $f"
        fi
    done
else
    bashio::log.notice "Couldn't reach ${HA_WWW_DIR} — skipping launcher install (is homeassistant_config mapped?)"
fi

# ── 7b. Ensure plugin-data root exists ──────────────────────────────
mkdir -p /data/plugin-data

# ── 8. Register the Lovelace launcher (sidebar entry) ───────────────
# Runs in background — register-launcher.py retries up to 60s if HA
# Core is still booting, so we don't want to block nginx startup on
# this. The sidebar entry appears as soon as HA + WS-API are ready;
# the SPA itself is reachable via direct URL the instant nginx starts.
bashio::log.info "Registering Lovelace launcher dashboard (background)..."
(
    export BROADSHEET_VERSION="${LAUNCHER_VER}"
    python3 /usr/share/broadsheet/init/register-launcher.py install \
        --version "${LAUNCHER_VER}" 2>&1
) &

# ── 9. Start sidecar (curation API on localhost) ────────────────────
bashio::log.info "Starting sidecar (curation + plugin-data API on localhost:8100)..."
python3 /usr/share/broadsheet/sidecar.py \
    --curation-path "${CURATION_PATH}" \
    --plugin-data-root /data/plugin-data \
    --bind 127.0.0.1:8100 &
SIDECAR_PID=$!

# ── 11. Shutdown cleanup ────────────────────────────────────────────
# HA addon spec only gives us SIGTERM to the container before
# destruction — there's no distinction between "you're being
# uninstalled" and "you're being restarted/updated". So we ALWAYS
# deregister the launcher on stop:
#
#   - register-launcher.py uninstall removes the Lovelace dashboard
#     entry + the registered resource. Idempotent. If this is a
#     restart (not an uninstall), the next addon boot re-registers
#     within ~5s. Brief sidebar-entry flicker on restart vs an
#     orphaned dashboard on uninstall is the right trade.
#
# We do NOT auto-remove the broadsheet HA theme on stop (only-if-our-
# marker logic is install-time only; preserves user-edited copies and
# keeps the theme available across restarts). Likewise we leave the
# harold-preset meeting-mode blueprint in place — the harold-preset
# settings panel has an explicit "Remove blueprint" affordance for
# users who want it gone before uninstall.
cleanup() {
    bashio::log.info 'Shutting down...'
    if [ -f /usr/share/broadsheet/init/register-launcher.py ]; then
        bashio::log.info '  Deregistering Lovelace launcher (best-effort)...'
        if python3 /usr/share/broadsheet/init/register-launcher.py uninstall 2>&1; then
            bashio::log.info '  Launcher deregistered'
        else
            bashio::log.warning '  Launcher deregister failed — sidebar entry may persist; users can remove via Settings → Dashboards'
        fi
    fi
    kill ${SIDECAR_PID} 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

# ── 10. Start nginx in the foreground ───────────────────────────────
bashio::log.info "broadsheet ready at http://<host>:${HOST_PORT}/"
exec nginx -g "daemon off;"
