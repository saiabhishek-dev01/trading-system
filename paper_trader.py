"""Paper-trading engine for NSE intraday equities.

Runs one session: polls 1-minute data, builds 5-minute bars, trades a
VWAP + EMA trend breakout strategy with fixed stop/target, applies the
risk engine (sized from investment_value), squares off at 15:15 IST and
writes a report. Create a file named KILL in this folder to stop and
close everything immediately.
"""
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
STATE_FILE = DATA / "state.json"
TRADES_FILE = DATA / "trades.csv"
LOG_FILE = DATA / "engine.log"
KILL_FILE = ROOT / "KILL"


def now_ist():
    return datetime.now(IST)


def log(msg):
    line = f"[{now_ist():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def hhmm(s):
    h, m = map(int, s.split(":"))
    return now_ist().replace(hour=h, minute=m, second=0, microsecond=0)


def tick(p):
    return round(round(p / 0.05) * 0.05, 2)


# ---------------------------------------------------------------- costs
def round_trip_charges(buy_value, sell_value):
    c = CFG["costs"]
    brokerage = (min(c["brokerage_cap_per_order"], buy_value * c["brokerage_pct"] / 100)
                 + min(c["brokerage_cap_per_order"], sell_value * c["brokerage_pct"] / 100))
    turnover = buy_value + sell_value
    stt = sell_value * c["stt_sell_pct"] / 100
    exch = turnover * c["exchange_txn_pct"] / 100
    sebi = turnover * c["sebi_per_crore"] / 1e7
    gst = (brokerage + exch + sebi) * c["gst_pct"] / 100
    stamp = buy_value * c["stamp_buy_pct"] / 100
    return round(brokerage + stt + exch + sebi + gst + stamp, 2)


def slip(price, side):
    """Adverse slippage: buys fill higher, sells fill lower."""
    s = CFG["costs"]["slippage_pct"] / 100
    return tick(price * (1 + s) if side == "BUY" else price * (1 - s))


# ---------------------------------------------------------------- data
def fetch():
    tickers = [f"{s}.NS" for s in CFG["universe"]]
    raw = yf.download(tickers, period="5d", interval="1m", group_by="ticker",
                      progress=False, threads=True, auto_adjust=False)
    out = {}
    for s in CFG["universe"]:
        try:
            df = raw[f"{s}.NS"][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        if df.empty:
            continue
        df.index = df.index.tz_convert(IST)
        out[s] = df
    return out


def to_5m_closed(df1):
    """Resample 1m to 5m bars and keep only bars whose last minute has printed."""
    bars = df1.resample("5min", label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    last_1m = df1.index[-1]
    return bars[bars.index + pd.Timedelta(minutes=4) < last_1m + pd.Timedelta(seconds=1)]


def indicators(b):
    st = CFG["strategy"]
    b = b.copy()
    b["ema_f"] = b["Close"].ewm(span=st["ema_fast"], adjust=False).mean()
    b["ema_s"] = b["Close"].ewm(span=st["ema_slow"], adjust=False).mean()
    d = b["Close"].diff()
    up = d.clip(lower=0).ewm(alpha=1 / st["rsi_period"], adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / st["rsi_period"], adjust=False).mean()
    b["rsi"] = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    pc = b["Close"].shift()
    tr = pd.concat([b["High"] - b["Low"], (b["High"] - pc).abs(), (b["Low"] - pc).abs()], axis=1).max(axis=1)
    b["atr"] = tr.ewm(alpha=1 / st["atr_period"], adjust=False).mean()
    day = b.index.date
    tp = (b["High"] + b["Low"] + b["Close"]) / 3
    pv = (tp * b["Volume"]).groupby(day).cumsum()
    vv = b["Volume"].groupby(day).cumsum()
    b["vwap"] = pv / vv.replace(0, math.nan)
    n = st["breakout_lookback_bars"]
    b["prev_high"] = b["High"].shift(1).rolling(n).max()
    b["prev_low"] = b["Low"].shift(1).rolling(n).min()
    # Opening range: high/low of the first orb_minutes of each session,
    # only defined on bars after the range is complete (no look-ahead).
    mins = b.index.hour * 60 + b.index.minute - (9 * 60 + 15)
    in_or = pd.Series(mins < st.get("orb_minutes", 15), index=b.index)
    b["or_high"] = b["High"].where(in_or).groupby(day).transform("max").where(~in_or)
    b["or_low"] = b["Low"].where(in_or).groupby(day).transform("min").where(~in_or)
    return b


def _exits(price, stop_dist):
    """Stop distance with a floor (so costs can't swamp it) and an R-multiple target."""
    st = CFG["strategy"]
    stop_dist = max(stop_dist, price * st["min_stop_pct"] / 100)
    return stop_dist, stop_dist * st["target_r"]


def _vwap_breakout(b):
    st = CFG["strategy"]
    r = b.iloc[-1]
    if any(pd.isna(r[k]) for k in ("ema_s", "rsi", "atr", "vwap", "prev_high")):
        return None
    side = None
    if (r.Close > r.vwap and r.ema_f > r.ema_s and r.Close > r.prev_high
            and st["long_rsi_min"] <= r.rsi <= st["long_rsi_max"]):
        side = "LONG"
    elif (r.Close < r.vwap and r.ema_f < r.ema_s and r.Close < r.prev_low
            and st["short_rsi_min"] <= r.rsi <= st["short_rsi_max"]):
        side = "SHORT"
    if not side:
        return None
    return (side, *_exits(r.Close, r.atr * st["stop_atr_mult"]))


def _orb(b):
    """First close beyond the opening range, in the direction of VWAP. Stop at range middle."""
    st = CFG["strategy"]
    if len(b) < 2:
        return None
    r, prev = b.iloc[-1], b.iloc[-2]
    ts = b.index[-1]
    h, m = map(int, st.get("orb_entry_until", "13:00").split(":"))
    if pd.isna(r.or_high) or pd.isna(r.vwap) or (ts.hour, ts.minute) >= (h, m):
        return None
    rng = r.or_high - r.or_low
    if rng / r.Close * 100 > st.get("orb_max_range_pct", 1.5):
        return None
    mid = (r.or_high + r.or_low) / 2
    first_bar_after_or = pd.isna(prev.or_high) or prev.name.date() != ts.date()
    if r.Close > r.or_high and r.Close > r.vwap and (first_bar_after_or or prev.Close <= r.or_high):
        return ("LONG", *_exits(r.Close, r.Close - mid))
    if r.Close < r.or_low and r.Close < r.vwap and (first_bar_after_or or prev.Close >= r.or_low):
        return ("SHORT", *_exits(r.Close, mid - r.Close))
    return None


STRATEGIES = {"vwap_trend_breakout_5m": _vwap_breakout, "orb": _orb}


def signal(b):
    """Returns (side, stop_distance, target_distance) or None."""
    return STRATEGIES[CFG["strategy"]["name"]](b)


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self):
        self.capital = float(CFG["investment_value"])
        self.realized = 0.0
        self.charges = 0.0
        self.positions = {}       # symbol -> dict
        self.closed = []
        self.entries_today = {}
        self.last_bar_seen = {}
        self.last_price = {}
        self.halted_reason = None
        self.started = now_ist()

    # ---- accounting
    def unrealized(self):
        u = 0.0
        for s, p in self.positions.items():
            px = self.last_price.get(s, p["entry"])
            u += (px - p["entry"]) * p["qty"] * (1 if p["side"] == "LONG" else -1)
        return u

    def blocked_cash(self):
        return sum(p["entry"] * p["qty"] for p in self.positions.values())

    def current_value(self):
        return self.capital + self.realized + self.unrealized()

    # ---- risk engine
    def size(self, symbol, price, stop_dist):
        r = CFG["risk"]
        if self.halted_reason:
            return 0, self.halted_reason
        if len(self.positions) >= r["max_open_positions"]:
            return 0, "max open positions"
        if self.entries_today.get(symbol, 0) >= r["max_entries_per_stock_per_day"]:
            return 0, "max entries for stock today"
        risk_rs = self.capital * r["risk_per_trade_pct"] / 100
        qty = int(risk_rs // stop_dist)
        max_exposure = self.capital * r["max_exposure_per_stock_pct"] / 100
        qty = min(qty, int(max_exposure // price))
        free_cash = self.capital + self.realized - self.blocked_cash()
        qty = min(qty, int(free_cash // price))
        if qty < 1:
            return 0, "size below 1 share"
        return qty, None

    def check_daily_loss(self):
        limit = self.capital * CFG["risk"]["daily_loss_limit_pct"] / 100
        if not self.halted_reason and (self.realized + self.unrealized()) <= -limit:
            self.halted_reason = f"daily loss limit Rs {limit:,.0f} hit"
            log(f"RISK: {self.halted_reason}; no new entries today")

    # ---- order handling
    def open(self, symbol, side, ref_price, stop_dist, target_dist, bar_time):
        fill = slip(ref_price, "BUY" if side == "LONG" else "SELL")
        qty, why = self.size(symbol, fill, stop_dist)
        if not qty:
            log(f"SKIP {side} {symbol}: {why}")
            return
        sgn = 1 if side == "LONG" else -1
        stop = tick(fill - sgn * stop_dist)
        target = tick(fill + sgn * target_dist)
        self.positions[symbol] = dict(side=side, qty=qty, entry=fill, stop=stop, target=target,
                                      entry_time=now_ist().isoformat(timespec="seconds"),
                                      signal_bar=str(bar_time), checked_from=None)
        self.entries_today[symbol] = self.entries_today.get(symbol, 0) + 1
        log(f"OPEN {side} {symbol} x{qty} @ {fill} | stop {stop} target {target} | value Rs {fill*qty:,.0f}")

    def close(self, symbol, exit_price, reason):
        p = self.positions.pop(symbol)
        sgn = 1 if p["side"] == "LONG" else -1
        gross = (exit_price - p["entry"]) * p["qty"] * sgn
        buy_v = (p["entry"] if sgn == 1 else exit_price) * p["qty"]
        sell_v = (exit_price if sgn == 1 else p["entry"]) * p["qty"]
        ch = round_trip_charges(buy_v, sell_v)
        net = gross - ch
        self.realized += net
        self.charges += ch
        rec = dict(symbol=symbol, side=p["side"], qty=p["qty"], entry=p["entry"], exit=exit_price,
                   stop=p["stop"], target=p["target"], entry_time=p["entry_time"],
                   exit_time=now_ist().isoformat(timespec="seconds"), reason=reason,
                   gross_pnl=round(gross, 2), charges=ch, net_pnl=round(net, 2))
        self.closed.append(rec)
        pd.DataFrame(self.closed).to_csv(TRADES_FILE, index=False)
        log(f"CLOSE {p['side']} {symbol} x{p['qty']} @ {exit_price} ({reason}) | gross {gross:+,.2f} charges {ch:.2f} net {net:+,.2f}")

    def manage(self, data):
        """Check stops/targets against each new 1m bar since entry."""
        for s in list(self.positions):
            df = data.get(s)
            if df is None:
                continue
            p = self.positions[s]
            entry_ts = pd.Timestamp(p["entry_time"])
            start = p["checked_from"] or entry_ts.floor("min") + pd.Timedelta(minutes=1)
            bars = df[df.index >= start]
            for ts, r in bars.iterrows():
                long = p["side"] == "LONG"
                hit_stop = r.Low <= p["stop"] if long else r.High >= p["stop"]
                hit_tgt = r.High >= p["target"] if long else r.Low <= p["target"]
                if hit_stop:   # conservative: stop wins if both in same bar
                    gap = r.Open < p["stop"] if long else r.Open > p["stop"]
                    px = r.Open if gap else p["stop"]
                    self.close(s, slip(px, "SELL" if long else "BUY"), "stop loss")
                    break
                if hit_tgt:
                    self.close(s, p["target"], "target")
                    break
                p["checked_from"] = ts   # re-check the latest (possibly partial) bar next loop

    def square_off_all(self, reason):
        for s in list(self.positions):
            p = self.positions[s]
            px = self.last_price.get(s, p["entry"])
            self.close(s, slip(px, "SELL" if p["side"] == "LONG" else "BUY"), reason)

    def save_state(self, status):
        st = dict(
            status=status, mode=CFG["mode"], updated=now_ist().isoformat(timespec="seconds"),
            investment_value=self.capital, current_value=round(self.current_value(), 2),
            realized_net=round(self.realized, 2), unrealized=round(self.unrealized(), 2),
            charges=round(self.charges, 2), halted=self.halted_reason,
            open_positions={s: {**p, "ltp": self.last_price.get(s)} for s, p in self.positions.items()},
            closed_trades=len(self.closed))
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, default=str))
        tmp.replace(STATE_FILE)

    # ---- main loop
    def run(self):
        cutoff = hhmm(CFG["session"]["no_new_entries_after"])
        square = hhmm(CFG["session"]["square_off_at"])
        log(f"Paper session start. Investment value Rs {self.capital:,.0f}. "
            f"Entries until {cutoff:%H:%M}, square-off {square:%H:%M}.")
        while True:
            t = now_ist()
            if KILL_FILE.exists():
                log("KILL file found: squaring off everything")
                self.square_off_all("kill switch")
                break
            try:
                data = fetch()
            except Exception as e:
                log(f"data fetch failed: {e!r}")
                data = {}
            for s, df in data.items():
                self.last_price[s] = float(df["Close"].iloc[-1])
            if data:
                self.manage(data)
                self.check_daily_loss()
            if t >= square:
                self.square_off_all("intraday square-off 15:15")
                break
            if data and t < cutoff:
                for s, df in data.items():
                    if s in self.positions:
                        continue
                    b = to_5m_closed(df)
                    if len(b) < 30 or b.index[-1].date() != t.date():
                        continue
                    last = b.index[-1]
                    if self.last_bar_seen.get(s) == last:
                        continue
                    self.last_bar_seen[s] = last
                    sig = signal(indicators(b))
                    if sig:
                        side, stop_dist, target_dist = sig
                        self.open(s, side, self.last_price[s], stop_dist, target_dist, last)
            self.save_state("running")
            log(f"value Rs {self.current_value():,.2f} | open {len(self.positions)} | "
                f"realized {self.realized:+,.2f} | unrealized {self.unrealized():+,.2f}")
            time.sleep(max(5, CFG["session"]["poll_seconds"] - (now_ist() - t).total_seconds()))
        self.save_state("finished")
        self.report()

    def report(self):
        n = len(self.closed)
        wins = sum(1 for c in self.closed if c["net_pnl"] > 0)
        gross = sum(c["gross_pnl"] for c in self.closed)
        lines = [
            f"# Paper trading report: {now_ist():%d %b %Y}",
            "",
            f"Session: {self.started:%H:%M} to {now_ist():%H:%M} IST, strategy {CFG['strategy']['name']}",
            f"Investment value: Rs {self.capital:,.2f}",
            f"Final value:      Rs {self.current_value():,.2f}",
            f"Net P&L:          Rs {self.realized:+,.2f} ({self.realized / self.capital * 100:+.3f}%)",
            f"Gross P&L:        Rs {gross:+,.2f}",
            f"Charges:          Rs {self.charges:,.2f}",
            f"Trades: {n}, winners {wins}, losers {n - wins}",
            f"Risk halt: {self.halted_reason or 'none'}",
            "",
        ]
        if n:
            lines.append(pd.DataFrame(self.closed)[["symbol", "side", "qty", "entry", "exit", "reason",
                                                    "gross_pnl", "charges", "net_pnl"]].to_string(index=False))
        text = "\n".join(lines)
        (DATA / f"report_{now_ist():%Y%m%d}.md").write_text(text, encoding="utf-8")
        print("\n" + text, flush=True)


if __name__ == "__main__":
    if KILL_FILE.exists():
        sys.exit("Remove the KILL file before starting a session.")
    Engine().run()
