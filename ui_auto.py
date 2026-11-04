"""Auto-trader pages: overview, paper account, real-money account, demo replay."""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

import autotrader as at
import ui_style as ui
from brokers import IST, KiteBroker, now_ist
from ui_style import inr, inr0

ROOT = at.ROOT


def load_cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


def save_cfg(cfg):
    (ROOT / "config.json").write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def handle_kite_redirect():
    """Kite sends the browser back to the dashboard with ?request_token=... after login."""
    tok = st.query_params.get("request_token")
    if tok:
        try:
            who = KiteBroker().complete_login(tok)
            st.toast(f"Zerodha login complete: {who}", icon="✅")
        except Exception as ex:
            st.error(f"Zerodha login failed: {ex}")
        st.query_params.clear()


# ------------------------------------------------------------------ state helpers
def running(mode, s=None, cfg=None):
    s = s if s is not None else at.load(mode)
    cfg = cfg or load_cfg()
    if not s or not s.get("heartbeat"):
        return False
    hb = datetime.strptime(s["heartbeat"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return (now_ist() - hb).total_seconds() < cfg["auto"]["loop_seconds"] + 180 \
        and not at.paths(mode)["stop"].exists()


def summary(s):
    """Headline numbers for a portfolio state."""
    cap = s["capital"] or 1
    lp = s.get("last_prices") or {}
    invested = sum(p["shares"] * lp.get(sym, p["entry"]) for sym, p in s["positions"].items())
    last = s["curve"][-1] if s["curve"] else None
    value = last["equity"] if last else s["cash"] + invested
    nifty = last["nifty"] if last else cap
    return dict(cap=cap, value=value, pnl=value - cap, ret=(value / cap - 1) * 100,
                nifty=nifty, nifty_ret=(nifty / cap - 1) * 100,
                edge=(value / cap - 1) * 100 - (nifty / cap - 1) * 100,
                cash=s["cash"], invested=invested, charges=s["charges"])


def live_checks(cfg):
    b = KiteBroker()
    ok, why = b.status()
    return b, ok, why, [
        (bool(b.key and b.secret), "Kite API key and secret in .env",
         "From your Kite Connect app on developers.kite.trade (₹500/month)."),
        (importlib.util.find_spec("kiteconnect") is not None, "Kite SDK installed", "pip install kiteconnect"),
        (b.env_enabled, "LIVE_TRADING_ENABLED=yes in .env", "The server-side master switch for real orders."),
        (ok, "Logged in to Zerodha today", "Exchange rules require a manual login every trading day."),
        (cfg["live"]["investment_value"] > 0, "Live investment value set", "How much of the account the engine may use."),
        (bool(cfg["live"]["enabled"]), "Live trading switched on", "Turned on below, with a typed confirmation."),
    ]


def _start(mode):
    at.paths(mode)["stop"].unlink(missing_ok=True)
    if (ROOT / "KILL").exists():
        (ROOT / "KILL").unlink()
    subprocess.Popen([sys.executable, "-u", str(ROOT / "autotrader.py"), "--mode", mode], cwd=ROOT,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def controls(mode, s, cfg, can_start=True):
    on = running(mode, s, cfg)
    c1, c2, c3 = st.columns([1.1, 0.8, 3])
    if c1.button("▶  Start auto-trader", key=f"start_{mode}", type="primary", disabled=on or not can_start,
                 width="stretch"):
        _start(mode)
        st.toast("Auto-trader starting. First pass within a minute.", icon="🚀")
    if c2.button("⏹  Stop", key=f"stop_{mode}", disabled=not on, width="stretch"):
        at.paths(mode)["stop"].touch()
        st.toast("Stopping after the current pass. Holdings are kept.", icon="⏹")
    hb = f" · last check {s['heartbeat'][11:16]} IST" if s and s.get("heartbeat") else ""
    dot = ui.C["good"] if on else ui.C["muted"]
    c3.markdown(f'<div style="padding-top:.45rem"><span class="pill"><span class="dot" style="background:{dot}">'
                f'</span>{"Engine running" if on else "Engine stopped"}{hb}</span></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ portfolio view
def kpis(s):
    k = summary(s)
    m = st.columns(5)
    d = (lambda x: f"{x:+.2f}%" if abs(x) >= 0.005 else None)
    m[0].metric("Account value", inr0(k["value"]), d(k["ret"]), border=True)
    m[1].metric("Profit / loss", inr0(k["pnl"], True), border=True,
                help="After all charges and slippage. Paper cash earns a 6% liquid-fund rate.")
    m[2].metric("Same money in Nifty 50", inr0(k["nifty"]), d(k["nifty_ret"]), border=True)
    m[3].metric("Ahead of Nifty by", f"{k['edge']:+.2f} pts", border=True,
                help="This account's return minus the Nifty 50 index fund's, in percentage points.")
    m[4].metric("Charges paid", inr0(k["charges"]), border=True,
                help="Zerodha delivery charges: STT, stamp duty, exchange, SEBI, GST, DP.")
    return k


def holdings_table(s):
    lp = s.get("last_prices") or {}
    rows = []
    for sym, p in s["positions"].items():
        last = lp.get(sym, p["entry"])
        val = p["shares"] * last
        rows.append({"Stock": sym, "Shares": p["shares"], "Buy price": p["entry"], "Last price": last,
                     "Value": val, "P&L": val - p["cost"], "P&L %": (val / p["cost"] - 1) * 100,
                     "Since": p["entry_date"]})
    df = pd.DataFrame(rows).sort_values("Value", ascending=False)
    tot = df["Value"].sum() + s["cash"]
    df["Weight"] = df["Value"] / tot * 100

    def color(v):
        return f"color: {ui.C['gain'] if v > 0 else ui.C['loss'] if v < 0 else ui.C['ink2']}; font-weight: 600"

    sty = df.style.map(color, subset=["P&L", "P&L %"]).format({
        "Buy price": "₹{:,.2f}", "Last price": "₹{:,.2f}", "Value": lambda v: inr0(v),
        "P&L": lambda v: inr0(v, True), "P&L %": "{:+.2f}%"})
    st.dataframe(sty, hide_index=True, width="stretch", column_config={
        "Weight": st.column_config.ProgressColumn("Weight", format="%.1f%%", min_value=0, max_value=20),
        "Shares": st.column_config.NumberColumn(format="%d")})
    return df


def orders_table(s):
    t = pd.DataFrame(s["trades"])
    if t.empty:
        return
    t["Value"] = t["qty"] * t["price"]
    t = t.rename(columns={"time": "Date", "side": "Side", "symbol": "Stock", "qty": "Shares",
                          "price": "Price", "charges": "Charges", "pnl": "Realised P&L"})
    t["Date"] = t["Date"].astype(str).str[:16]
    t["Side"] = t["Side"].map({"BUY": "🟦 BUY", "SELL": "🟥 SELL"})
    t = t[["Date", "Side", "Stock", "Shares", "Price", "Value", "Charges", "Realised P&L"]][::-1]

    t["Realised P&L"] = t["Realised P&L"].map(lambda v: "" if v is None or pd.isna(v) else inr0(v, True))

    def color(v):
        return "" if not v else f"color: {ui.C['loss'] if v.startswith('-') else ui.C['gain']}; font-weight: 600"

    sty = t.style.map(color, subset=["Realised P&L"]).format({
        "Price": "₹{:,.2f}", "Value": lambda v: inr0(v), "Charges": "₹{:,.2f}"})
    st.dataframe(sty, hide_index=True, width="stretch", height=min(420, 38 + 35 * len(t)))


def portfolio_view(mode):
    s = at.load(mode)
    if not s:
        ui.callout("No portfolio yet. Press <b>Start auto-trader</b>: it decides on the latest close and "
                   "places its first orders in the next market session (9:15–15:30 IST).")
        return
    kpis(s)
    if s.get("last_error"):
        ui.callout(f"⚠️ <b>Needs attention:</b> {s['last_error']}", "warn")
    for r in s.get("reconcile") or []:
        ui.callout(f"⛔ <b>Position mismatch with Zerodha:</b> {r}", "bad")
    if s.get("pending"):
        p = s["pending"]
        names = ", ".join(p["target"]) if p["target"] else "nothing: hold cash"
        ui.callout(f"🗓️ <b>Next orders</b> go out in the next market session. Decided on the "
                   f"{p['decided']} close: {p['reason']}.<br><b>Target holdings:</b> {names}")
    elif s.get("last_decision"):
        ui.callout(f"🗓️ Last rebalance decided on {s['last_decision']}. The next decision is after the "
                   f"first close of next month.")

    left, right = st.columns([2.1, 1])
    with left:
        if s["curve"]:
            st.plotly_chart(ui.value_chart(s["curve"], s["capital"]), width="stretch",
                            config={"displayModeBar": False})
        else:
            st.markdown('<div class="card"><h4>Account value vs Nifty 50</h4><div class="muted">The chart starts '
                        'after the first market session.</div></div>', unsafe_allow_html=True)
    with right:
        lp = s.get("last_prices") or {}
        rows = [(sym, p["shares"] * lp.get(sym, p["entry"])) for sym, p in s["positions"].items()]
        if rows:
            rows.append(("Cash", s["cash"]))
            st.plotly_chart(ui.allocation_chart(rows, height=360), width="stretch", config={"displayModeBar": False})
        else:
            st.markdown(ui.card("Where the money is", "100% cash",
                                f"{inr0(s['cash'])} earning a liquid-fund rate until the rule says buy."),
                        unsafe_allow_html=True)

    if s["positions"]:
        ui.section(f"Holdings · {len(s['positions'])} stocks"
                   + (f" · prices as of {s['prices_as_of']} close" if s.get("prices_as_of") else ""))
        holdings_table(s)

    mc = ui.monthly_chart(s["curve"], s["capital"])
    if mc:
        st.plotly_chart(mc, width="stretch", config={"displayModeBar": False})

    if s["trades"]:
        ui.section(f"Order history · {len(s['trades'])} orders")
        orders_table(s)
    lf = at.paths(mode)["log"]
    if lf.exists():
        with st.expander("Engine log"):
            st.code("\n".join(lf.read_text(encoding="utf-8").splitlines()[-60:]), language=None)


# ------------------------------------------------------------------ pages
def page_overview():
    cfg = load_cfg()
    ui.header("Overview", "Everything the system is doing, at a glance.")
    paper, live, demo = at.load("paper"), at.load("live"), at.load("demo")
    _, ok, _, checks = live_checks(cfg)
    c = st.columns(3)
    if paper:
        k = summary(paper)
        state = "running" if running("paper", paper, cfg) else "stopped"
        c[0].markdown(ui.card("🧪 Paper account", inr0(k["value"]),
                              f"Started with {inr0(k['cap'])} · engine {state}<br>"
                              f"Nifty 50 with the same money: {inr0(k['nifty'])}", k["ret"]), unsafe_allow_html=True)
    else:
        c[0].markdown(ui.card("🧪 Paper account", "Not started", "Open <b>Paper account</b> and press Start."),
                      unsafe_allow_html=True)
    done = sum(1 for d, *_ in checks if d)
    if live:
        k = summary(live)
        c[1].markdown(ui.card("🔴 Real-money account", inr0(k["value"]), f"Setup {done}/6 complete", k["ret"]),
                      unsafe_allow_html=True)
    else:
        c[1].markdown(ui.card("🔴 Real-money account", f"Setup {done}/6",
                              "Not trading. Needs your Zerodha Kite Connect app, a static IP and a daily login."),
                      unsafe_allow_html=True)
    if demo:
        k = summary(demo)
        c[2].markdown(ui.card("🎬 Demo replay (since 1 Jul)", inr0(k["value"]),
                              f"Nifty 50 with the same money: {inr0(k['nifty'])} "
                              f"({k['nifty_ret']:+.2f}%)", k["ret"]), unsafe_allow_html=True)
    else:
        c[2].markdown(ui.card("🎬 Demo replay", "Not built", "Open <b>Demo replay</b> to build it."),
                      unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([2.1, 1])
    with left:
        src, label = (paper, "Paper account") if paper and len(paper["curve"]) > 1 else (demo, "Demo replay")
        if src and src["curve"]:
            st.plotly_chart(ui.value_chart(src["curve"], src["capital"],
                                           title=f"{label}: value vs the same money in a Nifty 50 index fund"),
                            width="stretch", config={"displayModeBar": False})
    with right:
        ui.section("How it works")
        st.markdown(
            "1. **You set the investment value** in the sidebar.\n"
            "2. **Once a month** the engine ranks the Nifty 100 by 12-month momentum and picks the top 10.\n"
            "3. **It holds cash** when the Nifty 50 is below its 200-day average.\n"
            "4. **Orders go out automatically**: simulated in paper, real through Zerodha in live mode.\n"
            "5. **Every evening** it compares the value with a Nifty 50 index fund and alerts you.")
        ui.callout("📚 <b>What the research found:</b> intraday trading lost money in every test. Monthly "
                   "momentum had a small real edge (about 1–2 points a year) with roughly twice the falls of "
                   "the Nifty. Paper-trade for a few months before using real money.", "warn")


def page_paper():
    cfg = load_cfg()
    ui.header("Paper account", "Monthly momentum on simulated money: live prices, real charges, no risk.",
              mode="PAPER")
    s = at.load("paper")
    controls("paper", s, cfg)
    if s and s["capital"] != cfg["investment_value"]:
        st.caption(f"This account started with {inr0(s['capital'])}. A new investment value from the sidebar "
                   "applies after a reset.")

    @st.fragment(run_every="30s")
    def body():
        portfolio_view("paper")
    body()

    with st.expander("Reset paper account"):
        st.write("Deletes the paper portfolio and its history. The next start uses the sidebar investment value.")
        if st.button("Delete paper account", key="reset_paper", disabled=running("paper", s, cfg)):
            at.paths("paper")["state"].unlink(missing_ok=True)
            st.toast("Paper account deleted", icon="🗑️")
            st.rerun()


def page_live():
    cfg = load_cfg()
    ui.header("Real-money account", "The same engine, placing real delivery orders in the Zerodha account.",
              mode="LIVE")
    b, ok, why, checks = live_checks(cfg)
    ready = all(d for d, *_ in checks)
    left, right = st.columns([1, 1.25], gap="large")
    with left:
        ui.section(f"Safety checklist · {sum(d for d, *_ in checks)}/6")
        ui.steps(checks)
        st.caption(f"Broker: {why}. Orders only work from the static IP registered with Kite. "
                   "Setup guide: docs/LIVE_TRADING_SETUP.md")
    with right:
        ui.section("Zerodha login")
        if ok:
            try:
                ui.callout(f"✅ {why}<br>Available funds: <b>{inr(b.funds())}</b>", "good")
            except Exception as ex:
                ui.callout(f"✅ {why}<br>Could not read funds: {ex}", "good")
        elif b.kite is not None:
            st.link_button("🔑  Log in to Zerodha", b.login_url(), type="primary")
            tok = st.text_input("…or paste the request_token from the redirect URL", key="req_tok")
            if tok and st.button("Complete login"):
                try:
                    st.success(f"Logged in: {b.complete_login(tok.strip())}")
                    st.rerun()
                except Exception as ex:
                    st.error(f"Login failed: {ex}")
        else:
            ui.callout("Add <code>KITE_API_KEY</code> and <code>KITE_API_SECRET</code> to the <code>.env</code> "
                       "file on the trading machine, then reload this page.", "warn")

        ui.section("Live settings")
        with st.form("live_settings", border=True):
            c1, c2 = st.columns(2)
            lv = c1.number_input("Live investment value (₹)", min_value=0, step=5000,
                                 value=int(cfg["live"]["investment_value"]),
                                 help="Money the engine may use. Never more than the funds in the account. "
                                      "About ₹50,000+ is needed to hold 10 stocks in whole shares.")
            mo = c2.number_input("Max value of one order (₹)", min_value=1000, step=1000,
                                 value=int(cfg["live"]["max_order_value"]))
            enable = st.toggle("Switch on live trading", value=cfg["live"]["enabled"])
            confirm = st.text_input("To switch on, type  I ACCEPT REAL LOSSES", key="live_confirm")
            if st.form_submit_button("Save live settings", type="primary"):
                if enable and not cfg["live"]["enabled"] and confirm.strip() != "I ACCEPT REAL LOSSES":
                    st.error("Type the confirmation exactly to switch live trading on.")
                else:
                    cfg["live"].update(investment_value=lv, max_order_value=mo, enabled=bool(enable))
                    save_cfg(cfg)
                    st.toast("Live settings saved", icon="💾")
                    st.rerun()

    st.divider()
    s = at.load("live")
    controls("live", s, cfg, can_start=ready)
    if not ready:
        st.caption("Start unlocks when every checklist item is ✓.")

    @st.fragment(run_every="30s")
    def body():
        portfolio_view("live")
    body()


def page_demo():
    ui.header("Demo replay", "The same monthly rule replayed on real prices since 1 Jul 2026, so you can see "
                             "what a trading account looks like.")
    c1, c2 = st.columns([1, 3])
    if c1.button("🔄  Rebuild with latest prices", key="demo_rebuild", width="stretch"):
        import demo
        with st.spinner("Replaying since 1 Jul 2026…"):
            at.paths("demo")["log"].unlink(missing_ok=True)
            demo.build()
    c2.caption("Decisions on each month's last close, fills at the next open with 0.1% slippage and full "
               "Zerodha charges. The cash filter is off so every month trades. A replay of the past, not a "
               "live account.")
    portfolio_view("demo")
