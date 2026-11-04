# Components and costs (INR)

Estimates as of November 2026. Prices change: check each provider before committing.
Exchange rate assumed: US$1 = ₹90.

## What "fully automated" means here

Everything runs by itself (data, signals, AI review, risk checks, orders, exits,
square-off, reports, alerts) **except one step: the daily Zerodha login.** SEBI/exchange
rules require the account holder to log in manually at least once a day, and Zerodha
advises against automating it. The system sends a Telegram reminder at 8:45 am; one tap
on the dashboard's login button starts the day. If nobody logs in, it simply doesn't
trade that day.

The account that trades must belong to the person whose money it is. Kite Connect and
the static IP are personal (shareable only with immediate family). If Sai's money is
traded, it needs Sai's own Zerodha account, own API key and own static IP.

## Components

| # | Component | What it does | Choice | Needed for |
|---|-----------|--------------|--------|-----------|
| 1 | Broker API | Live prices, order placement, positions | Zerodha Kite Connect | Live trading (and live-quality paper data) |
| 2 | Market data (paper phase) | Prices for simulation | Yahoo Finance via `yfinance` (free, ~1-2 min delay) | Paper trading now |
| 3 | Historical data | Backtests | Kite historical API (minute candles); Yahoo gives only 60 days of 5-min | Serious backtests |
| 4 | News and filings | Sentiment, no-trade days | Free RSS (ET Markets, Moneycontrol, Mint, Business Standard), NSE/BSE announcements | AI phase |
| 5 | AI analyst | Headline sentiment, signal review, daily briefs | Claude API: Haiku 5.5 for headlines, Sonnet 5.5 for reviews | AI phase |
| 6 | Risk engine | Hard limits on every order | In this repo (`paper_trader.py`) | Always |
| 7 | Dashboard | Investment value, P&L, positions, kill switch, replays | In this repo (`app.py`, Streamlit) | Always |
| 8 | Server | Runs 24x7 near the exchange, fixed IP for orders | 1 VM in Mumbai, 2 vCPU / 4 GB, static IP | Live trading |
| 9 | Alerts | Fills, stops, daily summary, login reminder | Telegram bot | Live trading |
| 10 | TradingView | Charts for a human to look at | **Not needed.** The system computes its own indicators. Only useful if you want to eyeball charts, or use TradingView alerts as a signal source (requires a paid plan for webhooks). | Optional |

## Monthly running cost

| Item | ₹ / month | Notes |
|------|-----------|-------|
| Kite Connect (Connect plan) | 500 | Per API key. Required for live data + orders |
| Cloud VM, Mumbai, 2 vCPU / 4 GB + static IP | 2,000 – 3,000 | e.g. AWS Lightsail / EC2 t3.medium, GCP e2-medium. The 4 vCPU / 16 GB in the design doc is more than needed |
| Claude API (see breakdown) | 1,300 – 2,600 | Plus 18% GST if charged |
| News / data vendor | 0 | Free sources first. Paid feeds run ₹2,000 – 10,000 |
| Telegram | 0 | |
| Domain + HTTPS | 0 – 100 | Optional |
| **Total** | **≈ ₹4,000 – 6,000** | ≈ ₹48,000 – 72,000 a year |

### Claude API breakdown (21 trading days a month)

| Job | Model | Calls / month | Tokens per call (in / out) | ₹ / month |
|-----|-------|--------------|-----------------------------|-----------|
| Headline sentiment every 15 min | Haiku 5.5 ($0.10 / $0.50 per M) | ~525 | 3,000 / 500 | ~30 |
| Review each strategy signal | Sonnet 5.5 ($2 / $10 per M) | ~420 (20/day) | 6,000 / 1,500 | ~1,020 |
| Pre-market brief + post-market review | Sonnet 5.5 | ~42 | 20,000 / 3,000 | ~270 |
| Weekly strategy ideas | Sonnet 5.5 | ~4 | 50,000 / 10,000 | ~72 |
| **Total** | | | | **≈ ₹1,400** |

Using Opus 5.5 ($4 / $20) for signal reviews roughly doubles this, to about ₹2,500 a month.
Prompt caching (cached input billed at about a tenth of the normal input price) can cut 20–40% once the prompts settle.

### Trading charges (per trade, not fixed)

Zerodha intraday equity, per round trip, are computed in `round_trip_charges()`:
brokerage 0.03% capped at ₹20 per order, STT 0.025% on the sell side, exchange 0.00297%,
SEBI ₹10 per crore, stamp duty 0.003% on the buy side, and 18% GST on brokerage + exchange + SEBI.

| Trade size | Charges | + slippage (0.05% each side) | Total cost | Move needed just to break even |
|-----------|---------|------------------------------|-----------|-------------------------------|
| ₹20,000 | ~₹21 | ~₹20 | ~₹41 | ~0.21% |
| ₹1,00,000 | ~₹82 | ~₹100 | ~₹182 | ~0.18% |
| ₹5,00,000 | ~₹223 | ~₹500 | ~₹723 | ~0.15% |

## Break-even: the number that matters

Fixed costs of about ₹5,000 a month are about ₹60,000 a year. Before any trading profit:

| Capital | Return needed just to cover fixed costs |
|---------|----------------------------------------|
| ₹1,00,000 | **60% a year** |
| ₹5,00,000 | 12% a year |
| ₹10,00,000 | 6% a year |
| ₹25,00,000 | 2.4% a year |

With ₹1 lakh, the system has to beat almost every professional fund just to pay for
itself. Paper-trade at ₹1 lakh to prove the strategy, but plan live capital and costs
together. A cheaper setup for small capital: skip the AI for now (₹0), run the VM only
in market hours, and trade daily/swing strategies, which pay far less in charges per rupee made.

## Accounts you will need later (not now)

Paper trading needs no logins. Before going live:

1. **Zerodha**: Kite Connect developer app (API key + secret) on the trading account, and the static IP registered in the developer console.
2. **Anthropic**: an API key from the Claude Console (only when the AI layer is switched on).
3. **Telegram**: a bot token from @BotFather and your chat ID.
4. **Cloud**: an AWS / GCP / Azure account for the Mumbai server.

Never paste these into chat or commit them. They go in a `.env` file on the server (already in `.gitignore`).

## Taxes

Intraday equity profits are speculative business income, taxed at your slab rate; F&O is
non-speculative business income. Losses have their own set-off rules. Talk to a chartered
accountant before going live.
