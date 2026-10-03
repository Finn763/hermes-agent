---
title: "Bittensor — Manage Bittensor wallets, stake TAO, query subnets"
sidebar_label: "Bittensor"
description: "Manage Bittensor wallets, stake TAO, query subnets"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Bittensor

Manage Bittensor wallets, stake TAO, query subnets.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/blockchain/bittensor` |
| Path | `optional-skills/blockchain/bittensor` |
| Version | `0.1.0` |
| Author | Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `Bittensor`, `TAO`, `Blockchain`, `Crypto`, `Wallet`, `Staking`, `Subnet` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Bittensor Skill

Work with Bittensor wallets through `btcli` from the `terminal` tool: check
TAO balances, inspect stake and delegation positions, list subnets, and
prepare transfers. This skill covers the everyday wallet workflows and stops
short of key custody — it never stores mnemonics or passphrases, and every
state-changing step still confirms inside `btcli` itself.

---

## When to Use

- User asks for a TAO balance on a coldkey or hotkey
- User wants to stake, unstake, or review delegation positions
- User wants the active subnet list or a subnet metagraph
- User wants to move TAO between addresses
- User asks how coldkeys, hotkeys, and dTAO subnets fit together

---

## Prerequisites

`btcli` installed and on PATH (`pip install bittensor-cli`). Wallets live
under `~/.bittensor/wallets/` unless `--wallet.path` overrides it. No API key
is needed. Key creation and restores prompt for a coldkey passphrase inside
`btcli` — never paste a mnemonic or passphrase into any other surface.

Helper script: `~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py`

---

## How to Run

Invoke through the `terminal` tool:

```bash
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py status --wallet default
```

Use `read_file` to inspect the helper output saved to disk when it is long.

---

## Quick Reference

```
SCRIPT=~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py

python $SCRIPT balance --wallet default          # free, staked, total TAO
python $SCRIPT balance --wallet default --json   # machine-readable balance
python $SCRIPT stake --wallet default            # per-hotkey stake positions
python $SCRIPT subnets                           # active subnets and emissions
python $SCRIPT status --wallet default           # balance plus stake summary
```

Direct `btcli` equivalents:

```
btcli wallet balance --wallet.name default
btcli stake show --wallet.name default
btcli subnet list
btcli subnet metagraph --netuid 1
btcli wallet transfer --wallet.name default --dest <ss58> --amount <TAO>
```

---

## Procedure

### 0. Setup Check

```bash
btcli --version
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py status --wallet default
```

If `btcli` is missing, the helper prints the install hint. Confirm the
wallet name first with `btcli wallet list` before any write operation.

### 1. Wallet Balance

Show free (spendable), staked, and total TAO for one wallet.

```bash
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py balance --wallet default --json
```

Output: free, staked, and total TAO. Free balance pays fees and transfers;
staked balance is locked in subnet positions until unstaked.

### 2. Stake and Delegation Positions

Each row ties a hotkey to one subnet with its staked TAO.

```bash
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py stake --wallet default --json
```

To change stake, run the matching `btcli` verbs (`btcli stake add`,
`btcli stake remove`) and re-run this command to confirm the new figures.

### 3. List Active Subnets

```bash
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py subnets
```

Output per subnet: netuid, name, per-block emission, registered neurons.
Cross-check anything surprising against `btcli subnet list` directly.

### 4. Subnet Metagraph

```bash
btcli subnet metagraph --netuid 1
```

Columns include UID, hotkey, stake, rank, trust, consensus, incentive,
dividends, emission, and last update. The metagraph refreshes once per tempo
(roughly every few hundred blocks), so fresh changes can lag by minutes.

### 5. Transfer TAO

```bash
btcli wallet transfer --wallet.name default --dest <ss58-address> --amount <TAO>
```

Transfers are irreversible. Read the destination address back character by
character before confirming, then verify with the balance command.

---

## Pitfalls

- **Coldkey holds funds, hotkey does work.** The coldkey owns the TAO;
  the hotkey stakes, mines, or validates. Never export a coldkey to
  operate a hotkey task.
- **State changes always confirm in `btcli`.** The helper only reads and
  reports; staking, unstaking, and transfers run through `btcli` prompts.
- **Metagraph data lags.** Reads refresh once per tempo, so a just-made
  change may not appear for several minutes. Re-query before concluding
  that an operation failed.
- **dTAO subnets use alpha tokens.** Dynamic-TAO subnets price stake in
  subnet alpha rather than plain TAO; the metagraph shows which unit a
  figure is denominated in.
- **Registration costs TAO.** Attaching a hotkey to a subnet burns TAO or
  proof of work; check the current cost before registering.
- **Small free balance required.** Every extrinsic pays a fee from free
  balance, so keep dust un-staked or transfers and stake changes fail.

---

## Verification

```bash
python ~/.hermes/skills/blockchain/bittensor/scripts/bittensor_wallet.py status --wallet default
btcli stake show --wallet.name default
```

Both commands should agree on balances and positions after any operation.
