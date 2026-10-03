#!/usr/bin/env python3
"""银行基本面定期分析: 同花顺财务摘要(akshare) + 专项指标自动层 → 结构化 JSON。

用法:
    .venv/bin/python -m cnbankinvest.fin_report_analysis [--date YYYY-MM-DD] [--out DIR]

输入:
    watchlist.json                       股票池(12 家银行)
    data/fin_indicators_*.json           专项指标自动层(fin_indicators_puller 产出)

输出:
    {--out}/fundamentals_{date}.json

说明:
    - 同花顺(ths)财务摘要数值为带单位字符串("1736.82亿"/"3.32%"), 需解析;
      ROE 为单季度口径。
    - 净息差/不良率/拨备覆盖率/资本充足率/分红率/规模/每股/逾期等专项指标
      全部来自 fin_indicators_puller 自动层(东财 F10+分红送配), 透传进 indicators 字段。
    - 社融/新增贷款在运行时探针 dir(ak) 候选接口, 第一个可用者胜。
"""
import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

THS_RETRIES = 3          # 同花顺调用重试次数(含首次)
THS_SLEEP = 2.0          # 同花顺限频: 调用间隔与重试退避均 >= 2s
KEEP_PERIODS = 6         # 保留最近 N 个报告期
PROBE_MAX_CALLS = 4      # 社融/贷款探针最多尝试接口数

# 报告期月份 → 季度标签
MD_TO_Q = {"03-31": "Q1", "06-30": "Q2", "09-30": "Q3", "12-31": "Q4"}


def parse_num(raw, pct=False):
    """解析同花顺带单位字符串: "1736.82亿"→1736.82, "52.3万"→0.00523(亿), "3.32%"→3.32。

    金额统一转成亿元; pct=True 时按百分数返回数值。无法解析返回 None。
    """
    if raw is None or isinstance(raw, bool):  # pandas 3.0 会把 NaN 转成 False
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "--", "-", "None", "nan", "NaN", "False"):
        return None
    if s.endswith("%"):
        s = s[:-1]
        pct = True
    mult = 1.0
    if s.endswith("亿"):
        s = s[:-1]
    elif s.endswith("万"):
        s = s[:-1]
        mult = 1e-4  # 万 → 亿
    try:
        v = float(s) * mult
    except ValueError:
        return None
    return round(v, 4) if pct else round(v, 2)


def period_label(report_date):
    """"2026-06-30" → "2026Q2"。非标准报告期返回原字符串。"""
    try:
        y, md = report_date[:4], report_date[5:10]
        return f"{y}{MD_TO_Q[md]}"
    except (KeyError, IndexError):
        return report_date


def yoy_pct(cur, prev):
    """同比: 需要 prev 非 0 有效值。"""
    if cur is None or prev is None or abs(prev) < 1e-12:
        return None
    return round((cur / prev - 1.0) * 100.0, 2)


def fetch_ths_abstract(a_code):
    """拉取同花顺财务摘要(按报告期), 重试 THS_RETRIES 次, 每次间隔 >= 2s。"""
    import akshare as ak
    last_err = None
    for attempt in range(THS_RETRIES):
        try:
            df = ak.stock_financial_abstract_ths(symbol=a_code, indicator="按报告期")
            return df.to_dict(orient="records")
        except Exception as e:  # noqa: BLE001 - 网络异常统一兜底
            last_err = e
            time.sleep(THS_SLEEP)
    raise RuntimeError(f"同花顺财务摘要重试 {THS_RETRIES} 次仍失败: {last_err}")


def analyze_bank(records):
    """从同花顺记录提取最近 KEEP_PERIODS 个报告期的营收/净利/ROE 并计算同比。

    返回 {"periods": [...最新在前...], "latest": {...}|None}。
    """
    # 按报告期降序(新→旧)
    rows = sorted(records, key=lambda r: str(r.get("报告期") or ""), reverse=True)
    by_date = {}
    for r in rows:
        d = str(r.get("报告期") or "").strip()
        if not d or d in by_date:
            continue
        by_date[d] = {
            "revenue": parse_num(r.get("营业总收入")),
            "profit": parse_num(r.get("归母净利润") or r.get("净利润")),
            "roe": parse_num(r.get("净资产收益率"), pct=True),
        }
    dates = sorted(by_date.keys(), reverse=True)
    kept = dates[:KEEP_PERIODS]

    periods = []
    for d in kept:
        cur = by_date[d]
        prev_d = f"{int(d[:4]) - 1}{d[4:]}"
        prev = by_date.get(prev_d)
        item = {
            "period": period_label(d),
            "revenue_yi": cur["revenue"],
            "revenue_yoy_pct": yoy_pct(cur["revenue"], prev["revenue"] if prev else None),
            "profit_yi": cur["profit"],
            "profit_yoy_pct": yoy_pct(cur["profit"], prev["profit"] if prev else None),
            "roe_pct": cur["roe"],
        }
        periods.append(item)
    latest = None
    if periods:
        p = periods[0]
        latest = {
            "period": p["period"],
            "revenue_yoy_pct": p["revenue_yoy_pct"],
            "profit_yoy_pct": p["profit_yoy_pct"],
            "roe_pct": p["roe_pct"],
        }
    return {"periods": periods, "latest": latest}


def normalize_month(m):
    """统一月份格式: "202602" / "2026-02" / "2026年2月" → "2026-02"。"""
    s = str(m).strip().replace("年", "-").replace("月", "").replace("-", "")
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:]}"
    return str(m).strip()


def probe_credit_macro(missing):
    """运行时探针: 社融增量 + 新增人民币贷款(月度, 亿元), 第一个可用接口胜。

    候选来自 dir(ak) 过滤 shrzgm/social/loan/credit, 最多尝试 PROBE_MAX_CALLS 个。
    返回 (list[{"period","tsf_yi","new_loans_yi"}], 探针记录list)。
    """
    import akshare as ak

    pool = [
        n for n in dir(ak)
        if n.startswith(("macro_china", "macro_rmb"))
        and any(k in n.lower() for k in ("shrzgm", "social", "loan", "credit"))
    ]
    tsf_candidates = ["macro_china_shrzgm"] + [n for n in pool if "shrzgm" in n or "social" in n]
    loan_candidates = ["macro_rmb_loan"] + [n for n in pool if "loan" in n or "credit" in n]

    def dedup(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen and hasattr(ak, x):
                seen.add(x)
                out.append(x)
        return out

    tsf_candidates, loan_candidates = dedup(tsf_candidates), dedup(loan_candidates)

    tsf_rows, loan_rows, calls, notes = {}, {}, 0, []
    todo = [("tsf", n) for n in tsf_candidates] + [("loan", n) for n in loan_candidates]
    for kind, name in todo:
        if calls >= PROBE_MAX_CALLS:
            break
        if (kind == "tsf" and tsf_rows) or (kind == "loan" and loan_rows):
            continue
        calls += 1
        try:
            df = getattr(ak, name)()
            recs = df.to_dict(orient="records")
            notes.append(f"{name}: OK {len(recs)}行 列={list(df.columns)[:4]}")
            if kind == "tsf":
                for r in recs:
                    m = normalize_month(r.get("月份"))
                    v = r.get("社会融资规模增量")
                    tsf_rows[m] = parse_num(v)
            else:
                for r in recs:
                    m = normalize_month(r.get("月份"))
                    v = r.get("新增人民币贷款-总额")
                    loan_rows[m] = parse_num(v)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}: 失败 {type(e).__name__} {str(e)[:80]}")
        time.sleep(THS_SLEEP)

    months = sorted(set(tsf_rows) | set(loan_rows), reverse=True)[:6]
    out = [
        {"period": m, "tsf_yi": tsf_rows.get(m), "new_loans_yi": loan_rows.get(m)}
        for m in months
    ]
    if not out:
        missing.append("credit_macro: 社融/新增贷款接口均不可用")
    return out, notes


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_latest_indicators(target_date: str):
    """data/ 下 ≤ target_date 的最新 fin_indicators_*.json → (dict|None, as_of|None)。"""
    files = sorted(DATA_DIR.glob("fin_indicators_*.json"))
    ok = [f for f in files if f.stem.rsplit("_", 1)[-1] <= target_date]
    if not ok:
        return None, None
    f = ok[-1]
    return load_json(f), f.stem.rsplit("_", 1)[-1]


def atomic_write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main():
    socket.setdefaulttimeout(30)
    parser = argparse.ArgumentParser(description="银行基本面分析 → fundamentals_{date}.json")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="报告日期")
    parser.add_argument("--out", default=str(DATA_DIR), help="输出目录")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = []
    watchlist = load_json(WATCHLIST_PATH)["stocks"]

    # credit_macro 运行时探针
    credit_macro, probe_notes = probe_credit_macro(missing)

    # 银行专项指标自动层（fin_indicators_puller 缓存）
    indicators, indicators_as_of = find_latest_indicators(args.date)
    auto_by_code = {b.get("a_code"): b for b in (indicators or {}).get("banks", [])}
    if indicators_as_of is None:
        missing.append("fin_indicators: 无可用缓存（可运行 fin_indicators_puller）")

    # 个股: 同花顺财务摘要 + 专项指标自动层透传
    banks_out = []
    for st in watchlist:
        name, a_code = st["name"], st["a_code"]
        analysis = {"periods": [], "latest": None}
        try:
            records = fetch_ths_abstract(a_code)
            analysis = analyze_bank(records)
            if not analysis["periods"]:
                missing.append(f"{name}({a_code}) 财务摘要无可用报告期")
        except Exception as e:  # noqa: BLE001
            missing.append(f"{name}({a_code}) 同花顺财务摘要获取失败: {type(e).__name__} {str(e)[:60]}")
        time.sleep(THS_SLEEP)

        auto_rec = auto_by_code.get(a_code) or {}
        banks_out.append({
            "name": name,
            "a_code": a_code,
            "segment": st.get("segment"),
            "analysis": analysis,
            "indicators": auto_rec,
            "indicators_meta": {
                "as_of": indicators_as_of,
                "period": auto_rec.get("period"),
                "payout_fy": auto_rec.get("payout_fy"),
            },
        })

    result = {
        "meta": {
            "date": args.date,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "missing": missing,
            "fin_indicators_as_of": indicators_as_of,
        },
        "credit_macro": credit_macro,
        "banks": banks_out,
    }
    out_path = out_dir / f"fundamentals_{args.date}.json"
    atomic_write_json(out_path, result)
    print(f"已生成: {out_path}")
    ok = sum(1 for b in banks_out if b["analysis"]["periods"])
    print(f"银行解析成功 {ok}/{len(banks_out)}; credit_macro {len(credit_macro)} 期; missing {len(missing)} 条")
    for n in probe_notes:
        print(f"  探针 {n}")


if __name__ == "__main__":
    sys.exit(main())
