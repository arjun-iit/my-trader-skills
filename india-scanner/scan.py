#!/usr/bin/env python3
"""Chart Scanner for india-scanner: scan NSE charts for setups. Standard library only.

    python3 scan.py universe                          # every NSE EQ-series stock -> trader-in/universe.txt
    python3 scan.py universe --index nifty500         # or one index: nifty50, nifty100, nifty200, ...
    python3 scan.py daily                             # before 9:15 or after 15:30 IST -> trader-in/watchlist.json
    python3 scan.py daily --index nifty50             # an index's members, fetched on the spot
    python3 scan.py daily --symbols RELIANCE,TCS,M&M  # or --watchlist FILE, or --limit 200
    python3 scan.py orb                               # after 9:35 AM IST: opening-range breakouts so far
    python3 scan.py orb --index nifty50 --at 10:00    # cut the clock at 10:00 AM IST
    python3 scan.py orb --offline bundle.json --now "2026-11-09 10:00"

Symbols are NSE symbols (RELIANCE, M&M, BAJAJ-AUTO), read on Yahoo as
RELIANCE.NS. A symbol ending in .BO is read on BSE (RELIANCE.BO), one starting
with ^ is an index (^NSEI), and NIFTY, BANKNIFTY and SENSEX are short for
^NSEI, ^NSEBANK and ^BSESN.

universe  Downloads NSE's list of listed equities (EQUITY_L.csv) and keeps the
          EQ series: normal rolling settlement, where intraday trading is
          allowed. BE and BZ (trade-for-trade, delivery only) are out.
          --index NAME instead downloads that index's members from
          niftyindices.com: nifty50, niftynext50, nifty100, nifty200,
          nifty500, niftymidcap100, niftysmallcap100, niftybank.

daily     Scan of daily CLOSES (Yahoo spark: 20 symbols a request, 3 months of
          1-day bars, closes only). Liquidity from the spark meta: last price
          >= --min-price (Rs 20) and last price x last session's volume >=
          --min-turnover (Rs 5 crore). Indices skip the turnover floor (Yahoo
          gives them no volume). Setups, from closes only:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10 % over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5 % range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 yesterday and today closed up
          Today's daily bar is dropped until the 15:30 close.

orb       Opening-range breakouts, 5-minute bars. The range is the first
          --or-minutes (15) of the session: the 9:15, 9:20 and 9:25 bars.
          Stage 1: spark closes for every symbol (1 day of 5-minute bars, 20 a
          request); a chart goes on when a completed bar after the range closed
          at or above the highest close of the range (a necessary condition),
          or when spark is missing a bar (spark drops one now and then, so the
          full chart decides). Stage 2: each candidate's v8 chart (real highs
          and lows): range high = the highest high of the range bars, a hit =
          the first completed bar to CLOSE above it, at that bar's close time.
          Stage 2 fetches at most --max-confirm (200) charts, the most traded
          candidates first, and says so when it stops short.
          A chart is usable when every opening-range bar is there and no more
          than 20 % of the bars to the cutoff are missing (more is a halt or a
          thin stock). Yahoo often drops a bar after the range, mostly at
          15:15-15:25; a hit that comes after a missing bar says so, because
          the real first close above could have been in the gap.
          Floors: --min-price 20, --min-turnover 0.

Bars: null closes are dropped, a bar off the 5-minute grid (the live tick)
is dropped, and a bar counts only once it is complete (start + 5 minutes <=
the clock). Times are India time (IST, UTC+5:30, no daylight saving). --now
pins the clock ("2026-11-09 10:00", IST; ...Z for UTC).

Yahoo's endpoints are free, unofficial and can lag. query2 first, query1 on
a 429 or 5xx, one retry after a pause on a 429. A polite pause between
requests (--pause). Partial results are reported as partial.

--offline PATH reads files instead of the network: universe wants a folder
with EQUITY_L.csv (or ind_<index>list.csv with --index); daily and orb take a
folder of <T>.json v8 chart files or spark responses, or one file that is a
spark response, a v8 chart, or a bundle {"RELIANCE.NS": <v8 json>, ...}.
.json.gz is fine.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import csv
import datetime
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
NSE_EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
INDEX_URL = "https://www.niftyindices.com/IndexConstituent/ind_%slist.csv"
INDEXES = {
    "nifty50": "NIFTY 50", "niftynext50": "NIFTY NEXT 50", "nifty100": "NIFTY 100",
    "nifty200": "NIFTY 200", "nifty500": "NIFTY 500", "niftymidcap100": "NIFTY MIDCAP 100",
    "niftysmallcap100": "NIFTY SMALLCAP 100", "niftybank": "NIFTY BANK",
}
SPARK_MAX = 20      # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3         # seconds between requests: be polite to free endpoints
BACKOFF = 5.0       # seconds to wait before the one retry after a 429
SLEEP = time.sleep  # tests swap this out
BAR_MIN = 5
MAX_CONFIRM = 200   # stage-2 v8 charts per orb scan, most traded first
MAX_GAP_SHARE = 0.2  # more than this share of the bars missing: a halt or a thin stock
MIN_DAILY_BARS = 30
CRORE = 1e7
DEFAULT_UNIVERSE = "trader-in/universe.txt"
DEFAULT_WATCHLIST = "trader-in/watchlist.json"
DEFAULT_CONFIG = "trader-in/config.json"
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
ALIASES = {"NIFTY": "^NSEI", "NIFTY50": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYBANK": "^NSEBANK",
           "SENSEX": "^BSESN"}

# NSE trading holidays, capital market segment (nseindia.com holiday master, read 2026-11-09).
# Weekend dates are listed by NSE too; they change nothing.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading session only)",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local - IST


def ts_to_ist(ts):
    """Unix seconds -> naive India time. Never uses the machine's time zone."""
    return to_ist(EPOCH + datetime.timedelta(seconds=int(ts)))


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as India time (IST)."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ScanError('--now wants a time like "2026-11-09 10:00" (IST) or ...Z (UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, flag="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScanError("%s wants an India time like 10:00" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d):
    """(is_trading_day, reason). Weekends and the NSE holiday list."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in India time, or None if closed all day."""
    ok, _ = trading_day(d)
    if not ok:
        return None
    return OPEN, CLOSE


def clock_label(t):
    """datetime or time -> '10:15 AM'."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


# ---------------------------------------------------------------- symbols

def to_yahoo(sym):
    """RELIANCE -> RELIANCE.NS; RELIANCE.BO, ^NSEI stay; NIFTY -> ^NSEI."""
    s = sym.strip().upper()
    s = ALIASES.get(s, s)
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    return s + ".NS"


def from_yahoo(ysym):
    """RELIANCE.NS -> RELIANCE. BSE symbols and indices keep their Yahoo form."""
    s = (ysym or "").strip().upper()
    return s[:-3] if s.endswith(".NS") else s


def is_index(sym):
    return to_yahoo(sym).startswith("^")


def _q(ysym):
    return urllib.parse.quote(ysym, safe="")


# ---------------------------------------------------------------- network

def http_get(url, timeout=20, ua=UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise ScanError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(_q(to_yahoo(s)) for s in symbols), rng, interval)


def chart_path(sym, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (_q(to_yahoo(sym)), rng, interval)


# ---------------------------------------------------------------- parsing

def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are simply missing
    from the answer. `wanted` maps Yahoo symbols back to ours (^NSEI -> NIFTY)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise ScanError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = (item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or "").upper()
        sym = (wanted or {}).get(ysym, from_yahoo(ysym))
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise ScanError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, sym=""):
    """v8 chart JSON -> series. Raises ScanError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise ScanError("Yahoo returned something unexpected for %s" % sym)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise ScanError("no data for %s: %s" % (sym, err.get("description", "unknown symbol")))
    return series_from_result(res[0])


def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = from_yahoo(sym)
        try:
            data[sym] = parse_chart(doc, sym)
        except ScanError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <T>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            if isinstance(doc, dict) and ("chart" in doc or "spark" in doc):
                _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
            else:
                _absorb(doc, "", data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- universe

def _csv_rows(text):
    """CSV text -> rows of stripped strings. Extra fields on a row (more than the header) are
    ignored; a web page instead of a CSV is an error, not a crash."""
    head = text.lstrip()[:200].lower()
    if head.startswith("<") or "<html" in head:
        raise ScanError("the site sent a web page, not the CSV list: try again in a minute")
    rows = list(csv.DictReader(io.StringIO(text.replace("\r", ""))))
    out = []
    for r in rows:
        out.append({(k or "").strip(): (v if isinstance(v, str) else "").strip() for k, v in r.items() if k is not None})
    return out


def build_equity_universe(text):
    """NSE EQUITY_L.csv -> (kept [(SYM, name)], counts). Keeps the EQ series."""
    rows = _csv_rows(text)
    if not rows or "SYMBOL" not in rows[0] or "SERIES" not in rows[0]:
        raise ScanError("not NSE's EQUITY_L.csv (no SYMBOL and SERIES columns)")
    kept, counts = {}, {"rows": len(rows)}
    for r in rows:
        series = r.get("SERIES") or "?"
        if series != "EQ":
            why = {"BE": "BE (trade-for-trade, no intraday)", "BZ": "BZ (trade-for-trade, no intraday)"}.get(
                series, "%s series" % series)
            counts[why] = counts.get(why, 0) + 1
            continue
        kept[r["SYMBOL"].upper()] = " ".join((r.get("NAME OF COMPANY") or "").split())
    return sorted(kept.items()), counts


def build_index_universe(text, index):
    """niftyindices.com ind_<index>list.csv -> kept [(SYM, name)]."""
    rows = _csv_rows(text)
    if not rows or "Symbol" not in rows[0]:
        raise ScanError("not a niftyindices.com constituent list for %s" % index)
    kept = {r["Symbol"].upper(): " ".join((r.get("Company Name") or "").split()) for r in rows if r.get("Symbol")}
    return sorted(kept.items())


def index_key(name):
    k = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if k not in INDEXES:
        raise ScanError("unknown index %r: pick one of %s" % (name, ", ".join(sorted(INDEXES))))
    return k


def fetch_index(index, offline=None):
    k = index_key(index)
    if offline:
        p = os.path.join(offline, "ind_%slist.csv" % k) if os.path.isdir(offline) else offline
        if not os.path.exists(p):
            raise ScanError("no ind_%slist.csv in %s" % (k, offline))
        with open(p, encoding="utf-8-sig", errors="replace") as fh:
            return k, build_index_universe(fh.read(), k)
    try:
        text = http_get(INDEX_URL % k, timeout=60, ua=BROWSER_UA)
    except urllib.error.HTTPError as e:
        raise ScanError("niftyindices.com answered HTTP %d for the %s list" % (e.code, INDEXES[k]))
    except (urllib.error.URLError, OSError) as e:
        raise ScanError("could not reach niftyindices.com: %s" % getattr(e, "reason", e))
    return k, build_index_universe(text, k)


# ---------------------------------------------------------------- daily setups

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def daily_closes(series, now_utc):
    """[(date, close)] of COMPLETE daily bars: null closes dropped, today's bar dropped
    until the session closes, anything dated after the clock dropped."""
    now = to_ist(now_utc)
    today = now.date()
    s = session(today)
    today_done = s is None or now.time() >= s[1]
    out = {}
    for ts, c in zip(series["t"], series["c"]):
        if c is None:
            continue
        d = ts_to_ist(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = float(c)  # one bar a day: a late duplicate (the live tick) wins
    return sorted(out.items())


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def scan_daily(data, symbols, now_utc, min_price, min_turnover_cr):
    """Pure. data = {SYM: series}. Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = daily_closes(s, now_utc)
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None and bars:
            price = bars[-1][1]
        vol = meta.get("regularMarketVolume")
        turnover = (price or 0) * (vol or 0)
        if price is None or (price < min_price and not is_index(sym)) or (
                min_turnover_cr > 0 and not is_index(sym) and turnover < min_turnover_cr * CRORE):
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1][0]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [c for _, c in bars]
        tags, nums = find_setups(closes)
        if tags:
            hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                         "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1),
                         "volume": int(vol) if vol is not None else None,
                         "turnover_cr": round(turnover / CRORE, 2) if vol else None})
    hits.sort(key=lambda h: (-(h["turnover_cr"] or 0), h["symbol"]))  # most rupees traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def slot(day, k):
    return datetime.datetime.combine(day, OPEN) + datetime.timedelta(minutes=BAR_MIN * k)


def session_bars(series, day, cutoff):
    """5-minute bars of `day` that are complete at `cutoff` (naive India time),
    on the grid, with a close. Sorted, one per slot."""
    s = session(day)
    close_t = s[1] if s else CLOSE
    out = {}
    hs, ls = series.get("h"), series.get("l")
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        lt = ts_to_ist(ts)
        if lt.date() != day or lt.second or lt.minute % BAR_MIN:
            continue  # another day, or the live tick off the grid
        if lt.time() < OPEN or lt.time() >= close_t:
            continue  # pre-open, the 15:30 closing print
        if lt + datetime.timedelta(minutes=BAR_MIN) > cutoff:
            continue  # still in progress at the cutoff
        v = series.get("v")
        out[lt] = {"start": lt, "c": float(c),
                   "h": None if not hs or hs[i] is None else float(hs[i]),
                   "l": None if not ls or ls[i] is None else float(ls[i]),
                   "v": (v[i] or 0) if v else 0}
    return [out[k] for k in sorted(out)]


def coverage(bars, day, cutoff, n_range):
    """What Yahoo sent against what the clock says should be there:
    {want, have, range_ok, gaps [missing slot datetimes after the range], usable}."""
    want = int((cutoff - slot(day, 0)).total_seconds() // (BAR_MIN * 60))
    s = session(day)
    if s:
        want = min(want, int((datetime.datetime.combine(day, s[1]) - slot(day, 0)).total_seconds()
                             // (BAR_MIN * 60)))
    have = {b["start"] for b in bars}
    range_ok = all(slot(day, k) in have for k in range(n_range))
    gaps = [slot(day, k) for k in range(n_range, want) if slot(day, k) not in have]
    after = sum(1 for b in bars if b["start"] >= slot(day, n_range))
    usable = (range_ok and want > n_range and after > 0
              and (want - len(bars)) <= MAX_GAP_SHARE * want)
    return {"want": want, "have": len(bars), "range_ok": range_ok, "gaps": gaps, "usable": usable}


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout (range high >= any range close)."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range, gaps=()):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high.
    `bars` must start with the n_range range bars."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            before = [g.strftime("%H:%M") for g in gaps if g < b["start"]]
            h = {"price": round(b["c"], 2), "at": clock_label(closes_at),
                 "bar_start": b["start"].strftime("%H:%M"),
                 "bar_index": int((b["start"] - rng[0]["start"]).total_seconds() // (BAR_MIN * 60)),
                 "or_high": round(oh, 2), "or_low": round(ol, 2),
                 "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
            if before:
                h["missing_bars_before"] = before
            return h
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff naive IST) or raise ScanError with the plain message."""
    now = to_ist(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day)
    if not ok:
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day "
                        "after the opening range, or replay a past day with --now." % (day_label(day), why))
    s = session(day)
    range_end = slot(day, or_minutes // BAR_MIN)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    close_dt = datetime.datetime.combine(day, s[1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s IST)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("The market closed at %s IST on %s. Replay the session with --at 10:00."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s IST: the %d-minute opening range runs 9:15 to %s and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s IST, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb on the next session after %s, "
                        "or replay today with --at 10:00." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                               clock_label(first_close)))
    return day, cutoff


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_turnover_cr=0.0, confirm=None,
             max_confirm=None):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). With confirm and max_confirm, only the max_confirm most
    traded candidates (price x volume so far) are confirmed; the rest are counted as unconfirmed.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0, "gappy": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = session_bars(s, day, cutoff)
        cov = coverage(bars, day, cutoff, n_range)
        spark_gap = confirm is not None and (not cov["range_ok"] or cov["gaps"])
        if not cov["usable"] and not (spark_gap and len(bars) >= (1 - MAX_GAP_SHARE) * cov["want"]):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        last = bars[-1]["c"]
        if not is_index(sym) and (last < min_price or (min_turnover_cr > 0 and last * vol < min_turnover_cr * CRORE)):
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if spark_gap:
            counts["gappy"] += 1
            cands.append((sym, bars, last * vol))
        elif orb_candidate(bars, n_range):
            cands.append((sym, bars, last * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except ScanError as e:
                failed[sym] = str(e)
                continue
            full = session_bars(s2, day, cutoff)
        cov = coverage(full, day, cutoff, n_range)
        if not cov["usable"]:
            failed[sym] = ("Yahoo's chart is missing an opening-range bar" if not cov["range_ok"]
                           else "Yahoo's chart is missing too many bars")
            continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range, cov["gaps"])
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- fetching

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def fetch_spark(symbols, rng, interval, pause, a=None):
    """Batches of 20. -> ({SYM: series}, {SYM: error})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {to_yahoo(s): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)), wanted)
        except (ScanError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
        if a is not None and len(batches) > 25 and (k + 1) % 50 == 0:
            progress(a, "  spark %d of %d requests" % (k + 1, len(batches)))
    return data, failed


def read_symbols(a, offline_syms=None):
    """-> (symbols, label of where they came from)."""
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
        label = "your list"
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
        label = a.watchlist
    elif getattr(a, "index", None):
        k, kept = fetch_index(a.index, a.offline if a.offline and os.path.isdir(a.offline) and
                              os.path.exists(os.path.join(a.offline, "ind_%slist.csv" % index_key(a.index)))
                              else None)
        syms = [s for s, _ in kept]
        label = INDEXES[k]
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)  # offline: every chart in the file, unless a list was given
        label = "the offline file"
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe` first, or pass --index nifty50 or --symbols" % path)
        syms = symbols_from_file(path)
        label = path
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out, label


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def money(x):
    return "Rs {:,.2f}".format(x)


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def _p(x):
    return "{:,.2f}".format(x)


def _c(n):
    return "{:,}".format(n)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    t0 = time.time()
    if a.index:
        k, kept = fetch_index(a.index, a.offline)
        counts, source = {"rows": len(kept)}, "%s members (niftyindices.com)" % INDEXES[k]
    else:
        if a.offline:
            p = os.path.join(a.offline, "EQUITY_L.csv") if os.path.isdir(a.offline) else a.offline
            if not os.path.exists(p):
                raise ScanError("no EQUITY_L.csv in %s" % a.offline)
            with open(p, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
        else:
            try:
                text = http_get(NSE_EQUITY_URL, timeout=60, ua=BROWSER_UA)
            except urllib.error.HTTPError as e:
                raise ScanError("nseindia.com answered HTTP %d for EQUITY_L.csv" % e.code)
            except (urllib.error.URLError, OSError) as e:
                raise ScanError("could not reach nsearchives.nseindia.com: %s" % getattr(e, "reason", e))
        kept, counts = build_equity_universe(text)
        source = "NSE's list of listed equities (EQUITY_L.csv)"
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name in kept:
                fh.write("%s|%s\n" % (sym, name))
    dropped = {k: v for k, v in counts.items() if k != "rows"}
    if a.json:
        print(json.dumps({"count": len(kept), "rows": counts.get("rows", 0), "source": source,
                          "dropped": dropped, "out": None if a.no_write else a.out,
                          "seconds": round(time.time() - t0, 1)}, indent=2))
    else:
        print("%s: %s rows." % (source, _c(counts.get("rows", 0))))
        if a.index:
            print("Kept all %s." % _c(len(kept)))
        else:
            print("Kept %s EQ-series stocks (normal settlement, intraday allowed)." % _c(len(kept)))
            if dropped:
                print("Dropped: " + ", ".join("%s %s" % (_c(v), k)
                                              for k, v in sorted(dropped.items(), key=lambda kv: -kv[1])) + ".")
        if not a.no_write:
            print("Wrote %s (one SYMBOL|Name a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    now = parse_now(a.now)
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols, label = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily closes for %s symbols from %s (%d spark requests)..."
                 % (_c(len(symbols)), label, (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, "3mo", "1d", a.pause, a)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_turnover)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    doc = {"created_by": "india-scanner", "market": "NSE", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "daily", "as_of": as_of, "symbols_from": label, "universe": len(symbols),
           "scanned": counts["scanned"], "failed": nfailed, "source": "yahoo",
           "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    local = to_ist(now)
    print("Daily scan, %s IST, %s, closes through %s (Yahoo spark, daily closes only)." % (
        local.strftime("%a %Y-%m-%d %H:%M"), label, last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s. %s passed the liquidity filter (Rs %s+ and Rs %s crore+ traded)%s; "
          "%s scanned." % (_c(counts["fetched"]), _c(len(symbols)),
                           (" (%s failed or had no data)" % _c(nfailed)) if nfailed else "",
                           _c(counts["fetched"] - counts["illiquid"]), _n(a.min_price), _n(a.min_turnover),
                           (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
                           _c(counts["scanned"])))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                  ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (price x volume):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-12s %14s  RSI %5.1f  %s" % (h["symbol"], money(h["price"]), h["rsi"],
                                               ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /india-news SYMBOL.")
    return 0 if hits else 1


def cmd_orb(a):
    t0 = time.time()
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "15:00", "--until")
    data = failed = None
    explicit_day = None
    offline_syms = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [ts_to_ist(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError("offline replay: give --at HH:MM or --now \"YYYY-MM-DD HH:MM\"")
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols, label = read_symbols(a, offline_syms)
    confirm = None
    if not a.offline:
        today = to_ist(parse_now(None)).date()
        if (today - day).days > 6:
            raise ScanError("Yahoo's free 5-minute history is short: replay %s with --offline" % day.isoformat())
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s symbols from %s (%d spark requests)..."
                 % (_c(len(symbols)), label, (len(symbols) + SPARK_MAX - 1) // SPARK_MAX))
        data, failed = fetch_spark(symbols, rng, "%dm" % BAR_MIN, a.pause, a)
        done = []

        def confirm(sym):
            if a.pause:
                SLEEP(a.pause)
            done.append(sym)
            if len(done) % 50 == 0:
                progress(a, "  stage 2: %d v8 charts so far" % len(done))
            return parse_chart(yahoo_get(chart_path(sym, rng, "%dm" % BAR_MIN)), sym)
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_turnover,
                                     confirm, a.max_confirm)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s IST" % (day.isoformat(), cutoff.strftime("%H:%M"))
    doc = {"created_by": "india-scanner", "market": "NSE", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "mode": "orb", "as_of": as_of, "symbols_from": label, "universe": len(symbols),
           "scanned": counts["scanned"], "failed": nfailed, "unconfirmed": counts["unconfirmed"],
           "source": "yahoo", "or_minutes": a.or_minutes, "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_turnover_cr": a.min_turnover}, "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, failed_why=failed2, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s IST, %s: %d-minute range (9:15 to %s), 5-minute bars, Yahoo."
          % (day_label(day), clock_label(cutoff), label, a.or_minutes,
             clock_label(slot(day, a.or_minutes // BAR_MIN))))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % (_c(counts["fetched"]), _c(len(symbols)),
             (" (%s failed, had no data or missed a range bar)" % _c(nfailed)) if nfailed else "",
             _c(counts["scanned"]),
             (" (%s under the price / turnover floor)" % _c(counts["illiquid"])) if counts["illiquid"] else "",
             _c(counts["candidates"]), _c(counts["confirmed"])))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % (_c(counts["unconfirmed"]), _c(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % _c(counts["confirmed"])) if counts["unconfirmed"]
                 else _c(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %8s  %-12s %14s  range %s to %s  %+.2f%% over%s" % (
                h["at"], h["symbol"], money(h["price"]), _p(h["or_low"]), _p(h["or_high"]), h["pct_over"],
                ("  (after a missing %s bar)" % h["missing_bars_before"][-1]) if h.get("missing_bars_before") else ""))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /india-news SYMBOL and /india-entry SYMBOL.")
    return 0 if hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (india-scanner): NSE daily setups and opening-range "
                                             "breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="download NSE's equity list (or an index's members) -> "
                                        "trader-in/universe.txt")
    p.add_argument("--index", help="one index instead of every EQ stock: %s" % ", ".join(sorted(INDEXES)))
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--offline", metavar="PATH", help="a folder with EQUITY_L.csv / ind_<index>list.csv, or the file")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def common(p, min_price, min_turnover):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated NSE symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        g.add_argument("--index", help="an index's members, fetched now: %s" % ", ".join(sorted(INDEXES)))
        p.add_argument("--universe", help="symbol list, one SYMBOL|Name a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="last price floor in rupees")
        p.add_argument("--min-turnover", type=float, default=min_turnover,
                       help="rupees traded floor, in crore (price x volume)")
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-11-09 10:00" IST, or ...Z)')
        p.add_argument("--out", default=DEFAULT_WATCHLIST)
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=PAUSE, help="seconds between requests (%.1f)" % PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="setups from daily closes")
    common(p, 20.0, 5.0)
    p = sub.add_parser("orb", help="opening-range breakouts so far today")
    common(p, 20.0, 0.0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this India time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, 15:00)")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}[a.cmd](a)
    except ScanError as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
