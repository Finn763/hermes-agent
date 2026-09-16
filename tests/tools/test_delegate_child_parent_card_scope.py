"""A delegate_task child must never close its PARENT's Kanban card.

The in-process child shares ``os.environ`` with the parent worker, so it inherits
``HERMES_KANBAN_TASK`` still naming the parent's task. From inside the ownership
guard (``tid == env_tid``) a bare ``kanban_complete()`` therefore looks like a
legal self-scoped call. Task identity has to come from the delegated-child
lineage (ContextVar/marker + toolset strip), not from the shared env var.

Protective surface in the same file: the parent worker still completes its own
card, and a delegation outside any Kanban context behaves exactly as before.
"""
from __future__ import annotations

import json

from tests.tools.test_delegate_kanban_isolation import _make_running_kanban_task


class _Parent:
    """Minimal parent-worker double: enough surface for ``_run_single_child``."""

    _current_task_id = None
    _delegate_depth = 0
    _subagent_id = None
    session_id = "parent-worker-session"
    model = "test-model"

    def _touch_activity(self, _desc):
        return None


class _Child:
    """Child double that calls ``kanban_complete`` exactly like a model would."""

    tool_progress_callback = None
    _delegate_saved_tool_names = []
    _credential_pool = None
    _subagent_id = "sa-scope"
    _delegate_depth = 1
    _parent_subagent_id = None
    model = "test-model"
    session_id = "child-session"
    session_prompt_tokens = 0
    session_completion_tokens = 0
    session_estimated_cost_usd = 0.0
    session_reasoning_tokens = 0

    def get_activity_summary(self):
        return {"api_call_count": 0, "max_iterations": 1, "current_tool": None}

    def run_conversation(self, user_message, task_id, **_kwargs):
        import tools.kanban_tools  # noqa: F401 - lazily registers the kanban tools
        from tools.registry import registry

        # No task_id: this is the exact call shape that used to resolve to the
        # parent's id via ``_default_task_id``'s env fallback.
        attempted = registry.dispatch("kanban_complete", {"summary": "child closed the parent"})
        return {"final_response": str(attempted), "completed": True, "api_calls": 0, "messages": []}

    def close(self):
        return None


def _run_delegated_child(parent):
    from tools import delegate_tool

    return delegate_tool._run_single_child(0, "review only", _Child(), parent)


def test_worker_child_cannot_complete_the_parent_card(monkeypatch, tmp_path):
    """The reported bug: child inherits the parent's task id and closes its card."""
    kb, tid, workspace, _attachments = _make_running_kanban_task(monkeypatch, tmp_path)
    from hermes_cli import kanban_db_connect as kbc

    parent = _Parent()
    parent._current_task_id = tid
    result = _run_delegated_child(parent)

    conn = kbc.connect()
    try:
        task = kb.get_task(conn, tid)
        run = kb.latest_run(conn, tid)
    finally:
        conn.close()

    # The refusal is the fix; a bare "ok" here means the parent card was closed.
    assert "delegate_task child" in result["summary"], result
    assert json.loads(result["summary"])["error"], result
    assert task.status == "running", f"parent card was completed by its child: {task.status}"
    assert run.status == "running", run.status
    assert workspace.is_dir(), "parent scratch workspace was deleted"


def test_child_inherits_neither_kanban_identity_nor_toolset(monkeypatch):
    """Layer 1: even a parent that holds the kanban toolset hands none of it down."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_parent")
    from tools import delegate_tool

    parent = _Parent()
    parent.enabled_toolsets = ["terminal", "kanban"]
    parent.valid_tool_names = {"terminal", "kanban_complete", "kanban_comment"}
    enabled, disabled = delegate_tool._resolve_child_toolsets(parent, None, "leaf")

    assert "kanban" not in enabled, enabled
    assert "kanban" in disabled, disabled


def test_worker_child_does_not_heartbeat_the_parent_card(monkeypatch, tmp_path):
    """The same inherited-identity class, non-terminal path: every activity tick
    inside a delegate child used to reach the PARENT's card through the shared
    ``HERMES_KANBAN_TASK``/``CLAIM_LOCK`` env and extend its claim."""
    kb, tid, _workspace, _attachments = _make_running_kanban_task(monkeypatch, tmp_path)
    from agent.delegation_context import delegated_child_context
    from hermes_cli import kanban_db_connect as kbc
    from tools import kanban_tools

    def _claim_expires():
        conn = kbc.connect()
        try:
            return (
                conn.execute("SELECT claim_expires FROM tasks WHERE id = ?", (tid,)).fetchone()[0],
                conn.execute("SELECT claim_expires FROM task_runs WHERE task_id = ?", (tid,)).fetchone()[0],
            )
        finally:
            conn.close()

    conn = kbc.connect()
    try:
        monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", kb.get_task(conn, tid).claim_lock)
        # Rewind both rows so any forward movement is unambiguous.
        conn.execute("UPDATE tasks SET claim_expires = 1 WHERE id = ?", (tid,))
        conn.execute("UPDATE task_runs SET claim_expires = 1 WHERE task_id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(kanban_tools, "_auto_heartbeat_last_attempt", 0.0)

    with delegated_child_context("child-heartbeat"):
        assert kanban_tools.heartbeat_current_worker_from_env() is False
    assert _claim_expires() == (1, 1), "the child extended the parent's claim"

    # Protective surface: the owner's own tick still lands, and the refused child
    # tick did not consume the shared 60s rate-limit slot.
    assert kanban_tools.heartbeat_current_worker_from_env() is True
    assert _claim_expires()[0] > 1


def test_parent_worker_still_completes_its_own_card(monkeypatch, tmp_path):
    """Protective surface: the worker's own self-scoped call is untouched."""
    kb, tid, workspace, _attachments = _make_running_kanban_task(monkeypatch, tmp_path)
    from hermes_cli import kanban_db_connect as kbc
    import tools.kanban_tools  # noqa: F401 - registers the kanban tools
    from tools.registry import registry

    payload = json.loads(registry.dispatch("kanban_complete", {"summary": "parent handoff", "result": "done"}))
    assert payload.get("ok") is True, payload

    conn = kbc.connect()
    try:
        task = kb.get_task(conn, tid)
    finally:
        conn.close()
    assert task.status == "done"


def test_delegation_outside_kanban_context_is_unchanged(monkeypatch, tmp_path):
    """No HERMES_KANBAN_TASK anywhere: ordinary delegation still runs, and a bare
    ``kanban_complete`` from that child is refused by the pre-existing
    "not a run owner" gate rather than resolving some task from the env."""
    for var in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_DELEGATED_CHILD_CONTEXT"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    result = _run_delegated_child(_Parent())

    assert result["status"] == "completed", result
    assert json.loads(result["summary"])["error"].startswith("kanban_complete refused: delegate_task child")
