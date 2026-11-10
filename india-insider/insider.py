#!/usr/bin/env python3
"""Insider Tracker for india-insider: the trades company insiders and big
traders must disclose in India, read from NSE. Standard library only.

    python3 insider.py trades RELIANCE SBIN            # insider disclosures, last 180 days
    python3 insider.py trades TATATECH --days 365 --json
    python3 insider.py new                             # only disclosures not shown before (watchlist + plan + portfolio)
    python3 insider.py new --tickers RELIANCE INFY
    python3 insider.py deals                           # the latest session's bulk and block deals
    python3 insider.py deals --days 7 --mine           # the last week, your symbols only
    python3 insider.py deals --symbol COHANCE --days 30

Two public feeds, both filed with the exchange:
  insider disclosures  SEBI (Prohibition of Insider Trading) Regulations: a
      promoter, director, key manager or designated person reports a trade,
      and the company reports it to the exchange within two trading days of
      being told.
      https://www.nseindia.com/api/corporates-pit?index=equities&symbol=<SYM>&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY
  bulk and block deals  a bulk deal is one client trading more than 0.5 % of a
      company's shares in a day; a block deal is a single large trade in the
      block window. NSE names the client, the side, the quantity and the price.
      https://www.nseindia.com/api/snapshot-capital-market-largedeal   (latest session)
      https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=..&to=..

India has no public report of politicians' share trades like the US STOCK
Act's. These disclosures are the closest filed record of what people close to
a company did. They are late by design: a disclosure says what someone did
days ago, not what anyone will do next.

Each row shows who, their category, buy or sell, the mode (a market purchase
is not an ESOP allotment, a gift or a pledge), shares, rupee value, the trade
dates, when it reached the exchange and how many days that took. "!" marks a
market purchase or sale by a promoter, director or key manager.
State: trader-in/state/insider.json remembers what `new` has shown.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "india-news"))
import news as N  # noqa: E402

PIT = "https://www.nseindia.com/api/corporates-pit?index=equities&symbol=%s&from_date=%s&to_date=%s"
SNAP = "https://www.nseindia.com/api/snapshot-capital-market-largedeal"
HIST = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals?optionType=%s&from=%s&to=%s"
STATE = "trader-in/state/insider.json"
INSIDERS = ("promoter", "director", "key manager", "kmp")
NOTE = ("Public disclosures, filed after the trade. They say what someone did, not what anyone will do next, "
        "and not why. Not advice.")


def today_ist():
    return N.to_ist(datetime.datetime.now(datetime.timezone.utc)).date()


def _date(text, fmts=("%d-%b-%Y %H:%M", "%d-%b-%Y")):
    for f in fmts:
        try:
            return datetime.datetime.strptime(str(text).strip().title(), f).date()
        except ValueError:
            pass
    return None


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def inr(v):
    if v >= 1e7:
        return "Rs %.2f crore" % (v / 1e7)
    if v >= 1e5:
        return "Rs %.2f lakh" % (v / 1e5)
    return "Rs {:,.0f}".format(v)


def parse_pit(data, sym):
    out = []
    for r in N._rows(data, "NSE insider disclosures for %s" % sym):
        traded_to = _date(r.get("acqtoDt")) or _date(r.get("acqfromDt"))
        disclosed = _date(r.get("date"))
        cat = " ".join(str(r.get("personCategory") or "-").split())
        mode = " ".join(str(r.get("acqMode") or "-").split())
        side = str(r.get("tdpTransactionType") or "-").strip()
        market = mode.lower() in ("market purchase", "market sale")
        out.append({
            "id": "pit:%s:%s" % (r.get("pid") or r.get("did"), r.get("did")), "symbol": sym,
            "company": r.get("company"), "who": " ".join(str(r.get("acqName") or "?").split()), "category": cat,
            "side": side, "mode": mode, "security": r.get("secType"), "shares": _num(r.get("secAcq")),
            "value": _num(r.get("secVal")), "traded_from": str(_date(r.get("acqfromDt")) or ""),
            "traded_to": str(traded_to or ""), "disclosed": str(disclosed or ""),
            "delay_days": (disclosed - traded_to).days if disclosed and traded_to else None,
            "holding_before_pct": r.get("befAcqSharesPer"), "holding_after_pct": r.get("afterAcqSharesPer"),
            "regulation": r.get("anex"), "link": r.get("xbrl"),
            "flag": market and any(k in cat.lower() for k in INSIDERS)})
    out.sort(key=lambda x: (x["disclosed"], x["traded_to"]), reverse=True)
    return out


def fetch_pit(fetcher, sym, days):
    end = today_ist()
    start = end - datetime.timedelta(days=days)
    url = PIT % (urllib.parse.quote(sym, safe=""), start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))
    return parse_pit(fetcher.get(url, "nse-pit-%s.json" % sym, "nse"), sym)


def pit_lines(rows):
    out = []
    for r in rows:
        when = r["traded_to"] if r["traded_from"] in ("", r["traded_to"]) else "%s to %s" % (r["traded_from"], r["traded_to"])
        delay = ("disclosed %s, %d day%s later" % (r["disclosed"], r["delay_days"], "" if r["delay_days"] == 1 else "s")
                 if r["delay_days"] is not None else "disclosed %s" % (r["disclosed"] or "?"))
        out.append("%s %-11s %s (%s): %s, %s, %s shares, %s; traded %s, %s" % (
            "!" if r["flag"] else " ", r["symbol"], r["who"], r["category"], r["side"].lower(), r["mode"].lower(),
            "{:,.0f}".format(r["shares"]), inr(r["value"]), when, delay))
    return out


def symbols_from(a):
    if getattr(a, "tickers", None):
        return [N.norm_symbol(t) for t in a.tickers]
    return sorted(s for s in N.watch_from_files() if N.nse_symbol(s))


def cmd_trades(a):
    fetcher = N.Fetcher(offline=a.offline)
    rows, failed = [], 0
    for sym in [N.norm_symbol(s) for s in a.symbols]:
        try:
            got = fetch_pit(fetcher, sym, a.days)
            rows += got
            if not a.json and not got:
                print("  %-11s no insider disclosures on NSE in the last %d days." % (sym, a.days))
        except N.NewsError as e:
            failed += 1
            print("insider.py: %s: %s" % (sym, e), file=sys.stderr)
    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        if rows:
            print("Insider disclosures on NSE, last %d days (newest first; ! = market trade by a promoter, director "
                  "or key manager):" % a.days)
            print("\n".join(pit_lines(rows[:a.show])))
            if len(rows) > a.show:
                print("  ... and %d more (--show N)." % (len(rows) - a.show))
        print(NOTE)
    return 0 if rows and not failed else 1


def cmd_new(a):
    syms = symbols_from(a)
    if not syms:
        print("insider.py: no symbols. Pass --tickers, or run /india-scanner first.", file=sys.stderr)
        return 2
    state = N.load_json(a.state) or {"seen": {}}
    first = not state["seen"]
    fetcher = N.Fetcher(offline=a.offline)
    fresh = []
    for sym in syms:
        try:
            for r in fetch_pit(fetcher, sym, max(a.days, 45) if first else a.days):
                if r["id"] not in state["seen"]:
                    fresh.append(r)
                    state["seen"][r["id"]] = r["disclosed"]
        except N.NewsError as e:
            print("note: %s: %s" % (sym, e))
    if a.json:
        print(json.dumps(fresh, indent=2))
    elif fresh:
        print("NEW insider disclosures for %s:" % ", ".join(syms))
        print("\n".join(pit_lines(fresh)))
        print(NOTE)
    else:
        print("Nothing new for %s." % ", ".join(syms))
    if not a.dry_run:
        os.makedirs(os.path.dirname(a.state), exist_ok=True)
        with open(a.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
    return 0 if fresh else 1


def parse_deals(fetcher, days):
    """[{kind, date, symbol, name, client, side, qty, price}] newest first."""
    out = []
    if days <= 1:
        doc = json.loads(fetcher.get(SNAP, "nse-largedeal.json", "nse").decode("utf-8"))
        for kind, key in (("bulk", "BULK_DEALS_DATA"), ("block", "BLOCK_DEALS_DATA")):
            for r in doc.get(key) or []:
                out.append({"kind": kind, "date": str(_date(r.get("date")) or ""), "symbol": r.get("symbol"),
                            "name": r.get("name"), "client": " ".join(str(r.get("clientName") or "").split()),
                            "side": str(r.get("buySell") or "").upper(), "qty": _num(r.get("qty")),
                            "price": _num(r.get("watp"))})
    else:
        end = today_ist()
        start = end - datetime.timedelta(days=days)
        for kind, opt in (("bulk", "bulk_deals"), ("block", "block_deals")):
            url = HIST % (opt, start.strftime("%d-%m-%Y"), end.strftime("%d-%m-%Y"))
            for r in N._rows(fetcher.get(url, "nse-%s.json" % opt, "nse"), "NSE %s deals" % kind):
                out.append({"kind": kind, "date": str(_date(r.get("BD_DT_DATE")) or ""), "symbol": r.get("BD_SYMBOL"),
                            "name": r.get("BD_SCRIP_NAME"),
                            "client": " ".join(str(r.get("BD_CLIENT_NAME") or "").split()),
                            "side": str(r.get("BD_BUY_SELL") or "").upper(), "qty": _num(r.get("BD_QTY_TRD")),
                            "price": _num(r.get("BD_TP_WATP"))})
    for d in out:
        d["value"] = d["qty"] * d["price"]
    out.sort(key=lambda d: (d["date"], d["value"]), reverse=True)
    return out


def cmd_deals(a):
    fetcher = N.Fetcher(offline=a.offline)
    try:
        deals = parse_deals(fetcher, a.days)
    except (N.NewsError, ValueError) as e:
        print("insider.py: %s" % e, file=sys.stderr)
        return 1
    total = len(deals)
    mine = set(symbols_from(a)) if a.mine else None
    if a.symbol:
        deals = [d for d in deals if str(d["symbol"]).upper() == a.symbol.upper()]
    if mine is not None:
        deals = [d for d in deals if str(d["symbol"]).upper() in mine]
    if a.json:
        print(json.dumps(deals, indent=2))
        return 0 if deals else 1
    span = "the latest session" if a.days <= 1 else "the last %d days" % a.days
    print("NSE bulk and block deals, %s: %d in all%s, largest first within each day." % (
        span, total, (", %d shown after your filter" % len(deals)) if (a.symbol or a.mine) else ""))
    for d in deals[:a.show]:
        print("  %s  %-5s %-11s %-4s %14s shares at Rs %-9.2f %-18s %s" % (
            d["date"], d["kind"], d["symbol"], d["side"], "{:,.0f}".format(d["qty"]), d["price"], inr(d["value"]),
            d["client"]))
    if len(deals) > a.show:
        print("  ... and %d more (--show N)." % (len(deals) - a.show))
    if not deals:
        print("  None.")
    print("A name on a deal is a client's reported trade that day. The same client often buys and sells the same "
          "day; a deal is not a signal. Not advice.")
    return 0 if deals else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Insider Tracker (india-insider): NSE insider disclosures and large deals.")
    ap.add_argument("--offline", metavar="DIR")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("trades", help="insider disclosures for symbols")
    p.add_argument("symbols", nargs="+")
    p.add_argument("--days", type=int, default=180)
    p.add_argument("--show", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("new", help="only disclosures not shown before")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--state", default=STATE)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("deals", help="bulk and block deals")
    p.add_argument("--days", type=int, default=1)
    p.add_argument("--symbol")
    p.add_argument("--mine", action="store_true", help="only your watchlist / plan / portfolio symbols")
    p.add_argument("--tickers", nargs="*")
    p.add_argument("--show", type=int, default=40)
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    return {"trades": cmd_trades, "new": cmd_new, "deals": cmd_deals}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
