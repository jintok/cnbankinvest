#!/usr/bin/env python3
"""单家银行个股研究报告生成器。

读取 data/ 下 ≤ --date 的最新 market_*.json / news_*.json / fundamentals_*.json，
按 watchlist.json 解析 --code（A股代码）对应标的，填充包内 templates/single_bank_template.md，
写出 output/single/{名称}_银行个股报告_{yyyymmdd}.md（tmp+replace 原子写）。
纯标准库实现（不联网、不依赖 akshare）。

用法：
    .venv/bin/python -m cnbankinvest.gen_single_report --code 600036 [--date 2026-09-25] [--out 输出目录]
"""
import argparse
import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from cnbankinvest.paths import DATA_DIR, SINGLE_DIR, WATCHLIST_PATH as WATCHLIST

TEMPLATE = Path(__file__).resolve().parent / "templates" / "single_bank_template.md"

NEAR_THRESHOLD = 0.05   # 与中位数偏离 ±5% 以内视为「接近」
CALENDAR_DAYS = 30      # 跟踪清单规则日历的展望天数


# ---------------------------------------------------------------- 工具

def dash(x, nd=2, sign=False):
    """null → —，数字格式化；sign=True 时强制带 +/- 号。"""
    if x is None:
        return "—"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


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


# ---------------------------------------------------------------- 行情

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


# ---------------------------------------------------------------- 估值

def render_valuation(stock: dict, market: dict) -> str:
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


# ---------------------------------------------------------------- 基本面

def render_fundamentals(bank_fund: dict | None, gaps: list) -> str:
    if not bank_fund:
        return "（fundamentals 快照中无该银行记录）"
    analysis = bank_fund.get("analysis", {})
    periods = analysis.get("periods", [])
    parts = []
    latest = analysis.get("latest") or {}
    if latest:
        parts.append(f"**最新报告期 {latest.get('period', '—')}**：营收 "
                     f"{dash(periods[0].get('revenue_yi') if periods else None)} 亿"
                     f"（YoY {dash(latest.get('revenue_yoy_pct'), sign=True)}%），净利 "
                     f"{dash(periods[0].get('profit_yi') if periods else None)} 亿"
                     f"（YoY {dash(latest.get('profit_yoy_pct'), sign=True)}%），"
                     f"单季 ROE {dash(latest.get('roe_pct'))}%。")
    if periods:
        rows = []
        for i, p in enumerate(periods):
            label = f"**{p['period']}**（最新）" if i == 0 else p["period"]
            rows.append([label, dash(p.get("revenue_yi")), dash(p.get("revenue_yoy_pct"), sign=True),
                         dash(p.get("profit_yi")), dash(p.get("profit_yoy_pct"), sign=True),
                         dash(p.get("roe_pct"))])
        parts.append(md_table(["报告期", "营收(亿)", "营收YoY%", "净利(亿)", "净利YoY%", "ROE%(单季)"], rows))
        note = analysis.get("trend_note") or ""
        if note:
            parts.append(f"\n> 趋势（自动判定）：{note}。")
    else:
        parts.append("（无可用报告期）")

    cur = bank_fund.get("curated", {})
    cur_labels = [("nim_pct", "净息差%"), ("npl_ratio_pct", "不良率%"),
                  ("provision_coverage_pct", "拨备覆盖率%"), ("cet1_pct", "核心一级资本充足率%"),
                  ("payout_ratio_pct", "分红率%")]

    def cv(key):
        v = cur.get(key)
        return dash(v) if v is not None else "待填"

    parts.append("\n**专项指标（自动拉取：东财 F10/分红送配；手工台账值优先）**\n")
    parts.append(md_table(["报告期(已披露)"] + [lb for _, lb in cur_labels],
                          [[cur.get("report") or "待填"] + [cv(k) for k, _ in cur_labels]]))
    auto_info = (bank_fund or {}).get("indicators_auto") or {}
    if auto_info.get("as_of"):
        parts.append(f"\n> 自动层缓存 fin_indicators_{auto_info['as_of']}"
                     f"（报告期 {auto_info.get('period') or '—'}，"
                     f"分红率财年 {auto_info.get('payout_fy') or '—'}）；"
                     f"手工台账 `bank_fundamentals.json` 非空值优先覆盖。")
    bank_gaps = [g for g in gaps if g.startswith(bank_fund.get("name", "\0"))]
    if bank_gaps:
        parts.append("\n本行台账待填：" + "；".join(bank_gaps) + "。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 公告与舆情

def render_news_notice(name: str, a_code: str, news: dict) -> str:
    parts = []
    notices = [n for n in news.get("notices", []) if n.get("code") == a_code]
    parts.append(f"**公告（本周窗口 {len(notices)} 条）**\n")
    if notices:
        by_cat = {}
        for n in notices:
            by_cat.setdefault(n.get("category", "其他"), []).append(n)
        for cat, items in by_cat.items():
            parts.append(f"*{cat}*\n")
            for n in items:
                link = f"（[链接]({n['url']})）" if n.get("url") else ""
                parts.append(f"- `{n.get('date', '')}` {n.get('title', '')}{link}")
    else:
        parts.append("（本周窗口内无该银行公告）")

    flash = [f for f in news.get("flash", [])
             if f.get("title") and name in (f.get("title", "") + f.get("summary", ""))]
    parts.append(f"\n**快讯（标题/摘要命中「{name}」{len(flash)} 条）**\n")
    if flash:
        for f in flash:
            title = f"[{f['title']}]({f['url']})" if f.get("url") else f["title"]
            parts.append(f"- `{f.get('time', '')}` 【{f.get('source', '')}】{title}"
                         f" — {f.get('summary', '')}")
    else:
        parts.append("（无命中。注意：快讯源仅覆盖最近 24–48 小时，不代表一周无相关舆情。）")
    return "\n".join(parts)


# ---------------------------------------------------------------- 观点初稿

AUTO_MARK = "（初稿·自动）【待人工修订】"


def render_short_view(stock: dict, market: dict, news: dict) -> str:
    idx = {i["symbol"]: i for i in market.get("indexes", [])}
    bank_idx, hs300 = idx.get("399986"), idx.get("000300")
    bullets = []
    if bank_idx and hs300 and stock.get("a_wtd_pct") is not None:
        ex_300 = round(stock["a_wtd_pct"] - hs300["wtd_pct"], 2)
        ex_bank = round(stock["a_wtd_pct"] - bank_idx["wtd_pct"], 2)
        pos_300 = "跑赢" if ex_300 >= 0 else "跑输"
        pos_bank = "强于" if ex_bank >= 0 else "弱于"
        bullets.append(f"- **相对强弱**：本周 A 股{pos_300}沪深300 {ex_300:+.2f}pct、"
                       f"{pos_bank}中证银行 {ex_bank:+.2f}pct"
                       f"（A股周涨跌 {stock['a_wtd_pct']:+.2f}%）{AUTO_MARK}")
    stocks = market.get("stocks", [])
    peers = [s for s in stocks if s.get("segment") == stock.get("segment")]
    v_pb, v_pe = stock.get("pb"), stock.get("pe_ttm")
    pool_pb = med([s.get("pb") for s in stocks], nd=4)
    seg_pb = med([s.get("pb") for s in peers], nd=4)
    if v_pb is not None and pool_pb is not None:
        bullets.append(f"- **估值位置**：PB {v_pb:.2f}（{cmp_label(v_pb, pool_pb)}全池中位 {pool_pb:.2f}、"
                       f"{cmp_label(v_pb, seg_pb)}同板块中位 {seg_pb:.2f}），"
                       f"PE-TTM {dash(v_pe)} 倍{AUTO_MARK}")
    cn10y = market.get("rates", {}).get("cn10y")
    if stock.get("div_yield_ttm") is not None and cn10y is not None:
        spread = round(stock["div_yield_ttm"] - cn10y, 2)
        bullets.append(f"- **股息性价比**：股息率 {stock['div_yield_ttm']:.2f}% − 10Y 国债 "
                       f"{cn10y:.2f}% = 利差 {spread:+.2f}pct{AUTO_MARK}")
    elif cn10y is not None:
        bullets.append(f"- **股息性价比**：股息率数据缺失，暂无法计算与 10Y 国债（{cn10y:.2f}%）利差{AUTO_MARK}")
    n_notices = len([n for n in news.get("notices", []) if n.get("code") == stock.get("a_code")])
    bullets.append(f"- **事件面**：本周窗口内本行公告 {n_notices} 条，快讯命中见第六节{AUTO_MARK}")
    return "\n".join(bullets) if bullets else f"（无可用信号）{AUTO_MARK}"


def render_long_view(stock: dict, bank_fund: dict | None, fund: dict) -> str:
    bullets = []
    latest = (bank_fund or {}).get("analysis", {}).get("latest") or {}
    period = latest.get("period") or "最新期"
    rev_yoy, prof_yoy = latest.get("revenue_yoy_pct"), latest.get("profit_yoy_pct")
    if rev_yoy is not None or prof_yoy is not None:
        rev_word = "正增长" if (rev_yoy or 0) >= 0 else "负增长"
        prof_word = "正增长" if (prof_yoy or 0) >= 0 else "负增长"
        bullets.append(f"- **成长中枢**：{period} 营收 YoY {dash(rev_yoy, sign=True)}%（{rev_word}）、"
                       f"净利 YoY {dash(prof_yoy, sign=True)}%（{prof_word}）{AUTO_MARK}")
    note = (bank_fund or {}).get("analysis", {}).get("trend_note") or ""
    if note:
        bullets.append(f"- **营收趋势**：{note}（自动判定，基于近 6 个报告期同比）{AUTO_MARK}")
    banks = fund.get("banks", [])
    roes = [b.get("analysis", {}).get("latest", {}).get("roe_pct") for b in banks]
    roes = [r for r in roes if r is not None]
    roe = latest.get("roe_pct")
    if roes and roe is not None:
        pos = "上部" if roe >= med(roes, nd=2) else "下部"
        bullets.append(f"- **盈利能力**：单季 ROE {roe:.2f}%，处于核心池 ROE 区间 "
                       f"{min(roes):.2f}%–{max(roes):.2f}% 的{pos}{AUTO_MARK}")
    curated = (bank_fund or {}).get("curated", {})
    null_fields = [k for k, v in curated.items() if v is None]
    if null_fields:
        bullets.append(f"- **台账缺口**：净息差/不良率/拨备覆盖率/核心一级/分红率等待填项 "
                       f"（{len(null_fields)} 项），补齐后方可评估息差与资产质量{AUTO_MARK}")
    seg = stock.get("segment")
    dual = "AH 双重上市" if stock.get("h_close") is not None else "仅 A 股上市"
    bullets.append(f"- **板块定位**：{seg}，{dual}；长期逻辑需结合公司概况（第二节人工撰写）展开{AUTO_MARK}")
    return "\n".join(bullets) if bullets else f"（无可用信号）{AUTO_MARK}"


# ---------------------------------------------------------------- 跟踪清单

def render_tracking(target: date, name: str, gaps: list) -> str:
    parts = []
    bank_gaps = [g for g in gaps if g.startswith(name)]
    if bank_gaps:
        parts.append("**台账待填（curated_gaps 中本行条目）**\n")
        for g in bank_gaps:
            parts.append(f"- [ ] {g}")
    else:
        parts.append("**台账待填**：无（本行 curated 字段已补齐）。")

    window = [target + timedelta(days=i) for i in range(1, CALENDAR_DAYS + 1)]
    rules = []
    if any(d.day == 20 for d in window):
        rules.append("- **LPR 报价**（每月 20 日，遇节假日顺延）：关注 1Y/5Y 以上品种是否调整，"
                     "影响息差预期。")
    if any(d.day >= 28 for d in window):
        rules.append("- **月末窗口**：PMI（下月 1 日公布）与金融监管总局季度监管指标发布窗口，"
                     "留意社融/信贷数据。")
    if any(d.month == 10 for d in window):
        rules.append("- **三季报披露期**：关注本行及同板块业绩披露节奏与业绩预告。")
    if any(d.month == 8 for d in window):
        rules.append("- **中报披露收尾**：核对中报净息差、不良率、拨备覆盖率并回填台账。")
    if any(d.month == 4 for d in window):
        rules.append("- **年报/一季报披露期**：年度分红方案窗口，核对分红率并回填台账。")
    if any(d.month == 1 for d in window):
        rules.append("- **年报预告窗口**：关注业绩快报/预告与年度分红预案。")
    parts.append(f"\n**规则日历（未来 {CALENDAR_DAYS} 天）**\n")
    parts.extend(rules) if rules else parts.append("（未来窗口内无规则命中事项）")
    return "\n".join(parts)


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="生成单家银行个股研究报告 Markdown")
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

    news, d_news = load_latest("news_*.json", target)
    fund, d_fund = load_latest("fundamentals_*.json", target)
    news = news or {"flash": [], "notices": [], "meta": {}}
    fund = fund or {"banks": [], "meta": {"curated_gaps": []}}
    data_as_of = max(d for d in (d_mkt, d_news, d_fund) if d).isoformat()

    bank_fund = next((b for b in fund.get("banks", []) if b.get("a_code") == args.code), None)
    gaps = fund.get("meta", {}).get("curated_gaps", [])
    name = stock["name"]

    filled = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{STOCK_NAME}}": name,
        "{{A_CODE}}": args.code,
        "{{H_CODE}}": stock.get("h_code") or "无",
        "{{REPORT_DATE}}": target.isoformat(),
        "{{DATA_AS_OF}}": data_as_of,
        "{{GENERATED_AT}}": datetime.now().isoformat(timespec="seconds"),
        "{{SHORT_VIEW}}": render_short_view(stock, market, news),
        "{{LONG_VIEW}}": render_long_view(stock, bank_fund, fund),
        "{{MARKET_PERFORMANCE}}": render_market_perf(stock, market),
        "{{VALUATION}}": render_valuation(stock, market),
        "{{FUNDAMENTALS}}": render_fundamentals(bank_fund, gaps),
        "{{NEWS_NOTICE}}": render_news_notice(name, args.code, news),
        "{{TRACKING_LIST}}": render_tracking(target, name, gaps),
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

    bank_gaps = [g for g in gaps if g.startswith(name)]
    print(f"已生成 {out_file}")
    print("人工待办清单：")
    print("  [待修订] 一、短期观点（初稿已给）")
    print("  [待修订] 一、长期观点（初稿已给）")
    print("  [待撰写] 二、公司概况")
    print("  [待撰写] 四、估值分析（历史分位与合理区间）")
    print("  [待撰写] 五、基本面分析（定期报告点评）")
    print("  [待撰写] 七、催化剂与风险提示")
    print(f"  [台账]   本行待填 {len(bank_gaps)} 项（见八、跟踪清单）")


if __name__ == "__main__":
    sys.exit(main())
