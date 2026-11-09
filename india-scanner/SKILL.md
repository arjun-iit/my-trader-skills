---
name: india-scanner
description: >-
  Chart Scanner for Indian stocks. Scans NSE charts for setups: opening-range
  breakouts, flags, oversold bounces. Builds the list of NSE stocks that can be
  traded intraday (EQ series) from NSE's own equity list, or an index's members
  (NIFTY 50, NIFTY NEXT 50, NIFTY 100, 200, 500, MIDCAP 100, SMALLCAP 100, BANK)
  from niftyindices.com, runs a scan of daily closes (20-day closing-high
  breakout, bull flag, reclaimed the 20-day average, oversold, oversold bounce)
  and an intraday opening-range breakout scan on 5-minute bars (9:15 to 9:30
  IST range), then writes trader-in/watchlist.json for india-news and
  india-entry. BSE symbols (.BO) and the NIFTY, BANKNIFTY and SENSEX indices
  work too. Free Yahoo data, bar by bar, times in IST. Use when the user says
  "scan the Indian market", "scan NSE", "scan the Nifty 50", "Nifty breakouts",
  "opening range breakouts in India", "find me setups on NSE", "what is
  breaking out on Dalal Street", "oversold Nifty stocks", "build my India
  watchlist", or at the start of an Indian trading day before /india-news.
argument-hint: "[universe | daily | orb] [--index nifty50] [--at HH:MM]"
---

# india-scanner

The Chart Scanner is the first step of a day trade on NSE. It reads free
charts and lists the ones that match a rule. That is all it does. A scan is a
list of charts that match a rule, not a list of stocks to buy, and a setup on a
chart is not a prediction. SEBI's own studies found that most individual
intraday traders in India lose money; the scanner does not change that. It only
saves you from flipping through 2,400 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # every NSE EQ stock -> trader-in/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --index nifty500      # or one index's members
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before 9:15 or after 15:30 IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --index nifty50          # an index, fetched on the spot
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader-in/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --index nifty50 --at 10:00 # cut the clock at 10:00 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --now "2026-11-09 10:00"   # replay a session from the last week
```

Indexes for `--index`: nifty50, niftynext50, nifty100, nifty200, nifty500,
niftymidcap100, niftysmallcap100, niftybank.

Symbols are NSE symbols as NSE writes them (RELIANCE, M&M, BAJAJ-AUTO); the
tool reads them on Yahoo as RELIANCE.NS. Add .BO for the BSE price
(RELIANCE.BO). NIFTY, BANKNIFTY and SENSEX stand for the indices ^NSEI,
^NSEBANK and ^BSESN (an index has no volume, so it skips the turnover floor).

Flags: `--min-price 20` (rupees) and `--min-turnover` (rupees traded, in crore:
daily 5, orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-11-09 10:00"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason is
one plain line).

## What it reads and writes

The India pack keeps its files in `trader-in/`, apart from the US pack's
`trader/`, so a US watchlist never feeds an Indian entry.

| file | what |
| --- | --- |
| `trader-in/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily`, `orb` and india-news (for company names) |
| `trader-in/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of` (IST), `symbols_from`, `universe`, `scanned`, `failed`, `hits[]`. india-news and india-entry read `hits[].symbol` |
| `trader-in/config.json` | read by `orb`: `no_entries_after` (default 15:00 IST) |

## The universe

`universe` downloads NSE's list of listed equities (`EQUITY_L.csv`,
nsearchives.nseindia.com) and keeps the EQ series: normal rolling settlement,
where intraday trading is allowed. BE and BZ are trade-for-trade (delivery
only, no intraday) and are left out. On the Nov 9 2026 file: 2,645 rows, 2,396
EQ kept, 207 BE and 42 BZ dropped.

`universe --index NAME` instead downloads that index's members from
niftyindices.com (`ind_<name>list.csv`). `daily --index` and `orb --index` do
the same on the spot without writing a file.

## The daily scan (before the open or after the close)

Yahoo's spark endpoint returns closes for 20 symbols a request: the NIFTY 50
is 3 requests, the full EQ list 120. Liquidity comes from the spark meta: last
price at least `--min-price` and last price x last session's volume at least
`--min-turnover` crore. Run it before 9:15 or after 15:30 IST: during the
session the volume is the day's so far, and today's daily bar is left out
until 15:30.

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (rupees traded).

## The opening-range breakout scan (during the session)

NSE's session runs 9:15 to 15:30 IST (the 9:00 to 9:08 pre-open auction comes
before it). The opening range is the first `--or-minutes` (15): the 9:15, 9:20
and 9:25 five-minute bars. A hit is the first completed 5-minute bar to CLOSE
above the range high, at that bar's close time (the 9:30 bar closes at 9:35).

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a request.
   A chart goes on when a completed bar after the range closed at or above the
   highest close of the range (a necessary condition), or when spark is missing
   a bar (spark drops one now and then; the full chart decides).
2. Stage 2, each candidate: the v8 chart with real highs and lows, one request
   each. Range high = the highest high of the range bars, range low = the
   lowest low. Stage 2 checks at most `--max-confirm` (200), the most traded
   first, and prints PARTIAL with the count it skipped.

Yahoo's NSE bars have gaps. The full chart is nearly always complete through
the day but usually misses the 15:15 to 15:25 bars, and spark sometimes drops
a mid-day bar. So a chart is usable when every opening-range bar is there and
no more than 20 % of the bars to the cutoff are missing (more is a halt, a
circuit or a thin stock). A hit that comes after a missing bar says so: the
real first close above could have been inside the gap.

Before 9:35 AM, on a weekend or an NSE holiday, after the close, or after
`no_entries_after` (an expired setup) it prints one plain line and writes
nothing. Muhurat trading (the special Diwali evening session) is not covered.

Timing, Mon Nov 9 2026 replayed at 10:00 AM IST: the NIFTY 50 took 14 seconds
(3 spark requests, 28 v8 charts). The full EQ list took 2.5 minutes: 2,395 of
2,396 charts fetched, 1,681 usable and liquid (607 thin stocks had too many
missing bars), 817 candidates, the 200 most traded checked, 101 broke out. For
a fast answer, scan an index or your own list.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, never tick by tick, and NSE's real-time feed is not used.
  query2 first, query1 on a 429 or 5xx, one retry after a 5-second wait.
- Partial results are said as partial: "Fetched 2,395 of 2,396 charts (1
  failed...)". Never round a partial scan up to "the whole market".
- NSE trading holidays for 2026 (nseindia.com holiday master, read
  2026-11-09). NSE publishes the 2027 list in December: until it is added here,
  2027 holidays are not known to the tool.
- Times are IST (UTC+5:30, no daylight saving).

## Steps

1. Once a week or so: `scan.py universe` (or `--index nifty500`).
2. Before the open: `scan.py daily --index nifty50` (or the universe). Tell the
   user the counts (fetched, liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:00 is a common time): `scan.py orb`. Tell the user how
   many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/india-news SYMBOL` (what is moving
   it), then `/india-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example: Mon Nov 9 2026

NIFTY 50, replayed at 10:00 AM IST (real Yahoo charts):

```
python3 scan.py orb --index nifty50 --now "2026-11-09 10:00"
Fetched 50 of 50 charts; 50 usable; 28 candidates, 28 confirmed with real highs and lows.
4 of 50 charts closed above their opening-range high by 10:00 AM:
   9:35 AM  DRREDDY         Rs 1,388.90  range 1,370.40 to 1,386.00  +0.21% over
   9:35 AM  SBIN            Rs 1,040.00  range 1,023.45 to 1,034.95  +0.49% over
   9:55 AM  INDIGO          Rs 5,458.50  range 5,404.10 to 5,451.50  +0.13% over
  10:00 AM  NTPC              Rs 325.05  range 322.20 to 324.70  +0.11% over
```

SBIN's range is the 9:15 to 9:25 bars, high 1,034.95, low 1,023.45; the 9:30
bar closed at 1,040.00, 0.49 % over the high, at 9:35 AM. The user picks SBIN
and Claude hands it to `/india-news SBIN`, then `/india-entry SBIN`.

## Rules

- Say what was scanned: which list, how many symbols, how many charts came
  back, how many were usable, and the time of the data in IST.
- Say "matched a rule", never "a buy", "a winner", "a multibagger" or "a good
  trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not investment advice. This pack is not a
  SEBI-registered investment adviser or research analyst, and a scan hit is a
  rules check, not a recommendation.
- This skill places no orders. In this repo only groww-broker can, and only after
  groww-risk passes that exact order, the user types an explicit yes for it and
  clicks Yes on Claude Code's prompt (DRY-RUN until the user turns `live` on).
  With any other broker, the order is the user's own.
- No F&O, options, margin (MTF) or short selling: the pack plans cash-segment
  longs only.
