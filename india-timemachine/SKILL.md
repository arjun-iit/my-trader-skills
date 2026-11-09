---
name: india-timemachine
description: >-
  Time Machine for Indian stocks. Replays every possible outcome of an NSE
  trade before you risk a rupee: 10,000 simulated sessions plus every past
  time the setup fired. Takes the plan from india-exit (entry, stop, target),
  rebuilds the rest of the day 10,000 times from the stock's own 5-minute bars
  over the last 60 days (India time, up to your 15:10 flat-by), and re-runs the
  same rule on every past session. Prints the odds of target first, stop first,
  or neither by the flat-by, where the price sits then, the average R, the
  history, and one honest verdict, then hands off to /groww-budget. Use when
  the user says "run the time machine", "replay this trade", "what are the
  odds", "simulate this trade", "monte carlo", "backtest this setup", "how
  often does this hit the target", "is this a coin flip", or after /india-exit
  writes a plan, before /groww-budget.
argument-hint: "[run | chart] [--until HH:MM] [--seed N]"
---

# india-timemachine

The Time Machine sits between the plan and the money. india-exit has set the
entry, the stop and the target. Before anything is sized or sent, this skill
asks one question: if today plays out like the stock's own last 60 days, how
often does this plan hit the target first, the stop first, or neither? It
answers with 10,000 replays and with the real record of the same rule on every
past session. It is a simulation built from the stock's own recent bars, not a
prediction. Most intraday traders in India lose money, and a good-looking
replay does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader-in/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader-in/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 14:30      # end each replay earlier than the flat-by
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol SBIN --date 2026-11-09 \
    --entry 1040 --stop 1029.20 --target 1061.60 --entry-time "9:35 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--tick 0.05` (the tick for the history's stops; default the plan's),
`--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-11-09 10:00"` (India time) to pin the clock. Symbols are NSE
symbols (SBIN, M&M, BAJAJ-AUTO); `.BO` reads BSE.

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/plan.json` | the plan from india-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, opening range, flat_by, tick_size |
| Yahoo chart API | one request: 60 days of 5-minute bars (`SBIN.NS`, `range=60d&interval=5m`), free and unofficial, can lag |
| `trader-in/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict; `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a histogram |

## How it works

1. **The pool.** It keeps the sessions before the plan's date that have every
   5-minute bar from 9:15 up to `--until`, India time. `--until` defaults to
   the plan's flat-by (15:10), the time an intraday trade must be out by, which
   also suits the data: Yahoo's NSE bars usually lack 15:15 to 15:25, so a
   replay to the 15:30 close finds no whole sessions (it says so, with that
   hint). Sessions with a missing bar earlier in the day are left out and
   listed. Every bar becomes four ratios to the bar before it: open, high, low
   and close over the previous close.
2. **One replay.** Start at the entry price. For each bar left until
   `--until`, pick a past bar from the same time of day (plus or minus 2 bars)
   at random, and scale it to the current price. Bar by bar, it checks the
   stop first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither, which
   means the trade was still open at `--until`. Green, which means target first
   or above the entry at `--until`. R is the target's R at the target, -1 at the
   stop, and otherwise the distance from the entry at `--until` divided by the
   risk. A gap or a circuit through the stop still counts as -1R here. A real
   fill can be worse.
4. **The history.** The plan's own rule (the same opening range, stop rule and
   target R, stops rounded down to the plan's tick) runs again on every one of
   those past sessions. That is what really happened, not a simulation.
5. **The verdict** is one line from the numbers. It looks only at the replays
   that hit one side, and asks how often the target came first. A bigger
   target needs to win less often to break even: 1 in (1 + R). That is 50 % at
   1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even: "Close to a
   coin flip" at 1R, "Close to break-even" at any other R. 5 to 15 points:
   "Leans toward the target" or "Leans toward the stop". 15 or more: "Strongly
   toward" one of them.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the average
   R when its sign goes against the lean.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

It refuses, in one plain line, when there are fewer than 20 usable sessions,
when the entry bar is not in the data (wrong date, not closed yet, a future
day or a holiday), or when the plan does not make sense (a stop above the
entry, an entry off the 5-minute grid, a short). A live in-progress bar is
never used, and today's bars never go into the pool.

## Steps

1. Make sure `trader-in/plan.json` is today's plan from `/india-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the flat-by, the average R, the history line, then
   the verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip, close to break-even or toward the stop, say
   so plainly. Do not dress it up. If the history and the replays disagree, say
   that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news, circuits and surveillance actions can do things those 60
   days never did. Check `/india-news` (results coming up?) before going on.
6. If the user still wants the trade, hand off to `/groww-budget` to size it.

## Example: Mon Nov 9 2026

SBIN broke its 9:15 to 9:30 range: entry 1,040.00 at 9:35 AM (bar 3), stop
1,029.20 (the range midpoint), target 1,061.60 (2R, risk 10.80 a share):

```
10,000 replays of the rest of the session, 9:35 AM to 3:10 PM (67 five-minute bars), seed 19.
Each bar is drawn whole from SBIN's own 5-min bars ... over its last 58 sessions (2026-08-13 to 2026-11-06).
  target first   2.1 %
  stop first     31.8 %
  neither        66.1 %   still open at 3:10 PM
  green          40.2 %   target first, or above the entry at 3:10 PM
  Price at 3:10 PM: median 1037.95, 90 % of replays between 1022.82 and 1053.49.
  Average R: -0.159
History: the same rule (15-min opening range, stop at the opening-range midpoint, 2R target) on each of
those 58 real sessions fired 33 times: 2 target, 19 stop, 12 neither. Average R -0.351.
Strongly toward the stop: at 2R the target has to come first in 33 % of the replays that hit one side;
it did in 6 % (target first 2.1 % vs stop first 31.8 %).
66.1 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those.
```

A 2R target was a long way for SBIN in one session, and the real history
agrees. That is the honest answer for this plan, and what the user should hear
before `/groww-budget` sizes 46 shares. A 1R plan on M&M that morning came out
"Close to a coin flip" (30.8 % vs 34.6 %).

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R plan by target first vs stop
  first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan, and
never calls a result a sure thing. It never prints a P&L or a profit. It never
uses a bar that has not closed yet, and never mixes today's bars into the pool.

## Safety

- Education and tooling, not investment advice. This pack is not a
  SEBI-registered investment adviser or research analyst.
- This skill places no orders. In this repo only groww-broker can, and only after
  groww-risk passes that exact order, the user types an explicit yes for it and
  clicks Yes on Claude Code's prompt (DRY-RUN until the user turns `live` on).
- Intraday cash-segment longs only: no F&O, options, MTF or short selling.
