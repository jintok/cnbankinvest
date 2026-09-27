#!/usr/bin/env python3
"""akshare 接口探针：逐个调用候选接口，记录可用性/签名/字段/样例。

用法: bank/.venv/bin/python bank/probe_interfaces.py
输出: stdout 人类可读 + bank/probe_results.json 结构化结果
"""
import inspect
import json
import socket
import sys
import time
import traceback
from pathlib import Path

import akshare as ak
import pandas as pd

socket.setdefaulttimeout(30)  # 全局默认超时，防挂死

OUT_DIR = Path(__file__).resolve().parent
RESULTS = {}


def sample_row(df):
    """取最后一行(最近数据)作为样例，截断长字符串。"""
    if df is None or len(df) == 0:
        return None
    row = df.iloc[-1].to_dict()
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        try:
            json.dumps(v, default=str)
            out[str(k)] = v
        except TypeError:
            out[str(k)] = str(v)
    return out


def probe(key, desc, fn, retries=1):
    """调用 fn()，记录结果。失败重试一次。"""
    entry = {"desc": desc, "status": None, "signature": None, "rows": 0,
             "columns": [], "head_date": None, "tail_date": None, "sample": None,
             "error": None}
    try:
        entry["signature"] = str(inspect.signature(fn))
    except (TypeError, ValueError):
        entry["signature"] = "(n/a)"
    for attempt in range(retries + 1):
        try:
            t0 = time.time()
            df = fn()
            entry["elapsed_s"] = round(time.time() - t0, 1)
            if df is None:
                entry["status"] = "FAIL"
                entry["error"] = "returned None"
            elif isinstance(df, pd.DataFrame):
                entry["status"] = "OK"
                entry["rows"] = len(df)
                entry["columns"] = [str(c) for c in df.columns]
                entry["sample"] = sample_row(df)
                for dc in ("日期", "date", " trade_date", "报告期", "交易日"):
                    if dc in df.columns and len(df):
                        entry["head_date"] = str(df[dc].iloc[0])
                        entry["tail_date"] = str(df[dc].iloc[-1])
                        break
            else:
                entry["status"] = "OK"
                entry["sample"] = str(df)[:500]
            break
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {e}"[:300]
            if attempt < retries:
                time.sleep(2)
            else:
                entry["status"] = "FAIL"
                traceback.print_exc(limit=1)
    RESULTS[key] = entry
    icon = "✅" if entry["status"] == "OK" else "❌"
    print(f"{icon} [{key}] {desc}")
    print(f"   signature: {entry['signature']}")
    if entry["status"] == "OK":
        if entry.get("columns"):
            print(f"   rows={entry['rows']} cols={entry['columns']}")
            print(f"   date range: {entry['head_date']} → {entry['tail_date']}")
            print(f"   sample(last row): {json.dumps(entry['sample'], ensure_ascii=False, default=str)[:600]}")
        else:
            print(f"   non-DataFrame result: {str(entry['sample'])[:200]}")
        print(f"   elapsed: {entry.get('elapsed_s')}s")
    else:
        print(f"   ERROR: {entry['error']}")
    print()
    return entry


print(f"akshare {ak.__version__} | pandas {pd.__version__} | python {sys.version.split()[0]}\n")

# ---------- A. A股日线 ----------
probe("A1", "A股日线 qfq daily 601398 (东财)",
      lambda: ak.stock_zh_a_hist(symbol="601398", period="daily",
                                 start_date="20260901", end_date="20260927", adjust="qfq"))
probe("A2", "A股日线 weekly 601398 (东财)",
      lambda: ak.stock_zh_a_hist(symbol="601398", period="weekly",
                                 start_date="20260101", end_date="20260927", adjust="qfq"))

# ---------- B. A股实时快照 ----------
probe("B", "A股实时快照 stock_zh_a_spot_em", lambda: ak.stock_zh_a_spot_em())

# ---------- C. 个股估值历史 (乐咕乐股) ----------
probe("C", "估值历史 stock_a_indicator_lg 601398", lambda: ak.stock_a_indicator_lg(symbol="601398"))

# ---------- D. H股日线 ----------
probe("D", "H股日线 stock_hk_hist 01398 qfq",
      lambda: ak.stock_hk_hist(symbol="01398", period="daily",
                               start_date="20260901", end_date="20260927", adjust="qfq"))

# ---------- E. H股实时快照 ----------
probe("E", "H股实时快照 stock_hk_spot_em", lambda: ak.stock_hk_spot_em())

# ---------- F. 指数日线 (多个变体) ----------
probe("F1", "index_zh_a_hist 399986 中证银行 (东财)",
      lambda: ak.index_zh_a_hist(symbol="399986", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("F2", "index_zh_a_hist 000300 沪深300 (东财)",
      lambda: ak.index_zh_a_hist(symbol="000300", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("F3", "index_zh_a_hist 801780 申万银行 (东财)",
      lambda: ak.index_zh_a_hist(symbol="801780", period="daily",
                                 start_date="20260901", end_date="20260927"))
probe("F4", "stock_zh_index_daily sh000300 (新浪)",
      lambda: ak.stock_zh_index_daily(symbol="sh000300"))
probe("F5", "stock_zh_index_daily sz399986 (新浪)",
      lambda: ak.stock_zh_index_daily(symbol="sz399986"))
probe("F6", "stock_zh_index_hist_csindex 000922 中证红利",
      lambda: ak.stock_zh_index_hist_csindex(symbol="000922",
                                             start_date="20260901", end_date="20260927"))
probe("F7", "stock_zh_index_hist_csindex 399986 中证银行",
      lambda: ak.stock_zh_index_hist_csindex(symbol="399986",
                                             start_date="20260901", end_date="20260927"))
probe("F8", "index_hist_sw 801780 申万银行 (申万宏源)",
      lambda: ak.index_hist_sw(symbol="801780", period="day"))

# ---------- G. 中债收益率 ----------
probe("G", "bond_china_yield 中债收益率曲线",
      lambda: ak.bond_china_yield(start_date="20260901", end_date="20260927"))

# ---------- H. SHIBOR ----------
probe("H", "rate_interbank Shibor 7天",
      lambda: ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="7天"))

# ---------- I. 南向资金 ----------
probe("I1", "南向资金历史 stock_hsgt_hist_em 港股通",
      lambda: ak.stock_hsgt_hist_em(symbol="港股通"))
probe("I2", "沪深港通资金流汇总 stock_hsgt_fund_flow_summary_em",
      lambda: ak.stock_hsgt_fund_flow_summary_em())

# ---------- J. 新闻 ----------
probe("J1", "全球财经快讯 stock_info_global_em", lambda: ak.stock_info_global_em())
probe("J2", "财联社电报 stock_info_global_cls", lambda: ak.stock_info_global_cls())

# ---------- K. 公告 ----------
probe("K1", "公告 stock_notice_report 全部 20260926",
      lambda: ak.stock_notice_report(symbol="全部", date="20260926"))
probe("K2", "公告 stock_notice_report 601398 20260926",
      lambda: ak.stock_notice_report(symbol="601398", date="20260926"))

# ---------- L. 财务摘要 (同花顺) ----------
probe("L", "财务摘要 stock_financial_abstract_ths 601398 按报告期",
      lambda: ak.stock_financial_abstract_ths(symbol="601398", indicator="按报告期"))

# ---------- M. bank 相关接口 ----------
bank_funcs = sorted(x for x in dir(ak) if "bank" in x.lower())
RESULTS["M0"] = {"desc": "dir(ak) 含 bank 的接口", "status": "OK",
                 "bank_funcs": bank_funcs,
                 "signature": None, "rows": 0, "columns": [], "sample": None, "error": None}
print(f"✅ [M0] bank 相关接口共 {len(bank_funcs)} 个: {bank_funcs}\n")

# 对无必填参数的 bank 接口尝试直接调用
for name in bank_funcs:
    f = getattr(ak, name)
    try:
        sig = inspect.signature(f)
        required = [p for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        if required:
            RESULTS[f"M-{name}"] = {"desc": f"bank接口 {name}", "status": "SKIP",
                                    "signature": str(sig), "note": f"需要参数 {required}",
                                    "columns": [], "rows": 0, "sample": None, "error": None}
            print(f"⏭️  [M-{name}] 需要参数: {[p.name for p in required]}")
        else:
            probe(f"M-{name}", f"bank接口 {name}()", f)
    except (TypeError, ValueError):
        pass

# ---------- N. 美债/中债替代 ----------
probe("N", "bond_zh_us_rate 中美利差", lambda: ak.bond_zh_us_rate())

# ---------- 写出结果 ----------
out = OUT_DIR / "probe_results.json"
payload = {"meta": {"akshare": ak.__version__, "pandas": pd.__version__,
                    "python": sys.version.split()[0]}, "results": RESULTS}
out.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
ok = sum(1 for v in RESULTS.values() if v.get("status") == "OK")
fail = sum(1 for v in RESULTS.values() if v.get("status") == "FAIL")
skip = sum(1 for v in RESULTS.values() if v.get("status") in ("SKIP", None))
print(f"==== 汇总: OK={ok} FAIL={fail} SKIP={skip}  详细见 {out} ====")
