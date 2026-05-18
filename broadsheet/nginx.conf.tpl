worker_processes 1;
pid /var/run/nginx.pid;
error_log /dev/stderr notice;

events {
    worker_connections 1024;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 65;
    # Match the sidecar's MAX_UPLOAD_BYTES (5 MB) plus envelope headroom.
    client_max_body_size 10m;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml application/manifest+json;
    access_log /dev/stdout;

    # Connection-upgrade map for WebSocket support (HA WS proxy below).
    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

    server {
        # v0.2 architecture: broadsheet on a dedicated host port,
        # bypassing HA ingress entirely. nginx listens on 8124 inside
        # the container; the addon's `ports:` block maps to the same
        # host port by default (user-configurable via Network settings).
        # The browser hits this directly — no HA chrome, no ingress
        # token rewrites, no sub_filter games on every chunk.
        listen 8124 default_server;
        server_name _;

        root /usr/share/broadsheet/www;
        index index.html;

        # runtime-env.js — app.html loads it via RELATIVE
        # `<script src="./runtime-env.js">`. On the root it resolves
        # fine, but on a deep route (/lights/, /heat/…) — or after an
        # F5 there — it'd resolve to `/lights/runtime-env.js`, which
        # `try_files` would fall through to index.html (HTML, not JS),
        # the script parse-fails, window.__BROADSHEET_ENV__ never loads,
        # auth-mode reads as 'none' and broadsheet bounces to /setup
        # with no context. Serve the one real file for any depth.
        location ~ /runtime-env\.js$ {
            alias /usr/share/broadsheet/www/runtime-env.js;
            default_type application/javascript;
            add_header Cache-Control "no-store";
        }

        # Content-hashed build assets — filename IS the version, cache
        # forever. Missing files MUST return a real 404 (never the SPA
        # fallback) so SvelteKit can detect version skew when a tab
        # outlives a deploy and reload itself.
        location /_app/immutable/ {
            try_files $uri =404;
            add_header Cache-Control "public, max-age=31536000, immutable";
        }

        # Non-hashed /_app/ (version.json, env.js) — must revalidate.
        location /_app/ {
            try_files $uri =404;
            add_header Cache-Control "no-cache";
        }

        # Plugin static assets — staged into image at
        # www/plugin-assets/<id>/ by CI. Bundled with build.
        location /plugin-assets/ {
            try_files $uri =404;
            add_header Cache-Control "public, max-age=300";
        }

        # User-uploaded plugin data — lives on /data/ persistent volume.
        # CSP default-src 'none' is belt-and-braces against
        # script-bearing SVG uploads.
        location /plugin-data/ {
            alias /data/plugin-data/;
            try_files $uri =404;
            add_header Cache-Control "public, max-age=60, must-revalidate";
            add_header Content-Security-Policy "default-src 'none'";
            add_header X-Content-Type-Options "nosniff";
        }

        # ── Curation API — proxy to the sidecar Python service ──
        location /api/broadsheet/ {
            proxy_pass http://127.0.0.1:8100/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── Harold preset API — same sidecar, /harold-preset/ prefix ──
        location /api/harold-preset/ {
            proxy_pass http://127.0.0.1:8100/harold-preset/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # ── HA Core API + WebSocket via Supervisor ──
        # Same SUPERVISOR_TOKEN injection pattern as v0.1 — broadsheet's
        # frontend talks to its own origin, nginx injects the auth
        # header on the way to HA Core. No user-side LLAT needed for
        # the default install.
        #
        # The browser-side WS auth message still sends the token (HA
        # WS protocol requires it explicitly), so runtime-env.js carries
        # supervisorToken (baked by run.sh, rotates per container). The
        # nginx Authorization header injection is for REST calls only.
        location /api/websocket {
            proxy_pass http://supervisor/core/api/websocket;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_set_header Host $host;
            proxy_read_timeout 86400;
            proxy_buffering off;
        }
        location /api/ {
            proxy_pass http://supervisor/core/api/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_read_timeout 86400;
            proxy_buffering off;
        }

        # ── HA static /local/* — paintings + user uploads ──
        location /local/ {
            proxy_pass http://supervisor/core/local/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
        }

        # ── 0.9.4.3: lovelace-embed proxy + auxiliary HA-asset routes ──
        #
        # broadsheet's `lovelace-embed` block iframes an HA Lovelace
        # URL. HA serves `X-Frame-Options: SAMEORIGIN` which blocks the
        # cross-origin frame (broadsheet at :8124, HA at :8123). The
        # /embed/ route below proxies the request server-side (we're
        # same-origin with HA via supervisor network) and STRIPS
        # X-Frame-Options on the response so the browser allows the
        # frame. Also strips Content-Security-Policy (HA's CSP
        # includes frame-ancestors restrictions that would also block).
        #
        # The auxiliary routes (/static, /frontend_latest, /auth,
        # /manifest.json, /service_worker.js) are the paths HA's
        # Lovelace frontend hits at the root level for its own assets.
        # Without them, the iframe loads but the JS/CSS inside fails
        # to fetch (same-origin to broadsheet, but those paths only
        # exist on HA). /api/ + /api/websocket already proxy above.
        #
        # Auth via Supervisor token — embedded Lovelace renders as
        # the supervisor user (admin). Fine for the wall-tablet use
        # case; documented in CUSTOM-PAGES-GUIDE.md.
        location ~ ^/embed/(.*)$ {
            proxy_pass http://supervisor/core/$1$is_args$args;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
            proxy_hide_header X-Frame-Options;
            proxy_hide_header Content-Security-Policy;
            proxy_http_version 1.1;
            proxy_read_timeout 86400;
            proxy_buffering off;
        }
        location /static/ {
            proxy_pass http://supervisor/core/static/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
            proxy_hide_header X-Frame-Options;
        }
        location /frontend_latest/ {
            proxy_pass http://supervisor/core/frontend_latest/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
            proxy_hide_header X-Frame-Options;
        }
        location /auth/ {
            proxy_pass http://supervisor/core/auth/;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
            proxy_hide_header X-Frame-Options;
        }
        location = /manifest.json {
            proxy_pass http://supervisor/core/manifest.json;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
        }
        location = /service_worker.js {
            proxy_pass http://supervisor/core/service_worker.js;
            proxy_set_header Authorization "Bearer {{ env "SUPERVISOR_TOKEN" }}";
            proxy_set_header Host $host;
        }

        # ── SPA fallback ──
        # In v0.1 we needed sub_filter rewrites here to inject the
        # ingress URL prefix into every asset path. v0.2 doesn't go
        # through ingress, so the build's bare /_app/... paths resolve
        # against this nginx directly. No rewrites needed.
        # index.html must revalidate every load (it references current
        # hashed entry chunks); SvelteKit answers 304 when unchanged.
        location / {
            add_header Cache-Control "no-cache";
            try_files $uri $uri/ /index.html;
        }
    }
}
