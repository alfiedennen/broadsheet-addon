worker_processes 1;
pid /var/run/nginx.pid;
error_log /dev/stderr notice;

events {
    worker_connections 1024;
}

# Connection-upgrade map for WebSocket support — required by ingress_stream.
http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    # sendfile() bypasses nginx's user-space content filter chain — kernel
    # copies file → socket directly. sub_filter (which we use to rewrite
    # SvelteKit's absolute /_app/ asset paths) lives in that filter chain
    # and cannot operate on sendfile responses. Performance hit is
    # negligible for a small SPA bundle, so off across the board.
    sendfile      off;
    keepalive_timeout 65;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml application/manifest+json;
    access_log /dev/stdout;

    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

    # Sub-filter rewrites SvelteKit's absolute asset paths.
    # Built SPA emits `<link href="/_app/immutable/...">` because adapter-static
    # in fallback mode can't know the runtime URL prefix at build time, and
    # SvelteKit's `paths.relative` only applies to `%sveltekit.assets%`-style
    # template variables — NOT to module preload tags it emits itself. Without
    # rewriting, the browser requests `/_app/...` from origin root (HA frontend),
    # gets 404s for every chunk, and the SPA never boots.
    sub_filter_once off;
    sub_filter_types text/html application/javascript text/css;

    server {
        listen {{ env "INGRESS_PORT" }} default_server;
        server_name _;

        root /usr/share/broadsheet/www;
        index index.html;

        # SPA fallback — any unknown path serves index.html so the
        # SvelteKit client-side router can take over.
        # sub_filter rewrites `/_app/` to `<ingress_entry>/_app/` so the
        # built absolute paths resolve correctly through the ingress proxy.
        # INGRESS_ENTRY comes from bashio (no trailing slash), e.g.
        # `/api/hassio_ingress/<token>`.
        # runtime-env.js — app.html loads it via a RELATIVE
        # `<script src="./runtime-env.js">`. On the ingress root that
        # resolves fine, but on a deep route (/lights/, /heat/…) — or
        # after an F5 there — it resolves to `/lights/runtime-env.js`,
        # which `try_files` would fall through to index.html (HTML, not
        # JS). The script then parse-fails, window.__BROADSHEET_ENV__
        # never loads, auth-mode reads as 'none' and broadsheet bounces
        # to /setup. Serve the one real file for ANY depth of request.
        location ~ /runtime-env\.js$ {
            alias /usr/share/broadsheet/www/runtime-env.js;
            default_type application/javascript;
            add_header Cache-Control "no-store";
        }

        # Built immutable assets — a missing file must return a REAL
        # 404, never the index.html SPA fallback. When the add-on
        # updates under an open tab, that tab's SvelteKit runtime asks
        # for OLD chunk hashes; if `try_files` fed it index.html (200,
        # text/html) the browser chokes with "expected a JS module, got
        # text/html" and the app wedges. A clean 404 instead lets
        # SvelteKit detect the version skew and reload itself. The
        # sub_filter stays — chunks can carry cross-references to other
        # `/_app/` paths that still need the ingress prefix.
        location /_app/ {
            sub_filter '"/_app/' '"{{ env "INGRESS_ENTRY" }}/_app/';
            sub_filter "'/_app/" "'{{ env "INGRESS_ENTRY" }}/_app/";
            try_files $uri =404;
        }

        # Plugin static assets. Each bundled plugin's `static/` dir is
        # staged into the image at www/plugin-assets/<plugin-id>/ by
        # CI; plugin code references them via pluginAssetUrl(), which
        # resolves to /plugin-assets/<id>/… (ingress-prefixed at
        # runtime). A dedicated namespace — NOT /local/<id>/ — because
        # `location /local/` below already proxies to HA Core's own
        # www folder; plugin assets would collide. Missing files 404
        # cleanly (same reasoning as /_app/ — never the SPA fallback).
        location /plugin-assets/ {
            try_files $uri =404;
            add_header Cache-Control "public, max-age=300";
        }

        location / {
            sub_filter '"/_app/' '"{{ env "INGRESS_ENTRY" }}/_app/';
            sub_filter "'/_app/" "'{{ env "INGRESS_ENTRY" }}/_app/";
            sub_filter '"/favicon' '"{{ env "INGRESS_ENTRY" }}/favicon';
            # SvelteKit bakes `base: ""` into the inline bootstrap script
            # because adapter-static + fallback mode can't know the serve
            # URL at build time. Its entire runtime (version polling,
            # route-data fetches) builds URLs from this base. Rewrite it
            # to the ingress entry so those runtime fetches land on us.
            sub_filter 'base: ""' 'base: "{{ env "INGRESS_ENTRY" }}"';
            try_files $uri $uri/ /index.html;
        }

        # ── Curation API — proxy to the sidecar Python service ──
        location /api/broadsheet/ {
            proxy_pass http://127.0.0.1:8100/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── HA Core API + WebSocket via Supervisor ──
        # The crucial trick: SUPERVISOR_TOKEN is auto-injected as an
        # env var by HA. We expose it via the Authorization header on
        # every proxied request. The SPA never sees a token, never
        # pastes one — it just talks to its own origin.
        location /api/ {
            proxy_pass http://supervisor/core/api/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_read_timeout 86400;
            proxy_buffering off;
        }

        # ── HA static /local/* — paintings, plugin assets, etc. ──
        # Same pattern: bearer-injected proxy to the supervisor's HA
        # Core endpoint.
        location /local/ {
            proxy_pass http://supervisor/core/local/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
        }
    }
}
