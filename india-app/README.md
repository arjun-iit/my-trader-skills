# India Trader (desktop app)

A standalone window for the India (NSE) and Groww skills. No browser and no
Claude needed: each tab runs the same script the matching skill runs, and the
app and Claude share the files in `trader-in/`.

| Tab | Skill | What it does |
| --- | --- | --- |
| Scanner | india-scanner | Opening-range breakouts and daily setups on an index, your symbols or all NSE EQ stocks |
| News | india-news | NSE filings, upcoming results meetings, headlines, the move vs the previous close |
| Entry | india-entry | The first bar that closes above the opening range (or your level); can watch until it triggers |
| Exit | india-exit | Stop and target before you buy, on NSE's tick, flat by 15:10 |
| Time Machine | india-timemachine | 10,000 replays of the plan plus the same rule on past sessions |
| Alerts | india-alerts | Stop, target and flat-by alerts; auto-check on a timer (beeps and raises the window) |
| Results | india-analyst | Quarterly results from NSE filings |
| Insider | india-insider | Insider-trading disclosures, bulk and block deals |
| Groww | groww-budget, groww-risk, groww-broker | Size, risk check, tickets, your yes, place (with a confirmation dialog), exit ticket, status |
| Settings | | Your limits in `trader-in/config.json`, and the DRY-RUN / LIVE switch |

**Replay time** (top bar): type a past India time such as `2026-11-09 10:00`
to practise on that session; leave it empty for now.

## Start it

You need Python 3.8 or newer with Tkinter. The installers from python.org
include it on Windows and macOS; on Debian or Ubuntu, `sudo apt install python3-tk`.

- **Windows:** double-click `run_app.bat`
- **macOS:** double-click `run_app.command` (the first time: right-click, Open)
- **Linux:** `./run_app.sh`
- **Any system:** `python3 india-app/trader_app.py`

## One program file, no Python needed

On the system you want it for (PyInstaller cannot build for another system):

```bash
python -m pip install pyinstaller
```

```bash
python india-app/build_exe.py
```

The program is in `india-app/dist/` (`IndiaTrader.exe` on Windows). It keeps
its data in `~/IndiaTrader` (or the folder in `INDIA_TRADER_DATA`).

## Groww

DRY-RUN by default: nothing is sent to Groww until you tick LIVE in Settings
and save. Three gates before any order: groww-risk must PASS that exact order
today, you type your own reply ("yes") for that one ticket, and you confirm a
dialog before place, protect or cancel.

Login: paste the day's Groww access token in the Groww tab (kept in memory
for that session only, never written to disk), or set `GROWW_ACCESS_TOKEN`
(or `GROWW_API_KEY` with `GROWW_TOTP_SECRET`) in your environment before
starting the app. Never share a token.

Education and tooling, not investment advice. Most intraday traders lose money.
