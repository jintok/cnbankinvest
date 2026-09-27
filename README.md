# 中国银行板块投研周报系统 (cnbankinvest)

个人研究用途：对 **12 家上市银行（A股 + 6 家 H股）+ 4 个基准指数** 的每周跟踪，
一键拉数 → 自动生成周报 Markdown → 人工补观点与台账。

> 数据全部来自 akshare 聚合的公开接口（新浪/中证/申万/中债/东财/同花顺等），
> 接口逐一实测记录见 [`INTERFACE_NOTES.md`](INTERFACE_NOTES.md)。不构成投资建议。

## 覆盖标的池（watchlist.json）

| 板块 | 标的 |
|---|---|
| 国有大行（6） | 工商 601398/01398 · 建设 601939/00939 · 农业 601288/01288 · 中国银行 601988/03988 · 交通 601328/03328 · 邮储 601658/01658 |
| 股份行（3） | 招商 600036/03968 · 兴业 601166 · 平安 000001 |
| 城商行（3） | 宁波 002142 · 江苏 600919 · 成都 601838 |
| 指数（5） | 沪深300 000300（基准）· 中证银行 399986 · 申万银行 801780 · 中证红利 000922（红利风格）· **中证银行AH优选 931039**（A/H 因子，每月切换至 A/H 便宜侧） |

## 环境搭建

```bash
uv sync                # 按 uv.lock 创建 .venv 并安装依赖（akshare + pandas）
uv pip install -e .    # 以可编辑方式安装 src/cnbankinvest 包（python -m 调用需要）
```

## 每周工作流

```bash
scripts/refresh_weekly.sh          # 周五收盘后运行；或 --date 2026-09-25 复现指定周五
```

脚本依次执行（单步失败不中断，后续步骤用已有缓存）：

1. `data_puller` → `data/market_YYYY-MM-DD.json`（行情/资金/利率快照）
2. `news_puller` → `data/news_YYYY-MM-DD.json`（银行快讯 + watchlist 公告）
3. `fin_report_analysis` → `data/fundamentals_YYYY-MM-DD.json`（基本面自动部分 + 人工台账合并）
4. `gen_weekly_report` → `output/weekly/bank_weekly_YYYY-MM-DD.md`（周报）

然后：打开 `output/weekly/` 下最新报告，按文末「人工待办清单」完成
**核心观点 / 观点初稿修订 / 下周关注补充**，并填报台账（见下）。

**周报结构（正文 + 附录）**：正文（一~九节）是 3 分钟决策摘要——核心观点、一周速览
（指数表+资金/利率/AH/利差一行统计）、本周变化（涨跌前3后3等 WoW 亮点）、观点初稿
（短/长期各 ≤4 条）、A/H 因子、事件头条、基本面摘要统计、下周关注、风险提示；
个股明细一律在文末「附录（附1 行情估值 / 附2 基本面台账 / 附3 公告快讯 / 附4 监管指标与待办）」。
个股深度分析见 `output/single/` 单只个股报告。


## GitHub Pages 站点

`scripts/refresh_weekly.sh` 第 5 步自动读取 `output/weekly/`、`output/single/`，生成静态站点到 `docs/`（暗色主题、零外部依赖、可离线打开）：
首页 `docs/index.html`、方法论页、周报与个股报告的 HTML 版。发布方式：GitHub 仓库
Settings → Pages → Source 选 `/docs`（分支 master），站点地址为
`https://<user>.github.io/cnbankinvest/`。本地预览直接浏览器打开 `docs/index.html` 即可。

## 数据口径与来源

| 字段 | 来源接口 | 口径 | 单位 |
|---|---|---|---|
| A股收盘价/周涨跌 | `stock_zh_a_daily`（新浪） | qfq 前复权，取 ≤截止日最后交易日 | 元 / % |
| H股收盘价/周涨跌 | `stock_hk_daily`（新浪） | qfq，全历史自截 | 港元 / % |
| AH 溢价 | 上两源 + `currency_boc_sina` | (H×CNY/HKD − A)/A − 1，负值=H折价 | % |
| 汇率 CNY/HKD | `currency_boc_sina(港币)` | 中行折算价 ÷ 100 | — |
| 指数收盘 | `stock_zh_index_hist_csindex`（中证官网，000300/399986/000922/931039）、`index_hist_sw`（申万，801780）；回退链见 data_puller.py `INDEX_SOURCES` | 不复权收盘；931039=中证银行AH价格优选，每月切换至 A+H 样本中 A/H 便宜一侧 | 点 |
| 10Y 国债 | `bond_china_yield` | 中债国债收益率曲线「10年」列，T 日值 vs T-7 日值 | % |
| Shibor 1W | `rate_interbank` | 上海同业拆借市场 Shibor 1周最新值 | % |
| 南向资金 | `stock_hsgt_hist_em(南向资金)` | 近 5 个交易日「当日成交净买额」合计 | 亿港元 |
| PB / PE-TTM / 总市值 | `stock_value_em`（东财 datacenter-web） | ≤截止日最新日频 | — / 倍 / 亿元 |
| 个股股息率 TTM | `stock_history_dividend_detail` 自算 | 近 365 天除息现金分红（元/10股÷10）÷ 现价 | % |
| 快讯 | `stock_info_global_em` + `stock_info_global_cls` | 关键词正则过滤，每源 ≤15 条 | — |
| 公告 | `stock_notice_report(全部)` | 逐工作日全市场公告按 watchlist 过滤、标题归类 | — |
| 营收/净利/ROE | `stock_financial_abstract_ths`（同花顺） | 按报告期；ROE 为**单季度**口径 | 亿元 / % |
| 社融/信贷 | `fin_report_analysis.py` 运行时探针（首选 `macro_china_shrzgm`/`macro_rmb_loan`） | 月度；近期社融增量常返回 null（源数据滞后，以 null 如实呈现） | 亿元 |
| 板块超额/利差分位 | 生成器计算 | 中证银行−沪深300 周涨跌差；利差=12家股息率中位数−10Y | pct |
| A/H 因子 | 生成器计算（931039 vs 399986 + watchlist 7 家 A+H 银行） | 优选指数较中证银行周超额：跑赢=A/H 便宜侧（多为 H 折价侧）走强，跑输=A 股侧走强；另列 7 家 AH 溢价率均值/中位数及环比（上周同口径，需 fx 与 hist 缓存，缺失时写「环比待补」） | pct |

**关于 A/H 因子（周报第六节）**：中证银行AH价格优选指数（931039）在中证银行（399986）
样本内每月比较 A/H 价格、持有便宜一侧——其相对 399986 的超额直接反映「折价侧走强还是
A 股侧走强」。周报据此生成解读：优选跑赢 → H 折价侧走强（港股/便宜侧占优）；跑输 →
A 股侧走强（溢价收敛）。溢价汇总与南向资金周净流向并列展示，供人工判断 AH 轮动的
持续性（南向持续净流入通常收敛 H 股折价、推升溢价）。

## 手工维护台账（必填）

自动流程**拿不到**的银行专项指标，靠两个手工 JSON（报告会以「待填」标出缺口）：

- `data/regulatory_indicators.json` — **行业监管指标**：来源为国家金融监督管理总局
  每季度《商业银行主要监管指标》通报（金监总局官网「统计数据」栏目）。发布后在
  `items` 头部追加最新一期，填 `nim_pct`（净息差）/ `npl_ratio_pct`（不良率）/
  `provision_coverage_pct`（拨备覆盖率）/ `car_pct`（资本充足率）/ `profit_yoy_pct`。
- `data/bank_fundamentals.json` — **个股 curated 字段**：来源为各银行定期报告/业绩说明会，
  填 `report`（最新披露期）/`nim_pct`/`npl_ratio_pct`/`provision_coverage_pct`/
  `cet1_pct`（核心一级）/`payout_ratio_pct`（分红率）。

`fin_report_analysis` 每次运行会把这两个文件合并进 `fundamentals_*.json` 的
`curated` / `industry_regulatory` 字段，并在 `meta.curated_gaps` 列出仍缺的项目；
`gen_weekly_report` 将其渲染为报告第七节的「本周待办」清单。

**入库策略**：`output/`（周报/个股报告/人工新闻笔记/观点博客）与 `docs/`（站点）入库；
`data/` 下原始快照（`market_*`/`news_*`/`fundamentals_*`/`hist/`）可按需重拉、已 gitignore——
只有手工台账与 `spread_history.json`（汇总序列）入库。

## 已知限制

- **东财行情接口（push2/push2his）在本机网络（代理 REDACTED-PROXY）下不可达**：
  东财行情类一律不用，`stock_value_em`/`stock_notice_report`（datacenter-web 域名）可用但带重试。
- **快讯仅覆盖最近 24–48 小时**：`stock_info_global_em/_cls` 只返回最新 200/20 条，
  周报「一周要闻」主要靠公告补齐；财联社接口无 URL 字段。
- **指数股息率不可得**：`stock_a_gxl_lg` 仅支持上证A股/深证A股/创业板/科创板，
  399986/000922/000300 均不支持，报告中指数股息率固定为 null。
- **同花顺财务摘要为累计值口径**：Q2/Q3 营收净利为年初至今累计，ROE 为单季度，
  yoy 由接口直接给出；跨期比较时注意口径（INTERFACE_NOTES.md L 节）。
- **社融增量近期常为 null**：源接口（`macro_china_shrzgm` 候选链）数据滞后，
  报告中以「—」如实呈现，不以前值填充。
- **无净息差/不良率自动源**：akshare 无商业银行监管指标接口，只能手工维护（见上）。
- **南向资金周合计为近 5 个交易日口径**（非自然周），遇长假会跨周。
- **股息率 TTM 为自算口径**：按近 365 天除息记录，与行情软件的「股息率(TTM)」可能略有差异。

## 目录结构

```
cnbankinvest/               # 本仓库即原 finance 仓库 bank/ 模块的独立迁移版
├── watchlist.json            # 12 银行 + 指数（本模块唯一标的源）
├── pyproject.toml            # 包定义（src/cnbankinvest，依赖 akshare + pandas）
├── uv.lock                   # 锁定依赖版本
├── scripts/
│   └── refresh_weekly.sh     # 一键刷新入口（python -m 调用包内模块）
├── src/cnbankinvest/         # 全部代码（可编辑安装）
│   ├── paths.py              # 仓库级路径约定（data/ output/ docs/ 唯一来源）
│   ├── data_puller.py        # 行情快照 → data/market_*.json
│   ├── news_puller.py        # 新闻公告 → data/news_*.json
│   ├── fin_report_analysis.py# 基本面分析 → data/fundamentals_*.json
│   ├── gen_weekly_report.py  # 周报生成 → output/weekly/*.md
│   ├── gen_single_report.py  # 个股报告 → output/single/*.md
│   ├── gen_site.py           # 静态站点 → docs/
│   ├── templates/            # 周报/个股报告模板（随包内走）
│   └── probes/               # akshare 接口探针（历史，含 probe_results.json）
├── data/                     # 大部分 gitignore（可按需重拉）
│   ├── regulatory_indicators.json  # 手工：行业监管指标（入库）
│   ├── bank_fundamentals.json      # 手工：个股专项指标（入库）
│   ├── spread_history.json         # 核心池股息率-10Y利差序列（入库）
│   ├── market_*.json / news_*.json / fundamentals_*.json   # 周快照（gitignore）
│   └── hist/                 # 日线收盘缓存（增量合并，gitignore）
├── output/                   # 有留存价值的产出（全部入库）
│   ├── weekly/               # 生成的周报 Markdown
│   ├── single/               # 生成的个股报告 Markdown
│   ├── news/                 # 人工整理的新闻/事件笔记
│   └── blog/                 # 人工观点文章
├── docs/                     # 生成的静态站点（GitHub Pages Source=/docs）
├── INTERFACE_NOTES.md        # akshare 接口实测笔记（数据源选型依据）
├── README.md / AGENTS.md / LICENSE (MIT)
```


---

## 单只个股报告

针对单家银行的建议/研究报告，与周报共用同一份数据快照（market/news/fundamentals JSON），生成后人工修订观点部分。

```sh
.venv/bin/python -m cnbankinvest.gen_single_report --code 600036 [--date 2026-09-25]
# 输出: output/single/招商银行_银行个股报告_20260925.md
```

- 八节结构：投资结论与建议（短期 1–4 周 / 长期 6–24 月两个子块，自动初稿+待人工修订）→ 公司概况（人工）→ 近期行情与市场表现 → 估值分析（vs 全池及同板块中位数：PB/PE-TTM/股息率/股息率−10Y利差）→ 基本面分析（报告期趋势表 + 手工台账行）→ 近期公告与舆情（按本行过滤）→ 催化剂与风险提示（人工）→ 跟踪清单（本行台账缺口 + 数据日历）。
- 行情/估值/基本面全部自动填充；`--code` 不在 watchlist 内时报错并列出 12 家可用代码。
- 示例：`output/single/招商银行_银行个股报告_20260925.md`。
