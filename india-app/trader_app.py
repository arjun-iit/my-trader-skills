#!/usr/bin/env python3
"""India Trader: a desktop app for the India (NSE) and Groww skills. No browser.

    python3 india-app/trader_app.py          # Windows: double-click run_app.bat; macOS: run_app.command

One window, one tab per skill: Scanner, News, Entry, Exit, Time Machine, Alerts,
Results, Insider, Groww and Settings. Each tab runs the same script the Claude
skill runs (india-*/... and groww-*/...), so the app and Claude share the files
in trader-in/. Output streams into the tab; Stop ends a long run.

Groww keeps its gates: groww-risk must PASS the exact order today, you type
your own yes for that one ticket, and the app asks you to confirm before
place / protect / cancel. DRY-RUN unless you switch LIVE on in Settings.

Standard library only (Tkinter ships with Python from python.org on Windows and
macOS; on Linux install python3-tk). Python 3.8 or newer.
"""

import json
import os
import queue
import runpy
import subprocess
import sys
import threading
import time

APP = "India Trader"
HERE = os.path.dirname(os.path.realpath(__file__))
FROZEN = getattr(sys, "frozen", False)
ROOT = getattr(sys, "_MEIPASS", None) or os.path.dirname(HERE)   # where the skill folders are
CONFIG_KEYS = [
    ("capital_inr", "Capital (Rs)", "num"), ("cash_buffer_pct", "Cash buffer %", "num"),
    ("max_loss_per_trade_inr", "Max loss per trade (Rs)", "num"), ("max_daily_loss_inr", "Max daily loss (Rs)", "num"),
    ("max_trades_per_day", "Max trades per day", "int"), ("max_position_pct", "Max position % of capital", "num"),
    ("max_order_inr", "Max order (Rs)", "num"), ("min_order_inr", "Min order (Rs)", "num"),
    ("min_reward_risk", "Min reward:risk", "num"), ("no_entries_before", "No entries before (HH:MM)", "str"),
    ("no_entries_after", "No entries after (HH:MM)", "str"), ("flat_by", "Flat by (HH:MM)", "str"),
    ("entry_order", "Entry order (LIMIT / MARKET)", "str"), ("blocked", "Blocked symbols (comma separated)", "list"),
]
DEFAULTS = {"capital_inr": 100000, "cash_buffer_pct": 10, "max_position_pct": 50, "max_order_inr": 50000,
            "min_order_inr": 500, "max_loss_per_trade_inr": 500, "max_daily_loss_inr": 1500, "max_trades_per_day": 3,
            "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True, "no_entries_before": "09:30",
            "no_entries_after": "15:00", "flat_by": "15:10", "exchange": "NSE", "product": "MIS",
            "entry_order": "LIMIT", "use_leverage": False, "live": False, "blocked": []}
INDEXES = ["nifty50", "niftynext50", "nifty100", "nifty200", "nifty500", "niftymidcap100", "niftysmallcap100",
           "niftybank"]


def script(rel):
    return os.path.join(ROOT, rel)


def run_script_mode(argv):
    """`trader_app --run-skill india-alerts/alerts.py ARGS...`: run one skill script in this process.
    This is how the app runs scripts both from source and as a single packaged program (where
    sys.executable is the app itself, not Python); the script is found in this process's own copy
    of the skill folders."""
    rel = argv[0]
    if ".." in rel.replace("\\", "/").split("/") or os.path.isabs(rel) or not rel.endswith(".py"):
        sys.exit("--run-skill takes a skill script like india-alerts/alerts.py")
    path = script(rel)
    if not os.path.exists(path):
        sys.exit("no such skill script: %s" % rel)
    sys.argv = [path] + argv[1:]
    sys.path.insert(0, os.path.dirname(path))
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        sys.exit(code)
    sys.exit(0)


if len(sys.argv) > 1 and sys.argv[1] == "--run-skill":
    run_script_mode(sys.argv[2:])

import tkinter as tk  # noqa: E402  (after the --run-script dispatch, which needs no GUI)
from tkinter import messagebox, scrolledtext, ttk  # noqa: E402


class Runner(object):
    """Runs one script at a time in a background thread and streams its output."""

    def __init__(self, app):
        self.app = app
        self.proc = None
        self.q = queue.Queue()

    def busy(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, out, rel, args, on_done=None):
        if self.busy():
            messagebox.showinfo(APP, "Another command is still running. Wait for it, or press Stop.")
            return False
        cmd = [sys.executable] + ([] if FROZEN else [os.path.realpath(__file__)]) + ["--run-skill", rel] + args
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.update(self.app.session_env())
        shown = " ".join([os.path.join(*rel.split("/"))] + [a if " " not in a else '"%s"' % a for a in args])
        out.write("\n$ python3 %s\n" % shown, "cmd")
        try:
            self.proc = subprocess.Popen(cmd, cwd=self.app.workdir, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            out.write("could not start: %s\n" % e, "err")
            return False
        self.app.set_status("Running: %s" % shown)
        proc = self.proc

        def pump():
            for raw in iter(proc.stdout.readline, b""):
                self.q.put((out, raw.decode("utf-8", "replace"), None))
            proc.wait()
            self.q.put((out, None, (proc.returncode, on_done)))
        threading.Thread(target=pump, daemon=True).start()
        return True

    def stop(self):
        if self.busy():
            self.proc.terminate()
            self.app.set_status("Stopped.")

    def poll(self):
        try:
            while True:
                out, line, done = self.q.get_nowait()
                if line is not None:
                    tag = "alert" if line.startswith(("ALERT", "TRIGGERED", "NEW ")) else \
                        "err" if any(w in line for w in ("BLOCK", "WARNING", "error", "Error", "Not recorded")) else None
                    out.write(line, tag)
                else:
                    code, cb = done
                    out.write("[exit %s]\n" % code, "dim")
                    self.app.set_status("Ready. Last command exit %s." % code)
                    if cb:
                        cb(code)
        except queue.Empty:
            pass
        self.app.root.after(80, self.poll)


class Output(scrolledtext.ScrolledText):
    def __init__(self, master, **kw):
        super().__init__(master, wrap="none", height=18, font=("Courier", 10), **kw)
        xbar = ttk.Scrollbar(self.frame, orient="horizontal", command=self.xview)
        self.configure(xscrollcommand=xbar.set)
        xbar.pack(side="bottom", fill="x", before=self.vbar) if hasattr(self, "vbar") else xbar.pack(side="bottom", fill="x")
        self.tag_configure("cmd", foreground="#1a5fb4")
        self.tag_configure("err", foreground="#c01c28")
        self.tag_configure("alert", foreground="#a05a00", font=("Courier", 10, "bold"))
        self.tag_configure("dim", foreground="#777777")
        self.configure(state="disabled")

    def write(self, text, tag=None):
        self.configure(state="normal")
        self.insert("end", text, tag)
        self.see("end")
        self.configure(state="disabled")

    def clear(self):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class App(object):
    def __init__(self, root):
        self.root = root
        self.workdir = os.environ.get("INDIA_TRADER_DATA") or (os.path.dirname(HERE) if not FROZEN else
                                                               os.path.join(os.path.expanduser("~"), "IndiaTrader"))
        os.makedirs(self.workdir, exist_ok=True)
        self.runner = Runner(self)
        self.token = tk.StringVar()
        self.replay = tk.StringVar()
        root.title(APP)
        root.geometry("1120x760")
        root.minsize(900, 600)
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        top = ttk.Frame(root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text=APP, font=("TkDefaultFont", 13, "bold")).pack(side="left")
        ttk.Label(top, text="   Replay time (IST, optional):").pack(side="left")
        ttk.Entry(top, textvariable=self.replay, width=18).pack(side="left")
        ttk.Label(top, text=' e.g. 2026-11-09 10:00 to practise on a past session', foreground="#666").pack(side="left")
        ttk.Button(top, text="Stop", command=self.runner.stop).pack(side="right")
        self.mode_label = ttk.Label(top, text="", font=("TkDefaultFont", 10, "bold"))
        self.mode_label.pack(side="right", padx=10)
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        self.status = tk.StringVar(value="Ready. Data folder: %s" % self.workdir)
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w", padding=(6, 2)).pack(fill="x")
        for build in (self.tab_scanner, self.tab_news, self.tab_entry, self.tab_exit, self.tab_timemachine,
                      self.tab_alerts, self.tab_results, self.tab_insider, self.tab_groww, self.tab_settings):
            build()
        self.refresh_mode()
        self.auto_job = None
        root.after(80, self.runner.poll)

    # ------------------------------------------------------------ helpers
    def set_status(self, text):
        self.status.set(text)

    def session_env(self):
        tok = self.token.get().strip()
        return {"GROWW_ACCESS_TOKEN": tok} if tok else {}

    def now_args(self):
        v = self.replay.get().strip()
        return ["--now", v] if v else []

    def tab(self, title, note):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text=title)
        ttk.Label(f, text=note, foreground="#555", wraplength=1040, justify="left").pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(f)
        form.pack(fill="x")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=6)
        out = Output(f)
        out.pack(fill="both", expand=True)
        ttk.Button(bar, text="Clear output", command=out.clear).pack(side="right")
        return form, bar, out

    def field(self, form, row, label, var, width=16, col=0, values=None):
        ttk.Label(form, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=2)
        if values:
            w = ttk.Combobox(form, textvariable=var, values=values, width=width - 2, state="readonly")
        else:
            w = ttk.Entry(form, textvariable=var, width=width)
        w.grid(row=row, column=col + 1, sticky="w", padx=(0, 18), pady=2)
        return w

    def button(self, bar, text, fn):
        ttk.Button(bar, text=text, command=fn).pack(side="left", padx=(0, 6))

    def run(self, out, rel, args, on_done=None):
        self.runner.start(out, rel, args, on_done)

    @staticmethod
    def split(text):
        return [s for s in text.replace(",", " ").split() if s]

    def read_config(self):
        path = os.path.join(self.workdir, "trader-in", "config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                return dict(DEFAULTS, **json.load(fh)), True
        except (OSError, ValueError):
            return dict(DEFAULTS), False

    def write_config(self, cfg):
        d = os.path.join(self.workdir, "trader-in")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")

    def refresh_mode(self):
        cfg, _ = self.read_config()
        if cfg.get("live"):
            self.mode_label.configure(text="GROWW: LIVE", foreground="#c01c28")
        else:
            self.mode_label.configure(text="GROWW: DRY-RUN", foreground="#26a269")

    # ------------------------------------------------------------ tabs
    def tab_scanner(self):
        form, bar, out = self.tab("Scanner", "Scan NSE charts for setups. Opening-range breakouts need 9:35 AM IST "
                                             "or later (or a replay time). Daily setups use closes, best before 9:15 "
                                             "or after 15:30. A scan is a list of charts that match a rule, not a list "
                                             "of stocks to buy.")
        src, idx, syms, at = tk.StringVar(value="Index"), tk.StringVar(value="nifty50"), tk.StringVar(), tk.StringVar()
        self.field(form, 0, "Scan", src, values=["Index", "My symbols", "Whole universe"])
        self.field(form, 0, "Index", idx, col=2, values=INDEXES)
        self.field(form, 1, "Symbols (comma separated)", syms, width=40)
        self.field(form, 1, "Cut at (HH:MM, orb)", at, col=2, width=8)

        def source():
            if src.get() == "Index":
                return ["--index", idx.get()]
            if src.get() == "My symbols":
                if not syms.get().strip():
                    messagebox.showinfo(APP, "Type one or more NSE symbols, e.g. SBIN, RELIANCE, M&M.")
                    return None
                return ["--symbols", ",".join(self.split(syms.get()))]
            return []

        def go(mode):
            s = source()
            if s is None:
                return
            extra = (["--at", at.get().strip()] if mode == "orb" and at.get().strip() else [])
            self.run(out, "india-scanner/scan.py", [mode] + s + extra + self.now_args())
        self.button(bar, "Opening-range breakouts", lambda: go("orb"))
        self.button(bar, "Daily setups", lambda: go("daily"))
        self.button(bar, "Build universe (all NSE EQ)", lambda: self.run(out, "india-scanner/scan.py", ["universe"]))

    def tab_news(self):
        form, bar, out = self.tab("News", "NSE filings, upcoming results meetings, Google News headlines and the "
                                          "move vs the previous close. Leave symbols empty to use the watchlist, plan "
                                          "and portfolio. Headlines are leads, not facts.")
        syms, hours, filings_only = tk.StringVar(), tk.StringVar(value="48"), tk.BooleanVar()
        self.field(form, 0, "Symbols", syms, width=40)
        self.field(form, 0, "Hours back", hours, col=2, width=6)
        ttk.Checkbutton(form, text="NSE filings only (no headlines)", variable=filings_only).grid(row=0, column=4)

        def go(show_all=False):
            a = (["--tickers"] + self.split(syms.get()) if syms.get().strip() else []) + ["--hours", hours.get() or "48"]
            if filings_only.get():
                a.append("--no-headlines")
            if show_all:
                a.append("--all")
            self.run(out, "india-news/news.py", a)
        self.button(bar, "Check news", go)
        self.button(bar, "Show all (seen too)", lambda: go(True))

    def tab_entry(self):
        form, bar, out = self.tab("Entry", "Watches a setup bar by bar and says the moment to get in: the first "
                                           "completed bar that CLOSES above the opening-range high (9:15-9:30), or "
                                           "above your level. Long side only. It never places an order.")
        sym, setup, level, interval = tk.StringVar(), tk.StringVar(value="orb"), tk.StringVar(), tk.StringVar(value="5m")
        self.field(form, 0, "Symbol", sym)
        self.field(form, 0, "Setup", setup, col=2, values=["orb", "level"], width=8)
        self.field(form, 1, "Level (setup level)", level)
        self.field(form, 1, "Bars", interval, col=2, values=["5m", "1m"], width=6)

        def args(cmd):
            if cmd != "watchlist" and not sym.get().strip():
                messagebox.showinfo(APP, "Type an NSE symbol first.")
                return None
            a = [cmd] + ([sym.get().strip().upper()] if cmd != "watchlist" else [])
            a += ["--setup", setup.get(), "--interval", interval.get()]
            if setup.get() == "level":
                if not level.get().strip():
                    messagebox.showinfo(APP, "A level setup needs a level price.")
                    return None
                a += ["--level", level.get().strip()]
            return a + self.now_args()

        def go(cmd, write=False):
            a = args(cmd)
            if a is not None:
                self.run(out, "india-entry/entry.py", a + (["--write"] if write else []))
        self.button(bar, "Check", lambda: go("check"))
        self.button(bar, "Check and save entry", lambda: go("check", True))
        self.button(bar, "Watch until it triggers", lambda: go("watch"))
        self.button(bar, "Check the watchlist", lambda: go("watchlist"))

    def tab_exit(self):
        form, bar, out = self.tab("Exit", "Sets the stop and target BEFORE you buy, from the saved entry "
                                          "(trader-in/entry.json) or the prices you type. Prices go on NSE's tick; "
                                          "flat by 15:10 IST.")
        stop, target = tk.StringVar(value="or-mid"), tk.StringVar(value="2R")
        sym, entry, oh, ol = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.field(form, 0, "Stop", stop, values=["or-mid", "or-low", "pct:1", "atr:1.5"], width=12)
        self.field(form, 0, "Target", target, col=2, values=["1R", "1.5R", "2R", "3R"], width=8)
        self.field(form, 1, "Or plan by hand: symbol", sym)
        self.field(form, 1, "Entry", entry, col=2, width=10)
        self.field(form, 2, "Opening-range high", oh, width=10)
        self.field(form, 2, "Opening-range low", ol, col=2, width=10)

        def go(write):
            a = ["plan", "--stop", stop.get(), "--target", target.get()]
            if sym.get().strip():
                if not entry.get().strip():
                    messagebox.showinfo(APP, "A hand-made plan needs the entry price.")
                    return
                a += ["--symbol", sym.get().strip().upper(), "--entry", entry.get().strip()]
                if oh.get().strip() and ol.get().strip():
                    a += ["--or-high", oh.get().strip(), "--or-low", ol.get().strip()]
                elif stop.get().startswith("or-"):
                    a += ["--setup", "level"]
                    messagebox.showinfo(APP, "Without the opening range, pick a pct: or atr: stop.")
                    return
            self.run(out, "india-exit/exit.py", a + self.now_args() + (["--write"] if write else []))
        self.button(bar, "Plan", lambda: go(False))
        self.button(bar, "Plan and save", lambda: go(True))
        self.button(bar, "Show saved plan", lambda: self.run(out, "india-exit/exit.py", ["show"] + self.now_args()))

    def tab_timemachine(self):
        form, bar, out = self.tab("Time Machine", "Replays the saved plan 10,000 times from the stock's own last "
                                                  "60 days of 5-minute bars, plus the same rule on every past "
                                                  "session. A simulation, not a prediction.")
        until, seed = tk.StringVar(), tk.StringVar(value="19")
        self.field(form, 0, "Until (HH:MM, default flat-by)", until, width=8)
        self.field(form, 0, "Seed", seed, col=2, width=6)

        def go(chart=False):
            a = ["chart"] if chart else ["run", "--write", "--seed", seed.get() or "19"]
            if until.get().strip():
                a += ["--until", until.get().strip()]
            self.run(out, "india-timemachine/timemachine.py", a + self.now_args())
        self.button(bar, "Run the time machine", go)
        self.button(bar, "Fan chart", lambda: go(True))

    def tab_alerts(self):
        form, bar, out = self.tab("Alerts", "Rings when a price, a stop or the clock hits. An alert is information, "
                                            "never an order. Auto-check runs the check on a timer while the app is "
                                            "open (keep the computer awake).")
        sym, kind, price, every = tk.StringVar(), tk.StringVar(value="stop"), tk.StringVar(), tk.StringVar(value="5")
        auto = tk.BooleanVar()
        self.field(form, 0, "Symbol", sym)
        self.field(form, 0, "Alert type", kind, col=2, values=["stop", "target", "above", "below", "pct"], width=8)
        self.field(form, 0, "Price / percent", price, col=4, width=10)
        self.field(form, 1, "Auto-check every (min)", every, width=5)
        ttk.Checkbutton(form, text="Auto-check", variable=auto,
                        command=lambda: self.toggle_auto(auto, every, out)).grid(row=1, column=2, sticky="w")
        g = self.now_args
        self.button(bar, "Market clock", lambda: self.run(out, "india-alerts/alerts.py", g() + ["clock"]))
        self.button(bar, "Add stop/target/flat-by from plan",
                    lambda: self.run(out, "india-alerts/alerts.py", g() + ["add", "--from-plan"]))

        def add():
            if not sym.get().strip() or not price.get().strip():
                messagebox.showinfo(APP, "Type a symbol and a price (or a percent for pct).")
                return
            self.run(out, "india-alerts/alerts.py",
                     g() + ["add", "--symbol", sym.get().strip().upper(), "--" + kind.get(), price.get().strip()])
        self.button(bar, "Add alert", add)
        self.button(bar, "Check now", lambda: self.run(out, "india-alerts/alerts.py", g() + ["check"]))
        self.button(bar, "List", lambda: self.run(out, "india-alerts/alerts.py", ["list"]))
        self.button(bar, "Quote", lambda: self.run(out, "india-alerts/alerts.py",
                                                   ["quote"] + (self.split(sym.get()) or ["NIFTY"])))

    def toggle_auto(self, auto, every, out):
        if self.auto_job:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None
        if not auto.get():
            self.set_status("Auto-check off.")
            return
        try:
            mins = max(1, int(float(every.get())))
        except ValueError:
            mins = 5

        def tick():
            if not self.runner.busy():
                self.run(out, "india-alerts/alerts.py", ["check"], on_done=self.ring)
            self.auto_job = self.root.after(mins * 60000, tick)
        self.set_status("Auto-check every %d min." % mins)
        tick()

    def ring(self, code):
        if code == 0:
            self.root.bell()
            self.root.deiconify()
            self.root.lift()

    def tab_results(self):
        form, bar, out = self.tab("Results", "Quarterly results from the company's NSE filings: revenue, profit, EPS, "
                                             "margins, growth against the same quarter a year earlier. Filed numbers, "
                                             "not advice.")
        syms, qs, standalone = tk.StringVar(), tk.StringVar(value="5"), tk.BooleanVar()
        self.field(form, 0, "Symbols", syms, width=40)
        self.field(form, 0, "Quarters", qs, col=2, width=4)
        ttk.Checkbutton(form, text="Standalone", variable=standalone).grid(row=0, column=4)

        def go():
            if not syms.get().strip():
                messagebox.showinfo(APP, "Type one or more NSE symbols.")
                return
            self.run(out, "india-analyst/results.py", self.split(syms.get()) + ["--quarters", qs.get() or "5"] +
                     (["--standalone"] if standalone.get() else []))
        self.button(bar, "Show results", go)

    def tab_insider(self):
        form, bar, out = self.tab("Insider", "Insider-trading disclosures (promoters, directors, key managers) and "
                                             "NSE bulk and block deals. Public filings, made after the trade.")
        syms, days = tk.StringVar(), tk.StringVar(value="180")
        self.field(form, 0, "Symbols", syms, width=40)
        self.field(form, 0, "Days back", days, col=2, width=6)

        def trades():
            if not syms.get().strip():
                messagebox.showinfo(APP, "Type one or more NSE symbols.")
                return
            self.run(out, "india-insider/insider.py", ["trades"] + self.split(syms.get()) + ["--days", days.get() or "180"])
        self.button(bar, "Insider trades", trades)
        self.button(bar, "New for my symbols", lambda: self.run(out, "india-insider/insider.py", ["new"]))
        self.button(bar, "Bulk and block deals (latest)", lambda: self.run(out, "india-insider/insider.py", ["deals"]))
        self.button(bar, "Deals, last 7 days", lambda: self.run(out, "india-insider/insider.py",
                                                                ["deals", "--days", "7"]))

    def tab_groww(self):
        form, bar, out = self.tab("Groww", "Size the saved plan, check it against your limits, then place it at "
                                           "Groww with a stop-loss and target (an OCO smart order). Three gates: a "
                                           "PASS from the risk check today, your own typed yes for that one ticket, "
                                           "and your confirmation in a dialog. DRY-RUN sends nothing.")
        ticket, reply, close_sym, market = tk.StringVar(value="1"), tk.StringVar(), tk.StringVar(), tk.BooleanVar()
        ttk.Label(form, text="Groww access token (this session only, never saved):").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.token, show="*", width=40).grid(row=0, column=1, columnspan=3, sticky="w")
        self.field(form, 1, "Ticket number", ticket, width=4)
        ttk.Checkbutton(form, text="Market entry (default: limit at the plan's entry)",
                        variable=market).grid(row=1, column=2, columnspan=2, sticky="w")
        self.field(form, 2, "Your reply to the ticket", reply, width=40)
        self.field(form, 2, "Close symbol", close_sym, col=2, width=12)
        g = self.now_args
        row1 = ttk.Frame(bar)
        row1.pack(fill="x")
        row2 = ttk.Frame(bar)
        row2.pack(fill="x", pady=(4, 0))
        self.button(row1, "1. Size (groww-budget)", lambda: self.run(
            out, "groww-budget/size.py", ["--write"] + (["--market"] if market.get() else []) + g()))
        self.button(row1, "2. Risk check", lambda: self.run(out, "groww-risk/check.py", g()))
        self.button(row1, "3. Show tickets", lambda: self.run(out, "groww-broker/groww.py", ["show"] + g()))

        def approve():
            if not reply.get().strip():
                messagebox.showinfo(APP, "Type your own reply to the ticket, for example: yes")
                return
            self.run(out, "groww-broker/groww.py", ["approve", "--index", ticket.get(), "--reply", reply.get()] + g())
            reply.set("")
        self.button(row1, "4. Record my reply", approve)

        def confirm_then(action, extra):
            cfg, _ = self.read_config()
            live = bool(cfg.get("live"))
            text = ("LIVE: this sends a real order to your Groww account." if live else
                    "DRY-RUN: nothing is sent to Groww; the app records a simulated fill.")
            if not messagebox.askyesno(APP, "%s ticket %s?\n\n%s\n\nContinue?" % (action.capitalize(), ticket.get(), text),
                                       icon="warning" if live else "question"):
                return
            self.run(out, "groww-broker/groww.py", [action, "--index", ticket.get()] + extra + g())
        self.button(row1, "5. Place", lambda: confirm_then("place", []))
        self.button(row2, "Protect (OCO after a late fill)", lambda: confirm_then("protect", []))

        def cancel():
            if not reply.get().strip().lower().startswith("yes"):
                messagebox.showinfo(APP, "To cancel, type a reply that starts with yes (for example: yes cancel).")
                return
            confirm_then("cancel", ["--reply", reply.get()])
        self.button(row2, "Cancel unfilled entry", cancel)

        def close():
            if not close_sym.get().strip():
                messagebox.showinfo(APP, "Type the symbol to close.")
                return
            self.run(out, "groww-broker/groww.py", ["close", "--symbol", close_sym.get().strip().upper()] + g())
        self.button(row2, "Write exit ticket", close)
        self.button(row2, "Status", lambda: self.run(out, "groww-broker/groww.py", ["status"] + g()))
        self.button(row2, "Log", lambda: self.run(out, "groww-broker/groww.py", ["log"]))
        self.button(row2, "Login check", lambda: self.run(out, "groww-broker/groww.py", ["check"]))

    def tab_settings(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text="Settings")
        ttk.Label(f, text="Your rules, saved to trader-in/config.json (the Groww skills, the risk check and the India "
                          "skills all read it). Only money you can afford to lose.", foreground="#555",
                  wraplength=1040).pack(anchor="w")
        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=8)
        cfg, found = self.read_config()
        self.cfg_vars = {}
        for i, (key, label, kind) in enumerate(CONFIG_KEYS):
            val = cfg.get(key)
            v = tk.StringVar(value=", ".join(val) if kind == "list" else str(val))
            self.cfg_vars[key] = (v, kind)
            ttk.Label(grid, text=label).grid(row=i % 7, column=(i // 7) * 2, sticky="w", padx=(0, 6), pady=3)
            ttk.Entry(grid, textvariable=v, width=18).grid(row=i % 7, column=(i // 7) * 2 + 1, sticky="w",
                                                           padx=(0, 30), pady=3)
        self.live_var = tk.BooleanVar(value=bool(cfg.get("live")))
        live_box = ttk.Frame(f)
        live_box.pack(anchor="w", pady=6)
        ttk.Checkbutton(live_box, text="LIVE trading at Groww (off = DRY-RUN, nothing is sent)",
                        variable=self.live_var, command=self.confirm_live).pack(side="left")
        info = ttk.Label(f, foreground="#555", wraplength=1040, justify="left", text=(
            "Groww login: paste a daily access token in the Groww tab (kept in memory for this session only), or set "
            "GROWW_ACCESS_TOKEN (or GROWW_API_KEY with GROWW_TOTP_SECRET) in your environment before starting the "
            "app.\nData folder: %s%s" % (self.workdir, "" if found else "   (no config yet: Save writes one)")))
        info.pack(anchor="w", pady=(4, 10))
        ttk.Button(f, text="Save settings", command=self.save_settings).pack(anchor="w")

    def confirm_live(self):
        if self.live_var.get():
            ok = messagebox.askyesno(APP, "Switch Groww to LIVE?\n\nApproved orders will go to your real Groww account "
                                          "with real money. Most intraday traders lose money.\n\nYou can switch back "
                                          "to DRY-RUN any time. Then press Save settings.", icon="warning")
            if not ok:
                self.live_var.set(False)

    def save_settings(self):
        cfg, _ = self.read_config()
        try:
            for key, (v, kind) in self.cfg_vars.items():
                raw = v.get().strip()
                if kind == "num":
                    cfg[key] = float(raw)
                elif kind == "int":
                    cfg[key] = int(float(raw))
                elif kind == "list":
                    cfg[key] = [s.upper() for s in self.split(raw)]
                else:
                    cfg[key] = raw
        except ValueError:
            messagebox.showerror(APP, "A number field has something that is not a number.")
            return
        for k in ("no_entries_before", "no_entries_after", "flat_by"):
            t = cfg[k].split(":")
            if len(t) != 2 or not all(x.isdigit() for x in t):
                messagebox.showerror(APP, "%s wants a time like 15:10." % k)
                return
        if cfg["entry_order"].upper() not in ("LIMIT", "MARKET"):
            messagebox.showerror(APP, "Entry order is LIMIT or MARKET.")
            return
        cfg["entry_order"] = cfg["entry_order"].upper()
        cfg["live"] = bool(self.live_var.get())
        self.write_config(cfg)
        self.refresh_mode()
        self.set_status("Saved trader-in/config.json (%s)." % ("LIVE" if cfg["live"] else "DRY-RUN"))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
