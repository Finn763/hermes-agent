"""Desktop GPT-Live idle hangup: the cost-safety default and its Settings row.

The desktop hangs up a live call that goes silent (OpenAI bills session minutes,
idle included, at $0.05/min). The deadline lives in DEFAULT_CONFIG so it is
documented, settable with ``hermes config set``, and typed as a number in the web
config schema — which is what makes Settings → Voice render an input for it.
"""

from hermes_cli.config import DEFAULT_CONFIG
from hermes_cli.web_server_config import _build_schema_from_config


def test_default_idle_hangup_is_five_minutes():
    assert DEFAULT_CONFIG["voice"]["gpt_live"]["idle_hangup_seconds"] == 300


def test_idle_hangup_renders_as_a_number_in_settings():
    schema = _build_schema_from_config(DEFAULT_CONFIG)

    assert schema["voice.gpt_live.idle_hangup_seconds"]["type"] == "number"


def test_user_config_overrides_the_default(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"voice": {"gpt_live": {"idle_hangup_seconds": 0}}}), encoding="utf-8"
    )

    import importlib

    from hermes_cli import config as config_module

    importlib.reload(config_module)
    assert config_module.load_config()["voice"]["gpt_live"]["idle_hangup_seconds"] == 0
    # 0 is a value, not a missing key: the rest of the block still fills in.
    assert config_module.load_config()["voice"]["gpt_live"]["voice"] == "marin"
