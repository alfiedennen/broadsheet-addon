#!/usr/bin/env python3
"""
broadsheet launcher registration — v0.2 architecture.

What this does (install mode):
  1. Lists Lovelace resources via `lovelace/resources`
  2. Lists Lovelace dashboards via `lovelace/dashboards`
  3. If a resource with our launcher URL is missing, creates it
     (type: module, url: /local/broadsheet-launcher.<version>.js)
  4. If a stale launcher resource from a previous version exists,
     removes it (keeps Lovelace clean across upgrades)
  5. If a dashboard with url_path=broadsheet is missing, creates it
     with the launcher card as a panel-mode view
  6. Idempotent — re-running with the same state is a no-op

What this does (uninstall mode):
  Same listing, removes our resource + dashboard entries. Run from
  the SIGTERM trap in run.sh so an addon uninstall leaves no orphans
  in HA's frontend config.

Why this script exists: v0.2 architecture serves broadsheet on a
dedicated host port outside HA ingress, so HA's sidebar doesn't
"know" about broadsheet. The launcher card is a one-line custom
element that redirects window.top to broadsheet's URL — the cleanest
"sidebar entry that opens a different page" pattern HA permits
without modifying the user's configuration.yaml.

Authenticated via the SUPERVISOR_TOKEN env var (auto-injected by HA
into the addon container). Talks to HA Core via the supervisor proxy
at http://supervisor/core/api/websocket.

Failure modes:
  - HA not yet ready (addon starts before HA Core): retry up to
    DEFAULT_RETRY_S, then log + skip (next addon restart will
    re-attempt; the user sees no sidebar entry until then)
  - lovelace mode is YAML (not storage): WS calls fail; log + skip
    with a hint for the user to add the snippet manually
  - per-call failures: log + continue (partial registration is
    better than no registration)

Exit code 0 if the script connected and reached a stable state;
1 only if we couldn't authenticate at all.

Spec: docs/plans/plan-theme-G-frontend-not-panel.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import aiohttp

WS_URL = "ws://supervisor/core/api/websocket"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRY_S = 60.0  # max time to wait for HA to come up

# The launcher resource URL — versioned so cache busts cleanly on
# upgrade. The version segment matches the addon's `version:` so the
# resource always points at the JS file run.sh just wrote to
# /homeassistant/www/.
def launcher_url(version: str) -> str:
    safe = version.replace(".", "_")
    return f"/local/broadsheet-launcher.v{safe}.js"


# Lovelace resource URL prefix for marker-pattern cleanup. Any
# Lovelace resource starting with this string is considered
# broadsheet-managed and removable on uninstall / upgrade.
LAUNCHER_URL_PREFIX = "/local/broadsheet-launcher."

# The dashboard we register. url_path is the URL slug HA uses in
# the sidebar (e.g. /broadsheet). title shows in the sidebar.
DASHBOARD = {
    "url_path": "broadsheet",
    "title": "Broadsheet",
    "icon": "mdi:newspaper-variant",
    "show_in_sidebar": True,
    "require_admin": False,
}

# The Lovelace config we save for our dashboard — one panel-mode
# view containing only the launcher card.
DASHBOARD_CONFIG = {
    "title": "Broadsheet",
    "views": [
        {
            "panel": True,
            "cards": [
                {"type": "custom:broadsheet-launcher-card"},
            ],
        }
    ],
}


async def ws_call(
    ws: aiohttp.ClientWebSocketResponse,
    payload: dict[str, Any],
    msg_id: int,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Send a WS message, wait for the matching `result` reply."""
    payload["id"] = msg_id
    await ws.send_json(payload)
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for result (id={msg_id})")
        raw = await asyncio.wait_for(ws.receive(), timeout=remaining)
        if raw.type != aiohttp.WSMsgType.TEXT:
            continue
        msg = json.loads(raw.data)
        if msg.get("id") == msg_id and msg.get("type") == "result":
            return msg


async def authed_session(token: str, log) -> aiohttp.ClientWebSocketResponse | None:
    """Returns an authed WS, or None on failure. Caller closes."""
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(WS_URL, timeout=DEFAULT_TIMEOUT_S)
    except (aiohttp.ClientError, OSError) as e:
        log(f"  FATAL: couldn't connect to {WS_URL}: {e}")
        await session.close()
        return None

    try:
        hello = await ws.receive()
        if hello.type != aiohttp.WSMsgType.TEXT:
            log(f"  FATAL: unexpected first message type: {hello.type}")
            await ws.close()
            await session.close()
            return None
        hmsg = json.loads(hello.data)
        if hmsg.get("type") != "auth_required":
            log(f"  FATAL: expected auth_required, got: {hmsg}")
            await ws.close()
            await session.close()
            return None
        await ws.send_json({"type": "auth", "access_token": token})
        authed = await ws.receive()
        amsg = json.loads(authed.data)
        if amsg.get("type") != "auth_ok":
            log(f"  FATAL: auth failed: {amsg}")
            await ws.close()
            await session.close()
            return None
        log(f"  authed against HA {amsg.get('ha_version', '?')}")
    except Exception as e:
        log(f"  FATAL: auth handshake exception: {e}")
        await ws.close()
        await session.close()
        return None

    # Stash the session on the ws so the caller can close both
    setattr(ws, "_bs_session", session)
    return ws


async def close_authed(ws: aiohttp.ClientWebSocketResponse) -> None:
    session = getattr(ws, "_bs_session", None)
    try:
        await ws.close()
    finally:
        if session is not None:
            await session.close()


async def list_resources(
    ws: aiohttp.ClientWebSocketResponse, msg_id: int
) -> list[dict[str, Any]]:
    r = await ws_call(ws, {"type": "lovelace/resources"}, msg_id)
    if not r.get("success"):
        return []
    return r.get("result") or []


async def list_dashboards(
    ws: aiohttp.ClientWebSocketResponse, msg_id: int
) -> list[dict[str, Any]]:
    r = await ws_call(ws, {"type": "lovelace/dashboards"}, msg_id)
    if not r.get("success"):
        return []
    return r.get("result") or []


async def install(ws: aiohttp.ClientWebSocketResponse, version: str, log) -> int:
    """Register resource + dashboard. Returns exit code."""
    msg_id = 1
    target_url = launcher_url(version)

    log(f"  target resource url: {target_url}")
    resources = await list_resources(ws, msg_id)
    msg_id += 1

    # Find stale broadsheet resources (different version) — delete
    # so the user's Lovelace doesn't accumulate orphan entries on
    # upgrade. Keep the one matching our current version (no-op).
    keep = None
    for r in resources:
        url = r.get("url", "")
        if not url.startswith(LAUNCHER_URL_PREFIX):
            continue
        if url == target_url:
            keep = r
            log(f"  current resource already registered (id={r.get('id')})")
            continue
        # Stale — delete
        rid = r.get("id")
        if not rid:
            continue
        r_del = await ws_call(
            ws,
            {"type": "lovelace/resources/delete", "resource_id": rid},
            msg_id,
        )
        msg_id += 1
        if r_del.get("success"):
            log(f"  removed stale resource: {url}")
        else:
            log(f"  WARN: couldn't remove stale resource {url}: {r_del.get('error')}")

    if keep is None:
        # Create the new resource
        r_create = await ws_call(
            ws,
            {
                "type": "lovelace/resources/create",
                "url": target_url,
                "res_type": "module",
            },
            msg_id,
        )
        msg_id += 1
        if not r_create.get("success"):
            err = r_create.get("error", {}).get("message", "unknown")
            log(f"  FATAL: couldn't create resource: {err}")
            log(f"         (often means Lovelace mode = YAML, not storage)")
            log(f"         add manually: resources: [{{url: {target_url}, type: module}}]")
            return 1
        log(f"  registered new resource: {target_url}")

    # Dashboard
    dashboards = await list_dashboards(ws, msg_id)
    msg_id += 1
    existing = next(
        (d for d in dashboards if d.get("url_path") == DASHBOARD["url_path"]),
        None,
    )
    if existing is None:
        r_dash = await ws_call(
            ws,
            {"type": "lovelace/dashboards/create", **DASHBOARD},
            msg_id,
        )
        msg_id += 1
        if not r_dash.get("success"):
            err = r_dash.get("error", {}).get("message", "unknown")
            log(f"  FATAL: couldn't create dashboard: {err}")
            return 1
        log(f"  registered new dashboard: /{DASHBOARD['url_path']}")
    else:
        log(f"  dashboard /{DASHBOARD['url_path']} already exists (id={existing.get('id')})")

    # Always save the config — covers both the just-created case
    # and the "user nuked our config and we want it back" case. The
    # save call is idempotent on identical config.
    r_save = await ws_call(
        ws,
        {
            "type": "lovelace/config/save",
            "url_path": DASHBOARD["url_path"],
            "config": DASHBOARD_CONFIG,
        },
        msg_id,
    )
    msg_id += 1
    if not r_save.get("success"):
        log(f"  WARN: couldn't save dashboard config: {r_save.get('error')}")
    else:
        log("  dashboard config saved")

    return 0


async def uninstall(ws: aiohttp.ClientWebSocketResponse, log) -> int:
    """Remove resource + dashboard. Returns exit code (best-effort)."""
    msg_id = 1
    resources = await list_resources(ws, msg_id)
    msg_id += 1
    for r in resources:
        url = r.get("url", "")
        if not url.startswith(LAUNCHER_URL_PREFIX):
            continue
        rid = r.get("id")
        if not rid:
            continue
        r_del = await ws_call(
            ws,
            {"type": "lovelace/resources/delete", "resource_id": rid},
            msg_id,
        )
        msg_id += 1
        if r_del.get("success"):
            log(f"  removed resource: {url}")
        else:
            log(f"  WARN: couldn't remove resource {url}: {r_del.get('error')}")

    dashboards = await list_dashboards(ws, msg_id)
    msg_id += 1
    for d in dashboards:
        if d.get("url_path") != DASHBOARD["url_path"]:
            continue
        did = d.get("id")
        if not did:
            continue
        r_del = await ws_call(
            ws,
            {"type": "lovelace/dashboards/delete", "dashboard_id": did},
            msg_id,
        )
        msg_id += 1
        if r_del.get("success"):
            log(f"  removed dashboard: /{DASHBOARD['url_path']}")
        else:
            log(f"  WARN: couldn't remove dashboard: {r_del.get('error')}")

    return 0


async def amain(args, log) -> int:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        log("FATAL: SUPERVISOR_TOKEN env var not set — am I running outside an addon?")
        return 1

    log(f"launcher registration: mode={args.mode!r}")

    # Retry connection: HA Core might still be starting when our
    # addon comes up, especially on cold-boot. Try up to DEFAULT_RETRY_S.
    deadline = asyncio.get_event_loop().time() + DEFAULT_RETRY_S
    ws = None
    while ws is None:
        ws = await authed_session(token, log)
        if ws is not None:
            break
        if asyncio.get_event_loop().time() > deadline:
            log(f"  giving up after {DEFAULT_RETRY_S}s retry budget")
            return 1
        await asyncio.sleep(3)

    try:
        if args.mode == "install":
            return await install(ws, args.version, log)
        else:
            return await uninstall(ws, log)
    finally:
        await close_authed(ws)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("install", "uninstall"))
    p.add_argument(
        "--version",
        default=os.environ.get("BROADSHEET_VERSION", "0_0_0"),
        help="addon version; used to version the launcher resource URL",
    )
    args = p.parse_args()

    def log(msg: str) -> None:
        print(f"[register-launcher.py] {msg}", flush=True)

    return asyncio.run(amain(args, log))


if __name__ == "__main__":
    sys.exit(main())
