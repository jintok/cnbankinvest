---
name: cnbankinvest-align
description: 按 methodology/ 最新版方法论对齐报告生成代码，并用已有缓存重生成最新报告与站点
whenToUse: 当新版方法论已发布、需要修改模板/生成器/拉数代码使其与最新方法论一致，并重生成最新报告时
---

让代码与最新方法论对齐，并重生成最新报告（方法论先行的第二步）。规则详见 AGENTS.md「方法论版本管理」节。

## 流程

1. 列出 `methodology/v*.md`，取日期最大者为最新版；取其前一版，`diff` 两版得到**变更清单**（若是初始版本无前一版，则跳过 diff，直接向用户确认要对齐的范围）。
2. 逐条把变更映射到代码，定位受影响处：
   - 报告结构/章节/占位符 → `src/cnbankinvest/templates/*.md`、`gen_weekly_report.py`、`gen_single_report.py`
   - 指标口径 → 对应 render_* 函数、`charts.py`、`fin_report_analysis.py`、各 puller
   - 标的池 → `watchlist.json`（唯一标的源，不得硬编码）
   - 数据源 → 先读 `INTERFACE_NOTES.md`（改数据源前必读，遵守其中的网络约定）
3. 逐项实现修改；保持项目约定：中文输出、tmp+replace 原子写、单步失败不中断、纯标准库（不新增依赖，除非用户批准）。
4. 重生成最新报告验证（用已有缓存，不拉网）：
   - `.venv/bin/python -m cnbankinvest.gen_weekly_report --date {最新周报日期}`（日期取 `output/weekly/` 最新文件名）
   - 如有需要：`.venv/bin/python -m cnbankinvest.gen_single_report --code 600036 --date {同日}`
   - `.venv/bin/python -m cnbankinvest.gen_site`
5. 检查生成报告尾部「方法论版本：」应为最新版本号；用 `git diff` 核对产出变化符合预期。
6. 输出「改动项 × 方法论条目」对照表，供用户人工核对。

## 注意

- 本 skill 只负责**对齐**，不修改方法论文件本身；发现方法论写得不清楚时，提示用户走 `cnbankinvest-new-methodology` 发修订版。
- 已知数据缺口（指数股息率 null 等）是设计内行为，如实呈现，不得用前值填充。
