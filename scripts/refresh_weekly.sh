#!/bin/bash
# ============================================================
# 银行板块周报 · 一键刷新脚本
# ------------------------------------------------------------
# 用法:
#   scripts/refresh_weekly.sh                 默认: 报告日期 = 今天
#   scripts/refresh_weekly.sh --date 2026-09-25
#
# 依次执行: 行情快照(data_puller) → 新闻公告(news_puller)
#           → 银行专项指标(fin_indicators_puller) → 基本面台账(fin_report_analysis)
#           → 周报生成(gen_weekly_report) → 静态站点(gen_site, docs/ 供 GitHub Pages)。
# 模块位于 src/cnbankinvest（可编辑安装），以 python -m 方式调用。
# 参数 "$@" 原样传给全部脚本（--date 通用；各脚本只取自己认识的参数）。
# 单步失败只警告不中断：后一步读的是 data/ 下 ≤ --date 的最新缓存，
# 所以即使某步网络拉取失败，也能基于已有数据出报告。
# 建议 A股收盘后运行；周五运行生成当周周报，周末运行请显式传周五 --date。
# ============================================================
cd "$(dirname "$0")/.."

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=========================================================="
echo "  银行板块周报 · 一键刷新"
echo "  参数: $*"
echo "=========================================================="

step() {
  local title="$1"; shift
  echo ""
  echo "== $title =="
  if "$@"; then
    echo "-- $title 完成"
  else
    echo "!! $title 失败（不中断，后续步骤使用已有缓存数据）"
  fi
}

step "[1/6] 行情快照 (market_*.json)"   "$PY" -m cnbankinvest.data_puller "$@"
step "[2/6] 新闻公告 (news_*.json)"     "$PY" -m cnbankinvest.news_puller "$@"
step "[3/6] 银行专项指标 (fin_indicators_*.json)" "$PY" -m cnbankinvest.fin_indicators_puller "$@"
step "[4/6] 基本面台账 (fundamentals_*.json)" "$PY" -m cnbankinvest.fin_report_analysis "$@"
step "[5/6] 生成周报 (output/weekly/*.md)"   "$PY" -m cnbankinvest.gen_weekly_report "$@"
step "[6/6] 生成静态站点 (docs/)"       "$PY" -m cnbankinvest.gen_site "$@"

echo ""
echo "=========================================================="
echo "  刷新结束。最新周报: output/weekly/bank_weekly_*.md ｜ 站点: docs/index.html"
echo "  请按报告末尾「人工待办清单」补核心观点与台账。"
echo "=========================================================="
