"""Research pages: intraday paper session, day replay, intraday backtest, cost planner."""
import json
import subprocess
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import backtest as bt
import paper_trader as pt
import ui_style as ui
from ui_auto import ROOT, load_cfg, save_cfg
from ui_style import inr, inr0

DATA = ROOT / "data"
NOBAR = {"displayModeBar": False}


@st.cache_data(ttl=600, show_spinner="Downloading 60 days of 5-minute bars…")
def history(symbols, refresh_key):
    return bt.load_history(list(symbols), refresh=refresh_key > 0)


def sim_cfg(capital, strategy, overrides):
    c = load_cfg()
    c["investment_value"] = capital
    c["strategy"]["name"] = strategy
    for k, v in overrides.items():
        c[k].update(v)
    return c


def strategy_controls(key, cfg):
    with st.expander("⚙️  Strategy and cost settings"):
        a, b, c = st.columns(3)
        s = a.selectbox("Strategy", list(pt.STRATEGIES), key=f"{key}_s",
                        index=list(pt.STRATEGIES).index(cfg["strategy"]["name"]))
        tr = b.number_input("Target (R multiple)", 0.5, 5.0, float(cfg["strategy"]["target_r"]), 0.25, key=f"{key}_r")
        ms = c.number_input("Min stop %", 0.1, 2.0, float(cfg["strategy"]["min_stop_pct"]), 0.05, key=f"{key}_m")
        a2, b2, c2 = st.columns(3)
        orb = a2.selectbox("Opening range (min)", [15, 30, 45, 60], key=f"{key}_o",
                           index=[15, 30, 45, 60].index(int(cfg["strategy"].get("orb_minutes", 30))))
        sl = b2.number_input("Slippage % per side", 0.0, 0.5, float(cfg["costs"]["slippage_pct"]), 0.01, key=f"{key}_sl")
        epd = c2.number_input("Entries per stock per day", 1, 5, int(cfg["risk"]["max_entries_per_stock_per_day"]), key=f"{key}_e")
    return s, {"strategy": {"target_r": tr, "min_stop_pct": ms, "orb_minutes": orb},
               "costs": {"slippage_pct": sl}, "risk": {"max_entries_per_stock_per_day": epd}}


def equity_line(x, y, capital, title, hv=False):
    fig = go.Figure()
    fig.add_hline(y=capital, line=dict(color=ui.C["muted"], width=1, dash="dot"))
    fig.add_scatter(x=x, y=y, mode="lines", line=dict(color=ui.C["account"], width=2.25, shape="hv" if hv else "linear"),
                    name="Value", customdata=[inr0(v) for v in y], hovertemplate="%{customdata}<extra></extra>",
                    showlegend=False)
    fig.update_layout(title=title, height=320, yaxis=dict(tickprefix="₹", tickformat=",.0f"))
    return fig


def pnl_bars(x, y, title):
    fig = go.Figure(go.Bar(x=x, y=y, marker=dict(color=[ui.C["gain"] if v >= 0 else ui.C["loss"] for v in y],
                                                 cornerradius=3),
                           customdata=[inr0(v, True) for v in y], hovertemplate="%{customdata}<extra></extra>"))
    fig.update_layout(title=title, height=280, yaxis=dict(tickprefix="₹", tickformat=",.0f", zeroline=True,
                                                          zerolinecolor=ui.C["border"]))
    return fig


def trades_table(t):
    show = t.copy()

    def color(v):
        return f"color: {ui.C['gain'] if v > 0 else ui.C['loss'] if v < 0 else ui.C['ink2']}; font-weight: 600"
    cols = [c for c in ("net_pnl", "gross_pnl") if c in show.columns]
    st.dataframe(show.style.map(color, subset=cols).format(precision=2), hide_index=True, width="stretch")


# ------------------------------------------------------------------ pages
def page_intraday():
    cfg = load_cfg()
    ui.header("Intraday paper session", "5-minute breakout strategies on 20 Nifty stocks, squared off by 15:15. "
                                        "Paper only: intraday lost money in every test.", mode="PAPER")
    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.3, 2])
    strat = c3.selectbox("Strategy", list(pt.STRATEGIES), index=list(pt.STRATEGIES).index(cfg["strategy"]["name"]),
                         label_visibility="collapsed")
    if strat != cfg["strategy"]["name"]:
        cfg["strategy"]["name"] = strat
        save_cfg(cfg)
    if c1.button("▶  Start session", type="primary", width="stretch"):
        if (ROOT / "KILL").exists():
            (ROOT / "KILL").unlink()
        subprocess.Popen([sys.executable, "-u", str(ROOT / "paper_trader.py")], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        st.toast("Session starting", icon="🚀")
    if c2.button("⛔  Square off & stop", width="stretch"):
        (ROOT / "KILL").touch()
        st.toast("Kill switch set: positions square off within a minute", icon="⛔")

    @st.fragment(run_every="15s")
    def live_panel():
        sf = DATA / "state.json"
        if not sf.exists():
            ui.callout("No session yet. Press <b>Start session</b> during market hours (9:15–15:15 IST).")
            return
        s = json.loads(sf.read_text())
        pnl = s["current_value"] - s["investment_value"]
        m = st.columns(5)
        m[0].metric("Investment value", inr0(s["investment_value"]), border=True)
        m[1].metric("Current value", inr0(s["current_value"]), f"{pnl / s['investment_value'] * 100:+.2f}%", border=True)
        m[2].metric("Realised (net)", inr0(s["realized_net"], True), border=True)
        m[3].metric("Unrealised", inr0(s["unrealized"], True), border=True)
        m[4].metric("Charges paid", inr0(s["charges"]), border=True)
        st.caption(f"Status: **{s['status']}** · updated {s['updated'][11:19]} IST"
                   + (f" · ⚠ {s['halted']}" if s.get("halted") else ""))
        if s["open_positions"]:
            op = pd.DataFrame(s["open_positions"]).T.reset_index(names="symbol")
            op["P&L"] = [(row.ltp - row.entry) * row.qty * (1 if row.side == "LONG" else -1)
                         if row.ltp else 0 for row in op.itertuples()]
            ui.section("Open positions")
            st.dataframe(op[["symbol", "side", "qty", "entry", "ltp", "stop", "target", "P&L", "entry_time"]],
                         hide_index=True, width="stretch")
        tf = DATA / "trades.csv"
        if tf.exists():
            t = pd.read_csv(tf)
            ui.section(f"Closed trades · {len(t)}")
            trades_table(t)
        lf = DATA / "engine.log"
        if lf.exists():
            with st.expander("Engine log"):
                st.code("\n".join(lf.read_text(encoding="utf-8").splitlines()[-40:]), language=None)
        rep = sorted(DATA.glob("report_*.md"))
        if rep:
            with st.expander(f"Latest report ({rep[-1].name})"):
                st.markdown(rep[-1].read_text(encoding="utf-8"))
    live_panel()


def page_replay():
    cfg = load_cfg()
    ui.header("Replay a day", "Simulate a full trading day (9:15–15:15) on recorded 5-minute bars, as if the "
                              "intraday system had run all day.")
    cA, cB, cC = st.columns([1, 1, 1])
    cap = cA.number_input("Investment value (₹)", 10000, 10_00_00_000, int(cfg["investment_value"]), 10000, key="rp_cap")
    if cC.button("🔄  Refresh market data", key="rp_ref", width="stretch"):
        st.session_state["refresh"] = st.session_state.get("refresh", 0) + 1
    data = history(tuple(cfg["universe"]), st.session_state.get("refresh", 0))
    days = sorted({d for df in data.values() for d in df.index.date})[1:]
    day = cB.selectbox("Day", days[::-1], format_func=lambda d: d.strftime("%a %d %b %Y")
                       + ("  (today)" if d == date.today() else ""))
    s, over = strategy_controls("rp", cfg)
    if st.button("Run replay", type="primary", key="rp_go"):
        t, d, summ = bt.simulate(data, sim_cfg(cap, s, over), cap, only_days={str(day)})
        if t.empty:
            ui.callout("No trades that day under these rules.", "warn")
        else:
            net = summ["net_pnl"]
            m = st.columns(5)
            m[0].metric("Start", inr0(cap), border=True)
            m[1].metric("End of day", inr0(cap + net), f"{net / cap * 100:+.2f}%", border=True)
            m[2].metric("Net P&L", inr(net, True), border=True)
            m[3].metric("Charges", inr(summ["charges"]), border=True)
            m[4].metric("Trades · win rate", f"{summ['trades']} · {summ['win_rate_pct']}%", border=True)
            tt = t.sort_values("exit_time")
            st.plotly_chart(equity_line(tt["exit_time"], list(cap + tt["net_pnl"].cumsum()), cap,
                                        "Account value through the day", hv=True), width="stretch", config=NOBAR)
            ui.section("Trades")
            trades_table(t.drop(columns=["date"]))
        last = max(df.index[-1] for df in data.values())
        if day == last.date() and last.hour < 15:
            st.caption(f"Data for today ends at {last:%H:%M}. Refresh after 15:30 for the full day.")


def page_backtest():
    cfg = load_cfg()
    ui.header("Intraday backtest", "The intraday strategy over the last ~60 trading days with full charges and "
                                   "slippage.")
    cap2 = st.number_input("Investment value (₹)", 10000, 10_00_00_000, int(cfg["investment_value"]), 10000, key="bt_cap")
    s2, over2 = strategy_controls("bt", cfg)
    if st.button("Run backtest", type="primary", key="bt_go"):
        data = history(tuple(cfg["universe"]), st.session_state.get("refresh", 0))
        t, d, summ = bt.simulate(data, sim_cfg(cap2, s2, over2), cap2)
        if t.empty:
            ui.callout("No trades.", "warn")
            return
        m = st.columns(6)
        m[0].metric("Net P&L", inr0(summ["net_pnl"], True), f"{summ['return_pct']:+.2f}%", border=True)
        m[1].metric("Charges", inr0(summ["charges"]), border=True)
        m[2].metric("Trades", summ["trades"], border=True)
        m[3].metric("Win rate", f"{summ['win_rate_pct']}%", border=True)
        m[4].metric("Per trade", inr(summ["expectancy_per_trade"], True), border=True)
        m[5].metric("Max drawdown", f"{summ['max_drawdown_pct']}%", border=True)
        st.plotly_chart(equity_line(list(d["date"]), list(d["equity"]), cap2, "Account value"),
                        width="stretch", config=NOBAR)
        st.plotly_chart(pnl_bars(list(d["date"]), list(d["net_pnl"]), "Daily net P&L"), width="stretch", config=NOBAR)
        with st.expander("All trades"):
            trades_table(t)
        ui.callout("A strategy should not go live unless its profit per trade after costs is positive on data it "
                   "was not designed on. 60 days is too short to prove an edge.", "warn")


def page_costs():
    cfg = load_cfg()
    ui.header("Costs (INR)", "What running the system costs, and what return it needs just to pay for itself.")
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        ui.section("Monthly running cost")
        default = pd.DataFrame([
            ("Kite Connect API (data + orders)", 500, "Required for live. Zerodha, per API key"),
            ("Cloud VM, Mumbai + static IP", 2500, "Needed for live orders. A monthly strategy can use a ₹500–1,000 VM"),
            ("Claude API (signal reviews, headlines, briefs)", 1400, "Only for the AI layer; the monthly strategy needs none"),
            ("News / data vendor", 0, "Free RSS + NSE files to start; paid feeds ₹2,000–10,000"),
            ("TradingView", 0, "Optional, for your own charts"),
            ("Telegram alerts", 0, "Free"),
            ("Domain + HTTPS", 100, "Optional"),
        ], columns=["Item", "₹ per month", "Notes"])
        costs = st.data_editor(default, hide_index=True, width="stretch", num_rows="dynamic",
                               column_config={"₹ per month": st.column_config.NumberColumn(format="₹%d", width="small"),
                                              "Item": st.column_config.TextColumn(width="medium"),
                                              "Notes": st.column_config.TextColumn(width="large")})
    monthly = float(costs["₹ per month"].sum())
    with right:
        ui.section("Break-even")
        capx = st.number_input("Trading capital (₹)", 10000, 10_00_00_000, int(cfg["investment_value"]), 10000, key="c_cap")
        need = monthly * 12 / capx * 100
        a, b = st.columns(2)
        a.metric("Per month", inr0(monthly), border=True)
        b.metric("Per year", inr0(monthly * 12), border=True)
        st.metric("Return needed just to cover costs", f"{need:.1f}% a year", border=True,
                  help="Before any profit. A Nifty 50 index fund costs about 0.1–0.2% a year.")
        ui.callout(("⛔ Higher than almost any fund manages." if need > 15 else
                    "⚠️ A real hurdle on top of beating the index." if need > 3 else
                    "✅ Small relative to capital."), "bad" if need > 15 else "warn" if need > 3 else "good")

    caps = [1e5, 2.5e5, 5e5, 10e5, 25e5, 50e5]
    fig = go.Figure(go.Bar(x=[ui.inr_short(c) for c in caps], y=[monthly * 12 / c * 100 for c in caps],
                           marker=dict(color=ui.C["account"], cornerradius=4),
                           text=[f"{monthly * 12 / c * 100:.1f}%" for c in caps], textposition="outside",
                           cliponaxis=False, hovertemplate="%{x}: %{y:.1f}% a year<extra></extra>"))
    fig.update_layout(title="Return needed to cover running costs, by capital", height=300, hovermode="closest",
                      bargap=0.45,
                      yaxis=dict(ticksuffix="%"))
    st.plotly_chart(fig, width="stretch", config=NOBAR)

    ui.section("Charges on one intraday trade")
    c1, c2 = st.columns(2)
    tv = c1.slider("Trade value (₹)", 5000, 500000, 20000, 5000)
    move = c2.slider("Price move captured (%)", -2.0, 2.0, 0.5, 0.05)
    ch = pt.round_trip_charges(tv, tv * (1 + move / 100))
    slp = tv * cfg["costs"]["slippage_pct"] / 100 * 2
    gross = tv * move / 100
    x = st.columns(4)
    x[0].metric("Gross", inr(gross, True), border=True)
    x[1].metric("Charges (brokerage, STT, GST…)", inr(ch), border=True)
    x[2].metric("Slippage estimate", inr(slp), border=True)
    x[3].metric("Net", inr(gross - ch - slp, True), border=True)
    st.caption("Small trades pay a much larger share in costs, the main reason intraday strategies on small "
               "capital struggle.")
