"""Shared look and feel: colours, chart template, CSS, rupee formatting, small components.

Colours follow a validated colour-blind-safe palette (blue = this account, orange = Nifty
benchmark; blue/red poles for gains/losses; status colours always paired with an icon + text).
"""
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

IST = timezone(timedelta(hours=5, minutes=30))

C = dict(
    account="#2a78d6",      # series slot 1: this account / strategy
    bench="#eb6834",        # series slot 2: Nifty 50 benchmark
    gain="#2a78d6",         # diverging pole (gains)
    loss="#e34948",         # diverging pole (losses)
    good="#0ca30c", warn="#fab219", critical="#d03b3b",
    ink="#1d1d1b", ink2="#52514e", muted="#8a8984",
    grid="#ecebe6", surface="#fcfcfb", card="#ffffff", border="#e4e2dc",
    seq=["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281"],
)

pio.templates["desk"] = go.layout.Template(layout=dict(
    font=dict(family="Inter, 'Segoe UI', system-ui, sans-serif", size=13, color=C["ink2"]),
    colorway=[C["account"], C["bench"], "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=8, r=8, t=48, b=8),
    title=dict(font=dict(size=15, color=C["ink"]), x=0, xanchor="left", y=0.97),
    xaxis=dict(showgrid=False, linecolor=C["border"], ticks="outside", tickcolor=C["border"], zeroline=False),
    yaxis=dict(gridcolor=C["grid"], zeroline=False, linecolor="rgba(0,0,0,0)"),
    legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, bgcolor="rgba(0,0,0,0)"),
    hoverlabel=dict(bgcolor="white", bordercolor=C["border"], font=dict(color=C["ink"], size=13)),
    hovermode="x unified",
))
pio.templates.default = "desk"


# ------------------------------------------------------------------ formatting
def _group(whole):
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
    return f"{head},{tail}"


def inr(x, sign=False, decimals=2):
    """Indian digit grouping: ₹1,00,000.00"""
    if x is None:
        return "-"
    s = f"{abs(x):.{decimals}f}"
    whole, _, frac = s.partition(".")
    pre = ("+" if x > 0 else "-" if x < 0 else "") if sign else ("-" if x < 0 else "")
    return f"{pre}₹{_group(whole)}" + (f".{frac}" if decimals else "")


def inr0(x, sign=False):
    return inr(x, sign, 0)


def inr_short(x):
    a = abs(x)
    s = f"₹{a / 1e7:.2f} Cr" if a >= 1e7 else f"₹{a / 1e5:.2f} L" if a >= 1e5 else inr0(a)
    return ("-" if x < 0 else "") + s


def pct(x, sign=True):
    return f"{x:+.2f}%" if sign else f"{x:.2f}%"


# ------------------------------------------------------------------ CSS
CSS = f"""
<style>
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1320px; }}
h1, h2, h3 {{ letter-spacing: -0.01em; }}
[data-testid="stMetric"] {{ background: {C['card']}; }}
[data-testid="stMetricLabel"] p {{ color: {C['ink2']}; font-size: 0.82rem; font-weight: 500; }}
[data-testid="stMetricValue"] {{ font-size: 1.55rem; font-weight: 650; color: {C['ink']}; }}
[data-testid="stSidebar"] [data-testid="stMetricValue"] {{ font-size: 1.2rem; }}
.desk-header {{ display:flex; align-items:flex-end; justify-content:space-between; gap:1rem;
  padding-bottom: .9rem; margin-bottom: 1.1rem; border-bottom: 1px solid {C['border']}; flex-wrap: wrap; }}
.desk-header h1 {{ font-size: 1.7rem; margin: 0; padding: 0; font-weight: 700; color: {C['ink']}; }}
.desk-header .sub {{ color: {C['ink2']}; margin-top: .25rem; font-size: .95rem; }}
.pills {{ display:flex; gap:.5rem; flex-wrap: wrap; }}
.pill {{ display:inline-flex; align-items:center; gap:.4rem; padding: .28rem .7rem; border-radius: 999px;
  font-size: .8rem; font-weight: 600; border: 1px solid {C['border']}; background: {C['card']}; color: {C['ink']};
  white-space: nowrap; }}
.pill .dot {{ width:.55rem; height:.55rem; border-radius:50%; display:inline-block; }}
.pill.paper {{ background:#eaf2fc; border-color:#cde2fb; color:#1c5cab; }}
.pill.live  {{ background:#fdeceb; border-color:#f6c9c8; color:#a32727; }}
.card {{ background:{C['card']}; border:1px solid {C['border']}; border-radius: .8rem; padding: 1rem 1.1rem;
  height: 100%; }}
.card h4 {{ margin: 0 0 .35rem 0; font-size: .95rem; color: {C['ink']}; font-weight: 650; }}
.card .big {{ font-size: 1.6rem; font-weight: 700; color: {C['ink']}; line-height: 1.2; }}
.card .muted {{ color: {C['ink2']}; font-size: .86rem; }}
.delta {{ display:inline-block; font-size:.8rem; font-weight:600; padding:.05rem .45rem; border-radius:.4rem; }}
.delta.up {{ color:#1c5cab; background:#eaf2fc; }}
.delta.down {{ color:#a32727; background:#fdeceb; }}
.delta.flat {{ color:{C['ink2']}; background:#f0efec; }}
.callout {{ border:1px solid {C['border']}; border-left: 4px solid {C['account']}; background:{C['card']};
  border-radius:.6rem; padding:.8rem 1rem; margin: .4rem 0 1rem 0; color:{C['ink']}; }}
.callout.warn {{ border-left-color: {C['warn']}; }}
.callout.bad {{ border-left-color: {C['critical']}; }}
.callout.good {{ border-left-color: {C['good']}; }}
.steps {{ list-style:none; padding:0; margin:0; }}
.steps li {{ display:flex; gap:.7rem; align-items:flex-start; padding:.55rem 0; border-bottom:1px dashed {C['border']}; }}
.steps li:last-child {{ border-bottom: none; }}
.steps .ico {{ width:1.5rem; height:1.5rem; border-radius:50%; display:flex; align-items:center; justify-content:center;
  font-size:.8rem; font-weight:700; flex: none; }}
.steps .ok {{ background:#e3f5e3; color:#0a7a0a; }}
.steps .no {{ background:#f0efec; color:{C['ink2']}; }}
.steps .t {{ font-weight:600; color:{C['ink']}; font-size:.92rem; }}
.steps .h {{ color:{C['ink2']}; font-size:.82rem; }}
.section-title {{ font-size: 1.05rem; font-weight: 650; color: {C['ink']}; margin: 1.2rem 0 .4rem 0; }}
.brand {{ font-weight: 750; font-size: 1.15rem; color: {C['ink']}; letter-spacing:-0.01em; }}
.brand small {{ display:block; font-weight: 500; font-size:.78rem; color:{C['ink2']}; }}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------ components
def market_status():
    n = datetime.now(IST)
    mins = n.hour * 60 + n.minute
    if n.weekday() < 5 and 9 * 60 + 15 <= mins < 15 * 60 + 30:
        return True, f"Market open · {n:%H:%M} IST"
    return False, f"Market closed · {n:%a %H:%M} IST"


def header(title, sub="", mode=None):
    is_open, mtxt = market_status()
    pills = []
    if mode:
        pills.append(f'<span class="pill {mode.lower()}">{"🔴 REAL MONEY" if mode == "LIVE" else "🧪 PAPER MONEY"}</span>')
    pills.append(f'<span class="pill"><span class="dot" style="background:{C["good"] if is_open else C["muted"]}"></span>{mtxt}</span>')
    st.markdown(f'<div class="desk-header"><div><h1>{title}</h1><div class="sub">{sub}</div></div>'
                f'<div class="pills">{"".join(pills)}</div></div>', unsafe_allow_html=True)


def delta_html(value_pct):
    if value_pct is None:
        return ""
    cls, arrow = ("up", "▲") if value_pct > 0.005 else ("down", "▼") if value_pct < -0.005 else ("flat", "■")
    return f'<span class="delta {cls}">{arrow} {abs(value_pct):.2f}%</span>'


def card(title, big, sub="", delta=None):
    return (f'<div class="card"><h4>{title}</h4><div class="big">{big} {delta_html(delta)}</div>'
            f'<div class="muted">{sub}</div></div>')


def callout(html, kind=""):
    st.markdown(f'<div class="callout {kind}">{html}</div>', unsafe_allow_html=True)


def steps(items):
    """items: list of (done, title, help)"""
    li = "".join(f'<li><span class="ico {"ok" if d else "no"}">{"✓" if d else i}</span>'
                 f'<div><div class="t">{t}</div><div class="h">{h}</div></div></li>'
                 for i, (d, t, h) in enumerate(items, 1))
    st.markdown(f'<ul class="steps">{li}</ul>', unsafe_allow_html=True)


def section(title):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------ charts
def value_chart(curve, capital, title="Account value vs the same money in a Nifty 50 index fund", height=360):
    """Two lines (account, benchmark) with direct end labels and rupee hover text."""
    d = [r["date"] for r in curve]
    eq = [r["equity"] for r in curve]
    nf = [r["nifty"] for r in curve]
    fig = go.Figure()
    if d:
        fig.add_scatter(x=[d[0], d[-1]], y=[capital, capital], mode="lines", name=f"Invested {inr_short(capital)}",
                        line=dict(color=C["muted"], width=1, dash="dot"), hoverinfo="skip")
    for name, ys, col in (("This account", eq, C["account"]), ("Nifty 50 index fund", nf, C["bench"])):
        fig.add_scatter(x=d, y=ys, name=name, mode="lines", line=dict(color=col, width=2.25),
                        customdata=[[inr0(v), f"{(v / capital - 1) * 100:+.2f}%"] for v in ys],
                        hovertemplate="%{customdata[0]} (%{customdata[1]})<extra>" + name + "</extra>")
        if ys:
            fig.add_scatter(x=[d[-1]], y=[ys[-1]], mode="markers+text", showlegend=False, hoverinfo="skip",
                            marker=dict(size=9, color=col, line=dict(color="white", width=2)),
                            text=[f"  {inr_short(ys[-1])}"], textposition="middle right",
                            textfont=dict(color=C["ink"], size=12), cliponaxis=False)
    fig.update_layout(title=title, height=height, yaxis=dict(tickprefix="₹", tickformat=",.0f"),
                      margin=dict(r=78))
    return fig


def monthly_chart(curve, capital, height=300):
    import pandas as pd
    if len(curve) < 2:
        return None
    df = pd.DataFrame(curve)
    df["date"] = pd.to_datetime(df["date"])
    m = df.set_index("date")[["equity", "nifty"]].resample("ME").last().dropna()
    prev = m.shift(1)
    prev.iloc[0] = capital
    r = ((m / prev - 1) * 100).iloc[-12:]
    if r.empty:
        return None
    labels = [d.strftime("%b %Y") for d in r.index]
    fig = go.Figure()
    for col, name, color in (("equity", "This account", C["account"]), ("nifty", "Nifty 50", C["bench"])):
        fig.add_bar(x=labels, y=r[col], name=name, marker=dict(color=color, cornerradius=4),
                    hovertemplate="%{y:+.2f}%<extra>" + name + "</extra>")
    fig.update_layout(title="Monthly return", height=height, barmode="group", bargap=0.35, bargroupgap=0.08,
                      yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor=C["border"]))
    return fig


def allocation_chart(rows, height=None):
    """rows: list of (label, value). Single series, sorted, magnitude in one hue."""
    rows = sorted(rows, key=lambda r: r[1])
    tot = sum(v for _, v in rows) or 1
    fig = go.Figure(go.Bar(
        y=[r[0] for r in rows], x=[r[1] / tot * 100 for r in rows], orientation="h",
        marker=dict(color=[C["muted"] if r[0] == "Cash" else C["account"] for r in rows], cornerradius=4),
        text=[f"{r[1] / tot * 100:.1f}%" for r in rows], textposition="outside", cliponaxis=False,
        textfont=dict(color=C["ink2"], size=11),
        customdata=[inr0(r[1]) for r in rows], hovertemplate="%{y}: %{customdata} (%{x:.1f}%)<extra></extra>"))
    fig.update_layout(title="Where the money is", height=height or max(240, 28 * len(rows) + 70),
                      hovermode="closest", xaxis=dict(visible=False), yaxis=dict(gridcolor="rgba(0,0,0,0)"),
                      bargap=0.3, margin=dict(r=40))
    return fig
