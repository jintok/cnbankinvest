---
name: cnbankinvest-check
description: 只读审计：核对最新方法论、报告生成代码与最新产出报告三者是否一致，输出差异清单
whenToUse: 当用户想检查当前代码/报告是否与最新方法论一致，或周报生成后想核对口径是否偏离 spec 时
---

只读审计：最新方法论 vs 当前代码 vs 最新报告。**不修改任何文件**。

## 流程

1. 列出 `methodology/v*.md`，取日期最大者为当前版本，通读全文（研究范围/报告结构/指标口径/数据源/已知限制各节）。
2. 逐项核对代码（给出 `文件:行号` 证据）：
   - 标的池：`watchlist.json` 与方法论「研究范围」是否一致（12 银行 + 指数清单）。
   - 报告结构：`src/cnbankinvest/templates/weekly_template.md`、`single_bank_template.md` 章节与方法论「周报结构」是否一致。
   - 指标口径：`gen_weekly_report.py` / `gen_single_report.py` / `fin_report_analysis.py` 中各指标实现与「指标口径」表逐行核对（周涨跌、YTD、股息率 TTM、利差、AH 溢价、南向资金、PB/PE、财务指标）。
   - 数据源：各 puller 用的接口与「数据源与可用性」节是否一致（特别注意东财 push2/push2his 不得使用）。
   - 版本标注：最新 `output/weekly/*.md`、`output/single/*.md` 尾部「方法论版本：」是否等于当前版本号。
3. 输出审计报告（中文）：
   - ✅ 一致项（一句话带过）
   - ❌ 不一致项：每条含【方法论条目】【代码/报告现状（文件:行号）】【修复建议——改代码走 `cnbankinvest-align`，改方法论走 `cnbankinvest-new-methodology`】
   - ⚠️ 无法自动判定的项（如需人工判断的口径细节）

## 注意

- 严格只读：不重跑生成器、不写文件；如需验证性重跑，先征得用户同意（会覆盖 `output/` 最新报告）。
- 已知数据缺口（指数股息率 null、社融 null 等）是设计内行为，不算不一致。
