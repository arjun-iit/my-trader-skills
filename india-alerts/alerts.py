#!/usr/bin/env python3
"""Price Alerts for india-alerts: rings when a price, a stop or the NSE clock hits.
Standard library only. An alert is information, never an order.

    python3 alerts.py clock                              # NSE open or closed, next open
    python3 alerts.py add --from-plan                    # stop, target and flat-by from trader-in/plan.json
    python3 alerts.py add --opening-bell
    python3 alerts.py add --symbol SBIN --stop 1029.20   # or --target, --above, --below
    python3 alerts.py add --symbol ITC --pct 3           # 3 % from the previous close, either way
    python3 alerts.py add --at 15:10 --label "flat by"
    python3 alerts.py check                              # exit 0 = something rang
    python3 alerts.py quote SBIN M&M --write             # -> trader-in/prices.json
    python3 alerts.py list | reset ID | remove ID

Rule types: above / target (price at or above), below / stop (price at or
below), pct_move (percent from the previous close), time (the India clock
passed HH:MM on a trading day), opening_bell (the NSE is open). Each rings
once: price rules until `reset`, pct, time and bell rules once a trading day.
A rule with a "date" is watched on that date only, so yesterday's stop never
rings today.

Quotes: Yahoo's free chart endpoint (SBIN.NS; unofficial, delayed, can lag),
and Groww's live price instead when a Groww login is in the environment. A
check reads the latest quote; it is not a live feed, and a price can pass a
level between two checks. Times are India time (IST). --now pins the clock;
with --now, give prices with --price SYMBOL=PRICE (repeatable).
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "groww-broker"))
import growwlib as g  # noqa: E402

RULES = "trader-in/alerts.json"
STATE = "trader-in/state/alerts.json"
PRICES = "trader-in/prices.json"
PLAN = "trader-in/plan.json"
PRICE_TYPES = {"above": 1, "target": 1, "below": -1, "stop": -1}
INDEXES = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}


def norm(sym):
    s = sym.strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clock(now):
    """(is_open, words) for the NSE cash market at `now` (naive UTC)."""
    local = g.to_ist(now)
    ok, why = g.trading_day(local.date())
    stamp = local.strftime("%Y-%m-%d %H:%M")
    if ok and g.OPEN <= local.time() < g.CLOSE:
        return True, "NSE OPEN (%s IST, regular session 9:15 to 15:30)." % stamp
    d = local.date() if ok and local.time() < g.OPEN else local.date() + datetime.timedelta(days=1)
    for _ in range(15):
        if g.trading_day(d)[0]:
            break
        d += datetime.timedelta(days=1)
    reason = why if not ok else ("before the open" if local.time() < g.OPEN else "after the close")
    note = "" if d.year in g.CALENDAR_YEARS else " (holiday list covers %s only)" % g.CALENDAR_YEARS[0]
    return False, "NSE CLOSED (%s IST, %s). Next open: %s %s 09:15 IST%s." % (
        stamp, reason, d.strftime("%a"), d.isoformat(), note)


def quote(sym, now, given=None, lookup=True):
    """{"price", "prev_close", "time", "source"} or raises GrowwError."""
    if given is not None:
        return {"price": float(given), "prev_close": None, "time": g.ist_stamp(now), "source": "the price you gave"}
    if not lookup:
        raise g.GrowwError("no price for %s: with --now give --price %s=PRICE" % (sym, sym))
    exch = "BSE" if sym.endswith(".BO") else "NSE"
    base = sym[:-3] if sym.endswith(".BO") else sym
    ysym = INDEXES.get(base) or base + (".BO" if exch == "BSE" else ".NS")
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m" % urllib.parse.quote(ysym, safe="")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": g.YAHOO_UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            meta = json.load(r)["chart"]["result"][0]["meta"]
    except Exception as e:  # noqa: BLE001
        raise g.GrowwError("no quote for %s from Yahoo: %s" % (sym, e))
    if not meta.get("regularMarketPrice"):
        raise g.GrowwError("Yahoo sent no price for %s" % sym)
    t = meta.get("regularMarketTime")
    q = {"price": float(meta["regularMarketPrice"]), "source": "Yahoo (delayed)",
         "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
         "time": g.ist_stamp(g.EPOCH + datetime.timedelta(seconds=int(t))) if t else None}
    if g.credential_flow() and base not in INDEXES:
        try:
            v = (g.Groww().ltp(["%s_%s" % (exch, base)]) or {}).get("%s_%s" % (exch, base))
            if v:
                q.update(price=float(v), source="Groww live price", time=g.ist_stamp(now))
        except g.GrowwError:
            pass
    return q


def load_rules(path):
    doc = g.read_json(path, "alerts", required=False) or {"rules": []}
    return doc


def add_rule(doc, rule):
    doc["rules"] = [r for r in doc["rules"] if r["id"] != rule["id"]] + [rule]
    return rule


def describe(r):
    day = (" on %s" % r["date"]) if r.get("date") else ""
    if r["type"] in PRICE_TYPES:
        return "%s: %s %s at or %s Rs %.2f%s" % (r["id"], r["symbol"], r["type"],
                                                "above" if PRICE_TYPES[r["type"]] > 0 else "below", r["price"], day)
    if r["type"] == "pct_move":
        return "%s: %s moves %s%% from the previous close" % (r["id"], r["symbol"], r["pct"])
    if r["type"] == "time":
        return "%s: the clock passes %s IST%s (%s)" % (r["id"], r["at"], day, r.get("label") or "time")
    return "%s: the NSE opens" % r["id"]


def cmd_add(a, now):
    doc = load_rules(a.rules)
    today = g.to_ist(now).date().isoformat()
    made = []
    if a.from_plan:
        plan = g.read_json(a.plan, "plan (run /india-exit plan --write first)")
        sym, day = norm(str(plan["symbol"])), plan.get("date") or today
        base = slug(sym)
        made.append(add_rule(doc, {"id": base + "-plan-stop", "type": "stop", "symbol": sym, "price": float(plan["stop"]),
                                   "date": day}))
        made.append(add_rule(doc, {"id": base + "-plan-target", "type": "target", "symbol": sym,
                                   "price": float(plan["target"]), "date": day}))
        made.append(add_rule(doc, {"id": base + "-flat-by", "type": "time", "at": plan.get("flat_by") or "15:10",
                                   "label": "flat by", "symbol": sym, "date": day}))
    if a.opening_bell:
        made.append(add_rule(doc, {"id": "opening-bell", "type": "opening_bell"}))
    if a.at:
        g.parse_hhmm(a.at, "--at")
        made.append(add_rule(doc, {"id": slug(a.label or "time-" + a.at), "type": "time", "at": a.at,
                                   "label": a.label or "time", "symbol": norm(a.symbol) if a.symbol else None}))
    for typ in ("stop", "target", "above", "below"):
        v = getattr(a, typ)
        if v is not None:
            if not a.symbol:
                raise g.GrowwError("--%s needs --symbol" % typ)
            sym = norm(a.symbol)
            made.append(add_rule(doc, {"id": "%s-%s" % (slug(sym), typ), "type": typ, "symbol": sym, "price": float(v),
                                       "date": a.date}))
    if a.pct is not None:
        if not a.symbol:
            raise g.GrowwError("--pct needs --symbol")
        sym = norm(a.symbol)
        made.append(add_rule(doc, {"id": "%s-pct-%s" % (slug(sym), slug(str(a.pct))), "type": "pct_move", "symbol": sym,
                                   "pct": float(a.pct)}))
    if not made:
        raise g.GrowwError("nothing to add: use --from-plan, --opening-bell, --at, or --symbol with a level")
    g.write_json(a.rules, doc)
    print("Added %d alert%s to %s:" % (len(made), "" if len(made) == 1 else "s", a.rules))
    for r in made:
        print("  " + describe(r))
    print("Run `alerts.py check` every few minutes while the trade is open. An alert is information, never an order.")
    return 0


def cmd_check(a, now):
    doc, state = load_rules(a.rules), g.read_json(a.state, "state", required=False) or {"rang": {}}
    local = g.to_ist(now)
    today = local.date().isoformat()
    is_open, words = clock(now)
    print(words)
    trading = g.trading_day(local.date())[0]
    rules = [r for r in doc["rules"] if not r.get("date") or r["date"] == today]
    given = {}
    for s in a.price or []:
        k, _, v = s.partition("=")
        given[norm(k)] = float(v)
    quotes, errors, rang = {}, [], []
    need = sorted({r["symbol"] for r in rules if r["type"] in PRICE_TYPES or r["type"] == "pct_move"})
    for i, sym in enumerate(need):
        if i:
            time.sleep(0.4)
        try:
            quotes[sym] = quote(sym, now, given.get(sym), lookup=not a.now)
        except g.GrowwError as e:
            errors.append(str(e))
    for r in rules:
        done = state["rang"].get(r["id"])
        daily = r["type"] in ("pct_move", "time", "opening_bell")
        if done and (not daily or done.get("date") == today):
            continue
        msg = None
        q = quotes.get(r.get("symbol"))
        if r["type"] in PRICE_TYPES and q:
            hit = q["price"] >= r["price"] if PRICE_TYPES[r["type"]] > 0 else q["price"] <= r["price"]
            if hit:
                msg = "%s %s: Rs %.2f is at or %s Rs %.2f (%s)." % (
                    r["symbol"], r["type"].upper(), q["price"], "above" if PRICE_TYPES[r["type"]] > 0 else "below",
                    r["price"], q["source"])
                if r["type"] in ("stop", "target") and r["id"].endswith("-plan-" + r["type"]):
                    msg += (" If the trade is on at Groww, its OCO %s has probably filled: check "
                            "groww.py status. Nothing was sold by this alert." % ("stop-loss" if r["type"] == "stop"
                                                                                 else "target"))
        elif r["type"] == "pct_move" and q and q.get("prev_close"):
            move = (q["price"] / float(q["prev_close"]) - 1) * 100
            if abs(move) >= r["pct"]:
                msg = "%s moved %+.2f%% from the previous close %.2f: Rs %.2f (%s)." % (
                    r["symbol"], move, float(q["prev_close"]), q["price"], q["source"])
        elif r["type"] == "time" and trading and local.time() >= g.parse_hhmm(r["at"], "at"):
            msg = "%s: it is %s IST (set for %s)." % ((r.get("label") or "time").capitalize(), local.strftime("%H:%M"),
                                                      r["at"])
            if (r.get("label") or "") == "flat by":
                msg += (" If %s is still open, close it: groww.py close --symbol %s writes the exit ticket, then "
                        "/groww-risk and your yes. Groww squares off open intraday positions from 3:20 PM (Rs 50 + "
                        "GST each)." % (r.get("symbol") or "the trade", r.get("symbol") or "SYMBOL"))
        elif r["type"] == "opening_bell" and is_open:
            msg = "Opening bell: the NSE is open."
        if msg:
            rang.append(msg)
            state["rang"][r["id"]] = {"date": today, "at": g.ist_stamp(now)}
    for m in rang:
        print("ALERT " + m)
    if not rang:
        seen = ", ".join("%s Rs %.2f" % (s, q["price"]) for s, q in sorted(quotes.items()))
        print("Nothing hit. %d rule(s) watched today%s." % (len(rules), (", " + seen) if seen else ""))
    for e in errors:
        print("note: " + e)
    if not a.dry_run:
        g.write_json(a.state, state)
    return 0 if rang else 1


def cmd_quote(a, now):
    syms = [norm(s) for s in a.symbols]
    if not syms:
        raise g.GrowwError("give one or more NSE symbols")
    out = {}
    for i, sym in enumerate(syms):
        if i:
            time.sleep(0.4)
        try:
            q = quote(sym, now)
            out[sym] = q
            chg = ""
            if q.get("prev_close"):
                chg = "  %+.2f%% vs previous close %.2f" % ((q["price"] / float(q["prev_close"]) - 1) * 100,
                                                            float(q["prev_close"]))
            print("%-12s Rs %10.2f%s  at %s IST, %s" % (sym, q["price"], chg, (q.get("time") or "?")[11:16], q["source"]))
        except g.GrowwError as e:
            print("%-12s %s" % (sym, e))
    if a.write and out:
        doc = g.read_json(a.out, "prices", required=False) or {"prices": {}, "quotes": {}}
        doc["as_of"] = g.ist_stamp(now) + " IST"
        doc.setdefault("prices", {}).update({s: q["price"] for s, q in out.items()})
        doc.setdefault("quotes", {}).update(out)
        g.write_json(a.out, doc)
        print("Wrote %s." % a.out)
    print("Free quotes are delayed and unofficial; the broker's price is the one that fills.")
    return 0 if out else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price Alerts (india-alerts) for NSE stocks.")
    ap.add_argument("--rules", default=RULES)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-11-09 15:10"')
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("clock")
    p = sub.add_parser("add")
    p.add_argument("--from-plan", action="store_true")
    p.add_argument("--plan", default=PLAN)
    p.add_argument("--opening-bell", action="store_true")
    p.add_argument("--symbol")
    for t in ("stop", "target", "above", "below", "pct"):
        p.add_argument("--" + t, type=float)
    p.add_argument("--at", help="HH:MM India time")
    p.add_argument("--label")
    p.add_argument("--date", help="watch a price rule on this date only (YYYY-MM-DD)")
    p = sub.add_parser("check")
    p.add_argument("--price", action="append", metavar="SYMBOL=PRICE")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("quote")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--write", action="store_true")
    p.add_argument("--out", default=PRICES)
    sub.add_parser("list")
    for name in ("reset", "remove"):
        p = sub.add_parser(name)
        p.add_argument("id")
    a = ap.parse_args(argv)
    try:
        now = g.parse_now(a.now)
        if a.cmd == "clock":
            print(clock(now)[1])
            return 0
        if a.cmd == "add":
            return cmd_add(a, now)
        if a.cmd == "check":
            return cmd_check(a, now)
        if a.cmd == "quote":
            return cmd_quote(a, now)
        doc = load_rules(a.rules)
        state = g.read_json(a.state, "state", required=False) or {"rang": {}}
        if a.cmd == "list":
            if not doc["rules"]:
                print("No alerts. Add some: alerts.py add --from-plan")
            for r in doc["rules"]:
                rang = state["rang"].get(r["id"])
                print("  %s%s" % (describe(r), ("   [rang %s]" % rang["at"]) if rang else ""))
            return 0
        if a.cmd == "reset":
            state["rang"].pop(a.id, None)
            g.write_json(a.state, state)
            print("Reset %s: it can ring again." % a.id)
            return 0
        if a.cmd == "remove":
            n = len(doc["rules"])
            doc["rules"] = [r for r in doc["rules"] if r["id"] != a.id]
            g.write_json(a.rules, doc)
            print("Removed %s." % a.id if len(doc["rules"]) < n else "No alert named %s." % a.id)
            return 0
        ap.print_help()
        return 1
    except g.GrowwError as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
