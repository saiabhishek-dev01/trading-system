"""Broker connectors. The trading engine talks to one of these and never knows which.

PaperBroker  simulated fills at live (Yahoo, ~1-2 min delayed) prices, plus slippage
             and full Zerodha delivery charges. No account needed.
KiteBroker   real orders on the account holder's Zerodha account via Kite Connect.

Real-money safety gates (all must pass before KiteBroker sends an order)
  1. .env has LIVE_TRADING_ENABLED=yes          (set by a human on the server)
  2. config.json live.enabled is true           (switched on in the dashboard, with confirmation)
  3. a Kite session from today's manual login   (exchange rule: a human logs in daily)
  4. no KILL file
  5. order value <= live.max_order_value
Kite rules followed: LIMIT orders only (plain market orders are rejected over the API),
order calls must come from the static IP registered in the Kite developer console,
well under 10 orders/second so no exchange algo registration is needed.

Credentials live only in .env (gitignored):
  KITE_API_KEY=...  KITE_API_SECRET=...  LIVE_TRADING_ENABLED=no
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SESSION = DATA / "kite_session.json"
IST = timezone(timedelta(hours=5, minutes=30))

# Zerodha delivery (CNC) charges, same as swing_backtest.COST
COST = dict(stt=0.001, stamp_buy=0.00015, exch=0.0000297, sebi=10 / 1e7, gst=0.18,
            dp_per_sell=15.93)
SLIPPAGE = 0.001          # paper fills: 0.1% worse than the quoted price
LIMIT_BUFFER = 0.005      # live: limit price 0.5% through the last price, so it fills like a market order


def now_ist():
    return datetime.now(IST)


def env():
    vals = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    vals.update({k: v for k, v in os.environ.items() if k.startswith(("KITE_", "LIVE_", "TELEGRAM_"))})
    return vals


def delivery_charges(value, side):
    c = COST
    x = value * (c["stt"] + c["exch"] + c["sebi"]) + value * (c["exch"] + c["sebi"]) * c["gst"]
    return x + (value * c["stamp_buy"] if side == "BUY" else c["dp_per_sell"])


def tick(p):
    return round(round(p / 0.05) * 0.05, 2)


def yahoo_ltp(symbols):
    if not symbols:
        return {}
    raw = yf.download([f"{s}.NS" for s in symbols], period="1d", interval="1m",
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for s in symbols:
        try:
            v = raw[f"{s}.NS"]["Close"].dropna()
            if len(v):
                out[s] = float(v.iloc[-1])
        except KeyError:
            pass
    return out


class PaperBroker:
    name, live = "PAPER", False

    def status(self):
        return True, "Simulated fills on Yahoo prices (about 1-2 min delayed). No account needed."

    def ltp(self, symbols):
        return yahoo_ltp(symbols)

    def funds(self):
        return None                      # paper cash is tracked by the engine's ledger

    def holdings(self):
        return None

    def place(self, symbol, side, qty, ref_price, cfg=None):
        px = tick(ref_price * (1 + SLIPPAGE if side == "BUY" else 1 - SLIPPAGE))
        value = qty * px
        return dict(status="COMPLETE", symbol=symbol, side=side, qty=qty, price=px,
                    charges=round(delivery_charges(value, side), 2), order_id=None,
                    time=now_ist().strftime("%Y-%m-%d %H:%M:%S"))


class KiteBroker:
    name, live = "LIVE", True

    def __init__(self):
        e = env()
        self.key, self.secret = e.get("KITE_API_KEY"), e.get("KITE_API_SECRET")
        self.env_enabled = e.get("LIVE_TRADING_ENABLED", "no").lower() == "yes"
        self.kite = None
        try:
            from kiteconnect import KiteConnect
            if self.key:
                self.kite = KiteConnect(api_key=self.key)
        except ImportError:
            pass
        s = self._session()
        if self.kite and s:
            self.kite.set_access_token(s["access_token"])

    # ---------------------------------------------------------- daily login
    def _session(self):
        """Today's access token, if the account holder has logged in today."""
        if not SESSION.exists():
            return None
        s = json.loads(SESSION.read_text(encoding="utf-8"))
        # Kite tokens expire early each morning; treat a session as valid for the IST date it was made
        return s if s.get("date") == str(now_ist().date()) else None

    def login_url(self):
        return self.kite.login_url() if self.kite else None

    def complete_login(self, request_token):
        d = self.kite.generate_session(request_token, api_secret=self.secret)
        SESSION.write_text(json.dumps(dict(date=str(now_ist().date()), access_token=d["access_token"],
                                           user_id=d.get("user_id"), user_name=d.get("user_name"))),
                           encoding="utf-8")
        self.kite.set_access_token(d["access_token"])
        return d.get("user_name") or d.get("user_id")

    def status(self):
        if self.kite is None:
            return False, ("Add KITE_API_KEY and KITE_API_SECRET to .env" if not self.key
                           else "kiteconnect is not installed (pip install kiteconnect)")
        if not self._session():
            return False, "Not logged in today. Use the Zerodha login button."
        try:
            p = self.kite.profile()
            return True, f"Logged in as {p.get('user_name')} ({p.get('user_id')})"
        except Exception as ex:                      # token revoked/expired
            return False, f"Kite session invalid: {ex}"

    # ---------------------------------------------------------- data
    def ltp(self, symbols):
        q = self.kite.ltp([f"NSE:{s}" for s in symbols]) if symbols else {}
        return {k.split(":", 1)[1]: v["last_price"] for k, v in q.items()}

    def funds(self):
        return float(self.kite.margins("equity")["net"])

    def holdings(self):
        out = {}
        for h in self.kite.holdings():
            out[h["tradingsymbol"]] = out.get(h["tradingsymbol"], 0) + h["quantity"] + h.get("t1_quantity", 0)
        return out

    # ---------------------------------------------------------- orders
    def _gate(self, value, cfg):
        live = (cfg or {}).get("live", {})
        if not self.env_enabled:
            raise PermissionError("LIVE_TRADING_ENABLED is not 'yes' in .env")
        if not live.get("enabled"):
            raise PermissionError("Live trading is switched off in the dashboard")
        if (ROOT / "KILL").exists():
            raise PermissionError("Kill switch is on")
        if not self._session():
            raise PermissionError("No Kite login today")
        if value > live.get("max_order_value", 0):
            raise PermissionError(f"Order value {value:,.0f} exceeds max_order_value")

    def place(self, symbol, side, qty, ref_price, cfg=None):
        k = self.kite
        limit = tick(ref_price * (1 + LIMIT_BUFFER if side == "BUY" else 1 - LIMIT_BUFFER))
        self._gate(qty * limit, cfg)
        oid = k.place_order(variety=k.VARIETY_REGULAR, exchange=k.EXCHANGE_NSE, tradingsymbol=symbol,
                            transaction_type=k.TRANSACTION_TYPE_BUY if side == "BUY" else k.TRANSACTION_TYPE_SELL,
                            quantity=int(qty), product=k.PRODUCT_CNC, order_type=k.ORDER_TYPE_LIMIT,
                            price=limit, validity=k.VALIDITY_DAY, tag="momentum")
        last = {}
        for _ in range(30):                          # wait up to ~60 s for the fill
            time.sleep(2)
            last = k.order_history(oid)[-1]
            if last["status"] in ("COMPLETE", "REJECTED", "CANCELLED"):
                break
        else:
            k.cancel_order(variety=k.VARIETY_REGULAR, order_id=oid)
            time.sleep(2)
            last = k.order_history(oid)[-1]
        filled = int(last.get("filled_quantity") or 0)
        px = float(last.get("average_price") or 0)
        return dict(status=last.get("status", "UNKNOWN") if filled == qty else ("PARTIAL" if filled else last.get("status")),
                    symbol=symbol, side=side, qty=filled, price=px,
                    charges=round(delivery_charges(filled * px, side), 2), order_id=oid,
                    message=last.get("status_message"), time=now_ist().strftime("%Y-%m-%d %H:%M:%S"))


def get_broker(mode):
    return KiteBroker() if mode == "live" else PaperBroker()
