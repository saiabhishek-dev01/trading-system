# Going live with real money (Zerodha)

The auto-trader runs the same engine in two modes. Paper needs nothing. Real money needs
the steps below, done by the account holder. The money traded must be in the account
holder's own Zerodha account.

## One-time setup

1. **Kite Connect app.** On https://developers.kite.trade create an app on the
   Connect plan (₹500 a month). Set its redirect URL to the dashboard address, for example
   `http://127.0.0.1:8501/`. Note the API key and secret.
2. **Static IP.** Order calls must come from the IP registered in the app. Run the
   system on a machine with a fixed public IP (for example a Mumbai cloud VM) and register it.
   Data calls work from any IP; orders from any other IP are rejected.
3. **Secrets.** Copy `.env.example` to `.env` on that machine and fill in
   `KITE_API_KEY` and `KITE_API_SECRET`. Leave `LIVE_TRADING_ENABLED=no` until you are ready.
   Never paste these into chat or commit them (`.env` is gitignored).
4. **Optional alerts.** Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to get messages
   for every rebalance, the daily value and any error.

## Switching on

1. Set `LIVE_TRADING_ENABLED=yes` in `.env`.
2. Open the dashboard → **💸 Auto-trader: Real money**.
3. Press **Log in to Zerodha** (required every trading day by exchange rules).
4. Enter the **live investment value**. Start small: the strategy needs about ₹50,000 or more
   to hold 10 stocks with whole shares.
5. Tick **Switch on live trading**, type the confirmation, then save.
6. When every checklist item is ✅, press **Start auto-trader**.

## What it does with the money

* Once a month it decides the 10 holdings from the latest close. During the next session it
  sells what dropped out and buys what came in, as **limit orders, delivery (CNC)**,
  0.5% through the last price so they fill like market orders. Plain market orders are
  rejected over the API.
* If the Nifty 50 is below its 200-day average, it sells everything and holds cash.
* It only touches the stocks it bought. Other holdings in the account are left alone.
* Every evening it records the value next to the same money in a Nifty 50 index fund and
  checks its positions against Zerodha's holdings. Any mismatch is shown and alerted.

## Safety gates (an order is sent only if all pass)

| Gate | Where |
|---|---|
| `LIVE_TRADING_ENABLED=yes` | `.env` on the server |
| Live trading switched on, with typed confirmation | Dashboard |
| Zerodha login from today | Dashboard login button |
| No kill switch (`KILL` file) | Dashboard kill switch |
| Order value ≤ max order value | Dashboard live settings |

## If the daily login is missed

The engine waits, sends no orders and retries every 5 minutes. Because the strategy is monthly,
a missed day only delays a rebalance.

## Expectations

Read `docs/PROFITABILITY_RESEARCH.md` first. Monthly momentum had a small real edge over
the Nifty (about 1–2 points a year in the real fund) with roughly twice the drawdown. It
can lose money for months. The paper tab runs the identical engine. Watch it for a few
months before trusting it with real money.
