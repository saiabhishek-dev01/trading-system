# Trading system: automated paper and real-money trading for NSE equities

Built from the "AI-assisted trading system" design doc. You enter an investment value in
rupees and the auto-trader does the rest: decides, places orders, tracks value against a
Nifty 50 index fund and alerts you. Two modes share one engine:

* **Paper:** simulated fills on live prices with slippage and full Zerodha charges. No account needed.
* **Real money:** the same orders sent to Zerodha through Kite Connect, behind five safety
  gates. Setup: `docs/LIVE_TRADING_SETUP.md`.

The auto-trader runs **monthly momentum** (10 strongest Nifty 100 stocks, cash when the Nifty
is below its 200-day average), the only strategy with an edge in `docs/PROFITABILITY_RESEARCH.md`.
Intraday trading is paper-only: it lost money in every test.

## What's here

| File | Purpose |
|------|---------|
| `autotrader.py` | Auto-trader engine (monthly momentum): decide, execute through a broker, mark to market vs Nifty, reconcile with Zerodha. `--mode paper\|live` |
| `brokers.py` | Connectors: `PaperBroker` (simulated) and `KiteBroker` (Zerodha, real orders, safety gates, daily login) |
| `alerts.py` | Telegram alerts (optional) |
| `ui_auto.py` | Dashboard tabs for the auto-trader |
| `swing_backtest.py` | Daily-bar research: momentum, RSI-2, trend filter, after-tax results |
| `paper_trader.py` | Live paper-trading engine: polls 1-min data, trades on closed 5-min bars, manages stops/targets, applies risk limits, squares off at 15:15, writes a daily report |
| `backtest.py` | Historical simulation using the same strategy, risk and charges code. `--sweep` runs several variants |
| `app.py` | Streamlit dashboard: investment value, live session, replay any day, backtests, INR cost planner |
| `config.json` | Investment value, universe, risk limits, strategy parameters, charges |
| `docs/COMPONENTS_AND_COSTS.md` | Components needed for end-to-end automation, and monthly costs in INR |
| `docs/PROFITABILITY_RESEARCH.md` | What was tested and what (barely) works |
| `docs/LIVE_TRADING_SETUP.md` | Zerodha setup, safety gates, daily login |

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Run

```powershell
# Dashboard (opens in the browser)
.venv\Scripts\streamlit run app.py

# Auto-trader without the dashboard
.venv\Scripts\python autotrader.py --mode paper      # or --mode live (after setup)

# Intraday paper session from the command line (market hours, 9:15–15:15 IST)
.venv\Scripts\python paper_trader.py

# Backtest the last ~60 days, with parameter variants
.venv\Scripts\python backtest.py --sweep
```

Kill switch: create a file named `KILL` in this folder (or press the button in the
dashboard). Open positions are squared off within a minute.

## How the intraday paper engine trades

- **Universe:** 20 liquid Nifty 50 stocks (configurable).
- **Strategies:** `orb` (opening range breakout, default) and `vwap_trend_breakout_5m`.
- **Risk engine** (all derived from the investment value): 1% risk per trade, 2% daily loss
  stop, at most 5 open positions, at most 20% of capital per stock, limited entries per stock per day.
- **Exits:** every trade has a stop and a target from the moment it opens; all positions
  close by 15:15; no new entries after 15:00.
- **Honest simulation:** fills on the next bar after a signal, slippage on every fill,
  stop assumed to fill first if stop and target are touched in the same bar, and full
  Zerodha intraday charges (brokerage, STT, exchange, SEBI, GST, stamp duty).

## Results so far

The 60-day backtest (Aug–Nov 2026, ₹1,00,000) loses money on every variant tried. The
main reason is cost: on ₹1 lakh each trade is at most ₹20,000, so a round trip costs about
₹40 in charges and slippage, against typical intraday moves of ₹50–200. Run
`python backtest.py --sweep` for the current table.

Next steps that address this: daily/swing strategies (fewer, larger moves), larger
capital per trade, and minute data from Kite for longer, more reliable tests.

## Data

Paper mode uses Yahoo Finance (free, about 1–2 minutes delayed, 60 days of 5-minute
history). Live mode will use Zerodha Kite Connect. See `docs/COMPONENTS_AND_COSTS.md`.
