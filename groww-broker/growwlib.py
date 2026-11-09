"""Shared helpers for the Groww skills (groww-budget, groww-risk, groww-broker).
Standard library only.

What lives here: India time and the NSE calendar, rupee and tick math, the
trader-in/ files, Groww's instrument list (real tick size, intraday allowed),
Groww's charges, the order fingerprint, the "is this an explicit yes" check,
and a small Groww Trade API client.

Credentials come only from environment variables, never from files Claude
reads or from the chat. One of:
  GROWW_ACCESS_TOKEN                     a token made on Groww's Trading APIs page (expires 6:00 AM)
  GROWW_API_KEY + GROWW_TOTP_SECRET      the TOTP flow (the tool makes the 6-digit code)
  GROWW_API_KEY + GROWW_API_SECRET       the key-and-secret flow (approve it daily on Groww Cloud)
A token made from a key is cached until it expires in
~/.cache/my-trader-skills/groww-token.json (file mode 600, outside the repo),
because Groww allows 150 token requests a day. It is never printed.
"""

import base64
import csv
import datetime
import hashlib
import hmac
import io
import json
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

BASE_URL = os.environ.get("GROWW_BASE_URL") or "https://api.groww.in"
INSTRUMENTS_URL = os.environ.get("GROWW_INSTRUMENTS_URL") or "https://growwapi-assets.groww.in/instruments/instrument.csv"
TOKEN_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "my-trader-skills", "groww-token.json")
UA = "my-trader-skills/1.0 (groww-broker; standard-library Python)"

DIR = "trader-in"
PATHS = {
    "config": DIR + "/config.json",
    "plan": DIR + "/plan.json",
    "orders": DIR + "/orders.json",
    "risk": DIR + "/state/risk.json",
    "broker": DIR + "/state/groww.json",
    "instruments": DIR + "/state/groww-instruments.json",
}

IST = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
GROWW_SQUAREOFF = datetime.time(15, 20)   # Groww squares off open MIS positions from 3:20 PM (Rs 50 + GST each)
GROWW_FNO_CUTOFF = datetime.time(15, 5)   # no new intraday orders after 3:05 PM in F&O stocks (Groww risk policy)
GROWW_CUTOFF = datetime.time(15, 20)      # ... and after 3:20 PM in the others

# NSE trading holidays, capital market segment (nseindia.com holiday master, read 2026-11-09).
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra", "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri", "2026-03-03": "Holi", "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti", "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram", "2026-08-15": "Independence Day", "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat trading session only)", "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev", "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)

DEFAULTS = {
    "capital_inr": 100000,
    "cash_buffer_pct": 10,
    "max_position_pct": 50,
    "max_order_inr": 50000,
    "min_order_inr": 500,
    "max_loss_per_trade_inr": 500,
    "max_daily_loss_inr": 1500,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:30",
    "no_entries_after": "15:00",
    "flat_by": "15:10",
    "exchange": "NSE",
    "product": "MIS",
    "entry_order": "LIMIT",
    "use_leverage": False,
    "live": False,
    "blocked": [],
}

# Groww equity brokerage: Rs 20 or 0.1% per executed order, whichever is lower, minimum Rs 5
# (groww.in/pricing/stocks, read 2026-11-09). Taxes and exchange charges are on top.
BROKERAGE_CAP = Decimal("20")
BROKERAGE_RATE = Decimal("0.001")
BROKERAGE_MIN = Decimal("5")

YES_WORDS = ("yes", "y", "haan", "ha")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if", "nahi"}
REF_RE = re.compile(r"^(?=.{8,20}$)[A-Za-z0-9]+(-[A-Za-z0-9]+){0,2}$")
ORDER_KEYS = ("trading_symbol", "quantity", "price", "trigger_price", "validity", "exchange", "segment", "product",
              "order_type", "transaction_type")


class GrowwError(Exception):
    pass


# ---------------------------------------------------------------- time

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_ist(utc):
    return utc.replace(tzinfo=None) + IST


def ist_to_utc(local):
    return local - IST


def parse_now(text):
    """--now -> naive UTC. "2026-11-09 10:00" (or just 10:00 for today) is India time; ...Z is UTC."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    try:
        dt = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise GrowwError('--now wants an India time like "2026-11-09 10:00"')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise GrowwError("%s wants an India time like 15:00, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


def trading_day(d):
    if d.weekday() >= 5:
        return False, "weekend"
    name = NSE_HOLIDAYS.get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def ist_stamp(now_utc):
    return to_ist(now_utc).strftime("%Y-%m-%d %H:%M:%S")


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


# ---------------------------------------------------------------- rupees and ticks

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        s = str(x).strip()
        for p in ("Rs.", "Rs", "₹"):
            if s.startswith(p):
                s = s[len(p):]
        return Decimal(s.strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        raise GrowwError("%s is not a number: %r" % (what, x))


PAISA = Decimal("0.01")


def q2(x):
    return D(x).quantize(PAISA, rounding=ROUND_HALF_UP)


def floor_tick(x, tick):
    return ((D(x) / D(tick)).to_integral_value(rounding=ROUND_FLOOR) * D(tick)).quantize(PAISA)


def ceil_tick(x, tick):
    return ((D(x) / D(tick)).to_integral_value(rounding=ROUND_CEILING) * D(tick)).quantize(PAISA)


def round_tick(x, tick):
    return ((D(x) / D(tick)).to_integral_value(rounding=ROUND_HALF_UP) * D(tick)).quantize(PAISA)


def on_tick(x, tick):
    return D(x) % D(tick) == 0


def money(x):
    v = q2(x)
    s = "{:,.2f}".format(v)
    return "Rs " + s


def pstr(x):
    """A price as Groww wants it in a smart order: a decimal string, '1029.20'."""
    return format(q2(x), "f")


def brokerage(value):
    """Groww's brokerage for one executed equity order of this rupee value."""
    b = min(BROKERAGE_CAP, D(value) * BROKERAGE_RATE)
    return q2(max(BROKERAGE_MIN, b))


# ---------------------------------------------------------------- files

def read_json(path, what="file", required=True):
    if not path or not os.path.exists(path):
        if required:
            raise GrowwError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise GrowwError("%s is not valid JSON: %s" % (path, e))


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def load_config(path=None):
    cfg = dict(DEFAULTS)
    doc = read_json(path or PATHS["config"], "config", required=False) or {}
    if not isinstance(doc, dict):
        raise GrowwError("%s must be a JSON object" % (path or PATHS["config"]))
    cfg.update({k: v for k, v in doc.items() if v is not None or k in ("no_entries_before", "no_entries_after")})
    cfg["_found"] = bool(doc)
    for k in ("flat_by",):
        parse_hhmm(cfg[k], k)
    if str(cfg.get("product", "MIS")).upper() != "MIS":
        raise GrowwError("product %r: the Groww skills plan intraday (MIS) trades only" % cfg.get("product"))
    cfg["product"] = "MIS"
    cfg["exchange"] = str(cfg.get("exchange") or "NSE").upper()
    if cfg["exchange"] not in ("NSE", "BSE"):
        raise GrowwError("exchange is NSE or BSE, not %r" % cfg["exchange"])
    cfg["blocked"] = [str(s).upper() for s in (cfg.get("blocked") or [])]
    return cfg


# ---------------------------------------------------------------- instruments

def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")


def build_instruments(lines):
    """Groww's instrument.csv (an iterable of text lines) -> {"NSE:SBIN": {...}}, F&O underlyings."""
    out, fno = {}, set()
    for r in csv.DictReader(lines):
        seg = (r.get("segment") or "").strip()
        exch = (r.get("exchange") or "").strip()
        if seg == "FNO":
            u = (r.get("underlying_symbol") or "").strip()
            if u:
                fno.add("%s:%s" % (exch, u))
            continue
        if seg != "CASH" or exch not in ("NSE", "BSE"):
            continue
        sym = (r.get("trading_symbol") or "").strip().upper()
        if not sym:
            continue
        tick = (r.get("tick_size") or "").strip()
        out["%s:%s" % (exch, sym)] = {
            "exchange": exch, "symbol": sym, "name": (r.get("name") or "").strip(),
            "type": (r.get("instrument_type") or "").strip(), "series": (r.get("series") or "").strip(),
            "isin": (r.get("isin") or "").strip(), "tick": tick if tick and tick.lower() != "nan" else None,
            "intraday": _truthy(r.get("is_intraday")), "buy": _truthy(r.get("buy_allowed")),
            "sell": _truthy(r.get("sell_allowed")), "groww_symbol": (r.get("groww_symbol") or "").strip()}
    for k, v in out.items():
        v["fno"] = k in fno
    return out


def load_instruments(path=None, refresh=False, offline=None, now_utc=None, quiet=False):
    """Groww's instrument list, cached once a day (it is 20 MB; the cache keeps the stock rows)."""
    path = path or PATHS["instruments"]
    today = to_ist(now_utc or utc_now()).date().isoformat()
    if offline:
        with open(offline, encoding="utf-8-sig", errors="replace") as fh:
            return build_instruments(fh)
    doc = None if refresh else read_json(path, "instruments", required=False)
    if doc and doc.get("date") == today and doc.get("instruments"):
        return doc["instruments"]
    req = urllib.request.Request(INSTRUMENTS_URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            insts = build_instruments(io.TextIOWrapper(resp, encoding="utf-8-sig", errors="replace"))
    except (urllib.error.URLError, OSError) as e:
        if doc and doc.get("instruments"):
            if not quiet:
                print("note: could not refresh Groww's instrument list (%s); using the copy from %s"
                      % (getattr(e, "reason", e), doc.get("date")))
            return doc["instruments"]
        raise GrowwError("could not download Groww's instrument list from %s: %s"
                         % (INSTRUMENTS_URL.split("/")[2], getattr(e, "reason", e)))
    if not insts:
        raise GrowwError("Groww's instrument list came back empty")
    write_json(path, {"date": today, "source": INSTRUMENTS_URL, "count": len(insts), "instruments": insts})
    return insts


def instrument(insts, exchange, symbol):
    return insts.get("%s:%s" % (exchange.upper(), symbol.upper()))


# ---------------------------------------------------------------- orders

def _strip(d, drop):
    return {k: v for k, v in sorted((d or {}).items()) if k not in drop and v is not None}


def fingerprint(item):
    """Identifies the exact proposal: the entry (or exit) order, its protection and what it cancels.
    The reference ids are left out (they are made from this fingerprint)."""
    core = {"kind": item.get("kind"),
            "order": _strip(item.get("order"), ("order_reference_id",)),
            "protect": _strip(item.get("protect"), ("reference_id",)),
            "cancel_smart": sorted(item.get("cancel_smart") or [])}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def ref_id(prefix, fp):
    """8 to 20 characters, letters and digits: Groww rejects a repeat (GA007), so the same
    proposal can never be placed twice."""
    return (prefix + fp)[:20]


def max_loss(item):
    """(rupees lost if the stop fills at the stop, entry used, stop) for an entry proposal."""
    o = item.get("order") or {}
    plan = item.get("plan") or {}
    p = item.get("protect") or {}
    stop = (p.get("stop_loss") or {}).get("trigger_price") or plan.get("stop")
    qty = o.get("quantity")
    if o.get("order_type") == "LIMIT" and o.get("price"):
        entry = o["price"]
    else:
        entry = plan.get("entry_for_risk") or plan.get("entry")
    if stop is None or qty is None or entry is None:
        return None, entry, stop
    return q2(int(qty) * (D(entry) - D(stop))), D(entry), D(stop)


def plain(item):
    """One line a person can say yes to."""
    o = item.get("order") or {}
    side = o.get("transaction_type", "?")
    how = ("limit %s" % pstr(o["price"])) if o.get("order_type") == "LIMIT" else "market"
    parts = ["%s %s %s on %s, %s, %s (intraday), good for today" % (
        side, o.get("quantity"), o.get("trading_symbol"), o.get("exchange"), how, o.get("product"))]
    p = item.get("protect")
    if p:
        parts.append("stop-loss %s" % p["stop_loss"]["trigger_price"])
        parts.append("target %s" % p["target"]["trigger_price"])
    if item.get("kind") == "entry":
        loss, _, stop = max_loss(item)
        if loss is not None:
            parts.append("max loss about %s if the stop fills at %s" % (money(loss), pstr(stop)))
    if item.get("cancel_smart"):
        parts.append("first cancels the stop/target smart order %s" % ", ".join(item["cancel_smart"]))
    return " / ".join(parts)


def amount_numbers(item):
    o = item.get("order") or {}
    nums = [D(o[k]) for k in ("quantity", "price") if o.get(k) not in (None, 0, "0")]
    p = item.get("protect") or {}
    for leg in ("stop_loss", "target"):
        if (p.get(leg) or {}).get("trigger_price"):
            nums.append(D(p[leg]["trigger_price"]))
    loss, _, _ = max_loss(item)
    if loss is not None:
        nums.append(loss)
    return nums


REPLY_WORDS = {"YES", "Y", "HAAN", "HA", "BUY", "SELL", "PLACE", "ORDER", "IT", "OK", "GO", "AHEAD", "PLEASE",
               "DO", "THE", "THIS", "ONE", "NOW", "SHARES", "SHARE", "AT", "LIMIT", "MARKET", "NSE", "BSE", "MIS",
               "INTRADAY", "CLOSE", "EXIT", "SEND", "CONFIRM", "CONFIRMED", "RS", "INR", "AND", "WITH", "STOP", "TARGET"}


def parse_yes(reply, item, batch_symbols=(), known_symbols=()):
    """Is this reply an explicit yes for THIS one proposal? (ok, why). A reply that names any
    other stock (from the batch, or any symbol in Groww's list) is not a yes for this one."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it"
    sym = str((item.get("order") or {}).get("trading_symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z][A-Z0-9&\-]{1,15}", raw.upper()))
    others = ({s.upper() for s in batch_symbols} | {s.upper() for s in known_symbols}) - {sym} - REPLY_WORDS
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [D(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(item)
        if not any(abs(n - w) < Decimal("0.005") for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (", ".join(str(n) for n in nums), plain(item))
    return True, "explicit yes for %s" % plain(item)


# ---------------------------------------------------------------- state (the broker log)

def load_state(path=None):
    return read_json(path or PATHS["broker"], "broker log", required=False) or {"records": []}


def records_today(state, today, mode=None):
    return [r for r in state.get("records", []) if r.get("date") == today and (mode is None or r.get("mode") == mode)]


def latest(state, fp, today):
    found = None
    for r in state.get("records", []):
        if r.get("fingerprint") == fp and r.get("date") == today:
            found = r
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "groww-risk has not checked this order (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


# ---------------------------------------------------------------- TOTP (RFC 6238, for GROWW_TOTP_SECRET)

def totp(secret_b32, for_time=None, digits=6, step=30):
    s = re.sub(r"\s+", "", secret_b32 or "").upper()
    s += "=" * (-len(s) % 8)
    try:
        key = base64.b32decode(s)
    except (ValueError, TypeError):
        raise GrowwError("GROWW_TOTP_SECRET is not a base32 TOTP secret")
    counter = int((time.time() if for_time is None else for_time) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ---------------------------------------------------------------- the Groww Trade API

def credential_flow():
    """Which login the environment allows (names only, never values)."""
    if os.environ.get("GROWW_ACCESS_TOKEN"):
        return "access token (GROWW_ACCESS_TOKEN)"
    if os.environ.get("GROWW_API_KEY") and os.environ.get("GROWW_TOTP_SECRET"):
        return "API key + TOTP (GROWW_API_KEY, GROWW_TOTP_SECRET)"
    if os.environ.get("GROWW_API_KEY") and os.environ.get("GROWW_API_SECRET"):
        return "API key + secret (GROWW_API_KEY, GROWW_API_SECRET)"
    return None


class Groww(object):
    """A small client for https://api.groww.in (docs: groww.in/trade-api/docs/curl)."""

    def __init__(self, base=None, cache=TOKEN_CACHE, use_cache=True, pause=0.12):
        self.base = (base or BASE_URL).rstrip("/")
        self.cache = cache
        self.use_cache = use_cache
        self.pause = pause
        self._token = None
        self._last = 0.0

    # -- login
    def _cached(self):
        if not self.use_cache or not os.path.exists(self.cache):
            return None
        try:
            with open(self.cache, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return None
        if doc.get("key_hash") != self._key_hash() or doc.get("base") != self.base:
            return None
        exp = doc.get("expires_at")
        if not exp or datetime.datetime.fromisoformat(exp) <= utc_now() + datetime.timedelta(minutes=5):
            return None
        return doc.get("token")

    def _key_hash(self):
        return hashlib.sha256((os.environ.get("GROWW_API_KEY") or "").encode("utf-8")).hexdigest()[:12]

    def _save(self, token, expiry_text):
        if not self.use_cache:
            return
        try:
            exp = datetime.datetime.fromisoformat(str(expiry_text).replace("Z", ""))
            exp = ist_to_utc(exp)          # Groww's expiry is India time
        except (TypeError, ValueError):
            now = to_ist(utc_now())        # tokens end at 6:00 AM India time
            nxt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1 if now.time() >= datetime.time(6) else 0),
                                            datetime.time(6))
            exp = ist_to_utc(nxt)
        os.makedirs(os.path.dirname(self.cache), exist_ok=True)
        fd = os.open(self.cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"token": token, "expires_at": exp.isoformat(), "key_hash": self._key_hash(),
                       "base": self.base}, fh)

    def token(self):
        if self._token:
            return self._token
        tok = os.environ.get("GROWW_ACCESS_TOKEN")
        if tok:
            self._token = tok.strip()
            return self._token
        key = os.environ.get("GROWW_API_KEY")
        if not key:
            raise GrowwError("no Groww login: set GROWW_ACCESS_TOKEN, or GROWW_API_KEY with GROWW_TOTP_SECRET "
                             "or GROWW_API_SECRET, in your terminal before starting Claude (never in the chat)")
        cached = self._cached()
        if cached:
            self._token = cached
            return cached
        if os.environ.get("GROWW_TOTP_SECRET"):
            body = {"key_type": "totp", "totp": totp(os.environ["GROWW_TOTP_SECRET"])}
        elif os.environ.get("GROWW_API_SECRET"):
            ts = str(int(time.time()))
            chk = hashlib.sha256((os.environ["GROWW_API_SECRET"] + ts).encode("utf-8")).hexdigest()
            body = {"key_type": "approval", "checksum": chk, "timestamp": ts}
        else:
            raise GrowwError("GROWW_API_KEY is set but neither GROWW_TOTP_SECRET nor GROWW_API_SECRET is")
        doc = self._http("POST", "/v1/token/api/access", body=body, auth="Bearer " + key.strip(), versioned=False)
        tok = doc.get("token") or (doc.get("payload") or {}).get("token")
        if not tok:
            raise GrowwError("Groww did not return a token (approve the key on Groww Cloud's API keys page today)")
        self._save(tok, doc.get("expiry") or (doc.get("payload") or {}).get("expiry"))
        self._token = tok
        return tok

    # -- requests
    def _http(self, method, path, params=None, body=None, auth=None, versioned=True):
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params, safe=",")
        headers = {"Accept": "application/json", "User-Agent": UA, "Authorization": auth or ("Bearer " + self.token())}
        if versioned:
            headers["X-API-VERSION"] = "1.0"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in (0, 1):
            self._last = time.time()
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    text = resp.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if e.code == 429 and attempt == 0:
                    time.sleep(1.5)
                    continue
                try:
                    err = (json.loads(text).get("error") or {})
                except ValueError:
                    err = {}
                if e.code == 401:
                    raise GrowwError("Groww answered 401: the login is missing or expired (tokens end at 6:00 AM; "
                                     "a key needs today's approval on Groww Cloud)")
                raise GrowwError("Groww answered HTTP %d%s for %s %s" % (
                    e.code, (": %s %s" % (err.get("code", ""), err.get("message", ""))).rstrip() if err else "",
                    method, path))
            except (urllib.error.URLError, OSError) as e:
                raise GrowwError("could not reach %s: %s" % (self.base.split("/")[2], getattr(e, "reason", e)))
        try:
            doc = json.loads(text)
        except ValueError:
            raise GrowwError("Groww sent something that is not JSON for %s %s" % (method, path))
        if isinstance(doc, dict) and doc.get("status") == "FAILURE":
            err = doc.get("error") or {}
            raise GrowwError("Groww refused %s %s: %s %s" % (method, path, err.get("code", ""), err.get("message", "")))
        return doc

    def call(self, method, path, params=None, body=None):
        doc = self._http(method, path, params, body)
        return doc.get("payload", doc) if isinstance(doc, dict) else doc

    # read only
    def user(self):
        return self.call("GET", "/v1/user/detail")

    def margin(self):
        return self.call("GET", "/v1/margins/detail/user")

    def required_margin(self, orders, segment="CASH"):
        return self.call("POST", "/v1/margins/detail/orders?segment=" + segment, body=orders)

    def positions(self, segment="CASH"):
        return (self.call("GET", "/v1/positions/user", {"segment": segment}) or {}).get("positions", [])

    def orders(self, segment="CASH"):
        return (self.call("GET", "/v1/order/list", {"segment": segment, "page": 0, "page_size": 100}) or {}).get(
            "order_list", [])

    def order_status(self, groww_order_id, segment="CASH"):
        return self.call("GET", "/v1/order/status/%s" % urllib.parse.quote(groww_order_id, safe=""),
                         {"segment": segment})

    def order_detail(self, groww_order_id, segment="CASH"):
        return self.call("GET", "/v1/order/detail/%s" % urllib.parse.quote(groww_order_id, safe=""),
                         {"segment": segment})

    def smart_orders(self, segment="CASH", smart_order_type="OCO", status="ACTIVE"):
        doc = self.call("GET", "/v1/order-advance/list",
                        {"segment": segment, "smart_order_type": smart_order_type, "status": status,
                         "page": 0, "page_size": 50})
        if isinstance(doc, dict):
            return doc.get("orders") or doc.get("smart_orders") or doc.get("order_list") or []
        return doc or []

    def ltp(self, exchange_symbols, segment="CASH"):
        return self.call("GET", "/v1/live-data/ltp", {"segment": segment, "exchange_symbols": ",".join(exchange_symbols)})

    # these change something at the broker
    def place(self, body):
        return self.call("POST", "/v1/order/create", body=body)

    def cancel(self, groww_order_id, segment="CASH"):
        return self.call("POST", "/v1/order/cancel", body={"segment": segment, "groww_order_id": groww_order_id})

    def smart_create(self, body):
        return self.call("POST", "/v1/order-advance/create", body=body)

    def smart_cancel(self, smart_order_id, segment="CASH", smart_order_type="OCO"):
        return self.call("POST", "/v1/order-advance/cancel/%s/%s/%s" % (
            segment, smart_order_type, urllib.parse.quote(smart_order_id, safe="")))


YAHOO_UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
FRESH_MIN = 15   # a price older than this many minutes is not used


def yahoo_price(symbol, exchange, now_utc):
    """(price, age in minutes) from Yahoo's chart meta, or (None, why)."""
    ysym = symbol + (".BO" if exchange == "BSE" else ".NS")
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
           % urllib.parse.quote(ysym, safe=""))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": YAHOO_UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            meta = json.load(r)["chart"]["result"][0]["meta"]
    except Exception as e:  # noqa: BLE001 - any failure just means no fresh price
        return None, "Yahoo: %s" % e
    t, p = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if not p or not t:
        return None, "Yahoo sent no price"
    age = (now_utc - (EPOCH + datetime.timedelta(seconds=int(t)))).total_seconds() / 60.0
    return D(p), age


def fresh_price(symbol, exchange, now_utc, given=None, lookup=True):
    """(price or None, where it came from): the price you gave, else Groww's live price
    (with a login), else Yahoo's if it is under FRESH_MIN minutes old."""
    if given is not None:
        return D(given), "the price you gave"
    if not lookup:
        return None, "no fresh price (lookup off)"
    note = "no Groww login"
    if credential_flow():
        key = "%s_%s" % (exchange, symbol)
        try:
            v = (Groww().ltp([key]) or {}).get(key)
            if v:
                return D(v), "Groww's live price"
            note = "Groww sent no price"
        except GrowwError as e:
            note = str(e)
    p, age = yahoo_price(symbol, exchange, now_utc)
    if p is None:
        return None, "no fresh price (%s; %s)" % (note, age)
    if age > FRESH_MIN:
        return None, "no fresh price (Yahoo's last trade is %d minutes old)" % int(age)
    if age < -1:
        return None, "no fresh price (Yahoo's price is from after the pinned clock)"
    return p, "Yahoo's price, %d min old" % max(0, int(age))


def request_preview(method, path, body=None):
    """What would be sent, with the login hidden."""
    lines = ["%s %s%s" % (method, BASE_URL, path),
             "  Authorization: Bearer <your Groww token>  X-API-VERSION: 1.0"]
    if body is not None:
        lines.append("  " + json.dumps(body, separators=(", ", ": ")))
    return lines
