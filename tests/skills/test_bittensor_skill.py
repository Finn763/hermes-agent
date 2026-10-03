"""Tests for the Bittensor optional skill helper script.

Stdlib + pytest + unittest.mock only. No live network: ``btcli``
subprocess calls are mocked; parsers run against canned outputs.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "blockchain"
    / "bittensor"
    / "scripts"
    / "bittensor_wallet.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("bittensor_skill", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BALANCE_SAMPLE = """Balance:
  Free: 12.5 TAO
  Staked: 3.25 TAO
  Total: 15.75 TAO
"""

STAKE_SAMPLE = """Stake for coldkey default:
  Hotkey abc123 (netuid 1): 1.5 TAO
  Hotkey def456 (netuid 18): 1.75 TAO
"""

SUBNET_SAMPLE = """NETUID  Name       Emission   Neurons
1       Apex         1.0        256
18      Cortex       0.5        128
"""


def test_parse_balance_output_extracts_free_staked_total():
    mod = load_module()
    parsed = mod._parse_balance_output(BALANCE_SAMPLE)
    assert parsed == {"free_tao": 12.5, "staked_tao": 3.25, "total_tao": 15.75}


def test_parse_balance_output_rejects_empty():
    mod = load_module()
    with pytest.raises(ValueError, match="balance"):
        mod._parse_balance_output("no numbers here\n")


def test_parse_stake_output_extracts_positions():
    mod = load_module()
    rows = mod._parse_stake_output(STAKE_SAMPLE)
    assert len(rows) == 2
    assert rows[0] == {"hotkey": "abc123", "netuid": 1, "stake_tao": 1.5}
    assert rows[1]["netuid"] == 18


def test_parse_subnet_list_extracts_rows():
    mod = load_module()
    rows = mod._parse_subnet_list(SUBNET_SAMPLE)
    assert len(rows) == 2
    assert rows[0]["netuid"] == 1
    assert rows[0]["name"] == "Apex"
    assert rows[1]["neurons"] == 128


def test_main_balance_json_prints_parsed_payload(capsys):
    mod = load_module()
    with patch.object(mod, "_run_btcli", return_value=BALANCE_SAMPLE):
        exit_code = mod.main(["balance", "--wallet", "default", "--json"])
    assert exit_code == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["wallet"] == "default"
    assert rendered["balance"]["total_tao"] == 15.75


def test_main_status_reports_missing_btcli(capsys):
    mod = load_module()
    with patch.object(
        mod, "_run_btcli", side_effect=FileNotFoundError("no btcli")
    ):
        exit_code = mod.main(["status", "--wallet", "default"])
    assert exit_code == 2
    assert "btcli" in capsys.readouterr().err
