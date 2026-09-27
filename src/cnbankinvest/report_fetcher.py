#!/usr/bin/env python3
"""银行定期报告 PDF 下载器（东财公告链路，2026-09-27 实测打通）。

链路: stock_individual_notice_report(标题→art_code)
      → np-cnotice-stock.eastmoney.com/api/content/ann(art_code→attach_url)
      → pdf.dfcfw.com 下载 PDF。

用法：
    .venv/bin/python -m cnbankinvest.report_fetcher --code 601398 [--period 2026中报]
    .venv/bin/python -m cnbankinvest.report_fetcher --all            # 12家 × 最新报告期
    .venv/bin/python -m cnbankinvest.report_fetcher --code 601398 --period 2025年报 --force

period 取值: {年份}年报 / {年份}中报 / {年份}一季报 / {年份}三季报。
--all 的「最新报告期」读 data/ 下最新 fin_indicators_*.json 的各行 period。

输出：data/reports/{a_code}_{period}.pdf（目录 gitignore，可按需重下）。
注意：东财 datacenter/公告域名走代理可用但需重试；np-cnotice/pdf.dfcfw 为东财系，
      同样按「重试≥2次+间隔≥2s」约定调用。
"""
import argparse
import json
import re
import socket
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

socket.setdefaulttimeout(60)

EM_RETRIES = 2
EM_SLEEP = 2.0
REPORTS_DIR = DATA_DIR / "reports"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

MISSING = []


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# period → (标题正则片段, 查询起, 查询止)  日期均为大致披露窗口，宁宽勿漏
PERIOD_SPECS = {
    "年报": (r"(?:{year}年?年度报告)$", "{y1}-02-01", "{y1}-05-15"),
    "中报": (r"(?:{year}年?半年度报告)$", "{year}-07-15", "{year}-10-15"),
    "一季报": (r"(?:{year}年?第一季度报告)$", "{year}-03-20", "{year}-05-15"),
    "三季报": (r"(?:{year}年?第三季度报告)$", "{year}-09-25", "{year}-11-15"),
}
EXCLUDE_TITLE = ("摘要", "英文", "更新后", "已取消", "更正", "补充", "取消")


def parse_period(period: str):
    m = re.match(r"^(\d{4})(年报|中报|一季报|三季报)$", period)
    if not m:
        raise SystemExit(f"period 格式应为 {{年份}}{{年报|中报|一季报|三季报}}， got {period!r}")
    year, kind = int(m.group(1)), m.group(2)
    pat, d0, d1 = PERIOD_SPECS[kind]
    return re.compile(pat.format(year=year)), d0.format(year=year, y1=year + 1), d1.format(year=year, y1=year + 1)


def call_em(fn, *args, **kwargs):
    last = None
    for i in range(EM_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < EM_RETRIES:
                time.sleep(EM_SLEEP)
    raise RuntimeError(f"东财重试{EM_RETRIES}次仍失败: {last}")


def fetch_notice_list(a_code: str, begin: str, end: str):
    """公告列表（财务报告类优先，空则回退全部）。"""
    import akshare as ak
    for symbol in ("财务报告", "全部"):
        try:
            df = call_em(ak.stock_individual_notice_report, security=a_code, symbol=symbol,
                         begin_date=begin.replace("-", ""), end_date=end.replace("-", ""))
            if df is not None and len(df):
                return df
        except Exception as e:  # noqa: BLE001
            log(f"  ⚠️ 公告列表 {symbol} 失败: {e}")
            time.sleep(EM_SLEEP)
    return None


def find_report_url(df, title_re) -> str:
    """从公告 DataFrame 中挑出定期报告全文的公告页 URL。"""
    best = None
    cols = list(df.columns)
    for r in df.itertuples(index=False, name=None):
        row = dict(zip(cols, r))
        title = str(row.get("公告标题") or "")
        url = str(row.get("网址") or "")
        if not title_re.search(title.replace(" ", "")):
            continue
        if any(k in title for k in EXCLUDE_TITLE):
            continue
        best = url  # DataFrame 按日期倒序，第一条即最新
        break
    if not best:
        raise RuntimeError("窗口内未匹配到定期报告全文公告")
    return best


def resolve_pdf_url(notice_url: str) -> tuple:
    """公告页 URL → (art_code, pdf_url, size)。取附件中最大者（全文而非摘要）。"""
    art = re.search(r"(AN\d+)", notice_url)
    if not art:
        raise RuntimeError(f"URL 中无 art_code: {notice_url}")
    art_code = art.group(1)
    api = (f"https://np-cnotice-stock.eastmoney.com/api/content/ann"
           f"?art_code={art_code}&client_source=web&page_index=1")
    req = urllib.request.Request(api, headers=UA)
    last = None
    for i in range(EM_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            atts = (data.get("data") or {}).get("attach_list") or []
            if not atts:
                raise RuntimeError("公告无 PDF 附件")
            att = max(atts, key=lambda a: int(a.get("attach_size") or 0))
            return art_code, att["attach_url"], int(att.get("attach_size") or 0)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(EM_SLEEP)
    raise RuntimeError(f"公告内容接口失败: {last}")


def download(url: str, dest: Path) -> int:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as f:
        size = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    if size < 50_000:
        tmp.unlink()
        raise RuntimeError(f"下载过小({size}B)，疑似非全文 PDF")
    tmp.replace(dest)   # tmp+replace 原子写
    return size


def fetch_one(name: str, a_code: str, period: str, force: bool = False) -> Path | None:
    dest = REPORTS_DIR / f"{a_code}_{period}.pdf"
    if dest.exists() and dest.stat().st_size > 50_000 and not force:
        log(f"{name} {period} 已存在，跳过: {dest.name}")
        return dest
    title_re, d0, d1 = parse_period(period)
    df = fetch_notice_list(a_code, d0, d1)
    if df is None:
        MISSING.append(f"{name}({a_code}) {period} 公告列表为空")
        return None
    url = find_report_url(df, title_re)
    art_code, pdf_url, size = resolve_pdf_url(url)
    log(f"{name} {period}: art={art_code} 附件≈{size/1e6:.1f}MB，下载中")
    n = download(pdf_url, dest)
    log(f"  → {dest.name}（{n/1e6:.1f}MB）")
    return dest


def latest_periods_from_cache() -> dict:
    """最新 fin_indicators 缓存 → {a_code: period}。"""
    files = sorted(DATA_DIR.glob("fin_indicators_*.json"))
    if not files:
        return {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    return {b["a_code"]: b["period"] for b in data.get("banks", []) if b.get("period")}


def main() -> None:
    parser = argparse.ArgumentParser(description="银行定期报告 PDF 下载 → data/reports/")
    parser.add_argument("--code", default=None, help="A股代码，如 601398")
    parser.add_argument("--period", default=None, help="如 2026中报 / 2025年报；缺省取缓存最新报告期")
    parser.add_argument("--all", action="store_true", help="12 家 × 各自最新报告期")
    parser.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    watch = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))["stocks"]

    if args.all:
        periods = latest_periods_from_cache()
        for st in watch:
            per = periods.get(st["a_code"])
            if not per:
                MISSING.append(f"{st['name']}({st['a_code']}) 缓存无最新报告期")
                continue
            try:
                fetch_one(st["name"], st["a_code"], per, args.force)
            except Exception as e:  # noqa: BLE001
                MISSING.append(f"{st['name']} {per} 失败: {e}")
                log(f"  ⚠️ {st['name']} {per} 失败: {e}")
            time.sleep(EM_SLEEP)
    elif args.code:
        st = next((s for s in watch if s["a_code"] == args.code), None)
        if not st:
            avail = "、".join(s["a_code"] for s in watch)
            raise SystemExit(f"watchlist 无 {args.code}，可用: {avail}")
        period = args.period or latest_periods_from_cache().get(args.code)
        if not period:
            raise SystemExit("无 --period 且 fin_indicators 缓存无该行报告期")
        fetch_one(st["name"], args.code, period, args.force)
    else:
        parser.error("需要 --code 或 --all")

    if MISSING:
        print(f"失败 {len(MISSING)} 条:")
        for m in MISSING:
            print(f"  - {m}")
    else:
        print("全部成功。")


if __name__ == "__main__":
    main()
