#!/usr/bin/env python3
"""银行股每周市场快照拉取脚本。

数据源（均已在 INTERFACE_NOTES.md 中实测通过）：
- A股日线   : ak.stock_zh_a_daily（新浪，qfq）      —— 主源
- H股日线   : ak.stock_hk_daily（新浪，qfq，全历史自截）
- 指数日线  : ak.stock_zh_index_hist_csindex（中证官网：000300/399986/000922）
              ak.index_hist_sw（申万宏源官网：801780）
- 估值      : ak.stock_value_em（东财 datacenter-web，重试+间隔）
- 分红      : ak.stock_history_dividend_detail（新浪，派息单位=元/10股）
- 汇率      : ak.currency_boc_sina（中行牌价，折算价/100 = CNY/HKD）
- 利率      : ak.bond_china_yield（中债，10Y国债）
              ak.rate_interbank（Shibor 1周）
- 南向资金  : ak.stock_hsgt_hist_em（南向资金）

日线收盘价按 symbol 缓存在 data/hist/{symbol}.json，每次运行增量合并（tmp+replace 原子写）。

用法：
    .venv/bin/python -m cnbankinvest.data_puller [--date 2026-09-25] [--out data目录]

输出：data/market_YYYY-MM-DD.json（schema 见仓库周报系统设计）
"""
import argparse
import json
import socket
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

socket.setdefaulttimeout(30)  # 全局兜底超时，防挂死
EM_RETRIES = 2          # 东财源(datacenter-web)重试次数
EM_SLEEP = 2.0          # 东财源调用间隔（秒）
CALL_SLEEP = 1.0        # 普通源调用间隔（秒）

MISSING = []            # 收集失败项，写入 meta.missing


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def note_missing(item: str, exc: Exception) -> None:
    MISSING.append(f"{item}: {type(exc).__name__}: {str(exc)[:120]}")
    log(f"  ⚠️ 失败已记录 [{item}] {type(exc).__name__}: {str(exc)[:100]}")


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
    raise last


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def clean(obj):
    """把 numpy/pandas 标量、日期转成 JSON 可序列化的 Python 类型。"""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.strftime("%Y-%m-%d %H:%M:%S") if isinstance(obj, datetime) else obj.strftime("%Y-%m-%d")
    if obj is pd.NaT:
        return None
    if isinstance(obj, float) and pd.isna(obj):
        return None
    try:  # numpy 标量 → python
        import numpy as np
        if isinstance(obj, np.generic):
            return obj.item()
    except ImportError:
        pass
    return obj


# ---------------------------------------------------------------- 缓存

def hist_path(out_dir: Path, key: str) -> Path:
    return out_dir / "hist" / f"{key}.json"


def load_hist(out_dir: Path, key: str) -> dict:
    p = hist_path(out_dir, key)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {"source": data.get("source", ""), "rows": {r[0]: r[1] for r in data.get("rows", [])}}
        except Exception:  # noqa: BLE001
            log(f"  ⚠️ 缓存损坏，重新拉取: {p.name}")
    return {"source": "", "rows": {}}


def save_hist(out_dir: Path, key: str, source: str, rows: dict) -> None:
    p = hist_path(out_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.items())
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"source": source, "rows": [[d, c] for d, c in ordered]},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def update_hist(out_dir: Path, key: str, source: str, new_rows: dict) -> dict:
    """合并缓存并写回；返回合并后的 {date_iso: close}。"""
    cache = load_hist(out_dir, key)
    cache["rows"].update(new_rows)
    cache["source"] = source
    save_hist(out_dir, key, source, cache["rows"])
    return cache["rows"]


# ---------------------------------------------------------------- 序列拉取

def fetch_a_daily(code: str, end: date) -> dict:
    """新浪 A股日线 qfq，拉前年12月起（覆盖 YTD 基期与本周）。"""
    prefix = "sh" if code.startswith("6") else "sz"
    df = ak.stock_zh_a_daily(symbol=f"{prefix}{code}",
                             start_date=f"{end.year - 1}1201",
                             end_date=ymd(end), adjust="qfq")
    df = df[df["date"].astype(str) <= end.isoformat()]
    return {iso(r["date"]): float(r["close"]) for _, r in df.iterrows()}


def fetch_h_daily(code: str, end: date) -> dict:
    """新浪 H股日线 qfq，全历史返回，按日期截断。"""
    df = ak.stock_hk_daily(symbol=code, adjust="qfq")
    df = df[df["date"].astype(str) <= end.isoformat()]
    return {iso(r["date"]): float(r["close"]) for _, r in df.iterrows()}


def fetch_csindex(symbol: str, end: date) -> dict:
    """中证官网指数日线，拉前年12月起。"""
    df = ak.stock_zh_index_hist_csindex(symbol=symbol,
                                        start_date=f"{end.year - 1}1201",
                                        end_date=ymd(end))
    df = df[df["日期"].astype(str) <= end.isoformat()]
    return {iso(r["日期"]): float(r["收盘"]) for _, r in df.iterrows()}


def fetch_sina_index(symbol: str, end: date) -> dict:
    """新浪指数日线（全历史返回，按日期截断）。sz 前缀仅 399 开头深市指数。"""
    prefix = "sz" if symbol.startswith("399") else "sh"
    df = ak.stock_zh_index_daily(symbol=f"{prefix}{symbol}")
    df = df[df["date"].astype(str) <= end.isoformat()]
    return {iso(r["date"]): float(r["close"]) for _, r in df.iterrows()}


def fetch_sw_index(symbol: str, end: date) -> dict:
    """申万宏源指数日线，全历史返回，按日期截断。"""
    df = ak.index_hist_sw(symbol=symbol, period="day")
    df = df[df["日期"].astype(str) <= end.isoformat()]
    return {iso(r["日期"]): float(r["收盘"]) for _, r in df.iterrows()}


# 指数数据源回退链：按 symbol 配置有序候选源，首个返回非空序列者胜。
# 新增指数只需在此登记，无需改 pull_index 逻辑。
INDEX_SOURCES = {
    "801780": [("sina_sw", fetch_sw_index)],          # 申万官网独有
    "931039": [("csindex", fetch_csindex), ("sina", fetch_sina_index)],  # 中证银行AH优选
    "default": [("csindex", fetch_csindex), ("sina", fetch_sina_index)],  # 000300/399986/000922 等中证系
}


def fetch_index_series(symbol: str, end: date) -> dict:
    """按回退链取指数日线；全部失败/为空则抛最后一个异常。
    返回 {"source": 实际命中的源, "rows": {date_iso: close}}。"""
    chain = INDEX_SOURCES.get(symbol, INDEX_SOURCES["default"])
    last_exc = None
    for source, fetcher in chain:
        try:
            rows = fetcher(symbol, end)
            if rows:
                return {"source": source, "rows": rows}
            last_exc = RuntimeError(f"{source} 返回空序列")
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(CALL_SLEEP)
    raise last_exc


# ---------------------------------------------------------------- 价格工具

def close_on_or_before(rows: dict, d: date):
    """rows: {date_iso: close}，取 <= d 的最后一个收盘价。"""
    keys = [k for k in rows if k <= d.isoformat()]
    if not keys:
        return None
    return rows[max(keys)]


def pct(now, base):
    if now is None or base in (None, 0):
        return None
    return round((now / base - 1) * 100, 2)


# ---------------------------------------------------------------- 各板块

def pull_fx(target: date):
    """CNY/HKD。主源：中行牌价折算价/100；备源：fx_spot_quote 买报价。"""
    try:
        df = ak.currency_boc_sina(symbol="港币",
                                  start_date=ymd(target - timedelta(days=10)),
                                  end_date=ymd(target))
        df = df[df["日期"].astype(str) <= target.isoformat()]
        if len(df):
            v = float(df.iloc[-1]["中行折算价"])
            if v == v:  # 非 NaN
                return round(v / 100, 5)
    except Exception as e:  # noqa: BLE001
        note_missing("fx:currency_boc_sina", e)
    try:
        df = ak.fx_spot_quote()
        row = df[df["货币对"] == "HKD/CNY"]
        if len(row):
            v = float(row.iloc[-1]["买报价"])
            if v == v:
                return round(v, 5)
    except Exception as e:  # noqa: BLE001
        note_missing("fx:fx_spot_quote", e)
    return None


def pull_rates(target: date):
    out = {"cn10y": None, "cn10y_prev_week": None, "shibor_1w": None}
    # 中债 10Y 国债（过滤国债曲线）
    try:
        df = ak.bond_china_yield(start_date=ymd(target - timedelta(days=12)),
                                 end_date=ymd(target))
        gov = df[df["曲线名称"] == "中债国债收益率曲线"]
        gov = gov[gov["日期"].astype(str) <= target.isoformat()]
        series = {iso(r["日期"]): float(r["10年"]) for _, r in gov.iterrows()}
        out["cn10y"] = close_on_or_before(series, target)
        out["cn10y_prev_week"] = close_on_or_before(series, target - timedelta(days=7))
    except Exception as e:  # noqa: BLE001
        note_missing("rates:bond_china_yield", e)
    time.sleep(CALL_SLEEP)
    # Shibor 1周
    try:
        df = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="1周")
        df = df[df["报告日"].astype(str) <= target.isoformat()]
        if len(df):
            out["shibor_1w"] = float(df.iloc[-1]["利率"])
    except Exception as e:  # noqa: BLE001
        note_missing("rates:rate_interbank", e)
    return out


def pull_southbound(target: date):
    out = {"week_net_buy_hkd_yi": None, "days": []}
    try:
        df = ak.stock_hsgt_hist_em(symbol="南向资金")
        df = df[df["日期"].astype(str) <= target.isoformat()].tail(5)
        days = [{"date": iso(r["日期"]), "net_buy_hkd_yi": round(float(r["当日成交净买额"]), 2)}
                for _, r in df.iterrows()]
        out["days"] = days
        if days:
            out["week_net_buy_hkd_yi"] = round(sum(d["net_buy_hkd_yi"] for d in days), 2)
    except Exception as e:  # noqa: BLE001
        note_missing("southbound:stock_hsgt_hist_em", e)
    return out


def pull_index(symbol: str, name: str, role: str, target: date, out_dir: Path):
    key = f"i{symbol}"
    item = {"symbol": symbol, "name": name, "role": role,
            "close": None, "wtd_pct": None, "ytd_pct": None, "div_yield_ttm": None}
    try:
        got = fetch_index_series(symbol, target)
        rows = update_hist(out_dir, key, got["source"], got["rows"])
        item["close"] = close_on_or_before(rows, target)
        item["wtd_pct"] = pct(item["close"], close_on_or_before(rows, target - timedelta(days=7)))
        prev_year_end = close_on_or_before(rows, date(target.year - 1, 12, 31))
        item["ytd_pct"] = pct(item["close"], prev_year_end)
    except Exception as e:  # noqa: BLE001
        note_missing(f"index:{symbol}", e)
    return item


def pull_valuation(code: str, target: date):
    """东财 datacenter-web：PB / PE(TTM) / 总市值(亿元)，取 <= target 最新一日。"""
    df = call_em(ak.stock_value_em, symbol=code)
    df = df[df["数据日期"].astype(str) <= target.isoformat()]
    if not len(df):
        return None, None, None
    r = df.iloc[-1]
    pb = float(r["市净率"]) if pd.notna(r["市净率"]) else None
    pe = float(r["PE(TTM)"]) if pd.notna(r["PE(TTM)"]) else None
    mv = round(float(r["总市值"]) / 1e8, 1) if pd.notna(r["总市值"]) else None
    return pb, pe, mv


def pull_div_yield(code: str, target: date, a_close):
    """近365天现金分红(除权除息日口径) / 现价。派息单位=元/10股。"""
    if a_close in (None, 0):
        return None
    df = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
    if "除权除息日" not in df.columns:
        return None
    start = target - timedelta(days=365)
    total = 0.0
    for _, r in df.iterrows():
        ex = r["除权除息日"]
        if pd.isna(ex):
            continue
        ex_s = iso(ex)
        if start.isoformat() < ex_s <= target.isoformat():
            v = r.get("派息")
            total += float(v) / 10 if pd.notna(v) else 0.0
    return round(total / a_close * 100, 2) if total > 0 else 0.0


def pull_stock(st: dict, target: date, out_dir: Path, cny_per_hkd):
    item = {"name": st["name"], "a_code": st["a_code"], "h_code": st["h_code"],
            "segment": st["segment"], "a_close": None, "a_wtd_pct": None,
            "h_close": None, "h_wtd_pct": None, "ah_premium_pct": None,
            "pb": None, "pe_ttm": None, "total_mv_yi": None, "div_yield_ttm": None}

    # A股：日线 + 估值 + 股息率
    try:
        rows = update_hist(out_dir, f"a{st['a_code']}", "sina", fetch_a_daily(st["a_code"], target))
        item["a_close"] = close_on_or_before(rows, target)
        item["a_wtd_pct"] = pct(item["a_close"], close_on_or_before(rows, target - timedelta(days=7)))
    except Exception as e:  # noqa: BLE001
        note_missing(f"stock:{st['a_code']}:a_daily", e)
    time.sleep(CALL_SLEEP)
    try:
        item["pb"], item["pe_ttm"], item["total_mv_yi"] = pull_valuation(st["a_code"], target)
    except Exception as e:  # noqa: BLE001
        note_missing(f"stock:{st['a_code']}:valuation", e)
    time.sleep(EM_SLEEP)
    try:
        item["div_yield_ttm"] = pull_div_yield(st["a_code"], target, item["a_close"])
    except Exception as e:  # noqa: BLE001
        note_missing(f"stock:{st['a_code']}:dividend", e)
    time.sleep(CALL_SLEEP)

    # H股：仅日线
    if st["h_code"]:
        try:
            rows = update_hist(out_dir, f"h{st['h_code']}", "sina", fetch_h_daily(st["h_code"], target))
            item["h_close"] = close_on_or_before(rows, target)
            item["h_wtd_pct"] = pct(item["h_close"], close_on_or_before(rows, target - timedelta(days=7)))
        except Exception as e:  # noqa: BLE001
            note_missing(f"stock:{st['h_code']}:h_daily", e)
        time.sleep(CALL_SLEEP)

    # AH 溢价
    if item["h_close"] is not None and item["a_close"] and cny_per_hkd:
        h_cny = item["h_close"] * cny_per_hkd
        item["ah_premium_pct"] = round((h_cny - item["a_close"]) / item["a_close"] * 100, 2)
    return item


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="银行股每周市场快照")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 仓库根/data")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    watch = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    log(f"目标日期 {target}，输出目录 {out_dir}")

    fx = pull_fx(target)
    log(f"汇率 CNY/HKD = {fx}")
    rates = pull_rates(target)
    log(f"利率: {rates}")
    southbound = pull_southbound(target)
    log(f"南向资金: {southbound['week_net_buy_hkd_yi']} 亿港元/近{len(southbound['days'])}日")

    indexes = []
    for idx in watch["indexes"]:
        indexes.append(pull_index(idx["symbol"], idx["name"], idx["role"], target, out_dir))
        log(f"指数 {idx['name']}: close={indexes[-1]['close']} wtd={indexes[-1]['wtd_pct']}%")
        time.sleep(CALL_SLEEP)

    stocks = []
    for st in watch["stocks"]:
        stocks.append(pull_stock(st, target, out_dir, fx))
        it = stocks[-1]
        log(f"股票 {st['name']}: A={it['a_close']} H={it['h_close']} PB={it['pb']} AH溢价={it['ah_premium_pct']}%")

    result = {
        "meta": {"date": target.isoformat(),
                 "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "missing": MISSING},
        "fx": {"cny_per_hkd": fx},
        "rates": rates,
        "southbound": southbound,
        "indexes": [clean(x) for x in indexes],
        "stocks": [clean(x) for x in stocks],
    }
    out_file = out_dir / f"market_{target.isoformat()}.json"
    tmp = out_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(out_file)
    log(f"已写出 {out_file}（missing={len(MISSING)} 项）")
    if MISSING:
        for m in MISSING:
            log(f"  missing: {m}")


if __name__ == "__main__":
    main()
