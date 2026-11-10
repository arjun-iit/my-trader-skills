---
name: india-insider
description: >-
  The Insider Tracker for Indian stocks: the Indian counterpart to the Pelosi
  tracker. India has no public report of politicians' share trades, so this
  reads the closest filed record: insider-trading disclosures on NSE (trades by
  promoters, directors, key managers and their relatives under SEBI's insider
  trading regulations) and NSE's bulk and block deals (named clients trading
  large quantities). Shows who, their category, buy or sell, the mode (market
  purchase, off market, ESOP, pledge), shares, rupee value, the trade date and
  how many days it took to disclose; alerts once per new disclosure for your
  symbols. Use when the user says "what are promoters buying", "insider trades
  in RELIANCE", "did any director sell", "promoter buying", "bulk deals today",
  "block deals", "who bought SBIN", "big investor trades", "track insiders", or
  asks for the Indian version of the Pelosi skill.
argument-hint: "[trades SYMBOLS | new | deals] [--days N]"
---

# india-insider

In the US, members of Congress must report their trades, and trader-pelosi
reads those reports. India has no such report: MPs declare assets, not trades.
What India does publish is what people close to a company did with its shares,
and who traded it in size. This skill reads both from NSE.

It is fast. It is not early. A disclosure reaches the exchange days after the
trade, so by the time anyone reads it the trade is old. There is no inside
information here: only public filings, read quickly. Say that every time.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
It uses the NSE fetcher in `../india-news/news.py`. No keys.

```bash
python3 "${CLAUDE_SKILL_DIR}/insider.py" trades RELIANCE SBIN          # insider disclosures, last 180 days
python3 "${CLAUDE_SKILL_DIR}/insider.py" trades TATATECH --days 365 --json
python3 "${CLAUDE_SKILL_DIR}/insider.py" new                           # unseen ones, for watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/insider.py" new --tickers RELIANCE INFY
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals                         # the latest session's bulk and block deals
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --days 7 --mine         # last week, your symbols only
python3 "${CLAUDE_SKILL_DIR}/insider.py" deals --symbol COHANCE --days 30
```

## What it reads and writes

- **Insider disclosures**, per symbol:
  `nseindia.com/api/corporates-pit?index=equities&symbol=..&from_date=..&to_date=..`
  (NSE answers this feed per symbol; a market-wide query came back empty when
  tested, so the tool asks symbol by symbol).
- **Bulk and block deals**: `/api/snapshot-capital-market-largedeal` for the
  latest session, `/api/historicalOR/bulk-block-short-deals` for a date range.
- Writes `trader-in/state/insider.json`: the disclosures `new` has shown, so
  each alerts once. Reads the watchlist, plan and portfolio for your symbols.

## The rules

- **Quote the filing.** Every row comes from NSE, with the filing's link in
  `--json`. Third-party trackers can be off; the exchange record wins.
- **Category matters.** Promoter, promoter group, director, key managerial
  person, designated person, immediate relative, other. `!` marks a market
  purchase or sale by a promoter, director or key manager.
- **Mode matters.** A market purchase is a decision to buy at the market
  price. An ESOP allotment, a gift, an inter-se transfer, an off-market deal
  or a pledge is not. Never report a pledge or an ESOP as "buying".
- **Say the delay.** Each row shows the days between the trade and the
  disclosure. The company must tell the exchange within two trading days of
  being told, so a long gap is worth saying.
- **Deals are two-sided.** A bulk deal is one client trading over 0.5 % of a
  company's shares in a day; a block deal is one large trade in the block
  window. The same client often buys and sells the same day (a trading firm),
  and every block has a buyer and a seller. A name on a deal is not a signal.
- **No disclosures is an answer.** Large companies with government or
  institutional owners can go a year without one. Say "none in the last N
  days", not "insiders are neutral".

## Steps

1. `trades SYMBOL` for a stock the user asks about; `new` for their symbols
   (run it with `/loop 1h` or once a morning); `deals` for the day's large trades.
2. Read out the rows that matter first: `!` rows, then the largest by value.
   Give who, category, side, mode, shares, value, trade date and the delay.
3. End with the caveat: a public disclosure, filed after the trade; it says
   what someone did, not what anyone will do next.
4. If the user wants to act on it, it goes the long way: a disclosure is days
   old, so it is a reason to look at a chart, not a setup. `/india-scanner` and
   `/india-entry` decide if there is a trade today.

## Example: run on 2026-11-10

```
insider.py trades RELIANCE --days 400
  RELIANCE    BALANADU NARAYAN (Other): sell, off market, 2,320 shares, Rs 32.94 lakh; traded 2026-02-13,
              disclosed 2026-02-18, 5 days later
  RELIANCE    Shaila Narayan (Immediate relative): buy, off market, 3,120 shares, Rs 44.30 lakh; ...
```

Both are off-market transfers between related people on the same day, not
market buying or selling. SBIN and TATATECH had no disclosures in 400 days.

```
insider.py deals
  2026-11-09  bulk  DATAPATTNS  SELL   403,762 shares at Rs 3942.71   Rs 159.19 crore   GRAVITON RESEARCH CAPITAL LLP
  2026-11-09  bulk  DATAPATTNS  BUY    400,454 shares at Rs 3943.90   Rs 157.94 crore   GRAVITON RESEARCH CAPITAL LLP
  2026-11-09  block COHANCE     SELL 3,000,000 shares at Rs 430.00    Rs 129.00 crore   JASTI FAMILY TRUST
```

The DATAPATTNS rows are one trading firm buying and selling the same day. The
COHANCE block is a family trust selling to a named buyer.

## What it never does

- Never says or implies anyone has inside information, or that copying an
  insider or a big investor beats the market. No return claims.
- Never calls a pledge, a gift or an ESOP allotment a purchase.
- Never places an order.

## Safety

Education and tooling, not investment advice; this pack is not a
SEBI-registered investment adviser or research analyst. This skill only reads
and reports public filings. It never places an order.
