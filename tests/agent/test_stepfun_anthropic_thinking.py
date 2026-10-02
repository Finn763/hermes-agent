"""Regression guard: no Anthropic thinking for StepFun step-3.7-flash. See #39124."""

from __future__ import annotations


def _kw(model, reasoning, base_url="https://inference-api.nousresearch.com/v1"):
    from agent.anthropic_adapter import build_anthropic_kwargs

    return build_anthropic_kwargs(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        max_tokens=4096,
        reasoning_config=reasoning,
        base_url=base_url,
    )


class TestStepFunSkipsThinking:
    def test_stepfun_omits_thinking(self):
        kwargs = _kw("stepfun/step-3.7-flash:free", {"enabled": True, "effort": "medium"})
        assert "thinking" not in kwargs
        assert "output_config" not in kwargs

    def test_stepfun_no_forced_temperature(self):
        kwargs = _kw("stepfun/step-3.7-flash:free", {"enabled": True, "effort": "medium"})
        assert kwargs.get("temperature") != 1

    def test_minimax_still_gets_thinking(self):
        kwargs = _kw(
            "MiniMax-M2.7",
            {"enabled": True, "effort": "medium"},
            base_url="https://api.minimax.io/anthropic",
        )
        assert kwargs.get("thinking", {}).get("type") == "enabled"

    def test_step_13_lookalike_not_matched(self):
        kwargs = _kw(
            "vendor/step-13.7-flash",
            {"enabled": True, "effort": "medium"},
            base_url="https://api.example.com/anthropic",
        )
        assert "thinking" in kwargs
