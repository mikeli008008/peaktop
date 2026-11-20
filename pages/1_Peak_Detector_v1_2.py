"""Peak Detector v1.2 页面 — 七族风险评分 (趋势 / 广度 / 波动 / 信用 / 流动性 / 利率 / 不确定性).

计算在 peak_v12.py; 本文件只负责拉数据和展示。数据: Yahoo Finance, FRED, Geopolitical Risk 指数 (Caldara-Iacoviello).
每 4 小时自动刷新一次缓存, 所以每个交易日收盘后打开即是最新读数。
"""
from __future__ import annotations

import io
import os
import sys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import peak_v12 as V  # noqa: E402

st.set_page_config(page_title="Peak Detector v1.2", page_icon="📈", layout="wide")

ORANGE, BLUE, GREEN, RED, GREY = "#f97316", "#60a5fa", "#22c55e", "#ef4444", "#9ca3af"
SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
FRED_IDS = {"walcl": "WALCL", "wtregen": "WTREGEN", "rrp": "RRPONTSYD", "dgs2": "DGS2", "dfii10": "DFII10",
            "t10y2y": "T10Y2Y", "epu": "USEPUINDXD"}


# ------------------------------------------------------------------ data
def fred_key() -> str:
    try:
        if "FRED_API_KEY" in st.secrets:
            return st.secrets["FRED_API_KEY"]
    except Exception:
        pass
    return os.getenv("FRED_API_KEY", "")


def _close(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df["Close"].dropna() if "Close" in df.columns else pd.Series(dtype=float)


@st.cache_data(ttl=14400, show_spinner=False)
def yf_close(ticker: str, period: str = "max") -> pd.Series:
    try:
        return _close(yf.download(ticker, period=period, progress=False, auto_adjust=True))
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=14400, show_spinner=False)
def fred_series(sid: str, key: str) -> pd.Series:
    if not key:
        return pd.Series(dtype=float)
    try:
        from fredapi import Fred
        return Fred(api_key=key).get_series(sid, observation_start="2005-01-01").dropna()
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=14400, show_spinner=False)
def gpr_daily() -> pd.Series:
    try:
        r = requests.get(GPR_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        g = pd.read_excel(io.BytesIO(r.content))
        g = g.loc[:, ~g.columns.duplicated()]
        return pd.Series(g["GPRD"].values, index=pd.to_datetime(g["date"])).dropna().sort_index()
    except Exception:
        return pd.Series(dtype=float)


@st.cache_data(ttl=43200, show_spinner=False)
def sp500_share_above_200d() -> pd.Series:
    """Share of today's S&P 500 members (price > $5) above their own 200-day average, each day (5 years)."""
    try:
        syms = pd.read_csv(SP500_URL)["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
        px = yf.download(syms, period="5y", progress=False, auto_adjust=True, threads=True)["Close"]
        px = px.dropna(axis=1, how="all")
        ma = px.rolling(200, min_periods=200).mean()
        ok = ma.notna() & (px > 5)
        share = ((px > ma) & ok).sum(axis=1) / ok.sum(axis=1)
        return share[ok.sum(axis=1) >= 300]
    except Exception:
        return pd.Series(dtype=float)


def first_ok(*tickers):
    for t in tickers:
        s = yf_close(t)
        if len(s) > 600:
            return s, t
    return pd.Series(dtype=float), None


@st.cache_data(ttl=14400, show_spinner=False)
def load(key: str, with_breadth: bool):
    es, es_src = first_ok("ES=F", "^GSPC", "SPY")
    nq, nq_src = first_ok("NQ=F", "^NDX", "QQQ")
    d = dict(es=es, nq=nq, vix=yf_close("^VIX"), vix9d=yf_close("^VIX9D"), skew=yf_close("^SKEW"),
             spy=yf_close("SPY"), rsp=yf_close("RSP"), hyg=yf_close("HYG"), ief=yf_close("IEF"),
             gpr=gpr_daily())
    for k, sid in FRED_IDS.items():
        d[k] = fred_series(sid, key)
    d["breadth_share"] = sp500_share_above_200d() if with_breadth else pd.Series(dtype=float)
    src = {"es": es_src, "nq": nq_src}
    return d, src


# ------------------------------------------------------------------ page
st.title("📈 Peak Detector v1.2 · 七族见顶风险评分")
st.caption("七个独立维度各自与过去三年比较 (百分位), 等权平均成 0-100 分。分数越高 = 见顶风险越高。"
           "这是风险仪表, 不是卖出信号: 实验室回测里高分之后 NQ 涨得少, 但并不可靠地预示下跌。")

with st.sidebar:
    st.subheader("⚙️ v1.2 设置")
    key = fred_key()
    if not key:
        key = st.text_input("FRED API Key", type="password")
    else:
        st.success("✅ FRED API Key 已加载")
    with_breadth = st.checkbox("计算 S&P 500 广度 (首次约 1-2 分钟)", value=True,
                               help="广度的第三项: 成分股站上 200 日均线的比例。关闭则广度只用 SPY/RSP 两项。")
    rng = st.radio("历史区间", ["1 年", "3 年", "5 年", "全部"], index=2, horizontal=True)
    if st.button("🔄 强制刷新数据"):
        st.cache_data.clear()

if not key:
    st.warning("请在侧边栏输入 FRED API Key (流动性、利率、政策不确定性需要它)。")
    st.stop()

with st.spinner("正在拉取数据并计算 (首次较慢, 之后 4 小时内走缓存)…"):
    data, src = load(key, with_breadth)
    try:
        out = V.compute(data)
    except Exception as e:
        st.error(f"计算失败: {e}")
        st.stop()

S = out.dropna(subset=["score"])
if S.empty:
    st.error("数据不足, 无法计算分数。")
    st.stop()
last = S.iloc[-1]
asof = S.index[-1]
prev20 = S.iloc[-21] if len(S) > 20 else None


def band(p):
    if p <= 20:
        return "低风险 (底部 20%)", GREEN
    if p >= 80:
        return "高风险 (顶部 20%)", RED
    return "中性", GREY


lab, colr = band(last.pct)
st.markdown(f"#### {asof:%Y-%m-%d} 读数: **{last.score:.1f}** 分, 处于过去三年第 **{last.pct:.0f}** 百分位 — "
            f"<span style='color:{colr}'>{lab}</span>", unsafe_allow_html=True)

c = st.columns(6)
c[0].metric("评分 (0-100)", f"{last.score:.1f}",
            None if prev20 is None else f"{last.score - prev20.score:+.1f} vs 20 日前", delta_color="inverse")
c[1].metric("三年百分位", f"{last.pct:.0f}%", None if prev20 is None else f"{last.pct - prev20.pct:+.0f}", delta_color="inverse")
c[2].metric("规则下一收盘起的 NQ 仓位", f"{last.next_pos:.2f}x", help="百分位 ≤ 20 时 1.25x, 否则 1.0x; 从不降到 1.0x 以下")
c[3].metric("可用维度", f"{int(last.families)} / 7")
streak = 0
for v in S.pct[::-1]:
    if (v <= 20) == (last.pct <= 20):
        streak += 1
    else:
        break
c[4].metric("当前状态已持续", f"{streak} 天", help="低风险区 (≤20%) 或非低风险区连续的交易日数")
c[5].metric("实验室回测优势 t", f"+{V.LAB_ALPHA_T:.2f}", help="规则 vs 买入持有 NQ 的 alpha t, 2012-2026-02; 实验室实盘门槛 t 3")

# ---- families now
st.subheader("七个维度当前读数")
st.caption("每个维度 = 与自己过去三年比较的百分位。灰色竖线 = 20 个交易日前的位置。")
rows = []
for f in V.FAMILIES:
    v = last[f]
    p = None if prev20 is None else prev20[f]
    rows.append((V.LABELS[f], v, p))
fig = go.Figure()
names = [r[0] for r in rows][::-1]
vals = [r[1] if pd.notna(r[1]) else 0 for r in rows][::-1]
cols = [RED if (pd.notna(r[1]) and r[1] >= 80) else GREEN if (pd.notna(r[1]) and r[1] <= 20) else BLUE for r in rows][::-1]
fig.add_bar(y=names, x=vals, orientation="h", marker_color=cols,
            text=[("无数据" if pd.isna(r[1]) else f"{r[1]:.0f}") for r in rows][::-1], textposition="outside",
            hovertemplate="%{y}: %{x:.0f}<extra></extra>")
fig.add_scatter(y=names, x=[(r[2] if r[2] is not None and pd.notna(r[2]) else None) for r in rows][::-1], mode="markers",
                marker=dict(symbol="line-ns", size=18, line=dict(width=3, color=GREY)), name="20 日前",
                hovertemplate="20 日前: %{x:.0f}<extra></extra>")
fig.update_layout(height=320, margin=dict(l=10, r=30, t=10, b=10), xaxis=dict(range=[0, 108], title=None),
                  showlegend=False, template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# ---- history
st.subheader("评分与 NQ 历史")
years = {"1 年": 1, "3 年": 3, "5 年": 5, "全部": None}[rng]
H = S if years is None else S[S.index >= asof - pd.DateOffset(years=years)]
fig = make_subplots(specs=[[{"secondary_y": True}]])
tilt = H.held > 1
starts = H.index[tilt & ~tilt.shift(1, fill_value=False)]
ends = H.index[tilt & ~tilt.shift(-1, fill_value=False)]
for a, b in zip(starts, ends):
    fig.add_vrect(x0=a, x1=b, fillcolor=GREEN, opacity=0.12, line_width=0)
fig.add_scatter(x=H.index, y=H.score, name="v1.2 评分", line=dict(color=ORANGE, width=1.6), secondary_y=False)
fig.add_scatter(x=H.index, y=H.nq, name=f"NQ ({src.get('nq')})", line=dict(color=BLUE, width=1.2), secondary_y=True)
fig.add_hline(y=float(np.nanpercentile(S.score.tail(756), 20)), line_dash="dot", line_color=GREEN,
              annotation_text="近三年 20% 分位", secondary_y=False)
fig.update_yaxes(title_text="评分", range=[0, 100], secondary_y=False)
fig.update_yaxes(title_text="NQ", type="log", secondary_y=True, showgrid=False)
fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark",
                  legend=dict(orientation="h", y=1.08), hovermode="x unified")
st.plotly_chart(fig, use_container_width=True)
st.caption("绿色阴影 = 评分在自身底部 20%, 规则持有 1.25x NQ。")

# ---- heat map
st.subheader("各维度月度热力图")
M = H[V.FAMILIES].groupby(H.index.to_period("M")).mean()
M.index = M.index.to_timestamp()
fig = go.Figure(go.Heatmap(z=M.T.values, x=M.index, y=[V.LABELS[f] for f in V.FAMILIES], zmin=0, zmax=100,
                           colorscale=[[0, "#14532d"], [0.5, "#1f2937"], [1, "#b91c1c"]],
                           hovertemplate="%{y}<br>%{x|%Y-%m}: %{z:.0f}<extra></extra>", colorbar=dict(title="风险")))
fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark")
st.plotly_chart(fig, use_container_width=True)

# ---- rule vs buy and hold on the app's data
st.subheader("规则 vs 买入持有 (本页数据)")
st.caption("用本页的 NQ 收盘计算: 底部 20% 时多持 0.25x, 每次换仓扣 1 bp。实验室正式结果见下方表格 (截至 2026-02-20)。")
nqr = S.nq.pct_change().fillna(0)
held = S.held.fillna(1.0)
rule = (held * nqr - held.diff().abs().fillna(0) * 1e-4)
G = pd.DataFrame({"规则": (1 + rule).cumprod(), "买入持有 NQ": (1 + nqr).cumprod()}).loc[H.index]
G = G / G.iloc[0] * 100
tiltusd = ((held - 1) * nqr - held.diff().abs().fillna(0) * 1e-4) * 100_000
fig = make_subplots(rows=1, cols=2, subplot_titles=("$100 的增长 (对数)", "0.25x 加仓本身的累计盈亏 (每 $10 万 NQ)"))
fig.add_scatter(x=G.index, y=G["规则"], name="规则", line=dict(color=ORANGE), row=1, col=1)
fig.add_scatter(x=G.index, y=G["买入持有 NQ"], name="买入持有", line=dict(color=BLUE), row=1, col=1)
fig.add_scatter(x=H.index, y=tiltusd.loc[H.index].cumsum(), name="加仓盈亏 $", line=dict(color=GREEN), fill="tozeroy",
                row=1, col=2)
fig.update_yaxes(type="log", row=1, col=1)
fig.update_layout(height=340, margin=dict(l=10, r=10, t=40, b=10), template="plotly_dark", showlegend=False)
st.plotly_chart(fig, use_container_width=True)

# ---- lab results
st.subheader("实验室回测结果 (nq-replay, 2012 至 2026-02-20)")
a, b = st.columns(2)
with a:
    st.markdown("**按时期: 规则 vs 买入持有 NQ**")
    st.dataframe(V.LAB_ERAS.rename(columns={"era": "时期", "days_tilted_pct": "加仓天数 %", "rule_sharpe": "规则 Sharpe",
                                            "bh_sharpe": "买入持有 Sharpe", "ic_next20": "与未来20日相关"}),
                 hide_index=True, use_container_width=True)
    st.caption("三个时期规则都领先; 全期 alpha t +2.89, 低于实验室实盘门槛 3.0, 目前在前向模拟盘 (2026-11-23 起)。")
with b:
    st.markdown("**按评分百分位: NQ 未来 20 个交易日**")
    st.dataframe(V.LAB_BANDS.rename(columns={"band": "百分位", "mean_fwd20_pct": "平均涨跌 %", "share_up_pct": "上涨比例 %",
                                             "share_fell_5pct": "跌超 5% 比例 %"}),
                 hide_index=True, use_container_width=True)
    st.caption("优势集中在最低档: 低分之后 NQ 平均 +3.5%; 高分只意味着上涨空间小, 不等于要跌。")

# ---- data status
st.subheader("数据状态")
status = []
for k, label in [("es", f"ES ({src.get('es')})"), ("nq", f"NQ ({src.get('nq')})"), ("vix", "VIX"), ("vix9d", "VIX9D"),
                 ("skew", "SKEW"), ("spy", "SPY"), ("rsp", "RSP"), ("hyg", "HYG"), ("ief", "IEF"),
                 ("breadth_share", "S&P 500 站上 200 日线比例"), ("walcl", "WALCL"), ("wtregen", "WTREGEN (TGA)"),
                 ("rrp", "RRPONTSYD"), ("dgs2", "DGS2"), ("dfii10", "DFII10"), ("t10y2y", "T10Y2Y"),
                 ("epu", "USEPUINDXD (政策不确定性)"), ("gpr", "GPRD (地缘风险)")]:
    s = data.get(k)
    ok = s is not None and len(s) > 0
    status.append({"数据": label, "状态": "✅" if ok else "❌ 缺失", "最新日期": (pd.Timestamp(s.index[-1]).date() if ok else None),
                   "条数": int(len(s)) if ok else 0})
st.dataframe(pd.DataFrame(status), hide_index=True, use_container_width=True)

st.download_button("⬇️ 下载每日评分 CSV", S.drop(columns=["nq"]).round(2).to_csv().encode(), "peak_v12_daily.csv", "text/csv")

with st.expander("方法说明"):
    st.markdown("""
- **七个维度** (越高越危险): 趋势 (ES/NQ 相对 200/50 日均线、60 日区间位置), 广度 (SPY 新高而等权落后、RSP/SPY 59 日变化、成分股站上 200 日线比例),
  波动 (VIX 偏低、SKEW 偏高、实现波动/VIX、VIX9D/VIX), 信用 (HYG/IEF 20 日变化), 流动性 (美联储资产 − TGA − 逆回购, 13 周变化),
  利率 (2 年期收益率与 10 年实际利率的 6 个月变化、收益率曲线倒挂), 不确定性 (经济政策不确定性、地缘风险指数, 30 日均值)。
- 每个输入与自己过去 756 个交易日 (约三年) 比较得百分位; 维度内平均, 再对可用维度等权平均 ×100。至少 3 个维度才出分。
- 宏观数据按发布日的下一天计入, 避免用到当时还不知道的数据。
- 规则: 评分处于自身底部 20% 时, 下一收盘起持有 1.25x NQ, 否则 1.0x。从不降到 1.0x 以下。
- 与实验室版本的差别: FRED 用最新值而非首次发布值; 广度第三项用今天的 S&P 500 成分股 (实验室用全部美股); 期货用 Yahoo 连续合约。
  在 2012-2026 的实验室数据上, 本模块与实验室计算逐日一致 (分数误差 < 0.02, 仓位 3,442 天全部相同)。
- 中期选举只作为背景展示 (主页面), 2010 年以来只有 4 次, 无法检验。
""")
