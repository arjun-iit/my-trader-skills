#!/usr/bin/env python3
"""News monitor for india-news. Standard library only.

Watches NSE filings, upcoming board meetings and headlines for the NSE stocks
you watch or hold, and prints only what is new since the last check, newest
first, with how far each stock (and the NIFTY 50) is from the previous close.

    python3 news.py                                   # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE TCS M&M
    python3 news.py --tickers SBIN --all --hours 72   # everything in the last 3 days, seen or not
    python3 news.py --no-headlines                    # NSE filings and board meetings only
    python3 news.py --no-moves                        # skip the price lines

Your symbols, when --tickers is not given: every hit in trader-in/watchlist.json
(india-scanner), the symbol in trader-in/plan.json (india-exit) and the
positions in trader-in/portfolio.json.

Sources, one request each per symbol:
  - NSE corporate announcements (what the company told the exchange: results,
    board meeting outcomes, director changes, acquisitions, credit ratings,
    clarifications on price moves...):
    https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=<SYM>&from_date=..&to_date=..
  - NSE board meetings (the schedule, so a results date inside your trade shows):
    https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=<SYM>
  - Google News headlines for the company name (India edition; their links are
    long Google redirects, printed with --links and always in --json):
    https://news.google.com/rss/search?q="<Company>"+when:2d&hl=en-IN&gl=IN&ceid=IN:en
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against the previous close, for each symbol and the NIFTY 50.

NSE's site wants a browser-like request and sometimes a cookie from its home
page; the tool fetches one when NSE refuses. Times print in India time (IST).
State: trader-in/state/news.json remembers what you have seen (30 days), so an
item shows once. Offline: --offline DIR reads nse-ann-<SYM>.json,
nse-bm-<SYM>.json, gnews-<SYM>.xml and chart-<SYM>.json from DIR.

Headlines are leads, not facts: open the filing or the story before acting,
and treat nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import http.cookiejar
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NSE_HOME = "https://www.nseindia.com/"
NSE_ANN = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=%s&from_date=%s&to_date=%s"
NSE_BM = "https://www.nseindia.com/api/corporate-board-meetings?index=equities&symbol=%s"
GNEWS = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
YAHOO_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
IST = _dt.timedelta(hours=5, minutes=30)
KEEP_DAYS = 30
MOVE_PCT = 3.0       # a stock this far from the previous close is flagged as moving
RESULTS_DAYS = 7     # a results board meeting this close is flagged
HEADLINES_PER = 6    # newest Google News items kept per symbol
ALIASES = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYBANK": "^NSEBANK",
           "SENSEX": "^BSESN"}
INDEX_NAMES = {"^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK", "^BSESN": "SENSEX"}

# An NSE announcement whose category says one of these is marked "!".
IMPORTANT = re.compile(
    r"financial result|outcome of board meeting|change in (director|management|auditor|key managerial)"
    r"|resignation|cessation|acquisition|amalgamation|merger|demerger|scheme of arrangement|insolvency"
    r"|bankruptcy|default|fraud|credit rating|delisting|suspension|buy ?back|bonus|split|spurt in volume"
    r"|clarification|pledge|litigation|cyber|search|raid|penalt", re.I)
KEYWORDS = [
    ("earnings", r"\bresults?\b|\bprofit\b|\brevenue\b|\bEBITDA\b|\bQ[1-4]\b|\bquarter|\bguidance\b|\bearnings\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\boverweight\b|\bunderweight\b"
                r"|\boutperform\b|\bunderperform\b|\bbrokerage\b|\b(buy|sell|hold) rating\b"),
    ("legal", r"\blawsuit\b|\blitigation\b|\bcourt\b|\btribunal\b|\bNCLT\b|\bNCLAT\b|\bshow[- ]cause\b"
              r"|\bpenalt|\bprobe\b|\binvestigation\b|\braid\b|\bsearch operation|\bED\b|\bCBI\b|\btax demand"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bamalgamation\b|\bdemerger\b|\btakeover\b"
             r"|\bjoint venture\b|\bscheme of arrangement\b|\bstake\b|\border(s)? (win|worth|from)\b|\bbags\b"
             r"|\bcontract\b"),
    ("capital", r"\bdividend\b|\bbuy ?back\b|\bbonus\b|\bsplit\b|\brights issue\b|\bQIP\b|\bOFS\b"
                r"|offer for sale|\ballotment\b|fund ?rais|\bNCDs?\b|\bpreferential\b|\bblock deal\b|\bbulk deal\b"),
    ("people", r"\bCEO\b|\bMD\b|managing director|\bCFO\b|\bdirectors?\b|\bresign|\bappoint|\bKMP\b|\bpromoters?\b"),
    ("risk", r"\bdefault\b|\binsolvency\b|\bbankrupt|\bfraud\b|\bpledge|\bcredit rating\b|\bfire\b|\baccident\b"
             r"|\bshutdown\b|\brecall\b|\bcyber\b|\boutage\b"),
    ("halt", r"\bsuspen(d|sion)|\bcircuit\b|\btrading halt|\bASM\b|\bGSM\b|\bsurveillance\b|\bspurt in volume\b"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bUSFDA\b|\bFDA\b|\bCCI\b|\bDGCA\b|\bTRAI\b|\bapproval\b|\blicen[cs]e\b"
                   r"|warning letter|\bEIR\b|\bobservations?\b"),
    ("macro", r"\brepo rate\b|\bMPC\b|\binflation\b|\bCPI\b|\bGDP\b|\bUnion Budget\b|\bFIIs?\b|\bFPIs?\b"
              r"|\brupee\b|\bcrude\b|\bFed\b|\bmonsoon\b|\btariffs?\b"),
]
NAME_TAIL = re.compile(r"[\s,]+(limited|ltd\.?|private limited|pvt\.? ltd\.?)$", re.I)


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive India wall-clock time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST


def ist_naive_to_utc(local):
    return (local - IST).replace(tzinfo=_dt.timezone.utc)


def parse_time(value):
    """RFC 822 (RSS), ISO 8601, or NSE's '06-Nov-2026 19:10:58' (IST) -> aware UTC, or None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return ist_naive_to_utc(_dt.datetime.strptime(value, fmt))
        except ValueError:
            pass
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if d.tzinfo is None:
            return ist_naive_to_utc(d)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def norm_symbol(sym):
    s = (sym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def yahoo_symbol(sym):
    s = norm_symbol(sym)
    s = ALIASES.get(s, s)
    if s.startswith("^") or s.endswith(".BO"):
        return s
    return s + ".NS"


def is_index(sym):
    return yahoo_symbol(sym).startswith("^")


def nse_symbol(sym):
    """The NSE symbol for the exchange's own feeds, or None (an index, a BSE-only symbol)."""
    if is_index(sym):
        return None
    s = norm_symbol(sym)
    return s[:-3] if s.endswith(".BO") else s


def watch_from_files(portfolio="trader-in/portfolio.json", watchlist="trader-in/watchlist.json",
                     plan="trader-in/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[norm_symbol(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(norm_symbol(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[norm_symbol(sym)] = p.get("name") or out.get(norm_symbol(sym))
    return out


def names_from_universe(path="trader-in/universe.txt"):
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if "|" in ln:
                    s, n = ln.rstrip("\n").split("|", 1)
                    out[s.strip().upper()] = n.strip()
    return out


def search_name(name):
    """'Reliance Industries Limited' -> 'Reliance Industries' (what news stories call it)."""
    n = " ".join((name or "").split())
    for _ in range(2):
        n = NAME_TAIL.sub("", n)
    return n.strip()


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.4):
        self.offline = offline
        self.pause = pause
        self._last = 0.0
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._primed = False

    def _wait(self):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()

    def _open(self, url, headers):
        self._wait()
        req = urllib.request.Request(url, headers=dict({"Accept-Encoding": "gzip"}, **headers))
        with self._opener.open(req, timeout=30) as resp:
            data = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data

    def get(self, url, name, kind="web"):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        if kind == "nse":
            headers = {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*",
                       "Accept-Language": "en-IN,en;q=0.9", "Referer": "https://www.nseindia.com/companies-listing/"
                       "corporate-filings-announcements"}
        elif kind == "yahoo":
            headers = {"User-Agent": YAHOO_UA, "Accept": "application/json"}
        else:
            headers = {"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, application/xml, */*"}
        for attempt in (0, 1):
            try:
                return self._open(url, headers)
            except urllib.error.HTTPError as exc:
                if kind == "nse" and exc.code in (401, 403) and attempt == 0 and not self._primed:
                    self._primed = True
                    try:  # NSE hands out its cookies on the home page
                        self._open(NSE_HOME, {"User-Agent": BROWSER_UA, "Accept": "text/html"})
                    except (urllib.error.URLError, OSError):
                        pass
                    continue
                raise NewsError("HTTP %s for %s" % (exc.code, url.split("?")[0]))
            except urllib.error.URLError as exc:
                raise NewsError("could not reach %s: %s" % (url.split("/")[2], exc.reason))
            except OSError as exc:
                raise NewsError("network error for %s: %s" % (url.split("/")[2], exc))
        raise NewsError("NSE refused %s" % url.split("?")[0])


# ---------------------------------------------------------------- parsing

def _rows(data, what):
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    except ValueError:
        raise NewsError("%s is not JSON (NSE may have sent its block page)" % what)
    if isinstance(doc, dict):
        doc = doc.get("data") or doc.get("rows") or []
    return doc if isinstance(doc, list) else []


def parse_nse_ann(data, sym):
    """NSE corporate announcements JSON -> items."""
    out = []
    for r in _rows(data, "NSE announcements for %s" % sym):
        desc = " ".join(str(r.get("desc") or "announcement").split())
        text = " ".join(str(r.get("attchmntText") or "").split())
        company = r.get("sm_name") or sym
        when = parse_time(r.get("an_dt") or r.get("exchdisstime") or r.get("sort_date"))
        key = r.get("seq_id") or "%s|%s|%s" % (sym, r.get("an_dt"), desc)
        out.append({"id": "nse:%s" % key, "source": "NSE filing", "kind": "filing", "ticker": sym,
                    "company": company, "category": desc, "title": "%s: %s" % (company, desc),
                    "summary": text[:400], "link": r.get("attchmntFile") or "",
                    "important": bool(IMPORTANT.search(desc)), "time": when})
    return out


def parse_nse_bm(data, sym, today):
    """NSE board meetings JSON -> upcoming meetings [{date, purpose, results, desc}], soonest first."""
    by_date = {}
    for r in _rows(data, "NSE board meetings for %s" % sym):
        try:
            d = _dt.datetime.strptime(str(r.get("bm_date") or "").strip(), "%d-%b-%Y").date()
        except ValueError:
            continue
        if d < today:
            continue
        purpose = " ".join(str(r.get("bm_purpose") or "").split())
        desc = " ".join(str(r.get("bm_desc") or "").split())
        results = bool(re.search(r"result", purpose + " " + desc, re.I))
        m = {"ticker": sym, "date": d, "purpose": purpose or "Board meeting", "results": results,
             "desc": desc[:300], "company": r.get("sm_name") or sym}
        have = by_date.get(d)
        # NSE lists the intimation and the agenda as separate rows: one line per meeting,
        # the row that names the agenda (results) wins over a bare "Board Meeting Intimation".
        if have is None or (results and not have["results"]) or (
                results == have["results"] and "intimation" in have["purpose"].lower()):
            by_date[d] = m
    return [by_date[d] for d in sorted(by_date)]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_gnews(data, sym, limit=HEADLINES_PER):
    """Google News RSS -> headline items (newest `limit`)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("Google News for %s is not valid XML: %s" % (sym, exc))
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src_el = it.find("source")
        src = (src_el.text or "").strip() if src_el is not None else ""
        if src and title.endswith(" - " + src):
            title = title[:-len(" - " + src)].strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        out.append({"id": "gnews:" + guid, "source": src or "Google News", "kind": "headline",
                    "ticker": sym, "title": title, "summary": "", "link": link,
                    "time": parse_time(it.findtext("pubDate"))})
    out.sort(key=lambda i: i["time"] or _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc), reverse=True)
    return out[:limit]


def parse_chart(data, sym):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % sym)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % sym)
    when = meta.get("regularMarketTime")
    return {"ticker": sym, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


# ---------------------------------------------------------------- the check

def collect_moves(fetcher, symbols, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in symbols:
        name = "chart-%s.json" % t
        ys = urllib.parse.quote(yahoo_symbol(t), safe="")
        try:
            try:
                data = fetcher.get(CHART % ("query2", ys), name, "yahoo")
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", ys), name, "yahoo")
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def collect(fetcher, watch, now, hours, headlines=True, nse=True, errors=None):
    """-> (items, upcoming board meetings). Fills watch[sym] with the company name when NSE gives it."""
    errors = [] if errors is None else errors
    items, meetings = [], []
    today = to_ist(now).date()
    start = to_ist(now - _dt.timedelta(hours=hours)).date()
    for t in sorted(watch):
        ns = nse_symbol(t)
        if not ns:
            continue
        q = urllib.parse.quote(ns, safe="")
        if nse:
            try:
                ann = parse_nse_ann(fetcher.get(NSE_ANN % (q, start.strftime("%d-%m-%Y"), today.strftime("%d-%m-%Y")),
                                                "nse-ann-%s.json" % t, "nse"), t)
                items += ann
                if not watch.get(t) and ann:
                    watch[t] = ann[0]["company"]
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("NSE filings for %s: %s" % (t, exc))
            try:
                bm = parse_nse_bm(fetcher.get(NSE_BM % q, "nse-bm-%s.json" % t, "nse"), t, today)
                meetings += bm
                if not watch.get(t) and bm:
                    watch[t] = bm[0]["company"]
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("NSE board meetings for %s: %s" % (t, exc))
        if headlines:
            name = search_name(watch.get(t) or "") or ns
            days = max(1, int(math.ceil(hours / 24.0)))
            query = '"%s" (share OR shares OR stock OR NSE OR BSE OR Sensex OR Nifty) when:%dd' % (name, days)
            try:
                items += parse_gnews(fetcher.get(GNEWS % urllib.parse.quote_plus(query), "gnews-%s.xml" % t), t)
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("headlines for %s: %s" % (t, exc))
    return items, meetings


def mention_patterns(watch):
    pats = {}
    for t, name in watch.items():
        if is_index(t):
            continue
        alts = [r"\b(?:NSE|BSE)\s*:\s*%s\b" % re.escape(nse_symbol(t) or t)]
        n = search_name(name or "")
        if n and len(n) >= 4:
            alts.append(r"\b%s\b" % re.escape(n).replace(r"\ ", r"[\s-]"))
        pats[t] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s %s" % (it.get("category") or "", it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, rx in pats.items():
            if rx.search(hay):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
        if it["kind"] == "filing" and re.search(r"financial result", it.get("category") or "", re.I) \
                and "earnings" not in it["tags"]:
            it["tags"].insert(0, "earnings")
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


# ---------------------------------------------------------------- words

def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct and not is_index(m["ticker"]) else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        label = INDEX_NAMES.get(yahoo_symbol(m["ticker"]), m["ticker"])
        out.append("%-12s %s %+.2f%% vs previous close %s%s  %s" % (
            label, "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]), when, flag))
    return out


def render_meetings(meetings, today, results_days=RESULTS_DAYS):
    out = []
    for m in meetings:
        days = (m["date"] - today).days
        when = "today" if days == 0 else "tomorrow" if days == 1 else "in %d days" % days
        flag = "  RESULTS SOON" if m["results"] and days <= results_days else ""
        out.append("%-12s %s %s (%s)  %s%s" % (m["ticker"], m["date"].strftime("%a"), m["date"].isoformat(), when,
                                               m["purpose"], flag))
    return out


def render(items, errors, now, moves=None, meetings=None, move_pct=MOVE_PCT, results_days=RESULTS_DAYS,
           links=False):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    mt = render_meetings(meetings or [], to_ist(now).date(), results_days)
    if mt:
        out.append("Upcoming board meetings (NSE):")
        out += ["  " + ln for ln in mt]
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        mark = "!" if it.get("important") else " "
        who = ",".join(it["touches"]) or it["ticker"]
        src = it["source"] if len(it["source"]) <= 18 else it["source"][:17] + "."
        out.append("%s %s  %-12s %-18s %s" % (mark, when, who, src, it["title"]))
        if it["kind"] == "filing" and it.get("summary"):
            s = it["summary"]
            out.append("      " + (s if len(s) <= 220 else s[:217] + "..."))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        if it["link"] and (links or it["kind"] == "filing"):
            extra.append(it["link"])
        if extra:
            out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the filing or story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols (default: watchlist + plan + portfolio)")
    ap.add_argument("--portfolio", default="trader-in/portfolio.json")
    ap.add_argument("--watchlist", default="trader-in/watchlist.json")
    ap.add_argument("--plan", default="trader-in/plan.json")
    ap.add_argument("--universe", default="trader-in/universe.txt", help="SYMBOL|Name file for company names")
    ap.add_argument("--state", default="trader-in/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-headlines", action="store_true", help="NSE filings and board meetings only")
    ap.add_argument("--no-nse", action="store_true", help="headlines only")
    ap.add_argument("--no-moves", action="store_true", help="skip the price lines")
    ap.add_argument("--no-index", action="store_true", help="skip the NIFTY 50 line")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--results-days", type=int, default=RESULTS_DAYS, help="flag results this many days ahead")
    ap.add_argument("--links", action="store_true", help="print headline links too (long Google News URLs)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time (IST unless it has an offset), for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    if args.tickers:
        watch = {norm_symbol(t): known.get(norm_symbol(t)) for t in args.tickers}
    else:
        watch = known
    if not watch:
        print("news: no symbols. Pass --tickers, or run /india-scanner first (it writes "
              "trader-in/watchlist.json).", file=sys.stderr)
        return 2
    state = load_json(args.state) or {}
    names = dict(state.get("names") or {})
    names.update(names_from_universe(args.universe))
    for t in watch:
        if not watch[t] and names.get(nse_symbol(t) or t):
            watch[t] = names[nse_symbol(t) or t]
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items, meetings = collect(fetcher, watch, now, args.hours, headlines=not args.no_headlines,
                              nse=not args.no_nse, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = []
    if not args.no_moves:
        syms = ([] if args.no_index or "^NSEI" in [yahoo_symbol(t) for t in watch] else ["NIFTY"])
        syms += sorted(watch, key=lambda s: (not is_index(s), s))
        moves = collect_moves(fetcher, syms, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct and not is_index(m["ticker"])) for m in moves]
        bm = [dict(m, date=m["date"].isoformat(), days=(m["date"] - to_ist(now).date()).days) for m in meetings]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "board_meetings": bm,
                          "items": _json_ready(shown), "notes": errors}, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render(shown, errors, now, moves, meetings, args.move_pct, args.results_days, args.links))
    if not args.dry_run:
        remember(items, state, now)
        state["names"] = {k: v for k, v in dict(state.get("names") or {}, **{
            (nse_symbol(t) or t): n for t, n in watch.items() if n}).items()}
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
