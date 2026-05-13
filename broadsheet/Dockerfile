ARG BUILD_FROM
FROM $BUILD_FROM

# BUILD_FROM is HA's hass-base image — comes with bashio, jq, tempio,
# Python, and the supervisor-aware tooling for free.

# nginx serves the static SPA + reverse-proxies /api/* to supervisor
# Python is already in hass-base; aiohttp is what the sidecar uses
RUN apk add --no-cache nginx py3-pip py3-aiohttp \
    && rm -rf /var/cache/apk/*

# Copy the runtime files first (these change rarely)
COPY run.sh /
COPY nginx.conf.tpl /etc/nginx/nginx.conf.tpl
COPY sidecar.py /usr/share/broadsheet/sidecar.py

# Copy the prebuilt SPA bundle (rebuilt every CI run, hence last)
# `www/` is populated by GitHub Actions before the docker build —
# the broadsheet/core repo is checked out, pnpm build, copied here.
# In dev (manual local docker build) you can pre-populate www/ yourself.
COPY www/ /usr/share/broadsheet/www/

RUN chmod +x /run.sh

EXPOSE 8099

CMD ["/run.sh"]

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
