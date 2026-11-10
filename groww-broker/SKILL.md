---
name: groww-broker
description: >-
  Order Executor for Groww. Sends approved NSE intraday orders to the user's
  Groww account through Groww's official Trade API, DRY-RUN by default and
  never without the user's yes. Shows each order as a plain ticket (entry, the
  OCO stop-loss and target, max loss at the stop, brokerage) plus the exact
  API request, records the user's yes word for word, places the entry, waits
  for the fill and then places the OCO smart order for the filled shares.
  Builds the exit ticket that cancels the OCO and sells by the flat-by time,
  cancels unfilled entries, shows positions, orders and active OCOs, and logs
  each trade's max loss so groww-risk can hold the daily limit. Covers setting
  up Groww Trade API access (subscription, access token or API key with TOTP,
  set as environment variables, never in chat). Use when the user says "place
  the order on Groww", "buy it", "execute", "send it to Groww", "close my
  position", "square off", "get me out", "connect Groww", "Groww API setup",
  "check my Groww margin", or after /groww-risk passes.
argument-hint: "[setup | check | show | approve | place | protect | cancel | close | status | log]"
---

# groww-broker

The Order Executor is the only skill in this repo that can lead to a real
order on Groww, so it is the strictest. An order reaches Groww only after
three separate gates, every time, for every order:

1. **groww-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat, recorded
   word for word by `approve`.
3. **The user clicked Yes on Claude Code's permission prompt** for the
   `place`, `protect` or `cancel` command. The repo's `.claude/settings.json`
   has `ask` rules for exactly those commands, so Claude Code asks every time,
   even in auto mode (only bypass-permissions mode skips prompts: do not use it
   for trading).

And a fourth switch only the user flips: **DRY-RUN is the default.** Groww has
no paper account, so in DRY-RUN `place` prints the exact request it would send
and records a simulated fill at the ticket's price; nothing reaches Groww.
Orders go to the real account only after the user sets `"live": true` in
their own `trader-in/config.json`. Never set it for them.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/groww.py" setup                   # login and mode; prints no secrets
python3 "${CLAUDE_SKILL_DIR}/groww.py" check                   # read only: profile and margin from Groww
python3 "${CLAUDE_SKILL_DIR}/groww.py" show                    # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/groww.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/groww.py" place --index 1         # entry, wait for the fill, then the OCO
python3 "${CLAUDE_SKILL_DIR}/groww.py" protect --index 1       # the OCO for an entry that filled later
python3 "${CLAUDE_SKILL_DIR}/groww.py" cancel --index 1 --reply "yes cancel"
python3 "${CLAUDE_SKILL_DIR}/groww.py" close --symbol SBIN     # writes the exit ticket
python3 "${CLAUDE_SKILL_DIR}/groww.py" status                  # positions, today's orders, active OCOs
python3 "${CLAUDE_SKILL_DIR}/groww.py" log
```

`--now "2026-11-09 10:00"` pins the clock (India time). `place --wait 30`
sets how long it waits for the fill.

## What it reads and writes

| file | what |
| --- | --- |
| `trader-in/orders.json` | the proposals (groww-budget writes them; `close` replaces them with an exit) |
| `trader-in/state/risk.json` | groww-risk's verdicts; only a PASS from today is READY |
| `trader-in/state/groww.json` | every yes, no, placed, filled, protected, failed, cancelled and closed, with each entry's max loss |
| `trader-in/config.json` | `live` (false), `flat_by`, `exchange` |
| `~/.cache/my-trader-skills/groww-token.json` | a token made from an API key, until it expires (file mode 600, outside the repo, never printed) |

## Setup: Groww Trade API (on the user's own computer)

Groww's official API: groww.in/trade-api/docs (REST at `api.groww.in`, with
an official Python SDK `growwapi`; this tool uses the REST API with the
standard library). The Groww MCP servers listed online are community
projects, not Groww's: this skill does not use them.

1. **Subscription.** In the Groww app or groww.in: profile > Settings >
   Trading APIs. The API needs Groww's Trade API subscription (Rs 499 a month
   plus GST when checked in Nov 2026; check the page for today's price).
2. **Login, one of:**
   - **Access token**: on that page, Generate API keys > Access Token. It
     expires every day at 6:00 AM. Set `GROWW_ACCESS_TOKEN`.
   - **API key + TOTP**: on Groww Cloud's API keys page, make a key with TOTP;
     set `GROWW_API_KEY` and `GROWW_TOTP_SECRET` (the base32 secret your
     authenticator app uses). The tool makes the 6-digit code itself.
   - **API key + secret**: set `GROWW_API_KEY` and `GROWW_API_SECRET`; approve
     the key on Groww Cloud each day.
3. **Set them in the terminal before starting Claude Code**, for example
   `export GROWW_ACCESS_TOKEN=...` in the shell (or the shell profile). **Keys
   never go in the chat.** Never ask for them, never read a file that holds
   them, never echo them. If the user pastes a key anyway, tell them to delete
   that message and revoke the key on Groww's Trading APIs page.
4. Check it: `groww.py setup` (which login is set, the mode), then
   `groww.py check` (read only: the account's segments and margin).
5. Keep the ask rules in `.claude/settings.json`:
   `"ask": ["Bash(*groww.py place*)", "Bash(*groww.py protect*)", "Bash(*groww.py cancel*)"]`.
   Never move them to `allow`.

Groww's rate limits: 10 orders a second, 250 a minute; 150 token requests a
day (the token cache keeps that low).

## Steps: the entry

1. `groww.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/groww-risk` (or `/groww-budget`).
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order per
   question. Never offer "yes to all". Say whether it is DRY-RUN or LIVE.
3. Run `groww.py approve --index N --reply "<the user's reply, word for word>"`.
   "Not recorded" means it was not an explicit yes for this order (for example
   "sure", "ok", "yes to all", a question, a yes with a condition, or a yes that
   names another stock or a different number of shares). Ask again. Never turn
   a reply into a yes yourself.
4. Only after "Recorded your yes": `groww.py place --index N`. Claude Code's
   prompt appears: the user clicks Yes. In LIVE mode the tool:
   - sends `POST /v1/order/create` (the ticket's exact body),
   - polls `/v1/order/status/{id}` until it fills (up to `--wait` seconds),
   - then sends `POST /v1/order-advance/create`: the OCO for the filled shares
     (stop-loss SL-M, target limit; when one fills, Groww cancels the other).
   If the entry has not filled yet it says so: `protect --index N` once it
   fills, or `cancel --index N --reply "yes cancel"` to drop it. If the OCO
   fails, it says WARNING in capitals: the shares have no stop; protect again,
   set the stop in the Groww app, or close.
5. A failed or cancelled ticket is never placed again: it needs a new proposal,
   a new check and a new yes.

## Steps: out by the close

- The OCO is a day order and Groww squares off open intraday positions at
  3:20 PM (Rs 50 + GST each). Be flat by `flat_by` (15:10 IST) either way.
- `groww.py close --symbol SBIN` writes the exit ticket: the shares held (from
  Groww's positions in LIVE, the log in DRY-RUN) as a market sell, after
  cancelling the stock's active OCO. Then `/groww-risk` (an exit is never
  blocked by a day limit), `groww.py show`, the user's yes, `place`.
- On place, an exit cancels the OCO first (so a later trigger cannot sell
  shares no longer held), re-reads the position, and sells what is really
  there. If the stop or target already filled, it sells nothing and says so.

## Example: Mon Nov 9 2026 (DRY-RUN)

```
TICKET 1 of 1, DRY RUN (nothing is sent), Groww request preview
  BUY 46 SBIN on NSE, limit 1040.00, MIS (intraday), good for today
  stop-loss 1029.20
  target 1061.60
  max loss about Rs 496.80 if the stop fills at 1029.20
  why: opening-range breakout on SBIN: 46 shares, bound by your max loss limit (46 x 10.80 = Rs 496.80 at the stop)
  brokerage about Rs 40.00 both ways (taxes and exchange charges on top)
  READY: groww-risk PASS, waiting for your yes
  It would send:
    1. POST https://api.groww.in/v1/order/create
       {"trading_symbol": "SBIN", "quantity": 46, "price": 1040.0, "validity": "DAY", "exchange": "NSE", ...}
    2. POST https://api.groww.in/v1/order-advance/create   (after the entry fills, for the filled shares)
       {"smart_order_type": "OCO", ..., "stop_loss": {"trigger_price": "1029.20", "order_type": "SL_M", ...}}
```

The user replies "yes". `approve` records it; `place` asks Claude Code's
permission, then records a simulated fill of 46 at 1,040.00 with a simulated
OCO. "yes buy RELIANCE" or "yes, 50 shares" would not have been recorded.

## What it never does

- Never sends an order without all three gates, and never two orders on one yes.
- Never runs from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores in the repo, or repeats a Groww token, key or secret.
- Never turns `live` on. The user does that in their own config.
- Never trades F&O, options, delivery (CNC), MTF or shorts, and never places a
  GTT or a smart order other than the stop/target OCO for a filled entry.
- Never promises a fill price: a market order and an SL-M stop fill at whatever
  the market gives, and a circuit can stop trading altogether.

## Safety

- Education and tooling, not investment advice. This pack is not a
  SEBI-registered investment adviser or research analyst, and a PASS is a
  rules check, not a recommendation.
- DRY-RUN by default; LIVE only by the user's own config change.
- Keys live in environment variables on the user's computer, never in the chat.
