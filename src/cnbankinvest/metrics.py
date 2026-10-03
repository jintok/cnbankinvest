"""估值/位置类共享指标计算（供 gen_weekly_report / gen_single_report 使用）。

全部基于本地缓存离线计算，不联网：
- data/valuation_hist/{code}.json — PB 日频序列 + 分红记录（data_puller 维护）
- data/hist/{key}.json — 日线收盘缓存（data_puller 维护）

口径见 methodology/ 当前版本「指标口径」表。
"""
import json
import math
import statistics
from datetime import date, timedelta

from cnbankinvest.charts import hist_close_series
from cnbankinvest.paths import DATA_DIR

PCTILE_YEARS = 5          # 分位窗口：近 5 年
CORR_WINDOW = 250         # 相关性矩阵窗口：近 1 年（交易日）
W52_DAYS = 365            # 52 周窗口（自然日）


# ---------------------------------------------------------------- 估值缓存读取

def load_valuation_hist(code: str) -> dict:
    """读 valuation_hist/{code}.json → {"pb_rows": {date: pb}, "dividends": [{ex, per_10}]}。"""
    p = DATA_DIR / "valuation_hist" / f"{code}.json"
    if not p.exists():
        return {"pb_rows": {}, "dividends": []}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return {"pb_rows": {d: v for d, v in obj.get("pb_rows", [])},
                "dividends": obj.get("dividends", [])}
    except Exception:  # noqa: BLE001
        return {"pb_rows": {}, "dividends": []}


def _pctile(series: list, current) -> float | None:
    """current 在 series 中的百分位（0–100）；样本 <20 视为不足。"""
    vals = [v for v in series if v is not None]
    if current is None or len(vals) < 20:
        return None
    return round(sum(1 for v in vals if v <= current) / len(vals) * 100, 0)


def pb_pctile(code: str, target: date, current_pb) -> float | None:
    """当前 PB 在近 5 年日频 PB 中的分位。"""
    rows = load_valuation_hist(code)["pb_rows"]
    start = (target - timedelta(days=PCTILE_YEARS * 365)).isoformat()
    series = [v for d, v in rows.items() if start <= d <= target.isoformat()]
    return _pctile(series, current_pb)


def div_yield_series(code: str, target: date, years: int = PCTILE_YEARS) -> dict:
    """自算历史股息率序列 {date: yield%}：每日近 365 天分红（元/10股÷10）÷ 当日收盘。"""
    cache = load_valuation_hist(code)
    divs = [(d["ex"], d["per_10"] / 10) for d in cache["dividends"]]
    start = (target - timedelta(days=years * 365)).isoformat()
    prices = hist_close_series(f"a{code}", target.isoformat())
    prices = {d: c for d, c in prices.items() if d >= start}
    out = {}
    for d, close in prices.items():
        win_start = (date.fromisoformat(d) - timedelta(days=365)).isoformat()
        total = sum(v for ex, v in divs if win_start < ex <= d)
        if total > 0 and close:
            out[d] = round(total / close * 100, 2)
    return out


def div_yield_pctile(code: str, target: date, current_yield) -> float | None:
    """当前股息率在近 5 年自算序列中的分位。"""
    series = div_yield_series(code, target)
    return _pctile(list(series.values()), current_yield)


def week52(code: str, target: date) -> dict:
    """52 周位置：pos = (现价−52周低)/(52周高−52周低)；dd = 距 52 周高点回撤（负值）。"""
    start = (target - timedelta(days=W52_DAYS)).isoformat()
    prices = hist_close_series(f"a{code}", target.isoformat())
    prices = {d: c for d, c in prices.items() if d >= start}
    out = {"pos_pct": None, "drawdown_pct": None, "w52_high": None, "w52_low": None}
    if not prices:
        return out
    cur = prices[max(prices)]
    hi, lo = max(prices.values()), min(prices.values())
    out["w52_high"], out["w52_low"] = round(hi, 2), round(lo, 2)
    if hi > lo:
        out["pos_pct"] = round((cur - lo) / (hi - lo) * 100, 0)
    if hi:
        out["drawdown_pct"] = round((cur / hi - 1) * 100, 2)
    return out


def annualized_roe(bank_fund: dict | None) -> float | None:
    """ROE 年化 = 最新期累计 ROE × 4 ÷ 季度序数（同花顺净资产收益率为累计口径，
    如 2026Q2 累计 4.32% → 年化 ≈ 8.64%）。"""
    periods = (bank_fund or {}).get("analysis", {}).get("periods", [])
    if not periods:
        return None
    p = periods[0]
    roe = p.get("roe_pct")
    period = str(p.get("period") or "")
    if roe is None or len(period) < 2 or period[-2] != "Q":
        return None
    try:
        q = int(period[-1])
    except ValueError:
        return None
    if not 1 <= q <= 4:
        return None
    return round(roe * 4 / q, 2)


def pb_roe_deviation(pb, roe_annual, pool_ratios: list) -> float | None:
    """PB/ROE 相对板块中位偏离度 = 个股 PB/ROE ÷ 板块中位(PB/ROE) − 1，%。"""
    if pb is None or not roe_annual:
        return None
    ratios = [r for r in pool_ratios if r is not None and not math.isnan(r)]
    if len(ratios) < 3:
        return None
    med = statistics.median(ratios)
    if not med:
        return None
    return round(((pb / roe_annual) / med - 1) * 100, 1)


def index_corr_matrix(symbols: list, target: date) -> list | None:
    """指数近 1 年日收益 Pearson 相关矩阵。

    symbols: [(symbol, name), ...]；返回 (names, matrix) 或 None（样本不足）。
    """
    end_iso = target.isoformat()
    maps = []
    names = []
    for sym, name in symbols:
        m = hist_close_series(f"i{sym}", end_iso)
        if m:
            maps.append(m)
            names.append(name)
    if len(maps) < 2:
        return None
    rets = []
    for m in maps:
        dates = sorted(m)[-CORR_WINDOW - 1:]
        r = {dates[i]: m[dates[i]] / m[dates[i - 1]] - 1 for i in range(1, len(dates))}
        rets.append(r)
    common = set(rets[0])
    for r in rets[1:]:
        common &= set(r)
    dates = sorted(common)[-CORR_WINDOW:]
    if len(dates) < 60:
        return None
    n = len(rets)
    matrix = []
    cols = [[r[d] for d in dates] for r in rets]
    for i in range(n):
        row = []
        for j in range(n):
            xs, ys = cols[i], cols[j]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = sum((x - mx) ** 2 for x in xs)
            vy = sum((y - my) ** 2 for y in ys)
            row.append(round(cov / math.sqrt(vx * vy), 2) if vx and vy else None)
        matrix.append(row)
    return names, matrix
