#!/usr/bin/env python3
"""Risk Manager for groww-risk: checks every Groww order proposal against your
limits and Groww's own rules, and stops anything over them before it can reach
groww-broker. Standard library only. Prints PASS or BLOCK per order and
records each verdict with a fingerprint of the exact order.

    python3 check.py                           # trader-in/orders.json; writes trader-in/state/risk.json
    python3 check.py --json
    python3 check.py --no-write                # look without recording
    python3 check.py --now "2026-11-09 10:00"  # pin the clock (India time)

An entry opens a position (a buy). An exit only sells what you hold (from
groww-broker's close). Getting out must always be possible, so an exit is
checked for format and for selling more than you hold; every other rule is a
warning for an exit, never a BLOCK.

Checks on an entry: format (Groww's fields, MIS, CASH, DAY, prices on the
tick, reference ids), stock (in Groww's list, a stock, buying and intraday
allowed), blocked list, stop (an OCO stop-loss below the entry, required
while require_stop is true), target above the entry, loss at the stop over
max_loss_per_trade_inr, reward:risk under min_reward_risk, order size
(min_order_inr, max_order_inr), position cap (max_position_pct), cash
(capital minus the buffer, counting today's open entries), daily loss (the max
losses of today's entries plus this one over max_daily_loss_inr), trades per
day, orders per day, the window (an NSE trading day, no_entries_before /
no_entries_after, and Groww's own cut-off: 3:05 PM in F&O stocks, 3:20 PM in
the rest), and duplicates (the same stock twice in a batch, or already
entered today).

The entry it measures risk from: the limit price for a limit order; for a
market order the HIGHER of the plan's entry and a fresh price (your --price,
Groww's live price with a login, else Yahoo's if under 15 minutes old).
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "groww-broker"))
import growwlib as g  # noqa: E402

ORDER_TYPES = ("LIMIT", "MARKET")
OPEN_STATUSES = ("placed", "filled", "protected", "dry-run filled", "open", "unprotected")


def held_today(state, today, mode, symbol):
    """Shares of `symbol` bought minus sold today in this mode, from the broker log."""
    n = 0
    for r in g.records_today(state, today, mode):
        if r.get("symbol") != symbol or not r.get("filled_qty") or r.get("status") not in ("filled", "dry-run filled"):
            continue
        if r.get("kind") == "entry":
            n += int(r["filled_qty"])
        elif r.get("kind") == "exit":
            n -= int(r["filled_qty"])
    return n


def check_format(item, inst, reasons, warnings):
    o = item.get("order") or {}
    for k in ("trading_symbol", "quantity", "validity", "exchange", "segment", "product", "order_type",
              "transaction_type", "order_reference_id"):
        if o.get(k) in (None, ""):
            reasons.append("format: the order has no %s" % k)
    if reasons:
        return
    if o["segment"] != "CASH":
        reasons.append("format: segment %s (the Groww skills trade stocks: CASH)" % o["segment"])
    if o["product"] != "MIS":
        reasons.append("format: product %s (the Groww skills trade intraday: MIS)" % o["product"])
    if o["validity"] != "DAY":
        reasons.append("format: validity %s (DAY only)" % o["validity"])
    if o["exchange"] not in ("NSE", "BSE"):
        reasons.append("format: exchange %s" % o["exchange"])
    if o["order_type"] not in ORDER_TYPES:
        reasons.append("format: order type %s (LIMIT or MARKET)" % o["order_type"])
    if not isinstance(o["quantity"], int) or isinstance(o["quantity"], bool) or o["quantity"] < 1:
        reasons.append("format: quantity %r is not a whole number of shares" % o["quantity"])
    if not g.REF_RE.match(str(o["order_reference_id"])):
        reasons.append("format: order_reference_id %r is not 8-20 letters and digits" % o["order_reference_id"])
    tick = g.D(inst["tick"]) if inst and inst.get("tick") else None
    if o["order_type"] == "LIMIT":
        if not o.get("price") or g.D(o["price"]) <= 0:
            reasons.append("format: a limit order with no price")
        elif tick and not g.on_tick(o["price"], tick):
            reasons.append("format: limit %s is not on %s's Rs %s tick" % (g.pstr(o["price"]), o["trading_symbol"], tick))
    p = item.get("protect")
    if p:
        sl, tg = p.get("stop_loss") or {}, p.get("target") or {}
        if p.get("smart_order_type") != "OCO":
            reasons.append("format: the protection is not an OCO smart order")
        if p.get("quantity") != o["quantity"] or p.get("net_position_quantity") != o["quantity"]:
            reasons.append("format: the OCO quantity does not match the entry's %s shares" % o["quantity"])
        if p.get("transaction_type") != "SELL" or p.get("product_type") != "MIS" or p.get("segment") != "CASH":
            reasons.append("format: the OCO must SELL, MIS, CASH to protect a long")
        if p.get("trading_symbol") != o["trading_symbol"] or p.get("exchange") != o["exchange"]:
            reasons.append("format: the OCO is for a different stock or exchange")
        if sl.get("order_type") not in ("SL_M", "SL") or not sl.get("trigger_price"):
            reasons.append("format: the OCO has no stop-loss trigger")
        if tg.get("order_type") not in ("LIMIT", "MARKET") or not tg.get("trigger_price"):
            reasons.append("format: the OCO has no target trigger")
        if tg.get("order_type") == "LIMIT" and not tg.get("price"):
            reasons.append("format: the OCO target is a limit with no price")
        if not g.REF_RE.match(str(p.get("reference_id") or "")):
            reasons.append("format: the OCO reference_id is not 8-20 letters and digits")
        for name, v in (("stop-loss", sl.get("trigger_price")), ("target", tg.get("trigger_price"))):
            if v and tick and not g.on_tick(v, tick):
                reasons.append("format: the %s %s is not on the Rs %s tick" % (name, v, tick))
        for v in (sl.get("trigger_price"), tg.get("trigger_price"), tg.get("price")):
            if v is not None and not isinstance(v, str):
                warnings.append("Groww wants smart-order prices as decimal strings (\"1029.20\"); one is a number")
                break


def check(items, cfg, insts, state, now, prices, mode):
    local = g.to_ist(now)
    today = local.date().isoformat()
    records = g.records_today(state, today, mode)
    last = {}                                  # one record per order: its latest
    for r in records:
        if r.get("status") in OPEN_STATUSES + ("closed", "cancelled"):
            last[r.get("fingerprint")] = r
    entries_today = [r for r in last.values() if r.get("kind") == "entry" and r.get("status") != "cancelled"]
    placed_today = list(last.values())
    loss_today = sum((g.D(r.get("max_loss") or 0) for r in entries_today), g.D(0))
    open_notional = sum((g.D(r.get("notional") or 0) for r in entries_today if r.get("status") != "closed"), g.D(0))
    capital = g.D(cfg["capital_inr"])
    cash_room = capital * (100 - g.D(cfg["cash_buffer_pct"])) / 100 - open_notional
    batch_syms, results = {}, []
    n_entries, n_orders = len(entries_today), len(placed_today)
    for idx, item in enumerate(items, 1):
        reasons, warnings, info = [], [], {}
        kind = item.get("kind") or "entry"
        o = item.get("order") or {}
        sym = str(o.get("trading_symbol") or item.get("symbol") or "").upper()
        exch = str(o.get("exchange") or item.get("exchange") or "NSE").upper()
        inst = g.instrument(insts, exch, sym) if sym else None
        fp = g.fingerprint(item)
        check_format(item, inst, reasons, warnings)
        if not inst:
            reasons.append("stock: %s is not in Groww's %s list" % (sym or "?", exch))
        side = o.get("transaction_type")
        if kind == "exit":
            if side != "SELL":
                reasons.append("format: an exit must SELL")
            held = held_today(state, today, mode, sym)
            if item.get("held") is not None:
                held = max(held, int(item["held"]))
            if isinstance(o.get("quantity"), int) and o["quantity"] > held:
                reasons.append("short: selling %s but you hold %d %s from today's log (intraday longs only)"
                               % (o["quantity"], held, sym))
            ok, why = g.trading_day(local.date())
            if not ok or not (g.OPEN <= local.time() < g.CLOSE):
                warnings.append("the market is closed now (%s): Groww cannot fill this until it opens"
                                % (why or "outside 9:15-15:30 IST"))
            if local.time() >= g.GROWW_SQUAREOFF:
                warnings.append("Groww squares off open intraday positions from 3:20 PM (Rs 50 + GST each)")
            results.append({"index": idx, "symbol": sym, "kind": kind, "fingerprint": fp,
                            "verdict": "BLOCK" if reasons else "PASS", "reasons": reasons, "warnings": warnings})
            n_orders += 1
            continue
        if side != "BUY":
            reasons.append("format: an entry must BUY (the Groww skills plan longs only)")
        if inst:
            if inst.get("type") != "EQ":
                reasons.append("stock: %s is a %s, not a stock" % (sym, inst.get("type")))
            if not inst.get("buy"):
                reasons.append("stock: Groww does not allow buying %s" % sym)
            if not inst.get("intraday"):
                reasons.append("stock: Groww does not allow intraday (MIS) in %s" % sym)
        if sym in cfg["blocked"]:
            reasons.append("blocked: %s is on your blocked list" % sym)
        plan = item.get("plan") or {}
        p = item.get("protect") or {}
        stop = (p.get("stop_loss") or {}).get("trigger_price")
        target = (p.get("target") or {}).get("trigger_price")
        if stop is None and cfg.get("require_stop"):
            reasons.append("stop: no stop-loss at the broker (require_stop is true)")
            stop = plan.get("stop")
        if target is None:
            target = plan.get("target")
        qty = o.get("quantity") if isinstance(o.get("quantity"), int) else 0
        if o.get("order_type") == "LIMIT" and o.get("price"):
            entry, entry_src = g.D(o["price"]), "the limit price"
        else:
            entry, entry_src = g.D(plan.get("entry_for_risk") or plan.get("entry") or 0), "the plan's entry"
            fresh, src = prices.get(sym, (None, "no fresh price"))
            if fresh is not None and fresh > entry:
                entry, entry_src = fresh, src
            elif fresh is None:
                warnings.append("%s: a market order's risk is measured at the plan's entry" % src)
        info.update(entry=float(entry), entry_source=entry_src)
        if stop is not None and entry:
            stop_d = g.D(stop)
            if stop_d >= entry:
                reasons.append("stop: the stop %s is not below the entry %s" % (g.pstr(stop_d), g.pstr(entry)))
            else:
                risk = entry - stop_d
                loss = g.q2(qty * risk)
                info.update(stop=float(stop_d), risk_per_share=float(risk), max_loss=float(loss))
                if loss > g.D(cfg["max_loss_per_trade_inr"]):
                    reasons.append("loss: %d x %s = %s at the stop, over your %s max loss per trade"
                                   % (qty, g.pstr(risk), g.money(loss), g.money(cfg["max_loss_per_trade_inr"])))
                if target is not None:
                    t = g.D(target)
                    if t <= entry:
                        reasons.append("target: %s is not above the entry %s" % (g.pstr(t), g.pstr(entry)))
                    else:
                        rr = ((t - entry) / risk).quantize(g.PAISA)
                        info.update(target=float(t), reward_risk=float(rr))
                        if rr < g.D(cfg["min_reward_risk"]):
                            reasons.append("reward:risk: %s, under your min %s" % (rr, cfg["min_reward_risk"]))
                if loss_today + loss > g.D(cfg["max_daily_loss_inr"]):
                    reasons.append("daily loss: today's entries risk %s already; with this %s it is %s, over your %s"
                                   % (g.money(loss_today), g.money(loss), g.money(loss_today + loss),
                                      g.money(cfg["max_daily_loss_inr"])))
                info.update(loss_today_before=float(loss_today))
        notional = g.q2(qty * entry) if entry else g.D(0)
        info["notional"] = float(notional)
        if notional < g.D(cfg["min_order_inr"]):
            reasons.append("size: %s is under your min_order_inr %s" % (g.money(notional), cfg["min_order_inr"]))
        if notional > g.D(cfg["max_order_inr"]):
            reasons.append("size: %s is over your max_order_inr %s" % (g.money(notional), g.money(cfg["max_order_inr"])))
        if notional > capital * g.D(cfg["max_position_pct"]) / 100:
            reasons.append("position: %s is over %s%% of your %s capital" % (g.money(notional), cfg["max_position_pct"],
                                                                              g.money(capital)))
        if not cfg.get("use_leverage") and notional > cash_room:
            reasons.append("cash: %s leaves less than your %s%% buffer (room %s after today's open entries)"
                           % (g.money(notional), cfg["cash_buffer_pct"], g.money(max(cash_room, g.D(0)))))
        if n_entries >= int(cfg["max_trades_per_day"]):
            reasons.append("trades: %d of %d entries already today" % (n_entries, int(cfg["max_trades_per_day"])))
        if n_orders >= int(cfg["max_orders_per_day"]):
            reasons.append("orders: %d of %d orders already today" % (n_orders, int(cfg["max_orders_per_day"])))
        ok, why = g.trading_day(local.date())
        if not ok:
            reasons.append("window: the market is closed today (%s)" % why)
        else:
            t = local.time()
            if not (g.OPEN <= t < g.CLOSE):
                reasons.append("window: the market is closed now (it is open 9:15 to 15:30 IST)")
            if cfg.get("no_entries_before") and t < g.parse_hhmm(cfg["no_entries_before"], "no_entries_before"):
                reasons.append("window: no entries before %s (config)" % cfg["no_entries_before"])
            if cfg.get("no_entries_after") and t > g.parse_hhmm(cfg["no_entries_after"], "no_entries_after"):
                reasons.append("window: no entries after %s (config)" % cfg["no_entries_after"])
            cut = g.GROWW_FNO_CUTOFF if inst and inst.get("fno") else g.GROWW_CUTOFF
            if t > cut:
                reasons.append("window: Groww takes no new intraday orders in %s after %s" % (sym, g.ampm(cut)))
        if local.date().year not in g.CALENDAR_YEARS:
            warnings.append("the NSE holiday list covers %s only" % ", ".join(map(str, g.CALENDAR_YEARS)))
        prior = [r for r in entries_today if r.get("symbol") == sym]
        if prior:
            reasons.append("duplicate: %s was already entered today" % sym)
        if sym in batch_syms:
            reasons.append("duplicate: %s is also order %d in this batch" % (sym, batch_syms[sym]))
        if not p:
            warnings.append("no OCO protection: the stop lives only in the plan")
        if mode == "live":
            warnings.append("LIVE mode: an approved order goes to your real Groww account")
        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            batch_syms[sym] = idx
            n_entries += 1
            n_orders += 1
            loss_today += g.D(info.get("max_loss") or 0)
            cash_room -= notional
        results.append(dict({"index": idx, "symbol": sym, "kind": kind, "fingerprint": fp, "verdict": verdict,
                             "reasons": reasons, "warnings": warnings, "quantity": qty}, **info))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Risk Manager (groww-risk): PASS or BLOCK for Groww order proposals.")
    ap.add_argument("--orders", default=g.PATHS["orders"])
    ap.add_argument("--config", default=g.PATHS["config"])
    ap.add_argument("--state", default=g.PATHS["broker"])
    ap.add_argument("--out", default=g.PATHS["risk"])
    ap.add_argument("--instruments", default=g.PATHS["instruments"])
    ap.add_argument("--instruments-csv", help="read Groww's instrument.csv from this file instead")
    ap.add_argument("--price", action="append", default=[], metavar="SYMBOL=PRICE",
                    help="a fresh price for a market order's risk (repeatable)")
    ap.add_argument("--no-quote", action="store_true", help="do not look up fresh prices")
    ap.add_argument("--now", help='pretend it is this India time, e.g. "2026-11-09 10:00"')
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = g.parse_now(a.now)
        cfg = g.load_config(a.config)
        doc = g.read_json(a.orders, "orders (run /groww-budget size.py --write, or groww-broker close)")
        items = doc.get("orders") or []
        if not items:
            raise g.GrowwError("no orders in %s" % a.orders)
        insts = g.load_instruments(a.instruments, offline=a.instruments_csv, now_utc=now)
        state = g.load_state(a.state)
        mode = "live" if cfg.get("live") else "dry-run"
        given = {}
        for s in a.price:
            k, _, v = s.partition("=")
            given[k.strip().upper()] = g.D(v, "--price")
        prices = {}
        for it in items:
            o = it.get("order") or {}
            sym = str(o.get("trading_symbol") or "").upper()
            if it.get("kind", "entry") == "entry" and o.get("order_type") == "MARKET":
                prices[sym] = g.fresh_price(sym, o.get("exchange", "NSE"), now, given.get(sym),
                                            lookup=not a.no_quote and not a.now)
        results = check(items, cfg, insts, state, now, prices, mode)
    except g.GrowwError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    local = g.to_ist(now)
    out = {"created_by": "groww-risk", "date": local.date().isoformat(), "checked_at": g.ist_stamp(now) + " IST",
           "mode": mode, "results": results}
    if not a.no_write:
        g.write_json(a.out, out)
    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        if not cfg.get("_found"):
            print("Note: no %s yet, so the example limits are in use (capital Rs 1,00,000, Rs 500 max loss a trade). "
                  "Copy groww-budget/config.example.json there and set your own." % a.config)
        for r, it in zip(results, items):
            o = it.get("order") or {}
            print("%-5s #%d %s %s %s shares, %s" % (r["verdict"], r["index"], o.get("transaction_type", "?").lower(),
                                                    r["symbol"], o.get("quantity"), r["kind"]))
            if r["kind"] == "entry" and r.get("max_loss") is not None:
                print("          entry %s (%s), stop %s, target %s: risk %s a share"
                      % (g.pstr(r["entry"]), r["entry_source"], g.pstr(r["stop"]),
                         g.pstr(r["target"]) if r.get("target") else "-", g.pstr(r["risk_per_share"])))
                print("          max loss %s (limit %s), reward:risk %s (min %s)"
                      % (g.money(r["max_loss"]), g.money(cfg["max_loss_per_trade_inr"]), r.get("reward_risk", "-"),
                         cfg["min_reward_risk"]))
                print("          today before this: %s of %s daily loss; order value %s"
                      % (g.money(r.get("loss_today_before", 0)), g.money(cfg["max_daily_loss_inr"]),
                         g.money(r["notional"])))
            for x in r["reasons"]:
                print("          BLOCK: " + x)
            for w in r["warnings"]:
                print("          note: " + w)
        npass = sum(1 for r in results if r["verdict"] == "PASS")
        print("\n%d passed, %d blocked at %s IST (%s mode).%s" % (
            npass, len(results) - npass, local.strftime("%Y-%m-%d %H:%M"), mode.upper(),
            "" if a.no_write else " Recorded in %s." % a.out))
        print("A PASS is a rules check, not a recommendation. Next: /groww-broker shows the ticket and asks you yes or no.")
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
