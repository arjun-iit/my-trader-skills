---
name: india-news
description: >-
  News Monitor for Indian stocks. Tracks what moves the NSE stocks you watch:
  each company's NSE corporate announcements (results, board meeting outcomes,
  director changes, acquisitions, credit ratings, clarifications on price
  moves), its upcoming NSE board meetings (flags results due within a week),
  Google News India headlines per company, and how far each stock and the
  NIFTY 50 are from the previous close. Shows only what is new since the last
  check, newest first, in IST, tags each item (earnings, analyst, deal,
  capital, legal, regulatory, halt, macro...) and never repeats one. Use when
  the user says "any news on RELIANCE", "why is SBIN moving", "news on my India
  watchlist", "NSE announcements", "check the filings", "are results coming
  up", "board meeting date", "pre-market news India", "did anything break on
  Dalal Street", or after /india-scanner finds setups.
argument-hint: "[SYMBOLS]"
---

# india-news

A chart can look perfect and still be about to gap on a results day, a
surveillance action or a block deal. Before a setup becomes a trade, this
skill reads what the company told the exchange, the board-meeting calendar
and the day's headlines, for every stock on the watchlist, and tells you only
what is new. It also prints how far each stock and the NIFTY 50 are from the
previous close, so a headline and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                               # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE SBIN M&M --hours 24
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-headlines                # NSE filings and board meetings only
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers SBIN --links --json # with the headline links
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader-in/watchlist.json` | the scanner's hits (india-scanner) |
| `trader-in/plan.json` | the trade you are planning (india-exit) |
| `trader-in/portfolio.json` | what you hold (`positions[].symbol`), if you keep one |
| `trader-in/universe.txt` | company names for the headline search (india-scanner `universe`) |
| NSE corporate announcements | `https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=SBIN&from_date=..&to_date=..` |
| NSE board meetings | `https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=SBIN` |
| Google News RSS (India edition) | `https://news.google.com/rss/search?q="State Bank of India"+...&hl=en-IN&gl=IN&ceid=IN:en` |
| Yahoo Finance chart endpoint | the last price against the previous close (unofficial, can lag) |
| `trader-in/state/news.json` | what has been shown (kept 30 days) and each symbol's company name |

NSE's site wants a browser-like request and sometimes a cookie from its home
page; the tool fetches one when NSE refuses. If NSE still blocks it, the
filings part is skipped with a note and the headlines still run.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the filing or the story and read it. Quote the source and
  link it. Never summarise a paywalled story you could not read: say so. In
  the text output, headline links are left out (Google News links are long
  redirects): use `--links` or `--json` to get them.
- **Filings beat headlines.** If an NSE announcement and a headline disagree,
  the announcement is what the company told the exchange. Filings carry the
  company's PDF link.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: earnings, analyst, legal, deal, capital, people, risk, halt,
  regulatory, macro. Filings marked `!` are the categories that move prices:
  financial results, board meeting outcomes, director or KMP changes,
  resignations, acquisitions and mergers, credit ratings, insolvency or
  default, buybacks, bonuses and splits, pledges, litigation, and
  clarifications on a spurt in volume.
- **Results inside the trade.** "Upcoming board meetings" lists each symbol's
  next meeting; `RESULTS SOON` marks a results meeting within 7 days
  (`--results-days`). A stock can gap past any stop on results day.
- **MOVING** marks a stock 3 % or more from the previous close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter first: every `!` filing, every `RESULTS SOON`,
   anything tagged earnings, halt, legal, regulatory or risk, and every MOVING
   symbol.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "results on
   Friday: an overnight gap can jump past any stop, so the trade is in and out
   the same day").
4. If a story changes the picture, say so before `/india-entry` and
   `/india-exit`: results inside the trade, a surveillance (ASM/GSM) action, a
   block deal, a fund raise. Never act on a headline alone.

During the session: `/loop 15m /india-news`, or a scheduled task each morning
before 9:15 IST.

## Example: Mon Nov 9 2026

After the scanner's NIFTY 50 hits, the check (filings and moves) reads:

```
NIFTY 50     25,044.65 +0.23% vs previous close 24,986.40 at 15:31 IST
SBIN         1,042.00 +2.56% vs previous close 1,016.00 at 15:28 IST
  2026-11-07 18:10 IST  SBIN   NSE filing   State Bank of India: Analysts/Institutional Investor Meet/Con. Call Updates
      https://nsearchives.nseindia.com/corporate/SBIN_07112026180825_BSE_NSE_Transcript_07112026.pdf
```

The headlines that day put SBIN's move with the PSU bank rally ("NIFTY PSU
Bank index rallies up to 2.5% amid weak market", Upstox). For HILTON and
GRMOVER the board-meeting list read `Fri 2026-11-13 (in 4 days) Financial
Results RESULTS SOON`. When a symbol has no news, say so rather than padding
the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol and a pause between
  calls.

## Safety

Education and tooling, not investment advice; this pack is not a
SEBI-registered investment adviser or research analyst. This skill only reads
and reports. The India pack places no orders.
