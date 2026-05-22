# 📊 Peak Detector

> 美股顶部风险预警系统 | 8 个核心指标 → 0-100 风险评分
> 
> US Equity Peak Risk Warning System | 8 Core Indicators → 0-100 Risk Score

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)

> ⚠️ **不构成投资建议** | Not Investment Advice — See [DISCLAIMER](DISCLAIMER.md)

---

## 🎯 这是什么 / What is this?

**中文**: 一个开源的美股顶部风险评分系统.聚合 8 个机构级风险指标到单一 0-100 评分,
帮助投资者识别**高风险窗口** (而非精确择时).基于公开数据,完全免费.

**English**: An open-source US equity peak risk scoring system. Aggregates 8
institutional-grade risk indicators into a single 0-100 score, helping investors
identify **high-risk windows** (not precise timing). Free, uses only public data.

## ✨ 核心特性 / Features

- 📈 **8 个核心指标** 覆盖仓位拥挤度、市场结构、流动性广度、估值情绪、宏观流动性
- 🎯 **0-100 风险评分** + 5 级风险等级 (绿/黄/橙/红/深红)
- 🚨 **Override 触发规则** (VIX 飙升、HY 利差爆炸、跌破 200MA)
- 📅 **历史回看** 可查询任意历史日期的评分
- 📊 **可视化仪表盘** 总分、各指标贡献、历史曲线、SPX 叠加
- 🔧 **可配置阈值** 所有参数在 `config.yaml`,无需改代码
- 💰 **完全免费** 只用 FRED + yfinance 数据源

## 📸 截图 / Screenshot

> Live demo: [部署后填入 URL]

```
┌─────────────────────────────────────────────────────────┐
│  📊 Peak Detector | 评分日期: 2025-05-22                  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│           当前风险评分                                    │
│           ┌────────────┐                                │
│           │    67.5    │  🔴 红灯                        │
│           └────────────┘  减仓 50%+,买保护性 put         │
│                                                         │
│   各指标:                                                │
│   CTA Positioning: 8/10  VIX/SKEW: 7/10                 │
│   Gamma Env: 5/10        A/D Diverg: 6/10               │
│   Concentration: 7/10    Put/Call: 8/10                 │
│   HY Spread: 4/10        Fed Liquidity: 6/10            │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## 🚀 快速开始 / Quick Start

### Option A: 使用 Live Demo (推荐)

直接访问: [Streamlit Cloud Demo](https://YOUR-DEPLOY-URL.streamlit.app)
(无需安装,浏览器直接用)

### Option B: 本地运行 / Run Locally

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/peak-detector.git
cd peak-detector

# 2. 装依赖 / Install dependencies
pip install -r requirements.txt

# 3. 申请 FRED API Key (30秒免费)
#    https://fredaccount.stlouisfed.org/apikeys
export FRED_API_KEY="your_key_here"

# 4. 启动 / Run
streamlit run peak_detector.py
```

打开 http://localhost:8501

### Option C: 一键部署到 Streamlit Cloud

1. Fork 这个 repo
2. 访问 https://share.streamlit.io
3. 点击 "New app" 选择你 fork 的 repo
4. 在 Secrets 中添加: `FRED_API_KEY = "你的key"`
5. Deploy! 几分钟后获得公网 URL

## 🧩 8 大指标 / Indicators

| # | 指标 / Indicator | 数据源 / Source | 权重 / Weight |
|---|------|------|------|
| 1 | **CTA Positioning Proxy** | yfinance (SPX, NDX) | 20% |
| 2 | **VIX/SKEW Complacency** | yfinance (^VIX, ^SKEW) | 15% |
| 3 | **Gamma Environment** | yfinance (RV vs IV) | 10% |
| 4 | **A/D Line Divergence** | yfinance (RSP/SPY) | 10% |
| 5 | **Concentration Risk** | yfinance (RSP/SPY) | 10% |
| 6 | **Put/Call Complacency** | yfinance (^VIX9D/^VIX) | 15% |
| 7 | **HY Credit Spread** | FRED (BAMLH0A0HYM2) | 10% |
| 8 | **Fed Net Liquidity** | FRED (WALCL-WTREGEN-RRP) | 10% |

详细方法论见 [methodology.md](methodology.md)

## 📊 风险等级 / Risk Levels

| 总分 / Score | 等级 / Level | 建议 / Action |
|------|------|------|
| 0-30 | 🟢 绿灯 / Green | 正常持仓 / Normal allocation |
| 31-50 | 🟡 黄灯 / Yellow | 停止加仓 / No new buying |
| 51-65 | 🟠 橙灯 / Orange | 减仓 20-30% / Reduce 20-30% |
| 66-80 | 🔴 红灯 / Red | 减仓 50%+ 买保护 / Reduce 50%+, hedge |
| 81-100 | ⚫ 深红 / Deep Red | 防御仓位 / Defensive only |

## ⚙️ 配置 / Configuration

所有阈值和权重在 `config.yaml`,修改后刷新页面即可生效:

```yaml
weights:
  cta_positioning: 0.20   # 改权重 (总和保持 = 1.0)
  vix_skew: 0.15
  # ...

cta_positioning:
  thresholds:
    - { max: 1.5, score: 6 }   # 改阈值
    # ...
```

## 🧪 测试 / Testing

```bash
# 离线测试 (用合成数据,不需要网络)
python test_offline.py
```

## ⚠️ 局限性 / Limitations

1. **不是择时工具** — 极端值可以持续数月. Not a timing tool — extremes can persist for months.
2. **对外生冲击无效** — 政策突变、地缘事件无预警. No warning for exogenous shocks (policy, geopolitics).
3. **代理指标精度损失** — vs 机构付费数据约 20-30%. Proxy indicators lose ~20-30% accuracy vs institutional data.
4. **历史不代表未来** — 阈值基于历史校准. Past doesn't predict future.

## 🛠️ 贡献 / Contributing

欢迎 PR 改进指标、调整阈值、增加可视化.建议改进方向:

- 加入更多指标 (AAII bull-bear、CFTC COT 数据)
- 替换 FRED 数据缓存机制
- 增加 email/telegram 告警功能
- 多市场支持 (港股、A 股、欧股)

## 📜 License

[MIT License](LICENSE) — 自由使用、修改、分发

## ⚠️ 重要免责声明 / IMPORTANT DISCLAIMER

**本软件不构成任何投资建议.过往表现不代表未来收益.使用本软件的任何决策由用户
自行承担全部责任.作者和贡献者不对使用本软件产生的任何损失负责.**

**This software does NOT constitute investment advice. Past performance does not
guarantee future results. All decisions made using this software are the sole
responsibility of the user. The authors and contributors are not liable for any
losses arising from the use of this software.**

详见 [DISCLAIMER.md](DISCLAIMER.md)

---

## 🌟 鸣谢 / Acknowledgments

- 数据源 / Data: [FRED](https://fred.stlouisfed.org/), [Yahoo Finance](https://finance.yahoo.com/)
- 灵感 / Inspired by: Goldman Sachs Marquee, Nomura McElligott notes, SpotGamma, Tier1Alpha
- 框架 / Built with: [Streamlit](https://streamlit.io/), [Plotly](https://plotly.com/)

---

**如果这个项目对你有用,请给个 ⭐ Star! / If this project helps you, please give a ⭐ Star!**
