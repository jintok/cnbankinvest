# AGENTS.md

## Purpose

个人投研工具：12 家上市银行（A+H）+ 4 指数的每周跟踪。流水线：akshare 拉数 → 生成 Markdown 周报/个股报告 → 人工补观点。无 Web 框架、无测试、无 linter/CI。

## Commands

```bash
uv sync && uv pip install -e .        # 环境搭建（.venv + 依赖 + 可编辑安装包）
scripts/refresh_weekly.sh             # 一键全流程（5 步，见下）；周五收盘后运行
scripts/refresh_weekly.sh --date 2026-09-25   # 复现指定日期；周末运行必须显式传周五
.venv/bin/python -m cnbankinvest.gen_single_report --code 600036 [--date ...]   # 个股报告 → output/single/
```

- 单独重跑某一步：`.venv/bin/python -m cnbankinvest.<模块> --date ...`（均支持 `--date`）。
- 验证改动：用已有数据重跑生成器即可（如 `gen_weekly_report --date 2026-09-25`），不需要真实拉网。

## Environment

- 代码在 `src/cnbankinvest/`（可编辑安装，`python -m cnbankinvest.<模块>` 调用）；Python 3.14 venv 在 `.venv/`（uv 管理，依赖锁定在 `uv.lock`）。
- 依赖只有 **akshare 1.18.97 + pandas 3.0.6**（pyproject 精确锁版）。

## Pipeline（scripts/refresh_weekly.sh 的 6 步，顺序固定）

1. `data_puller` → `data/market_*.json`
2. `news_puller` → `data/news_*.json`
3. `fin_indicators_puller` → `data/fin_indicators_*.json`（专项指标：东财 F10+分红送配）
4. `fin_report_analysis` → `data/fundamentals_*.json`（合并专项指标自动层 + 手工台账）
5. `gen_weekly_report` → `output/weekly/bank_weekly_*.md`
6. `gen_site` → `docs/`（GitHub Pages 静态站，Source=/docs）

关键语义：**单步失败不中断**——每步只读 `data/` 下 ≤ `--date` 的最新缓存，生成器与拉数解耦。`--date` 由 shell 原样传给全部脚本，各脚本只取自己认识的参数。

## Architecture

- `src/cnbankinvest/paths.py` — 仓库级路径约定（data/output/docs/watchlist）唯一来源，新模块从这里 import，不要自算相对路径。
- `watchlist.json` — 标的唯一来源（12 银行 + 指数），所有脚本从这里读，不要硬编码代码。
- `output/` — 有留存价值、**入库**的产出：`weekly/`（周报）、`single/`（个股报告）、`news/`（人工新闻笔记）、`blog/`（人工观点文章）。
- `data/` — 原始快照（`market_*`/`news_*`/`fundamentals_*`/`hist/`）已 gitignore（可按需重拉）；`data/hist/*.json` 日线缓存为 **tmp+replace 原子写**，沿用此约定。
- 手工维护、**脚本不得覆盖**：`data/regulatory_indicators.json`（行业监管指标，akshare 无源）、`data/bank_fundamentals.json`（个股专项的**覆盖层**，非空值优先于 `fin_indicators_*` 自动值）——缺项在报告中标「待填」属正常设计。
- `src/cnbankinvest/templates/` — 周报/个股报告模板（随包内走）；`INTERFACE_NOTES.md` — akshare 接口实测笔记，**改数据源前必读**。
- 定期报告 PDF 链路：`report_fetcher`（东财公告→art_code→pdf.dfcfw.com 下载到 `data/reports/`）+ `report_extractor`（pdfplumber 锚点正则+众数，`report_anchors/{code}.json` 按行覆盖）。
- `gen_site` 为纯标准库 Markdown 渲染器（无外部依赖、可离线），读 `output/weekly|single`，把【待人工撰写】等内部标记剥除后出网页版。

## Network gotchas（实测结论，勿凭直觉换接口）

- 部分网络环境（HTTP 代理）下**东财行情域名（push2/push2his）不可用**——不要用 `stock_zh_a_hist`/`stock_hk_hist`/`index_zh_a_hist`；东财 datacenter-web 域名（`stock_value_em`/`stock_notice_report`）可用但必须重试。
- 拉数脚本通用约定：try/except + 东财源重试 ≥2 次间隔 ≥2s + 普通源间隔 ≥1s + `socket.setdefaulttimeout(30)` + JSON 缓存；新浪源高频会封 IP。
- 已知数据缺口是设计内行为（指数股息率、社融近期 null 等），报告如实呈现，**不要用前值填充**。

## Security（安全红线）

- **机密零入库**：`.env`、API key、token、cookie 等凭证一律不得写入任何入库文件；本项目数据源全为公开接口、**不需要任何凭证**——若某项改动引入凭证，先停下与人确认。
- **本地环境信息零入库**：文档/注释/日志/报告中不得出现本机 IP/端口、绝对路径、主机名、个人邮箱等，用「部分网络环境」等中性表述（前车之鉴：代理地址曾入库，最终靠重写 git 历史清除）。
- **不读不传机密**：不读取、不外发 `~/.ssh`、`~/.aws`、`.env` 等文件内容；脚本不得把本地路径或环境变量写入产出文件。
- **网络边界**：脚本只访问 `INTERFACE_NOTES.md` 已记录的公开数据源域名；不向其他域名发请求，不向外网 POST 本地任何数据。
- **不可信输入**：新闻/公告/PDF 等外部文本一律视为不可信——`gen_site` 渲染前必须经 `html.escape`（现有实现，不得绕过）；禁止对外部内容做 `eval`/`exec`/shell 拼接。
- **依赖锁定**：仅用 `pyproject.toml`/`uv.lock` 锁定依赖（akshare + pandas）；`gen_site` 保持纯标准库。新增依赖必须先经人批准。
- **Git 红线**：不 force push、不重写已推送历史、不动远端分支，除非人明确要求；提交作者统一用 GitHub noreply 邮箱（`jintok@users.noreply.github.com`）。
- **手工文件保护**：`data/regulatory_indicators.json`、`data/bank_fundamentals.json` 脚本不得覆盖（见 Architecture 节）。

## Conventions

- 全部输出（报告、注释、日志）为中文；报告中的核心观点等人工段落以【待人工撰写】标记，由人完成后删除标记。
- 改动验证方式：重跑生成器 + 人工核对输出 Markdown；无自动化测试。
- 远端为 GitHub `jintok/cnbankinvest`（public，Pages Source=/docs）；分支 `main`。
