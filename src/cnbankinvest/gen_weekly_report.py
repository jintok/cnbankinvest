#!/usr/bin/env python3
"""银行板块周报生成器（纯自动「指标 + 图表」仪表盘，方法论 v20261003 结构）。

读取 data/ 下 ≤ --date 的最新 market_*.json / fundamentals_*.json，
填充包内 templates/weekly_template.md，写出 output/weekly/bank_weekly_YYYY-MM-DD.md（tmp+replace 原子写）。
纯标准库实现（不联网、不依赖 akshare）；估值分位/52 周/相关性等经 cnbankinvest.metrics
读 data/valuation_hist/ 与 data/hist/ 缓存离线计算。

每次运行同步维护 data/spread_history.json（核心池股息率中位数 − 10Y国债 利差序列），
同日期重复运行会覆盖当日记录，保证幂等。

用法：
    .venv/bin/python -m cnbankinvest.gen_weekly_report [--date 2026-09-25] [--out weekly目录]
"""
import argparse
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

from cnbankinvest import methodology, metrics
from cnbankinvest.charts import fence, relative_line_spec
from cnbankinvest.paths import DATA_DIR, WEEKLY_DIR

TEMPLATE = Path(__file__).resolve().parent / "templates" / "weekly_template.md"
SPREAD_HISTORY = DATA_DIR / "spread_history.json"
SPREAD_MIN_SAMPLES = 20  # 利差历史分位数最少样本数


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


def load_latest(pattern, target: date):
    """读取 data/ 下日期 ≤ target 的最新一份文件；无则返回 None。"""
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


def med(values, nd=2):
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), nd) if vals else None


# ---------------------------------------------------------------- A/H 因子

def hist_close(key: str, d: date):
    """读 data/hist/{key}.json 缓存，取 <= d 的最后收盘；无缓存返回 None。"""
    p = DATA_DIR / "hist" / f"{key}.json"
    if not p.exists():
        return None
    try:
        rows = {r[0]: r[1] for r in json.loads(p.read_text(encoding="utf-8")).get("rows", [])}
    except Exception:  # noqa: BLE001
        return None
    keys = [k for k in rows if k <= d.isoformat()]
    return rows[max(keys)] if keys else None


def ah_premium_stats(market: dict, target: date) -> dict:
    """A+H 银行 AH 溢价率汇总：当前均值/中位数 + 环比（上周同口径）。
    环比需 fx 与 hist 缓存齐全，否则 wow=None（报告中写「环比待补」）。"""
    duals = [s for s in market["stocks"] if s.get("h_code")]
    cur = [s["ah_premium_pct"] for s in duals if s.get("ah_premium_pct") is not None]
    if not cur:
        return {"n": 0, "mean": None, "median": None, "wow": None}
    mean = round(sum(cur) / len(cur), 2)
    median = round(statistics.median(cur), 2)
    wow = None
    fx = market.get("fx", {}).get("cny_per_hkd")
    if fx:
        prev_d = target - timedelta(days=7)
        prevs = []
        for s in duals:
            if s.get("ah_premium_pct") is None:
                continue
            a = hist_close(f"a{s['a_code']}", prev_d)
            h = hist_close(f"h{s['h_code']}", prev_d)
            if a and h:
                prevs.append((h * fx - a) / a * 100)
        if len(prevs) == len(cur):  # 仅当全部 A+H 家数都拿到上周口径才算环比
            wow = round(mean - sum(prevs) / len(prevs), 2)
    return {"n": len(cur), "mean": mean, "median": median, "wow": wow}


# ---------------------------------------------------------------- 估值增强（分位/52周/PB-ROE）

def enrich_stocks(market: dict, fund: dict, target: date) -> dict:
    """逐股计算估值增强指标：PB 分位、股息率分位、52 周位置、ROE 年化、PB-ROE 偏离。
    返回 {a_code: {...}}；数据缺失时各字段为 None。"""
    fund_by_code = {b["a_code"]: b for b in fund.get("banks", [])}
    roe_map = {c: metrics.annualized_roe(b) for c, b in fund_by_code.items()}
    ratios = []
    for s in market["stocks"]:
        roe = roe_map.get(s["a_code"])
        if s.get("pb") and roe:
            ratios.append(s["pb"] / roe)
    out = {}
    for s in market["stocks"]:
        code = s["a_code"]
        roe = roe_map.get(code)
        out[code] = {
            "pb_pctile": metrics.pb_pctile(code, target, s.get("pb")),
            "dy_pctile": metrics.div_yield_pctile(code, target, s.get("div_yield_ttm")),
            "w52": metrics.week52(code, target),
            "roe_annual": roe,
            "pb_roe_dev": metrics.pb_roe_deviation(s.get("pb"), roe, ratios),
        }
    return out


# ---------------------------------------------------------------- 一、一周速览

def render_index_table(market: dict) -> str:
    rows = [[i["name"], dash(i["close"]),
             dash(i["wtd_pct"], sign=True), dash(i["ytd_pct"], sign=True),
             dash(i.get("index_div_yield"))]
            for i in market["indexes"]]
    return md_table(["名称", "收盘", "周涨跌%", "年初至今%", "股息率%"], rows)


def render_dashboard(market: dict, ah_stats: dict, spread, spread_note: str,
                     enrich: dict) -> str:
    parts = [render_index_table(market), ""]
    r = market["rates"]
    sb = market["southbound"]
    bits = []
    if sb.get("week_net_buy_hkd_yi") is not None:
        direction = "净流入" if sb["week_net_buy_hkd_yi"] >= 0 else "净流出"
        bits.append(f"南向资金周{direction} {abs(sb['week_net_buy_hkd_yi']):.2f} 亿港元")
    if r.get("cn10y") is not None:
        s = f"10Y 国债 {r['cn10y']:.2f}%"
        if r.get("cn10y_prev_week") is not None:
            s += f"（周变动 {(r['cn10y'] - r['cn10y_prev_week']) * 100:+.1f}bp）"
        bits.append(s)
    if r.get("shibor_1w") is not None:
        bits.append(f"SHIBOR 1W {r['shibor_1w']:.2f}%")
    if r.get("lpr_1y") is not None:
        chg = f"（1Y 变动 {r['lpr_1y_change_bp']:+.0f}bp）" \
            if r.get("lpr_1y_change_bp") else ""
        bits.append(f"LPR 1Y/5Y {r['lpr_1y']:.2f}%/{dash(r.get('lpr_5y'))}%{chg}")
    if r.get("credit_spread_3y") is not None:
        bits.append(f"商业银行债 AAA 3Y 信用利差 {r['credit_spread_3y'] * 100:+.1f}bp")
    if ah_stats.get("mean") is not None:
        s = f"AH 溢价均值 {ah_stats['mean']:.2f}%"
        if ah_stats.get("wow") is not None:
            s += f"（环比 {ah_stats['wow']:+.2f}pct）"
        bits.append(s)
    if spread is not None:
        bits.append(f"核心池股息率中位数−10Y 利差 {spread:+.2f}pct（{spread_note}）")
    parts.append("**速览**：" + "；".join(bits) + "。" if bits else "（本周无可用速览数据）")

    pbs = [s["pb"] for s in market["stocks"] if s.get("pb") is not None]
    pb_pcts = [e["pb_pctile"] for e in enrich.values() if e["pb_pctile"] is not None]
    divs = [s["div_yield_ttm"] for s in market["stocks"] if s.get("div_yield_ttm") is not None]
    level = []
    if pbs:
        s = f"板块 PB 中位数 {med(pbs):.2f}"
        if pb_pcts:
            s += f"（近 5 年分位中位 P{med(pb_pcts, 0):.0f}）"
        level.append(s)
    if divs:
        level.append(f"股息率中位数 {med(divs):.2f}%")
    if level:
        parts.append("\n**估值水位**：" + "；".join(level) + "（明细见第四节）。")
    parts.append("\n> AH 溢价负值 = H 股折价；利差 = 12 家股息率中位数 − 10Y 国债；"
                 "指数股息率为中证官网股息率2 口径（仅当前值）。明细见附1、第四节。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 二、本周变化

def render_wow(market: dict, ah_stats: dict) -> str:
    parts = []
    ranked = sorted((s for s in market["stocks"] if s.get("a_wtd_pct") is not None),
                    key=lambda s: s["a_wtd_pct"], reverse=True)
    if ranked:
        top = "、".join(f"{s['name']} {s['a_wtd_pct']:+.2f}%" for s in ranked[:3])
        bot = "、".join(f"{s['name']} {s['a_wtd_pct']:+.2f}%" for s in ranked[-3:])
        parts.append(f"- **A股周涨跌**：前 3 → {top}；后 3 → {bot}。")
    sb = market["southbound"]
    if sb.get("week_net_buy_hkd_yi") is not None:
        direction = "净流入" if sb["week_net_buy_hkd_yi"] >= 0 else "净流出"
        parts.append(f"- **南向资金**：{direction} {abs(sb['week_net_buy_hkd_yi']):.2f} 亿港元，"
                     f"H 股资金面边际{'改善' if sb['week_net_buy_hkd_yi'] >= 0 else '走弱'}"
                     f"（与第五节 AH 因子对照）。")
    r = market["rates"]
    if r.get("cn10y") is not None and r.get("cn10y_prev_week") is not None:
        bp = round((r["cn10y"] - r["cn10y_prev_week"]) * 100, 1)
        direction = "下行" if bp < 0 else "上行"
        parts.append(f"- **10Y 国债**：周变动 {bp:+.1f}bp（{direction}）"
                     f"{'，债券走牛利好高股息性价比' if bp < 0 else '，注意息差与分母端压力'}。")
    if ah_stats.get("wow") is not None:
        parts.append(f"- **AH 溢价**：环比 {ah_stats['wow']:+.2f}pct"
                     f"（{'折价加深' if ah_stats['wow'] < 0 else '溢价走扩'}，展开见第五节 A/H 因子）。")
    elif ah_stats.get("mean") is not None:
        parts.append("- **AH 溢价**：环比待补（缺 fx 或 hist 缓存，当前值见第五节）。")
    return "\n".join(parts) or "（本周无可用变化数据）"


# ---------------------------------------------------------------- 三、跨指数比较

def render_corr_matrix(market: dict, target: date) -> str:
    """相关性矩阵：跟踪指数两两近 1 年日收益 Pearson 相关。"""
    symbols = [(i["symbol"], i["name"]) for i in market["indexes"]]
    got = metrics.index_corr_matrix(symbols, target)
    if not got:
        return "（hist 缓存不足，相关性矩阵暂缺）"
    names, matrix = got
    rows = []
    for nm, row in zip(names, matrix):
        rows.append([nm] + [dash(v) for v in row])
    return ("**相关性矩阵**（近 1 年日收益 Pearson 相关，hist 日线缓存）\n\n"
            + md_table(["指数"] + names, rows))


def render_dividend_matrix(market: dict, enrich: dict) -> str:
    """股息率对比矩阵：12 家银行（自算 TTM + 分位 + 利差）vs 指数股息率2（当前值 + 利差）。"""
    cn10y = market["rates"].get("cn10y")
    rows = []
    for s in market["stocks"]:
        dy = s.get("div_yield_ttm")
        spread = round(dy - cn10y, 2) if (dy is not None and cn10y is not None) else None
        pctile = enrich.get(s["a_code"], {}).get("dy_pctile")
        rows.append([s["name"], "银行", dash(dy),
                     f"P{pctile:.0f}" if pctile is not None else "—",
                     dash(spread, sign=True)])
    for i in market["indexes"]:
        dy = i.get("index_div_yield")
        spread = round(dy - cn10y, 2) if (dy is not None and cn10y is not None) else None
        rows.append([i["name"], "指数", dash(dy), "—", dash(spread, sign=True)])
    return ("**股息率对比矩阵**（银行=自算 TTM 口径含近 5 年分位；"
            "指数=中证官网股息率2，仅当前值无分位；利差=股息率 − 10Y 国债）\n\n"
            + md_table(["名称", "类型", "股息率%", "股息率分位", "对10Y利差pct"], rows))


def render_cross_index(market: dict, enrich: dict, target: date) -> str:
    return render_corr_matrix(market, target) + "\n\n" + render_dividend_matrix(market, enrich)


# ---------------------------------------------------------------- 四、估值水位

def render_valuation_level(market: dict, enrich: dict) -> str:
    rows = []
    for s in market["stocks"]:
        e = enrich.get(s["a_code"], {})
        w52 = e.get("w52") or {}
        rows.append([s["name"], s["segment"],
                     dash(s.get("pb")),
                     f"P{e['pb_pctile']:.0f}" if e.get("pb_pctile") is not None else "—",
                     dash(s.get("div_yield_ttm")),
                     f"P{e['dy_pctile']:.0f}" if e.get("dy_pctile") is not None else "—",
                     f"{w52['pos_pct']:.0f}%" if w52.get("pos_pct") is not None else "—",
                     dash(w52.get("drawdown_pct"), sign=True),
                     dash(e.get("roe_annual")),
                     dash(e.get("pb_roe_dev"), nd=1, sign=True)])
    table = md_table(["名称", "板块", "PB", "PB分位", "股息率%", "股息率分位",
                      "52周位置", "距高点回撤%", "ROE年化%", "PB-ROE偏离%"], rows)
    note = ("\n\n> 分位为近 5 年日频口径（估值缓存 valuation_hist）；PB-ROE 偏离 = "
            "个股 PB/ROE ÷ 板块中位(PB/ROE) − 1，负值=相对低估；ROE 年化 = 最新期累计 ROE × 4/季度序数（同花顺累计口径）。")
    return table + note


# ---------------------------------------------------------------- 五、A/H 因子

def render_ah_factor(market: dict, ah_stats: dict) -> str:
    parts = []
    idx = {i["symbol"]: i for i in market["indexes"]}
    sel, bank = idx.get("931039"), idx.get("399986")
    if sel and sel.get("wtd_pct") is not None and bank and bank.get("wtd_pct") is not None:
        diff = round(sel["wtd_pct"] - bank["wtd_pct"], 2)
        interp = ("优选跑赢：A/H 便宜侧（多为 H 股折价侧）走强，持有便宜一侧占优"
                  if diff >= 0 else "优选跑输：A 股侧走强，AH 溢价趋于收敛")
        parts.append(f"- **优选 vs 中证银行**：本周 931039 收 {dash(sel['close'])}"
                     f"（{dash(sel['wtd_pct'], sign=True)}%），"
                     f"399986（{dash(bank['wtd_pct'], sign=True)}%），差 {diff:+.2f}pct——{interp}。")
    else:
        parts.append("- **优选 vs 中证银行**：931039 或 399986 数据缺失，本周无法比较。")
    if ah_stats.get("mean") is not None:
        wow = ah_stats.get("wow")
        wow_text = f"，环比 {wow:+.2f}pct" if wow is not None \
            else "（环比待补：本周缺 fx 或 hist 缓存）"
        parts.append(f"- **AH 溢价汇总**：{ah_stats['n']} 家 A+H 银行溢价率均值 {ah_stats['mean']:.2f}%、"
                     f"中位数 {ah_stats['median']:.2f}%（负值=H 股折价）{wow_text}。")
    sb = market.get("southbound", {})
    if sb.get("week_net_buy_hkd_yi") is not None:
        direction = "净流入" if sb["week_net_buy_hkd_yi"] >= 0 else "净流出"
        parts.append(f"- **南向资金呼应**：本周南向资金{direction} "
                     f"{abs(sb['week_net_buy_hkd_yi']):.2f} 亿港元"
                     f"（南向买入通常收敛 H 股折价、推升 AH 溢价，可与上条对照）。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 六、基本面摘要

def render_fund_summary(fund: dict) -> str:
    banks = fund.get("banks", [])
    lines = []
    rev, prof, roe, periods = [], [], [], {}
    nim, npl, cov, cet1, payout = [], [], [], [], []
    dep_yoy, loan_yoy, overdue = [], [], []
    for b in banks:
        lat = b.get("analysis", {}).get("latest") or {}
        if lat.get("revenue_yoy_pct") is not None:
            rev.append(lat["revenue_yoy_pct"])
        if lat.get("profit_yoy_pct") is not None:
            prof.append(lat["profit_yoy_pct"])
        if lat.get("roe_pct") is not None:
            roe.append(lat["roe_pct"])
        if lat.get("period"):
            periods[lat["period"]] = periods.get(lat["period"], 0) + 1
        ind = b.get("indicators") or {}
        for key, acc in (("nim_pct", nim), ("npl_ratio_pct", npl),
                         ("provision_coverage_pct", cov), ("cet1_pct", cet1),
                         ("payout_ratio_pct", payout), ("deposits_yoy_pct", dep_yoy),
                         ("loans_yoy_pct", loan_yoy), ("overdue_ratio_pct", overdue)):
            if ind.get(key) is not None:
                acc.append(ind[key])
    if periods:
        period = max(periods, key=periods.get)
        lines.append(f"- **最新报告期**：{period}（{periods[period]}/{len(banks)} 家已披露至该期）。")
    if rev:
        lines.append(f"- **营收 YoY 中位数 {med(rev):.2f}%**，净利 YoY 中位数 {med(prof):.2f}%"
                     f"（{len(banks)} 家，同花顺按报告期口径）。")
    if roe:
        lines.append(f"- **ROE（累计）区间**：{min(roe):.2f}%–{max(roe):.2f}%。")
    if nim and npl:
        lines.append(f"- **净息差中位数 {med(nim):.2f}%**；不良率区间 {min(npl):.2f}%–{max(npl):.2f}%；"
                     f"拨备覆盖率中位数 {med(cov):.0f}%；核心一级中位数 {med(cet1):.2f}%；"
                     f"分红率中位数 {med(payout):.1f}%（东财 F10 自动层）。")
    if dep_yoy or loan_yoy:
        lines.append(f"- **规模增速**：存款 YoY 中位数 {dash(med(dep_yoy), sign=True)}%、"
                     f"贷款 YoY 中位数 {dash(med(loan_yoy), sign=True)}%。")
    if overdue:
        lines.append(f"- **逾期率区间**：{min(overdue):.2f}%–{max(overdue):.2f}%"
                     f"（中位数 {med(overdue):.2f}%）。")
    return "\n".join(lines) or "（无可用基本面数据）"


# ---------------------------------------------------------------- 七、宏观与利率

def render_macro(market: dict, fund: dict) -> str:
    r = market["rates"]
    lines = []
    if r.get("cn10y") is not None:
        s = f"- **10Y 国债**：{r['cn10y']:.2f}%"
        if r.get("cn10y_prev_week") is not None:
            s += f"（周变动 {(r['cn10y'] - r['cn10y_prev_week']) * 100:+.1f}bp）"
        if r.get("cn3y") is not None:
            s += f"；3Y 国债 {r['cn3y']:.2f}%"
        lines.append(s + "。")
    if r.get("shibor_1w") is not None:
        lines.append(f"- **Shibor 1W**：{r['shibor_1w']:.2f}%。")
    if r.get("lpr_1y") is not None:
        chg1 = f"{r['lpr_1y_change_bp']:+.0f}bp" if r.get("lpr_1y_change_bp") is not None else "—"
        chg5 = f"{r['lpr_5y_change_bp']:+.0f}bp" if r.get("lpr_5y_change_bp") is not None else "—"
        lines.append(f"- **LPR**（{r.get('lpr_date') or '—'}）：1Y {r['lpr_1y']:.2f}%（变动 {chg1}）、"
                     f"5Y {dash(r.get('lpr_5y'))}%（变动 {chg5}）。")
    if r.get("credit_spread_3y") is not None:
        lines.append(f"- **信用利差**：商业银行普通债 AAA 3Y {dash(r.get('bank3y'))}% − "
                     f"国债 3Y {dash(r.get('cn3y'))}% = {r['credit_spread_3y'] * 100:+.1f}bp。")
    cm = fund.get("credit_macro") or []
    if cm:
        rows = [[c["period"], dash(c.get("tsf_yi"), nd=0), dash(c.get("new_loans_yi"), nd=0)]
                for c in cm]
        lines.append("\n**社融与信贷近 6 个月（亿元）**\n\n"
                     + md_table(["月份", "社融增量", "新增贷款"], rows))
    return "\n".join(lines) or "（无可用宏观数据）"


# ---------------------------------------------------------------- 利差历史

def update_spread_history(target: date, market: dict):
    """维护 spread_history.json：追加 {date, pool_median_div_yield, cn10y, spread}。
    同日期覆盖（幂等）。返回 (历史列表, 当前spread, 分位文字)。"""
    history = []
    if SPREAD_HISTORY.exists():
        try:
            history = json.loads(SPREAD_HISTORY.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            history = []
    divs = [s["div_yield_ttm"] for s in market["stocks"] if s.get("div_yield_ttm") is not None]
    cn10y = market["rates"].get("cn10y")
    if not divs or cn10y is None:
        return history, None, "—"
    med = round(statistics.median(divs), 2)
    spread = round(med - cn10y, 2)
    history = [h for h in history if h.get("date") != target.isoformat()]
    history.append({"date": target.isoformat(), "pool_median_div_yield": med,
                    "cn10y": cn10y, "spread": spread})
    history.sort(key=lambda h: h["date"])
    tmp = SPREAD_HISTORY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SPREAD_HISTORY)
    spreads = [h["spread"] for h in history if h.get("spread") is not None]
    if len(spreads) < SPREAD_MIN_SAMPLES:
        pct_text = f"样本积累中(n={len(spreads)})"
    else:
        pctile = sum(1 for v in spreads if v <= spread) / len(spreads) * 100
        pct_text = f"历史分位 P{pctile:.0f}（n={len(spreads)}，含本周）"
    return history, spread, pct_text


# ---------------------------------------------------------------- 图表（```chart 围栏）

def chart_index_relative(market: dict, target: date):
    """指数归一化走势 spec：读 data/hist/i*.json，近 120 交易日起点=100。"""
    entries = [(f"i{i['symbol']}", i["name"]) for i in market["indexes"]]
    return relative_line_spec("指数相对走势（近120交易日，起点=100）", entries,
                              target.isoformat())


def chart_weekly_moves(market: dict):
    items = [{"name": s["name"], "value": s["a_wtd_pct"]}
             for s in market["stocks"] if s.get("a_wtd_pct") is not None]
    if not items:
        return None
    items.sort(key=lambda it: it["value"], reverse=True)
    return {"type": "bar", "title": "核心池 A 股周涨跌幅（%）", "items": items}


def chart_ah_premium(market: dict):
    items = [{"name": s["name"], "value": s["ah_premium_pct"]}
             for s in market["stocks"] if s.get("ah_premium_pct") is not None]
    if not items:
        return None
    items.sort(key=lambda it: it["value"])
    return {"type": "bar", "title": "A/H 溢价率（%，负值=H股折价）", "items": items}


def chart_pb_pctile(market: dict, enrich: dict):
    items = [{"name": s["name"], "value": enrich[s["a_code"]]["pb_pctile"]}
             for s in market["stocks"]
             if enrich.get(s["a_code"], {}).get("pb_pctile") is not None]
    if not items:
        return None
    items.sort(key=lambda it: it["value"], reverse=True)
    return {"type": "bar", "title": "PB 近 5 年分位（%）", "items": items}


# ---------------------------------------------------------------- 附录

def render_appendix_market(market: dict, enrich: dict) -> str:
    cn10y = market["rates"].get("cn10y")
    rows = []
    for s in market["stocks"]:
        e = enrich.get(s["a_code"], {})
        w52 = e.get("w52") or {}
        spread = round(s["div_yield_ttm"] - cn10y, 2) \
            if s.get("div_yield_ttm") is not None and cn10y is not None else None
        rows.append([s["name"], s["segment"], dash(s["a_close"]),
                     dash(s["a_wtd_pct"], sign=True), dash(s["h_close"]),
                     dash(s["h_wtd_pct"], sign=True), dash(s["ah_premium_pct"], sign=True),
                     dash(s["pb"]),
                     f"P{e['pb_pctile']:.0f}" if e.get("pb_pctile") is not None else "—",
                     dash(s["pe_ttm"]),
                     dash(s["div_yield_ttm"]),
                     f"P{e['dy_pctile']:.0f}" if e.get("dy_pctile") is not None else "—",
                     f"{w52['pos_pct']:.0f}%" if w52.get("pos_pct") is not None else "—",
                     dash(spread, sign=True)])
    return md_table(["名称", "板块", "A收盘", "周%", "H收盘", "周%",
                     "AH溢价%", "PB", "PB分位", "PE-TTM", "股息率TTM%", "股息率分位",
                     "52周位置", "利差pct"], rows)


def render_appendix_ledger(fund: dict) -> str:
    banks = fund.get("banks", [])
    growth_rows, ind_rows = [], []
    for b in banks:
        lat = b.get("analysis", {}).get("latest") or {}
        ind = b.get("indicators") or {}
        growth_rows.append([b["name"], lat.get("period") or "—",
                            dash(lat.get("revenue_yoy_pct"), sign=True),
                            dash(lat.get("profit_yoy_pct"), sign=True),
                            dash(lat.get("roe_pct")),
                            dash(ind.get("eps")), dash(ind.get("bps")),
                            dash(ind.get("deposits_yi"), nd=0),
                            dash(ind.get("deposits_yoy_pct"), sign=True),
                            dash(ind.get("gross_loans_yi"), nd=0),
                            dash(ind.get("loans_yoy_pct"), sign=True)])
        ind_rows.append([b["name"], ind.get("period") or "—",
                         dash(ind.get("nim_pct")), dash(ind.get("nim_spread_pct")),
                         dash(ind.get("npl_ratio_pct")), dash(ind.get("overdue_ratio_pct")),
                         dash(ind.get("provision_coverage_pct"), nd=0),
                         dash(ind.get("cet1_pct")), dash(ind.get("car_pct")),
                         dash(ind.get("payout_ratio_pct"), nd=1)])
    t1 = "**成长与规模**\n\n" + md_table(
        ["名称", "最新期", "营收YoY%", "净利YoY%", "ROE%(累计)", "EPS(元)", "BVPS(元)",
         "存款(亿)", "存款YoY%", "贷款(亿)", "贷款YoY%"], growth_rows)
    t2 = "\n\n**专项指标（东财 F10 自动层）**\n\n" + md_table(
        ["名称", "指标期", "净息差%", "净利差%", "不良率%", "逾期率%",
         "拨备覆盖率%", "核心一级%", "资本充足率%", "分红率%"], ind_rows)
    as_of = (fund.get("meta") or {}).get("fin_indicators_as_of")
    note = (f"\n\n> 专项指标来源：东财 F10 主要指标/分红送配自动拉取（fin_indicators_{as_of}）；"
            f"分红率为上一完整财年口径；存款/贷款 YoY 为对上年同报告期自算。" if as_of else
            "\n\n> 专项指标：fin_indicators 缓存缺失（可运行 fin_indicators_puller）。")
    return t1 + t2 + note


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="生成银行板块周报 Markdown（指标+图表仪表盘）")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 output/weekly")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else WEEKLY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    market, d_mkt = load_latest("market_*.json", target)
    fund, d_fund = load_latest("fundamentals_*.json", target)
    if market is None:
        raise SystemExit(f"错误：data/ 下没有 ≤ {target} 的 market_*.json，请先运行 data_puller.py")
    fund = fund or {"banks": [], "credit_macro": [], "meta": {}}
    data_as_of = max(d for d in (d_mkt, d_fund) if d).isoformat()

    ah_stats = ah_premium_stats(market, target)
    _, spread, spread_note = update_spread_history(target, market)
    enrich = enrich_stocks(market, fund, target)

    filled = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{REPORT_DATE}}": target.isoformat(),
        "{{DATA_AS_OF}}": data_as_of,
        "{{GENERATED_AT}}": datetime.now().isoformat(timespec="seconds"),
        "{{METHOD_VERSION}}": methodology.current_version(),
        "{{DASHBOARD}}": render_dashboard(market, ah_stats, spread, spread_note, enrich),
        "{{WOW_CHANGES}}": render_wow(market, ah_stats),
        "{{CROSS_INDEX}}": render_cross_index(market, enrich, target),
        "{{VALUATION_LEVEL}}": render_valuation_level(market, enrich),
        "{{AH_FACTOR}}": render_ah_factor(market, ah_stats),
        "{{FUND_SUMMARY}}": render_fund_summary(fund),
        "{{MACRO_RATES}}": render_macro(market, fund),
        "{{CHART_INDEX}}": fence(chart_index_relative(market, target)),
        "{{CHART_MOVES}}": fence(chart_weekly_moves(market)),
        "{{CHART_AH}}": fence(chart_ah_premium(market)),
        "{{CHART_PB_PCTILE}}": fence(chart_pb_pctile(market, enrich)),
        "{{APPENDIX_MARKET}}": render_appendix_market(market, enrich),
        "{{APPENDIX_LEDGER}}": render_appendix_ledger(fund),
    }
    for token, value in replacements.items():
        if token not in filled:
            raise SystemExit(f"错误：模板缺少占位符 {token}")
        filled = filled.replace(token, value)
    leftover = [t for t in ("{{", "}}") if t in filled]
    if leftover:
        raise SystemExit("错误：模板存在未替换的占位符")

    out_file = out_dir / f"bank_weekly_{target.isoformat()}.md"
    tmp = out_file.with_suffix(".md.tmp")
    tmp.write_text(filled, encoding="utf-8")
    tmp.replace(out_file)
    print(f"已生成 {out_file}")


if __name__ == "__main__":
    main()
