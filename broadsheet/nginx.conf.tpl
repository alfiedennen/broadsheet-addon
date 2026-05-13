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
        location / {
            sub_filter '"/_app/' '"{{ env "INGRESS_ENTRY" }}/_app/';
            sub_filter "'/_app/" "'{{ env "INGRESS_ENTRY" }}/_app/";
            sub_filter '"/favicon' '"{{ env "INGRESS_ENTRY" }}/favicon';
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
