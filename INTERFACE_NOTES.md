# 银行股研究 · akshare 接口摸底笔记

> 日期：2026-09-27 ｜ 环境：`.venv`（uv 创建，CPython 3.14.6）｜ akshare **1.18.97** ｜ pandas 3.0.6
> 复现方式：`.venv/bin/python src/cnbankinvest/probes/probe_interfaces.py`（第一轮 A–N）+ `src/cnbankinvest/probes/probe_round2.py`（参数修正复测 + 备用源）
> 原始探针输出：`src/cnbankinvest/probes/probe_results.json`
> 2026-09-27 补充：FX 与指数股息率探测（见文末「补充探测」）

## 0. 总体结论（先看这个）

| 用途 | 主选接口 | 备用 |
|---|---|---|
| A股日线 | `stock_zh_a_daily`（新浪）✅ | `stock_zh_a_hist`（东财，代理环境下不稳❌） |
| H股日线 | `stock_hk_daily`（新浪）✅ | `stock_hk_hist`（东财，代理环境下不稳❌） |
| 指数日线 | `stock_zh_index_hist_csindex`（中证，000300/399986/000922）✅ | `stock_zh_index_daily`（新浪，sh000300/sz399986）✅；申万801780用 `index_hist_sw` ✅ |
| 个股市值/PE/PB | `stock_value_em`（东财数据中心）✅ | `stock_zh_valuation_baidu`（百度，单指标序列）✅ |
| 10Y国债 | `bond_china_yield` ✅ | `bond_zh_us_rate` 的中国列为 NaN，不可用❌ |
| SHIBOR | `rate_interbank`（indicator="1周"）✅ | — |
| 南向资金 | `stock_hsgt_hist_em(symbol="南向资金")` ✅ | `stock_hsgt_fund_flow_summary_em()`（当日汇总）✅ |
| 新闻 | `stock_info_global_em` / `stock_info_global_cls` ✅ | — |
| 公告 | `stock_notice_report(symbol=报告类型, date)` ✅ | — |
| 财务摘要 | `stock_financial_abstract_ths`（同花顺）✅ | — |
| 汇率(HKD) | `currency_boc_sina(symbol="港币")` ✅ | `fx_spot_quote()`（HKD/CNY 常为 NaN，不可靠⚠️） |

**最大的坑：在部分网络环境（HTTP 代理）下，东财行情域名（`push2his.eastmoney.com`、`NN.push2.eastmoney.com`）经 Python requests 反复 `ProxyError`（两轮共 6 次全部失败），而 curl 偶尔能通——表现为不稳定，不能依赖。** 东财 `datacenter-web` 类接口（`stock_value_em`、`stock_notice_report`）和新浪/中证/申万/中债/同花顺源均稳定。所有拉数脚本必须：try/except + 失败重试 ≥2 次 + 调用间隔 ≥2s + `socket.setdefaulttimeout(30)` 兜底 + JSON 缓存（沿用仓库「脚本+结果同目录、增量合并、原子写」约定）。

---

## A. A股日线

- ❌ `ak.stock_zh_a_hist(symbol="601398", period="daily", start_date="20260901", end_date="20260927", adjust="qfq")` — **东财 kline 接口，在代理网络环境下反复拒绝（ProxyError ×6）**。period="weekly" 曾在第一轮侥幸成功一次（37 行，2026-01-09→2026-09-24），证实参数本身正确，纯属网络抖动。成功时列：`日期/股票代码/开盘/收盘/最高/最低/成交量/成交额/振幅/涨跌幅/涨跌额/换手率`（qfq 价保留 2 位小数）。
- ✅ **替代（推荐主用）**：`ak.stock_zh_a_daily(symbol="sh601398", start_date="20260901", end_date="20260927", adjust="qfq")`（新浪）
  - 签名：`(symbol: str = 'sh603843', start_date: str = '19900101', end_date: str = '21000118', adjust: str = '')`
  - 列：`date(datetime.date)/open/high/low/close/volume(股)/amount(元)/outstanding_share/turnover(换手率，小数形式，0.001≈0.1%)`
  - 样例（工行 2026-09-24）：close=8.13，volume=2.68 亿股，amount=21.8 亿元
  - ⚠️ symbol 需带交易所前缀 `sh/sz/bj`；docstring 原话警告「大量抓取容易封 IP」，务必控制频率+缓存

## B. A股实时快照

- ❌ `ak.stock_zh_a_spot_em()` — 东财 clist 接口，同东财行情域名不可用问题。
- ✅ **替代**：`ak.stock_zh_a_spot()`（新浪）——5568 行全市场，列：`代码(sh601398格式)/名称/最新价/涨跌额/涨跌幅/买入/卖出/昨收/今开/最高/最低/成交量/成交额/...`；盘中数据，收盘后「最新价」即收盘价。

## C. 个股估值历史（PE/PB/市值）

- ❌ `ak.stock_a_indicator_lg(symbol="601398")` — **akshare 1.18.97 已删除该函数**（`AttributeError`）。乐咕数据源只剩指数级接口（`stock_index_pe_lg` 等）。
- ✅ `ak.stock_value_em(symbol="601398")` — 东财数据中心（**域名不同，稳定**）。工行 2120 行日频（约 8.5 年）。列：`数据日期/当日收盘价/当日涨跌幅/总市值/流通市值(元)/总股本/流通股本/PE(TTM)/PE(静)/市净率/PEG值/市现率/市销率`。样例 2026-09-24：总市值 2.898 万亿，PE(TTM) 7.74，PB 0.73。**无股息率字段**。
- ✅ `ak.stock_zh_valuation_baidu(symbol="601398", indicator="市盈率(TTM)", period="近一年")` — 365 行 `date/value`，指标可选 `总市值/市盈率(TTM)/市盈率(静)/市净率/市现率`；❌ indicator="股息率" 不支持（返回 NoneType 错误）。
- 股息率补充：指数级 `ak.stock_a_gxl_lg(symbol="上证A股")` ✅（`日期/股息率%`，5279 行全历史，2026-09-24 为 2.61%；**合法 symbol 只有 上证A股/深证A股/创业板/科创板**，不支持沪深300/中证红利/中证银行，见补充探测）；个股市息率用 `ak.stock_history_dividend_detail(symbol="601398", indicator="分红")` 自算（`派息` 列单位 = **元/10股**，工行共 23 条分红记录；除权除息日可能为 NaT，需过滤）。

## D. H股日线

- ❌ `ak.stock_hk_hist(symbol="01398", period="daily", start_date="20260901", end_date="20260927", adjust="qfq")` — 东财 kline，同东财行情域名不可用问题。
- ✅ **替代（推荐主用）**：`ak.stock_hk_daily(symbol="01398", adjust="qfq")`（新浪）——返回**全历史**（工行 4900 行），**无 start/end 参数，需自行截断**；列：`date/open/high/low/close/volume(股)/amount(元)`。样例 2026-09-25：close=7.515。

## E. H股实时快照

- ❌ `ak.stock_hk_spot_em()` — 东财 clist，同东财行情域名不可用问题。
- ✅ **替代**：`ak.stock_hk_spot()`（新浪）——2806 行，列：`日期时间/代码/中文名称/英文名称/交易类型/最新价/涨跌额/涨跌幅/昨收/今开/最高/最低/...`。

## F. 指数日线

- ❌ `ak.index_zh_a_hist(symbol="399986"/"000300"/"801780", period="daily", ...)` — 东财，全部 ProxyError。
- ✅ `ak.stock_zh_index_hist_csindex(symbol="000922", start_date="20260901", end_date="20260927")` — **中证官网，支持日期区间，推荐作为 000300/399986/000922 主源**。列：`日期/指数代码/指数中文全称/指数中文简称/.../开盘/最高/最低/收盘/涨跌/涨跌幅/成交量/成交金额`（已验证 000922、399986 均可用，000300 同是中证指数应一致）。
- ✅ `ak.stock_zh_index_daily(symbol="sh000300")` / `("sz399986")` — 新浪，**一次性返回全历史、无日期参数**；列：`date/open/high/low/close/volume`。sh000300 自 2002-01-04（6000 行），sz399986 自 2015-05-19（2763 行）。适合做初始化，不适合做增量。
- ✅ `ak.index_hist_sw(symbol="801780", period="day")` — **申万宏源官网，801780 唯一可用源**。列：`代码/日期/收盘/开盘/最高/最低/成交量/成交额`，自 2014-02-21（3049 行），同样一次性全历史。
- 小结：000300/399986/000922 → csindex（可控区间）主、新浪备；801780 → index_hist_sw。

## G. 中国国债收益率（10Y）

- ✅ `ak.bond_china_yield(start_date="20260901", end_date="20260927")` — 中国债券信息网，稳定。列：`曲线名称/日期/3月/6月/1年/3年/5年/7年/10年/30年`（单位 %）。
- 每次返回 **3 条曲线**：`中债国债收益率曲线`、`中债中短期票据收益率曲线(AAA)`、`中债商业银行普通债收益率曲线(AAA)`——取 10Y 国债须过滤 `曲线名称=="中债国债收益率曲线"` 再取 `10年` 列。样例：2026-09-24 = **1.6738**。

## H. SHIBOR / 同业拆借

- ✅ `ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1周")`
- ⚠️ **indicator 必须写 "1周"，写 "7天" 会 `KeyError: '7天'`**；合法值：`隔夜/1周/2周/3周/1月/.../11月/1年`。列：`报告日/利率(%)/涨跌`。4989 行自 2006-10-08，更新到 T-1（2026-09-24 = 1.40）。

## I. 南向资金

- ✅ `ak.stock_hsgt_hist_em(symbol="南向资金")` — ⚠️ **合法 symbol：`北向资金/沪股通/深股通/南向资金/港股通沪/港股通深`；写 "港股通" 会 `KeyError`**。列：`日期/当日成交净买额/买入成交额/卖出成交额(亿元口径)/历史累计净买额/当日资金流入/当日余额/持股市值/领涨股/沪深300/...`。2722 行自 2014-11-17，更新到前一交易日。
- ✅ `ak.stock_hsgt_fund_flow_summary_em()` — 当日汇总 4 行（沪港通/深港通 × 方向），列：`交易日/类型/板块/资金方向/成交净买额/资金净流入/当日资金余额/上涨数/持平数/下跌数/相关指数/指数涨跌幅`。

## J. 新闻快讯

- ✅ `ak.stock_info_global_em()` — 200 行，列：`标题/摘要/发布时间/链接`，覆盖最近约 24–48 小时全球快讯（样例最新 2026-09-26 05:15）。
- ✅ `ak.stock_info_global_cls()` — 财联社电报 20 行，列：`标题/内容/发布日期/发布时间`，含**当日**（探针时点 2026-09-27 11:54）。做周报「一周要闻」需自行按时间窗过滤或换历史接口；**该接口无 URL 字段**。

## K. 公告

- ✅ `ak.stock_notice_report(symbol="全部", date="20260925")`
- ⚠️ **symbol 是「报告类型」不是股票代码**：`全部/重大事项/财务报告/融资公告/风险提示/资产重组/信息变更/持股变动`；`symbol="601398"` 会 `KeyError`（第一轮 K2 即因此失败）。按**单日**查询，返回当天全市场（约 800 行），列：`代码/名称/公告标题/公告类型/公告日期/网址`，按代码自行过滤即可；date 格式 `yyyymmdd`。第一轮失败系东财抖动，复测（间隔+重试）成功。

## L. 财务摘要（同花顺）

- ✅ `ak.stock_financial_abstract_ths(symbol="601398", indicator="按报告期")` — 85 个报告期自 2003-12-31。列含：`报告期/净利润/净利润同比增长率/扣非净利润/营业总收入/营业总收入同比增长率/基本每股收益/每股净资产/净资产收益率/净资产收益率-摊薄/产权比率/资产负债率` 等。
- ⚠️ **数值全是带单位字符串**（`"1736.82亿"`、`"3.32%"`），入库前必须解析；ROE 为**单季度**口径（工行 2026Q2 = 4.32%）；❌ 无净息差/不良率等银行专项指标。

## M. bank 相关接口扫描

`dir(ak)` 含 "bank" 共 28 个，逐一核查结论：**没有「商业银行净息差/不良率/拨备覆盖率」类监管指标接口**。明细：

- ❌ 无用：`macro_bank_*_interest_rate` ×10+ —— 全是**外国央行**利率决议（美联储/欧央行/日本央行…），与中国的银行研究无关。
- ✅ 可用且相关：`macro_rmb_deposit()`（人民币存款，月度，`新增存款-数量` 单位亿元）、`macro_rmb_loan()`（人民币贷款，月度，亿元）、`macro_china_bank_financing()`（银行理财，月度）。
- ❌ `stock_gpzy_distribute_statistics_bank_em()`（银行股质押分布）报 `TypeError: 'NoneType' object is not subscriptable`，源站数据问题。
- 银行专项指标（净息差/不良/拨备）后续方案：从 L 的财务摘要拿 `资产负债率/ROE`，其余靠定期报告手工维护，或再接别的数据源。
- 另注：`stock_a_gxl_lg`（指数股息率）虽不含 bank 字样，见 C 节，对红利风格研究有用。

## N. 中美国债收益率（bond_zh_us_rate）

- ✅ `ak.bond_zh_us_rate()` 本身可用：9352 行自 1990-12-19，列 `日期/中国国债收益率2年/5年/10年/30年/10年-2年/中国GDP年增率/美国国债收益率...`。
- ⚠️ **中国各期限列近期全是 NaN**（美国侧正常：10Y=5.17 @2026-09-25）——中国 10Y 以 G（`bond_china_yield`）为准，本接口仅适合取美国收益率/利差参考。

---

## 补充探测（2026-09-27，为 data_puller.py 选型）

### FX（HKD/CNY）

- ✅ **`ak.currency_boc_sina(symbol="港币", start_date="20260920", end_date="20260927")`** —— 中行外汇牌价（新浪源，稳定）。列：`日期(datetime.date)/中行汇买价/中行钞买价/中行钞卖价/汇卖价/央行中间价/中行折算价`。**牌价单位 = 100 外币兑人民币**，故 `cny_per_hkd = 中行折算价 / 100`（2026-09-26 = 86.05 → 0.8605）。港币的 `央行中间价` 列为 NaN，不可用。
- ⚠️ `ak.fx_spot_quote()`（CFETS 即期 25 行）虽有 `HKD/CNY` 行，但买/卖报价均为 NaN——**不可靠，仅作最后备选**。
- ❌ `ak.forex_hist_em()` 签名只有 symbol、无日期参数，不适合按日取值。

### 指数股息率（div_yield_ttm）

- ❌ `stock_a_gxl_lg` 合法 symbol 仅 `{上证A股, 深证A股, 创业板, 科创板}`（源码 choice 枚举），**沪深300/中证红利/中证银行均 `KeyError`**——指数级股息率接口不可用，周报指数股息率字段置 null。
- 个股市息率：`stock_history_dividend_detail(symbol, indicator="分红")` ✅，按「除权除息日落在近 365 天」过滤、`派息(元/10股)/10` 求和 ÷ 现价。

---

## 附：credit_macro 运行时探针（fin_report_analysis.py，2026-09-27 实测）

`fin_report_analysis.py` 在运行时对 `dir(ak)` 过滤 `shrzgm/social/loan/credit`（限 `macro_china*`/`macro_rmb*` 前缀）得到 3 个候选，按需尝试（≤4 次调用，第一个可用者胜）：

| 接口 | 结果 | 说明 |
|---|---|---|
| `macro_china_shrzgm()` | ✅ 136 行 | 社融增量。列：`月份/社会融资规模增量/其中-人民币贷款/…`；`月份` 为 `"202604"` 紧凑格式（需规范化成 `2026-04`）；**滞后约 2 个月**（实测最新 2026-04，2026-05 起为 None） |
| `macro_rmb_loan()` | ✅ 30 行 | 新增人民币贷款。列：`月份/新增人民币贷款-总额/…`；`月份` 为 `"2026-08"` 格式；总额单位亿元，数值型 |
| `macro_china_new_financial_credit()` | 未触发 | 备选，前两个可用故未调用 |

注意：两行"其中-人民币贷款"（社融口径，31522 @2026-03）与 `macro_rmb_loan` 新增贷款（29900 @2026-03）口径不同，脚本按需求采用后者。THS 财务摘要列无 `归母净利润`，脚本回退用 `净利润`；pandas 3.0 下缺失值经 `to_dict` 会变成 `False`，解析时需按缺失处理。

## 附：通用注意事项

- **网络环境**：东财行情域名（push2/push2his）在代理网络环境下经 requests 不稳定，脚本里对东财源一律「重试 3 次 + 间隔 3s」，仍失败则落备用源。
- **超时**：akshare 接口大多无 timeout 参数，脚本入口设 `socket.setdefaulttimeout(30)`。
- **单位速查**：新浪 A股日线 volume=股、amount=元；东财 hist 成交额=元；`stock_value_em` 市值=元；南向资金历史=亿元；同花顺财务摘要=带单位字符串；存贷款月度=亿元；收益率=%；中行牌价=100 外币。
- **日期格式**：东财/中证/中债类用字符串 `yyyymmdd`；新浪系返回 `datetime.date` 对象；csindex 返回 `日期` 字符串列。
- **Python 3.14 兼容性**：本轮探针全部接口在 3.14.6 下无兼容性问题（akshare 1.18.97 + pandas 3.0.6）。

---

## 专项指标与定期报告链路（2026-09-27 实测，阶段1/2 选型依据）

### 东财 F10 主要指标（银行专项列，datacenter-web 域名，可用需重试）

`ak.stock_financial_analysis_indicator_em(symbol="601398.SH", indicator="按报告期")` — 141 列，85 个报告期。

- **银行专项列**：`NET_INTEREST_MARGIN`(净息差%) / `NET_INTEREST_SPREAD`(净利差%) /
  `NON_PERFORMING_LOAN`(不良余额,元) / `GROSSLOANS`(贷款总额,元) / `LOAN_PROVISION_RATIO`(拨贷比%) /
  `HXYJBCZL`(核心一级%) / `FIRST_ADEQUACY_RATIO`(一级%) / `NEWCAPITALADER`(资本充足率%) /
  `TOTALDEPOSITS`(存款,元) / `LTDRR`(贷存比,小数)。
- ⚠️ `RISK_COVERAGE`(拨备覆盖率) 列存在但 12 家银行全为 None → **由拨贷比÷不良率推导**（不良率=不良余额÷贷款总额），实测与报告值一致（工行 2.80/1.287≈217.5 vs 报告 217.58）。
- 口径：NIM/不良余额**季度**更新；拨贷比/资本充足率族**半年度**（季报行为 NaN，符合监管披露节奏）。
- 报告期标识：`REPORT_DATE_NAME`（"2026中报"）+ `NOTICE_DATE`（披露日）。

### 东财分红送配详情（分红率推导）

`ak.stock_fhps_detail_em(symbol="601398")` — 按报告期一行（年报/中报各一），列含
`现金分红-现金分红比例`(每10股派X元) / `每股收益`(该期累计) / `方案进度`。
**分红率 = 财年内(中期+期末)每10股分红合计÷10 ÷ 年报EPS**：工行 FY2025 = (1.414+1.689)/10÷1.00 = 31.0% ✓。

### 定期报告 PDF 下载链路（report_fetcher 用）

1. `ak.stock_individual_notice_report(security, symbol="财务报告", begin_date, end_date)` — datacenter-web 域名✅，含定期报告全文条目（标题+公告页 URL）。
2. `https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code=AN...&client_source=web&page_index=1` — 公告内容 JSON，`attach_list` 含 PDF 直链与大小；**挑最大附件**（全文而非摘要）。urllib 直连可用。
3. `https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf` — PDF 下载✅（工行中报 6MB）。
- 巨潮 cninfo hisAnnouncement API 可达但 SSE 股票参数需先查 org-id，暂不用（东财链路已够）。

### PDF 提取版式要点（report_extractor 用，工行/招行 2026中报实测）

- 主要指标表行格式：`不良贷款率（7） 1.29 1.31 1.34`——label 后有**全角脚注号**`（N）`，随后 3 列为本期/上期/上上期；正则必须先跳过脚注号再取数。
- 别名：工行 NIM 写作「净利息收益率」、spread 写作「净利息差」；招行用「净息差」。资本充足率族需 lookbehind 防子串误配（核心一级/一级 vs 总）。
- 同一指标在摘要/主要指标/管理层讨论/子公司段落重复出现且**口径可能不同**（集团 vs 银行/权重法），取**众数**、离散时退回首现值（最早页=法定主要指标表）。
- 交叉验证：工行/招行中报提取值 vs F10 自动层 6/6 一致。
