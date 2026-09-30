"""Warn when an in-session model switch will trigger preflight compression on the next turn."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from agent.model_metadata import MINIMUM_CONTEXT_LENGTH
from hermes_cli.model_switch import ModelSwitchResult, resolve_display_context_length

# The switch summary is where the user learns what the next turn costs. Below the compression
# trigger there is still a cost: the first reply ships the whole history to a route whose prefix
# cache is cold (provider caches are per model/route), so a hosted prefill re-reads it at ~2-5k
# tok/s and the largest sessions feel that as a stall. Flagged once the session is at least halfway
# to the trigger this same summary already quotes. Calibration knob, not a contract.
COLD_READ_THRESHOLD_FRACTION = 0.5


def _append_warning(result: ModelSwitchResult, text: str) -> None:
    if result.warning_message:
        result.warning_message = f"{result.warning_message} | {text}"
    else:
        result.warning_message = text


def _threshold_tokens(compressor: Any, model: str, context_length: int, provider: str = "") -> int:
    """The trigger the compressor WILL use after the switch (cap, model_thresholds and small-window
    floor included), so the warning quotes the real number; duck-typed engines keep the plain ratio."""
    preview = getattr(compressor, "preview_threshold_tokens", None)
    if callable(preview):
        return int(preview(model, context_length, provider))
    return max(int(context_length * float(getattr(compressor, "threshold_percent", 0.5))), MINIMUM_CONTEXT_LENGTH)


def _estimate_tokens(agent: Any, messages: Optional[List[dict]]) -> Optional[int]:
    cc = getattr(agent, "context_compressor", None)
    if cc is None:
        return None

    if messages is not None:
        try:
            from agent.model_metadata import estimate_request_tokens_rough

            system_prompt = getattr(agent, "_cached_system_prompt", None) or ""
            tools = getattr(agent, "tools", None)
            return int(
                estimate_request_tokens_rough(
                    messages, system_prompt=system_prompt, tools=tools or None))
        except Exception:
            pass

    last = int(getattr(cc, "last_prompt_tokens", 0) or 0)
    if last > 0:
        return last
    session_prompt = int(getattr(agent, "session_prompt_tokens", 0) or 0)
    return session_prompt if session_prompt > 0 else None


def _history_can_shrink(cc: Any, messages: Optional[List[dict]]) -> bool:
    """Whether preflight compression could actually drop anything from this payload.

    The compressor never touches the protected head/tail, so a history that fits entirely inside
    them cannot be shrunk however many tokens it holds. That is a fact about *compression*, not
    about the payload the new route still has to read — hence this gates the compression promise
    alone, never the cold-read note.
    """
    if messages is None:
        return True
    protect = (
        int(getattr(cc, "protect_first_n", 3)) + int(getattr(cc, "protect_last_n", 20)) + 1)
    return len(messages) > protect


def _append_cold_read_note(result: ModelSwitchResult, estimate: int, threshold: int) -> None:
    """Note the pre-read a switch costs when no compression is on the way.

    Nothing in the delay is something Hermes rebuilds: the new route answers from an empty prefix
    cache, so the cost is the history itself being re-read. Small sessions answer from a cold cache
    fast enough that the line would be noise, hence the floor.
    """
    if estimate < int(threshold * COLD_READ_THRESHOLD_FRACTION):
        return
    _append_warning(
        result,
        f"Session is ~{estimate:,} tokens; the first reply on {result.new_model} re-reads them "
        f"before it answers — a route that has not served this session has no warm prefix cache, "
        f"so expect a delay on large sessions.")


def merge_preflight_compression_warning(
    result: ModelSwitchResult,
    *,
    agent: Any = None,
    messages: Optional[List[dict]] = None,
    custom_providers: list | None = None,
    config_context_length: int | None = None,
    configured_model: str | None = None,
    configured_provider: str | None = None,
    configured_base_url: str | None = None) -> None:
    """If the next user message will likely preflight-compress, append a warning."""
    if not result.success or agent is None:
        return

    cc = getattr(agent, "context_compressor", None)
    if cc is None:
        return

    # Fall back to the agent's custom providers: without them the shrink warning used the
    # hardcoded catalog (e.g. "qwen" → 131072) even when the provider declared 1M.
    if custom_providers is None:
        custom_providers = getattr(agent, "_custom_providers", None)

    def _or_agent(value, attr):
        return value if value is not None else getattr(agent, attr, None)

    old_ctx = int(getattr(cc, "context_length", 0) or 0)
    new_ctx = resolve_display_context_length(
        result.new_model,
        result.target_provider,
        base_url=result.base_url or getattr(agent, "base_url", "") or "",
        api_key=result.api_key or getattr(agent, "api_key", "") or "",
        model_info=result.model_info,
        custom_providers=custom_providers,
        config_context_length=config_context_length,
        configured_model=_or_agent(configured_model, "model"),
        configured_provider=_or_agent(configured_provider, "provider"),
        configured_base_url=_or_agent(configured_base_url, "base_url"))
    if not new_ctx:
        return

    estimate = _estimate_tokens(agent, messages)
    if estimate is None:
        return

    new_threshold = _threshold_tokens(cc, result.new_model, new_ctx, result.target_provider)
    if estimate < new_threshold:
        _append_cold_read_note(result, estimate, new_threshold)
        return

    # A compression notice is a promise that the next turn will compress; the cold-read note is a
    # plain statement about the cost the switch already carries. Only the promise needs the
    # compressor to be able to run — the payload is sent either way. Same for the suppression rules:
    # they decide whether to promise compression, not whether the switch costs a re-read.
    can_promise_compression = (
        bool(getattr(agent, "compression_enabled", True))
        and _history_can_shrink(cc, messages)
        and int(getattr(cc, "_ineffective_compression_count", 0) or 0) < 2)
    if not can_promise_compression:
        _append_cold_read_note(result, estimate, new_threshold)
        return

    parts: list[str] = []
    if old_ctx and new_ctx < old_ctx:
        parts.append(f"Context window shrinks ({old_ctx:,} → {new_ctx:,}). ")
    parts.append(
        f"Session is ~{estimate:,} tokens; "
        f"{result.new_model} allows {new_ctx:,} "
        f"(auto-compress at ~{new_threshold:,}). "
        f"Your next message will run preflight compression before the model replies.")
    _append_warning(result, "".join(parts))


def enrich_model_switch_warnings_for_gateway(
    result: ModelSwitchResult,
    runner: Any,
    *,
    session_key: str,
    source: Any,
    custom_providers: list | None = None,
    load_gateway_config: Callable[[], dict] | None = None) -> None:
    """Gateway helper: cached agent + session DB messages."""
    lock = getattr(runner, "_agent_cache_lock", None)
    cache = getattr(runner, "_agent_cache", None)
    agent = None
    if lock is not None and cache is not None:
        with lock:
            entry = cache.get(session_key)
            if entry and entry[0] is not None:
                agent = entry[0]
    if agent is None:
        return

    configured: dict = dict.fromkeys(
        ("config_context_length", "configured_model", "configured_provider", "configured_base_url"))
    if load_gateway_config is not None:
        try:
            cfg = load_gateway_config()
            model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
            if isinstance(model_cfg, dict) and model_cfg.get("context_length") is not None:
                configured.update(
                    config_context_length=int(model_cfg["context_length"]),
                    configured_model=model_cfg.get("default") or model_cfg.get("model"),
                    configured_provider=model_cfg.get("provider"),
                    configured_base_url=model_cfg.get("base_url"))
        except Exception:
            pass

    messages = None
    db = getattr(runner, "_session_db", None)
    store = getattr(runner, "session_store", None)
    if db is not None and store is not None:
        try:
            entry = store.get_or_create_session(source)
            messages = db.get_messages_as_conversation(entry.session_id)
        except Exception:
            pass

    merge_preflight_compression_warning(
        result, agent=agent, messages=messages, custom_providers=custom_providers, **configured)
