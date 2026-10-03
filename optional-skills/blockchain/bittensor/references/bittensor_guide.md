# Bittensor Architecture Guide

Background reading for the Bittensor skill. Facts below describe the public
network design; confirm live figures (emission rates, tempo, registration
cost) with `btcli` at the time of use.

## Roles

- **Coldkey.** Owns funds. Signs transfers and stake changes. Stays offline
  whenever possible and is protected by a passphrase set at creation time.
- **Hotkey.** Bound to one coldkey. Registers on subnets to mine, validate,
  or stake. Holds no funds directly; compromise costs position, not principal.
- **Validator / miner.** Validators score miner outputs; miners produce them.
  Both bond stake weight through hotkeys on the subnets they serve.

## TAO Tokenomics

TAO caps at 21,000,000 with Bitcoin-style halving cadence. New issuance per
block splits between the root network (validators securing consensus) and
subnets (each subnet's own validators and miners). Figures move with
governance, so treat any quoted split as stale and check `btcli subnet list`.

## Subnets

Each subnet is an independent incentive market identified by a netuid with
its own tempo (blocks per epoch), immunity period, and emission share. The
metagraph snapshot per subnet records every neuron's stake, rank, trust,
consensus, incentive, dividends, and emission. It refreshes once per tempo,
so reads lag writes by design.

## dTAO and Alpha Tokens

Dynamic-TAO subnets give each subnet a local alpha token priced against TAO
by an on-chain pool. Staking into a dTAO subnet mints alpha; removing stake
burns alpha back to TAO at the current price. Pool slippage means the
TAO value of a position moves with both subnet performance and pool depth.

## Staking and Delegation

Staking locks TAO against a hotkey on one subnet and earns a share of that
subnet's emission. Delegation is the same action pointed at somebody else's
hotkey (typically a validator). Unstaking reverses it after the subnet's
cooldown. Rewards accrue per block in rho (the smallest TAO unit) and are
only spendable once removed to free balance.

## Common Workflows

1. Read: balance, then stake positions, then the subnet list.
2. Change: stake, unstake, or transfer through `btcli` prompts.
3. Verify: re-read balance and positions; allow one tempo for metagraph
   views to catch up.
4. Rotate: on hotkey worry, generate a fresh hotkey, register it, move
   stake, and retire the old one.
