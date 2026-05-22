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
def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    """从 yfinance 拉取数据"""
    try:
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        st.warning(f"yfinance 拉取 {ticker} 失败: {e}")
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
    """
    if spx.empty or ndx.empty:
        return 0, 0.0, "数据缺失"

    if as_of:
        spx = spx[spx.index <= as_of]
        ndx = ndx[ndx.index <= as_of]

    if len(spx) < 200 or len(ndx) < 200:
        return 0, 0.0, "历史数据不足"

    def intensity(df):
        close = df["Close"]
        sma200 = close.rolling(200).mean()
        sma50 = close.rolling(50).mean()
        high60 = close.rolling(60).max()
        low60 = close.rolling(60).min()

        # 距 200MA 偏离 (核心信号)
        deviation_200 = (close - sma200) / sma200
        # 距 50MA 偏离
        deviation_50 = (close - sma50) / sma50
        # Donchian 60日位置 (0-1, 1=接近高点)
        donchian_range = (high60 - low60).replace(0, np.nan)
        donchian_pos = (close - low60) / donchian_range

        return deviation_200, deviation_50, donchian_pos

    spx_d200, spx_d50, spx_donch = intensity(spx)
    ndx_d200, ndx_d50, ndx_donch = intensity(ndx)

    # 取两指数平均
    dev_200 = (float(spx_d200.iloc[-1]) + float(ndx_d200.iloc[-1])) / 2
    dev_50 = (float(spx_d50.iloc[-1]) + float(ndx_d50.iloc[-1])) / 2
    donch = (float(spx_donch.iloc[-1]) + float(ndx_donch.iloc[-1])) / 2

    if np.isnan(dev_200):
        return 0, 0.0, "计算异常"

    # 综合分: 用绝对偏离打分
    # 距 200MA > 15% 且接近 60日高点 = CTA 已极度满仓
    score = threshold_to_score(dev_200, config["cta_positioning"]["thresholds"])

    # 强化: 如果 Donchian 位置 > 0.85 (接近 60日高点), 至少 6 分
    if donch > 0.85 and score < 6:
        score = 6
    # 双重强化: 距 200MA > 12% 且 Donchian > 0.90, 至少 8 分
    if dev_200 > 0.12 and donch > 0.90 and score < 8:
        score = 8

    detail = f"距200MA: {dev_200*100:+.1f}% | 距50MA: {dev_50*100:+.1f}% | Donchian: {donch:.2f}"
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
                    config: dict, as_of: Optional[pd.Timestamp] = None) -> list:
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

    return triggers


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

    score, raw, detail = calc_hy_spread(data["hy"], config, as_of)
    results["hy_spread"] = {"score": score, "raw": raw, "detail": detail, "name": "HY Credit Spread"}

    score, raw, detail = calc_fed_liquidity(data["walcl"], data["wtregen"], data["rrp"], config, as_of)
    results["fed_liquidity"] = {"score": score, "raw": raw, "detail": detail, "name": "Fed Liquidity"}

    weights = config["weights"]
    total = sum(results[k]["score"] * weights[k] for k in weights) * 10

    triggers = check_overrides(data["vix"], data["spx"], data["hy"], config, as_of)

    # as_of 默认用数据实际末尾日期 (规避 Streamlit Cloud 时区/时钟问题)
    if as_of is None:
        if not data["spx"].empty:
            as_of_display = data["spx"].index[-1]
        else:
            as_of_display = pd.Timestamp.now()
    else:
        as_of_display = as_of

    return {
        "indicators": results,
        "total_score": total,
        "triggers": triggers,
        "as_of": as_of_display
    }


# ============================================================
# 数据批量加载
# ============================================================

@st.cache_data(ttl=14400, show_spinner="正在拉取市场数据 (首次加载较慢, 约 30-60 秒)...")
def load_all_data(fred_api_key: str, period: str = "max") -> dict:
    """一次性加载所有需要的数据
    
    period='max' 拉取全部历史 (yfinance 通常给 20+ 年)
    这样历史回看才能覆盖 2007/2018/2020/2022 等关键时点
    """
    data = {
        "spx": fetch_yf("^GSPC", period),
        "ndx": fetch_yf("^NDX", period),
        "spy": fetch_yf("SPY", period),
        "rsp": fetch_yf("RSP", period),
        "vix": fetch_yf("^VIX", period),
        "vix9d": fetch_yf("^VIX9D", period),  # 2011年才有
        "skew": fetch_yf("^SKEW", period),
    }

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

    dates = spx.index[-days::5]

    records = []
    for date in dates:
        try:
            result = compute_all_scores(_data, _config, as_of=date)
            records.append({
                "date": date,
                "total_score": result["total_score"],
                "spx_close": float(spx.loc[spx.index <= date, "Close"].iloc[-1])
            })
        except Exception:
            continue

    return pd.DataFrame(records).set_index("date")


# ============================================================
# Streamlit UI
# ============================================================

def main():
    st.set_page_config(
        page_title="Peak Detector | 美股顶部风险预警",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": "https://github.com/YOUR_USERNAME/peak-detector",
            "Report a Bug": "https://github.com/YOUR_USERNAME/peak-detector/issues",
            "About": "Peak Detector - 美股顶部风险预警系统. 仅供研究和教育用途."
        }
    )

    config = load_config()

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
        - 详见 [methodology.md](https://github.com/YOUR_USERNAME/peak-detector/blob/main/methodology.md)
        """)
        st.divider()
        st.caption("🌟 喜欢的话给个 [GitHub Star](https://github.com/YOUR_USERNAME/peak-detector)")

    # 计算评分
    result = compute_all_scores(data, config, as_of=as_of_date)
    total = result["total_score"]
    risk_level = get_risk_level(total, config["risk_levels"])

    # ============== 顶部:总分大显示 ==============
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        date_str = result["as_of"].strftime("%Y-%m-%d")
        st.markdown(f"### 评分日期: {date_str}")

        st.markdown(
            f"""
            <div style='text-align:center; padding: 20px;
                        background: linear-gradient(135deg, {risk_level["color"]}22, {risk_level["color"]}44);
                        border-radius: 12px; border-left: 6px solid {risk_level["color"]};'>
                <div style='font-size: 18px; color: #888;'>当前风险评分</div>
                <div style='font-size: 72px; font-weight: bold; color: {risk_level["color"]};'>
                    {total:.1f}
                </div>
                <div style='font-size: 24px; margin-top: 8px;'>{risk_level["level"]}</div>
                <div style='font-size: 16px; color: #aaa; margin-top: 8px;'>{risk_level["advice"]}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with col2:
        st.markdown("### 📍 各维度贡献")
        weights = config["weights"]
        for key in weights:
            ind = result["indicators"][key]
            contribution = ind["score"] * weights[key] * 10
            st.markdown(
                f"**{ind['name']}**: "
                f"<span style='color:{risk_level['color']}'>{ind['score']}/10</span> "
                f"(贡献 {contribution:.1f})",
                unsafe_allow_html=True
            )

    with col3:
        st.markdown("### 🚨 Override 触发")
        if result["triggers"]:
            for t in result["triggers"]:
                st.error(t)
            st.warning("**建议强制升一档风险等级**")
        else:
            st.success("✅ 无 Override 触发")

    st.divider()

    # ============== 中部:各指标详情 ==============
    st.subheader("🔍 8 大指标详情")

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

    st.divider()

    # ============== 下部:历史曲线 ==============
    st.subheader("📈 历史风险评分曲线 (过去 ~2 年, 每周采样)")

    with st.spinner("计算历史评分..."):
        hist = compute_historical_scores(data, config, days=500)

    if not hist.empty:
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.6, 0.4],
            subplot_titles=("风险评分", "标普 500")
        )

        fig.add_trace(
            go.Scatter(
                x=hist.index, y=hist["total_score"],
                mode="lines+markers",
                line=dict(color="#f97316", width=2),
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

        fig.add_trace(
            go.Scatter(
                x=hist.index, y=hist["spx_close"],
                mode="lines",
                line=dict(color="#60a5fa", width=2),
                name="S&P 500",
                hovertemplate="%{x|%Y-%m-%d}<br>SPX: %{y:.0f}<extra></extra>"
            ),
            row=2, col=1
        )

        fig.update_layout(
            height=600,
            showlegend=False,
            hovermode="x unified",
            template="plotly_dark"
        )
        fig.update_yaxes(title_text="评分", range=[0, 100], row=1, col=1)
        fig.update_yaxes(title_text="SPX", row=2, col=1)

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
        st.caption("📦 [GitHub](https://github.com/YOUR_USERNAME/peak-detector) | MIT License")


if __name__ == "__main__":
    main()
