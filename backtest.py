"""Historical simulation of the intraday strategy on 5-minute NSE bars.

Uses the same signal, indicator, risk-sizing and charges code as the live
paper engine. Rules that keep it honest:
  * signal on a closed bar, fill at the NEXT bar's open plus slippage
  * stop and target checked on later bars; if both are touched in one bar,
    the stop is assumed to have filled first
  * full Zerodha intraday charges on every round trip
  * no entries at or after 15:00, everything squared off at 15:15
  * daily loss limit and position caps from config.json

Usage:
  python backtest.py                       # baseline, last ~60 days
  python backtest.py --sweep               # baseline + parameter variants
  python backtest.py --capital 200000
"""
import argparse
import copy
import json
from datetime import time as dtime
from pathlib import Path

import pandas as pd
import yfinance as yf

import paper_trader as pt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "backtests"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = ROOT / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)


def load_history(symbols, period="60d", refresh=False):
    """5-minute bars for each symbol, cached per day so sweeps don't re-download."""
    cache = CACHE / f"5m_{period}_{pd.Timestamp.now(tz=pt.IST):%Y%m%d}.pkl"
    if cache.exists() and not refresh:
        data = pd.read_pickle(cache)
        if set(symbols) <= set(data):
            return {s: data[s] for s in symbols}
    raw = yf.download([f"{s}.NS" for s in symbols], period=period, interval="5m",
                      group_by="ticker", progress=False, threads=True, auto_adjust=False)
    data = {}
    for s in symbols:
        try:
            df = raw[f"{s}.NS"][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        if not df.empty:
            df.index = df.index.tz_convert(pt.IST)
            data[s] = df
    pd.to_pickle(data, cache)
    return data


def simulate(data, cfg, capital=None, only_days=None):
    """Run the strategy day by day across all symbols. Returns (trades, daily, summary).
    only_days: optional set of date strings 'YYYY-MM-DD' to replay (earlier days still warm up indicators)."""
    saved = copy.deepcopy(pt.CFG)
    pt.CFG.clear()
    pt.CFG.update(cfg)
    try:
        return _simulate(data, cfg, capital or cfg["investment_value"], only_days)
    finally:
        pt.CFG.clear()
        pt.CFG.update(saved)


def _simulate(data, cfg, start_capital, only_days=None):
    st, rk = cfg["strategy"], cfg["risk"]
    cutoff = dtime(*map(int, cfg["session"]["no_new_entries_after"].split(":")))
    sq = dtime(*map(int, cfg["session"]["square_off_at"].split(":")))

    ind = {s: pt.indicators(df) for s, df in data.items()}
    days = sorted({d for df in ind.values() for d in df.index.date})
    # First day only warms up the indicators (EMA/RSI/ATR need history).
    days = days[1:]
    if only_days:
        days = [d for d in days if str(d) in only_days]

    equity = float(start_capital)
    trades, daily = [], []

    for day in days:
        bars = {s: df[df.index.date == day] for s, df in ind.items()}
        times = sorted({t for b in bars.values() for t in b.index})
        positions, entries, day_pnl, halted = {}, {}, 0.0, False
        pending = {}  # symbol -> (side, atr) to fill at next bar open

        def close(sym, px, reason, ts):
            nonlocal day_pnl
            p = positions.pop(sym)
            sgn = 1 if p["side"] == "LONG" else -1
            gross = (px - p["entry"]) * p["qty"] * sgn
            buy_v = (p["entry"] if sgn == 1 else px) * p["qty"]
            sell_v = (px if sgn == 1 else p["entry"]) * p["qty"]
            ch = pt.round_trip_charges(buy_v, sell_v)
            day_pnl += gross - ch
            trades.append(dict(date=str(day), symbol=sym, side=p["side"], qty=p["qty"],
                               entry_time=p["t"].strftime("%H:%M"), exit_time=ts.strftime("%H:%M"),
                               entry=p["entry"], exit=px, reason=reason,
                               gross_pnl=round(gross, 2), charges=ch, net_pnl=round(gross - ch, 2)))

        for ts in times:
            tod = ts.time()
            # 1) fill orders queued on the previous bar's signal, at this bar's open
            for sym, (side, stop_dist, target_dist) in list(pending.items()):
                pending.pop(sym)
                b = bars[sym]
                if ts not in b.index or halted or tod >= cutoff:
                    continue
                if len(positions) >= rk["max_open_positions"]:
                    continue
                if entries.get(sym, 0) >= rk["max_entries_per_stock_per_day"]:
                    continue
                o = float(b.at[ts, "Open"])
                fill = pt.slip(o, "BUY" if side == "LONG" else "SELL")
                qty = int((equity * rk["risk_per_trade_pct"] / 100) // stop_dist)
                qty = min(qty, int((equity * rk["max_exposure_per_stock_pct"] / 100) // fill))
                used = sum(p["entry"] * p["qty"] for p in positions.values())
                qty = min(qty, int((equity + day_pnl - used) // fill))
                if qty < 1:
                    continue
                sgn = 1 if side == "LONG" else -1
                positions[sym] = dict(side=side, qty=qty, entry=fill, t=ts,
                                      stop=pt.tick(fill - sgn * stop_dist),
                                      target=pt.tick(fill + sgn * target_dist))
                entries[sym] = entries.get(sym, 0) + 1

            # 2) manage open positions on this bar (including the entry bar)
            for sym in list(positions):
                b = bars[sym]
                if ts not in b.index:
                    continue
                r = b.loc[ts]
                p = positions[sym]
                long = p["side"] == "LONG"
                if (r.Low <= p["stop"]) if long else (r.High >= p["stop"]):
                    gap = (r.Open < p["stop"]) if long else (r.Open > p["stop"])
                    px = float(r.Open) if gap and ts != p["t"] else p["stop"]
                    close(sym, pt.slip(px, "SELL" if long else "BUY"), "stop loss", ts)
                elif (r.High >= p["target"]) if long else (r.Low <= p["target"]):
                    close(sym, p["target"], "target", ts)

            # 3) daily loss limit (realized + marked-to-close)
            unreal = 0.0
            for sym, p in positions.items():
                if ts in bars[sym].index:
                    c = float(bars[sym].at[ts, "Close"])
                    unreal += (c - p["entry"]) * p["qty"] * (1 if p["side"] == "LONG" else -1)
            if not halted and day_pnl + unreal <= -equity * rk["daily_loss_limit_pct"] / 100:
                halted = True

            # 4) square-off: bar starting 15:10 closes at 15:15
            bar_end = (ts + pd.Timedelta(minutes=5)).time()
            if bar_end >= sq:
                for sym in list(positions):
                    b = bars[sym]
                    c = float(b.loc[:ts, "Close"].iloc[-1])
                    close(sym, pt.slip(c, "SELL" if positions[sym]["side"] == "LONG" else "BUY"),
                          "square-off 15:15", ts + pd.Timedelta(minutes=5))
                break

            # 5) new signals on this closed bar -> fill next bar
            if halted or bar_end >= cutoff:
                continue
            for sym, b in bars.items():
                if sym in positions or sym in pending or ts not in b.index:
                    continue
                full = ind[sym].loc[:ts]
                if len(full) < 30:
                    continue
                sig = pt.signal(full)
                if sig:
                    pending[sym] = sig

        equity += day_pnl
        daily.append(dict(date=str(day), net_pnl=round(day_pnl, 2), equity=round(equity, 2)))

    tdf, ddf = pd.DataFrame(trades), pd.DataFrame(daily)
    return tdf, ddf, summarize(tdf, ddf, start_capital)


def summarize(tdf, ddf, capital):
    if tdf.empty:
        return dict(trades=0, net_pnl=0.0, return_pct=0.0, days=len(ddf))
    eq = pd.concat([pd.Series([capital]), ddf["equity"]]).reset_index(drop=True)
    dd = (eq / eq.cummax() - 1).min() * 100
    wins = tdf[tdf.net_pnl > 0]
    losses = tdf[tdf.net_pnl <= 0]
    pf = wins.net_pnl.sum() / abs(losses.net_pnl.sum()) if len(losses) and losses.net_pnl.sum() else float("inf")
    return dict(
        days=len(ddf), trades=len(tdf),
        net_pnl=round(tdf.net_pnl.sum(), 2), gross_pnl=round(tdf.gross_pnl.sum(), 2),
        charges=round(tdf.charges.sum(), 2),
        return_pct=round(tdf.net_pnl.sum() / capital * 100, 2),
        win_rate_pct=round(len(wins) / len(tdf) * 100, 1),
        expectancy_per_trade=round(tdf.net_pnl.mean(), 2),
        avg_win=round(wins.net_pnl.mean(), 2) if len(wins) else 0.0,
        avg_loss=round(losses.net_pnl.mean(), 2) if len(losses) else 0.0,
        profit_factor=round(pf, 2),
        max_drawdown_pct=round(dd, 2),
        green_days=int((ddf.net_pnl > 0).sum()), red_days=int((ddf.net_pnl < 0).sum()),
        best_day=round(ddf.net_pnl.max(), 2), worst_day=round(ddf.net_pnl.min(), 2),
    )


VWAP = {"name": "vwap_trend_breakout_5m"}
ORB = {"name": "orb"}
ONE_A_DAY = {"max_entries_per_stock_per_day": 1}
SWEEP = [
    # (label, {"strategy": {...}, "risk": {...}, "costs": {...}})
    ("vwap 5m baseline", {"strategy": VWAP}),
    ("vwap 5m, stop>=0.5%, 2R, 1/stock/day",
     {"strategy": {**VWAP, "min_stop_pct": 0.5, "target_r": 2.0}, "risk": ONE_A_DAY}),
    ("vwap 5m, zero slippage (cost check)", {"strategy": VWAP, "costs": {"slippage_pct": 0.0}}),
    ("ORB 15m, 1.5R", {"strategy": {**ORB, "min_stop_pct": 0.3, "target_r": 1.5}, "risk": ONE_A_DAY}),
    ("ORB 15m, 2R", {"strategy": {**ORB, "min_stop_pct": 0.3, "target_r": 2.0}, "risk": ONE_A_DAY}),
    ("ORB 30m, 2R", {"strategy": {**ORB, "orb_minutes": 30, "min_stop_pct": 0.3, "target_r": 2.0},
                     "risk": ONE_A_DAY}),
    ("ORB 15m, 2R, slippage 0.02%", {"strategy": {**ORB, "min_stop_pct": 0.3, "target_r": 2.0},
                                     "risk": ONE_A_DAY, "costs": {"slippage_pct": 0.02}}),
]


def apply(base, over):
    cfg = copy.deepcopy(base)
    for k, v in over.items():
        cfg[k].update(v)
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()

    base = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    data = load_history(base["universe"], refresh=a.refresh)
    print(f"Loaded {len(data)} symbols, "
          f"{min(df.index[0] for df in data.values()):%d %b} to {max(df.index[-1] for df in data.values()):%d %b %Y}")

    rows = []
    for name, over in (SWEEP if a.sweep else SWEEP[:1]):
        cfg = apply(base, over)
        tdf, ddf, s = simulate(data, cfg, a.capital)
        slug = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
        tdf.to_csv(OUT / f"trades_{slug}.csv", index=False)
        ddf.to_csv(OUT / f"daily_{slug}.csv", index=False)
        rows.append({"variant": name, **s})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "summary.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(res.to_string(index=False))


if __name__ == "__main__":
    main()
