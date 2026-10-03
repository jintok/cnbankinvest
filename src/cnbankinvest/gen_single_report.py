#!/usr/bin/env python3
"""单家银行个股研究报告生成器（纯自动「指标 + 图表」，方法论 v20261003 结构）。

读取 data/ 下 ≤ --date 的最新 market_*.json / fundamentals_*.json，
按 watchlist.json 解析 --code（A股代码）对应标的，填充包内 templates/single_bank_template.md，
写出 output/single/{名称}_银行个股报告_{yyyymmdd}.md（tmp+replace 原子写）。
纯标准库实现（不联网、不依赖 akshare）；估值分位/52 周/PB-ROE 经 cnbankinvest.metrics
读 data/valuation_hist/ 与 data/hist/ 缓存离线计算。

用法：
    .venv/bin/python -m cnbankinvest.gen_single_report --code 600036 [--date 2026-09-25] [--out 输出目录]
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

from cnbankinvest import methodology, metrics
from cnbankinvest.charts import fence, relative_line_spec
from cnbankinvest.paths import DATA_DIR, SINGLE_DIR, WATCHLIST_PATH as WATCHLIST

TEMPLATE = Path(__file__).resolve().parent / "templates" / "single_bank_template.md"

NEAR_THRESHOLD = 0.05   # 与中位数偏离 ±5% 以内视为「接近」

# THS 报告期标签 ↔ F10 报告期名（用于把 EPS/BVPS 并入趋势表）
Q_TO_F10 = {"Q1": "一季报", "Q2": "中报", "Q3": "三季报", "Q4": "年报"}


# ---------------------------------------------------------------- 工具

def dash(x, nd=2, sign=False):
    """null → —，数字格式化；sign=True 时强制带 +/- 号。"""
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s


def md_table(header, rows):
    line = "| " + " | ".join(header) + " |"
    sep = "|" + "|".join(["---"] * len(header)) + "|"
    return "\n".join([line, sep] + ["| " + " | ".join(r) + " |" for r in rows])


def med(values, nd=4):
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), nd) if vals else None


def cmp_label(v, m):
    """相对中位数位置标签：高于 / 低于 / 接近（±5% 以内）。"""
    if v is None or m is None or abs(m) < 1e-12:
        return "—"
    r = (v - m) / abs(m)
    if r > NEAR_THRESHOLD:
        return "高于"
    if r < -NEAR_THRESHOLD:
        return "低于"
    return "接近"


def load_latest(pattern, target: date):
    """读取 data/ 下日期 ≤ target 的最新一份文件；无则返回 (None, None)。"""
    best, best_d = None, None
    for p in DATA_DIR.glob(pattern):
        try:
            d = date.fromisoformat(p.stem.split("_")[-1])
        except ValueError:
            continue
        if d <= target and (best_d is None or d > best_d):
            best, best_d = p, d
    if best is None:
        return None, None
    return json.loads(best.read_text(encoding="utf-8")), best_d


def pctile_text(v):
    return f"P{v:.0f}" if v is not None else "—"


# ---------------------------------------------------------------- 一、关键指标仪表盘

def render_dashboard(stock: dict, market: dict, bank_fund: dict | None,
                     enrich: dict) -> str:
    ind = (bank_fund or {}).get("indicators") or {}
    w52 = enrich.get("w52") or {}
    cn10y = market.get("rates", {}).get("cn10y")
    dy = stock.get("div_yield_ttm")
    spread = round(dy - cn10y, 2) if (dy is not None and cn10y is not None) else None
    rows = [
        ["A股收盘（元）", dash(stock.get("a_close")) + f"（周 {dash(stock.get('a_wtd_pct'), sign=True)}%）"],
        ["H股收盘（港元）", (dash(stock.get("h_close")) +
                            f"（周 {dash(stock.get('h_wtd_pct'), sign=True)}%）")
         if stock.get("h_close") is not None else "无 H 股"],
        ["AH 溢价%", dash(stock.get("ah_premium_pct"), sign=True)],
        ["PB", f"{dash(stock.get('pb'))}（近5年分位 {pctile_text(enrich.get('pb_pctile'))}）"],
        ["PE-TTM（倍）", dash(stock.get("pe_ttm"))],
        ["股息率 TTM%", f"{dash(dy)}（近5年分位 {pctile_text(enrich.get('dy_pctile'))}）"],
        ["股息率−10Y 利差 pct", dash(spread, sign=True)],
        ["52 周位置", (f"{w52['pos_pct']:.0f}%（区间 {dash(w52.get('w52_low'))}–"
                       f"{dash(w52.get('w52_high'))}，距高点 {dash(w52.get('drawdown_pct'), sign=True)}%）")
         if w52.get("pos_pct") is not None else "—"],
        ["ROE 年化%（累计年化）", dash(enrich.get("roe_annual"))],
        ["PB-ROE 偏离%", dash(enrich.get("pb_roe_dev"), nd=1, sign=True)],
        ["净息差% / 净利差%", f"{dash(ind.get('nim_pct'))} / {dash(ind.get('nim_spread_pct'))}"],
        ["不良率% / 逾期率%", f"{dash(ind.get('npl_ratio_pct'))} / {dash(ind.get('overdue_ratio_pct'))}"],
        ["拨备覆盖率%", dash(ind.get("provision_coverage_pct"), nd=0)],
        ["核心一级 / 资本充足率%", f"{dash(ind.get('cet1_pct'))} / {dash(ind.get('car_pct'))}"],
        ["分红率%（上一完整财年）", dash(ind.get("payout_ratio_pct"), nd=1)],
        ["存款 / 贷款 YoY%", f"{dash(ind.get('deposits_yoy_pct'), sign=True)} / "
                             f"{dash(ind.get('loans_yoy_pct'), sign=True)}"],
    ]
    return md_table(["指标", "数值"], rows)


# ---------------------------------------------------------------- 二、近期行情与市场表现

def render_market_perf(stock: dict, market: dict) -> str:
    idx = {i["symbol"]: i for i in market.get("indexes", [])}
    bank_idx, hs300 = idx.get("399986"), idx.get("000300")
    lines = [f"- **A股**：收盘 {dash(stock.get('a_close'))} 元，周涨跌 {dash(stock.get('a_wtd_pct'), sign=True)}%。"]
    if stock.get("h_close") is not None:
        lines.append(f"- **H股**：收盘 {dash(stock.get('h_close'))} 港元，周涨跌 {dash(stock.get('h_wtd_pct'), sign=True)}%；"
                     f"AH 溢价 {dash(stock.get('ah_premium_pct'), sign=True)}%（负值=H股折价）。")
        dual = [s["ah_premium_pct"] for s in market.get("stocks", [])
                if s.get("ah_premium_pct") is not None]
        dmed = med(dual, nd=2)
        if dmed is not None and stock.get("ah_premium_pct") is not None:
            lines.append(f"- **AH 对比**：双重上市 {len(dual)} 家 AH 溢价中位数 {dmed:+.2f}%，"
                         f"本行{cmp_label(stock['ah_premium_pct'], dmed)}中位数"
                         f"（{stock['ah_premium_pct'] - dmed:+.2f}pct）。")
    else:
        lines.append("- **H股**：无 H 股上市。")
    if bank_idx and hs300 and stock.get("a_wtd_pct") is not None:
        ex_bank = round(stock["a_wtd_pct"] - bank_idx["wtd_pct"], 2)
        ex_300 = round(stock["a_wtd_pct"] - hs300["wtd_pct"], 2)
        lines.append(f"- **相对强弱**：本周中证银行 {dash(bank_idx['wtd_pct'], sign=True)}%、"
                     f"沪深300 {dash(hs300['wtd_pct'], sign=True)}%；本行 A 股"
                     f"相对中证银行 {ex_bank:+.2f}pct、相对沪深300 {ex_300:+.2f}pct。")
    sb = market.get("southbound", {})
    if sb.get("week_net_buy_hkd_yi") is not None:
        direction = "净流入" if sb["week_net_buy_hkd_yi"] >= 0 else "净流出"
        lines.append(f"- **南向资金**（H股定价参考）：近 {len(sb.get('days', []))} 个交易日合计"
                     f"{direction} {abs(sb['week_net_buy_hkd_yi']):.2f} 亿港元。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 三、估值分析

def render_valuation(stock: dict, market: dict, enrich: dict) -> str:
    stocks = market.get("stocks", [])
    segment = stock.get("segment")
    peers = [s for s in stocks if s.get("segment") == segment]
    pool_pb = med([s.get("pb") for s in stocks], nd=4)
    pool_pe = med([s.get("pe_ttm") for s in stocks], nd=4)
    pool_div = med([s.get("div_yield_ttm") for s in stocks], nd=4)
    seg_pb = med([s.get("pb") for s in peers], nd=4)
    seg_pe = med([s.get("pe_ttm") for s in peers], nd=4)
    seg_div = med([s.get("div_yield_ttm") for s in peers], nd=4)

    def row(label, key, pmed, smed, nd=2):
        v = stock.get(key)
        return [label, dash(v, nd), dash(pmed, nd), dash(smed, nd),
                f"{cmp_label(v, pmed)}全池 / {cmp_label(v, smed)}同板块"]

    rows = [row("PB", "pb", pool_pb, seg_pb),
            row("PE-TTM（倍）", "pe_ttm", pool_pe, seg_pe),
            row("股息率TTM（%）", "div_yield_ttm", pool_div, seg_div)]
    parts = [md_table(["指标", "本行", "全池中位数(12家)", f"同板块中位数({segment})"], rows)]

    w52 = enrich.get("w52") or {}
    bits = []
    if enrich.get("pb_pctile") is not None:
        bits.append(f"PB 近 5 年分位 P{enrich['pb_pctile']:.0f}")
    if enrich.get("dy_pctile") is not None:
        bits.append(f"股息率近 5 年分位 P{enrich['dy_pctile']:.0f}")
    if w52.get("pos_pct") is not None:
        bits.append(f"52 周位置 {w52['pos_pct']:.0f}%（距高点 {dash(w52.get('drawdown_pct'), sign=True)}%）")
    if enrich.get("pb_roe_dev") is not None:
        dev = enrich["pb_roe_dev"]
        bits.append(f"PB-ROE 偏离 {dev:+.1f}%（{'相对低估' if dev < -20 else ('相对高估' if dev > 20 else '区间内')}）")
    if bits:
        parts.append("\n**历史位置**：" + "；".join(bits) + "。")

    cn10y = market.get("rates", {}).get("cn10y")
    if stock.get("div_yield_ttm") is not None and cn10y is not None:
        spread = round(stock["div_yield_ttm"] - cn10y, 2)
        parts.append(f"\n**股息率−10Y国债利差**：{spread:+.2f}pct"
                     f"（股息率 {stock['div_yield_ttm']:.2f}% − 10Y 国债 {cn10y:.2f}%）。")
    else:
        parts.append("\n**股息率−10Y国债利差**：—（股息率或 10Y 国债数据缺失）。")
    seg_pbs = [(s["name"], s["pb"]) for s in peers if s.get("pb") is not None]
    if seg_pbs:
        lo, hi = min(seg_pbs, key=lambda x: x[1]), max(seg_pbs, key=lambda x: x[1])
        parts.append(f"\n> 板块定位：{segment}共 {len(peers)} 家，PB 区间 "
                     f"{lo[1]:.2f}（{lo[0]}）~ {hi[1]:.2f}（{hi[0]}）。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 四、基本面分析

def render_fundamentals(bank_fund: dict | None) -> str:
    if not bank_fund:
        return "（fundamentals 快照中无该银行记录）"
    analysis = bank_fund.get("analysis", {})
    periods = analysis.get("periods", [])
    ind = bank_fund.get("indicators") or {}
    parts = []
    latest = analysis.get("latest") or {}
    if latest:
        parts.append(f"**最新报告期 {latest.get('period', '—')}**：营收 "
                     f"{dash(periods[0].get('revenue_yi') if periods else None)} 亿"
                     f"（YoY {dash(latest.get('revenue_yoy_pct'), sign=True)}%），净利 "
                     f"{dash(periods[0].get('profit_yi') if periods else None)} 亿"
                     f"（YoY {dash(latest.get('profit_yoy_pct'), sign=True)}%），"
                     f"累计 ROE {dash(latest.get('roe_pct'))}%。")
    if periods:
        # EPS/BVPS 按报告期并入趋势表（F10 history 的 period 为「2026中报」式命名）
        per_q = {}
        for h in ind.get("history", []):
            pname = str(h.get("period") or "")
            for q, suffix in Q_TO_F10.items():
                if pname.endswith(suffix):
                    per_q[f"{pname[:4]}{q}"] = h
        rows = []
        for i, p in enumerate(periods):
            label = f"**{p['period']}**（最新）" if i == 0 else p["period"]
            h = per_q.get(p["period"]) or {}
            rows.append([label, dash(p.get("revenue_yi")), dash(p.get("revenue_yoy_pct"), sign=True),
                         dash(p.get("profit_yi")), dash(p.get("profit_yoy_pct"), sign=True),
                         dash(p.get("roe_pct")), dash(h.get("eps")), dash(h.get("bps"))])
        parts.append(md_table(["报告期", "营收(亿)", "营收YoY%", "净利(亿)", "净利YoY%",
                               "ROE%(累计)", "EPS(元)", "BVPS(元)"], rows))
    else:
        parts.append("（无可用报告期）")

    ind_labels = [("nim_pct", "净息差%"), ("nim_spread_pct", "净利差%"),
                  ("npl_ratio_pct", "不良率%"), ("overdue_ratio_pct", "逾期率%"),
                  ("provision_coverage_pct", "拨备覆盖率%"), ("cet1_pct", "核心一级%"),
                  ("car_pct", "资本充足率%"), ("payout_ratio_pct", "分红率%")]
    parts.append("\n**专项指标（东财 F10/分红送配自动拉取）**\n")
    parts.append(md_table(["指标期"] + [lb for _, lb in ind_labels],
                          [[ind.get("period") or "—"]
                           + [dash(ind.get(k)) for k, _ in ind_labels]]))
    scale = []
    if ind.get("deposits_yi") is not None:
        scale.append(f"存款 {ind['deposits_yi']:,.0f} 亿（YoY {dash(ind.get('deposits_yoy_pct'), sign=True)}%）")
    if ind.get("gross_loans_yi") is not None:
        scale.append(f"贷款 {ind['gross_loans_yi']:,.0f} 亿（YoY {dash(ind.get('loans_yoy_pct'), sign=True)}%）")
    if scale:
        parts.append("\n**规模**：" + "；".join(scale) + "（YoY 为对上年同报告期自算）。")
    meta = (bank_fund or {}).get("indicators_meta") or {}
    if meta.get("as_of"):
        parts.append(f"\n> 自动层缓存 fin_indicators_{meta['as_of']}"
                     f"（指标期 {meta.get('period') or '—'}，分红率财年 {meta.get('payout_fy') or '—'}）。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 图表（```chart 围栏）

def chart_relative(stock: dict, target: date):
    """本行 vs 双基准归一化走势 spec：读 data/hist，近 120 交易日起点=100。"""
    entries = [(f"a{stock['a_code']}", stock["name"]),
               ("i399986", "中证银行"), ("i000300", "沪深300")]
    return relative_line_spec("本行与基准相对走势（近120交易日，起点=100）", entries,
                              target.isoformat())


def chart_earnings(bank_fund: dict | None):
    """营收/净利 YoY 近报告期分组柱状 spec（periods 最新在前 → 转时间正序）。"""
    periods = (bank_fund or {}).get("analysis", {}).get("periods", [])
    rows = [p for p in periods
            if p.get("revenue_yoy_pct") is not None or p.get("profit_yoy_pct") is not None]
    if len(rows) < 2:
        return None
    rows = rows[::-1]
    return {"type": "grouped_bar", "title": "营收/净利同比增速（%，近报告期）",
            "x": [p["period"] for p in rows],
            "series": [{"name": "营收YoY", "vals": [p.get("revenue_yoy_pct") for p in rows]},
                       {"name": "净利YoY", "vals": [p.get("profit_yoy_pct") for p in rows]}]}


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="生成单家银行个股研究报告 Markdown（指标+图表）")
    ap.add_argument("--code", required=True, help="A股代码，如 600036（可用代码见 watchlist.json）")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 output/single")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else SINGLE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    watchlist = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    stocks = watchlist.get("stocks", [])
    watch_stock = next((s for s in stocks if s.get("a_code") == args.code), None)
    if watch_stock is None:
        available = "、".join(f"{s['a_code']}({s['name']})" for s in stocks)
        raise SystemExit(f"错误：watchlist.json 中不存在 A股代码 {args.code}。可用代码：{available}")

    market, d_mkt = load_latest("market_*.json", target)
    if market is None:
        raise SystemExit(f"错误：data/ 下没有 ≤ {target} 的 market_*.json，请先运行 data_puller.py")
    mkt_stock = next((s for s in market.get("stocks", []) if s.get("a_code") == args.code), None)
    if mkt_stock is None:
        raise SystemExit(f"错误：market 快照（{d_mkt}）中无 {args.code}({watch_stock['name']}) 的行情记录")
    stock = {**watch_stock, **mkt_stock}  # 行情字段以 market 快照为准

    fund, d_fund = load_latest("fundamentals_*.json", target)
    fund = fund or {"banks": [], "meta": {}}
    data_as_of = max(d for d in (d_mkt, d_fund) if d).isoformat()

    bank_fund = next((b for b in fund.get("banks", []) if b.get("a_code") == args.code), None)

    # 估值增强（分位/52周/PB-ROE；板块中位用全池 PB/ROE 比值）
    fund_by_code = {b["a_code"]: b for b in fund.get("banks", [])}
    ratios = []
    for s in market.get("stocks", []):
        roe = metrics.annualized_roe(fund_by_code.get(s["a_code"]))
        if s.get("pb") and roe:
            ratios.append(s["pb"] / roe)
    enrich = {
        "pb_pctile": metrics.pb_pctile(args.code, target, stock.get("pb")),
        "dy_pctile": metrics.div_yield_pctile(args.code, target, stock.get("div_yield_ttm")),
        "w52": metrics.week52(args.code, target),
        "roe_annual": metrics.annualized_roe(bank_fund),
        "pb_roe_dev": metrics.pb_roe_deviation(
            stock.get("pb"), metrics.annualized_roe(bank_fund), ratios),
    }

    name = stock["name"]
    filled = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{STOCK_NAME}}": name,
        "{{A_CODE}}": args.code,
        "{{H_CODE}}": stock.get("h_code") or "无",
        "{{REPORT_DATE}}": target.isoformat(),
        "{{DATA_AS_OF}}": data_as_of,
        "{{GENERATED_AT}}": datetime.now().isoformat(timespec="seconds"),
        "{{METHOD_VERSION}}": methodology.current_version(),
        "{{DASHBOARD}}": render_dashboard(stock, market, bank_fund, enrich),
        "{{MARKET_PERFORMANCE}}": render_market_perf(stock, market),
        "{{CHART_RELATIVE}}": fence(chart_relative(stock, target)),
        "{{VALUATION}}": render_valuation(stock, market, enrich),
        "{{FUNDAMENTALS}}": render_fundamentals(bank_fund),
        "{{CHART_EARNINGS}}": fence(chart_earnings(bank_fund)),
    }
    for token, value in replacements.items():
        if token not in filled:
            raise SystemExit(f"错误：模板缺少占位符 {token}")
        filled = filled.replace(token, value)
    if "{{" in filled or "}}" in filled:
        raise SystemExit("错误：模板存在未替换的占位符")

    out_file = out_dir / f"{name}_银行个股报告_{target.strftime('%Y%m%d')}.md"
    tmp = out_file.with_suffix(".md.tmp")
    tmp.write_text(filled, encoding="utf-8")
    tmp.replace(out_file)
    print(f"已生成 {out_file}")


if __name__ == "__main__":
    sys.exit(main())
