"""Teams mention gating and observed group context (Telegram parity).

Once an app carries resource-specific consent (``ChannelMessage.Read.Group`` / ``ChatMessage.Read.Chat``)
Teams delivers every message in the conversation, so ``require_mention`` has to gate them;
``observe_unmentioned_group_messages`` keeps the filtered ones as session context — the same pair of
settings Telegram already honours — instead of dropping the conversation on the floor.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gateway.config import Platform, PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_teams = load_plugin_adapter("teams")
TeamsAdapter = _teams.TeamsAdapter

_BOT_ID = "bot-app-id"
_CHANNEL_ID = "19:abc@thread.tacv2"


def _make_adapter(**extra):
    adapter = TeamsAdapter(PlatformConfig(enabled=True, extra={
        "client_id": _BOT_ID, "client_secret": "secret", "tenant_id": "tenant", **extra}))
    adapter._app = SimpleNamespace(id=_BOT_ID)
    adapter.handle_message = AsyncMock()
    return adapter


def _activity(
    *, text="side chatter", conversation_type="channel", activity_id="activity-1",
    entities=None, conversation_id=_CHANNEL_ID,
):
    return SimpleNamespace(
        text=text,
        id=activity_id,
        from_=SimpleNamespace(id="user-123", aad_object_id="aad-456", name="Test User"),
        conversation=SimpleNamespace(
            id=conversation_id, conversation_type=conversation_type,
            name="Test Channel", tenant_id="tenant-789"),
        attachments=[],
        entities=entities or [])


def _ctx(activity):
    return SimpleNamespace(activity=activity, conversation_ref=MagicMock())


def _mention_entity(mentioned_id=_BOT_ID):
    """A Bot Framework ``mention`` entity, as the Teams SDK models it."""
    return [{"type": "mention", "mentioned": {"id": mentioned_id, "name": "Hermes"}, "text": "<at>Hermes</at>"}]


class _FakeSessionEntry:
    session_id = "teams-shared-session"


class _FakeSessionStore:
    def __init__(self):
        self.sources = []
        self.messages = []

    def get_or_create_session(self, source):
        self.sources.append(source)
        return _FakeSessionEntry()

    def append_to_transcript(self, session_id, message, skip_db=False):
        self.messages.append((session_id, message, skip_db))


def _observed_rows(store):
    return [message for _sid, message, _skip in store.messages]


# ---------------------------------------------------------------------------
# Red → green: an unmentioned channel message is kept as context, not dropped
# ---------------------------------------------------------------------------

def test_unmentioned_channel_message_is_observed_without_dispatching():
    """Today the whole conversation is discarded (nothing is stored and the agent has no context)."""

    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity(text="did anyone fix the build?")))

        adapter.handle_message.assert_not_awaited()
        assert len(store.messages) == 1
        session_id, row, skip_db = store.messages[0]
        assert session_id == "teams-shared-session"
        assert skip_db is False
        assert row["role"] == "user"
        assert row["content"] == "[Test User|aad-456]\ndid anyone fix the build?"
        assert row["observed"] is True
        assert row["message_id"] == "activity-1"
        # Shared chat-scoped source: the later mention turn must land in this same session.
        assert store.sources[0].chat_id == _CHANNEL_ID
        assert store.sources[0].chat_type == "channel"
        assert store.sources[0].user_id is None
        assert store.sources[0].user_name is None

    asyncio.run(_run())


def test_group_chat_message_is_observed_like_a_channel():
    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity(conversation_type="groupChat", conversation_id="19:cd@thread.v2")))

        adapter.handle_message.assert_not_awaited()
        assert _observed_rows(store)[0]["content"].endswith("side chatter")
        assert store.sources[0].chat_type == "group"

    asyncio.run(_run())


def test_mention_entity_for_another_bot_still_counts_as_unmentioned():
    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity(entities=_mention_entity("some-other-bot"))))

        adapter.handle_message.assert_not_awaited()
        assert len(store.messages) == 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# A later mention sees the observed context
# ---------------------------------------------------------------------------

def test_mention_turn_is_attributed_and_shares_the_observed_session():
    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)

        await adapter._on_message(_ctx(_activity(text="<at>Hermes</at> what did we decide?", entities=_mention_entity())))

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.call_args[0][0]
        assert event.text == "[Test User|aad-456]\nwhat did we decide?"
        assert event.source.user_id is None
        assert event.source.user_name is None
        assert "observed Teams group context" in event.channel_prompt
        assert "current new message" in event.channel_prompt

    asyncio.run(_run())


def test_gateway_splits_observed_rows_into_a_context_only_block():
    """The Teams channel prompt carries the marker gateway/run.py keys on."""

    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store
        await adapter._on_message(_ctx(_activity(text="did anyone fix the build?")))
        await adapter._on_message(_ctx(_activity(text="<at>Hermes</at> status?", activity_id="activity-2",
                                                entities=_mention_entity())))
        current = adapter.handle_message.call_args[0][0]

        from gateway.run import _build_gateway_agent_history

        history = [*_observed_rows(store), {"role": "user", "content": current.text}]
        agent_history, observed_context = _build_gateway_agent_history(
            history, channel_prompt=current.channel_prompt)

        assert observed_context == "[Test User|aad-456]\ndid anyone fix the build?"
        assert [m["content"] for m in agent_history] == ["[Test User|aad-456]\nstatus?"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Protection: defaults and the observe-off path keep today's behaviour
# ---------------------------------------------------------------------------

def test_observe_defaults_off_and_unmentioned_messages_still_dispatch():
    """No flags configured: an unmentioned channel message dispatches exactly as before."""

    async def _run():
        adapter = _make_adapter()
        assert adapter._teams_observe_unmentioned_group_messages() is False
        assert adapter._teams_require_mention() is False
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity()))

        adapter.handle_message.assert_awaited_once()
        assert store.messages == []

    asyncio.run(_run())


def test_observe_without_require_mention_keeps_dispatching():
    """Telegram parity: observation only applies to messages the mention gate would skip."""

    async def _run():
        adapter = _make_adapter(observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity()))

        adapter.handle_message.assert_awaited_once()
        assert store.messages == []
        # No observed context exists, so the turn keeps its own sender identity.
        assert adapter.handle_message.call_args[0][0].source.user_id == "aad-456"

    asyncio.run(_run())


def test_observe_off_drops_unmentioned_messages_when_mention_is_required():
    async def _run():
        adapter = _make_adapter(require_mention=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity()))

        adapter.handle_message.assert_not_awaited()
        assert store.messages == []

    asyncio.run(_run())


def test_direct_messages_ignore_the_mention_gate():
    async def _run():
        adapter = _make_adapter(require_mention=True, observe_unmentioned_group_messages=True)
        store = _FakeSessionStore()
        adapter._session_store = store

        await adapter._on_message(_ctx(_activity(conversation_type="personal", conversation_id="19:a@thread.v2")))

        adapter.handle_message.assert_awaited_once()
        assert store.messages == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Config plumbing
# ---------------------------------------------------------------------------

def test_config_bridges_observe_flag_to_teams(monkeypatch, tmp_path):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "platforms:\n"
        "  teams:\n"
        "    require_mention: true\n"
        "    observe_unmentioned_group_messages: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    from gateway.config import load_gateway_config

    config = load_gateway_config()
    assert config is not None
    teams_cfg = config.platforms.get(Platform("teams"))
    assert teams_cfg is not None
    assert teams_cfg.extra.get("require_mention") is True
    assert teams_cfg.extra.get("observe_unmentioned_group_messages") is True


def test_bridged_keys_keep_the_flag_off_other_platforms():
    from gateway.config_loader import _bridged_keys

    section = {"observe_unmentioned_group_messages": True}
    assert _bridged_keys(Platform.TELEGRAM, section, {})["observe_unmentioned_group_messages"] is True
    assert "observe_unmentioned_group_messages" not in _bridged_keys(Platform.DISCORD, section, {})


def test_env_var_enables_observation(monkeypatch):
    monkeypatch.setenv("TEAMS_OBSERVE_UNMENTIONED_GROUP_MESSAGES", "true")
    adapter = _make_adapter(require_mention=True)
    assert adapter._teams_observe_unmentioned_group_messages() is True

    monkeypatch.setenv("TEAMS_REQUIRE_MENTION", "true")
    assert adapter._teams_require_mention() is True
