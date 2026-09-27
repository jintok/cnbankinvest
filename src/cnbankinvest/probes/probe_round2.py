#!/usr/bin/env python3
"""第二轮探针：修正参数后重试 + 东财接口抖动复测。结果追加到 probe_results.json。"""
import json
import socket
import sys
import time
from pathlib import Path

import akshare as ak
import pandas as pd

socket.setdefaulttimeout(30)

OUT_DIR = Path(__file__).resolve().parent
RESULTS = json.loads((OUT_DIR / "probe_results.json").read_text(encoding="utf-8"))["results"]


def sample_row(df):
    if df is None or len(df) == 0:
        return None
    row = df.iloc[-1].to_dict()
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        out[str(k)] = v if pd.notna(v) or not hasattr(v, "__class__") else str(v)
    return out


def probe(key, desc, fn, retries=2, pause=3):
    entry = {"desc": desc, "status": None, "rows": 0, "columns": [],
             "head_date": None, "tail_date": None, "sample": None, "error": None}
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            df = fn()
            entry["elapsed_s"] = round(time.time() - t0, 1)
            entry["status"] = "OK" if df is not None else "FAIL"
            if isinstance(df, pd.DataFrame):
                entry["rows"] = len(df)
                entry["columns"] = [str(c) for c in df.columns]
                entry["sample"] = sample_row(df)
                for dc in ("日期", "报告日", "发布时间", "统计时间"):
                    if dc in df.columns and len(df):
                        entry["head_date"] = str(df[dc].iloc[0])
                        entry["tail_date"] = str(df[dc].iloc[-1])
                        break
            break
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:250]
            time.sleep(pause)
    RESULTS[key] = entry
    icon = "✅" if entry["status"] == "OK" else "❌"
    print(f"{icon} [{key}] {desc}")
    if entry["status"] == "OK":
        print(f"   rows={entry['rows']} cols={entry['columns'][:16]}")
        print(f"   dates: {entry['head_date']} -> {entry['tail_date']}  elapsed={entry.get('elapsed_s')}s")
        print(f"   sample: {json.dumps(entry['sample'], ensure_ascii=False, default=str)[:500]}")
    else:
        print(f"   ERROR: {entry['error']}")
    print()
    time.sleep(pause)


print("== 东财接口复测（带重试与间隔）==")
probe("A1", "A股日线 daily 601398 qfq (复测)",
      lambda: ak.stock_zh_a_hist(symbol="601398", period="daily",
                                 start_date="20260901", end_date="20260927", adjust="qfq"))
probe("B", "A股实时快照 stock_zh_a_spot_em (复测)", lambda: ak.stock_zh_a_spot_em())
probe("D", "H股日线 stock_hk_hist 01398 qfq (复测)",
      lambda: ak.stock_hk_hist(symbol="01398", period="daily",
                               start_date="20260901", end_date="20260927", adjust="qfq"))
probe("E", "H股实时快照 stock_hk_spot_em (复测)", lambda: ak.stock_hk_spot_em())
probe("F1", "index_zh_a_hist 399986 (复测)",
      lambda: ak.index_zh_a_hist(symbol="399986", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("F2", "index_zh_a_hist 000300 (复测)",
      lambda: ak.index_zh_a_hist(symbol="000300", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("F3", "index_zh_a_hist 801780 (复测)",
      lambda: ak.index_zh_a_hist(symbol="801780", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("K1", "公告 stock_notice_report 全部 20260925 (复测, symbol=报告类型)",
      lambda: ak.stock_notice_report(symbol="全部", date="20260925"))

print("== 修正参数 ==")
probe("H", "rate_interbank Shibor 1周 (indicator='1周')",
      lambda: ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1周"))
probe("I1", "南向资金 stock_hsgt_hist_em(symbol='南向资金')",
      lambda: ak.stock_hsgt_hist_em(symbol="南向资金"))
probe("C1", "百度估值 stock_zh_valuation_baidu 601398 市盈率(TTM)",
      lambda: ak.stock_zh_valuation_baidu(symbol="601398", indicator="市盈率(TTM)", period="近一年"))
probe("C2", "百度估值 stock_zh_valuation_baidu 601398 市净率",
      lambda: ak.stock_zh_valuation_baidu(symbol="601398", indicator="市净率", period="近一年"))
probe("C3", "东财估值 stock_value_em 601398",
      lambda: ak.stock_value_em(symbol="601398"))
probe("C4", "百度估值 stock_zh_valuation_baidu 601398 股息率?",
      lambda: ak.stock_zh_valuation_baidu(symbol="601398", indicator="股息率", period="近一年"))

print("== M 补充：存贷款/银行相关宏观 ==")
probe("M-macro_rmb_deposit", "macro_rmb_deposit 人民币存款", lambda: ak.macro_rmb_deposit())
probe("M-macro_rmb_loan", "macro_rmb_loan 人民币贷款", lambda: ak.macro_rmb_loan())

print("== G 曲线名称取值 ==")
try:
    g = ak.bond_china_yield(start_date="20260901", end_date="20260927")
    print("曲线名称:", sorted(g["曲线名称"].unique()))
    gov = g[g["曲线名称"].str.contains("国债")]
    print(gov.tail(3).to_string())
    RESULTS["G-curves"] = {"desc": "bond_china_yield 曲线名称", "status": "OK",
                           "curves": sorted(g["曲线名称"].unique()),
                           "gov10y_sample": gov.tail(3).to_dict("records"),
                           "columns": [], "rows": 0, "sample": None, "error": None}
except Exception as e:  # noqa: BLE001
    print("G 复查失败:", e)

# 汇总
ok = sum(1 for v in RESULTS.values() if v.get("status") == "OK")
fail = sum(1 for v in RESULTS.values() if v.get("status") == "FAIL")
print(f"==== 累计: OK={ok} FAIL={fail} ====")

payload = json.loads((OUT_DIR / "probe_results.json").read_text(encoding="utf-8"))
payload["results"] = RESULTS
(OUT_DIR / "probe_results.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print("已更新 probe_results.json")
