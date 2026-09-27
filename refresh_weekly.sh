#!/bin/bash
# ============================================================
# 银行板块周报 · 一键刷新脚本
# ------------------------------------------------------------
# 用法:
#   ./refresh_weekly.sh                 默认: 报告日期 = 今天
#   ./refresh_weekly.sh --date 2026-09-25
#
# 依次执行: 行情快照(data_puller) → 新闻公告(news_puller)
#           → 基本面台账(fin_report_analysis) → 周报生成(gen_weekly_report)
#           → 静态站点(gen_site, docs/ 供 GitHub Pages)。
# 参数 "$@" 原样传给全部脚本（--date 通用；各脚本只取自己认识的参数）。
# 单步失败只警告不中断：后一步读的是 data/ 下 ≤ --date 的最新缓存，
# 所以即使某步网络拉取失败，也能基于已有数据出报告。
# 建议 A股收盘后运行；周五运行生成当周周报，周末运行请显式传周五 --date。
# ============================================================
cd "$(dirname "$0")"

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

step "[1/5] 行情快照 (market_*.json)"   "$PY" data_puller.py "$@"
step "[2/5] 新闻公告 (news_*.json)"     "$PY" news_puller.py "$@"
step "[3/5] 基本面台账 (fundamentals_*.json)" "$PY" fin_report_analysis.py "$@"
step "[4/5] 生成周报 (weekly/*.md)"     "$PY" gen_weekly_report.py "$@"
step "[5/5] 生成静态站点 (docs/)"       "$PY" gen_site.py "$@"

echo ""
echo "=========================================================="
echo "  刷新结束。最新周报: weekly/bank_weekly_*.md ｜ 站点: docs/index.html"
echo "  请按报告末尾「人工待办清单」补核心观点与台账。"
echo "=========================================================="
