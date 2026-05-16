#!/usr/bin/env python3
"""broadsheet sidecar — tiny aiohttp service for /api/broadsheet/curation.

Reads + writes broadsheet.json (the user's curation file). Exposed only
on localhost; nginx proxies the SPA's /api/broadsheet/* requests to it.

Spec: github.com/alfiedennen/broadsheet docs/SETTINGS-SCHEMA.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format='[broadsheet:sidecar] %(message)s',
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

CURRENT_VERSION = 1

# Required top-level keys for v1 schema. Sidecar enforces this minimal
# shape; deeper validation lives in the SPA so it can give better
# error messages near the user.
REQUIRED_KEYS = {
    'version',
    'createdAt',
    'lastModifiedAt',
    'people',
    'floors',
    'areas',
    'devices',
    'entities',
    'labels',
    'pagePins',
    'pages',
    'voice',
    'paintings',
    'integrations',
    'plugins',
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


async def get_curation(request: web.Request) -> web.Response:
    path: Path = request.app['curation_path']
    try:
        if not path.exists():
            return web.json_response({'error': 'curation file missing'}, status=404)
        body = json.loads(path.read_text())
        return web.json_response(body)
    except json.JSONDecodeError as e:
        log.error('curation file corrupt: %s', e)
        return web.json_response({'error': f'corrupt curation: {e}'}, status=500)


async def put_curation(request: web.Request) -> web.Response:
    path: Path = request.app['curation_path']

    try:
        body: Any = await request.json()
    except json.JSONDecodeError as e:
        return web.json_response({'error': f'invalid json: {e}'}, status=400)

    if not isinstance(body, dict):
        return web.json_response({'error': 'top-level must be object'}, status=400)

    if body.get('version') != CURRENT_VERSION:
        return web.json_response(
            {'error': f'version must be {CURRENT_VERSION}'},
            status=400,
        )

    missing = REQUIRED_KEYS - body.keys()
    if missing:
        return web.json_response(
            {'error': f'missing keys: {sorted(missing)}'},
            status=400,
        )

    # Stamp the modification time server-side regardless of what the
    # client sent (so we know on-disk reflects this exact moment).
    body['lastModifiedAt'] = now_iso()

    # Atomic write — render to .tmp, fsync, rename. Avoids partial
    # writes if the addon crashes mid-write.
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(json.dumps(body, indent=2))
        tmp.replace(path)
    except OSError as e:
        log.error('curation write failed: %s', e)
        return web.json_response({'error': f'write failed: {e}'}, status=500)

    log.info('curation saved (%d bytes)', path.stat().st_size)
    return web.json_response({'ok': True, 'lastModifiedAt': body['lastModifiedAt']})


## ── plugin-data: persistent user-uploaded files for plugins ───────
# Lives under <plugin_data_root>/<plugin_id>/<filename>. This is the
# runtime counterpart to bundled /plugin-assets/ — files here are
# user-uploaded (e.g. emanations paintings) and survive add-on updates
# because /data is the addon's persistent volume.
#
# Security model: the sidecar binds to localhost only and is reached
# only via nginx → HA Ingress, which means uploads require an HA-
# authenticated session. The validation here defends against path
# traversal and oversized writes; the auth boundary is the HA ingress.

PLUGIN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
# Filename rules: structural defense (Path.resolve + relative_to) is the
# real guard against path traversal — see _file_path. The validator
# below blocks the chars that are unambiguously dangerous (path
# separators, control bytes, leading dot, `..`) and demands an image
# extension. Everything else is permitted, including spaces,
# parentheses, apostrophes, unicode — real-world filenames the user
# typed themselves should round-trip without sanitisation. The earlier
# `[A-Za-z0-9._-]` regex was paranoid and rejected names like
# "Elena's office.png" — annoying with no security upside.
ALLOWED_EXT_RE = re.compile(r'\.(png|jpg|jpeg|svg|webp|gif|json)$', re.IGNORECASE)
BAD_CHARS_RE = re.compile(r'[\x00-\x1f\x7f/\\]')
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per file — generous for paintings


def _validate_filename(name: str) -> str | None:
    """None if `name` is a safe upload filename; else a human error message."""
    if not name or not name.strip():
        return 'filename is empty'
    if len(name) > 128:
        return f'filename too long ({len(name)} chars; max 128)'
    if BAD_CHARS_RE.search(name):
        return 'filename contains path separators or control characters'
    if '..' in name:
        return 'filename contains ".."'
    if name.startswith('.'):
        return 'filename must not start with "."'
    if not ALLOWED_EXT_RE.search(name):
        return 'filename must end with .png, .jpg, .jpeg, .svg, .webp, .gif, or .json'
    return None


def _plugin_dir(request: web.Request, plugin_id: str) -> Path | None:
    """Per-plugin storage dir, with path-traversal guard. None if invalid."""
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        return None
    root: Path = request.app['plugin_data_root']
    root_resolved = root.resolve()
    d = (root / plugin_id).resolve()
    # Confine — guard against any odd path resolution
    try:
        d.relative_to(root_resolved)
    except ValueError:
        return None
    return d


def _file_path(d: Path, filename: str) -> tuple[Path | None, str | None]:
    """
    Resolved file path inside plugin dir + traversal guard.

    Returns (path, None) on success, or (None, error_message) on failure
    so callers can surface the specific reason to the user instead of a
    generic "invalid filename".
    """
    err = _validate_filename(filename)
    if err is not None:
        return None, err
    d_resolved = d.resolve() if d.exists() else d.parent.resolve() / d.name
    p = (d / filename).resolve()
    try:
        p.relative_to(d_resolved)
    except ValueError:
        return None, 'filename resolves outside the plugin data directory'
    return p, None


async def list_plugin_data(request: web.Request) -> web.Response:
    plugin_id = request.match_info['plugin_id']
    d = _plugin_dir(request, plugin_id)
    if d is None:
        return web.json_response({'error': 'invalid plugin id'}, status=400)
    if not d.exists():
        return web.json_response({'files': []})
    files = []
    for p in sorted(d.iterdir()):
        if p.is_file():
            st = p.stat()
            files.append({'filename': p.name, 'size': st.st_size, 'mtime': st.st_mtime})
    return web.json_response({'files': files})


async def upload_plugin_data(request: web.Request) -> web.Response:
    plugin_id = request.match_info['plugin_id']
    d = _plugin_dir(request, plugin_id)
    if d is None:
        return web.json_response({'error': 'invalid plugin id'}, status=400)

    try:
        reader = await request.multipart()
    except Exception as e:  # noqa: BLE001
        return web.json_response({'error': f'expected multipart: {e}'}, status=400)
    field = await reader.next()
    if field is None or field.name != 'file':
        return web.json_response(
            {'error': 'expected multipart field named "file"'}, status=400
        )

    filename = field.filename or ''
    fp, err = _file_path(d, filename)
    if fp is None:
        return web.json_response({'error': err or 'invalid filename'}, status=400)

    d.mkdir(parents=True, exist_ok=True)

    size = 0
    tmp = fp.with_suffix(fp.suffix + '.tmp')
    try:
        with tmp.open('wb') as f:
            while True:
                chunk = await field.read_chunk(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    f.close()
                    tmp.unlink(missing_ok=True)
                    return web.json_response(
                        {'error': f'file exceeds {MAX_UPLOAD_BYTES} bytes'},
                        status=413,
                    )
                f.write(chunk)
        tmp.replace(fp)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        log.error('plugin-data write failed: %s', e)
        return web.json_response({'error': f'write failed: {e}'}, status=500)

    log.info('plugin-data uploaded: %s/%s (%d bytes)', plugin_id, filename, size)
    return web.json_response({'filename': filename, 'size': size})


async def delete_plugin_data(request: web.Request) -> web.Response:
    plugin_id = request.match_info['plugin_id']
    filename = request.match_info['filename']
    d = _plugin_dir(request, plugin_id)
    if d is None:
        return web.json_response({'error': 'invalid plugin id'}, status=400)
    fp, err = _file_path(d, filename)
    if fp is None:
        return web.json_response({'error': err or 'invalid filename'}, status=400)
    if not fp.exists():
        return web.json_response({'error': 'not found'}, status=404)
    try:
        fp.unlink()
    except OSError as e:
        return web.json_response({'error': f'delete failed: {e}'}, status=500)
    log.info('plugin-data deleted: %s/%s', plugin_id, filename)
    return web.json_response({'ok': True})


async def health(request: web.Request) -> web.Response:
    path: Path = request.app['curation_path']
    return web.json_response(
        {
            'status': 'ok',
            'curation_path': str(path),
            'curation_exists': path.exists(),
            'curation_size': path.stat().st_size if path.exists() else 0,
        }
    )


# ── Harold preset endpoints ─────────────────────────────────────────
#
# `/api/harold-preset/*` is served by the SPA-side @broadsheet/harold-preset
# plugin via these sidecar routes. The plugin can't reach HA's filesystem
# from the browser; the sidecar can (homeassistant_config:rw mount gives
# it /homeassistant/blueprints/), so any blueprint install / wakeword
# download / etc has to come through here.

# Bundled artefacts live at /usr/share/broadsheet/harold-preset/ (baked
# into the addon image at build time). On install the SPA hits these
# routes to GET the wakeword OR POST to install the blueprint.
HAROLD_PRESET_ROOT = Path('/usr/share/broadsheet/harold-preset')
HA_BLUEPRINTS_DIR = Path('/homeassistant/blueprints/automation/broadsheet')
MEETING_MODE_BLUEPRINT_DST = HA_BLUEPRINTS_DIR / 'meeting-mode.yaml'


async def harold_wakeword(request: web.Request) -> web.StreamResponse:
    """Serve a bundled wake-word artefact (`hey_harold.tflite`,
    `hey_harold.json`, `esphome-snippet.yaml`).
    """
    filename = request.match_info.get('filename', '')
    # Strict allowlist — don't let arbitrary paths escape via ..
    allowed = {
        'hey_harold.tflite',
        'hey_harold.json',
        'esphome-snippet.yaml',
    }
    if filename not in allowed:
        return web.json_response({'error': 'not found'}, status=404)
    path = HAROLD_PRESET_ROOT / filename
    if not path.exists():
        return web.json_response(
            {
                'error': 'artefact missing in addon image',
                'path': str(path),
                'hint': 'old addon version? Update broadsheet add-on to a build that includes harold-preset bundles.',
            },
            status=404,
        )
    return web.FileResponse(path)


async def install_blueprint(request: web.Request) -> web.Response:
    """Copy meeting-mode.blueprint.yaml into HA's blueprints/ tree."""
    src = HAROLD_PRESET_ROOT / 'meeting-mode.blueprint.yaml'
    if not src.exists():
        return web.json_response(
            {
                'error': 'blueprint source missing in addon image',
                'src': str(src),
            },
            status=500,
        )
    try:
        HA_BLUEPRINTS_DIR.mkdir(parents=True, exist_ok=True)
        MEETING_MODE_BLUEPRINT_DST.write_bytes(src.read_bytes())
        log.info('installed blueprint: %s', MEETING_MODE_BLUEPRINT_DST)
        return web.json_response(
            {
                'ok': True,
                'installed_at': str(MEETING_MODE_BLUEPRINT_DST),
            }
        )
    except OSError as e:
        log.exception('blueprint install failed')
        return web.json_response(
            {
                'error': 'install failed',
                'detail': str(e),
                'hint': 'is homeassistant_config:rw mapped in the addon config? It should be.',
            },
            status=500,
        )


async def uninstall_blueprint(request: web.Request) -> web.Response:
    """Remove the meeting-mode blueprint. Idempotent — already-gone = success."""
    try:
        if MEETING_MODE_BLUEPRINT_DST.exists():
            MEETING_MODE_BLUEPRINT_DST.unlink()
            log.info('removed blueprint: %s', MEETING_MODE_BLUEPRINT_DST)
        return web.json_response({'ok': True})
    except OSError as e:
        log.exception('blueprint uninstall failed')
        return web.json_response(
            {'error': 'uninstall failed', 'detail': str(e)}, status=500
        )


def main() -> None:
    parser = argparse.ArgumentParser(description='broadsheet curation sidecar')
    parser.add_argument(
        '--curation-path',
        required=True,
        help='Filesystem path to broadsheet.json',
    )
    parser.add_argument(
        '--plugin-data-root',
        default='/data/plugin-data',
        help='Filesystem root for per-plugin user-uploaded files',
    )
    parser.add_argument(
        '--bind',
        default='127.0.0.1:8100',
        help='host:port to bind (default 127.0.0.1:8100)',
    )
    args = parser.parse_args()

    # client_max_size accommodates the 5 MB upload + multipart envelope
    # overhead. Without this aiohttp's default 1 MB cap rejects uploads
    # before the streaming reader ever sees them.
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 256 * 1024)
    app['curation_path'] = Path(args.curation_path)
    app['plugin_data_root'] = Path(args.plugin_data_root)
    app.router.add_get('/curation', get_curation)
    app.router.add_put('/curation', put_curation)
    app.router.add_get('/plugin-data/{plugin_id}', list_plugin_data)
    app.router.add_post('/plugin-data/{plugin_id}', upload_plugin_data)
    app.router.add_delete('/plugin-data/{plugin_id}/{filename}', delete_plugin_data)
    app.router.add_get('/health', health)
    # Harold preset (@broadsheet/harold-preset plugin) — wakeword
    # downloads + meeting-mode blueprint auto-install. Mounted under
    # /harold-preset/* — nginx prefixes /api/ when routing from the SPA.
    app.router.add_get('/harold-preset/wakeword/{filename}', harold_wakeword)
    app.router.add_post('/harold-preset/blueprint/install', install_blueprint)
    app.router.add_delete('/harold-preset/blueprint/install', uninstall_blueprint)

    host, port = args.bind.split(':')
    log.info('starting on %s, curation file: %s', args.bind, args.curation_path)
    web.run_app(app, host=host, port=int(port), access_log=None, print=None)


if __name__ == '__main__':
    main()
