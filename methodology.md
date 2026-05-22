# Peak Detector — 方法论文档

> 本文档定义 8 个核心风险指标的来源、计算方法、阈值和权重。
> 仅供个人研究和教育用途，不构成投资建议。

## 设计哲学

顶部不是基本面恶化形成的，而是 **边际买盘耗尽 + 仓位拥挤 + 对冲缺失 + 流动性脆弱** 的共振。
本系统识别 **高风险窗口**（不是精确择时），用于调节投资组合的风险敞口。

## 评分框架

每个指标输出 0-10 分（10 = 最危险/最极端）。
加权汇总为 0-100 总分。

| 总分 | 风险等级 | 建议 |
|------|---------|------|
| 0-30 | 🟢 绿灯 | 正常持仓，可加杠杆 |
| 31-50 | 🟡 黄灯 | 标准仓位，停止加仓 |
| 51-65 | 🟠 橙灯 | 减仓 20-30%，停止追高 |
| 66-80 | 🔴 红灯 | 减仓 50%+，买保护性 put |
| 81-100 | ⚫ 深红 | 防御仓位，转入现金/短债 |

## 五大维度 + 8 指标

---

### 维度一：仓位拥挤度（权重 20%）

#### 1. CTA Positioning Proxy (权重 20%)
- **逻辑**: 模拟代表性 CTA 趋势跟随基金的仓位
- **数据**: yfinance — `^GSPC` (S&P 500) 和 `^NDX` (NASDAQ-100)
- **计算**:
  ```
  对每个指数计算:
    score_1 = (price - SMA200) / SMA200    # 距离长均线的偏离
    score_2 = (price - SMA50) / SMA50      # 距离中均线的偏离
    score_3 = (price - 20日最低) / (20日最高 - 20日最低)  # Donchian位置
  CTA_long_intensity = 平均(score_1, score_2, score_3)
  对过去 252 个交易日做 z-score 标准化
  ```
- **阈值映射** (z-score → 0-10 分):
  | z-score | 分数 |
  |---------|------|
  | < 0 | 0 |
  | 0 ~ 0.5 | 2 |
  | 0.5 ~ 1.0 | 4 |
  | 1.0 ~ 1.5 | 6 |
  | 1.5 ~ 2.0 | 8 |
  | > 2.0 | 10 |
- **解读**: z-score 高 = CTA 已重仓多头，新增买盘耗尽，下行非对称风险大

---

### 维度二：市场结构脆弱性（权重 25%）

#### 2. VIX / SKEW Complacency (权重 15%)
- **逻辑**: 低 VIX + 低 SKEW = 市场极度自满，无人对冲
- **数据**: yfinance — `^VIX` 和 `^SKEW`
- **计算**:
  ```
  vix_score: VIX 在过去 252 日的百分位（越低越危险，倒数）
  skew_score: SKEW 绝对水平
    SKEW < 130 → 高分（complacency）
    SKEW > 145 → 低分（已经对冲）
  combined = 0.5 * vix_complacency + 0.5 * skew_complacency
  ```
- **阈值映射**:
  | VIX 百分位 | VIX 分数 |
  |-----------|---------|
  | > 50% | 0 |
  | 30-50% | 3 |
  | 15-30% | 6 |
  | 5-15% | 8 |
  | < 5% | 10 |

  | SKEW 水平 | SKEW 分数 |
  |----------|----------|
  | > 150 | 0 |
  | 140-150 | 3 |
  | 130-140 | 6 |
  | 120-130 | 8 |
  | < 120 | 10 |

#### 3. Gamma Environment Proxy (权重 10%)
- **逻辑**: 用 realized vol 远低于 implied vol 代理 dealer long gamma 环境
  - 但持续过低 = 即将转换，是预警信号
- **数据**: yfinance — `^GSPC` (计算 30 日 realized vol) 和 `^VIX`
- **计算**:
  ```
  rv_30d = SPX 日收益率的 30 日年化波动率
  iv = VIX / 100
  vrp = iv - rv_30d  (variance risk premium)
  vrp_ratio = rv_30d / iv
  ```
- **阈值映射**:
  | vrp_ratio | 分数 | 解读 |
  |-----------|------|------|
  | > 1.0 | 0 | rv > iv，已经在恐慌 |
  | 0.7-1.0 | 2 | 正常 |
  | 0.5-0.7 | 5 | 偏低 |
  | 0.3-0.5 | 8 | 极低 vol，complacency |
  | < 0.3 | 10 | 极端 complacency |

---

### 维度三：流动性与广度（权重 20%）

#### 4. A/D Line Divergence (权重 10%)
- **逻辑**: 价格新高但广度不创新高 = 顶部背离
- **数据**: yfinance — 用 `RSP` 和 `SPY` 价格作为简化代理
  (真正的 NYSE A/D line 需付费数据，用等权 vs 市值权重指数差异近似)
- **计算**:
  ```
  spy_high_60d = SPY 60日新高布尔值
  ad_proxy = RSP / SPY 比率
  ad_falling_60d = ad_proxy 60日趋势斜率为负

  divergence = spy_high_60d AND ad_falling_60d
  divergence_duration = 过去 30 日内 divergence 为真的天数
  ```
- **阈值映射**:
  | divergence_duration | 分数 |
  |---------------------|------|
  | 0 天 | 0 |
  | 1-3 天 | 3 |
  | 4-7 天 | 6 |
  | 8-15 天 | 8 |
  | > 15 天 | 10 |

#### 5. Concentration Risk — RSP/SPY (权重 10%)
- **逻辑**: 涨幅集中在少数大票 = 市场虚胖
- **数据**: yfinance — `RSP` 和 `SPY`
- **计算**:
  ```
  ratio = RSP / SPY
  ratio_60d_change = (ratio_today - ratio_60d_ago) / ratio_60d_ago
  ```
- **阈值映射**:
  | 60日变化 | 分数 |
  |----------|------|
  | > +2% | 0 |
  | 0 ~ +2% | 2 |
  | -2% ~ 0 | 5 |
  | -5% ~ -2% | 8 |
  | < -5% | 10 |

---

### 维度四：估值与情绪（权重 15%）

#### 6. Put/Call Complacency (权重 15%)
- **逻辑**: 散户 put/call 比率持续低 = 看涨情绪极端
- **数据**: yfinance — 用 VIX 短期/长期结构 + SPY 强度组合代理
  (CBOE Put/Call 免费但需额外数据源，这里用 VIX9D/VIX 代理)
- **计算**:
  ```
  vix9d = yfinance ^VIX9D
  vix = yfinance ^VIX
  ts_ratio = vix9d / vix    # < 1 = contango（正常），> 1 = backwardation（恐慌）

  contango_strength = 1 - ts_ratio  (越大越 complacent)
  contango_percentile = 过去 252 日 contango_strength 的百分位
  ```
- **阈值映射**:
  | contango 百分位 | 分数 |
  |----------------|------|
  | < 30% | 0 |
  | 30-50% | 2 |
  | 50-70% | 5 |
  | 70-90% | 8 |
  | > 90% | 10 |

---

### 维度五：宏观与政策（权重 20%）

#### 7. High Yield Credit Spread (权重 10%)
- **逻辑**: 信用利差扩大 = credit 市场先于股市感知风险
- **数据**: FRED — `BAMLH0A0HYM2` (ICE BofA US High Yield Index OAS)
- **计算**:
  ```
  current_spread = 当前 OAS
  spread_change_20d = current - 20日前的spread
  spread_percentile = 当前spread在过去 252 日的百分位
  ```
- **阈值映射** (两个分数取较大):
  | 20日变化 (bps) | 分数 |
  |---------------|------|
  | < 0 | 0 |
  | 0-20 | 3 |
  | 20-50 | 6 |
  | 50-100 | 8 |
  | > 100 | 10 |

  | 当前百分位 | 分数 |
  |-----------|------|
  | < 30% | 0 |
  | 30-50% | 2 |
  | 50-70% | 4 |
  | 70-90% | 6 |
  | > 90% | 8 |

#### 8. Fed Net Liquidity Impulse (权重 10%)
- **逻辑**: Fed 资产 - 财政部 TGA - RRP = 真实净流动性，13 周变化驱动股市
- **数据**: FRED — `WALCL`, `WTREGEN`, `RRPONTSYD`
- **计算**:
  ```
  net_liq = WALCL - WTREGEN - RRPONTSYD  (单位: 百万美元)
  change_13w = net_liq_today - net_liq_13w_ago
  change_pct = change_13w / net_liq_13w_ago
  ```
- **阈值映射**:
  | 13周变化% | 分数 |
  |----------|------|
  | > +3% | 0 |
  | +1% ~ +3% | 2 |
  | -1% ~ +1% | 5 |
  | -3% ~ -1% | 8 |
  | < -3% | 10 |

---

## 总分计算

```
total_score = sum(indicator_score * indicator_weight) * 10

# 权重总和 = 1.0
weights = {
  'cta_positioning': 0.20,
  'vix_skew': 0.15,
  'gamma_env': 0.10,
  'ad_divergence': 0.10,
  'concentration': 0.10,
  'putcall_proxy': 0.15,
  'hy_spread': 0.10,
  'fed_liquidity': 0.10
}
```

## Override 规则（强制升档）

任一发生，风险等级强制升一档：
- VIX 单日上涨 > 30% 且收盘 > 25
- HY spread 20 日扩大 > 100bps
- 标普跌破 200 日均线（首次跌破，过去 60 日内一直在 MA 上方）

## 局限性声明

1. **不是择时工具**：极端值可以持续数月
2. **对外生冲击无效**：COVID、地缘战争、政策突变无法预测
3. **代理指标的损失**：用 yfinance 免费数据代理机构数据，精度损失约 20-30%
4. **历史不代表未来**：阈值基于历史校准，市场结构变化可能失效

## 数据源汇总

| 数据 | 来源 | Ticker/Series |
|------|------|--------------|
| S&P 500 | yfinance | ^GSPC, SPY |
| NASDAQ-100 | yfinance | ^NDX |
| 等权 S&P | yfinance | RSP |
| VIX | yfinance | ^VIX |
| VIX9D | yfinance | ^VIX9D |
| SKEW | yfinance | ^SKEW |
| HY OAS | FRED | BAMLH0A0HYM2 |
| Fed Balance Sheet | FRED | WALCL |
| Treasury General Account | FRED | WTREGEN |
| Reverse Repo | FRED | RRPONTSYD |
