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
FILENAME_RE = re.compile(
    r'^[A-Za-z0-9._-]{1,128}\.(png|jpg|jpeg|svg|webp|gif|json)$',
    re.IGNORECASE,
)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per file — generous for paintings


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


def _file_path(d: Path, filename: str) -> Path | None:
    """Resolved file path inside plugin dir, with traversal guard. None if invalid."""
    if not FILENAME_RE.fullmatch(filename):
        return None
    d_resolved = d.resolve() if d.exists() else d
    p = (d / filename).resolve()
    try:
        # Use the not-yet-existing form for both sides so the comparison works
        # before the dir is created.
        p.relative_to(d_resolved if d.exists() else d.parent.resolve() / d.name)
    except ValueError:
        return None
    return p


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
    fp = _file_path(d, filename)
    if fp is None:
        return web.json_response(
            {
                'error': (
                    'invalid filename — must match [A-Za-z0-9._-]{1,128} '
                    'and end .png/.jpg/.jpeg/.svg/.webp/.gif/.json'
                )
            },
            status=400,
        )

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
    fp = _file_path(d, filename)
    if fp is None:
        return web.json_response({'error': 'invalid filename'}, status=400)
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

    host, port = args.bind.split(':')
    log.info('starting on %s, curation file: %s', args.bind, args.curation_path)
    web.run_app(app, host=host, port=int(port), access_log=None, print=None)


if __name__ == '__main__':
    main()
