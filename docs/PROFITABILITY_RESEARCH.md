# Can this be made profitable? Research notes (4 Nov 2026)

Capital assumed: ₹1,00,000 unless stated. All results include full Zerodha charges and
slippage. Swing results also show after-tax returns under current rules (STCG 20%,
LTCG 12.5% above ₹1.25 lakh a year).

## 1. Intraday (5-minute bars, 20 Nifty stocks): no

`python backtest.py --sweep`, 59 trading days.

| Variant | Trades | Net P&L |
|---|---|---|
| VWAP trend breakout | 2,191 | −₹53,304 |
| Same, zero slippage | 2,161 | −₹32,105 (gross +₹3,179) |
| Opening range breakout 30 min, 2R | 542 | −₹14,543 |

Live paper session, 4 Nov 2026, 14:19–15:15: 7 trades, −₹222 net (−₹86 trading, −₹136 charges).

**Why:** a ₹20,000 trade pays about ₹41 per round trip in charges and slippage. Typical
intraday moves are ₹50–200. Before costs these rules were close to zero, and costs turn
that into a steady loss. Intraday edges mostly belong to firms with co-location,
order-book data and near-zero costs.

## 2. Short-term swing mean reversion (RSI-2 pullbacks): no

`python swing_backtest.py`. 2–10 day holds lose 1–3% a year after costs. Delivery STT
is 0.1% on both sides, and that alone uses up most of the bounce these trades capture.

## 3. Monthly momentum (top 10 of the Nifty 100 by 12-month return): small real edge, smaller than the backtest says

| After-tax return per year | 2010–2018 (design) | 2019–2026 (unseen) |
|---|---|---|
| Nifty 50 buy & hold | 8.3% | 10.7% |
| Same 100 stocks, equal-weight buy & hold | 17.9% | 20.1% |
| Momentum top 10 + Nifty 200-day filter | 19.3% | 31.5% |

**These backtest numbers are inflated by survivorship bias.** The universe is today's
Nifty 100, which includes stocks that joined because they rose sharply (BSE, HAL,
Mazagon Dock and others). Momentum concentrates in exactly those stocks.

**Real-world check (no survivorship bias):** the Bandhan Nifty200 Momentum 30 index fund,
using NAVs from mfapi.in, Sep 2022 – Nov 2026:

| | Return per year | Worst fall |
|---|---|---|
| Nifty200 Momentum 30 index fund | 10.1% | −31.7% (Sep 2024 → Apr 2025) |
| Nifty 50 index fund | 8.4% | −15.5% |

Year by year: momentum +40% (2023), +20% (2024), −5% (2025), −6% (2026 so far).
The real edge is about **1–2 points a year with twice the drawdown**, not the 10+ points
the backtest suggests. In the design period, the backtest edge over same-universe
buy-and-hold was also only about 1.4 points after tax.

## 4. Trend filter on the index (hold Nifty only above its 200-day average): no

It lowers drawdowns but also lowers returns (6.1% vs 8.3% in 2010–2018, 9.5% vs 10.7% in 2019–2026).

## What would make it profitable

1. **Stop intraday trading at this capital.** The cost hurdle can't be engineered away at ₹20,000 a trade.
2. **The edges that exist are slow:** factor tilts (momentum, quality, value) held for
   months, rebalanced rarely, ideally held past one year for 12.5% LTCG instead of 20% STCG.
3. **Those edges are already sold cheaply.** A momentum index fund costs about 0.3–0.5% a year
   with no server, API or AI bill. A ₹5,000-a-month system running the same idea on ₹1 lakh
   costs about 60% of capital a year and can't compete.
4. **A self-built system only makes sense if it beats that cheap option after its own
   costs.** That needs either much more capital (₹10–25 lakh+, so fixed costs are 2–6% a year
   or less) or a genuinely different edge. Proving either needs **point-in-time index
   constituent data** (paid) so backtests aren't flattered by survivorship.
5. **Cheapest honest setup if continuing:** a monthly rebalance strategy needs no intraday
   data, no AI and no always-on server. It runs on a laptop once a month; orders can go
   through a Kite basket by hand, or Kite Connect (₹500) plus a small static-IP VM (about ₹500–1,000).

## Recommendation

For ₹1 lakh, the evidence favours a low-cost index fund (Nifty 50, optionally with a
momentum or multi-factor index fund as a smaller part) over running this system with real
money. Keep the system as a paper-trading research tool. Revisit live trading only with
point-in-time data, a strategy that beats the cheap index alternative after all costs and
taxes on unseen data, and enough capital that fixed costs are small.

This is research, not personal financial advice. A SEBI-registered adviser can look at
your full situation.
