#!/usr/bin/env python3
"""Budget Manager for groww-budget: sizes an NSE day trade to your rupee budget
and your max loss per trade, and writes ONE Groww order proposal: the entry
plus a stop-loss and target that cancel each other (a Groww OCO smart order).
Standard library only. Nothing is placed.

    python3 size.py                    # trader-in/plan.json + trader-in/config.json
    python3 size.py --write            # write trader-in/orders.json
    python3 size.py --market --write   # a market entry instead of a limit at the plan's entry
    python3 size.py --price 1044.5     # size from a fresh price you give
    python3 size.py --json

The size is the smallest of four limits: max loss (max_loss_per_trade_inr /
risk a share), position cap (capital x max_position_pct), max order
(max_order_inr), and cash (capital minus the cash buffer; with
"use_leverage": true, Groww's live intraday margin instead). Whole shares.

Prices go on the stock's real tick from Groww's instrument list: the stop
rounds down, the target and a limit entry round up. Intraday (MIS) only, and
only in stocks Groww allows for intraday.

It refuses, and writes nothing, when: zero shares fit, the order is under
min_order_inr, reward:risk is under min_reward_risk, the stop is not below
the entry or the target not above it, the plan is for another day, the symbol
is blocked, Groww does not allow intraday or buying in it, or the plan is a
short.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "groww-broker"))
import growwlib as g  # noqa: E402

def size(a):
    now = g.parse_now(a.now)
    today = g.to_ist(now).date().isoformat()
    cfg = g.load_config(a.config)
    plan = g.read_json(a.plan, "plan (run /india-exit plan --write first)")
    for k in ("symbol", "date", "entry", "stop", "target"):
        if plan.get(k) is None:
            raise g.GrowwError("%s is missing %r: make a new plan with /india-exit" % (a.plan, k))
    if str(plan.get("side") or "buy").lower() not in ("buy", "long"):
        raise g.GrowwError("the plan is a short: the Groww skills plan intraday longs only")
    if plan["date"] != today:
        raise g.GrowwError("the plan is for %s, not today (%s): make a new one with /india-exit" % (plan["date"], today))
    raw = str(plan["symbol"]).upper()
    exchange = "BSE" if raw.endswith(".BO") else cfg["exchange"]
    symbol = raw[:-3] if raw.endswith((".NS", ".BO")) else raw
    if symbol.startswith("^"):
        raise g.GrowwError("%s is an index: it cannot be bought as a stock" % symbol)
    if symbol in cfg["blocked"]:
        raise g.GrowwError("%s is on your blocked list" % symbol)

    insts = g.load_instruments(a.instruments, refresh=a.refresh, offline=a.instruments_csv, now_utc=now)
    inst = g.instrument(insts, exchange, symbol)
    if not inst:
        raise g.GrowwError("%s is not in Groww's %s stock list" % (symbol, exchange))
    if inst.get("type") != "EQ":
        raise g.GrowwError("%s on %s is a %s, not a stock" % (symbol, exchange, inst.get("type")))
    if not inst.get("buy"):
        raise g.GrowwError("Groww does not allow buying %s right now" % symbol)
    if not inst.get("intraday"):
        raise g.GrowwError("Groww does not allow intraday (MIS) in %s: the Groww skills trade intraday only" % symbol)
    tick = g.D(inst["tick"]) if inst.get("tick") else None
    if not tick:
        raise g.GrowwError("Groww's list has no tick size for %s" % symbol)

    notes = []
    plan_entry, plan_stop, plan_target = g.D(plan["entry"]), g.D(plan["stop"]), g.D(plan["target"])
    stop = g.floor_tick(plan_stop, tick)
    target = g.ceil_tick(plan_target, tick)
    if stop != g.q2(plan_stop):
        notes.append("stop %s moved down to %s, on %s's Rs %s tick" % (g.pstr(plan_stop), g.pstr(stop), symbol, tick))
    if target != g.q2(plan_target):
        notes.append("target %s moved up to %s, on the Rs %s tick" % (g.pstr(plan_target), g.pstr(target), tick))

    price, price_src = g.fresh_price(symbol, exchange, now, a.price, not a.no_quote)
    market = a.market or str(cfg.get("entry_order", "LIMIT")).upper() == "MARKET"
    if a.limit is not None:
        market = False
    if market:
        limit = None
        entry = plan_entry
        if price is not None and price > entry:
            entry = price
            notes.append("sized at %s, %s, above the plan's entry %s" % (g.pstr(price), price_src, g.pstr(plan_entry)))
        entry_words = "market order (risk measured at %s)" % g.pstr(entry)
    else:
        limit = g.ceil_tick(g.D(a.limit) if a.limit is not None else plan_entry, tick)
        entry = limit
        entry_words = "limit order at %s" % g.pstr(limit)
        if price is not None and price > limit:
            notes.append("the price is %s now (%s), above your limit %s: it fills only if it comes back"
                         % (g.pstr(price), price_src, g.pstr(limit)))
    if stop >= entry:
        raise g.GrowwError("the stop %s is not below the entry %s" % (g.pstr(stop), g.pstr(entry)))
    if target <= entry:
        raise g.GrowwError("the target %s is not above the entry %s: the move already happened" % (g.pstr(target),
                                                                                                 g.pstr(entry)))
    risk = entry - stop
    rr = ((target - entry) / risk).quantize(g.PAISA)
    if rr < g.D(cfg["min_reward_risk"]):
        raise g.GrowwError("reward:risk is %s at an entry of %s, under your min_reward_risk %s: skip it, or make a "
                           "new plan" % (rr, g.pstr(entry), cfg["min_reward_risk"]))

    capital = g.D(cfg["capital_inr"])
    limits = []
    max_loss = g.D(cfg["max_loss_per_trade_inr"])
    limits.append(("max loss", "Rs %s / %s" % (g.pstr(max_loss), g.pstr(risk)), int(max_loss // risk)))
    pos_cap = capital * g.D(cfg["max_position_pct"]) / 100
    limits.append(("position cap", "%s%% of %s = %s / %s" % (cfg["max_position_pct"], g.money(capital), g.money(pos_cap),
                                                             g.pstr(entry)), int(pos_cap // entry)))
    max_order = g.D(cfg["max_order_inr"])
    limits.append(("max order", "%s / %s" % (g.money(max_order), g.pstr(entry)), int(max_order // entry)))
    if cfg.get("use_leverage"):
        if not g.credential_flow():
            raise g.GrowwError("use_leverage needs a Groww login to read your live intraday margin")
        gw = g.Groww()
        bal = g.D(((gw.margin() or {}).get("equity_margin_details") or {}).get("mis_balance_available") or 0)
        need = gw.required_margin([{"trading_symbol": symbol, "transaction_type": "BUY", "quantity": 1,
                                    "price": float(entry), "order_type": "LIMIT", "product": "MIS",
                                    "exchange": exchange, "segment": "CASH"}])
        per = g.D((need or {}).get("cash_mis_margin_required") or (need or {}).get("total_requirement") or 0)
        if per <= 0:
            raise g.GrowwError("Groww did not say the intraday margin for %s" % symbol)
        limits.append(("margin", "Groww intraday margin %s / %s a share" % (g.money(bal), g.money(per)), int(bal // per)))
    else:
        cash = capital * (100 - g.D(cfg["cash_buffer_pct"])) / 100
        limits.append(("cash", "%s minus %s%% buffer = %s / %s" % (g.money(capital), cfg["cash_buffer_pct"], g.money(cash),
                                                                  g.pstr(entry)), int(cash // entry)))
    qty = min(n for _, _, n in limits)
    decider = [name for name, _, n in limits if n == qty][0]
    if qty < 1:
        raise g.GrowwError("zero shares fit: one share risks %s, and the %s limit allows none" % (g.pstr(risk), decider))
    notional = g.q2(qty * entry)
    if notional < g.D(cfg["min_order_inr"]):
        raise g.GrowwError("%d shares = %s, under your min_order_inr %s" % (qty, g.money(notional), cfg["min_order_inr"]))
    loss = g.q2(qty * risk)
    fees = g.brokerage(notional) + g.brokerage(qty * stop)
    fees_t = g.brokerage(notional) + g.brokerage(qty * target)

    order = {"trading_symbol": symbol, "quantity": qty, "price": 0 if market else float(limit), "validity": "DAY",
             "exchange": exchange, "segment": "CASH", "product": "MIS", "order_type": "MARKET" if market else "LIMIT",
             "transaction_type": "BUY"}
    protect = {"smart_order_type": "OCO", "segment": "CASH", "trading_symbol": symbol, "quantity": qty,
               "net_position_quantity": qty, "transaction_type": "SELL",
               "target": {"trigger_price": g.pstr(target), "order_type": "LIMIT", "price": g.pstr(target)},
               "stop_loss": {"trigger_price": g.pstr(stop), "order_type": "SL_M", "price": None},
               "product_type": "MIS", "exchange": exchange, "duration": "DAY"}
    item = {"kind": "entry", "symbol": symbol, "exchange": exchange, "order": order, "protect": protect}
    fp = g.fingerprint(item)
    order["order_reference_id"] = g.ref_id("tse", fp)
    protect["reference_id"] = g.ref_id("tsp", fp)
    why = "%s on %s: %d shares, bound by your %s limit (%d x %s = %s at the stop)" % (
        {"orb": "opening-range breakout", "level": "close above a level"}.get(plan.get("setup"), plan.get("setup") or "plan"),
        symbol, qty, decider, qty, g.pstr(risk), g.money(loss))
    item.update({
        "fingerprint": fp, "why": why,
        "plan": {"entry": float(plan_entry), "entry_for_risk": float(entry), "stop": float(stop), "target": float(target),
                 "risk_per_share": float(risk), "max_loss": float(loss), "reward_risk": float(rr), "tick": float(tick),
                 "notional": float(notional), "brokerage_est_at_stop": float(fees),
                 "brokerage_est_at_target": float(fees_t), "setup": plan.get("setup"),
                 "entry_time": plan.get("entry_time"), "flat_by": cfg["flat_by"], "fno_stock": bool(inst.get("fno")),
                 "price_used": float(price) if price is not None else None, "price_source": price_src,
                 "plan_date": plan["date"]}})
    doc = {"created_by": "groww-budget", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": today,
           "broker": "groww", "live": bool(cfg.get("live")),
           "note": "Proposals only. groww-risk checks them; groww-broker asks you yes or no before anything is sent.",
           "orders": [item]}
    return doc, item, limits, decider, notes, entry_words, cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="Budget Manager (groww-budget): size an NSE day trade for Groww.")
    ap.add_argument("--plan", default=g.PATHS["plan"])
    ap.add_argument("--config", default=g.PATHS["config"])
    ap.add_argument("--out", default=g.PATHS["orders"])
    ap.add_argument("--write", action="store_true", help="write trader-in/orders.json")
    ap.add_argument("--market", action="store_true", help="a market entry (default: limit at the plan's entry)")
    ap.add_argument("--limit", type=float, help="a limit entry at this price")
    ap.add_argument("--price", type=float, help="a fresh price to size from (default: Groww LTP, else Yahoo)")
    ap.add_argument("--no-quote", action="store_true", help="do not look up a fresh price")
    ap.add_argument("--instruments", default=g.PATHS["instruments"], help="cached Groww instrument list")
    ap.add_argument("--instruments-csv", help="read Groww's instrument.csv from this file instead")
    ap.add_argument("--refresh", action="store_true", help="download Groww's instrument list again")
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-11-09 10:00"')
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc, item, limits, decider, notes, entry_words, cfg = size(a)
    except g.GrowwError as e:
        print("size.py: no order: %s" % e, file=sys.stderr)
        return 1
    if a.write:
        g.write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(item, limits=[{"limit": n, "math": m, "shares": s} for n, m, s in limits],
                              notes=notes), indent=2, ensure_ascii=False))
        return 0
    p = item["plan"]
    o = item["order"]
    print("Groww order proposal: %s on %s, intraday (MIS), %s" % (item["symbol"], item["exchange"], entry_words))
    print("  Entry %s, stop %s, target %s (Groww tick Rs %s): risk %s a share, reward:risk %s"
          % (g.pstr(p["entry_for_risk"]), g.pstr(p["stop"]), g.pstr(p["target"]), g.D(str(p["tick"])),
             g.pstr(p["risk_per_share"]), p["reward_risk"]))
    print("  %-13s %-58s %s" % ("limit", "the math", "shares"))
    for name, math, n in limits:
        print("  %-13s %-58s %d%s" % (name, math, n, "  <- decides it" if name == decider else ""))
    print("  BUY %d %s = %s. Max loss at the stop: %s." % (o["quantity"], item["symbol"], g.money(p["notional"]),
                                                          g.money(p["max_loss"])))
    print("  Brokerage about %s both ways if the stop fills (%s at the target); STT, exchange charges, stamp duty and "
          "GST are on top." % (g.money(p["brokerage_est_at_stop"]), g.money(p["brokerage_est_at_target"])))
    print("  Protection: an OCO smart order (stop-loss SL-M at %s, target limit at %s), placed right after the entry "
          "fills." % (g.pstr(p["stop"]), g.pstr(p["target"])))
    for n in notes:
        print("Note: " + n + ".")
    print("A stop is an order, not a promise: in a fast market or a circuit it can fill below the stop.")
    if a.write:
        print("Wrote %s (1 proposal, %s mode). Next: /groww-risk, then /groww-broker. Nothing was placed."
              % (a.out, "LIVE" if cfg.get("live") else "DRY-RUN"))
    else:
        print("Nothing written (add --write). Nothing was placed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
