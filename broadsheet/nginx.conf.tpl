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
    sendfile      on;
    keepalive_timeout 65;
    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml application/manifest+json;
    access_log /dev/stdout;

    map $http_upgrade $connection_upgrade {
        default upgrade;
        ''      close;
    }

    server {
        listen {{ .INGRESS_PORT }} default_server;
        server_name _;

        root /usr/share/broadsheet/www;
        index index.html;

        # SPA fallback — any unknown path serves index.html so the
        # SvelteKit client-side router can take over.
        location / {
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
            proxy_set_header Authorization "Bearer {{ .SUPERVISOR_TOKEN }}";
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
            proxy_set_header Authorization "Bearer {{ .SUPERVISOR_TOKEN }}";
            proxy_set_header Host $host;
        }
    }
}
