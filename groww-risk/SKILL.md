---
name: groww-risk
description: >-
  Risk Manager for Groww. Checks every Groww order proposal against your limits
  and Groww's own rules, and stops anything over them before it can reach
  groww-broker. For an NSE intraday entry: a stop is required (the OCO
  stop-loss), the loss at the stop must fit your max loss per trade, reward:risk
  must meet your minimum, today's total risk must fit your daily loss limit, no
  more than your trades per day, and no new entries outside your window, on an
  NSE holiday, or after Groww's own cut-off (3:05 PM in F&O stocks, 3:20 PM in
  the rest). Also checks the order format against Groww's API, prices on the
  real tick, intraday allowed in the stock, size, position cap, cash buffer,
  blocked list and duplicates. Getting out is never blocked by a day limit.
  Prints PASS or BLOCK with the reason and records each verdict so
  groww-broker only shows an order that passed. Use when the user says "check
  my Groww order", "is this safe", "risk check", "can I take this trade", "how
  much have I risked today", or before /groww-broker, always.
argument-hint: "[--no-write] [--now \"YYYY-MM-DD HH:MM\"]"
---

# groww-risk

The Risk Manager is the gate between a proposal and Groww. Every proposal
written by groww-budget or by groww-broker's close passes through it. A BLOCK
is final for that order: it does not go to the user for a yes, and it does not
go to Groww. These limits keep each loss and each day's losses small. They do
not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It uses the shared helpers in `../groww-broker/growwlib.py`.

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader-in/ files; writes trader-in/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --price SBIN=1044.5       # a fresh price for a market order
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-09 10:00"  # pin the clock (India time)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/orders.json` | the proposals (groww-budget, or groww-broker's close) |
| `trader-in/config.json` | the limits (groww-budget ships `config.example.json`); without it the example limits apply and it says so |
| Groww's instrument list | cached in `trader-in/state/groww-instruments.json`: tick, intraday allowed, F&O stock |
| `trader-in/state/groww.json` | groww-broker's log: today's entries, their max losses, open positions, duplicates |
| `trader-in/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

DRY-RUN and LIVE keep separate tallies: rehearsals never use up a live day's limits.

## Entries and exits

An **entry** buys. An **exit** sells shares you hold (groww-broker's close).
Getting out must always be possible, so an exit is checked for format and for
selling more than you hold today (that would be a short); the market being
closed and Groww's 3:20 PM square-off are warnings, never a BLOCK.

## The checks (entries)

| check | blocks when |
| --- | --- |
| format | a Groww field is missing or wrong: not CASH, not MIS, not DAY, not NSE/BSE, not LIMIT/MARKET, a quantity that is not whole shares, a reference id that is not 8-20 letters and digits, a limit price off the tick; an OCO that does not SELL the same shares, MIS, with a stop-loss and a target on the tick |
| stock | not in Groww's list, not a stock, buying not allowed, intraday not allowed |
| blocked | the symbol is on your `blocked` list |
| stop | no OCO stop-loss while `require_stop` is true; a stop not below the entry |
| loss | quantity x (entry minus stop) over `max_loss_per_trade_inr` |
| reward:risk | (target minus entry) / (entry minus stop) under `min_reward_risk` |
| size | under `min_order_inr` or over `max_order_inr` |
| position | over `max_position_pct` of `capital_inr` |
| cash | over capital minus the buffer, counting today's open entries (skipped with `use_leverage`) |
| daily loss | the max losses of today's entries plus this one over `max_daily_loss_inr` |
| trades / orders | `max_trades_per_day` entries or `max_orders_per_day` orders already today |
| window | not an NSE trading day; outside 9:15-15:30; before `no_entries_before` or after `no_entries_after`; after Groww's intraday cut-off (3:05 PM in F&O stocks, 3:20 PM in the rest, from Groww's risk policy) |
| duplicate | the same stock twice in one batch, or already entered today |

How the numbers work:

- **The entry it measures from**: the limit price for a limit order (it cannot
  fill higher). For a market order, the HIGHER of the plan's entry and a fresh
  price (your `--price`, Groww's live price with a login, else Yahoo's under 15
  minutes old). A plan written at 9:35 cannot understate the risk at 10:20.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as groww-broker logged it. Three full-size Rs 500 trades use up a
  Rs 1,500 day.
- **The window** is India time, worked out in the tool (IST, no daylight
  saving), with the NSE 2026 holiday list. Set both `no_entries_*` to null to
  switch your own window off; Groww's cut-off still applies.

Warnings do not block, but say them to the user: LIVE mode, no fresh price for
a market order, a stop that lives only in the plan, the holiday list not
covering the year.

## Steps

1. If the price may have moved since the plan, re-run `/groww-budget` (it
   reads a fresh price) or pass `--price SYMBOL=PRICE`.
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, how much of today's limits it uses. For a BLOCK,
   give the reason and the fix (fewer shares, a closer stop from india-exit,
   wait for the window, or skip the trade). Never change a limit yourself.
4. If the order changes in any way, check it again. groww-broker matches each
   order to its verdict by fingerprint, so an edited order is not ready until
   it passes again, and a verdict from yesterday does not count today.

## Example: Mon Nov 9 2026

The SBIN proposal from groww-budget at 10:00 AM IST, example config, DRY-RUN:

```
PASS  #1 buy SBIN 46 shares, entry
          entry 1040.00 (the limit price), stop 1029.20, target 1061.60: risk 10.80 a share
          max loss Rs 496.80 (limit Rs 500.00), reward:risk 2.0 (min 1.0)
          today before this: Rs 0.00 of Rs 1,500.00 daily loss; order value Rs 47,840.00
```

The same order at 3:06 PM:

```
BLOCK #1 buy SBIN 46 shares, entry
          BLOCK: window: no entries after 15:00 (config)
          BLOCK: window: Groww takes no new intraday orders in SBIN after 3:05 PM
```

And after SBIN was entered once that day: `BLOCK: duplicate: SBIN was already entered today`.

## Safety

- Education and tooling, not investment advice. A PASS is a rules check, not a
  recommendation. This pack is not a SEBI-registered adviser.
- It never changes a limit, never turns `live` on, and never places anything.
