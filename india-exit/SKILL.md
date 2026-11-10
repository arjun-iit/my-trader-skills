---
name: india-exit
description: >-
  Exit Planner for Indian stocks. Sets your stop and your target before you
  enter an NSE trade, so you know when to get out. Reads the entry from
  india-entry (or a price you give), puts the stop at the opening-range
  midpoint by default (or the range low, your price, a percent, or a multiple
  of the ATR), rounds it down to NSE's tick size for that price band (Rs 0.01
  under Rs 250, up to Rs 5 above Rs 20,000), and sets the target at 2R (or 1R,
  or your price) on the same tick grid. Refuses a stop at or above the entry, a
  target at or below it, or a stop too tight to survive the spread, and says
  why. Sets a flat-by time (15:10 IST) ahead of the broker's intraday
  square-off. Writes trader-in/plan.json. Long side only. Use when the user
  says "where's my stop on SBIN", "set my stop and target", "plan the exit",
  "stop loss for RELIANCE", "what's my R", "risk reward", "tick size", or right
  after /india-entry triggers.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|price:X]"
---

# india-exit

The Exit Planner decides how you get out before you get in: one stop, one
target, one time to be flat. It does the arithmetic in exact decimals, puts
every price on NSE's tick grid so a broker will accept it, and refuses a plan
that cannot work. It never places an order. A stop is a plan, not a guarantee:
a gap, a circuit limit or a fast market can fill past it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # from trader-in/entry.json: or-mid stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --write                  # also save trader-in/plan.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-low --target 1.5R
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # ATR from the session's bars (Yahoo)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol SBIN --entry 1040 --or-high 1034.95 --or-low 1023.45
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol TCS --entry 2490.55 --setup level --stop pct:1
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader-in/plan.json
```

Other flags: `--tick 0.05` (override the tick), `--flat-by 15:00`, `--date`,
`--time 9:35 AM`, `--interval 1m`, `--offline FILE` (a saved Yahoo chart for
an ATR stop), `--now "2026-11-09 10:00"` (IST), `--json`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/entry.json` | the trigger from `/india-entry`: symbol, price, time, bar, opening range |
| `trader-in/config.json` | `flat_by` (15:10), `min_reward_risk` (1.0, a warning here), `tick_size` (only to force one). Optional |
| `trader-in/plan.json` | with `--write`: entry, stop, target, risk a share, reward:risk, tick size, flat-by |

## The rules

- **Stop** (a long's stop rounds DOWN to the tick, away from the entry):
  `or-mid` (the opening-range midpoint, the default for an orb entry),
  `or-low`, `price:X`, `pct:X` (X % under the entry), `atr:K` (K times the
  14-bar ATR of the session's bars up to the entry bar; with fewer bars it
  averages what it has and says so). A level setup has no default: give one.
- **Target** (half up to the tick): `2R` (default), `1R`, `1.5R` ... = entry +
  R x (entry - stop), or `price:X`.
- **Tick size.** An NSE order price must be a multiple of the stock's tick.
  NSE sets it every month from the previous month's closing price (circular
  NSE/CMTR/67133, from Apr 15 2025):

  | price | tick |
  | --- | --- |
  | under Rs 250 | Rs 0.01 |
  | Rs 250 to 1,000 | Rs 0.05 |
  | above Rs 1,000 to 5,000 | Rs 0.10 |
  | above Rs 5,000 to 10,000 | Rs 0.50 |
  | above Rs 10,000 to 20,000 | Rs 1.00 |
  | above Rs 20,000 | Rs 5.00 |

  The tool picks the band from the entry price. Near a band edge, or after a
  big move this month, the broker's tick can differ: check it in the order
  window and pass `--tick` if it does.
- **Refused, with the reason:** a stop at or above the entry, a target at or
  below it, a risk under 0.05 % of the price (inside the spread's noise), an
  entry.json that has not triggered (unless you give `--entry`), a short.
- **Flat by** 15:10 IST (config `flat_by`): out by then, win or lose, before
  the broker's intraday (MIS) auto square-off, usually 15:15 to 15:25 (it
  charges a fee and fills at the market).

## Steps

1. After `/india-entry` triggers (it wrote `trader-in/entry.json`): run
   `exit.py plan`. Or give `--symbol` and `--entry` to plan ahead.
2. Read the plan to the user: entry, stop and why, target, risk a share and
   as a percent, reward:risk, the tick used, flat-by.
3. If they agree, `exit.py plan --write` saves it for `/india-news` (which
   watches the planned symbol) and for their own records.
4. Say that nothing was placed. Next, `/india-timemachine` replays the plan
   on the stock's last 60 days. On Groww: `/groww-budget` sizes it to their
   max loss per trade and re-rounds the prices to Groww's real tick, then
   `/groww-risk`, then `/groww-broker`. With another broker, sizing is theirs:
   shares = their max loss per trade / risk a share, rounded down.

## Example: Mon Nov 9 2026

SBIN triggered at 9:35 AM at Rs 1,040.00; range 1,023.45 to 1,034.95:

```
Exit plan: SBIN long, Mon 2026-11-09, opening-range breakout on 5-minute bars
  Entry         Rs 1,040.00 at 9:35 AM (bar 3)
  Stop          Rs 1,029.20  opening-range midpoint: (Rs 1,034.95 + Rs 1,023.45) / 2 = Rs 1029.2
  Target        Rs 1,061.60  2R (two times the risk)
  Risk a share  Rs 10.80 (1.04% of the entry)
  Reward:risk   2.0
  Tick          Rs 0.10 (NSE price band of the entry)
  Flat by       15:10 IST, win or lose: before your broker's intraday square-off
```

INDIGO at Rs 5,458.50 sits in the Rs 0.50 band, so its or-mid of Rs 5,427.80
rounds down to Rs 5,427.50.

## What it never does

It never places, sizes or sends an order, never moves a stop further away
after the entry, never plans a short, and never promises the stop will fill
at its price.

## Safety

- Education and tooling, not investment advice. This pack is not a
  SEBI-registered investment adviser or research analyst.
- This skill places no orders. In this repo only groww-broker can, and only after
  groww-risk passes that exact order, the user types an explicit yes for it and
  clicks Yes on Claude Code's prompt (DRY-RUN until the user turns `live` on).
  With any other broker, the order is the user's own.
- No F&O, options, margin (MTF) or short selling: the pack plans cash-segment
  longs only.
