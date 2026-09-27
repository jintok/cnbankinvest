#!/usr/bin/env python3
"""银行板块周报生成器（正文 3 分钟决策摘要 + 附录明细 结构）。

读取 data/ 下 ≤ --date 的最新 market_*.json / news_*.json / fundamentals_*.json，
填充包内 templates/weekly_template.md，写出 output/weekly/bank_weekly_YYYY-MM-DD.md（tmp+replace 原子写）。
纯标准库实现（不联网、不依赖 akshare）。

结构约定：正文（一~九节）是决策摘要层，全部个股明细在文末附录（附1~附4）。
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

from cnbankinvest.charts import fence, relative_line_spec
from cnbankinvest.paths import DATA_DIR, WEEKLY_DIR

TEMPLATE = Path(__file__).resolve().parent / "templates" / "weekly_template.md"
SPREAD_HISTORY = DATA_DIR / "spread_history.json"
SPREAD_MIN_SAMPLES = 20  # 利差历史分位数最少样本数

INDEX_CHART_SERIES = [("399986", "中证银行"), ("000300", "沪深300"),
                      ("000922", "中证红利"), ("931039", "银行AH优选")]


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


# ---------------------------------------------------------------- 信号（复用层）

def build_signals(market: dict, ah_stats: dict) -> list:
    """自动信号清单（纯数据计算，null 安全）。供一周速览/观点初稿复用。"""
    signals = []
    idx = {i["symbol"]: i for i in market["indexes"]}
    bank, hs300 = idx.get("399986"), idx.get("000300")
    if bank and hs300 and bank["wtd_pct"] is not None and hs300["wtd_pct"] is not None:
        excess = round(bank["wtd_pct"] - hs300["wtd_pct"], 2)
        signals.append(f"**板块超额**：中证银行本周 {dash(bank['wtd_pct'], sign=True)}%，"
                       f"沪深300 {dash(hs300['wtd_pct'], sign=True)}%，"
                       f"周超额 {excess:+.2f}pct。")
    idx931 = idx.get("931039")
    if idx931 and bank and idx931["wtd_pct"] is not None and bank["wtd_pct"] is not None:
        diff = round(idx931["wtd_pct"] - bank["wtd_pct"], 2)
        signals.append(f"**AH优选超额**：银行AH优选(931039) 本周 {dash(idx931['wtd_pct'], sign=True)}%，"
                       f"较中证银行 {diff:+.2f}pct。")
    sb = market["southbound"]
    if sb.get("week_net_buy_hkd_yi") is not None:
        direction = "净流入" if sb["week_net_buy_hkd_yi"] >= 0 else "净流出"
        signals.append(f"**南向资金**：近 {len(sb.get('days', []))} 个交易日合计{direction} "
                       f"{abs(sb['week_net_buy_hkd_yi']):.2f} 亿港元。")
    r = market["rates"]
    if r.get("cn10y") is not None:
        if r.get("cn10y_prev_week") is not None:
            bp = round((r["cn10y"] - r["cn10y_prev_week"]) * 100, 1)
            signals.append(f"**无风险利率**：10Y 国债 {r['cn10y']:.2f}%，周变动 {bp:+.1f}bp"
                           f"（Shibor 1W {dash(r.get('shibor_1w'))}%）。")
        else:
            signals.append(f"**无风险利率**：10Y 国债 {r['cn10y']:.2f}%"
                           f"（Shibor 1W {dash(r.get('shibor_1w'))}%）。")
    ah = [(s["name"], s["ah_premium_pct"]) for s in market["stocks"]
          if s.get("ah_premium_pct") is not None]
    if ah:
        hi = max(ah, key=lambda x: x[1])
        lo = min(ah, key=lambda x: x[1])
        signals.append(f"**AH 溢价极值**：最高 {hi[0]} {hi[1]:+.2f}%，"
                       f"最低 {lo[0]} {lo[1]:+.2f}%（负值=H股折价）。")
    if ah_stats.get("mean") is not None:
        wow = ah_stats.get("wow")
        wow_text = f"，环比 {wow:+.2f}pct" if wow is not None else "（环比待补：缺 fx 或 hist 缓存）"
        signals.append(f"**AH溢价均值**：{ah_stats['n']} 家 A+H 银行溢价率均值 {ah_stats['mean']:.2f}%、"
                       f"中位数 {ah_stats['median']:.2f}%（负值=H股折价）{wow_text}。")
    cn10y = r.get("cn10y")
    if cn10y is not None:
        spreads = [(s["name"], round(s["div_yield_ttm"] - cn10y, 2))
                   for s in market["stocks"] if s.get("div_yield_ttm") is not None]
        if spreads:
            shi = max(spreads, key=lambda x: x[1])
            slo = min(spreads, key=lambda x: x[1])
            signals.append(f"**股息率−10Y利差**：最高 {shi[0]} {shi[1]:+.2f}pct，"
                           f"最低 {slo[0]} {slo[1]:+.2f}pct（负值=股息率低于10Y国债）。")
    seg_pb = {}
    for s in market["stocks"]:
        if s.get("pb") is not None:
            seg_pb.setdefault(s["segment"], []).append((s["name"], s["pb"]))
    if seg_pb:
        bits = [f"{seg} {min(v, key=lambda x: x[1])[1]:.2f}–{max(v, key=lambda x: x[1])[1]:.2f}"
                f"（{min(v, key=lambda x: x[1])[0]} ~ {max(v, key=lambda x: x[1])[0]}）"
                for seg, v in seg_pb.items()]
        signals.append("**板块 PB 区间**：" + "；".join(bits) + "。")
    return signals


def pick_signal(signals: list, prefix: str) -> str:
    """按加粗前缀从信号清单取一条（如 '**板块超额**'），无则返回空串。"""
    for s in signals:
        if s.startswith(prefix):
            return s
    return ""


# ---------------------------------------------------------------- 基本面统计（复用层）

def fund_stats(fund: dict) -> dict:
    """基本面汇总统计：中位数/ROE 区间/趋势计数/台账完整度。"""
    banks = fund.get("banks", [])
    out = {"n": len(banks), "period": None, "period_n": 0,
           "rev_med": None, "prof_med": None, "roe_min": None, "roe_max": None,
           "trend_up": 0, "trend_down": 0, "trend_flat": 0,
           "curated_report_filled": 0, "reg_filled": 0, "reg_total": 6}
    rev, prof, roe, periods = [], [], [], {}
    for b in banks:
        lat = b.get("analysis", {}).get("latest", {})
        if lat.get("revenue_yoy_pct") is not None:
            rev.append(lat["revenue_yoy_pct"])
        if lat.get("profit_yoy_pct") is not None:
            prof.append(lat["profit_yoy_pct"])
        if lat.get("roe_pct") is not None:
            roe.append(lat["roe_pct"])
        if lat.get("period"):
            periods[lat["period"]] = periods.get(lat["period"], 0) + 1
        if "上行" in b.get("analysis", {}).get("trend_note", ""):
            out["trend_up"] += 1
        elif "下行" in b.get("analysis", {}).get("trend_note", ""):
            out["trend_down"] += 1
        else:
            out["trend_flat"] += 1
        if b.get("curated", {}).get("report"):
            out["curated_report_filled"] += 1
    if periods:
        out["period"] = max(periods, key=periods.get)   # 众数
        out["period_n"] = periods[out["period"]]
    if rev:
        out["rev_med"] = round(statistics.median(rev), 2)
    if prof:
        out["prof_med"] = round(statistics.median(prof), 2)
    if roe:
        out["roe_min"], out["roe_max"] = min(roe), max(roe)
    ind = fund.get("industry_regulatory", {}) or {}
    latest = ind.get("latest")
    if isinstance(latest, dict):
        out["reg_filled"] = sum(1 for v in latest.values() if v is not None)
    else:
        # 台账未合并时读手工文件口径：items[0] 六字段非空计数
        items = ind.get("items") or []
        if items and isinstance(items[0], dict):
            keys = ("nim_pct", "npl_ratio_pct", "provision_coverage_pct",
                    "car_pct", "profit_yoy_pct", "tsf_note")
            out["reg_filled"] = sum(1 for k in keys if items[0].get(k) is not None)
    return out


# ---------------------------------------------------------------- 二、一周速览

def render_index_table(market: dict) -> str:
    rows = [[i["name"], dash(i["close"]),
             dash(i["wtd_pct"], sign=True), dash(i["ytd_pct"], sign=True)]
            for i in market["indexes"]]
    return md_table(["名称", "收盘", "周涨跌%", "年初至今%"], rows)


def render_dashboard(market: dict, ah_stats: dict, spread, spread_note: str) -> str:
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
    if ah_stats.get("mean") is not None:
        s = f"AH 溢价均值 {ah_stats['mean']:.2f}%"
        if ah_stats.get("wow") is not None:
            s += f"（环比 {ah_stats['wow']:+.2f}pct）"
        bits.append(s)
    if spread is not None:
        bits.append(f"核心池股息率中位数−10Y 利差 {spread:+.2f}pct（{spread_note}）")
    parts.append("**速览**：" + "；".join(bits) + "。" if bits else "（本周无可用速览数据）")
    parts.append("\n> AH 溢价负值 = H 股折价；利差 = 12 家股息率中位数 − 10Y 国债，明细见附1、第五节。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 三、本周变化

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


# ---------------------------------------------------------------- 四、观点初稿

def render_view_drafts(market: dict, news: dict, fund: dict, signals: list) -> str:
    parts = ["**短期观点（1–4 周）**（初稿·自动）\n"]
    shorts = [pick_signal(signals, "**板块超额**"),
              pick_signal(signals, "**南向资金**"),
              pick_signal(signals, "**无风险利率**")]
    cat_names = {"业绩快报": "业绩", "分红派息": "分红", "增减持": "增减持",
                 "再融资": "再融资", "监管处罚": "监管", "人事变动": "人事"}
    counts = {}
    for n in news.get("notices", []):
        k = cat_names.get(n["category"], n["category"])
        counts[k] = counts.get(k, 0) + 1
    if counts:
        desc = "、".join(f"{k} {v} 条" for k, v in counts.items())
        shorts.append(f"**事件面**：本周 watchlist 公告 {len(news['notices'])} 条（{desc}）；"
                      f"银行相关快讯 {len(news.get('flash', []))} 条（头条见第六节，明细见附3）。")
    parts += [f"- {s}" for s in shorts if s] or ["- （本周无可用信号）"]

    parts.append("\n**长期观点（6–24 个月）**（初稿·自动）\n")
    st = fund_stats(fund)
    longs = []
    if st["rev_med"] is not None:
        longs.append(f"**成长中枢**：核心池 {st['n']} 家最新期营收 YoY 中位数 {st['rev_med']:.2f}%、"
                     f"净利 YoY 中位数 {st['prof_med']:.2f}%。")
    if st["roe_min"] is not None:
        longs.append(f"**盈利能力**：单季 ROE 区间 {st['roe_min']:.2f}%–{st['roe_max']:.2f}%。")
    if st["trend_up"] or st["trend_down"]:
        longs.append(f"**营收趋势**：连续上行 {st['trend_up']} 家、连续下行 {st['trend_down']} 家、"
                     f"波动 {st['trend_flat']} 家（口径见附2 trend_note）。")
    if st["curated_report_filled"] < st["n"]:
        longs.append("**台账待填报**：净息差/不良率/拨备覆盖率/核心一级资本充足率/分红率等 curated 字段"
                     "当前为空，需人工从金融监管总局季度通报与银行定期报告填报"
                     "（data/bank_fundamentals.json），填报后本节与第七节自动补全。")
    parts += [f"- {s}" for s in longs if s] or ["- （基本面台账为空）"]
    return "\n".join(parts)


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


# ---------------------------------------------------------------- 六、事件与政策

def render_events_policy(market: dict, news: dict, fund: dict) -> str:
    parts = ["**头条**\n"]
    flash = news.get("flash", [])[:5]
    if flash:
        for f in flash:
            t = f.get("time", "")
            mmdd = t[5:16] if len(t) >= 16 else t          # MM-DD HH:MM
            title = f"[{f['title']}]({f['url']})" if f.get("url") else f["title"]
            parts.append(f"- `{mmdd}`【{f['source']}】{title}")
    else:
        parts.append("- （本周窗口内无命中的银行相关快讯）")
    notices = news.get("notices", [])[:10]
    if notices:
        for n in notices:
            title = f"[{n['title']}]({n['url']})" if n.get("url") else n["title"]
            parts.append(f"- `{n['date'][5:]}` [{n['category']}] {n['name']}：{title}")
    cm = fund.get("credit_macro", [])
    if cm:
        c = cm[0]
        tsf = f"社融增量 {c['tsf_yi']:,.0f} 亿元" if c.get("tsf_yi") is not None else "社融增量本月未更新"
        loan = f"新增贷款 {c['new_loans_yi']:,.0f} 亿元" if c.get("new_loans_yi") is not None else "新增贷款未更新"
        parts.append(f"\n**信贷脉冲**：{c['period']}：{tsf}，{loan}（近 6 个月明细见附3）。")
    return "\n".join(parts)


# ---------------------------------------------------------------- 七、基本面摘要

def render_fund_summary(fund: dict) -> str:
    st = fund_stats(fund)
    lines = []
    if st["period"]:
        lines.append(f"- **最新报告期**：{st['period']}（{st['period_n']}/{st['n']} 家已披露至该期）。")
    if st["rev_med"] is not None:
        lines.append(f"- **营收 YoY 中位数 {st['rev_med']:.2f}%**，"
                     f"净利 YoY 中位数 {st['prof_med']:.2f}%（{st['n']} 家，同花顺按报告期口径）。")
    if st["roe_min"] is not None:
        lines.append(f"- **ROE（单季）区间**：{st['roe_min']:.2f}%–{st['roe_max']:.2f}%。")
    lines.append(f"- **营收趋势**：连续上行 {st['trend_up']} 家、连续下行 {st['trend_down']} 家、"
                 f"波动 {st['trend_flat']} 家。")
    lines.append(f"- **手工台账完整度**：个股台账已填 report 的 "
                 f"{st['curated_report_filled']}/{st['n']} 家；"
                 f"行业监管指标已填 {st['reg_filled']}/{st['reg_total']} 项"
                 f"（明细与本周待办见附2/附4）。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 八、下周关注

CN_WEEKDAY = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def render_weekly_focus(target: date) -> str:
    monday = target + timedelta(days=(7 - target.weekday()))  # 下周一
    days = [monday + timedelta(days=i) for i in range(7)]
    bullets = [f"下周区间：{days[0].isoformat()}（{CN_WEEKDAY[days[0].weekday()]}）~ "
               f"{days[-1].isoformat()}（{CN_WEEKDAY[days[-1].weekday()]}）"]
    if any(d.day == 20 for d in days):
        bullets.append("- **LPR 报价**（每月 20 日）：关注 1Y/5Y 以上品种是否调整。")
    if any(d.day >= 28 for d in days):
        bullets.append("- **月末窗口**：PMI（下月 1 日）与金融监管总局季度指标发布窗口，留意社融/信贷数据。")
    if any(d.month == 10 for d in days):
        bullets.append("- **三季报披露期**开启：关注核心池银行业绩预告与率先披露个股。")
    if any(d.month == 10 and d.day <= 8 for d in days):
        bullets.append("- **国庆长假**：A股休市安排与港股通资金流向（历史规律：节前南向资金波动放大）。")
    if any(d.month == 2 and 15 <= d.day <= 23 for d in days):
        bullets.append("- **春节假期**：A股休市，关注节前资金面与长假的政策窗口。")
    return "\n".join(bullets)


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
    syms = {i["symbol"] for i in market["indexes"]}
    entries = [(f"i{sym}", nm) for sym, nm in INDEX_CHART_SERIES if sym in syms]
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


# ---------------------------------------------------------------- 附录

def render_appendix_market(market: dict) -> str:
    cn10y = market["rates"].get("cn10y")
    rows = []
    for s in market["stocks"]:
        spread = round(s["div_yield_ttm"] - cn10y, 2) \
            if s.get("div_yield_ttm") is not None and cn10y is not None else None
        rows.append([s["name"], s["segment"], dash(s["a_close"]),
                     dash(s["a_wtd_pct"], sign=True), dash(s["h_close"]),
                     dash(s["h_wtd_pct"], sign=True), dash(s["ah_premium_pct"], sign=True),
                     dash(s["pb"]), dash(s["pe_ttm"]),
                     dash(s["div_yield_ttm"]), dash(spread, sign=True)])
    return md_table(["名称", "板块", "A收盘", "周%", "H收盘", "周%",
                     "AH溢价%", "PB", "PE-TTM", "股息率TTM%", "利差pct"], rows)


def render_appendix_ledger(fund: dict) -> str:
    rows = []
    for b in fund.get("banks", []):
        lat = b.get("analysis", {}).get("latest", {})
        cur = b.get("curated", {})

        def cv(key, nd=2):
            v = cur.get(key)
            return dash(v, nd) if v is not None else "待填"
        rows.append([b["name"], lat.get("period") or "—",
                     dash(lat.get("revenue_yoy_pct"), sign=True),
                     dash(lat.get("profit_yoy_pct"), sign=True),
                     dash(lat.get("roe_pct")),
                     cv("nim_pct"), cv("npl_ratio_pct"), cv("provision_coverage_pct", nd=0),
                     cv("cet1_pct"), cv("payout_ratio_pct")])
    table = md_table(["名称", "最新期", "营收YoY%", "净利YoY%", "ROE%(单季)",
                      "净息差%", "不良率%", "拨备覆盖率%", "核心一级%", "分红率%"], rows)
    as_of = (fund.get("meta") or {}).get("fin_indicators_as_of")
    note = (f"\n\n> 专项指标来源：东财 F10 主要指标/分红送配自动拉取"
            f"（fin_indicators_{as_of}），手工台账 `bank_fundamentals.json` 非空值优先；"
            f"分红率为上一完整财年口径。" if as_of else
            "\n\n> 专项指标来源：仅手工台账（fin_indicators 缓存缺失）。")
    return table + note


def render_appendix_news(news: dict, fund: dict) -> str:
    parts = []
    flash = news.get("flash", [])
    parts.append(f"**快讯明细（{len(flash)} 条，含摘要）**\n")
    if flash:
        for f in flash:
            title = f"[{f['title']}]({f['url']})" if f.get("url") else f["title"]
            parts.append(f"- `{f.get('time', '')}` 【{f['source']}】{title} — {f.get('summary', '')}")
    else:
        parts.append("（无）")
    notices = news.get("notices", [])
    parts.append(f"\n**公告明细（{len(notices)} 条，按类别）**\n")
    if notices:
        by_cat = {}
        for n in notices:
            by_cat.setdefault(n["category"], []).append(n)
        for cat, items in by_cat.items():
            parts.append(f"\n*{cat}*\n")
            for n in items:
                parts.append(f"- `{n['date']}` {n['name']}：{n['title']}"
                             + (f"（[链接]({n['url']})）" if n.get("url") else ""))
    else:
        parts.append("（无）")
    cm = fund.get("credit_macro") or []
    if cm:
        parts.append("\n**社融与信贷近 6 个月（亿元）**\n")
        rows = [[c["period"], dash(c.get("tsf_yi"), nd=0), dash(c.get("new_loans_yi"), nd=0)]
                for c in cm]
        parts.append(md_table(["月份", "社融增量", "新增贷款"], rows))
    return "\n".join(parts)


def render_appendix_reg(fund: dict) -> str:
    parts = []
    ind = fund.get("industry_regulatory", {}) or {}
    parts.append("**行业监管指标（商业银行整体）**\n")
    if ind.get("latest"):
        rows = [[k, dash(v)] for k, v in ind["latest"].items()]
        parts.append(md_table([f"指标（{ind.get('latest_period', '')}）", "数值"], rows))
    else:
        period = ind.get("latest_period") or "最新期"
        parts.append(f"台账待填报（{period}）：净息差/不良率/拨备覆盖率/资本充足率/利润同比 均待人工从"
                     "金融监管总局季度《商业银行主要监管指标》通报填报（data/regulatory_indicators.json）。")
    if ind.get("trend"):
        parts.append("\n趋势：" + "；".join(str(t) for t in ind["trend"]))
    gaps = fund.get("meta", {}).get("curated_gaps", [])
    parts.append(f"\n**本周待办（curated_gaps 共 {len(gaps)} 项）**\n")
    if gaps:
        for g in gaps:
            parts.append(f"- [ ] {g}")
    else:
        parts.append("（无）")
    return "\n".join(parts)


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="生成银行板块周报 Markdown（正文+附录）")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 output/weekly")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else WEEKLY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    market, d_mkt = load_latest("market_*.json", target)
    news, d_news = load_latest("news_*.json", target)
    fund, d_fund = load_latest("fundamentals_*.json", target)
    if market is None:
        raise SystemExit(f"错误：data/ 下没有 ≤ {target} 的 market_*.json，请先运行 data_puller.py")
    news = news or {"flash": [], "notices": [], "meta": {}}
    fund = fund or {"banks": [], "credit_macro": [], "industry_regulatory": {},
                    "meta": {"curated_gaps": []}}
    data_as_of = max(d for d in (d_mkt, d_news, d_fund) if d).isoformat()

    ah_stats = ah_premium_stats(market, target)
    signals = build_signals(market, ah_stats)
    _, spread, spread_note = update_spread_history(target, market)

    filled = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{{REPORT_DATE}}": target.isoformat(),
        "{{DATA_AS_OF}}": data_as_of,
        "{{GENERATED_AT}}": datetime.now().isoformat(timespec="seconds"),
        "{{DASHBOARD}}": render_dashboard(market, ah_stats, spread, spread_note),
        "{{WOW_CHANGES}}": render_wow(market, ah_stats),
        "{{VIEW_DRAFTS}}": render_view_drafts(market, news, fund, signals),
        "{{AH_FACTOR}}": render_ah_factor(market, ah_stats),
        "{{CHART_INDEX}}": fence(chart_index_relative(market, target)),
        "{{CHART_MOVES}}": fence(chart_weekly_moves(market)),
        "{{CHART_AH}}": fence(chart_ah_premium(market)),
        "{{EVENTS_POLICY}}": render_events_policy(market, news, fund),
        "{{FUND_SUMMARY}}": render_fund_summary(fund),
        "{{WEEKLY_FOCUS}}": render_weekly_focus(target),
        "{{APPENDIX_MARKET}}": render_appendix_market(market),
        "{{APPENDIX_LEDGER}}": render_appendix_ledger(fund),
        "{{APPENDIX_NEWS}}": render_appendix_news(news, fund),
        "{{APPENDIX_REG}}": render_appendix_reg(fund),
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

    gaps = fund.get("meta", {}).get("curated_gaps", [])
    print(f"已生成 {out_file}")
    print("人工待办清单：")
    print("  [待撰写] 一、核心观点")
    print("  [待修订] 四、观点初稿（短期/长期初稿已给，正文部分）")
    print("  [待补充] 八、下周关注（【人工补充】项）")
    print("  [台账]   curated_gaps 共 %d 项待填报（见附录附4「本周待办」）" % len(gaps))


if __name__ == "__main__":
    main()
