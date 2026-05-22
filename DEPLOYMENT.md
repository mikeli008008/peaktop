# 🚀 部署指南 — 从零到 Live Demo

> 假设你完全没有本地 Python 环境,只有浏览器和 GitHub 账户

## 总体流程 (约 10-15 分钟)

```
[1. 创建 GitHub Repo] (2分钟)
    ↓
[2. 上传文件] (3分钟)
    ↓
[3. 申请 FRED API Key] (1分钟)
    ↓
[4. 部署到 Streamlit Cloud] (5分钟)
    ↓
[5. 获得 Live Demo URL] ✅
```

---

## Step 1: 创建 GitHub Repo

1. 登录 https://github.com
2. 右上角 **+** → **New repository**
3. 填写:
   - **Repository name**: `peak-detector`
   - **Description**: `美股顶部风险预警系统 | US Equity Peak Risk Warning System`
   - 选 **Public**
   - **不要**勾选任何 Initialize options (我们已经准备好所有文件)
4. 点击 **Create repository**

## Step 2: 上传文件 (网页拖拽即可)

GitHub 创建完 repo 后,会显示一个空 repo 页面.

1. 点击 **"uploading an existing file"** 链接 (在 "Quick setup" 区域)
2. 把下面这些文件**全部拖进**网页上传区:

```
peak_detector.py
config.yaml
methodology.md
test_offline.py
requirements.txt
README.md
LICENSE
DISCLAIMER.md
.gitignore
DEPLOYMENT.md
```

3. ⚠️ 文件夹 `.streamlit/` 需要单独处理:
   - 先上传完上面的文件并 commit
   - 然后在 repo 页面点击 **"Add file" → "Create new file"**
   - 文件名输入: `.streamlit/config.toml` (注意斜杠会自动创建文件夹)
   - 把 `.streamlit/config.toml` 的内容复制粘贴进去
   - 同样方法创建 `.streamlit/secrets.toml.example`

4. 滚动到下方,**Commit changes** → **Commit directly to main**

## Step 3: 申请 FRED API Key

如果你已经有了,跳过.

1. 访问 https://fredaccount.stlouisfed.org/apikeys
2. 注册账户 (邮箱+密码,免费)
3. 点击 **Request API Key**
4. 简单填写用途 (写 "Personal research" 即可)
5. 立即获得一串 32 位字符的 API Key
6. **复制保存,不要分享给任何人**

## Step 4: 部署到 Streamlit Cloud

1. 访问 https://share.streamlit.io
2. 用 GitHub 账户登录 (一键授权)
3. 点击 **"Create app"** → **"Deploy a public app from GitHub"**
4. 填写:
   - **Repository**: 选你的 `your-username/peak-detector`
   - **Branch**: `main`
   - **Main file path**: `peak_detector.py`
   - **App URL**: 可以自定义 (比如 `peak-detector-han`)

5. 点击 **"Advanced settings"** → **"Secrets"**
6. 粘贴你的 FRED key (注意格式!):
   ```toml
   FRED_API_KEY = "你的key粘贴到这里"
   ```
   注意要带引号

7. **Save** → **Deploy!**

部署需要 2-5 分钟 (装依赖比较慢).第一次成功后,以后每次 git push 自动更新.

## Step 5: 完成!

你会得到一个 URL 类似:
```
https://peak-detector-han.streamlit.app
```

这就是你的 Live Demo,可以:
- 任何设备打开 (手机/平板/电脑)
- 分享给任何人 (朋友、客户、X/Twitter)
- 收藏到浏览器主屏幕

---

## 常见问题 / Troubleshooting

### Q: 部署失败,提示找不到 `^VIX9D` 数据?

A: 偶发的 yfinance 问题,等几分钟重新部署一次.或在 Streamlit Cloud 的 app 页面
点击右上角菜单 → "Reboot app".

### Q: FRED 数据拉不到?

A: 检查 Secrets 是否正确填写,key 必须用双引号包起来:
```toml
FRED_API_KEY = "abc123..."     # ✅ 正确
FRED_API_KEY = abc123...        # ❌ 错误,没引号
```

### Q: 我想改阈值/权重,怎么改?

A: 在 GitHub 网页上直接编辑 `config.yaml`,commit 后 Streamlit Cloud 自动重新部署.

### Q: 我想让别人也能用但不想让他们看到我的代码?

A: 不行.Streamlit Cloud 免费版要求 repo 必须是 public.如果一定要 private 代码,
需要付费版本 (每月$$) 或自己用 Render/Fly.io 等其他平台部署.

### Q: API key 会不会泄露?

A: Streamlit Cloud 的 Secrets 是加密存储,**不会**出现在你的 repo 里,
**不会**暴露给访问 Live Demo 的用户.访问者用的是你的 key 配额,但 FRED 免费
账户每天有 100,000 次请求额度,完全够用.

### Q: 别人用我的 Live Demo 会消耗我的 FRED 配额吗?

A: 是的,但因为 `@st.cache_data(ttl=14400)` 设置了 4 小时缓存,实际请求很少.
即使每天 1000 个独立访客也只占配额的 < 1%.

### Q: 想隐藏 Live Demo URL?

A: Streamlit Cloud 的免费 app URL 是可猜测的 (基于 username 和 repo).如果想
完全 private,需要付费版或自部署.

---

## 推广 / Promotion (可选)

如果你想让更多人用:

1. **README 加 Live Demo URL**: 在 README.md 顶部加一个 badge
   ```markdown
   [![Live Demo](https://img.shields.io/badge/Live-Demo-orange?logo=streamlit)](https://peak-detector-han.streamlit.app)
   ```

2. **发布社交媒体**:
   - X/Twitter: 截图 + 项目特色 + GitHub 链接
   - 知乎: 写一篇深度方法论介绍
   - 雪球: 实测某个历史日期的评分案例
   - LinkedIn: 强调技术深度,链接到 GitHub

3. **提交到聚合站**:
   - [Awesome Quant](https://github.com/wilsonfreitas/awesome-quant) PR
   - [Awesome Trading](https://github.com/edeNFed/awesome-trading) PR
   - Hacker News "Show HN"
   - r/algotrading, r/quant, r/wallstreetbets

---

## 维护 / Maintenance

最少维护:
- 每月看一次 Streamlit Cloud 是否运行正常
- yfinance 偶尔会 break (升级它的版本一般能解决)
- FRED API key 不会过期,无需更新

进阶维护:
- 每季度回看历史评分,看是否需要调整阈值
- 关注社区 Issues 和 PR
- 根据新的市场结构变化加新指标

---

**祝部署顺利! 有任何问题随时回到对话问我.**
