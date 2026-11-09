#!/usr/bin/env python3
"""Order Executor for groww-broker: sends an approved NSE day trade to Groww
through the Groww Trade API, never without your yes. Standard library only.

    python3 groww.py setup                          # what is set up (login, mode); prints no secrets
    python3 groww.py check                          # read only: your Groww profile and margin
    python3 groww.py show                           # every ticket in trader-in/orders.json, READY or not
    python3 groww.py approve --index 1 --reply "yes"
    python3 groww.py place --index 1                # entry, then the stop/target OCO once it fills
    python3 groww.py protect --index 1              # the OCO for an entry that filled later
    python3 groww.py cancel --index 1 --reply "yes cancel"   # cancel an entry that has not filled
    python3 groww.py close --symbol SBIN            # writes the exit ticket (sell what you hold)
    python3 groww.py status                         # positions, today's orders, active OCOs
    python3 groww.py log

Three gates, every order, every time:
  1. groww-risk passed this exact order today (matched by fingerprint);
  2. you typed an explicit yes for this one order, recorded word for word by `approve`;
  3. Claude Code asks you before running place, protect or cancel (the ask rules
     in .claude/settings.json), so you click Yes for that command.

DRY-RUN is the default (Groww has no paper account): place, protect, cancel
and close print the exact request they would send and record a simulated fill
at the ticket's price, and nothing reaches Groww. Only you switch to LIVE, by
setting "live": true in trader-in/config.json yourself.

The protection: Groww has no bracket order for stocks, so after the entry
fills the tool places an OCO smart order (stop-loss SL-M and target limit,
one cancels the other) for the filled shares. The yes for a ticket covers its
entry and its OCO. An exit first cancels the stock's active OCO, then sells
at market, so a later trigger cannot sell shares you no longer hold.

Login: environment variables only (see growwlib.py). Never in the chat.
"""

import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import growwlib as g  # noqa: E402

FILLED = ("EXECUTED", "COMPLETED")
DEAD = ("REJECTED", "FAILED", "CANCELLED")
SAFETY = "Education and tooling, not investment advice. A PASS is a rules check, not a recommendation."


# ---------------------------------------------------------------- loading

def load(a):
    now = g.parse_now(getattr(a, "now", None))
    cfg = g.load_config(a.config)
    doc = g.read_json(a.orders, "orders (run /groww-budget size.py --write)", required=False) or {}
    items = doc.get("orders") or []
    state = g.load_state(a.state)
    risk = g.read_json(a.risk, "risk check", required=False)
    return now, cfg, items, state, risk


def mode_of(cfg):
    return "live" if cfg.get("live") else "dry-run"


def pick(items, index):
    if not items:
        raise g.GrowwError("no tickets in trader-in/orders.json: run /groww-budget (size.py --write) first")
    if index < 1 or index > len(items):
        raise g.GrowwError("there is no ticket %d (there %s %d)" % (index, "is" if len(items) == 1 else "are", len(items)))
    return items[index - 1]


def record(state, a, now, mode, item, status, **extra):
    r = {"at": g.ist_stamp(now) + " IST", "date": g.to_ist(now).date().isoformat(), "mode": mode,
         "kind": item.get("kind", "entry"), "symbol": (item.get("order") or {}).get("trading_symbol"),
         "fingerprint": g.fingerprint(item), "status": status}
    r.update({k: v for k, v in extra.items() if v is not None})
    state.setdefault("records", []).append(r)
    g.write_json(a.state, state)
    return r


def records_for(state, fp, today):
    return [r for r in state.get("records", []) if r.get("fingerprint") == fp and r.get("date") == today]


def status_of(item, risk, state, today):
    """(ready, label)."""
    fp = g.fingerprint(item)
    rv, why = g.risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by groww-risk: " + "; ".join(rv.get("reasons", []))
    rec = g.latest(state, fp, today)
    if rec:
        if rec["status"] == "approved":
            return False, "APPROVED by you at %s: waiting for groww.py place" % rec["at"]
        return False, "DONE: %s at %s" % (rec["status"], rec["at"])
    return True, "READY: groww-risk PASS, waiting for your yes"


def requests_for(item):
    """The HTTP calls this ticket makes, in order, as (method, path, body)."""
    calls = []
    for sid in item.get("cancel_smart") or []:
        calls.append(("POST", "/v1/order-advance/cancel/CASH/OCO/%s" % sid, None))
    calls.append(("POST", "/v1/order/create", item["order"]))
    if item.get("protect"):
        calls.append(("POST", "/v1/order-advance/create", item["protect"]))
    return calls


def render(item, idx, total, cfg, risk, state, today):
    ready, label = status_of(item, risk, state, today)
    mode = mode_of(cfg)
    lines = ["TICKET %d of %d, %s, Groww%s" % (idx, total, "LIVE account" if mode == "live" else "DRY RUN (nothing is sent)",
                                               "" if mode == "live" else " request preview")]
    for part in g.plain(item).split(" / "):
        lines.append("  " + part)
    if item.get("why"):
        lines.append("  why: " + item["why"])
    p = item.get("plan") or {}
    if p.get("brokerage_est_at_stop") is not None:
        lines.append("  brokerage about %s both ways (taxes and exchange charges on top)" % g.money(p["brokerage_est_at_stop"]))
    if item.get("kind") == "entry":
        lines.append("  flat by %s IST; Groww squares off open intraday positions at 3:20 PM (Rs 50 + GST each)"
                     % (p.get("flat_by") or cfg["flat_by"]))
    lines.append("  " + label)
    lines.append("  %s:" % ("It sends" if mode == "live" else "It would send"))
    for i, (m, path, body) in enumerate(requests_for(item), 1):
        pre = g.request_preview(m, path, body)
        tail = ""
        if path == "/v1/order-advance/create":
            tail = "   (after the entry fills, for the filled shares)"
        lines.append("    %d. %s%s" % (i, pre[0], tail))
        lines.extend("       " + x.strip() for x in pre[2:])
    if ready:
        lines.append("  Reply \"yes\" to place this one order, or \"no\" to skip it.")
    return lines


# ---------------------------------------------------------------- commands

def cmd_setup(a):
    cfg = g.load_config(a.config)
    flow = g.credential_flow()
    print("Groww login: %s" % (flow or "NOT SET"))
    print("Mode: %s" % ("LIVE (orders go to your real Groww account)" if cfg.get("live") else
                        "DRY-RUN (the default: nothing is sent to Groww)"))
    print("Config: %s%s" % (a.config, "" if cfg.get("_found") else " (missing: copy groww-budget/config.example.json)"))
    print("Token cache: %s%s" % (g.TOKEN_CACHE, " (exists)" if os.path.exists(g.TOKEN_CACHE) else ""))
    if not flow:
        print("\nTo connect (on your own computer, never in the chat):")
        print("  1. Groww app or groww.in > profile > Settings > Trading APIs: buy the Trade API subscription.")
        print("  2. Make an access token there (it expires at 6:00 AM daily), or an API key with TOTP on Groww Cloud.")
        print("  3. In your terminal, before starting Claude:  export GROWW_ACCESS_TOKEN=...   (or GROWW_API_KEY and")
        print("     GROWW_TOTP_SECRET). Then run: python3 groww-broker/groww.py check")
    return 0 if flow else 1


def _mask(s):
    s = str(s or "")
    return ("*" * max(0, len(s) - 4)) + s[-4:]


def cmd_check(a):
    gw = g.Groww()
    u = gw.user() or {}
    m = gw.margin() or {}
    eq = m.get("equity_margin_details") or {}
    print("Groww account %s: NSE %s, BSE %s, segments %s" % (
        _mask(u.get("ucc")), "on" if u.get("nse_enabled") else "off", "on" if u.get("bse_enabled") else "off",
        ", ".join(u.get("active_segments") or []) or "?"))
    print("Margin: clear cash %s, intraday (MIS) available %s, delivery (CNC) available %s, used %s" % (
        g.money(m.get("clear_cash") or 0), g.money(eq.get("mis_balance_available") or 0),
        g.money(eq.get("cnc_balance_available") or 0), g.money(m.get("net_margin_used") or 0)))
    cfg = g.load_config(a.config)
    print("Mode: %s. Read only: nothing was placed." % ("LIVE" if cfg.get("live") else "DRY-RUN"))
    return 0


def cmd_show(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    if not items:
        print("No tickets: run /groww-budget (size.py --write), or groww.py close --symbol SYM.")
        return 1
    for i, it in enumerate(items, 1):
        print("\n".join(render(it, i, len(items), cfg, risk, state, today)))
        print()
    print("One order at a time: a yes covers one ticket, never the batch. " + SAFETY)
    return 0 if any(status_of(it, risk, state, today)[0] for it in items) else 1


def cmd_approve(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    item = pick(items, a.index)
    ready, label = status_of(item, risk, state, today)
    if not ready:
        raise g.GrowwError("ticket %d is not ready for a yes: %s" % (a.index, label))
    try:
        known = {v["symbol"] for v in g.load_instruments(quiet=True, now_utc=now).values()}
    except g.GrowwError:
        known = set()
    ok, why = g.parse_yes(a.reply, item, [(it.get("order") or {}).get("trading_symbol", "") for it in items], known)
    if not ok:
        print("Not recorded: %s. Ask again, one order at a time." % why)
        return 1
    record(state, a, now, mode_of(cfg), item, "approved", reply=a.reply, plain=g.plain(item))
    print("Recorded your yes for ticket %d: %s." % (a.index, g.plain(item)))
    print("Next: groww.py place --index %d (%s)." % (a.index, "LIVE: it goes to Groww" if cfg.get("live")
                                                     else "DRY RUN: nothing is sent"))
    return 0


def cmd_decline(a):
    now, cfg, items, state, risk = load(a)
    item = pick(items, a.index)
    record(state, a, now, mode_of(cfg), item, "declined", reply=a.reply)
    print("Recorded: ticket %d declined. Nothing was placed." % a.index)
    return 0


def _approved(state, item, today):
    rec = g.latest(state, g.fingerprint(item), today)
    if not rec or rec.get("status") != "approved":
        raise g.GrowwError("no unused yes for this ticket today: show it, ask the user, then groww.py approve")
    return rec


def _wait_fill(gw, oid, wait):
    """Poll the order until it fills, dies or `wait` seconds pass. -> (status, filled_qty, avg_price, remark)."""
    deadline = time.time() + max(0, wait)
    st = {}
    while True:
        st = gw.order_status(oid) or {}
        status = str(st.get("order_status") or "").upper()
        if status in FILLED or status in DEAD or time.time() >= deadline:
            break
        time.sleep(2)
    filled = int(st.get("filled_quantity") or 0)
    avg = None
    if filled:
        try:
            avg = (gw.order_detail(oid) or {}).get("average_fill_price")
        except g.GrowwError:
            avg = None
    return status, filled, avg, st.get("remark")


def _oco_for(item, qty, fp):
    p = json.loads(json.dumps(item["protect"]))
    p["quantity"] = qty
    p["net_position_quantity"] = qty
    p["reference_id"] = g.ref_id("tsp", fp) if qty == item["protect"]["quantity"] else g.ref_id("tsq%d" % qty, fp)
    return p


def place_entry(a, now, cfg, item, state):
    mode, fp = mode_of(cfg), g.fingerprint(item)
    o = item["order"]
    loss, entry, _ = g.max_loss(item)
    base = {"max_loss": float(loss) if loss is not None else None,
            "notional": (item.get("plan") or {}).get("notional"), "qty": o["quantity"]}
    if mode == "dry-run":
        price = o["price"] if o["order_type"] == "LIMIT" else (item.get("plan") or {}).get("entry_for_risk")
        print("DRY RUN, nothing was sent to Groww. It would send:")
        for m, path, body in requests_for(item):
            print("\n".join("  " + x for x in g.request_preview(m, path, body)))
        record(state, a, now, mode, item, "dry-run filled", filled_qty=o["quantity"], avg_price=price,
               groww_order_id="dry-" + fp[:10], smart_order_id="dry-oco-" + fp[:8], **base)
        print("Recorded a simulated fill: %d %s at %s, protected by a simulated OCO (stop %s, target %s)."
              % (o["quantity"], o["trading_symbol"], g.pstr(price), item["protect"]["stop_loss"]["trigger_price"],
                 item["protect"]["target"]["trigger_price"]))
        return 0
    gw = g.Groww()
    try:
        res = gw.place(o) or {}
    except g.GrowwError as e:
        record(state, a, now, mode, item, "failed", note=str(e), **base)
        raise g.GrowwError("%s. Nothing was bought. A failed order is not retried without a new check and a new yes."
                           % e)
    oid = res.get("groww_order_id")
    if not oid:
        record(state, a, now, mode, item, "failed", note="Groww returned no order id: %s" % res, **base)
        raise g.GrowwError("Groww returned no order id; check the Groww app before trying again")
    record(state, a, now, mode, item, "placed", groww_order_id=oid, groww_status=res.get("order_status"), **base)
    print("Placed with Groww: order %s (%s). Waiting up to %ds for the fill..." % (oid, res.get("order_status"), a.wait))
    status, filled, avg, remark = _wait_fill(gw, oid, a.wait)
    if status in DEAD and not filled:
        record(state, a, now, mode, item, "failed", groww_order_id=oid, groww_status=status, note=remark, **base)
        print("Groww says %s%s. Nothing is held. A failed order is not retried without a new check and a new yes."
              % (status, (": " + remark) if remark else ""))
        return 1
    if not filled:
        record(state, a, now, mode, item, "open", groww_order_id=oid, groww_status=status, **base)
        print("Not filled yet (%s). When it fills: groww.py protect --index %d. To drop it: groww.py cancel --index %d."
              % (status or "pending", a.index, a.index))
        return 0
    record(state, a, now, mode, item, "filled", groww_order_id=oid, groww_status=status, filled_qty=filled,
           avg_price=avg, **base)
    print("Filled: %d %s%s." % (filled, o["trading_symbol"], (" at an average of %s" % avg) if avg else ""))
    return protect_now(a, now, cfg, item, state, gw, oid, filled)


def protect_now(a, now, cfg, item, state, gw, oid, filled):
    fp = g.fingerprint(item)
    body = _oco_for(item, filled, fp)
    try:
        res = gw.smart_create(body) or {}
    except g.GrowwError as e:
        record(state, a, now, "live", item, "unprotected", groww_order_id=oid, filled_qty=filled, note=str(e))
        print("WARNING: the stop/target OCO was NOT placed: %s" % e)
        print("Your %d shares have no stop at Groww. Set the stop in the Groww app now, or run groww.py protect "
              "--index %d, or close the position." % (filled, a.index))
        return 1
    sid = res.get("smart_order_id")
    record(state, a, now, "live", item, "protected", groww_order_id=oid, filled_qty=filled, smart_order_id=sid)
    print("Protected: OCO %s for %d shares, stop-loss %s, target %s (one cancels the other)."
          % (sid, filled, body["stop_loss"]["trigger_price"], body["target"]["trigger_price"]))
    print("It is a day order: be flat by %s IST either way (groww.py close --symbol %s)."
          % ((item.get("plan") or {}).get("flat_by") or cfg["flat_by"], item["order"]["trading_symbol"]))
    return 0


def place_exit(a, now, cfg, item, state):
    mode = mode_of(cfg)
    o = item["order"]
    if mode == "dry-run":
        print("DRY RUN, nothing was sent to Groww. It would send:")
        for m, path, body in requests_for(item):
            print("\n".join("  " + x for x in g.request_preview(m, path, body)))
        record(state, a, now, mode, item, "dry-run filled", filled_qty=o["quantity"], groww_order_id="dry-x" +
               g.fingerprint(item)[:10], qty=o["quantity"])
        _mark_closed(state, a, now, mode, o["trading_symbol"])
        print("Recorded a simulated exit: sold %d %s at market." % (o["quantity"], o["trading_symbol"]))
        return 0
    gw = g.Groww()
    for sid in item.get("cancel_smart") or []:
        try:
            gw.smart_cancel(sid)
            print("Cancelled the OCO %s." % sid)
        except g.GrowwError as e:
            if not re.search(r"GA004|not exist|CANCELLED|COMPLETED|TRIGGERED", str(e), re.I):
                record(state, a, now, mode, item, "failed", note="could not cancel OCO %s: %s" % (sid, e))
                raise g.GrowwError("could not cancel the OCO %s (%s): nothing was sold. Check the Groww app." % (sid, e))
            print("The OCO %s is no longer active (%s)." % (sid, e))
    held = [p for p in gw.positions() if str(p.get("trading_symbol")).upper() == o["trading_symbol"]
            and str(p.get("product")).upper() == "MIS"]
    qty_now = sum(int(p.get("quantity") or 0) for p in held)
    if qty_now <= 0:
        record(state, a, now, mode, item, "closed", note="no MIS position left at Groww (the stop or target filled)")
        _mark_closed(state, a, now, mode, o["trading_symbol"])
        print("Groww shows no intraday position in %s now (the stop or the target filled). Nothing was sold."
              % o["trading_symbol"])
        return 0
    body = dict(o)
    if qty_now < o["quantity"]:
        body["quantity"] = qty_now
        print("Groww shows %d shares, fewer than the ticket's %d: selling %d." % (qty_now, o["quantity"], qty_now))
    try:
        res = gw.place(body) or {}
    except g.GrowwError as e:
        record(state, a, now, mode, item, "failed", note=str(e), qty=body["quantity"])
        raise g.GrowwError("%s. The OCO is cancelled and the shares are still held: close them in the Groww app, or "
                           "write a new exit ticket (groww.py close)" % e)
    oid = res.get("groww_order_id")
    record(state, a, now, mode, item, "placed", groww_order_id=oid, qty=body["quantity"])
    status, filled, avg, remark = _wait_fill(gw, oid, a.wait) if oid else ("?", 0, None, None)
    if filled:
        record(state, a, now, mode, item, "filled", groww_order_id=oid, filled_qty=filled, avg_price=avg)
        _mark_closed(state, a, now, mode, o["trading_symbol"])
        print("Sold %d %s%s." % (filled, o["trading_symbol"], (" at an average of %s" % avg) if avg else ""))
        return 0
    print("The sell is %s%s: check the Groww app; groww.py status shows it." % (status, (": " + remark) if remark else ""))
    return 1


def _mark_closed(state, a, now, mode, symbol):
    today = g.to_ist(now).date().isoformat()
    for r in reversed(state.get("records", [])):
        if r.get("date") == today and r.get("mode") == mode and r.get("kind") == "entry" and r.get("symbol") == symbol \
                and r.get("status") in ("filled", "protected", "dry-run filled", "open"):
            r2 = dict(r, status="closed", at=g.ist_stamp(now) + " IST")
            state["records"].append(r2)
            g.write_json(a.state, state)
            return


def cmd_place(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    item = pick(items, a.index)
    rv, why = g.risk_verdict(risk, g.fingerprint(item), today)
    if rv is None or rv.get("verdict") != "PASS":
        raise g.GrowwError("ticket %d has no groww-risk PASS today: %s" % (a.index, why or "; ".join(rv.get("reasons", []))))
    _approved(state, item, today)
    if mode_of(cfg) == "live" and not g.credential_flow():
        raise g.GrowwError("LIVE mode but no Groww login in the environment: see groww.py setup")
    if item.get("kind") == "exit":
        return place_exit(a, now, cfg, item, state)
    return place_entry(a, now, cfg, item, state)


def cmd_protect(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    item = pick(items, a.index)
    recs = records_for(state, g.fingerprint(item), today)
    if not any(r["status"] == "approved" for r in recs):
        raise g.GrowwError("ticket %d was never approved today" % a.index)
    if any(r["status"] in ("protected", "dry-run filled") for r in recs):
        print("Ticket %d is already protected." % a.index)
        return 0
    placed = [r for r in recs if r.get("groww_order_id")]
    if not placed:
        raise g.GrowwError("ticket %d has not been placed: groww.py place --index %d" % (a.index, a.index))
    oid = placed[-1]["groww_order_id"]
    gw = g.Groww()
    status, filled, avg, remark = _wait_fill(gw, oid, 0)
    if not filled:
        print("Order %s is %s with nothing filled yet: no OCO is needed until it fills." % (oid, status))
        return 1
    record(state, a, now, "live", item, "filled", groww_order_id=oid, filled_qty=filled, avg_price=avg,
           max_loss=placed[-1].get("max_loss"), notional=placed[-1].get("notional"))
    return protect_now(a, now, cfg, item, state, gw, oid, filled)


def cmd_cancel(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    item = pick(items, a.index)
    words = re.findall(r"[a-z']+", (a.reply or "").lower())
    if not words or words[0] not in g.YES_WORDS or "?" in (a.reply or ""):
        print("Not cancelled: the reply has to be an explicit yes (for example \"yes cancel\").")
        return 1
    recs = [r for r in records_for(state, g.fingerprint(item), today) if r.get("groww_order_id")]
    if not recs:
        raise g.GrowwError("ticket %d has no order at Groww to cancel" % a.index)
    oid = recs[-1]["groww_order_id"]
    if mode_of(cfg) == "dry-run" or oid.startswith("dry"):
        record(state, a, now, mode_of(cfg), item, "cancelled", groww_order_id=oid, reply=a.reply)
        print("DRY RUN: would POST %s/v1/order/cancel {\"segment\": \"CASH\", \"groww_order_id\": \"%s\"}. Recorded."
              % (g.BASE_URL, oid))
        return 0
    res = g.Groww().cancel(oid) or {}
    record(state, a, now, "live", item, "cancelled", groww_order_id=oid, groww_status=res.get("order_status"),
           reply=a.reply)
    print("Cancel sent for %s: %s. If part of it filled first, groww.py status shows the shares; protect or close them."
          % (oid, res.get("order_status")))
    return 0


def cmd_close(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    mode = mode_of(cfg)
    sym = a.symbol.upper()
    exchange = cfg["exchange"]
    held, source, smart = None, "", []
    if mode == "live":
        gw = g.Groww()
        pos = [p for p in gw.positions() if str(p.get("trading_symbol")).upper() == sym
               and str(p.get("product")).upper() == "MIS"]
        held = sum(int(p.get("quantity") or 0) for p in pos)
        if pos:
            exchange = str(pos[0].get("exchange") or exchange).upper()
        source = "Groww's positions"
        try:
            smart = [s.get("smart_order_id") for s in gw.smart_orders() if str(s.get("trading_symbol")).upper() == sym
                     and s.get("smart_order_id")]
        except g.GrowwError as e:
            print("note: could not list active OCOs (%s); using the ids in the log" % e)
    if held is None:
        held = 0
        for r in g.records_today(state, today, mode):
            if r.get("symbol") == sym and r.get("filled_qty") and r.get("status") in ("filled", "dry-run filled"):
                held += int(r["filled_qty"]) if r.get("kind") == "entry" else -int(r["filled_qty"])
        source = "today's %s log" % mode
    if not smart:
        smart = sorted({r["smart_order_id"] for r in g.records_today(state, today, mode)
                        if r.get("symbol") == sym and r.get("smart_order_id") and r.get("kind") == "entry"})
    qty = a.qty if a.qty else held
    if qty <= 0:
        print("No intraday position in %s to close (from %s)." % (sym, source))
        return 1
    order = {"trading_symbol": sym, "quantity": int(qty), "price": 0, "validity": "DAY", "exchange": exchange,
             "segment": "CASH", "product": "MIS", "order_type": "MARKET", "transaction_type": "SELL"}
    item = {"kind": "exit", "symbol": sym, "exchange": exchange, "order": order, "cancel_smart": smart,
            "held": held}
    fp = g.fingerprint(item)
    order["order_reference_id"] = g.ref_id("tsx", fp)
    item.update({"fingerprint": fp, "why": "close %s: sell the %d intraday shares (from %s) at market, after "
                                          "cancelling its stop/target OCO" % (sym, qty, source)})
    g.write_json(a.orders, {"created_by": "groww-broker close", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "date": today, "broker": "groww", "live": mode == "live", "orders": [item]})
    print("Wrote the exit ticket to %s: %s." % (a.orders, g.plain(item)))
    print("Next: /groww-risk (an exit is never blocked by the day limits), then groww.py show and the user's yes.")
    return 0


def cmd_status(a):
    now, cfg, items, state, risk = load(a)
    today = g.to_ist(now).date().isoformat()
    mode = mode_of(cfg)
    if mode == "live" or a.live:
        gw = g.Groww()
        pos = [p for p in gw.positions() if int(p.get("quantity") or 0)]
        print("Open positions (Groww):" if pos else "No open positions at Groww.")
        for p in pos:
            print("  %-12s %5s %s, net price %s" % (p.get("trading_symbol"), p.get("quantity"), p.get("product"),
                                                     p.get("net_price")))
        ords = gw.orders()
        print("Today's orders (Groww): %d" % len(ords))
        for o in ords[:20]:
            print("  %s %s %s %s %s, filled %s, %s" % (o.get("groww_order_id"), o.get("transaction_type"),
                                                        o.get("quantity"), o.get("trading_symbol"), o.get("order_type"),
                                                        o.get("filled_quantity"), o.get("order_status")))
        smart = gw.smart_orders()
        print("Active OCOs: %d" % len(smart))
        for s in smart:
            print("  %s %s %s: stop %s, target %s" % (s.get("smart_order_id"), s.get("trading_symbol"), s.get("quantity"),
                                                     (s.get("stop_loss") or {}).get("trigger_price"),
                                                     (s.get("target") or {}).get("trigger_price")))
        return 0
    recs = g.records_today(state, today, mode)
    print("DRY-RUN status from the log (%d records today):" % len(recs))
    for r in recs:
        print("  %s  %-5s %-12s %-15s qty %s%s" % (r["at"][11:16], r["kind"], r.get("symbol"), r["status"],
                                                  r.get("filled_qty") or r.get("qty") or "-",
                                                  ("  max loss %s" % g.money(r["max_loss"])) if r.get("max_loss") else ""))
    return 0


def cmd_log(a):
    state = g.load_state(a.state)
    recs = state.get("records", [])[-a.last:]
    if not recs:
        print("The broker log is empty.")
        return 0
    for r in recs:
        print("%s  %-7s %-5s %-12s %-15s %s" % (r["at"], r["mode"], r["kind"], r.get("symbol"), r["status"],
                                               r.get("groww_order_id") or r.get("reply") or r.get("note") or ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order Executor (groww-broker): Groww orders, never without your yes.")
    ap.add_argument("--config", default=g.PATHS["config"])
    ap.add_argument("--orders", default=g.PATHS["orders"])
    ap.add_argument("--state", default=g.PATHS["broker"])
    ap.add_argument("--risk", default=g.PATHS["risk"])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("setup", help="what is set up; prints no secrets")
    sub.add_parser("check", help="read only: profile and margin from Groww")
    p = sub.add_parser("show", help="the tickets")
    p.add_argument("--now")
    p = sub.add_parser("approve", help="record your explicit yes for one ticket")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p.add_argument("--now")
    p = sub.add_parser("decline", help="record a no")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", default="no")
    p.add_argument("--now")
    for name, hlp in (("place", "send an approved ticket (DRY-RUN unless live)"),
                      ("protect", "place the OCO for an entry that filled later")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--index", type=int, required=True)
        p.add_argument("--wait", type=int, default=30, help="seconds to wait for the fill (30)")
        p.add_argument("--now")
    p = sub.add_parser("cancel", help="cancel a ticket's unfilled entry")
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True)
    p.add_argument("--now")
    p = sub.add_parser("close", help="write the exit ticket for an intraday position")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", type=int)
    p.add_argument("--now")
    p = sub.add_parser("status", help="positions, orders and OCOs")
    p.add_argument("--live", action="store_true", help="ask Groww even in DRY-RUN mode (read only)")
    p.add_argument("--now")
    p = sub.add_parser("log", help="the broker log")
    p.add_argument("--last", type=int, default=30)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"setup": cmd_setup, "check": cmd_check, "show": cmd_show, "approve": cmd_approve,
                "decline": cmd_decline, "place": cmd_place, "protect": cmd_protect, "cancel": cmd_cancel,
                "close": cmd_close, "status": cmd_status, "log": cmd_log}[a.cmd](a)
    except g.GrowwError as e:
        print("groww.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("groww.py: stopped. If an order was sent, check groww.py status before anything else.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
