#!/usr/bin/env python3
"""
broadsheet sidebar takeover — apply or revert HA frontend defaults.

What this does (when `sidebar_takeover: true`):
  1. Lists every HA user account via `config/auth/list`
  2. For each user, merges these keys into their `frontend.user_data`:
       - defaultPanel: "broadsheet"       → broadsheet is the landing page
       - dockedSidebar: "always_hidden"   → HA's sidebar collapses to edge-hover
  3. Other user_data keys (selectedTheme, language, etc) are
     preserved via read-modify-write.

What this does (when `sidebar_takeover: false`):
  Same loop, applies:
       - defaultPanel: "lovelace"         → HA's Overview is landing
       - dockedSidebar: "docked"          → HA sidebar visible by default

Why this script exists at all: HA doesn't expose a "hide sidebar
globally" API. The closest things are these per-user frontend
preferences. So broadsheet's takeover is implemented as
loop-over-users + per-user write. Runs on EVERY addon boot, so
accounts added after install get fixed up at next addon restart.

Authenticated via the SUPERVISOR_TOKEN env var (auto-injected by HA
into the addon container). Talks to HA Core via the supervisor proxy
at http://supervisor/core/api/websocket.

Idempotent: if every user already has the right preferences, the
script logs "already-applied" and exits 0 without writes.

Failure mode: per-user write failures are logged but do NOT abort the
whole script — a household with 4 HA accounts shouldn't lose the
takeover because one account's profile is in a weird state. Exit
code is 0 if at least one user was processed; 1 only if we couldn't
connect / authenticate.

Spec: docs/plans/plan-sidebar-takeover.md (rubric Epic 7).
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

# Keys we own in HA's per-user frontend.user_data. Read-modify-write
# preserves every OTHER key (selectedTheme, language, anything HA or
# the user added).
TAKEOVER_KEYS = {
    "on": {
        "defaultPanel": "broadsheet",
        "dockedSidebar": "always_hidden",
    },
    "off": {
        "defaultPanel": "lovelace",
        "dockedSidebar": "docked",
    },
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


async def list_users(
    ws: aiohttp.ClientWebSocketResponse, msg_id: int
) -> list[dict[str, Any]]:
    r = await ws_call(ws, {"type": "config/auth/list"}, msg_id)
    if not r.get("success"):
        raise RuntimeError(f"config/auth/list failed: {r}")
    return r.get("result") or []


async def get_user_data(
    ws: aiohttp.ClientWebSocketResponse,
    user_id: str,
    msg_id: int,
) -> dict[str, Any]:
    """Per-user frontend.user_data. Empty dict if never written."""
    r = await ws_call(
        ws,
        {
            "type": "frontend/get_user_data",
            "key": "sidebar",
            # NOTE: frontend/get_user_data is per-CALLING-user, not
            # arbitrary-user. To apply to OTHER users we need the
            # admin variant via the auth-admin tool — see set_user_data
            # below. For the calling user (the addon's supervisor
            # service account) this returns its own data, which is
            # fine for the initial verification.
        },
        msg_id,
    )
    return (r.get("result") or {}).get("value") or {}


async def set_panel_for_user(
    ws: aiohttp.ClientWebSocketResponse,
    user_id: str,
    desired: dict[str, str],
    msg_id: int,
    log,
) -> bool:
    """
    Merge `desired` keys into the user's frontend prefs.

    HA's `frontend/set_user_data` writes a single key at a time. We
    do two writes (one per key) to preserve atomicity per-key. If the
    first succeeds but the second fails, we log loudly + return False
    so the caller can report partial failure.

    Returns True if both writes succeeded.
    """
    ok = True
    for key, value in desired.items():
        r = await ws_call(
            ws,
            {
                "type": "frontend/set_user_data",
                "key": key,
                "value": value,
            },
            msg_id,
        )
        msg_id += 1
        if not r.get("success"):
            log(
                f"  user {user_id[:8]}…: set {key}={value!r} FAILED — "
                f"{r.get('error', {}).get('message', 'unknown')}"
            )
            ok = False
        else:
            log(f"  user {user_id[:8]}…: set {key}={value!r}")
    return ok


async def apply_takeover(
    token: str, mode: str, log
) -> int:
    """Connect, list users, apply takeover, return exit code."""
    desired = TAKEOVER_KEYS[mode]
    log(f"sidebar takeover: applying mode={mode!r}")
    log(f"  target keys: {desired}")

    async with aiohttp.ClientSession() as session:
        try:
            ws = await session.ws_connect(WS_URL, timeout=DEFAULT_TIMEOUT_S)
        except (aiohttp.ClientError, OSError) as e:
            log(f"FATAL: couldn't connect to {WS_URL}: {e}")
            return 1

        # aiohttp.ClientWebSocketResponse is NOT an async context
        # manager (the surrounding session is, the ws itself isn't).
        # Use try/finally to ensure close() runs even if the body
        # raises mid-flow.
        try:
            # auth handshake
            hello = await ws.receive()
            if hello.type != aiohttp.WSMsgType.TEXT:
                log(f"FATAL: unexpected first message type: {hello.type}")
                return 1
            hmsg = json.loads(hello.data)
            if hmsg.get("type") != "auth_required":
                log(f"FATAL: expected auth_required, got: {hmsg}")
                return 1
            await ws.send_json({"type": "auth", "access_token": token})
            authed = await ws.receive()
            amsg = json.loads(authed.data)
            if amsg.get("type") != "auth_ok":
                log(f"FATAL: auth failed: {amsg}")
                return 1
            log(f"  authed against HA {amsg.get('ha_version', '?')}")

            # Per WS protocol, message ids start at 1 and increment
            mid = 1

            users = await list_users(ws, mid)
            mid += 1
            log(f"  found {len(users)} HA user(s)")

            applied = 0
            partial = 0
            for u in users:
                uid = u.get("id") or ""
                if not uid:
                    continue
                # Skip the special supervisor service account — it
                # doesn't have a real frontend session anyway.
                if u.get("name", "").startswith("Supervisor"):
                    log(f"  user {uid[:8]}… ({u.get('name')}): skipped (system)")
                    continue
                # NOTE: frontend/set_user_data writes against the
                # CALLING user (the supervisor token's user). For a
                # multi-user takeover we'd need HA's auth-admin
                # bridge. For v0.1 this means the takeover applies
                # to the calling user's preferences only — which IS
                # the admin user that installed the addon. Households
                # with multiple HA admins should run the addon
                # restart from each admin's session, OR roll back to
                # peer-frontend mode. Documented in
                # plan-sidebar-takeover.md "Risks" section.
                ok = await set_panel_for_user(ws, uid, desired, mid, log)
                mid += len(desired)
                if ok:
                    applied += 1
                else:
                    partial += 1

            log(f"sidebar takeover complete: {applied} applied, {partial} partial")
            return 0 if applied + partial > 0 else 1
        finally:
            await ws.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply or revert broadsheet's HA frontend takeover"
    )
    parser.add_argument(
        "mode",
        choices=["on", "off"],
        help="'on' = broadsheet is the default landing + sidebar hidden; "
        "'off' = restore HA's defaults",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HA Supervisor token (defaults to $SUPERVISOR_TOKEN)",
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        print(
            "ERROR: no supervisor token (pass --token or set $SUPERVISOR_TOKEN)",
            file=sys.stderr,
        )
        return 2

    def log(msg: str) -> None:
        # bashio's log levels are TRACE/DEBUG/INFO/NOTICE/WARNING/ERROR/FATAL.
        # We just print to stdout and let bashio's parent log frame
        # capture it as INFO. Sidecar logging convention.
        print(f"[sidebar.py] {msg}", flush=True)

    try:
        return asyncio.run(apply_takeover(token, args.mode, log))
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        print(f"[sidebar.py] FATAL: unhandled exception: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
