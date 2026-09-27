# AGENTS.md

## Purpose

个人投研工具：12 家上市银行（A+H）+ 4 指数的每周跟踪。流水线：akshare 拉数 → 生成 Markdown 周报/个股报告 → 人工补观点。无 Web 框架、无测试、无 linter/CI。

## Commands

```bash
./refresh_weekly.sh                      # 一键全流程（5 步，见下）；周五收盘后运行
./refresh_weekly.sh --date 2026-09-25   # 复现指定日期；周末运行必须显式传周五
.venv/bin/python gen_single_report.py --code 600036 [--date ...]   # 个股报告 → single/
```

- 单独重跑某一步：直接 `.venv/bin/python <script>.py --date ...`（各脚本均支持 `--date`）。
- 验证改动：用已有数据重跑生成器即可（如 `gen_weekly_report.py --date 2026-09-25`），不需要真实拉网。

## Environment

- Python 3.14 venv 在 `.venv/`（uv 创建）；依赖只有 **akshare 1.18.97 + pandas 3.0.6**。
- **仓库没有 requirements/pyproject**，`.venv` 已 gitignore——重建环境需手动 `uv venv && uv pip install akshare pandas`。

## Pipeline（refresh_weekly.sh 的 5 步，顺序固定）

1. `data_puller.py` → `data/market_*.json`
2. `news_puller.py` → `data/news_*.json`
3. `fin_report_analysis.py` → `data/fundamentals_*.json`（合并手工台账）
4. `gen_weekly_report.py` → `weekly/bank_weekly_*.md`
5. `gen_site.py` → `docs/`（GitHub Pages 静态站，Source=/docs）

关键语义：**单步失败不中断**——每步只读 `data/` 下 ≤ `--date` 的最新缓存，生成器与拉数解耦。`--date` 由 shell 原样传给全部脚本，各脚本只取自己认识的参数。

## Architecture

- `watchlist.json` — 标的唯一来源（12 银行 + 指数），所有脚本从这里读，不要硬编码代码。
- `data/hist/*.json` — 日线收盘增量缓存，**tmp+replace 原子写**，沿用此约定。
- 手工维护、**脚本不得覆盖**：`data/regulatory_indicators.json`（行业监管指标）、`data/bank_fundamentals.json`（个股专项）——缺项在报告中标「待填」属正常设计。
- `templates/` — 周报/个股报告模板；`INTERFACE_NOTES.md` — akshare 接口实测笔记，**改数据源前必读**。
- `gen_site.py` 为纯标准库 Markdown 渲染器（无外部依赖、可离线），会把【待人工撰写】等内部标记剥除后再出网页版。

## Network gotchas（实测结论，勿凭直觉换接口）

- 本机走代理 `REDACTED-PROXY`：**东财行情域名（push2/push2his）一律不可用**——不要用 `stock_zh_a_hist`/`stock_hk_hist`/`index_zh_a_hist`；东财 datacenter-web 域名（`stock_value_em`/`stock_notice_report`）可用但必须重试。
- 拉数脚本通用约定：try/except + 东财源重试 ≥2 次间隔 ≥2s + 普通源间隔 ≥1s + `socket.setdefaulttimeout(30)` + JSON 缓存；新浪源高频会封 IP。
- 已知数据缺口是设计内行为（指数股息率、社融近期 null 等），报告如实呈现，**不要用前值填充**。

## Conventions

- 全部输出（报告、注释、日志）为中文；报告中的核心观点等人工段落以【待人工撰写】标记，由人完成后删除标记。
- 改动验证方式：重跑生成器 + 人工核对输出 Markdown；无自动化测试。
