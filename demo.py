"""Demo replay: run the auto-trader's monthly rule on real past prices so the dashboard
shows a filled-in portfolio (holdings, rebalances, value vs Nifty).

Same decide() rule and the same charges as the auto-trader; decisions on the last close of
each month, fills at the next day's open with 0.1% slippage. The regime filter is OFF here
so the demo shows trades even in months where the real rule would hold cash.

  python demo.py [--start 2026-07-01] [--capital 100000]
"""
import argparse
import math

import pandas as pd
import yfinance as yf

import autotrader as at
import swing_backtest as sb
from brokers import SLIPPAGE, delivery_charges, now_ist, tick


def history(symbols):
    tick_ = [f"{s}.NS" for s in symbols] + [at.INDEX]
    raw = yf.download(tick_, period="2y", interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    o, c = {}, {}
    for t in tick_:
        try:
            o[t.replace(".NS", "")], c[t.replace(".NS", "")] = raw[t]["Open"], raw[t]["Close"]
        except KeyError:
            pass
    o, c = pd.DataFrame(o), pd.DataFrame(c)
    idx = c[at.INDEX].dropna().index
    return o.reindex(idx).where(lambda x: x > 0), c.reindex(idx).where(lambda x: x > 0)


def build(start="2026-07-01", capital=100000.0, slots=10):
    o, c = history(sb.universe())
    s = at.new_state("paper", {"investment_value": capital})
    s["mode"] = "demo"
    days = c.loc[start:].index
    month = pd.Series(c.index.to_period("M"), index=c.index)
    decision_days = [d for d in c.index[:-1] if month[d] != month.shift(-1)[d] and d >= pd.Timestamp(start) - pd.Timedelta(days=31)]
    fills = {c.index[c.index.get_loc(d) + 1]: d for d in decision_days if c.index.get_loc(d) + 1 < len(c)}
    for d in days:
        if d in fills:
            dec = fills[d]
            hist = c.loc[:dec]
            eq = at.equity_at(s, hist.iloc[-1].to_dict())
            target, why = at.decide(hist, eq, slots, regime_filter=False)
            s["last_decision"] = str(dec.date())
            row = o.loc[d]
            for sym in [x for x in s["positions"] if x not in target]:
                p = s["positions"].pop(sym)
                px = tick(row[sym] * (1 - SLIPPAGE))
                val = p["shares"] * px
                ch = delivery_charges(val, "SELL")
                s["cash"] += val - ch
                s["charges"] += ch
                s["trades"].append(dict(status="COMPLETE", symbol=sym, side="SELL", qty=p["shares"], price=px,
                                        charges=round(ch, 2), time=str(d.date()), pnl=round(val - ch - p["cost"], 2)))
            eq = at.equity_at(s, row.to_dict())
            for sym in target:
                if sym in s["positions"] or len(s["positions"]) >= slots or pd.isna(row.get(sym)):
                    continue
                px = tick(row[sym] * (1 + SLIPPAGE))
                n = math.floor(min(eq / slots, s["cash"]) / (px * 1.0025))
                if n < 1:
                    continue
                val = n * px
                ch = delivery_charges(val, "BUY")
                s["cash"] -= val + ch
                s["charges"] += ch
                s["positions"][sym] = dict(shares=n, entry=px, entry_date=str(d.date()), cost=round(val + ch, 2))
                s["trades"].append(dict(status="COMPLETE", symbol=sym, side="BUY", qty=n, price=px,
                                        charges=round(ch, 2), time=str(d.date()), pnl=None))
            if s["bench_units"] is None:
                s["bench_units"] = capital / row[at.INDEX]
                s["started"] = str(d.date())
            at.log("demo", f"{d.date()} rebalance (decided {dec.date()} close): {', '.join(s['positions'])}")
        if s["started"]:
            if s["curve"]:
                s["cash"] += s["cash"] * at.CASH_YIELD * (d - pd.Timestamp(s["curve"][-1]["date"])).days / 365
            r = c.loc[d]
            s["curve"].append(dict(date=str(d.date()), equity=round(at.equity_at(s, r.to_dict()), 2),
                                   nifty=round(float(s["bench_units"] * r[at.INDEX]), 2)))
    s["last_prices"] = {sym: round(float(c.iloc[-1][sym]), 2) for sym in s["positions"]}
    s["prices_as_of"] = str(c.index[-1].date())
    s["heartbeat"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    s["demo_note"] = f"Replay from {start} on real prices, regime filter off. Not live."
    at.save("demo", s)
    return s


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--capital", type=float, default=100000)
    a = ap.parse_args()
    at.paths("demo")["log"].unlink(missing_ok=True)
    s = build(a.start, a.capital)
    last = s["curve"][-1]
    print(f"{last['date']}: ₹{last['equity']:,.0f} ({last['equity'] / a.capital - 1:+.2%}) vs Nifty "
          f"₹{last['nifty']:,.0f} ({last['nifty'] / a.capital - 1:+.2%}) · {len(s['trades'])} orders · "
          f"charges ₹{s['charges']:,.0f}")
