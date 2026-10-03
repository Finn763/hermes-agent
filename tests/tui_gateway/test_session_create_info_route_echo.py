"""``session.create`` info echoes the requested route (issue #125346, option 3).

The result contract types ``info`` as ``SessionLiveInfo`` (model / provider /
reasoning_effort / service_tier / fast), but the create path built a minimal
dict with only model/provider/cwd — so a desktop plugin reading the create
reply could not tell which reasoning effort or tier the next turn will run,
and the composer chip keeps showing a stale sticky pick from an earlier chat.
"""

import pytest


@pytest.fixture
def _create(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_cli.banner.prefetch_update_check", lambda: None)
    from tui_gateway import server

    (tmp_path / "config.yaml").write_text(
        "model:\n  default: claude-opus-5\n  provider: anthropic\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(server, "_sessions", {})
    monkeypatch.setattr(server, "_load_cfg", lambda: {})
    monkeypatch.setattr(server, "_profile_home", lambda *a: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_schedule_agent_build", lambda *a: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda *a: None)
    monkeypatch.setattr(server, "_project_info_for_cwd", lambda *a: None)
    return lambda params: (
        server._methods["session.create"]("r1", {"cols": 80, **params}),
        server._sessions,
    )


@pytest.mark.parametrize(
    "params,effort,tier,fast",
    [
        ({"reasoning_effort": "high", "fast": True}, "high", "priority", True),
        ({"reasoning_effort": "none"}, "none", "", False),
        ({}, "", "", False),
    ],
)
def test_session_create_info_echoes_requested_route(
    _create, params, effort, tier, fast
):
    response, _ = _create(params)

    assert "error" not in response, response
    info = response["result"]["info"]
    assert info["reasoning_effort"] == effort
    assert info["service_tier"] == tier
    assert info["fast"] is fast


def test_session_create_idempotent_retry_keeps_route_echo(_create):
    first, _ = _create({
        "idempotency_key": "route-echo-1",
        "reasoning_effort": "high",
        "fast": True,
    })
    assert "error" not in first, first

    from tui_gateway import server

    response = server._methods["session.create"](
        "r2", {"cols": 80, "idempotency_key": "route-echo-1"}
    )

    assert "error" not in response, response
    info = response["result"]["info"]
    assert info["reasoning_effort"] == "high"
    assert info["service_tier"] == "priority"
    assert info["fast"] is True
