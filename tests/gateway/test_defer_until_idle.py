"""Tests for issue #116290: busy_policy="defer_until_idle" for session commands.

A busy /compress, /undo, /retry or /save must be authorized and recorded as a
typed deferred command (never touching the running turn), acknowledged as
scheduled, and executed after the turn commits -- with deterministic ordering,
duplicate coalescing, and honest failure/restart surfacing.
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

_tg = types.ModuleType("telegram")
_tg.constants = types.ModuleType("telegram.constants")
_ct = MagicMock()
_ct.SUPERGROUP = "supergroup"
_ct.GROUP = "group"
_ct.PRIVATE = "private"
_tg.constants.ChatType = _ct
sys.modules.setdefault("telegram", _tg)
sys.modules.setdefault("telegram.constants", _tg.constants)
sys.modules.setdefault("telegram.ext", types.ModuleType("telegram.ext"))

from gateway.platforms.base import MessageEvent, MessageType, Platform, SessionSource
from hermes_cli.commands import VALID_BUSY_POLICIES, resolve_command


def _make_source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_type="dm",
        user_id="user1",
    )


def _make_event(text):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(),
        message_id="msg1",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._draining = False
    runner._restart_requested = False
    return runner


class TestDeferUntilIdleRegistry:
    def test_policy_is_known(self):
        assert "defer_until_idle" in VALID_BUSY_POLICIES

    @pytest.mark.parametrize("name", ["compress", "undo", "retry", "save"])
    def test_session_commands_defer(self, name):
        assert resolve_command(name).busy_policy == "defer_until_idle"

    def test_compact_alias_follows_compress(self):
        assert resolve_command("compact").busy_policy == "defer_until_idle"

    @pytest.mark.parametrize("name", ["model", "resume", "fast", "reasoning", "title"])
    def test_routing_and_immediate_commands_untouched(self, name):
        assert resolve_command(name).busy_policy != "defer_until_idle"


class TestDeferUntilIdleDispatch:
    @pytest.mark.asyncio
    async def test_busy_compress_scheduled_not_rejected(self):
        runner = _make_runner()
        agent = MagicMock()
        runner._session_state("sk1").turn.agent = agent
        event = _make_event("/compress here 5")

        result = await runner._dispatch_busy_slash_command(
            event, resolve_command("compress"), "sk1", event.source
        )

        assert "scheduled" in result.lower()
        assert "/compress" in result
        agent.interrupt.assert_not_called()
        entries = runner._session_state("sk1").conversation.deferred_commands
        assert [(e["command"], e["args"]) for e in entries] == [("compress", "here 5")]
        assert entries[0]["event"] is event

    @pytest.mark.asyncio
    async def test_duplicate_defer_coalesces(self):
        runner = _make_runner()
        runner._session_state("sk1").turn.agent = MagicMock()
        cmd = resolve_command("undo")

        first = await runner._dispatch_busy_slash_command(
            _make_event("/undo"), cmd, "sk1", _make_source()
        )
        second = await runner._dispatch_busy_slash_command(
            _make_event("/undo"), cmd, "sk1", _make_source()
        )

        assert "scheduled" in first.lower()
        assert "already scheduled" in second.lower()
        assert len(runner._session_state("sk1").conversation.deferred_commands) == 1

    @pytest.mark.asyncio
    async def test_distinct_args_queue_separately(self):
        runner = _make_runner()
        runner._session_state("sk1").turn.agent = MagicMock()
        cmd = resolve_command("undo")

        await runner._dispatch_busy_slash_command(_make_event("/undo"), cmd, "sk1", _make_source())
        await runner._dispatch_busy_slash_command(_make_event("/undo 3"), cmd, "sk1", _make_source())

        entries = runner._session_state("sk1").conversation.deferred_commands
        assert [e["args"] for e in entries] == ["", "3"]

    @pytest.mark.asyncio
    async def test_accept_refuses_while_draining(self):
        runner = _make_runner()
        runner._draining = True
        runner._session_state("sk1").turn.agent = MagicMock()

        result = await runner._dispatch_busy_slash_command(
            _make_event("/save md"), resolve_command("save"), "sk1", _make_source()
        )

        assert "restart" in result.lower() or "shut" in result.lower()
        assert runner._session_state("sk1").conversation.deferred_commands == []


class TestDeferUntilIdleDrain:
    def _seed(self, runner, *names):
        state = runner._session_state("sk1")
        for name in names:
            event = _make_event(f"/{name}")
            state.conversation.deferred_commands.append(
                {"command": name, "args": "", "event": event, "source": event.source}
            )
        return state

    @pytest.mark.asyncio
    async def test_drain_executes_in_order_and_delivers(self):
        runner = _make_runner()
        adapter = MagicMock()
        adapter.send = AsyncMock()
        runner._adapter_for_source = lambda _source: adapter
        runner._thread_metadata_for_source = lambda _source, *a, **k: {"thread": "t"}
        calls = []
        runner._handle_compress_command = AsyncMock(
            side_effect=lambda _e: calls.append("compress") or "compressed-ok"
        )
        runner._handle_save_command = AsyncMock(
            side_effect=lambda _e: calls.append("save") or "saved-ok"
        )
        self._seed(runner, "compress", "save")

        await runner._drain_deferred_commands("sk1")

        assert calls == ["compress", "save"]
        assert runner._session_state("sk1").conversation.deferred_commands == []
        sent = [c.args[1] for c in adapter.send.call_args_list]
        assert sent == ["compressed-ok", "saved-ok"]
        for c in adapter.send.call_args_list:
            assert c.args[0] == "123"
            assert c.kwargs["metadata"] == {"thread": "t"}

    @pytest.mark.asyncio
    async def test_drain_failure_delivered_honestly_and_continues(self):
        runner = _make_runner()
        adapter = MagicMock()
        adapter.send = AsyncMock()
        runner._adapter_for_source = lambda _source: adapter
        runner._thread_metadata_for_source = lambda _source, *a, **k: None
        runner._handle_compress_command = AsyncMock(side_effect=RuntimeError("boom"))
        runner._handle_save_command = AsyncMock(return_value="saved-ok")
        self._seed(runner, "compress", "save")

        await runner._drain_deferred_commands("sk1")

        assert runner._session_state("sk1").conversation.deferred_commands == []
        sent = [c.args[1] for c in adapter.send.call_args_list]
        assert len(sent) == 2
        assert "/compress" in sent[0] and "boom" in sent[0]
        assert sent[1] == "saved-ok"

    @pytest.mark.asyncio
    async def test_drain_skipped_while_running_preserves_entries(self):
        runner = _make_runner()
        runner._session_state("sk1").turn.agent = MagicMock()
        runner._handle_compress_command = AsyncMock()
        self._seed(runner, "compress")

        await runner._drain_deferred_commands("sk1")

        runner._handle_compress_command.assert_not_called()
        assert len(runner._session_state("sk1").conversation.deferred_commands) == 1

    @pytest.mark.asyncio
    async def test_drain_while_draining_notifies_without_executing(self):
        runner = _make_runner()
        runner._draining = True
        adapter = MagicMock()
        adapter.send = AsyncMock()
        runner._adapter_for_source = lambda _source: adapter
        runner._thread_metadata_for_source = lambda _source, *a, **k: None
        runner._handle_compress_command = AsyncMock()
        self._seed(runner, "compress")

        await runner._drain_deferred_commands("sk1")

        runner._handle_compress_command.assert_not_called()
        assert runner._session_state("sk1").conversation.deferred_commands == []
        sent = adapter.send.call_args_list[0].args[1]
        assert "/compress" in sent
