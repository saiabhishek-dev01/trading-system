"""Trading Desk dashboard: auto-trader (paper + real money), demo replay, intraday research, costs.

Run:  .venv\\Scripts\\streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Trading Desk", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

import ui_auto  # noqa: E402  (after set_page_config)
import ui_research  # noqa: E402
import ui_style as ui  # noqa: E402
from ui_auto import ROOT, load_cfg, save_cfg  # noqa: E402
from ui_style import inr0  # noqa: E402

ui.apply()
st.logo(str(ROOT / "assets" / "logo.svg"), size="large", icon_image=str(ROOT / "assets" / "icon.svg"))
ui_auto.handle_kite_redirect()
cfg = load_cfg()

pages = {
    "Trade": [
        st.Page(ui_auto.page_overview, title="Overview", icon="🏠", default=True),
        st.Page(ui_auto.page_paper, title="Paper account", icon="🧪", url_path="paper"),
        st.Page(ui_auto.page_live, title="Real-money account", icon="🔴", url_path="live"),
    ],
    "Research": [
        st.Page(ui_auto.page_demo, title="Demo replay", icon="🎬", url_path="demo"),
        st.Page(ui_research.page_intraday, title="Intraday paper session", icon="⚡", url_path="intraday"),
        st.Page(ui_research.page_replay, title="Replay a day", icon="⏪", url_path="replay"),
        st.Page(ui_research.page_backtest, title="Intraday backtest", icon="📊", url_path="backtest"),
    ],
    "Plan": [
        st.Page(ui_research.page_costs, title="Costs (INR)", icon="💰", url_path="costs"),
    ],
}
nav = st.navigation(pages)

with st.sidebar:
    st.caption("NSE · monthly momentum · Zerodha")
    with st.container(border=True):
        inv = st.number_input("Investment value (₹)", min_value=10000, max_value=10_00_00_000,
                              value=int(cfg["investment_value"]), step=10000,
                              help="Paper money the system trades. Used for the paper account, replays and "
                                   "backtests. Real money is set on the Real-money page.")
        if inv != cfg["investment_value"]:
            if st.button("Save investment value", type="primary", width="stretch"):
                cfg["investment_value"] = inv
                save_cfg(cfg)
                st.toast(f"Saved {inr0(inv)}", icon="💾")
                st.rerun()
        else:
            st.caption(f"About {inr0(inv / cfg['auto']['slots'])} per stock across {cfg['auto']['slots']} stocks.")
    if st.button("⛔  Kill switch: stop everything", width="stretch",
                 help="Stops every engine. Intraday positions are squared off; monthly holdings are kept."):
        (ROOT / "KILL").touch()
        for m in ("paper", "live"):
            ui_auto.at.paths(m)["stop"].touch()
        st.toast("Kill switch set", icon="⛔")
    if (ROOT / "KILL").exists():
        st.caption("Kill switch is ON. Starting an engine clears it.")
    st.caption("Prices: Yahoo Finance in paper mode (about 1–2 min delayed), Zerodha Kite in live mode. "
               "Research and paper trading, not investment advice.")

nav.run()
