"""Auto-trader: monthly momentum, run end to end through a broker connector.

Same engine for both modes; only the broker differs.
  python autotrader.py --mode paper          # loop forever (every 5 min), simulated money
  python autotrader.py --mode live           # loop forever, real orders via Kite (gated, see brokers.py)
  python autotrader.py --mode paper --once   # single pass

What a pass does
  1. If a new month's first session has closed: rank the Nifty 100 by 12-1 month return,
     keep the top N above their 200-day average (all cash if the Nifty is below its own
     200-day average). This becomes the pending target.
  2. During market hours on a later day: sell what left the target, buy what joined,
     equal weight (equity / N each), whole shares, through the broker.
  3. After the close: record the day's value next to a Nifty 50 index-fund benchmark that
     started with the same money on the same day. In live mode, compare the engine's
     positions with Zerodha's holdings and flag any difference.
Stops when data/auto/<mode>/STOP or the global KILL file exists.

Why this strategy: it was the only one with a (small) edge in docs/PROFITABILITY_RESEARCH.md.
Intraday trading stays paper-only (paper_trader.py) because it lost money in every test.
"""
import argparse
import json
import math
import time
import traceback

import pandas as pd
import yfinance as yf

import alerts
import swing_backtest as sb
from brokers import IST, ROOT, get_broker, now_ist

CONFIG = ROOT / "config.json"
INDEX = "^NSEI"
CASH_YIELD = 0.06            # paper idle cash earns a liquid-fund rate


def cfg_load():
    return json.loads(CONFIG.read_text(encoding="utf-8-sig"))


def paths(mode):
    d = ROOT / "data" / "auto" / mode
    d.mkdir(parents=True, exist_ok=True)
    return dict(dir=d, state=d / "state.json", log=d / "engine.log", stop=d / "STOP")


def log(mode, msg):
    line = f"{now_ist():%Y-%m-%d %H:%M:%S} {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:                  # Windows console without UTF-8
        print(line.encode("ascii", "replace").decode(), flush=True)
    with open(paths(mode)["log"], "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load(mode):
    p = paths(mode)["state"]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(mode, s):
    p = paths(mode)["state"]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    tmp.replace(p)


def new_state(mode, cfg):
    cap = float(cfg["investment_value"] if mode == "paper" else cfg["live"]["investment_value"])
    return dict(mode=mode, capital=cap, cash=cap, positions={}, charges=0.0, trades=[], curve=[],
                bench_units=None, started=None, pending=None, last_decision=None, reconcile=[],
                heartbeat=None, last_error=None)


# ------------------------------------------------------------------ market data
def daily_closes(symbols):
    """Daily closes of finished sessions only (today's bar is dropped until 15:35 IST)."""
    tick = [f"{s}.NS" for s in symbols] + [INDEX]
    raw = yf.download(tick, period="2y", interval="1d", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    c = {}
    for t in tick:
        try:
            c[t.replace(".NS", "")] = raw[t]["Close"]
        except KeyError:
            pass
    c = pd.DataFrame(c)
    c = c.reindex(c[INDEX].dropna().index).where(lambda x: x > 0)
    n = now_ist()
    if len(c) and c.index[-1].date() == n.date() and n.hour * 60 + n.minute < 15 * 60 + 35:
        c = c.iloc[:-1]
    return c


def market_open():
    """True only during 9:20-15:20 IST on a day the NSE is actually trading (checked via
    fresh index ticks, which also catches exchange holidays)."""
    n = now_ist()
    if n.weekday() >= 5 or not (9 * 60 + 20 <= n.hour * 60 + n.minute <= 15 * 60 + 20):
        return False
    bars = yf.download(INDEX, period="1d", interval="1m", progress=False)
    if bars.empty:
        return False
    last = bars.index[-1].tz_convert(IST) if bars.index.tz is not None else bars.index[-1]
    return last.date() == n.date()


# ------------------------------------------------------------------ strategy
def decide(c, equity, slots, regime_filter=True):
    nifty = c[INDEX]
    if regime_filter and nifty.iloc[-1] <= nifty.rolling(200).mean().iloc[-1]:
        return [], "Nifty below its 200-day average: hold cash"
    stocks = [s for s in c.columns if s != INDEX]
    last = c[stocks].iloc[-1]
    mom = (c[stocks].shift(21) / c[stocks].shift(252) - 1).iloc[-1]
    sma = c[stocks].rolling(200).mean().iloc[-1]
    ok = mom.notna() & (last > sma) & (last * 1.01 <= equity / slots)      # at least one share affordable
    why = "Nifty above its 200-day average: invested" if regime_filter else "Regime filter off: invested"
    return list(mom[ok].nlargest(slots).index), why


def equity_at(s, prices):
    return s["cash"] + sum(p["shares"] * prices.get(sym, p["entry"]) for sym, p in s["positions"].items())


# ------------------------------------------------------------------ execution
def execute(mode, s, broker, cfg):
    slots = cfg["auto"]["slots"]
    target = s["pending"]["target"]
    held = list(s["positions"])
    q = broker.ltp(sorted(set(target) | set(held)))
    today = str(now_ist().date())

    for sym in [x for x in held if x not in target]:
        p = s["positions"][sym]
        r = broker.place(sym, "SELL", p["shares"], q[sym], cfg)
        if r["qty"] <= 0:
            log(mode, f"SELL {sym} not filled: {r.get('status')} {r.get('message') or ''}")
            continue
        proceeds = r["qty"] * r["price"] - r["charges"]
        cost_part = p["cost"] * r["qty"] / p["shares"]
        s["cash"] += proceeds
        s["charges"] += r["charges"]
        p["shares"] -= r["qty"]
        p["cost"] -= cost_part
        if p["shares"] == 0:
            del s["positions"][sym]
        s["trades"].append(dict(r, pnl=round(proceeds - cost_part, 2)))
        log(mode, f"SELL {sym} x{r['qty']} @ {r['price']} pnl {proceeds - cost_part:+,.0f}")

    eq = equity_at(s, q)
    free = s["cash"] if not broker.live else min(s["cash"], broker.funds())
    for sym in target:
        if sym in s["positions"] or len(s["positions"]) >= slots or sym not in q:
            continue
        budget = min(eq / slots, free)
        if broker.live:                                       # stay inside the per-order cap
            budget = min(budget, cfg["live"]["max_order_value"] / 1.01)
        n = math.floor(budget / (q[sym] * 1.0075))            # room for limit buffer, slippage, charges
        if n < 1:
            continue
        r = broker.place(sym, "BUY", n, q[sym], cfg)
        if r["qty"] <= 0:
            log(mode, f"BUY {sym} not filled: {r.get('status')} {r.get('message') or ''}")
            continue
        spent = r["qty"] * r["price"] + r["charges"]
        s["cash"] -= spent
        free -= spent
        s["charges"] += r["charges"]
        s["positions"][sym] = dict(shares=r["qty"], entry=r["price"], entry_date=today, cost=round(spent, 2))
        s["trades"].append(dict(r, pnl=None))
        log(mode, f"BUY {sym} x{r['qty']} @ {r['price']}")

    if s["bench_units"] is None:
        nifty = yf.download(INDEX, period="1d", interval="1m", progress=False)["Close"].dropna()
        s["bench_units"] = s["capital"] / float(nifty.iloc[-1].squeeze())
        s["started"] = today
    s["pending"] = None
    msg = (f"[{mode.upper()}] Rebalanced: holding {', '.join(s['positions']) or 'cash only'}. "
           f"Cash ₹{s['cash']:,.0f}")
    log(mode, msg)
    alerts.send(msg)


def mark_to_market(mode, s, c, broker):
    if not s["started"]:
        return
    last_seen = pd.Timestamp(s["curve"][-1]["date"]) if s["curve"] else pd.Timestamp(s["started"]) - pd.Timedelta(days=1)
    new = [d for d in c.index if d > last_seen and d >= pd.Timestamp(s["started"])]
    for d in new:
        if s["curve"] and mode == "paper":
            gap = (d - pd.Timestamp(s["curve"][-1]["date"])).days
            s["cash"] += s["cash"] * CASH_YIELD * gap / 365
        row = c.loc[d]
        s["curve"].append(dict(date=str(d.date()), equity=round(equity_at(s, row.to_dict()), 2),
                               nifty=round(float(s["bench_units"] * row[INDEX]), 2)))
    if len(c):
        s["last_prices"] = {sym: round(float(c.iloc[-1][sym]), 2) for sym in s["positions"]
                            if sym in c.columns and pd.notna(c.iloc[-1][sym])}
        s["prices_as_of"] = str(c.index[-1].date())
    if new:
        last = s["curve"][-1]
        msg = (f"[{mode.upper()}] {last['date']}: ₹{last['equity']:,.0f} "
               f"({last['equity'] / s['capital'] - 1:+.2%}) vs Nifty ₹{last['nifty']:,.0f} "
               f"({last['nifty'] / s['capital'] - 1:+.2%})")
        log(mode, msg)
        alerts.send(msg)
        if broker.live:                                       # engine book vs Zerodha's
            h = broker.holdings()
            s["reconcile"] = [f"{sym}: engine {p['shares']}, Zerodha {h.get(sym, 0)}"
                              for sym, p in s["positions"].items() if h.get(sym, 0) < p["shares"]]
            if s["reconcile"]:
                alerts.send(f"[LIVE] Position mismatch: {'; '.join(s['reconcile'])}")


def run_once(mode):
    cfg = cfg_load()
    broker = get_broker(mode)
    ok, why = broker.status()
    if ok and mode == "live" and not load(mode) and cfg["live"]["investment_value"] <= 0:
        ok, why = False, "Set the live investment value in the dashboard first"
    if not ok:
        log(mode, f"Broker not ready: {why}")
        s = load(mode)
        if s:                                   # keep the dashboard informed; never create a portfolio here
            s["heartbeat"], s["last_error"] = now_ist().strftime("%Y-%m-%d %H:%M:%S"), why
            save(mode, s)
        return s
    s = load(mode) or new_state(mode, cfg)
    s["heartbeat"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
    try:
        c = daily_closes(sorted(set(sb.universe()) | set(s["positions"])))
        last = c.index[-1]
        if s["pending"] is None and (s["last_decision"] is None
                                     or last.strftime("%Y-%m") != s["last_decision"][:7]):
            eq = equity_at(s, c.iloc[-1].to_dict())
            target, reason = decide(c, eq, cfg["auto"]["slots"], cfg["auto"].get("regime_filter", True))
            s["pending"] = dict(decided=str(last.date()), target=target, reason=reason)
            s["last_decision"] = str(last.date())
            log(mode, f"New target from {last.date()} close ({reason}): {', '.join(target) or 'cash'}")
        if s["pending"] and str(now_ist().date()) > s["pending"]["decided"] and market_open():
            execute(mode, s, broker, cfg)
        mark_to_market(mode, s, c, broker)
        s["last_error"] = None
    except PermissionError as ex:                              # a live safety gate said no
        s["last_error"] = f"Blocked by safety gate: {ex}"
        log(mode, s["last_error"])
    except Exception as ex:
        s["last_error"] = f"{type(ex).__name__}: {ex}"
        log(mode, traceback.format_exc())
        alerts.send(f"[{mode.upper()}] Error: {s['last_error']}")
    save(mode, s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    p = paths(a.mode)
    p["stop"].unlink(missing_ok=True)
    log(a.mode, f"Auto-trader started ({a.mode})")
    while True:
        run_once(a.mode)
        if a.once:
            break
        for _ in range(cfg_load()["auto"]["loop_seconds"]):
            if p["stop"].exists() or (ROOT / "KILL").exists():
                log(a.mode, "Stopped (STOP or KILL file)")
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
