"""
Unit tests for broadsheet/init/sidebar.py — the HA sidebar takeover
sidecar.

Run with:
    cd broadsheet-addon
    python3 -m pip install pytest pytest-asyncio aiohttp
    python3 -m pytest tests/

These tests mock the HA WS via a fake WebSocket that records sent
messages + plays back a scripted response stream. No real HA needed.

Coverage:
  - mode='on' sends defaultPanel=broadsheet + dockedSidebar=always_hidden
  - mode='off' sends defaultPanel=lovelace + dockedSidebar=docked
  - System users (whose names start "Supervisor") are skipped
  - Per-user write failures don't abort the whole script
  - Auth failure exits non-zero before any user writes
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make broadsheet/init/ importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "broadsheet" / "init"))

import sidebar  # noqa: E402


class FakeWS:
    """
    Mock aiohttp.ClientWebSocketResponse for the takeover sidecar.

    Records every send_json call into `sent`. Plays back replies from
    `reply_script` in order — each receive() pops the next scripted
    message and returns it. Use script_auth_ok() + script_users() +
    script_set_results() to build the script in normal order.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.reply_script: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(dict(payload))

    async def receive(self) -> Any:
        if not self.reply_script:
            raise AssertionError(
                f"FakeWS.receive() with empty script — sent so far: {self.sent}"
            )
        msg = self.reply_script.pop(0)

        class _Frame:
            type = __import__("aiohttp").WSMsgType.TEXT
            data = json.dumps(msg)

        return _Frame()

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "FakeWS":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class FakeSession:
    """Returns the same FakeWS for every ws_connect call in a test."""

    def __init__(self, ws: FakeWS) -> None:
        self._ws = ws

    async def ws_connect(self, *args: Any, **kwargs: Any) -> FakeWS:
        return self._ws

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def script_auth_ok(ws: FakeWS, ha_version: str = "2026.5.1") -> None:
    """Prime the WS with an auth_required → auth_ok handshake."""
    ws.reply_script.append({"type": "auth_required"})
    ws.reply_script.append({"type": "auth_ok", "ha_version": ha_version})


def script_users(ws: FakeWS, users: list[dict[str, Any]], msg_id: int) -> None:
    """Add a `config/auth/list` success response."""
    ws.reply_script.append(
        {"id": msg_id, "type": "result", "success": True, "result": users}
    )


def script_set_result(
    ws: FakeWS, msg_id: int, success: bool = True, error_msg: str = ""
) -> None:
    """Add a `frontend/set_user_data` success or failure response."""
    if success:
        ws.reply_script.append(
            {"id": msg_id, "type": "result", "success": True, "result": None}
        )
    else:
        ws.reply_script.append(
            {
                "id": msg_id,
                "type": "result",
                "success": False,
                "error": {"message": error_msg or "mocked failure"},
            }
        )


@pytest.fixture
def fake_ws(monkeypatch: pytest.MonkeyPatch) -> FakeWS:
    """
    Patch aiohttp.ClientSession to return a SINGLETON session bound
    to one FakeWS. Test primes the ws's reply_script BEFORE calling
    apply_takeover; the production code's ws_connect() returns the
    same ws each time it's called within a test.
    """
    ws = FakeWS()
    # Pre-prime the auth handshake (every test needs it; tests that
    # want auth-fail override this after the fixture returns).
    script_auth_ok(ws)
    sess = FakeSession(ws)

    def _factory(*args: Any, **kwargs: Any) -> FakeSession:
        return sess

    monkeypatch.setattr(sidebar.aiohttp, "ClientSession", _factory)
    return ws


def _capture_log(buf: list[str]):
    def log(m: str) -> None:
        buf.append(m)

    return log


@pytest.mark.asyncio
async def test_mode_on_sends_takeover_keys(fake_ws: FakeWS) -> None:
    """mode='on' writes defaultPanel=broadsheet + dockedSidebar=always_hidden."""
    script_users(fake_ws, [{"id": "alice", "name": "Alice"}], msg_id=1)
    script_set_result(fake_ws, msg_id=2)
    script_set_result(fake_ws, msg_id=3)

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("fake-token", "on", _capture_log(log_buf))
    assert rc == 0

    # First message is the auth handshake reply
    assert fake_ws.sent[0]["type"] == "auth"
    # Then config/auth/list
    assert fake_ws.sent[1]["type"] == "config/auth/list"
    # Then two frontend/set_user_data writes for Alice
    assert fake_ws.sent[2]["type"] == "frontend/set_user_data"
    assert fake_ws.sent[2]["key"] == "defaultPanel"
    assert fake_ws.sent[2]["value"] == "broadsheet"
    assert fake_ws.sent[3]["type"] == "frontend/set_user_data"
    assert fake_ws.sent[3]["key"] == "dockedSidebar"
    assert fake_ws.sent[3]["value"] == "always_hidden"


@pytest.mark.asyncio
async def test_mode_off_sends_rollback_keys(fake_ws: FakeWS) -> None:
    """mode='off' writes defaultPanel=lovelace + dockedSidebar=docked."""
    script_users(fake_ws, [{"id": "alice", "name": "Alice"}], msg_id=1)
    script_set_result(fake_ws, msg_id=2)
    script_set_result(fake_ws, msg_id=3)

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("fake-token", "off", _capture_log(log_buf))
    assert rc == 0
    assert fake_ws.sent[2]["value"] == "lovelace"
    assert fake_ws.sent[3]["value"] == "docked"


@pytest.mark.asyncio
async def test_supervisor_user_is_skipped(fake_ws: FakeWS) -> None:
    """System users (name starts 'Supervisor') don't get takeover applied."""
    script_users(
        fake_ws,
        [
            {"id": "sys-1", "name": "Supervisor"},
            {"id": "alice", "name": "Alice"},
        ],
        msg_id=1,
    )
    # ONLY Alice's two writes — supervisor user is skipped, no replies needed
    script_set_result(fake_ws, msg_id=2)
    script_set_result(fake_ws, msg_id=3)

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("fake-token", "on", _capture_log(log_buf))
    assert rc == 0

    # Exactly 4 messages sent: auth, list, alice-key1, alice-key2.
    # No writes for the Supervisor user.
    assert len(fake_ws.sent) == 4
    set_msgs = [m for m in fake_ws.sent if m["type"] == "frontend/set_user_data"]
    assert len(set_msgs) == 2
    # Skip log line surfaces the supervisor account
    assert any("Supervisor" in line for line in log_buf)


@pytest.mark.asyncio
async def test_per_user_write_failure_doesnt_abort(fake_ws: FakeWS) -> None:
    """If one user's write fails, the script logs + returns success overall."""
    script_users(fake_ws, [{"id": "alice", "name": "Alice"}], msg_id=1)
    # First write fails, second succeeds
    script_set_result(fake_ws, msg_id=2, success=False, error_msg="schema validation")
    script_set_result(fake_ws, msg_id=3, success=True)

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("fake-token", "on", _capture_log(log_buf))
    # Partial success is still rc=0 — we processed at least one user
    assert rc == 0
    # FAILED line surfaced in logs
    assert any("FAILED" in line for line in log_buf)


@pytest.mark.asyncio
async def test_auth_failure_exits_nonzero(fake_ws: FakeWS) -> None:
    """If HA refuses auth, exit code is non-zero before any user writes."""
    # Override the auth_ok reply with an auth_invalid
    fake_ws.reply_script.clear()
    fake_ws.reply_script.append({"type": "auth_required"})
    fake_ws.reply_script.append({"type": "auth_invalid", "message": "bad token"})

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("bad-token", "on", _capture_log(log_buf))
    assert rc == 1
    # No config/auth/list call should have been sent
    assert not any(m["type"] == "config/auth/list" for m in fake_ws.sent)
    # FATAL log line
    assert any("FATAL" in line for line in log_buf)


@pytest.mark.asyncio
async def test_zero_users_exits_one(fake_ws: FakeWS) -> None:
    """Edge case: HA returns 0 users (impossible in practice) → exit 1."""
    script_users(fake_ws, [], msg_id=1)

    log_buf: list[str] = []
    rc = await sidebar.apply_takeover("fake-token", "on", _capture_log(log_buf))
    assert rc == 1
