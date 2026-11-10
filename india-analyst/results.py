#!/usr/bin/env python3
"""Earnings Analyst for india-analyst: reads the quarterly results a company
filed with NSE and says what changed. Standard library only.

    python3 results.py TATATECH
    python3 results.py SBIN RELIANCE --quarters 8
    python3 results.py TATATECH --standalone --json
    python3 results.py INFY TCS --metrics trader-in/metrics.json

Source: NSE's integrated filings (financials), one list request per symbol,
  https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol=<SYM>&type=Integrated Filing- Financials
and each filing's XBRL file on nsearchives.nseindia.com (one request per
quarter, cached in trader-in/state/results-cache.json). Consolidated results
by default, standalone when a company files no consolidated ones. A revised
filing replaces the original.

From each filing it takes the QUARTER column (the three-month context ending
on the quarter's last day): revenue from operations, total income, profit
before tax, net profit, diluted EPS, finance costs. For banks: interest
earned, interest expended, operating profit before provisions, provisions.
Growth is against the same quarter a year earlier. Margins: net margin = net
profit / revenue; operating margin is DERIVED as (profit before tax + finance
costs - other income) / revenue, because companies do not file an operating
profit line; banks get net interest income instead and no operating margin.
The last four quarters are summed when all four are there.

The next results date comes from NSE's board-meetings feed when one is listed.
Amounts print in Rs crore. It reports what was filed and never says a stock is
cheap, expensive, a buy or a sell.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "india-news"))
import news as N  # noqa: E402  (NSE fetcher with the cookie handling, board meetings)

LIST_URL = ("https://www.nseindia.com/api/integrated-filing-results?index=equities&symbol=%s"
            "&type=Integrated%%20Filing-%%20Financials&page=1&size=40")
CACHE = "trader-in/state/results-cache.json"
CRORE = 1e7
TAGS = {
    "revenue": ("RevenueFromOperations",),
    "interest_earned": ("InterestEarned",),
    "interest_expended": ("InterestExpended",),
    "income": ("Income", "TotalIncome"),
    "pbt": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "net_profit": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "owners_profit": ("ProfitOrLossAttributableToOwnersOfParent",
                      "ProfitLossAfterTaxesMinorityInterestAndShareOfProfitLossOfAssociates"),
    "eps": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "DilutedEarningsPerShareAfterExtraordinaryItems", "DilutedEarningsLossPerShareFromContinuingOperations"),
    "finance_costs": ("FinanceCosts",),
    "op_profit_bank": ("OperatingProfitBeforeProvisionAndContingencies",),
    "provisions": ("ProvisionsOtherThanTaxAndContingencies",),
    "exceptional": ("ExceptionalItemsBeforeTax",),
}


class ResultsError(Exception):
    pass


def qdate(text):
    return datetime.datetime.strptime(text.strip().title(), "%d-%b-%Y").date()


def parse_xbrl(data, quarter_end):
    """XBRL bytes -> {metric: number} for the three-month context ending on quarter_end."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ResultsError("the XBRL file is not valid XML: %s" % e)
    best = None
    for c in root.iter():
        if not c.tag.endswith("}context"):
            continue
        start = end = None
        dims = False
        for x in c.iter():
            t = x.tag.rsplit("}", 1)[-1]
            if t == "startDate":
                start = x.text
            elif t == "endDate":
                end = x.text
            elif t in ("explicitMember", "typedMember"):
                dims = True
        if dims or not start or not end:
            continue
        s, e = datetime.date.fromisoformat(start.strip()), datetime.date.fromisoformat(end.strip())
        days = (e - s).days
        if e == quarter_end and 80 <= days <= 100 and (best is None or days < best[1]):
            best = (c.get("id"), days, s)
    if not best:
        raise ResultsError("no three-month column ending %s in the filing" % quarter_end)
    facts = {}
    for el in root:
        if el.get("contextRef") == best[0] and el.text:
            facts[el.tag.rsplit("}", 1)[-1]] = el.text.strip()
    out = {"from": best[2].isoformat(), "to": quarter_end.isoformat(),
           "audited": facts.get("WhetherResultsAreAuditedOrUnaudited"),
           "nature": facts.get("NatureOfReportStandaloneConsolidated")}
    for key, names in TAGS.items():
        for n in names:
            if n in facts:
                try:
                    out[key] = float(facts[n])
                except ValueError:
                    pass
                break
    return out


def pick_filings(rows, standalone, quarters):
    """One filing per quarter: the wanted kind (consolidated first), the latest broadcast."""
    by_q = {}
    for r in rows:
        try:
            q = qdate(r["qe_Date"])
            when = datetime.datetime.strptime(r["broadcast_Date"], "%d-%b-%Y %H:%M:%S")
        except (KeyError, ValueError):
            continue
        if not r.get("xbrl") or r["xbrl"].endswith("/null"):
            continue
        kind = "standalone" if str(r.get("consolidated", "")).lower().startswith("stand") else "consolidated"
        by_q.setdefault(q, {}).setdefault(kind, [])
        by_q[q][kind].append((when, r))
    want = "standalone" if standalone else "consolidated"
    other = "consolidated" if standalone else "standalone"
    out = []
    for q in sorted(by_q, reverse=True)[:quarters]:
        kind = want if want in by_q[q] else other
        when, r = max(by_q[q][kind], key=lambda x: x[0])
        out.append({"quarter_end": q, "kind": kind, "filed": when, "xbrl": r["xbrl"], "ixbrl": r.get("ixbrl"),
                    "revised": str(r.get("type_Sub", "")).lower() != "original", "company": r.get("smName")})
    return out


def analyse(sym, fetcher, cache, standalone=False, quarters=5):
    sym = N.norm_symbol(sym)
    rows = N._rows(fetcher.get(LIST_URL % urllib.parse.quote(sym, safe=""), "nse-if-%s.json" % sym, "nse"),
                   "NSE results list for %s" % sym)
    if not rows:
        raise ResultsError("NSE lists no integrated financial filings for %s (check the symbol)" % sym)
    filings = pick_filings(rows, standalone, max(quarters, 5))
    notes, qs = [], []
    for f in filings:
        if f["xbrl"] in cache:
            vals = cache[f["xbrl"]]
        else:
            try:
                vals = parse_xbrl(fetcher.get(f["xbrl"], "xbrl-%s-%s.xml" % (sym, f["quarter_end"]), "nse"),
                                  f["quarter_end"])
            except (ResultsError, N.NewsError) as e:
                notes.append("%s quarter: %s" % (f["quarter_end"], e))
                continue
            cache[f["xbrl"]] = vals
        bank = "interest_earned" in vals and "revenue" not in vals
        rev = vals.get("interest_earned") if bank else vals.get("revenue")
        q = dict(vals, quarter_end=f["quarter_end"].isoformat(), kind=f["kind"], filed=f["filed"].strftime("%Y-%m-%d"),
                 revised=f["revised"], link=f["ixbrl"] or f["xbrl"], bank=bank, top_line=rev)
        profit = vals.get("net_profit")
        if rev and profit is not None:
            q["net_margin"] = 100.0 * profit / rev
        if bank and vals.get("interest_expended") is not None and rev:
            q["nii"] = rev - vals["interest_expended"]
        if not bank and rev and vals.get("pbt") is not None and vals.get("income") is not None:
            op = vals["pbt"] + vals.get("finance_costs", 0.0) - (vals["income"] - rev)
            q["op_margin"] = 100.0 * op / rev
        qs.append(q)
    if not qs:
        raise ResultsError("could not read any quarter for %s: %s" % (sym, "; ".join(notes)))
    by_end = {q["quarter_end"]: q for q in qs}
    for q in qs:
        e = datetime.date.fromisoformat(q["quarter_end"])
        try:
            prior = by_end.get(e.replace(year=e.year - 1).isoformat())
        except ValueError:
            prior = None
        if prior and prior["kind"] == q["kind"]:
            for k in ("top_line", "net_profit", "eps"):
                if q.get(k) is not None and prior.get(k):
                    q[k + "_yoy"] = 100.0 * (q[k] / prior[k] - 1) if prior[k] > 0 else None
    ttm = None
    last4 = qs[:4]
    if len(last4) == 4 and all(q.get("top_line") is not None and q.get("net_profit") is not None for q in last4):
        ends = [datetime.date.fromisoformat(q["quarter_end"]) for q in last4]
        if (ends[0] - ends[3]).days < 300 and len({q["kind"] for q in last4}) == 1:
            ttm = {"top_line": sum(q["top_line"] for q in last4), "net_profit": sum(q["net_profit"] for q in last4),
                   "eps": sum(q["eps"] for q in last4) if all(q.get("eps") is not None for q in last4) else None}
    latest = qs[0]
    if latest.get("exceptional"):
        notes.append("exceptional items of Rs %s crore are inside the latest quarter's profit before tax"
                     % cr(latest["exceptional"]))
    if not latest["bank"] and latest.get("income") and latest.get("top_line") and \
            (latest["income"] - latest["top_line"]) > 0.25 * abs(latest.get("pbt") or 0) > 0:
        notes.append("other income is %.0f%% of the latest quarter's profit before tax: gains outside the business "
                     "are inside it" % (100.0 * (latest["income"] - latest["top_line"]) / latest["pbt"]))
    if latest["kind"] != ("standalone" if standalone else "consolidated"):
        notes.append("no %s results filed: these are %s" % ("standalone" if standalone else "consolidated", latest["kind"]))
    nxt = None
    try:
        bm = N.parse_nse_bm(fetcher.get(N.NSE_BM % urllib.parse.quote(sym, safe=""), "nse-bm-%s.json" % sym, "nse"),
                            sym, N.to_ist(datetime.datetime.now(datetime.timezone.utc)).date())
        res = [m for m in bm if m["results"]]
        if res:
            nxt = {"date": res[0]["date"].isoformat(), "purpose": res[0]["purpose"]}
    except N.NewsError as e:
        notes.append("board meetings: %s" % e)
    return {"symbol": sym, "company": filings[0]["company"], "kind": latest["kind"], "bank": latest["bank"],
            "quarters": qs, "ttm": ttm, "next_results": nxt, "notes": notes}


def cr(x):
    return "{:,.2f}".format(x / CRORE) if x is not None else "-"


def pct(x):
    return "%+.1f%%" % x if x is not None else "-"


def report(d, show):
    qs = d["quarters"][:show]
    top = "Int. earned" if d["bank"] else "Revenue"
    out = ["%s (%s), %s results filed with NSE, Rs crore" % (d["company"], d["symbol"], d["kind"])]
    out.append("  %-12s %12s %8s %12s %8s %9s %8s %7s %7s  %s" % (
        "quarter end", top[:12], "vs yr", "net profit", "vs yr", "dil. EPS", "vs yr", "net m.",
        "NII" if d["bank"] else "op m.", "filed"))
    for q in qs:
        extra = cr(q.get("nii")) if d["bank"] else ("%.1f%%" % q["op_margin"] if q.get("op_margin") is not None else "-")
        out.append("  %-12s %12s %8s %12s %8s %9s %8s %7s %7s  %s%s" % (
            q["quarter_end"], cr(q.get("top_line")), pct(q.get("top_line_yoy")), cr(q.get("net_profit")),
            pct(q.get("net_profit_yoy")), ("%.2f" % q["eps"]) if q.get("eps") is not None else "-",
            pct(q.get("eps_yoy")), ("%.1f%%" % q["net_margin"]) if q.get("net_margin") is not None else "-", extra,
            q["filed"], " (revised)" if q["revised"] else ""))
    if d["ttm"]:
        t = d["ttm"]
        out.append("  Last four quarters: %s Rs %s crore, net profit Rs %s crore%s." % (
            top.lower(), cr(t["top_line"]), cr(t["net_profit"]),
            (", diluted EPS Rs %.2f" % t["eps"]) if t["eps"] is not None else ""))
    if not d["bank"]:
        out.append("  Operating margin is derived: (profit before tax + finance costs - other income) / revenue.")
    out.append("  Next results: %s" % ("%s (%s), from NSE's board-meetings list" % (
        d["next_results"]["date"], d["next_results"]["purpose"]) if d["next_results"]
        else "no results meeting listed on NSE yet (check /india-news and the company's investor page)"))
    for n in d["notes"]:
        out.append("  NOTE: %s." % n)
    out.append("  Latest filing: %s" % qs[0]["link"])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Earnings Analyst (india-analyst): quarterly results from NSE filings.")
    ap.add_argument("symbols", nargs="+", help="NSE symbols")
    ap.add_argument("--quarters", type=int, default=5, help="quarters to show (default 5)")
    ap.add_argument("--standalone", action="store_true", help="standalone results (default consolidated)")
    ap.add_argument("--metrics", metavar="PATH", help="write one row per company")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cache = N.load_json(a.cache) or {}
    fetcher = N.Fetcher(offline=a.offline)
    docs, failed = [], 0
    for sym in a.symbols:
        try:
            docs.append(analyse(sym, fetcher, cache, a.standalone, a.quarters + 4))
        except (ResultsError, N.NewsError) as e:
            failed += 1
            print("results.py: %s: %s" % (sym.upper(), e), file=sys.stderr)
    if not a.offline and cache:
        os.makedirs(os.path.dirname(a.cache), exist_ok=True)
        with open(a.cache, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    if a.metrics and docs:
        rows = []
        for d in docs:
            q = d["quarters"][0]
            rows.append({"symbol": d["symbol"], "company": d["company"], "kind": d["kind"],
                         "quarter_end": q["quarter_end"], "top_line_cr": round(q["top_line"] / CRORE, 2) if q.get("top_line") else None,
                         "top_line_yoy_pct": q.get("top_line_yoy"), "net_profit_cr": round(q["net_profit"] / CRORE, 2) if q.get("net_profit") is not None else None,
                         "net_profit_yoy_pct": q.get("net_profit_yoy"), "diluted_eps": q.get("eps"),
                         "net_margin_pct": q.get("net_margin"), "op_margin_pct": q.get("op_margin"),
                         "ttm": d["ttm"], "next_results": d["next_results"]})
        os.makedirs(os.path.dirname(a.metrics) or ".", exist_ok=True)
        with open(a.metrics, "w", encoding="utf-8") as fh:
            json.dump({"source": "NSE integrated filings (XBRL)", "rows": rows}, fh, indent=2)
            fh.write("\n")
    if a.json:
        print(json.dumps(docs, indent=2))
    else:
        for d in docs:
            print("\n".join(report(d, a.quarters)))
            print()
        if docs:
            print("Filed numbers, not advice. Growth is against the same quarter a year earlier.%s"
                  % ((" Wrote %s." % a.metrics) if a.metrics else ""))
    return 0 if docs and not failed else 1


if __name__ == "__main__":
    sys.exit(main())
