ARG BUILD_FROM
FROM $BUILD_FROM

# BUILD_FROM is HA's hass-base image — comes with bashio, jq, tempio,
# Python, and the supervisor-aware tooling for free.

# nginx serves the static SPA + reverse-proxies /api/* to supervisor
# Python is already in hass-base; aiohttp is what the sidecar uses
RUN apk add --no-cache nginx py3-pip py3-aiohttp \
    && rm -rf /var/cache/apk/*

# Wire run.sh into the s6-overlay service-discovery layout. The
# hass-base image's ENTRYPOINT is /init (s6-overlay's PID-1 init),
# which scans /etc/services.d/<name>/run and supervises each found
# script. If we instead set CMD ["/run.sh"] (the obvious-looking
# pattern), our script becomes PID 1 directly, s6-overlay-suexec
# refuses to operate ("can only run as pid 1"), and the addon dies
# at start with that exact error in the logs.
COPY run.sh /etc/services.d/broadsheet/run
COPY nginx.conf.tpl /etc/nginx/nginx.conf.tpl
COPY sidecar.py /usr/share/broadsheet/sidecar.py

# Copy the prebuilt SPA bundle (rebuilt every CI run, hence last)
# `www/` is populated by GitHub Actions before the docker build —
# the broadsheet/core repo is checked out, pnpm build, copied here.
# In dev (manual local docker build) you can pre-populate www/ yourself.
COPY www/ /usr/share/broadsheet/www/

RUN chmod +x /etc/services.d/broadsheet/run

EXPOSE 8099

# No CMD — hass-base's /init (s6-overlay) handles startup via
# /etc/services.d/broadsheet/run.

# Labels (used by HA's add-on builder + GHCR display)
LABEL \
    io.hass.name="broadsheet" \
    io.hass.description="An editorial frontend for Home Assistant" \
    io.hass.arch="aarch64|amd64" \
    io.hass.type="addon" \
    io.hass.version="0.1.0" \
    maintainer="alfiedennen" \
    org.opencontainers.image.title="broadsheet" \
    org.opencontainers.image.description="An editorial frontend for Home Assistant" \
    org.opencontainers.image.source="https://github.com/alfiedennen/broadsheet-addon" \
    org.opencontainers.image.licenses="MIT"
