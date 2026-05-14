#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
#
# broadsheet add-on entrypoint.
#
# Sequence:
#   1. Read add-on options
#   2. Ensure curation file exists at the configured path
#   3. Read SUPERVISOR_TOKEN (auto-injected by HA) + ingress_entry
#   4. Write runtime-env.js so the SPA picks up env vars before boot
#   5. Render nginx.conf from the template (substitutes ingress_port)
#   6. Start the sidecar (curation API on localhost:8100)
#   7. Exec nginx in the foreground

set -e

# ── 1. Read add-on options ──────────────────────────────────────────
LOG_LEVEL=$(bashio::config 'log_level')
CURATION_PATH=$(bashio::config 'curation_path')
TMDB_KEY=$(bashio::config 'tmdb_api_key')
REGION=$(bashio::config 'region')

bashio::log.level "${LOG_LEVEL}"
bashio::log.info "broadsheet starting up..."
bashio::log.info "  curation: ${CURATION_PATH}"
bashio::log.info "  region:   ${REGION}"

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
    "tmdb-tv": {"enabled": false, "config": {}}
  }
}
EOF
fi

# ── 3. Discover ingress URL + Supervisor token ──────────────────────
# Supervisor exposes the add-on's assigned ingress entry via bashio.
# SUPERVISOR_TOKEN is auto-injected as an env var by HA itself.
INGRESS_ENTRY=$(bashio::addon.ingress_entry)
INGRESS_PORT=$(bashio::addon.ingress_port)
bashio::log.info "  ingress entry: ${INGRESS_ENTRY}"
bashio::log.info "  ingress port:  ${INGRESS_PORT}"

if [ -z "${SUPERVISOR_TOKEN}" ]; then
    bashio::log.error "SUPERVISOR_TOKEN is empty — addon won't be able to talk to HA"
    exit 1
fi

# ── 4. Write runtime-env.js for the SPA ─────────────────────────────
# The SPA reads window.__BROADSHEET_ENV__ at boot to detect addon
# mode + get the credentials. Written fresh on every container boot
# so the SUPERVISOR_TOKEN is always current (it rotates).
mkdir -p /usr/share/broadsheet/www
cat > /usr/share/broadsheet/www/runtime-env.js <<EOF
// Injected by run.sh on every container boot.
// SUPERVISOR_TOKEN rotates per container lifetime; this file is
// re-generated every start, never persisted in the image.
window.__BROADSHEET_ENV__ = {
  ingressEntry: "${INGRESS_ENTRY}",
  supervisorToken: "${SUPERVISOR_TOKEN}",
  region: "${REGION}",
  tmdbKey: $([ -n "${TMDB_KEY}" ] && echo "\"${TMDB_KEY}\"" || echo "null"),
  // Ingress-prefixed so the SPA's curation client hits THIS nginx's
  // /api/broadsheet/ location block (HA's ingress proxy strips the
  // prefix before the request reaches us). A bare /api/broadsheet/...
  // would resolve against origin root and 404 on HA's frontend.
  curationEndpoint: "${INGRESS_ENTRY}/api/broadsheet/curation"
};
EOF

# ── 5. Render nginx config from template ────────────────────────────
# tempio is bundled in hass-base — substitutes env vars into the
# template via Go template syntax + the `env` function. Two CLI gotchas
# to remember (each cost a debugging round in the M5 verification):
#   - Flag is `-template`, not `-conf`. Wrong flag → tempio errors with
#     `Missing template argument` and exits.
#   - tempio reads its data context from STDIN as JSON. We don't have
#     non-env data, so we pipe `{}` to satisfy the parser. Without it
#     tempio also errors out.
# Pattern lifted verbatim from home-assistant/addons/mosquitto.
# All three vars MUST be exported — tempio's `{{ env "X" }}` reads the
# process environment, not shell-local variables. INGRESS_ENTRY was the
# one that bit us: left unexported, tempio rendered it as empty string,
# making the nginx sub_filter rewrite (`"/_app/` → `"<entry>/_app/`) a
# silent no-op, and the SPA kept 404ing on every asset.
export INGRESS_PORT
export INGRESS_ENTRY
export SUPERVISOR_TOKEN
echo "{}" | tempio -template /etc/nginx/nginx.conf.tpl -out /etc/nginx/nginx.conf

# ── 6. Start sidecar (curation API on localhost) ────────────────────
bashio::log.info "Starting sidecar (curation API on localhost:8100)..."
python3 /usr/share/broadsheet/sidecar.py \
    --curation-path "${CURATION_PATH}" \
    --bind 127.0.0.1:8100 &
SIDECAR_PID=$!

# Trap shutdown so the sidecar exits cleanly with nginx
trap "bashio::log.info 'Shutting down...'; kill ${SIDECAR_PID} 2>/dev/null; exit 0" SIGTERM SIGINT

# ── 7. Start nginx in the foreground ────────────────────────────────
bashio::log.info "broadsheet ready at ingress entry ${INGRESS_ENTRY}"
exec nginx -g "daemon off;"
