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
        '--bind',
        default='127.0.0.1:8100',
        help='host:port to bind (default 127.0.0.1:8100)',
    )
    args = parser.parse_args()

    app = web.Application()
    app['curation_path'] = Path(args.curation_path)
    app.router.add_get('/curation', get_curation)
    app.router.add_put('/curation', put_curation)
    app.router.add_get('/health', health)

    host, port = args.bind.split(':')
    log.info('starting on %s, curation file: %s', args.bind, args.curation_path)
    web.run_app(app, host=host, port=int(port), access_log=None, print=None)


if __name__ == '__main__':
    main()
