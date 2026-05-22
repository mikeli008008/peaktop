"""
核心计算逻辑离线测试 - 用合成数据验证 8 个指标函数能正常运行
不需要网络,不需要 FRED API key
"""
import sys
import numpy as np
import pandas as pd
import yaml

# 把 streamlit cache 装饰器 mock 掉
class FakeStreamlit:
    def cache_data(self, *args, **kwargs):
        def decorator(func):
            return func
        if args and callable(args[0]):
            return args[0]
        return decorator
    def warning(self, *a, **k): pass
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def success(self, *a, **k): pass

sys.modules['streamlit'] = FakeStreamlit()

# 现在 import 主程序
from peak_detector import (
    calc_cta_positioning, calc_vix_skew, calc_gamma_env,
    calc_ad_divergence, calc_concentration, calc_putcall_proxy,
    calc_hy_spread, calc_fed_liquidity, check_overrides,
    threshold_to_score, get_risk_level
)

# 生成 5 年合成日数据
np.random.seed(42)
dates = pd.date_range("2020-01-01", "2025-05-21", freq="B")
n = len(dates)

# 模拟有趋势的股价 (年化 8% 收益, 18% 波动)
returns = np.random.randn(n) * 0.18 / np.sqrt(252) + 0.08 / 252
spx_close = 3000 * np.exp(np.cumsum(returns))

spx = pd.DataFrame({
    "Open": spx_close * 0.999,
    "High": spx_close * 1.005,
    "Low": spx_close * 0.995,
    "Close": spx_close,
    "Volume": np.random.randint(1e9, 5e9, n)
}, index=dates)

ndx = spx * 1.5  # NDX 大约是 SPX 的1.5倍
spy = spx / 10  # SPY 大约是 SPX 的1/10
rsp = spy * 1.4  # 假设 RSP 价格

# VIX: 反向相关于 SPX 短期表现
vix_close = 18 + 8 * np.random.randn(n).cumsum() / np.sqrt(n) * -np.sign(returns.cumsum())
vix_close = np.clip(vix_close, 10, 50)
vix = pd.DataFrame({"Close": vix_close}, index=dates)
vix9d = pd.DataFrame({"Close": vix_close * 0.92}, index=dates)  # 通常处于 contango
skew = pd.DataFrame({"Close": 130 + 10 * np.random.randn(n).cumsum() / np.sqrt(n)}, index=dates)

# FRED 数据
fred_dates = pd.date_range("2020-01-01", "2025-05-21", freq="W")
hy = pd.Series(3.5 + np.cumsum(np.random.randn(len(fred_dates))) * 0.05, 
               index=fred_dates).clip(2, 12)
walcl = pd.Series(8_000_000 + np.cumsum(np.random.randn(len(fred_dates))) * 50_000,
                  index=fred_dates)
wtregen = pd.Series(500_000 + np.cumsum(np.random.randn(len(fred_dates))) * 20_000,
                    index=fred_dates).clip(100_000, 1_500_000)
rrp = pd.Series(1500 + np.cumsum(np.random.randn(len(fred_dates))) * 30,
                index=fred_dates).clip(0, 3000)

# 加载配置
with open("config.yaml") as f:
    config = yaml.safe_load(f)

print("=" * 70)
print("Peak Detector 离线测试 - 使用合成数据")
print("=" * 70)
print(f"模拟日数据: {dates[0].date()} 到 {dates[-1].date()} ({n} 个交易日)")
print()

# 测试每个指标
print("=== 各指标输出 ===")
tests = [
    ("1. CTA Positioning", calc_cta_positioning(spx, ndx, config)),
    ("2. VIX/SKEW",        calc_vix_skew(vix, skew, config)),
    ("3. Gamma Env",       calc_gamma_env(spx, vix, config)),
    ("4. A/D Divergence",  calc_ad_divergence(spy, rsp, config)),
    ("5. Concentration",   calc_concentration(spy, rsp, config)),
    ("6. Put/Call Proxy",  calc_putcall_proxy(vix9d, vix, config)),
    ("7. HY Spread",       calc_hy_spread(hy, config)),
    ("8. Fed Liquidity",   calc_fed_liquidity(walcl, wtregen, rrp, config)),
]

scores = []
weights_list = list(config["weights"].values())
for name, result in tests:
    score, raw, detail = result
    scores.append(score)
    print(f"  {name:25s} 分数: {score:>2d}/10  | {detail}")

# 计算总分
total = sum(s * w for s, w in zip(scores, weights_list)) * 10
risk = get_risk_level(total, config["risk_levels"])

print()
print("=== 总分 ===")
print(f"加权总分: {total:.1f}/100")
print(f"风险等级: {risk['level']}")
print(f"建议:    {risk['advice']}")

# Override 检查
triggers = check_overrides(vix, spx, hy, config)
print()
print("=== Override 检查 ===")
if triggers:
    for t in triggers:
        print(f"  {t}")
else:
    print("  ✅ 无触发")

print()
print("=" * 70)
print("✅ 所有 8 个指标函数运行正常,无异常")
print("=" * 70)
