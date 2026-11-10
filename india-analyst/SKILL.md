---
name: india-analyst
description: >-
  The Earnings Analyst for Indian stocks. Reads a company's quarterly results
  straight from the filings it made with NSE (the XBRL data in NSE's integrated
  financial filings) and sums up what changed: revenue, net profit, diluted
  EPS, net margin and a derived operating margin, quarter by quarter, each
  against the same quarter a year earlier, with the last four quarters added
  up. For banks: interest earned, net interest income and profit. Consolidated
  by default, standalone on request, amounts in Rs crore, with the next results
  date when NSE lists one. Use when the user says "how did Tata Tech's results
  look", "analyze the results", "what changed this quarter", "is revenue
  growing", "Q2 results", "compare margins", "when are results due", or before
  trading an NSE stock around its results.
argument-hint: "SYMBOLS [--quarters N] [--standalone]"
---

# india-analyst

Results move stocks more than anything else on the calendar, and the press
release puts the best number first. This skill reads the numbers the company
actually filed with the exchange, lines them up quarter by quarter, and says
plainly what changed. For a day trader it answers two questions before a
trade: what did the last report say, and is the next one inside this trade?

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It uses the NSE fetcher in `../india-news/news.py`. No keys.

```bash
python3 "${CLAUDE_SKILL_DIR}/results.py" TATATECH
python3 "${CLAUDE_SKILL_DIR}/results.py" SBIN RELIANCE --quarters 8
python3 "${CLAUDE_SKILL_DIR}/results.py" TATATECH --standalone --json
python3 "${CLAUDE_SKILL_DIR}/results.py" INFY TCS --metrics trader-in/metrics.json
```

## What it reads and writes

- NSE's integrated filings list for the symbol
  (`/api/integrated-filing-results?...&type=Integrated Filing- Financials`),
  one request, then each quarter's XBRL file on nsearchives.nseindia.com (one
  request per quarter, cached in `trader-in/state/results-cache.json`).
- NSE's board-meetings feed, for the next results date.
- Writes `trader-in/metrics.json` with `--metrics`: one row per company.
- Earnings calls: it does not fetch transcripts. If the user pastes one (or
  india-news shows the transcript filing), read it and sum up what management
  said changed.

## The rules

- **Filed numbers first.** Every figure comes from the XBRL the company
  filed. The latest filing's link is printed: open it when a number needs
  context.
- **The quarter column.** A filing carries the quarter and the year to date.
  The tool takes the three-month column only.
- **Compare like with like.** Growth is against the same quarter a year
  earlier, consolidated with consolidated. NSE's integrated filings start with
  the quarter ended March 2025, so older comparisons show "-".
- **Say what is derived.** Companies do not file an operating profit line.
  Operating margin here is (profit before tax + finance costs - other income)
  / revenue, and is labelled derived. Banks have no such margin: they get net
  interest income (interest earned minus interest expended) instead.
- **Flag one-offs.** It notes exceptional items in the latest quarter, and
  other income that is a large share of profit before tax. A quarter that
  looks odd (a profit that collapses and recovers) usually has one: open that
  quarter's filing before explaining it.
- **Net profit** is profit for the period, before minority interest.

## Steps

1. Run it for the companies in question (or the symbols on the watchlist).
2. Open the latest filing link when the numbers need context, and read it.
3. Write the summary: one line on the quarter (revenue, profit, EPS, growth),
   one on margins, one on anything unusual, and the link. Plain words, real
   numbers, no adjectives the numbers do not support.
4. If the next results fall inside the planned trade, say it before
   `/india-exit` sets the stop: a stop does not protect against a gap. When NSE
   lists no meeting yet, say that the date is not known, not that there is none.

## Example: run on 2026-11-10

`results.py TATATECH`: quarter ended 2026-09-30, consolidated: revenue
Rs 1,698.80 crore (+28.4 % against a year earlier), net profit Rs 171.44 crore
(+3.6 %), diluted EPS Rs 4.22 (+3.4 %); net margin 10.1 %, derived operating
margin 12.9 %; last four quarters Rs 6,301.38 crore revenue and Rs 13.86 EPS.
Filed 2026-10-14. The December 2025 quarter shows a net profit of only
Rs 6.64 crore: a one-off to read in that filing before saying why.

`results.py SBIN`: interest earned Rs 141,073.34 crore (+10.2 %), net profit
Rs 25,741.47 crore (+19.7 %), diluted EPS Rs 27.47, net interest income
Rs 56,800.76 crore. Filed 2026-11-03.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell.
- Never invents a number the filing does not have.
- Never hammers NSE: one request per filing, cached.

## Safety

Education and tooling, not investment advice; this pack is not a
SEBI-registered investment adviser or research analyst. This skill only reads
and reports. It never places an order.
