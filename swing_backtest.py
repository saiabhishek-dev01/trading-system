"""Daily-bar (swing / positional) strategy research for NSE delivery equities.

Why: intraday tests lost money mainly to costs. These strategies trade far less
and target multi-percent moves, so costs are a small share of each trade.

Honesty rules
  * parameters are fixed in advance from well-known published rules, NOT tuned
  * results reported separately for 2010-2018 (design) and 2019-today (unseen)
  * decisions use the close of day t, orders fill at the OPEN of day t+1 plus slippage
  * full Zerodha delivery costs: STT 0.1% both sides, stamp 0.015% on buys,
    exchange + SEBI + GST, DP charge Rs 15.93 per stock sold, slippage 0.1% per side
  * idle cash earns a liquid-fund rate (6%/yr), as it would in LIQUIDBEES
  * benchmarks: Nifty 50 ETF buy-and-hold, and equal-weight buy-and-hold of the SAME
    universe (which carries the same survivorship bias as the strategies)

Known bias: the universe is TODAY's Nifty 100, so stocks that later collapsed and
left the index are missing. Absolute returns are flattered; compare against the
same-universe buy-and-hold to judge the strategy itself.

Usage: python swing_backtest.py [--capital 100000] [--refresh]
"""
import argparse
import io
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "swing"
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

START = "2008-06-01"          # extra history so 200-day / 12-month indicators are warm by 2010
PERIODS = {"2010-2018 (design)": ("2010-01-01", "2018-12-31"),
           "2019-2026 (unseen)": ("2019-01-01", "2099-12-31"),
           "2010-2026 (full)": ("2010-01-01", "2099-12-31")}
INDEX, ETF = "^NSEI", "NIFTYBEES"

COST = dict(stt=0.001, stamp_buy=0.00015, exch=0.0000297, sebi=10 / 1e7, gst=0.18,
            dp_per_sell=15.93, slippage=0.001, etf_stt_sell=0.00001)
CASH_YIELD = 0.06


# ------------------------------------------------------------------ data
def universe():
    cache = CACHE / "nifty100.csv"
    try:
        r = requests.get("https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        cache.write_text(r.text, encoding="utf-8")
    except Exception:
        pass
    return pd.read_csv(cache)["Symbol"].tolist()


def load(symbols, refresh=False):
    f = CACHE / f"daily_{pd.Timestamp.today():%Y%m%d}.pkl"
    if f.exists() and not refresh:
        return pd.read_pickle(f)
    tick = [f"{s}.NS" for s in symbols] + [f"{ETF}.NS", INDEX]
    raw = yf.download(tick, start=START, interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    o, c = {}, {}
    for t in tick:
        try:
            df = raw[t]
        except KeyError:
            continue
        name = t.replace(".NS", "")
        o[name], c[name] = df["Open"], df["Close"]
    data = {"open": pd.DataFrame(o), "close": pd.DataFrame(c)}
    # Drop days the index didn't trade (holidays with stray rows) and obviously bad prints
    idx = data["close"][INDEX].dropna().index
    for k in data:
        data[k] = data[k].reindex(idx).where(lambda x: x > 0)
    pd.to_pickle(data, f)
    return data


def fix_etf(data):
    """Yahoo's NIFTYBEES history has a broken split adjustment, so trade the Nifty 50
    index itself (scaled to ETF-like unit prices). It excludes dividends (~1.2%/yr),
    which slightly understates this benchmark."""
    for k in data:
        data[k][ETF] = data[k][INDEX] / 100
    return data


# ------------------------------------------------------------------ indicators
def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ------------------------------------------------------------------ simulator
class Sim:
    """Daily portfolio simulator. A strategy returns the set of symbols it wants to hold
    after today's close; changes fill at tomorrow's open. New positions are sized at
    equity / slots; existing positions are left alone (no churn from re-weighting)."""

    def __init__(self, data, start, end, capital, slots, taxes=False):
        self.taxes = taxes
        self.o, self.c = data["open"], data["close"]
        self.dates = self.c.loc[start:end].index
        self.capital, self.slots = capital, slots
        self.cash, self.pos = float(capital), {}       # sym -> shares
        self.entry_i = {}
        self.basis = {}                                 # sym -> (cost incl. charges, buy date)
        self.fy_st = self.fy_lt = 0.0                   # realized gains this financial year
        self.tax_paid = 0.0
        self.charges = 0.0
        self.trades = 0
        self.curve = []

    def _cost(self, sym, value, side):
        etf = sym == ETF
        c = COST
        stt = (c["etf_stt_sell"] if side == "SELL" else 0) if etf else c["stt"]
        x = value * (stt + c["exch"] + c["sebi"]) + value * (c["exch"] + c["sebi"]) * c["gst"]
        if side == "BUY":
            x += value * c["stamp_buy"]
        else:
            x += c["dp_per_sell"]
        return x

    def equity(self, prices):
        return self.cash + sum(n * prices.get(s, np.nan) if not np.isnan(prices.get(s, np.nan)) else 0
                               for s, n in self.pos.items())

    def _rebalance(self, want, i):
        px = self.o.iloc[i]
        # sells first
        for s in [s for s in self.pos if s not in want]:
            p = px.get(s)
            if p is None or np.isnan(p):
                continue                      # no price today: try again tomorrow
            fill = p * (1 - COST["slippage"])
            v = self.pos.pop(s) * fill
            ch = self._cost(s, v, "SELL")
            self.cash += v - ch
            self.charges += ch
            self.trades += 1
            self.entry_i.pop(s, None)
            self._realize(s, v - ch, self.o.index[i])
        # buys
        eq = self.equity(px.where(px.notna(), self.c.iloc[i - 1]))
        # lists keep the strategy's priority order; sets are sorted so runs are reproducible
        for s in (want if isinstance(want, list) else sorted(want)):
            if s in self.pos:
                continue
            p = px.get(s)
            if p is None or np.isnan(p):
                continue
            fill = p * (1 + COST["slippage"])
            budget = min(eq / self.slots, self.cash)
            n = int(budget / (fill * (1 + 0.0012)))     # leave room for charges
            if n < 1:
                continue
            v = n * fill
            ch = self._cost(s, v, "BUY")
            self.cash -= v + ch
            self.charges += ch
            self.pos[s] = n
            self.entry_i[s] = i
            self.basis[s] = (v + ch, self.o.index[i])
            self.trades += 1

    # ---- capital gains tax, current rules applied to every year for a like-for-like
    # comparison: STCG 20% (held < 1 year), LTCG 12.5% above Rs 1.25 lakh a year,
    # short-term losses offset long-term gains. Paid at each financial year end (31 Mar).
    def _realize(self, s, proceeds, when):
        cost, bought = self.basis.pop(s)
        gain = proceeds - cost
        if (when - bought).days >= 365:
            self.fy_lt += gain
        else:
            self.fy_st += gain

    def _tax_due(self, st, lt):
        if st < 0:
            lt, st = lt + st, 0.0
        return 0.20 * st + 0.125 * max(0.0, lt - 125000)

    def _pay_tax(self):
        t = self._tax_due(self.fy_st, self.fy_lt)
        self.cash -= t
        self.tax_paid += t
        self.fy_st = self.fy_lt = 0.0             # (loss carry-forward ignored: conservative)

    def after_tax_liquidation(self):
        """Value if everything were sold at the last close and the final year's tax paid."""
        d, px = self.c.index[self.c.index.get_loc(self.dates[-1])], self.c.loc[self.dates[-1]]
        cash, st, lt = self.cash, self.fy_st, self.fy_lt
        for s, n in self.pos.items():
            p = px.get(s)
            if p is None or np.isnan(p):
                p = self.c[s].loc[:d].dropna().iloc[-1]
            v = n * p * (1 - COST["slippage"])
            v -= self._cost(s, v, "SELL")
            cost, bought = self.basis[s]
            if (d - bought).days >= 365:
                lt += v - cost
            else:
                st += v - cost
            cash += v
        return cash - self._tax_due(st, lt)

    def run(self, decide):
        all_dates = self.c.index
        want = None
        prev = None
        for d in self.dates:
            i = all_dates.get_loc(d)
            if self.taxes and prev is not None and d.month == 4 and prev.month == 3:
                self._pay_tax()
            prev = d
            if want is not None:
                self._rebalance(want, i)
            self.cash *= (1 + CASH_YIELD) ** (1 / 252)
            cl = self.c.iloc[i]
            self.curve.append((d, self.equity(cl.where(cl.notna(), self.c.iloc[:i + 1].ffill().iloc[-1]))))
            want = decide(i, self)
        return pd.Series(dict(self.curve))


def stats(curve, sim, capital, taxed_sim):
    yrs = (curve.index[-1] - curve.index[0]).days / 365.25
    final = curve.iloc[-1]
    rets = curve.pct_change().dropna()
    at = taxed_sim.after_tax_liquidation()
    return dict(final_value=round(final), cagr_pct=round(((final / capital) ** (1 / yrs) - 1) * 100, 1),
                after_tax_value=round(at), after_tax_cagr_pct=round(((at / capital) ** (1 / yrs) - 1) * 100, 1),
                max_drawdown_pct=round((curve / curve.cummax() - 1).min() * 100, 1),
                sharpe=round(rets.mean() / rets.std() * np.sqrt(252), 2) if rets.std() else 0,
                trades=sim.trades, charges=round(sim.charges),
                charges_pct_of_capital_per_yr=round(sim.charges / capital / yrs * 100, 2))


# ------------------------------------------------------------------ strategies
def make_strategies(data, stocks):
    c = data["close"]
    sma200 = c.rolling(200).mean()
    sma5 = c.rolling(5).mean()
    r2 = c[stocks].apply(lambda s: rsi(s, 2))
    mom_12_1 = c.shift(21) / c.shift(252) - 1            # 12-month return, skipping latest month
    month_end = pd.Series(c.index.to_period("M"), index=c.index)
    is_month_end = month_end != month_end.shift(-1)
    nifty_up = c[INDEX] > sma200[INDEX]

    def hold_etf(i, sim):
        return {ETF}

    def equal_weight_universe(i, sim):
        if sim.pos:                                       # buy once, then hold
            return set(sim.pos)
        row = c.iloc[i][stocks]
        return set(row.dropna().index)

    def nifty_trend(i, sim):
        if not is_month_end.iloc[i]:
            return set(sim.pos)
        return {ETF} if nifty_up.iloc[i] else set()

    def momentum(top=10, regime=True):
        def f(i, sim):
            if not is_month_end.iloc[i]:
                return set(sim.pos)
            if regime and not nifty_up.iloc[i]:
                return set()
            m = mom_12_1.iloc[i][stocks].dropna()
            above = c.iloc[i][m.index] > sma200.iloc[i][m.index]
            m = m[above]
            return list(m.nlargest(top).index)
        return f

    def rsi2_pullback(max_pos=5, max_days=10):
        def f(i, sim):
            row, keep = c.iloc[i], set()
            for s in sim.pos:
                if row.get(s) > sma5.iloc[i].get(s) or i - sim.entry_i.get(s, i) >= max_days:
                    continue                              # exit: bounced above 5-day avg, or time stop
                keep.add(s)
            if not nifty_up.iloc[i]:
                return keep
            free = max_pos - len(keep)
            if free > 0:
                cand = r2.iloc[i]
                ok = (cand < 10) & (row[stocks] > sma200.iloc[i][stocks])
                picks = cand[ok].drop(labels=list(keep), errors="ignore").nsmallest(free).index
                return sorted(keep) + list(picks)
            return keep
        return f

    return [
        ("Benchmark: Nifty 50 ETF buy & hold", hold_etf, 1),
        ("Benchmark: same 100 stocks, equal-weight buy & hold", equal_weight_universe, len(stocks)),
        ("Nifty ETF only when Nifty > 200-day avg", nifty_trend, 1),
        ("Momentum top 10, monthly, with Nifty trend filter", momentum(10, True), 10),
        ("Momentum top 10, monthly, no filter", momentum(10, False), 10),
        ("Momentum top 20, monthly, with filter", momentum(20, True), 20),
        ("RSI(2) pullback, 5 positions, 2-10 day holds", rsi2_pullback(), 5),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()

    stocks = universe()
    data = fix_etf(load(stocks, a.refresh))
    stocks = [s for s in stocks if s in data["close"]]
    print(f"{len(stocks)} stocks with data; {data['close'].index[0]:%b %Y} to {data['close'].index[-1]:%d %b %Y}")

    rows = []
    for pname, (start, end) in PERIODS.items():
        for name, fn, slots in make_strategies(data, stocks):
            sim = Sim(data, start, end, a.capital, slots)
            curve = sim.run(fn)
            tsim = Sim(data, start, end, a.capital, slots, taxes=True)
            tsim.run(fn)
            slug = "".join(ch if ch.isalnum() else "_" for ch in f"{pname}_{name}")[:80]
            curve.to_csv(OUT / f"curve_{slug}.csv")
            rows.append({"period": pname, "strategy": name, **stats(curve, sim, a.capital, tsim)})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / f"results_{int(a.capital)}.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.max_colwidth", 52):
        for p in PERIODS:
            print(f"\n=== {p} | capital Rs {a.capital:,.0f} ===")
            print(res[res.period == p].drop(columns="period").to_string(index=False))


if __name__ == "__main__":
    main()
