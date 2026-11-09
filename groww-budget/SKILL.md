---
name: groww-budget
description: >-
  Budget Manager for Groww. Sizes every NSE intraday trade to your rupee budget
  and your max loss per trade, and writes ONE Groww order proposal: the entry
  (limit at the plan's entry, or market) plus an OCO smart order that holds the
  stop-loss and the target. Reads the plan from india-exit, finds the whole
  shares that fit inside four limits (max loss per trade, position cap, max
  order, cash after your buffer, or Groww's live intraday margin if you allow
  leverage), says which limit decided it, puts every price on the stock's real
  tick from Groww's instrument list, estimates Groww's brokerage, and writes
  trader-in/orders.json in the exact shape of Groww's Trade API. Refuses stocks
  Groww does not allow for intraday. Nothing is placed. Use when the user says
  "how many shares on Groww", "size this trade", "I have Rs 50,000", "I only
  want to risk Rs 500 a trade", "set my Groww rules", or after /india-exit and
  /india-timemachine, before /groww-risk.
argument-hint: "[--write] [--market | --limit PRICE] [--price PRICE]"
---

# groww-budget

The Budget Manager turns a plan ("buy SBIN at 1,040, stop 1,029.20") into an
exact Groww order ("buy 46 shares, intraday"), inside rules you set once. The
stop decides the size: the further away the stop, the fewer shares. It writes
a proposal only: groww-risk checks it and groww-broker asks you yes or no.
SEBI's studies found that most individual intraday traders lose money. Sizing
to a max loss keeps each loss small; it does not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It uses the shared helpers in `../groww-broker/growwlib.py`.

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                    # trader-in/plan.json + trader-in/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write            # write trader-in/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --market --write   # a market entry instead of a limit at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1041 --json
python3 "${CLAUDE_SKILL_DIR}/size.py" --price 1044.50    # size from a fresh price you give
```

`--now "2026-11-09 10:00"` pins the clock (India time). A plan for another
day is refused: make a new one with /india-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/config.json` | your rules (start from `config.example.json` in this folder) |
| `trader-in/plan.json` | the trade: entry, stop, target, setup, flat-by (india-exit writes it) |
| Groww's instrument list | `growwapi-assets.groww.in/instruments/instrument.csv` (public, 20 MB), cached daily in `trader-in/state/groww-instruments.json`: real tick size, intraday allowed, buy allowed, F&O stock or not |
| a fresh price | your `--price`, else Groww's live price (with a Groww login), else Yahoo's if under 15 minutes old |
| `trader-in/orders.json` | the one proposal it writes with `--write` |

## Step 1: set the rules once

If `trader-in/config.json` does not exist, copy `config.example.json` there
and ask the user for each value in plain words:

- **capital_inr**: how much money this is for. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays unused (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default Rs 500).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (50 % of capital), the
  biggest order (Rs 50,000) and the smallest (Rs 500).
- The day limits groww-risk enforces: **max_daily_loss_inr** (1,500), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "15:00") and **flat_by** ("15:10"), India time. The India skills read the same three times.
- **entry_order**: "LIMIT" (default, at the plan's entry) or "MARKET".
- **use_leverage**: false (default) sizes on your own cash. True lets Groww's intraday margin (MIS
  leverage) set the cash limit; the max loss per trade still caps the size.
- **blocked**: symbols they never want to trade.
- **live** stays false until they have watched the whole flow work in DRY-RUN. Only the user turns it on.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares, intraday (MIS) only. The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | capital x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (capital minus the buffer) / entry; with `use_leverage`, Groww's MIS margin available / Groww's margin for one share |

- **Ticks.** NSE sets each stock's tick monthly, so the band table can be
  wrong: in Nov 2026 SBIN at Rs 1,042 has a Rs 0.05 tick, not Rs 0.10. The tool
  reads the real tick from Groww's list: the stop rounds DOWN, the target and
  a limit entry round UP, and it says when a price moved.
- **The entry it sizes at**: the limit price for a limit order. For a market
  order, the plan's entry, or a fresh price if it is higher (a price that ran
  up means a bigger loss at the same stop, so fewer shares).
- **Brokerage**: Groww charges Rs 20 or 0.1 % per executed order, whichever is
  lower, minimum Rs 5 (groww.in/pricing/stocks). STT, exchange charges, SEBI
  fees, stamp duty and GST come on top.

It refuses, and writes nothing, when: zero shares fit, the order is under the
minimum, reward:risk is under `min_reward_risk`, the stop is not below the
entry, the target is not above it (the move already happened), the plan is
for another day, the symbol is blocked, Groww does not allow intraday or
buying in it (Groww allows intraday in about 1,700 of 4,350 NSE stocks), or
the plan is a short. Say the reason in one line.

## Step 3: show the user

Show the four limits, which one decided, the shares, the rupees, the max loss
at the stop and the brokerage estimate. Say that a stop is an order, not a
promise: in a fast market or a circuit it can fill below the stop. Then hand
over to `/groww-risk`.

## Example: Mon Nov 9 2026

SBIN triggered at 9:35 AM at Rs 1,040.00; india-exit's plan: stop 1,029.20,
target 1,061.60. Example config: Rs 1,00,000 capital, 10 % buffer, Rs 500 max
loss.

```
Groww order proposal: SBIN on NSE, intraday (MIS), limit order at 1040.00
  Entry 1040.00, stop 1029.20, target 1061.60 (Groww tick Rs 0.05): risk 10.80 a share, reward:risk 2.0
  max loss      Rs 500.00 / 10.80                                          46  <- decides it
  position cap  50% of Rs 100,000.00 = Rs 50,000.00 / 1040.00              48
  max order     Rs 50,000.00 / 1040.00                                     48
  cash          Rs 100,000.00 minus 10% buffer = Rs 90,000.00 / 1040.00    86
  BUY 46 SBIN = Rs 47,840.00. Max loss at the stop: Rs 496.80.
  Brokerage about Rs 40.00 both ways if the stop fills ...
```

The proposal in `trader-in/orders.json` holds Groww's order body
(`trading_symbol`, `quantity`, `price`, `validity` DAY, `exchange` NSE,
`segment` CASH, `product` MIS, `order_type` LIMIT, `transaction_type` BUY,
`order_reference_id`) and the OCO body (`smart_order_type` OCO, SELL 46,
stop-loss SL_M at "1029.20", target LIMIT at "1061.60", `product_type` MIS).
The reference ids are made from the order's fingerprint, so Groww refuses the
same proposal twice. A market entry at a fresh Rs 1,052 would make reward:risk
0.42: refused, because the move it was waiting for has mostly happened.

## Safety

- Education and tooling, not investment advice. This pack is not a
  SEBI-registered investment adviser or research analyst.
- DRY-RUN by default (`"live": false`): Groww has no paper account, so nothing
  is sent until the user turns `live` on in their own config.
- Nothing here places an order. Only groww-broker can, and only after
  groww-risk passes that exact order today, the user types an explicit yes for
  it, and the user clicks Yes on Claude Code's permission prompt.
- Intraday cash-segment longs only: no F&O, options, delivery, MTF or shorts.
