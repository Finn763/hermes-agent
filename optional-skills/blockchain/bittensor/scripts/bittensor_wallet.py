"""Bittensor wallet helper: balance, stake, and subnet queries via btcli.

Stdlib only (argparse, json, re, subprocess, sys). All chain access goes
through the ``btcli`` executable; this module only builds argv, runs it,
and parses the text output. Unit tests mock ``_run_btcli``, so the test
suite never touches the network.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

BTCLI_INSTALL_HINT = (
    "btcli not found on PATH. Install it with `pip install bittensor-cli` "
    "and re-run this command."
)

_BALANCE_RE = re.compile(r"(?i)\b(free|staked|total)\b[^0-9]*([0-9][0-9,]*\.?[0-9]*)\s*TAO")
_STAKE_RE = re.compile(
    r"Hotkey\s+(\S+)\s*\(netuid\s+(\d+)\)\s*:\s*([0-9][0-9,]*\.?[0-9]*)\s*TAO"
)
_SUBNET_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+([0-9][0-9,]*\.?[0-9]*)\s+(\d+)\s*$")


def _run_btcli(args: list[str]) -> str:
    """Run ``btcli`` with argv and return stdout text."""
    proc = subprocess.run(
        ["btcli", *args], capture_output=True, text=True, timeout=180
    )
    if proc.returncode != 0:
        raise RuntimeError(f"btcli failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _parse_balance_output(text: str) -> dict:
    """Parse ``btcli wallet balance`` text into free/staked/total TAO."""
    found: dict[str, float] = {}
    for match in _BALANCE_RE.finditer(text):
        found[match.group(1).lower()] = float(match.group(2).replace(",", ""))
    if not found:
        raise ValueError("could not parse balance output: no TAO figures found")
    return {
        "free_tao": found.get("free_tao", found.get("free", 0.0)),
        "staked_tao": found.get("staked_tao", found.get("staked", 0.0)),
        "total_tao": found.get("total_tao", found.get("total", 0.0)),
    }


def _parse_stake_output(text: str) -> list[dict]:
    """Parse ``btcli stake show`` text into per-hotkey stake positions."""
    rows = []
    for match in _STAKE_RE.finditer(text):
        rows.append(
            {
                "hotkey": match.group(1),
                "netuid": int(match.group(2)),
                "stake_tao": float(match.group(3).replace(",", "")),
            }
        )
    return rows


def _parse_subnet_list(text: str) -> list[dict]:
    """Parse ``btcli subnet list`` table rows into subnet records."""
    rows = []
    for line in text.splitlines():
        match = _SUBNET_RE.match(line)
        if match:
            rows.append(
                {
                    "netuid": int(match.group(1)),
                    "name": match.group(2),
                    "emission_tao_per_block": float(match.group(3).replace(",", "")),
                    "neurons": int(match.group(4)),
                }
            )
    return rows


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bittensor wallet helper via btcli.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_balance = sub.add_parser("balance", help="Show wallet TAO balance.")
    p_balance.add_argument("--wallet", default="default")
    p_balance.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    p_stake = sub.add_parser("stake", help="Show stake positions for a wallet.")
    p_stake.add_argument("--wallet", default="default")
    p_stake.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    p_subnets = sub.add_parser("subnets", help="List active subnets.")
    p_subnets.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    p_status = sub.add_parser("status", help="Wallet balance plus stake summary.")
    p_status.add_argument("--wallet", default="default")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    args = parser.parse_args(argv)
    try:
        if args.command == "balance":
            raw = _run_btcli(["wallet", "balance", "--wallet.name", args.wallet])
            _print({"wallet": args.wallet, "balance": _parse_balance_output(raw)}, args.json)
        elif args.command == "stake":
            raw = _run_btcli(["stake", "show", "--wallet.name", args.wallet])
            _print({"wallet": args.wallet, "positions": _parse_stake_output(raw)}, args.json)
        elif args.command == "subnets":
            raw = _run_btcli(["subnet", "list"])
            _print({"subnets": _parse_subnet_list(raw)}, args.json)
        elif args.command == "status":
            balance_raw = _run_btcli(["wallet", "balance", "--wallet.name", args.wallet])
            stake_raw = _run_btcli(["stake", "show", "--wallet.name", args.wallet])
            positions = _parse_stake_output(stake_raw)
            _print(
                {
                    "wallet": args.wallet,
                    "balance": _parse_balance_output(balance_raw),
                    "open_positions": len(positions),
                    "positions": positions,
                },
                args.json,
            )
    except FileNotFoundError:
        print(BTCLI_INSTALL_HINT, file=sys.stderr)
        return 2
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
