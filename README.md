# 中国银行板块投研周报系统 (cnbankinvest)

个人研究用途：对 **12 家上市银行（A股 + 6 家 H股）+ 6 个基准指数** 的每周跟踪，
一键拉数 → 全自动生成「指标 + 图表」周报/个股报告 Markdown（v20261003 起无人工撰写段落）。

> 数据全部来自 akshare 聚合的公开接口（新浪/中证/申万/中债/东财/同花顺等），
> 接口逐一实测记录见 [`INTERFACE_NOTES.md`](INTERFACE_NOTES.md)。不构成投资建议。
>
> 投研方法论按版本化管理：[`methodology/`](methodology/) 下 `v{yyyyMMdd}.md` 为不可变 spec，
> 当前版本为目录中日期最大者；改动报告口径须先发新版方法论（见 `AGENTS.md`「方法论版本管理」）。

## 覆盖标的池（watchlist.json）

| 板块 | 标的 |
|---|---|
| 国有大行（6） | 工商 601398/01398 · 建设 601939/00939 · 农业 601288/01288 · 中国银行 601988/03988 · 交通 601328/03328 · 邮储 601658/01658 |
| 股份行（3） | 招商 600036/03968 · 兴业 601166 · 平安 000001 |
| 城商行（3） | 宁波 002142 · 江苏 600919 · 成都 601838 |
| 指数（6） | 沪深300 000300（基准）· 中证银行 399986 · 申万银行 801780 · 中证红利 000922（红利风格）· **中证银行AH优选 931039**（A/H 因子，每月切换至 A/H 便宜侧）· **智选高股息 932305**（第二代红利策略） |

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

1. `data_puller` → `data/market_YYYY-MM-DD.json`（行情/估值/分红/利率/LPR/南向/指数估值快照）
2. `fin_indicators_puller` → `data/fin_indicators_YYYY-MM-DD.json`（净息差/不良率/逾期率/拨备覆盖率/资本充足率/分红率/规模/每股指标，东财 F10+分红送配）
3. `fin_report_analysis` → `data/fundamentals_YYYY-MM-DD.json`（同花顺财务摘要 + 专项指标自动层透传）
4. `gen_weekly_report` → `output/weekly/bank_weekly_YYYY-MM-DD.md`（周报）
5. `gen_site` → `docs/`（静态站点）

**周报结构（全自动仪表盘，七节 + 附录）**：一、一周速览（指数表+估值水位行+利率汇率行+走势图）；
二、本周变化（涨跌前3后3 + 资金面）；三、跨指数比较（相关性矩阵 + 股息率对比矩阵）；
四、估值水位（PB/股息率近5年分位、52周位置、PB-ROE 对照）；五、A/H 因子；
六、基本面摘要（营收/净利/ROE/息差/资产质量/规模增速）；七、宏观与利率（10Y/Shibor/LPR/信用利差/社融）；
附1 个股行情估值明细 / 附2 个股基本面明细。
个股深度分析见 `output/single/` 单只个股报告（五节：仪表盘/行情/估值/基本面/风险提示）。


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
| 信用利差 | `bond_china_yield` | 商业银行普通债(AAA) 3年 − 国债 3年 | pct |
| LPR | `macro_china_lpr` | 最近一期 1Y/5Y 报价及相对上一期变动（月度，每月 20 日） | % |
| Shibor 1W | `rate_interbank` | 上海同业拆借市场 Shibor 1周最新值 | % |
| 南向资金 | `stock_hsgt_hist_em(南向资金)` | 近 5 个交易日「当日成交净买额」合计 | 亿港元 |
| PB / PE-TTM / 总市值 | `stock_value_em`（东财 datacenter-web） | ≤截止日最新日频 | — / 倍 / 亿元 |
| PB / 股息率近5年分位 | `metrics.py` 自算（估值缓存 `data/valuation_hist/`） | 当前值在近 5 年日频序列中的百分位 | % |
| 52 周位置 | `metrics.py` 自算（hist 日线缓存） | (现价−52周低)÷(52周高−52周低) | % |
| 指数股息率 | `stock_zh_index_value_csindex`（中证官网） | 股息率2（计算用股本加权口径），≤截止日最新；**仅近 1 个月当前值，无历史分位** | % |
| 指数相关性矩阵 | `metrics.py` 自算（hist 日线缓存） | 近 1 年日收益 Pearson 相关 | — |
| 个股股息率 TTM | `stock_history_dividend_detail` 自算 | 近 365 天除息现金分红（元/10股÷10）÷ 现价 | % |
| 规模/每股/逾期 | `stock_financial_analysis_indicator_em`（东财 F10） | 存款/贷款总额对上年同报告期自算 YoY；EPSJB/BPS；逾期率=逾期贷款÷贷款总额 | 亿元 / 元 / % |
| 营收/净利/ROE | `stock_financial_abstract_ths`（同花顺） | 按报告期；ROE 为**单季度**口径 | 亿元 / % |
| 社融/信贷 | `fin_report_analysis.py` 运行时探针（首选 `macro_china_shrzgm`/`macro_rmb_loan`） | 月度；近期社融增量常返回 null（源数据滞后，以 null 如实呈现） | 亿元 |
| 板块超额/利差分位 | 生成器计算 | 中证银行−沪深300 周涨跌差；利差=12家股息率中位数−10Y | pct |
| A/H 因子 | 生成器计算（931039 vs 399986 + watchlist 7 家 A+H 银行） | 优选指数较中证银行周超额：跑赢=A/H 便宜侧（多为 H 折价侧）走强，跑输=A 股侧走强；另列 7 家 AH 溢价率均值/中位数及环比（上周同口径，需 fx 与 hist 缓存，缺失时写「环比待补」） | pct |

**关于 A/H 因子（周报第五节）**：中证银行AH价格优选指数（931039）在中证银行（399986）
样本内每月比较 A/H 价格、持有便宜一侧——其相对 399986 的超额直接反映「折价侧走强还是
A 股侧走强」。周报据此生成解读：优选跑赢 → H 折价侧走强（港股/便宜侧占优）；跑输 →
A 股侧走强（溢价收敛）。溢价汇总与南向资金周净流向并列展示，供人工判断 AH 轮动的
持续性（南向持续净流入通常收敛 H 股折价、推升溢价）。

## 手工维护台账（v20261003 起已废弃）

专项指标（净息差/不良率/拨备覆盖率/核心一级/分红率/规模/每股/逾期）全部由
`fin_indicators_puller` 自动拉取，`data/regulatory_indicators.json` 与
`data/bank_fundamentals.json` 已删除（git 历史可查）。行业监管指标（金监总局季度通报口径）
无自动数据源，不再纳入报告。

**入库策略**：`output/`（周报/个股报告/人工新闻笔记/观点博客）与 `docs/`（站点）入库；
`data/` 下原始快照（`market_*`/`news_*`/`fundamentals_*`/`hist/`/`valuation_hist/`）可按需重拉、
已 gitignore——只有 `spread_history.json`（汇总序列）入库。

## 已知限制

- **东财行情接口（push2/push2his）在部分网络环境（HTTP 代理）下不可达**：
  东财行情类一律不用，`stock_value_em`/`stock_notice_report`（datacenter-web 域名）可用但带重试。
- **指数股息率仅有当前值**：中证官网估值接口只返回近 1 个月（20 行），
  指数股息率无历史序列、分位不可算；银行股息率分位为自算口径。
- **同花顺财务摘要为累计值口径**：Q2/Q3 营收净利为年初至今累计，ROE 为单季度，
  yoy 由接口直接给出；跨期比较时注意口径（INTERFACE_NOTES.md L 节）。
- **社融增量近期常为 null**：源接口（`macro_china_shrzgm` 候选链）数据滞后，
  报告中以「—」如实呈现，不以前值填充。
- **行业监管指标无自动源**：akshare 无金监总局商业银行监管指标接口，v20261003 起不再纳入报告；
  资产质量前瞻仅覆盖逾期率（不良生成率/关注类占比无自动源）。
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
│   ├── methodology.py        # 方法论版本解析（methodology/ 当前版本，供生成器/站点共用）
│   ├── metrics.py            # 估值增强指标离线计算（分位/52周/PB-ROE/相关性矩阵）
│   ├── data_puller.py        # 行情/利率/LPR/南向/指数估值快照 → data/market_*.json
│   ├── news_puller.py        # 新闻公告 → data/news_*.json（v20261003 起退出周报流水线，可单独运行）
│   ├── fin_indicators_puller.py  # 银行专项指标 → data/fin_indicators_*.json
│   ├── fin_report_analysis.py# 基本面分析 → data/fundamentals_*.json
│   ├── gen_weekly_report.py  # 周报生成 → output/weekly/*.md
│   ├── gen_single_report.py  # 个股报告 → output/single/*.md
│   ├── gen_site.py           # 静态站点 → docs/
│   ├── report_fetcher.py     # 定期报告 PDF 下载 → data/reports/*.pdf
│   ├── report_extractor.py   # PDF 指标提取（pdfplumber 锚点+众数）
│   ├── report_anchors/       # 每银行提取锚点覆盖（可选 JSON）
│   ├── templates/            # 周报/个股报告模板（随包内走）
│   └── probes/               # akshare 接口探针（历史，含 probe_results.json）
├── data/                     # 大部分 gitignore（可按需重拉）
│   ├── spread_history.json         # 核心池股息率-10Y利差序列（入库）
│   ├── market_*.json / news_*.json / fundamentals_*.json / fin_indicators_*.json   # 周快照（gitignore）
│   ├── hist/                 # 日线收盘缓存（增量合并，gitignore）
│   ├── valuation_hist/       # 估值历史缓存（PB 日频 + 分红记录，gitignore）
│   └── reports/              # 定期报告 PDF + 提取结果（gitignore，report_fetcher 可重下）
├── output/                   # 有留存价值的产出（全部入库）
│   ├── weekly/               # 生成的周报 Markdown
│   ├── single/               # 生成的个股报告 Markdown
│   ├── news/                 # 人工整理的新闻/事件笔记
│   └── blog/                 # 人工观点文章
├── docs/                     # 生成的静态站点（GitHub Pages Source=/docs）
├── methodology/              # 投研方法论版本 spec（v{yyyyMMdd}.md，不可变、入库；当前版=日期最大者）
├── INTERFACE_NOTES.md        # akshare 接口实测笔记（数据源选型依据）
├── README.md / AGENTS.md / LICENSE (MIT)
```


---

## 单只个股报告

针对单家银行的指标+图表研究报告，与周报共用同一份数据快照（market/fundamentals JSON），
v20261003 起全自动生成，无人工修订环节。

```sh
.venv/bin/python -m cnbankinvest.gen_single_report --code 600036 [--date 2026-09-25]
# 输出: output/single/招商银行_银行个股报告_20260925.md
```

- 五节结构：关键指标仪表盘（估值/分位/52周/PB-ROE/息差/资产质量一览）→ 近期行情与市场表现
  → 估值分析（vs 全池及同板块中位数 + 近5年分位 + 52周区间 + PB-ROE 定位）→
  基本面分析（报告期趋势表含 EPS/BVPS + 专项指标 + 规模增速）→ 风险提示（通用模板）。
- `data/valuation_hist/` 缓存缺失时分位类指标如实显示「—」；`--code` 不在 watchlist 内时报错并列出 12 家可用代码。
- 示例：`output/single/招商银行_银行个股报告_20260925.md`。

---

## 定期报告 PDF 深度分析（report_fetcher + report_extractor）

需要比 F10 指标更深的数据（生息资产结构、贷款质量明细、分段息差等）时，直接下 PDF 提取：

```sh
# 下载（东财公告链路：标题→art_code→pdf.dfcfw.com，实测可用）
.venv/bin/python -m cnbankinvest.report_fetcher --code 601398            # 最新一期（读 fin_indicators 缓存）
.venv/bin/python -m cnbankinvest.report_fetcher --code 601398 --period 2025年报
.venv/bin/python -m cnbankinvest.report_fetcher --all                    # 12 家 × 各自最新一期

# 提取（pdfplumber，锚点正则 + 众数取值，输出指标值/命中页码 + 与 F10 交叉验证）
.venv/bin/python -m cnbankinvest.report_extractor --pdf data/reports/601398_2026中报.pdf --code 601398
```

- PDF 存 `data/reports/`（gitignore 可重下），提取结果 JSON 存 `data/reports/extracts/`。
- 默认正则覆盖净息差（含「净利息收益率」别名）/不良率/拨备覆盖率/资本充足率族/杠杆率/成本收入比；
  某行版式特殊时放 `src/cnbankinvest/report_anchors/{code}.json` 覆盖（见该目录 README）。
- 工行/招行 2026中报实测：与 F10 自动层交叉验证 **6/6 一致**（含由拨贷比推导的拨备覆盖率）。
