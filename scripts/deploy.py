#!/usr/bin/env python3
"""
Deploy the broadsheet add-on to the production canary HA.

Drives HA's WS `supervisor/api`: /store/reload (pick up the new GHCR
image tag), then /addons/<slug>/update, then polls /addons/<slug>/info
until the live version matches the target.

The update call can drop or hang mid-operation — never trust its
return; the poll loop is the source of truth.

Usage:
    python scripts/deploy.py <HA_LONG_LIVED_TOKEN> <TARGET_VERSION>

Token is passed as argv (never committed). Get it from Harold Road's
.env (HA_TOKEN).
"""
import asyncio
import json
import sys
import time

import websockets

HA_WS = "ws://homeassistant.local:8123/api/websocket"
SLUG = "68fa04fc_broadsheet"


async def main() -> int:
    if len(sys.argv) < 3:
        print("usage: deploy.py <HA_TOKEN> <TARGET_VERSION>")
        return 2
    token, target = sys.argv[1], sys.argv[2]

    async with websockets.connect(HA_WS, max_size=None, ping_interval=None) as ws:
        await ws.recv()  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_ok":
            print("AUTH FAILED:", hello)
            return 1
        print("auth ok — HA", hello.get("ha_version"))

        seq = [0]

        async def cmd(payload: dict, timeout: float = 120) -> dict:
            seq[0] += 1
            mid = seq[0]
            payload["id"] = mid
            await ws.send(json.dumps(payload))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time()))
                r = json.loads(raw)
                if r.get("id") == mid and r.get("type") == "result":
                    return r
            return {"success": False, "error": "timeout"}

        async def info() -> dict:
            # `supervisor/api` returns the add-on data at `result` directly
            # (not wrapped in `result.data` — that's the raw supervisor REST
            # shape, which the WS command unwraps for us).
            r = await cmd(
                {"type": "supervisor/api", "endpoint": f"/addons/{SLUG}/info", "method": "get"},
                timeout=30,
            )
            return r.get("result", {}) or {}

        r = await cmd(
            {"type": "supervisor/api", "endpoint": "/store/reload", "method": "post"}, timeout=90
        )
        print("store/reload:", r.get("success"), r.get("error") or "")

        d = await info()
        print(
            f"before: version={d.get('version')} latest={d.get('version_latest')} "
            f"update_available={d.get('update_available')}"
        )

        print(f"updating -> {target} ...")
        try:
            r = await cmd(
                {"type": "supervisor/api", "endpoint": f"/addons/{SLUG}/update", "method": "post"},
                timeout=300,
            )
            print("update call:", r.get("success"), r.get("error") or "")
        except Exception as e:  # noqa: BLE001
            print("update call raised (expected sometimes — re-verifying):", e)

        for _ in range(40):
            await asyncio.sleep(4)
            try:
                d = await info()
            except Exception as e:  # noqa: BLE001
                print("  (info retry)", e)
                continue
            print(
                f"  version={d.get('version')} state={d.get('state')} "
                f"update_available={d.get('update_available')}"
            )
            if d.get("version") == target and d.get("state") == "started":
                print("DEPLOYED OK")
                return 0
        print("VERIFY TIMEOUT — check the add-on manually")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
