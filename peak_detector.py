"""
Peak Detector - 美股顶部风险预警系统
8 个核心指标合成 0-100 风险评分

部署:
  - 本地: export FRED_API_KEY="key" && streamlit run peak_detector.py
  - Streamlit Cloud: 在 secrets 中配置 FRED_API_KEY

⚠️  本工具仅供个人研究和教育用途, 不构成投资建议
"""

import os
import warnings
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

import numpy as np
import pandas as pd
import yaml
import yfinance as yf
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from fredapi import Fred

warnings.filterwarnings("ignore")

# ============================================================
# 配置加载
# ============================================================

@st.cache_data
@st.cache_data(ttl=300)  # 5分钟过期: config.yaml 更新后无需 Reboot 即可生效
def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# v5.1 必需的 config 段落 — 启动自检, 缺失时给出明确错误而非 KeyError
REQUIRED_CONFIG_SECTIONS = [
    "weights", "risk_levels", "overrides",
    "cta_positioning", "overextension", "vix_skew", "gamma_env",
    "ad_divergence", "concentration", "putcall_proxy",
    "hy_spread", "fed_liquidity",
    "prob_table_10d", "prob_table_20d", "events",
]


def validate_config(config: dict) -> list:
    """返回缺失的段落列表; 空列表 = config 是完整的 v5.1 版本"""
    return [k for k in REQUIRED_CONFIG_SECTIONS if k not in config]


def get_fred_key() -> str:
    """优先级: Streamlit secrets > 环境变量 > 用户输入"""
    # Streamlit Cloud secrets
    try:
        if hasattr(st, "secrets") and "FRED_API_KEY" in st.secrets:
            return st.secrets["FRED_API_KEY"]
    except Exception:
        pass
    # 环境变量
    return os.getenv("FRED_API_KEY", "")


# ============================================================
# 数据加载层
# ============================================================

@st.cache_data(ttl=14400, show_spinner=False)  # 4小时缓存
def fetch_yf(ticker: str, period: str = "3y") -> pd.DataFrame:
    """从 yfinance 拉取数据 (鲁棒版)"""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        # 处理 MultiIndex columns (新版yfinance会返回 (col, ticker) 形式)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        # 验证关键列存在
        if "Close" not in df.columns:
            return pd.DataFrame()
        # 删除全部为NaN的行
        df = df.dropna(subset=["Close"])
        if len(df) == 0:
            return pd.DataFrame()
        return df
    except Exception as e:
        return pd.DataFrame()


@st.cache_data(ttl=14400, show_spinner=False)
def fetch_fred(series_id: str, api_key: str, start: str = "2020-01-01") -> pd.Series:
    """从 FRED 拉取数据"""
    if not api_key:
        return pd.Series(dtype=float)
    try:
        fred = Fred(api_key=api_key)
        s = fred.get_series(series_id, observation_start=start)
        return s.dropna()
    except Exception as e:
        st.warning(f"FRED 拉取 {series_id} 失败: {e}")
        return pd.Series(dtype=float)


# ============================================================
# 工具函数
# ============================================================

def threshold_to_score(value: float, thresholds: list) -> int:
    """根据阈值表把数值映射为 0-10 分"""
    for t in thresholds:
        if value <= t["max"]:
            return t["score"]
    return thresholds[-1]["score"]


def get_risk_level(total_score: float, risk_levels: list) -> dict:
    """根据总分返回风险等级"""
    for rl in risk_levels:
        if total_score <= rl["max"]:
            return rl
    return risk_levels[-1]


# ============================================================
# 8 个指标的计算函数
# ============================================================

def calc_cta_positioning(spx: pd.DataFrame, ndx: pd.DataFrame, config: dict,
                         as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标1: CTA Positioning Proxy
    
    改进版: 用绝对水平判断 CTA 多头拥挤度
    - 价格距 200MA 的偏离 (绝对%) → 越远说明 CTA 越满仓
    - 加上 Donchian 通道位置作为强化信号
    - 不使用滚动 z-score (会被长牛市钝化)
    - 容错: NDX 缺失时只用 SPX
    """
    if spx.empty:
        return 0, 0.0, "SPX 数据缺失"

    if as_of:
        spx = spx[spx.index <= as_of]
        if not ndx.empty:
            ndx = ndx[ndx.index <= as_of]

    if len(spx) < 200:
        return 0, 0.0, "SPX 历史数据不足"

    def intensity(df):
        close = df["Close"]
        sma200 = close.rolling(200).mean()
        sma50 = close.rolling(50).mean()
        high60 = close.rolling(60).max()
        low60 = close.rolling(60).min()

        deviation_200 = (close - sma200) / sma200
        deviation_50 = (close - sma50) / sma50
        donchian_range = (high60 - low60).replace(0, np.nan)
        donchian_pos = (close - low60) / donchian_range

        return deviation_200, deviation_50, donchian_pos

    spx_d200, spx_d50, spx_donch = intensity(spx)
    
    # NDX 可用就平均, 不可用就只用 SPX
    use_ndx = (not ndx.empty) and len(ndx) >= 200
    if use_ndx:
        ndx_d200, ndx_d50, ndx_donch = intensity(ndx)
        dev_200 = (float(spx_d200.iloc[-1]) + float(ndx_d200.iloc[-1])) / 2
        dev_50 = (float(spx_d50.iloc[-1]) + float(ndx_d50.iloc[-1])) / 2
        donch = (float(spx_donch.iloc[-1]) + float(ndx_donch.iloc[-1])) / 2
        source = "SPX+NDX"
    else:
        dev_200 = float(spx_d200.iloc[-1])
        dev_50 = float(spx_d50.iloc[-1])
        donch = float(spx_donch.iloc[-1])
        source = "SPX only"

    if np.isnan(dev_200):
        return 0, 0.0, "计算异常"

    score = threshold_to_score(dev_200, config["cta_positioning"]["thresholds"])

    if donch > 0.85 and score < 6:
        score = 6
    if dev_200 > 0.12 and donch > 0.90 and score < 8:
        score = 8

    detail = f"距200MA: {dev_200*100:+.1f}% | 距50MA: {dev_50*100:+.1f}% | Donchian: {donch:.2f} ({source})"
    return score, dev_200, detail


def calc_vix_skew(vix: pd.DataFrame, skew: pd.DataFrame, config: dict,
                  as_of: Optional[pd.Timestamp] = None) -> Tuple[int, dict, str]:
    """指标2: VIX/SKEW Complacency
    
    改进版: 用绝对水平判断 complacency
    - 滚动百分位在持续低 VIX 制度下会失效 (2021那种)
    - 改用 VIX 绝对水平直接打分
    """
    if vix.empty:
        return 0, {}, "数据缺失"

    if as_of:
        vix = vix[vix.index <= as_of]
        if not skew.empty:
            skew = skew[skew.index <= as_of]

    if len(vix) < 30:
        return 0, {}, "历史数据不足"

    current_vix = float(vix["Close"].iloc[-1])
    
    # VIX 30日均值平滑 (避免单日噪音)
    vix_ma30 = float(vix["Close"].rolling(30).mean().iloc[-1])
    effective_vix = min(current_vix, vix_ma30)  # 取较低值,更准确反映 complacency
    
    # VIX 绝对水平打分 (取代百分位)
    vix_score = threshold_to_score(effective_vix, config["vix_skew"]["vix_absolute_thresholds"])

    if not skew.empty and len(skew) > 0:
        current_skew = float(skew["Close"].iloc[-1])
        skew_ma30 = float(skew["Close"].rolling(30).mean().iloc[-1])
        effective_skew = max(current_skew, skew_ma30)  # SKEW 较高值更准
        skew_score = threshold_to_score(effective_skew, config["vix_skew"]["skew_thresholds"])
    else:
        current_skew = None
        skew_score = vix_score

    final_score = int(0.6 * vix_score + 0.4 * skew_score)

    detail = f"VIX: {current_vix:.1f} (30dMA {vix_ma30:.1f})"
    if current_skew:
        detail += f" | SKEW: {current_skew:.0f}"

    return final_score, {"vix": current_vix, "skew": current_skew}, detail


def calc_gamma_env(spx: pd.DataFrame, vix: pd.DataFrame, config: dict,
                   as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标3: Gamma Environment (realized vs implied vol)
    
    改进版: 用绝对 RV 水平兜底,因为 RV/IV 比率在低 vol 制度下噪音大
    """
    if spx.empty or vix.empty:
        return 0, 0.0, "数据缺失"

    if as_of:
        spx = spx[spx.index <= as_of]
        vix = vix[vix.index <= as_of]

    if len(spx) < 30:
        return 0, 0.0, "历史数据不足"

    returns = spx["Close"].pct_change()
    rv_30d = returns.rolling(30).std() * np.sqrt(252)

    current_rv = float(rv_30d.iloc[-1])
    current_vix_pct = float(vix["Close"].iloc[-1]) / 100

    if current_vix_pct <= 0:
        return 0, 0.0, "VIX 异常"

    vrp_ratio = current_rv / current_vix_pct
    
    # 主信号: RV/IV 比率
    score_ratio = threshold_to_score(vrp_ratio, config["gamma_env"]["vrp_ratio_thresholds"])
    
    # 兜底: 绝对 RV 水平 (低 RV = dealer long gamma, complacency)
    score_abs = threshold_to_score(current_rv, config["gamma_env"]["rv_absolute_thresholds"])
    
    # 取较大值
    score = max(score_ratio, score_abs)

    detail = f"RV(30d): {current_rv*100:.1f}% | IV: {current_vix_pct*100:.1f}% | RV/IV: {vrp_ratio:.2f}"
    return score, vrp_ratio, detail


def calc_ad_divergence(spy: pd.DataFrame, rsp: pd.DataFrame, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> Tuple[int, int, str]:
    """指标4: A/D Line Divergence (RSP/SPY proxy)"""
    if spy.empty or rsp.empty:
        return 0, 0, "数据缺失"

    if as_of:
        spy = spy[spy.index <= as_of]
        rsp = rsp[rsp.index <= as_of]

    if len(spy) < 90 or len(rsp) < 90:
        return 0, 0, "历史数据不足"

    merged = pd.DataFrame({
        "spy": spy["Close"],
        "rsp": rsp["Close"]
    }).dropna()

    if len(merged) < 90:
        return 0, 0, "数据不足"

    merged["ratio"] = merged["rsp"] / merged["spy"]
    merged["spy_high_60d"] = merged["spy"] >= merged["spy"].rolling(60).max()

    ratio_60d_ago = merged["ratio"].shift(60)
    merged["ad_falling"] = (merged["ratio"] - ratio_60d_ago) < 0

    merged["divergence"] = merged["spy_high_60d"] & merged["ad_falling"]

    duration = int(merged["divergence"].iloc[-30:].sum())
    score = threshold_to_score(duration, config["ad_divergence"]["duration_thresholds"])

    detail = f"过去30日背离 {duration} 天"
    return score, duration, detail


def calc_concentration(spy: pd.DataFrame, rsp: pd.DataFrame, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标5: Concentration Risk (RSP/SPY 60日变化)"""
    if spy.empty or rsp.empty:
        return 0, 0.0, "数据缺失"

    if as_of:
        spy = spy[spy.index <= as_of]
        rsp = rsp[rsp.index <= as_of]

    merged = pd.DataFrame({
        "spy": spy["Close"],
        "rsp": rsp["Close"]
    }).dropna()

    if len(merged) < 60:
        return 0, 0.0, "历史数据不足"

    merged["ratio"] = merged["rsp"] / merged["spy"]
    current = float(merged["ratio"].iloc[-1])
    past = float(merged["ratio"].iloc[-60])
    change_pct = (current - past) / past

    score = threshold_to_score(change_pct, config["concentration"]["thresholds"])
    detail = f"RSP/SPY 60日变化: {change_pct*100:+.2f}%"
    return score, change_pct, detail


def calc_putcall_proxy(vix9d: pd.DataFrame, vix: pd.DataFrame, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标6: Put/Call Complacency (VIX9D/VIX contango proxy)
    
    改进版: 用绝对 contango 水平
    - contango 很大 (短期VIX远低于长期VIX) = complacency
    - contango 收窄或倒挂 = 短期紧张, 已经在 stress 边缘
    - 历史校准: contango > 0.10 是典型 complacency
    """
    if vix9d.empty or vix.empty:
        return 0, 0.0, "数据缺失"

    if as_of:
        vix9d = vix9d[vix9d.index <= as_of]
        vix = vix[vix.index <= as_of]

    merged = pd.DataFrame({
        "vix9d": vix9d["Close"],
        "vix": vix["Close"]
    }).dropna()

    if len(merged) < 30:
        return 0, 0.0, "历史数据不足"

    merged["ts_ratio"] = merged["vix9d"] / merged["vix"]
    merged["contango"] = 1 - merged["ts_ratio"]

    current_contango = float(merged["contango"].iloc[-1])
    # 30日均值平滑
    contango_ma30 = float(merged["contango"].rolling(30).mean().iloc[-1])
    effective_contango = max(current_contango, contango_ma30)  # 较大值更准
    
    # 绝对 contango 水平直接打分
    score = threshold_to_score(effective_contango, config["putcall_proxy"]["contango_absolute_thresholds"])

    detail = f"VIX9D/VIX contango: {current_contango:.3f} (30dMA {contango_ma30:.3f})"
    return score, effective_contango, detail


def calc_overextension(spx: pd.DataFrame, ndx: pd.DataFrame, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标: Price Overextension (距 50MA 超买度)
    
    v5 新增: 回测中预测力最强的单一信号之一 (ret20 corr -0.11)
    单边打分: 只对上方偏离计分 (mean reversion 信号)
    """
    if spx.empty:
        return 0, 0.0, "SPX 数据缺失"

    s = spx[spx.index <= as_of]["Close"] if as_of is not None else spx["Close"]
    if len(s) < 50:
        return 0, 0.0, "历史数据不足"

    s_ma = s.rolling(50).mean()
    s_dev = (float(s.iloc[-1]) - float(s_ma.iloc[-1])) / float(s_ma.iloc[-1])

    use_ndx = (not ndx.empty)
    if use_ndx:
        n = ndx[ndx.index <= as_of]["Close"] if as_of is not None else ndx["Close"]
        if len(n) >= 50:
            n_ma = n.rolling(50).mean()
            n_dev = (float(n.iloc[-1]) - float(n_ma.iloc[-1])) / float(n_ma.iloc[-1])
            dev = (s_dev + n_dev) / 2
        else:
            dev = s_dev
    else:
        dev = s_dev

    score = threshold_to_score(dev, config["overextension"]["thresholds"])
    detail = f"距50MA: {dev*100:+.1f}% (SPX+NDX平均)"
    return score, dev, detail


def calc_smt_divergence(spx: pd.DataFrame, ndx: pd.DataFrame, config: dict,
                        as_of: Optional[pd.Timestamp] = None) -> Tuple[int, int, str]:
    """指标: SMT 背离 (NQ vs ES, 日线)
    
    ICT 概念: 最近10日内一个指数创60日新高而另一个没有 = 背离
    ⚠️ 回测显示日线 SMT 对未来回报无预测力 (corr +0.01),
    因此【仅展示, 不计入总分】(权重=0)。日内 SMT 在 killzone 内仍有效,
    但日线频率上信号消失。
    """
    if spx.empty or ndx.empty:
        return 0, 0, "数据缺失"

    s = spx[spx.index <= as_of]["Close"] if as_of is not None else spx["Close"]
    n = ndx[ndx.index <= as_of]["Close"] if as_of is not None else ndx["Close"]
    if len(s) < 80 or len(n) < 80:
        return 0, 0, "历史数据不足"

    high_lookback, recent_window = 60, 10
    s_roll_max = s.rolling(high_lookback).max()
    n_roll_max = n.rolling(high_lookback).max()
    s_made = bool((s >= s_roll_max * 0.9995).iloc[-recent_window:].any())
    n_made = bool((n >= n_roll_max * 0.9995).iloc[-recent_window:].any())

    if s_made and n_made:
        return 0, 0, "ES/NQ 双双新高, 无背离"
    if not s_made and not n_made:
        return 0, 0, "双双无新高, 非顶部区域"

    s_nh = (s >= s_roll_max * 0.9995).iloc[-20:]
    n_nh = (n >= n_roll_max * 0.9995).iloc[-20:]
    div_days = int((s_nh != n_nh).sum())
    leader = "NQ创新高/ES未确认 ⚠️" if n_made else "ES创新高/NQ未确认 ⚠️"
    score = 9 if div_days >= 10 else 7 if div_days >= 5 else 5
    return score, div_days, f"{leader} 背离持续{div_days}日"


def calc_hy_spread(hy: pd.Series, config: dict,
                   as_of: Optional[pd.Timestamp] = None) -> Tuple[int, dict, str]:
    """指标7: HY Credit Spread"""
    if hy.empty:
        return 0, {}, "FRED 数据缺失"

    if as_of:
        hy = hy[hy.index <= as_of]

    if len(hy) < 252:
        return 0, {}, "历史数据不足"

    current = float(hy.iloc[-1])
    past_20d = float(hy.iloc[-21]) if len(hy) >= 21 else current
    change_bps = (current - past_20d) * 100

    history = hy.iloc[-252:]
    percentile = (history < current).sum() / len(history)

    change_score = threshold_to_score(change_bps, config["hy_spread"]["change_thresholds"])
    pct_score = threshold_to_score(percentile, config["hy_spread"]["percentile_thresholds"])
    
    # 绝对水平兜底: HY < 3.5% 是历史性 complacency, 信贷市场对风险定价过低
    # 反过来 HY > 6% 是已经在 stress
    abs_score = threshold_to_score(current, config["hy_spread"]["absolute_thresholds"])
    
    # 综合: 利差扩大或处于历史高位 = 风险已显现 (取较大)
    #       利差极低 = complacency,反向风险 (单独打分)
    risk_score = max(change_score, pct_score)
    final_score = max(risk_score, abs_score)

    detail = f"HY OAS: {current:.2f}% | 20日: {change_bps:+.0f}bps | P{percentile*100:.0f}"
    return final_score, {"current": current, "change_bps": change_bps, "percentile": percentile}, detail


def calc_fed_liquidity(walcl: pd.Series, wtregen: pd.Series, rrp: pd.Series, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> Tuple[int, float, str]:
    """指标8: Fed Net Liquidity Impulse"""
    if walcl.empty or wtregen.empty or rrp.empty:
        return 0, 0.0, "FRED 数据缺失"

    if as_of:
        walcl = walcl[walcl.index <= as_of]
        wtregen = wtregen[wtregen.index <= as_of]
        rrp = rrp[rrp.index <= as_of]

    merged = pd.DataFrame({
        "walcl": walcl,
        "wtregen": wtregen,
        "rrp": rrp
    })
    merged = merged.resample("W").last().ffill().dropna()

    if len(merged) < 14:
        return 0, 0.0, "历史数据不足"

    merged["net_liq"] = merged["walcl"] - merged["wtregen"] - merged["rrp"] * 1000

    current = float(merged["net_liq"].iloc[-1])
    past_13w = float(merged["net_liq"].iloc[-14])

    if past_13w == 0:
        return 0, 0.0, "计算异常"

    change_pct = (current - past_13w) / abs(past_13w)
    score = threshold_to_score(change_pct, config["fed_liquidity"]["thresholds"])
    detail = f"净流动性 13周变化: {change_pct*100:+.2f}%"
    return score, change_pct, detail


# ============================================================
# Override 检查
# ============================================================

def check_overrides(vix: pd.DataFrame, spx: pd.DataFrame, hy: pd.Series,
                    config: dict, as_of: Optional[pd.Timestamp] = None,
                    jpy: Optional[pd.DataFrame] = None) -> list:
    """检查是否触发强制升档规则"""
    triggers = []

    if as_of:
        vix = vix[vix.index <= as_of] if not vix.empty else vix
        spx = spx[spx.index <= as_of] if not spx.empty else spx
        hy = hy[hy.index <= as_of] if not hy.empty else hy

    overrides = config["overrides"]

    if not vix.empty and len(vix) >= 2:
        today = float(vix["Close"].iloc[-1])
        yesterday = float(vix["Close"].iloc[-2])
        daily_change = (today - yesterday) / yesterday
        if daily_change > overrides["vix_spike"]["daily_change_pct"] and today > overrides["vix_spike"]["close_above"]:
            triggers.append(f"⚠️ VIX 单日飙升 {daily_change*100:.0f}% 至 {today:.1f}")

    if not hy.empty and len(hy) >= 21:
        change_bps = (float(hy.iloc[-1]) - float(hy.iloc[-21])) * 100
        if change_bps > overrides["hy_blowout"]["change_20d_bps"]:
            triggers.append(f"⚠️ HY 利差 20日扩大 {change_bps:.0f}bps")

    if overrides["spx_ma200_break"]["enabled"] and not spx.empty and len(spx) >= 260:
        close = spx["Close"]
        ma200 = close.rolling(200).mean()
        lookback = overrides["spx_ma200_break"]["lookback_days"]

        current_below = close.iloc[-1] < ma200.iloc[-1]
        recent_above = (close.iloc[-lookback-1:-1] > ma200.iloc[-lookback-1:-1]).all()

        if current_below and recent_above:
            triggers.append(f"⚠️ 标普首次跌破 200 日均线")

    # v5.1: 日元 carry unwind 警报
    # 回测: USDJPY 20日跌>3% 时, 未来20日跌>5%概率从14.7%升至22.1%
    if "yen_carry" in overrides and jpy is not None and not jpy.empty:
        j = jpy[jpy.index <= as_of]["Close"] if as_of is not None else jpy["Close"]
        if len(j) >= 21:
            jpy_20d = (float(j.iloc[-1]) - float(j.iloc[-21])) / float(j.iloc[-21]) * 100
            if jpy_20d < overrides["yen_carry"]["jpy_20d_change_pct"]:
                triggers.append(f"⚠️ 日元急升: USDJPY 20日 {jpy_20d:+.1f}% (carry unwind 风险, 参考2024年8月)")

    return triggers


def third_friday(year: int, month: int) -> pd.Timestamp:
    """月度 OPEX 日 (第三个周五)"""
    start = pd.Timestamp(year=year, month=month, day=1)
    fridays = pd.date_range(start, start + pd.offsets.MonthEnd(0), freq='W-FRI')
    return fridays[2]


def get_upcoming_events(config: dict, as_of: pd.Timestamp, horizon_days: int = 14) -> list:
    """收集未来 N 天的事件: FOMC/BoJ/CPI/OPEX/手动事件
    返回 [{date, name, type, days_until}] 按日期排序"""
    events = []
    ev_cfg = config.get("events", {})
    as_of_date = pd.Timestamp(as_of).normalize()
    end = as_of_date + pd.Timedelta(days=horizon_days)

    type_map = [("fomc", "FOMC 利率决议", "policy"),
                ("boj", "日银决议 (carry风险)", "policy"),
                ("cpi", "美国 CPI 公布", "macro")]
    for key, label, etype in type_map:
        for d_str in ev_cfg.get(key, []):
            d = pd.Timestamp(d_str)
            if as_of_date <= d <= end:
                events.append({"date": d, "name": label, "type": etype,
                               "days_until": (d - as_of_date).days})

    # OPEX (任意年份可计算)
    for m_offset in range(0, 2):
        target = as_of_date + pd.offsets.MonthBegin(m_offset) if m_offset > 0 else as_of_date.replace(day=1)
        opex = third_friday(target.year, target.month)
        if as_of_date <= opex <= end:
            quad = "(季度三巫日)" if target.month in (3, 6, 9, 12) else ""
            events.append({"date": opex, "name": f"月度 OPEX {quad}", "type": "opex",
                           "days_until": (opex - as_of_date).days})

    # 手动事件 (IPO 等)
    for ev in ev_cfg.get("manual_events", []):
        d = pd.Timestamp(ev["date"])
        if as_of_date <= d <= end:
            events.append({"date": d, "name": ev["name"], "type": ev.get("type", "other"),
                           "days_until": (d - as_of_date).days})

    return sorted(events, key=lambda x: x["date"])


# ============================================================
# 评分聚合
# ============================================================

def compute_all_scores(data: dict, config: dict,
                       as_of: Optional[pd.Timestamp] = None) -> dict:
    """计算所有 8 个指标并汇总"""
    results = {}

    score, raw, detail = calc_cta_positioning(data["spx"], data["ndx"], config, as_of)
    results["cta_positioning"] = {"score": score, "raw": raw, "detail": detail, "name": "CTA Positioning"}

    score, raw, detail = calc_vix_skew(data["vix"], data["skew"], config, as_of)
    results["vix_skew"] = {"score": score, "raw": raw, "detail": detail, "name": "VIX/SKEW Complacency"}

    score, raw, detail = calc_gamma_env(data["spx"], data["vix"], config, as_of)
    results["gamma_env"] = {"score": score, "raw": raw, "detail": detail, "name": "Gamma Environment"}

    score, raw, detail = calc_ad_divergence(data["spy"], data["rsp"], config, as_of)
    results["ad_divergence"] = {"score": score, "raw": raw, "detail": detail, "name": "A/D Divergence"}

    score, raw, detail = calc_concentration(data["spy"], data["rsp"], config, as_of)
    results["concentration"] = {"score": score, "raw": raw, "detail": detail, "name": "Concentration Risk"}

    score, raw, detail = calc_putcall_proxy(data["vix9d"], data["vix"], config, as_of)
    results["putcall_proxy"] = {"score": score, "raw": raw, "detail": detail, "name": "Put/Call Proxy"}

    score, raw, detail = calc_overextension(data["spx"], data["ndx"], config, as_of)
    results["overextension"] = {"score": score, "raw": raw, "detail": detail, "name": "Price Overextension"}

    score, raw, detail = calc_hy_spread(data["hy"], config, as_of)
    results["hy_spread"] = {"score": score, "raw": raw, "detail": detail, "name": "HY Credit Spread"}

    score, raw, detail = calc_fed_liquidity(data["walcl"], data["wtregen"], data["rrp"], config, as_of)
    results["fed_liquidity"] = {"score": score, "raw": raw, "detail": detail, "name": "Fed Liquidity"}

    weights = config["weights"]
    total = sum(results[k]["score"] * weights[k] for k in weights) * 10

    # SMT 背离: 仅展示, 不计入总分 (回测显示日线频率无预测力)
    smt_score, smt_raw, smt_detail = calc_smt_divergence(data["spx"], data["ndx"], config, as_of)
    smt_info = {"score": smt_score, "raw": smt_raw, "detail": smt_detail, "name": "SMT Divergence (NQ/ES)"}

    # 回撤概率查询 (基于2010-2026回测的经验分布)
    prob_info = None
    if "prob_table_20d" in config:
        for row in config["prob_table_20d"]:
            if row["lo"] <= total < row["hi"]:
                prob_info = {"d20": row}
                break
        if prob_info and "prob_table_10d" in config:
            for row in config["prob_table_10d"]:
                if row["lo"] <= total < row["hi"]:
                    prob_info["d10"] = row
                    break

    triggers = check_overrides(data["vix"], data["spx"], data["hy"], config, as_of,
                               jpy=data.get("jpy"))

    # as_of 默认用数据实际末尾日期 (规避 Streamlit Cloud 时区/时钟问题)
    if as_of is None:
        if not data["spx"].empty:
            as_of_display = data["spx"].index[-1]
        else:
            as_of_display = pd.Timestamp.now()
    else:
        as_of_display = as_of

    # v5.1: 事件风险窗口 (未来14日)
    upcoming_events = get_upcoming_events(config, as_of_display, horizon_days=14)
    # 高分 + 重大事件5日内 → 升档建议 (事件本身不改分数)
    major_soon = [e for e in upcoming_events
                  if e["type"] in ("policy", "macro", "liquidity") and e["days_until"] <= 5]
    if total >= 55 and major_soon:
        names = ", ".join(f"{e['name']}({e['days_until']}日后)" for e in major_soon[:3])
        triggers.append(f"📅 高风险分数 + 事件临近: {names} → 建议按上一档风险对待")

    return {
        "indicators": results,
        "total_score": total,
        "triggers": triggers,
        "as_of": as_of_display,
        "smt": smt_info,
        "prob": prob_info,
        "events": upcoming_events
    }


# ============================================================
# 数据批量加载
# ============================================================

@st.cache_data(ttl=14400, show_spinner="正在拉取市场数据 (首次加载较慢, 约 30-60 秒)...")
def load_all_data(fred_api_key: str, period: str = "max") -> dict:
    """一次性加载所有需要的数据
    
    period='max' 拉取全部历史
    带 fallback 机制: Streamlit Cloud 上 ^GSPC, ^NDX 等 ticker 经常失败,
    自动回退到 SPY, QQQ 等 ETF 数据
    """
    def fetch_with_fallback(primary: str, fallback: str, period: str):
        """先试 primary, 失败用 fallback"""
        df = fetch_yf(primary, period)
        if df.empty or len(df) < 100:
            df = fetch_yf(fallback, period)
        return df
    
    data = {
        # SPX: 优先 ^GSPC, 失败用 SPY
        "spx": fetch_with_fallback("^GSPC", "SPY", period),
        # NDX: 优先 ^NDX, 失败用 QQQ
        "ndx": fetch_with_fallback("^NDX", "QQQ", period),
        "spy": fetch_yf("SPY", period),
        "rsp": fetch_yf("RSP", period),
        "vix": fetch_yf("^VIX", period),
        "vix9d": fetch_yf("^VIX9D", period),  # 2011年才有
        "skew": fetch_yf("^SKEW", period),
        "jpy": fetch_yf("JPY=X", period),  # USDJPY, carry unwind 警报用
    }
    
    # 如果 SPY 主数据也失败了, 用 spx 兜底
    if data["spy"].empty:
        data["spy"] = data["spx"]
    
    # 如果 rsp 失败, ad_divergence 和 concentration 会失效, 但其他指标不影响

    # FRED 拉 20 年历史
    start_date = "2005-01-01"
    data["hy"] = fetch_fred("BAMLH0A0HYM2", fred_api_key, start_date)
    data["walcl"] = fetch_fred("WALCL", fred_api_key, start_date)
    data["wtregen"] = fetch_fred("WTREGEN", fred_api_key, start_date)
    data["rrp"] = fetch_fred("RRPONTSYD", fred_api_key, start_date)

    return data


# ============================================================
# 历史回测
# ============================================================

@st.cache_data(ttl=14400, show_spinner="正在计算历史评分...")
def compute_historical_scores(_data: dict, _config: dict, days: int = 730) -> pd.DataFrame:
    """计算过去 N 天的每日总分"""
    spx = _data["spx"]
    if spx.empty or len(spx) < days:
        return pd.DataFrame()

    dates = list(spx.index[-days::5])
    # 确保最新交易日始终包含 (修复: 周采样导致曲线滞后最多4个交易日)
    if len(dates) == 0 or dates[-1] != spx.index[-1]:
        dates.append(spx.index[-1])

    ndx = _data.get("ndx", pd.DataFrame())

    records = []
    for date in dates:
        try:
            result = compute_all_scores(_data, _config, as_of=date)
            rec = {
                "date": date,
                "total_score": result["total_score"],
                "spx_close": float(spx.loc[spx.index <= date, "Close"].iloc[-1])
            }
            if not ndx.empty:
                nd = ndx.loc[ndx.index <= date, "Close"]
                rec["ndx_close"] = float(nd.iloc[-1]) if len(nd) else None
            records.append(rec)
        except Exception:
            continue

    return pd.DataFrame(records).set_index("date")


# ============================================================
# Streamlit UI
# ============================================================

# ============================================================
# 宏观背景 (仅供参考, 不计入评分) — 2026-11-20 新增
# 利率周期、政策/地缘不确定性、美国中期选举周期。
# nq-replay 实验室回测 (research/peaktop/v12.py, 2012-2026): 利率上行对后续 NQ 略偏负 (IC -0.13);
# 政策/地缘不确定性方向相反: 高不确定性之后 NQ 反而略好 (IC +0.09)。中期选举只有 4 个样本, 无法检验。
# ============================================================

def midterm_date(year: int) -> pd.Timestamp:
    """美国大选日: 11 月第一个周一之后的周二"""
    d = pd.Timestamp(year, 11, 1)
    first_mon = d + pd.Timedelta(days=(0 - d.dayofweek) % 7)
    return first_mon + pd.Timedelta(days=1)


def election_cycle_phase(ref: pd.Timestamp) -> Tuple[str, str]:
    """返回 (阶段, 说明)。中期选举年 = 年份除以 4 余 2 (2022, 2026, 2030)"""
    y = ref.year
    if y % 4 == 2:
        e = midterm_date(y)
        if ref < e:
            return "中期选举年 (选前)", f"距中期选举 {(e - ref).days} 天 ({e.date()})。历史上选前数月波动偏大、回报偏弱。"
        return "中期选举后", f"{e.date()} 中期选举后。历史上选后 12 个月美股偏强 (样本少, 仅作参考)。"
    if y % 4 == 3:
        e = midterm_date(y - 1)
        if ref < e + pd.Timedelta(days=365):
            return "中期选举后 12 个月内", f"{e.date()} 中期选举后第 {(ref - e).days} 天。历史上这一段偏强 (样本少, 仅作参考)。"
        return "大选前一年", "历史上大选前一年整体偏强。"
    if y % 4 == 0:
        return "总统大选年", f"大选日 {midterm_date(y).date()}。政策不确定性通常在选前升高。"
    return "新总统第一年", "新政府政策落地期, 历史上回报分化较大。"


def macro_context(fred_key: str, ref: pd.Timestamp) -> dict:
    """利率与政策不确定性读数 (截至 ref)。缺数据时对应项为 None, 不会显示为 0。"""
    out = {}
    def last_change(sid, n_obs):
        s = fetch_fred(sid, fred_key, "2018-01-01")
        if s.empty:
            return None, None
        s = s[s.index <= ref]
        if len(s) <= n_obs:
            return None, None
        return float(s.iloc[-1]), float(s.iloc[-1] - s.iloc[-1 - n_obs])
    out["dgs2"] = last_change("DGS2", 126)          # 约 6 个月
    out["dfii10"] = last_change("DFII10", 126)
    out["curve"] = last_change("T10Y2Y", 126)
    epu = fetch_fred("USEPUINDXD", fred_key, "2018-01-01")
    if not epu.empty:
        epu = epu[epu.index <= ref]
    if len(epu) > 800:
        m30 = epu.rolling(30, min_periods=20).mean()
        hist = m30.iloc[-756:].dropna()
        out["epu"] = (float(m30.iloc[-1]), float((hist < m30.iloc[-1]).mean()))
    else:
        out["epu"] = None
    out["cycle"] = election_cycle_phase(ref)
    return out


def main():
    st.set_page_config(
        page_title="Peak Detector | 美股顶部风险预警",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": "https://github.com/mikeli008008/peaktop",
            "Report a Bug": "https://github.com/mikeli008008/peaktop/issues",
            "About": "Peak Detector - 美股顶部风险预警系统. 仅供研究和教育用途."
        }
    )

    try:
        config = load_config()
    except yaml.YAMLError as e:
        st.error(
            f"❌ **config.yaml 文件损坏 (YAML 语法错误)**\n\n"
            f"最常见原因: 编辑时旧内容没删干净, 新旧混在一起。\n\n"
            f"修复: 不要手动粘贴 — 用 GitHub 的 **Add file → Upload files** 上传完整的 config.yaml 文件整体覆盖。\n\n"
            f"错误详情 (含行号):\n```\n{e}\n```"
        )
        st.stop()

    # config 版本自检: 缺段落时给出明确指引
    missing = validate_config(config)
    if missing:
        st.error(
            f"❌ **config.yaml 版本不匹配** — 缺少以下段落: `{'`, `'.join(missing)}`\n\n"
            f"GitHub 上的 config.yaml 不是最新的 v5.1 版本。修复方法:\n"
            f"1. 在 GitHub 打开 config.yaml → 编辑 → 全选删除 → 粘贴完整的 v5.1 config → Commit\n"
            f"2. 提交后在 Raw 视图确认能搜到 `overextension`、`prob_table_20d`、`events` 三个关键词\n"
            f"3. 等待自动重新部署 (本检查每5分钟刷新, 无需 Reboot)"
        )
        st.stop()

    # 标题
    st.title("📊 Peak Detector")
    st.caption("美股顶部风险预警系统 | 8 核心指标 → 0-100 风险评分 | ⚠️ 仅供研究和教育用途, 不构成投资建议")

    # 顶部免责声明 (折叠)
    with st.expander("⚠️ 重要免责声明 (首次使用请阅读)"):
        st.markdown("""
        **本工具不构成任何投资建议**

        - 本系统是基于公开数据的**研究和教育**工具
        - 所有输出仅供参考, 不能替代专业财务顾问
        - 过往表现不代表未来收益
        - 任何投资决策由用户**自行承担**全部责任
        - 作者及贡献者不对使用本工具产生的任何损失负责
        - 本工具不收集、存储或传输任何用户的金融账户信息

        数据来源: FRED (Federal Reserve Economic Data), Yahoo Finance
        """)

    # 侧边栏 — 设置部分
    with st.sidebar:
        st.header("⚙️ 设置")

        fred_key = get_fred_key()
        if not fred_key:
            fred_key = st.text_input("FRED API Key", type="password",
                                     help="在 https://fredaccount.stlouisfed.org/apikeys 免费申请")
        else:
            st.success("✅ FRED API Key 已加载")

    if not fred_key:
        st.warning("⚠️ 请在侧边栏输入 FRED API Key")
        st.info("免费申请地址: https://fredaccount.stlouisfed.org/apikeys (30秒)")
        st.markdown("""
        ### 为什么需要 FRED API Key
        
        本工具的 2 个宏观指标 (HY 信用利差、Fed 净流动性) 数据来自 
        Federal Reserve Economic Data (FRED).免费,无限制使用.
        
        申请步骤:
        1. 访问 https://fredaccount.stlouisfed.org/apikeys
        2. 注册账户 (邮箱即可)
        3. 复制 API Key 粘贴到上方
        4. 浏览器关闭后 key 不会被保存,下次需要重新粘贴
        """)
        return

    # 加载数据 (拉取 20+ 年历史)
    data = load_all_data(fred_key)

    # 用真实数据末尾日期作为日期选择器上限 (解决 Streamlit Cloud 时区问题)
    if not data["spx"].empty:
        data_max_date = data["spx"].index[-1].date()
        data_min_date = data["spx"].index[0].date()
    else:
        data_max_date = datetime.now().date()
        data_min_date = datetime(2005, 1, 1).date()

    # 侧边栏 — 历史回看 (放在数据加载后)
    with st.sidebar:
        st.divider()
        st.subheader("📅 历史回看")
        st.caption(f"数据范围: {data_min_date} 至 {data_max_date}")
        
        use_historical = st.checkbox("查看历史某日评分")
        as_of_date = None
        if use_historical:
            default_date = min(datetime(2022, 1, 3).date(), data_max_date)
            as_of_date = st.date_input(
                "选择日期",
                value=default_date,
                min_value=data_min_date,
                max_value=data_max_date
            )
            as_of_date = pd.Timestamp(as_of_date)

        st.divider()
        st.subheader("📖 关于")
        st.markdown("""
        - 8 个指标覆盖 5 大维度
        - 总分 ≥ 65 进入风险窗口
        - 总分变化速度比绝对值更重要
        - 详见 [methodology.md](https://github.com/mikeli008008/peaktop/blob/main/methodology.md)
        """)
        st.divider()
        st.caption("🌟 喜欢的话给个 [GitHub Star](https://github.com/mikeli008008/peaktop)")

    # 计算评分
    result = compute_all_scores(data, config, as_of=as_of_date)
    total = result["total_score"]
    risk_level = get_risk_level(total, config["risk_levels"])
    
    # 数据加载诊断 (折叠在 expander 里, 默认不显示)
    with st.expander("🔧 数据加载诊断 (排错用)"):
        diag_cols = st.columns(4)
        data_status = [
            ("SPX ^GSPC", data["spx"]),
            ("NDX ^NDX", data["ndx"]),
            ("SPY", data["spy"]),
            ("RSP", data["rsp"]),
            ("VIX ^VIX", data["vix"]),
            ("VIX9D ^VIX9D", data["vix9d"]),
            ("SKEW ^SKEW", data["skew"]),
            ("HY (FRED)", data["hy"]),
            ("WALCL (FRED)", data["walcl"]),
            ("WTREGEN (FRED)", data["wtregen"]),
            ("RRP (FRED)", data["rrp"]),
        ]
        for i, (name, df) in enumerate(data_status):
            with diag_cols[i % 4]:
                if hasattr(df, 'empty') and df.empty:
                    st.error(f"❌ {name}: 空")
                else:
                    n = len(df)
                    if hasattr(df, 'index') and len(df) > 0:
                        last_date = df.index[-1]
                        st.success(f"✅ {name}: {n} 条, 末日 {last_date.strftime('%Y-%m-%d') if hasattr(last_date, 'strftime') else last_date}")
                    else:
                        st.warning(f"⚠️ {name}: {n} 条")
        
        st.markdown("**各指标原始详情:**")
        for key, ind in result["indicators"].items():
            st.text(f"{ind['name']:25s} | 分数 {ind['score']}/10 | {ind['detail']}")

    # ============== 顶部:总分大显示 ==============
    date_str = result["as_of"].strftime("%Y-%m-%d")
    st.markdown(f"#### 评分日期: {date_str}")

    col1, col2, col3 = st.columns([1.1, 1.3, 1.0], gap="medium")

    with col1:
        st.markdown(
            f"""
            <div style='text-align:center; padding: 24px 20px;
                        background: linear-gradient(135deg, {risk_level["color"]}22, {risk_level["color"]}44);
                        border-radius: 12px; border-left: 6px solid {risk_level["color"]};'>
                <div style='font-size: 15px; color: #888;'>当前风险评分</div>
                <div style='font-size: 64px; font-weight: bold; line-height: 1.1; color: {risk_level["color"]};'>
                    {total:.1f}
                </div>
                <div style='font-size: 22px; margin-top: 6px;'>{risk_level["level"]}</div>
                <div style='font-size: 14px; color: #aaa; margin-top: 6px;'>{risk_level["advice"]}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # SMT 状态作为紧凑徽章放在分数卡下方
        smt = result.get("smt")
        if smt:
            smt_bg = "#7c2d1222" if smt["score"] > 0 else "#1a1a1a"
            smt_border = "#f97316" if smt["score"] > 0 else "#333"
            st.markdown(
                f"""
                <div style='margin-top: 10px; padding: 10px 14px; border-radius: 8px;
                            border: 1px solid {smt_border}; background: {smt_bg};
                            font-size: 13px;'>
                    🔀 <b>SMT (NQ/ES 日线)</b>: {smt['detail']}
                    <span style='color:#666;'>· 仅展示不计分</span>
                </div>
                """,
                unsafe_allow_html=True
            )

    with col2:
        st.markdown("##### 📍 各维度贡献 (合计 = 总分)")
        weights = config["weights"]
        contrib = []
        for key in weights:
            ind = result["indicators"][key]
            contrib.append((ind["name"], ind["score"] * weights[key] * 10, ind["score"]))
        contrib.sort(key=lambda x: x[1])

        bar_colors = ["#22c55e" if s <= 3 else "#eab308" if s <= 6 else "#ef4444"
                      for _, _, s in contrib]
        fig_c = go.Figure(go.Bar(
            x=[c for _, c, _ in contrib],
            y=[n for n, _, _ in contrib],
            orientation="h",
            marker=dict(color=bar_colors),
            text=[f"{c:.1f} ({s}/10)" for _, c, s in contrib],
            textposition="outside",
            textfont=dict(size=11),
            hovertemplate="%{y}: 贡献 %{x:.1f}<extra></extra>"
        ))
        fig_c.update_layout(
            height=340,
            margin=dict(l=0, r=55, t=5, b=5),
            template="plotly_dark",
            xaxis=dict(title=None, range=[0, max(c for _, c, _ in contrib) * 1.35]),
            yaxis=dict(tickfont=dict(size=11)),
            showlegend=False,
        )
        st.plotly_chart(fig_c, use_container_width=True)

    with col3:
        st.markdown("##### 🚨 Override 触发")
        if result["triggers"]:
            for t in result["triggers"]:
                st.error(t)
            st.warning("**建议强制升一档风险等级**")
        else:
            st.success("✅ 无 Override 触发")

    st.divider()

    # ============== 回撤概率面板 (基于历史回测) ==============
    prob = result.get("prob")
    if prob:
        st.markdown("##### 📉 历史回撤概率 — 当前分数段 (2010-2026 回测)")
        d10 = prob.get("d10")
        d20 = prob.get("d20")
        mcols = st.columns(6)
        if d10:
            mcols[0].metric("10日 回撤>3%", f"{d10['p3']}%")
            mcols[1].metric("10日 回撤>5%", f"{d10['p5']}%")
            mcols[2].metric("10日 中位回撤", f"{d10['med_dd']}%")
        if d20:
            mcols[3].metric("20日 回撤>5%", f"{d20['p5']}%")
            mcols[4].metric("20日 回撤>10%", f"{d20['p10']}%")
            mcols[5].metric("20日 平均回报", f"{d20['mean_ret']:+}%",
                            delta=f"样本 n={d20['n']}", delta_color="off")
        st.caption("⚠️ 经验分布仅供参考, 不构成预测. 极低分(0-25)区间回撤概率同样高(崩盘延续期), 绿灯≠安全, 需结合分数变化方向判断.")

    st.divider()

    # ============== 事件风险窗口 ==============
    events = result.get("events") or []
    st.markdown("##### 🗓️ 事件风险窗口 (未来 14 日)")
    if events:
        type_icons = {"policy": "🏛️", "macro": "📊", "opex": "📆", "liquidity": "💧", "other": "📌"}
        ecols = st.columns(4)
        for i, ev in enumerate(events[:8]):
            with ecols[i % 4]:
                icon = type_icons.get(ev["type"], "📌")
                days = ev["days_until"]
                border = "#ef4444" if days <= 2 else "#f97316" if days <= 5 else "#555"
                day_label = "今天" if days == 0 else f"{days}日后"
                st.markdown(
                    f"""
                    <div style='padding: 10px 12px; border-radius: 8px; margin-bottom: 8px;
                                border: 1px solid #333; border-left: 4px solid {border};
                                background: #1a1a1a; min-height: 62px;'>
                        <div style='font-size: 13px; font-weight: 600;'>{icon} {ev['name']}</div>
                        <div style='font-size: 12px; color: #888; margin-top: 3px;'>
                            {ev['date'].strftime('%m-%d %a')} · <b style='color:{border};'>{day_label}</b>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
        st.caption("事件不计入评分; 高分(≥55)+重大事件5日内会在 Override 区给出升档建议. "
                   "日历覆盖2026年(FOMC/BoJ官网确认, CPI确认至8月); IPO等手动事件在 config.yaml 的 manual_events 维护.")
    else:
        st.info("未来 14 日无已登记的重大事件 (日历覆盖2026年; 历史回看模式下仅显示 OPEX)")

    st.divider()

    # ============== 中部:各指标详情 ==============
    st.subheader("🔍 9 大指标详情")

    indicators_per_row = 4
    keys = list(config["weights"].keys())
    for row_start in range(0, len(keys), indicators_per_row):
        cols = st.columns(indicators_per_row)
        for i, key in enumerate(keys[row_start:row_start + indicators_per_row]):
            ind = result["indicators"][key]
            with cols[i]:
                score = ind["score"]
                color = "#22c55e" if score <= 3 else "#eab308" if score <= 6 else "#ef4444"
                st.markdown(
                    f"""
                    <div style='padding: 12px; border-radius: 8px;
                                border: 1px solid #444;
                                background: #1a1a1a;'>
                        <div style='color: #aaa; font-size: 13px;'>{ind['name']}</div>
                        <div style='font-size: 36px; font-weight: bold; color: {color};'>
                            {score}<span style='font-size: 18px; color: #666;'>/10</span>
                        </div>
                        <div style='font-size: 12px; color: #888;'>{ind['detail']}</div>
                        <div style='font-size: 11px; color: #666; margin-top: 4px;'>
                            权重: {config['weights'][key]*100:.0f}%
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

    # ============== 宏观背景 (不计入评分) ==============
    st.subheader("🏛️ 宏观背景 (仅供参考, 不计入评分)")
    ref_date = pd.Timestamp(as_of_date) if as_of_date else pd.Timestamp(datetime.now().date())
    mc = macro_context(fred_key, ref_date)
    mcols = st.columns(5)
    def show(col, label, val, help_txt):
        with col:
            st.metric(label, val, help=help_txt)
    d = mc["dgs2"]; show(mcols[0], "2年期美债收益率 (6个月变化)",
                         f"{d[0]:.2f}% ({d[1]:+.2f})" if d[0] is not None else "数据缺失",
                         "利率上行 = 企业借贷成本上升。回测中略偏负 (IC -0.13), 2022 年后更明显。")
    d = mc["dfii10"]; show(mcols[1], "10年期实际利率 (6个月变化)",
                          f"{d[0]:.2f}% ({d[1]:+.2f})" if d[0] is not None else "数据缺失",
                          "扣除通胀后的借贷成本 (TIPS)。")
    d = mc["curve"]; show(mcols[2], "10年-2年利差",
                         f"{d[0]:+.2f}%" if d[0] is not None else "数据缺失",
                         "负值 = 收益率曲线倒挂。")
    e = mc["epu"]; show(mcols[3], "政策不确定性 (EPU, 30日均值)",
                       f"{e[0]:.0f} (近3年 P{e[1]*100:.0f})" if e else "数据缺失",
                       "Baker-Bloom-Davis 每日指数。注意: 回测中高不确定性之后 NQ 反而略好, 方向与直觉相反。")
    ph, note = mc["cycle"]; show(mcols[4], "美国选举周期", ph, note)
    st.caption(f"选举周期: {note} 2010-2026 只有 4 次中期选举, 无法做统计检验, 因此不计入总分。")

    st.divider()

    # ============== 下部:历史曲线 ==============
    st.subheader("📈 历史风险评分曲线 (过去 ~2 年, 每周采样 + 最新交易日)")

    with st.spinner("计算历史评分..."):
        hist = compute_historical_scores(data, config, days=500)

    if not hist.empty:
        has_ndx = "ndx_close" in hist.columns and hist["ndx_close"].notna().any()
        n_rows = 3 if has_ndx else 2
        fig = make_subplots(
            rows=n_rows, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.5, 0.25, 0.25] if has_ndx else [0.6, 0.4],
            subplot_titles=("风险评分", "纳指 NDX (NQ 现货)", "标普 500") if has_ndx
                            else ("风险评分", "标普 500")
        )

        fig.add_trace(
            go.Scatter(
                x=hist.index, y=hist["total_score"],
                mode="lines+markers",
                line=dict(color="#f97316", width=2),
                marker=dict(size=4),
                name="Total Score",
                hovertemplate="%{x|%Y-%m-%d}<br>评分: %{y:.1f}<extra></extra>"
            ),
            row=1, col=1
        )

        for rl in config["risk_levels"][:-1]:
            fig.add_hline(
                y=rl["max"], line_dash="dot", line_color=rl["color"],
                opacity=0.4, row=1, col=1
            )

        if has_ndx:
            fig.add_trace(
                go.Scatter(
                    x=hist.index, y=hist["ndx_close"],
                    mode="lines",
                    line=dict(color="#a78bfa", width=2),
                    name="NDX",
                    hovertemplate="%{x|%Y-%m-%d}<br>NDX: %{y:.0f}<extra></extra>"
                ),
                row=2, col=1
            )

        fig.add_trace(
            go.Scatter(
                x=hist.index, y=hist["spx_close"],
                mode="lines",
                line=dict(color="#60a5fa", width=2),
                name="S&P 500",
                hovertemplate="%{x|%Y-%m-%d}<br>SPX: %{y:.0f}<extra></extra>"
            ),
            row=n_rows, col=1
        )

        fig.update_layout(
            height=750 if has_ndx else 600,
            showlegend=False,
            hovermode="x unified",
            template="plotly_dark"
        )
        fig.update_yaxes(title_text="评分", range=[0, 100], row=1, col=1)
        if has_ndx:
            fig.update_yaxes(title_text="NDX", row=2, col=1)
        fig.update_yaxes(title_text="SPX", row=n_rows, col=1)

        st.plotly_chart(fig, use_container_width=True)

        with st.expander("📚 历史关键时点参考"):
            st.markdown("""
            可在侧边栏选择以下日期回看评分:
            - **2007-10-15**: 金融危机前期顶部
            - **2018-09-30**: Q4 大幅回调前
            - **2020-02-14**: COVID 暴跌前
            - **2022-01-03**: 加息周期顶部
            - **2024-12-15**: 普通时期对照

            评分系统的预警能力主要体现在 **拥挤+脆弱共振** 的时期,
            对外生冲击 (政策突变、地缘事件) 几乎无预警能力.
            """)
    else:
        st.warning("历史数据不足,无法生成曲线")

    # 底部
    st.divider()
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.caption(
            "⚠️ **免责声明**: 本工具仅供个人研究和教育用途, 不构成任何投资建议. "
            "过往表现不代表未来收益. 任何投资决策由用户自行承担责任. "
            "数据来源: FRED, Yahoo Finance."
        )
    with col_b:
        st.caption("📦 [GitHub](https://github.com/mikeli008008/peaktop) | MIT License")


if __name__ == "__main__":
    main()
