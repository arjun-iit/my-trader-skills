---
name: india-alerts
description: >-
  Price Alerts for Indian stocks. Rings when a price, a stop or the NSE clock
  hits. Turns the india-exit plan into three alerts in one go (the stop, the
  target and "flat by" at your flat-by time, 15:10 IST), plus above / below /
  stop / target levels, percent moves from the previous close, any time of
  day, and the NSE opening bell, each ringing once. Knows NSE hours (9:15 to
  15:30 IST) with the 2026 holiday list, fetches free delayed quotes from Yahoo
  (or Groww's live price when a Groww login is set), and writes
  trader-in/prices.json. Use when the user says "alert me when SBIN hits",
  "watch my stop", "watch my trade", "remind me to square off by 3:10", "is the
  NSE open", "when does the market open", "get me a price for RELIANCE", or
  right after /india-exit writes a plan or /groww-broker places a trade.
argument-hint: "[clock | add | check | quote | list | reset | remove]"
---

# india-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. With a Groww trade the OCO smart order (the
stop-loss and the target) already sits at Groww and does the work; the alerts
tell the user what happened and when it is time to be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It uses the shared helpers in `../groww-broker/growwlib.py`.

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan               # stop, target, flat-by from trader-in/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SBIN --stop 1029.20
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol SBIN --target 1061.60
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol ITC --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote SBIN M&M NIFTY --write  # -> trader-in/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset sbin-plan-stop
```

`--now "2026-11-09 15:10"` (before the command) pins the clock in India time;
with it, give prices by hand: `check --price SBIN=1029`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/plan.json` | the trade from india-exit, for `add --from-plan` |
| `trader-in/alerts.json` | the rules |
| `trader-in/state/alerts.json` | which rules rang and when, so each rings once |
| `trader-in/prices.json` | the latest quotes, with `quote --write` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above the level | after `reset` |
| `below` / `stop` | price at or below the level | after `reset` |
| `pct_move` | moved that percent from the previous close, either way | once a trading day |
| `time` | the India clock passed `at` on a trading day | once a trading day |
| `opening_bell` | the NSE is open | once a trading day |

A rule with a date is watched on that date only; `--from-plan` rules carry the
plan's date, so yesterday's stop never rings today.

## Quotes and the clock

- Yahoo's chart endpoint (`SBIN.NS`), free and unofficial, delayed, one request
  per symbol. With a Groww login in the environment the price comes from
  Groww's live-price API instead. Never present a quote as live: a check reads
  the latest quote, and a price can pass a level between two checks.
- NSE's regular session is 9:15 to 15:30 IST, closed on weekends and the 2026
  holidays (nseindia.com, read 2026-11-09). 2027 is not in the list yet, and
  it says so. Muhurat trading is not covered.

## Steps

1. Right after india-exit writes the plan (or groww-broker places the trade),
   run `add --from-plan`. Confirm the three alerts back to the user: the stop,
   the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes with `/loop 5m` or a
   scheduled task. It fetches only the symbols in today's rules.
3. Tell the user what rang, one line each. A stop or target alert means the
   Groww OCO has probably filled: check `groww.py status`. "Flat by" means
   close anything still open: `groww.py close --symbol SYM`, then /groww-risk,
   then the user's yes. Groww squares off open intraday positions from 3:20 PM
   (Rs 50 + GST each). Never act for them.

## Example: Mon Nov 9 2026

SBIN plan: stop 1,029.20, target 1,061.60, flat by 15:10. `add --from-plan`
writes `sbin-plan-stop`, `sbin-plan-target` and `sbin-flat-by` for that day.

```
NSE OPEN (2026-11-09 11:00 IST, regular session 9:15 to 15:30).
ALERT SBIN STOP: Rs 1029.00 is at or below Rs 1029.20 ... If the trade is on at Groww, its OCO stop-loss
has probably filled: check groww.py status. Nothing was sold by this alert.
```

It rings once. On the Nov 10 holiday, `clock` reads: `NSE CLOSED (... NSE
holiday: Diwali-Balipratipada). Next open: Wed 2026-11-11 09:15 IST.`

## What it never does

It never places, cancels or changes an order, never claims a quote is live,
and never predicts where a price is going.

## Safety

Education and tooling, not investment advice; this pack is not a
SEBI-registered investment adviser or research analyst. This skill only reads
prices and the clock. It never places an order.
