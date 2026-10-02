"""RED for #35817: non-vision computer_use capture must return text summary silently."""

from __future__ import annotations

import json


def _make_agent(provider="deepseek", model="deepseek-v4-flash"):
    from run_agent import AIAgent
    agent = object.__new__(AIAgent)
    agent.provider = provider
    agent.model = model
    return agent


def _multimodal_result():
    return {
        "_multimodal": True,
        "content": [
            {"type": "text", "text": "capture mode=som 800x600 app=Safari"},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAA"}},
        ],
        "text_summary": "capture mode=som 800x600 app=Safari",
        "meta": {"mode": "som", "width": 800, "height": 600},
    }


class TestNonVisionComputerUse:
    def test_returns_plain_summary_without_error_block(self, monkeypatch):
        agent = _make_agent()
        monkeypatch.setattr(agent, "_model_supports_vision", lambda: False)
        out = agent._tool_result_content_for_active_model(
            "computer_use", _multimodal_result()
        )
        assert isinstance(out, str)
        # Must NOT be {"error": ..., "text_summary": ...} JSON.
        try:
            parsed = json.loads(out)
        except (ValueError, TypeError):
            parsed = None
        assert not (isinstance(parsed, dict) and "error" in parsed), (
            f"non-vision computer_use leaked error JSON: {out[:200]}"
        )
        assert "capture mode=som" in out
        assert "data:image" not in out
