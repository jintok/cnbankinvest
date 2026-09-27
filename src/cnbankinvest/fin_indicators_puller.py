#!/usr/bin/env python3
"""银行专项监管指标拉取脚本（净息差/不良率/拨备覆盖率/资本充足率/分红率）。

数据源（2026-09-27 实测，详见 INTERFACE_NOTES.md「专项指标」节）：
- 东财 F10 主要指标 : ak.stock_financial_analysis_indicator_em（datacenter-web，重试+间隔）
    银行专项列：NET_INTEREST_MARGIN(净息差) / NON_PERFORMING_LOAN(不良余额,元) /
    GROSSLOANS(贷款总额,元) / LOAN_PROVISION_RATIO(拨贷比) / HXYJBCZL(核心一级) /
    FIRST_ADEQUACY_RATIO(一级) / NEWCAPITALADER(资本充足率) / TOTALDEPOSITS(存款) /
    LTDRR(贷存比,小数) / PARENTNETPROFIT / TOTALOPERATEREVE
    派生：不良率 = 不良余额/贷款总额；拨备覆盖率 = 拨贷比/不良率（半年度口径，因拨贷比半年披露）
- 东财分红送配详情 : ak.stock_fhps_detail_em（每10股现金分红 + 每股收益）
    派生：分红率 = 财年(中期+期末)每股分红合计 ÷ 年报每股收益

用法：
    .venv/bin/python -m cnbankinvest.fin_indicators_puller [--date 2026-09-25] [--out data目录]

输出：data/fin_indicators_YYYY-MM-DD.json
（fin_report_analysis 会读 ≤ --date 的最新一份，合并进 fundamentals 的 curated 生效层，
  手工台账 data/bank_fundamentals.json 的非空值仍然优先。）
"""
import argparse
import json
import socket
import time
from datetime import date, datetime
from pathlib import Path

from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

socket.setdefaulttimeout(30)

EM_RETRIES = 2          # 东财源重试次数
EM_SLEEP = 2.0          # 东财源调用间隔（秒）
KEEP_PERIODS = 6        # 保留最近 N 个报告期历史
KEEP_FYS = 3            # 分红率保留最近 N 个财年

MISSING = []


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def num(v):
    """东财列值 → float；None/bool/NaN/解析失败 → None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def call_em(fn, *args, **kwargs):
    """东财源调用：失败重试 EM_RETRIES 次，间隔 EM_SLEEP 秒。"""
    last = None
    for i in range(EM_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < EM_RETRIES:
                time.sleep(EM_SLEEP)
    raise RuntimeError(f"东财重试{EM_RETRIES}次仍失败: {last}")


def em_suffix(a_code: str) -> str:
    return f"{a_code}.SH" if a_code.startswith(("6", "9")) else f"{a_code}.SZ"


def dstr(v) -> str:
    """REPORT_DATE 等 → 'YYYY-MM-DD' 字符串。"""
    s = str(v)
    return s[:10]


def period_from_row(row) -> str:
    """'2026中报'/'2025年报' 等报告期名；缺失时由日期推导。"""
    name = row.get("REPORT_DATE_NAME")
    if name:
        return str(name)
    return dstr(row.get("REPORT_DATE"))


def build_bank_record(a_code: str) -> dict:
    """东财主要指标 → 单行银行指标记录（最新值 + 历史）。"""
    import akshare as ak
    df = call_em(ak.stock_financial_analysis_indicator_em,
                 symbol=em_suffix(a_code), indicator="按报告期")

    def row_fields(row):
        npl_amt = num(row.get("NON_PERFORMING_LOAN"))          # 元
        loans = num(row.get("GROSSLOANS"))                     # 元
        npl_ratio = npl_amt / loans * 100 if (npl_amt and loans) else None
        lpr = num(row.get("LOAN_PROVISION_RATIO"))             # 拨贷比 %
        coverage = lpr / npl_ratio * 100 if (lpr and npl_ratio) else None
        ldr = num(row.get("LTDRR"))
        npf = num(row.get("PARENTNETPROFIT"))                  # 元
        rev = num(row.get("TOTALOPERATEREVE"))                 # 元
        return {
            "period": period_from_row(row),
            "report_date": dstr(row.get("REPORT_DATE")),
            "notice_date": dstr(row.get("NOTICE_DATE")),
            "nim_pct": num(row.get("NET_INTEREST_MARGIN")),
            "nim_spread_pct": num(row.get("NET_INTEREST_SPREAD")),
            "npl_ratio_pct": round(npl_ratio, 2) if npl_ratio else None,
            "provision_coverage_pct": round(coverage, 1) if coverage else None,
            "loan_provision_ratio_pct": lpr,
            "cet1_pct": num(row.get("HXYJBCZL")),
            "tier1_pct": num(row.get("FIRST_ADEQUACY_RATIO")),
            "car_pct": num(row.get("NEWCAPITALADER")),
            "gross_loans_yi": round(loans / 1e8, 2) if loans else None,
            "npl_amt_yi": round(npl_amt / 1e8, 2) if npl_amt else None,
            "deposits_yi": (lambda d: round(d / 1e8, 2) if d else None)(num(row.get("TOTALDEPOSITS"))),
            "ldr_pct": round(ldr * 100, 2) if ldr else None,
            "net_profit_yi": round(npf / 1e8, 2) if npf else None,
            "revenue_yi": round(rev / 1e8, 2) if rev else None,
        }

    rows = list(df.itertuples(index=False, name=None))
    cols = list(df.columns)
    recs = [dict(zip(cols, r)) for r in rows]
    periods = [row_fields(r) for r in recs[:KEEP_PERIODS]]
    if not periods:
        raise RuntimeError("主要指标无报告期数据")
    latest = periods[0]
    history = [{k: p[k] for k in
                ("period", "nim_pct", "npl_ratio_pct", "provision_coverage_pct",
                 "cet1_pct", "car_pct")} for p in periods]
    return latest, history


def build_payout(a_code: str) -> dict:
    """分红送配详情 → 各财年分红率（(中期+期末)每股分红 ÷ 年报EPS）。"""
    import akshare as ak
    df = call_em(ak.stock_fhps_detail_em, symbol=a_code)

    by_period = {}
    cols = list(df.columns)
    for r in df.itertuples(index=False, name=None):
        row = dict(zip(cols, r))
        per = dstr(row.get("报告期"))
        if not per.startswith("20"):
            continue
        by_period[per] = row

    fys = {}
    for per, row in sorted(by_period.items(), reverse=True):
        fy = per[:4]
        cash = num(row.get("现金分红-现金分红比例"))   # 每10股派X元
        eps = num(row.get("每股收益"))
        if cash is None:
            continue
        entry = fys.setdefault(fy, {"fy": fy, "div_per_share": 0.0, "annual_eps": None,
                                    "schemes": []})
        entry["div_per_share"] += cash / 10.0
        entry["schemes"].append({
            "period": per,
            "per_10_cash": cash,
            "progress": str(row.get("方案进度") or ""),
        })
        if per.endswith("-12-31") and eps:
            entry["annual_eps"] = eps
        time.sleep(0)  # no-op，保持结构一致

    out = []
    for fy in sorted(fys, reverse=True)[:KEEP_FYS]:
        e = fys[fy]
        payout = (e["div_per_share"] / e["annual_eps"] * 100
                  if e["annual_eps"] else None)
        out.append({"fiscal_year": fy,
                    "payout_ratio_pct": round(payout, 1) if payout else None,
                    "div_per_share": round(e["div_per_share"], 4),
                    "annual_eps": e["annual_eps"],
                    "schemes": e["schemes"]})
    # 最新完整财年 = 第一个有 annual_eps 的（年报已出、分红率可算）
    latest = next((o for o in out if o["payout_ratio_pct"] is not None), None)
    return {"latest": latest, "by_year": out}


def main() -> None:
    import akshare as ak  # noqa: F401 - 触发导入耗时放在最前
    parser = argparse.ArgumentParser(description="银行专项监管指标 → fin_indicators_{date}.json")
    parser.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--out", default=None, help="输出目录，默认 仓库根/data")
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    watch = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))["stocks"]
    banks = []
    for st in watch:
        name, a_code = st["name"], st["a_code"]
        rec = {"name": name, "a_code": a_code, "segment": st.get("segment"),
               "source": "stock_financial_analysis_indicator_em + stock_fhps_detail_em"}
        try:
            latest, history = build_bank_record(a_code)
            rec.update(latest)
            rec["history"] = history
            log(f"{name}({a_code}) {latest['period']}: NIM={latest['nim_pct']} "
                f"不良={latest['npl_ratio_pct']}% 拨备覆盖={latest['provision_coverage_pct']} "
                f"核心一级={latest['cet1_pct']}")
        except Exception as e:  # noqa: BLE001
            MISSING.append(f"{name}({a_code}) 专项指标失败: {type(e).__name__} {str(e)[:80]}")
            log(f"  ⚠️ {name} 专项指标失败: {e}")
        time.sleep(EM_SLEEP)
        try:
            payout = build_payout(a_code)
            rec["payout"] = payout
            latest_p = payout["latest"]
            if latest_p:
                rec["payout_ratio_pct"] = latest_p["payout_ratio_pct"]
                rec["payout_fy"] = latest_p["fiscal_year"]
                log(f"  {name} 分红率 FY{latest_p['fiscal_year']}={latest_p['payout_ratio_pct']}%")
        except Exception as e:  # noqa: BLE001
            MISSING.append(f"{name}({a_code}) 分红送配失败: {type(e).__name__} {str(e)[:80]}")
            log(f"  ⚠️ {name} 分红送配失败: {e}")
        time.sleep(EM_SLEEP)
        banks.append(rec)

    result = {
        "meta": {
            "date": target.isoformat(),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "missing": MISSING,
        },
        "banks": banks,
    }
    out_path = out_dir / f"fin_indicators_{target.isoformat()}.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, out_path)
    ok = sum(1 for b in banks if b.get("nim_pct") is not None)
    print(f"已生成: {out_path}（专项指标 {ok}/{len(banks)} 家成功, missing {len(MISSING)} 条）")


if __name__ == "__main__":
    main()
