#!/usr/bin/env python3
"""银行股每周新闻/事件拉取脚本。

数据源（已在 INTERFACE_NOTES.md 实测通过）：
- 快讯 : ak.stock_info_global_em（东财全球快讯）+ ak.stock_info_global_cls（财联社电报）
- 公告 : ak.stock_notice_report（东财公告大全，按日全市场，symbol=报告类型）

筛选逻辑：
- 快讯按银行关键词正则过滤，每个来源最多保留 15 条，按标题去重
- 公告取目标日期前 7 天内每个工作日（周一至周五）的全市场公告，
  仅保留 watchlist 中 A股代码 的记录，按标题关键词归类

用法：
    .venv/bin/python -m cnbankinvest.news_puller [--date 2026-09-25] [--out data目录]

输出：data/news_YYYY-MM-DD.json
"""
import argparse
import json
import re
import socket
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import akshare as ak

from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

socket.setdefaulttimeout(30)

NOTICE_SLEEP = 1.5         # 公告接口逐日调用间隔（秒），防东财限流
MAX_FLASH_PER_SOURCE = 15
MAX_NOTICES = 60

MISSING = []

# 银行板块关键词（快讯过滤）
BANK_RE = re.compile(
    "银行|商行|央行|人民银行|LPR|降准|降息|存款利率|净息差|息差|不良|拨备|"
    "金融监管|金监总局|社融|信贷|地产|城投|汇金")

# 公告标题归类：(类别, 正则)
NOTICE_CATEGORIES = [
    ("业绩快报", re.compile("业绩快报|业绩预告|年度报告|半年度报告|季度报告|经营情况")),
    ("分红派息", re.compile("分红|派息|利润分配|股息|现金红利")),
    ("增减持", re.compile("增持|减持")),
    ("再融资", re.compile("定增|增发|配股|可转债|可转换|优先股|资本债券|二级资本|永续债|再融资|发行债券")),
    ("监管处罚", re.compile("处罚|警示函|监管函|违规|罚款|立案调查|问责")),
    ("人事变动", re.compile("任职|离职|辞任|辞职|任命|聘任|董事长|行长|副行长|董事变动|高管变动|换届")),
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def note_missing(item: str, exc: Exception) -> None:
    MISSING.append(f"{item}: {type(exc).__name__}: {str(exc)[:120]}")
    log(f"  ⚠️ 失败已记录 [{item}] {type(exc).__name__}: {str(exc)[:100]}")


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def categorize(title: str) -> str:
    for label, rx in NOTICE_CATEGORIES:
        if rx.search(title):
            return label
    return "其他"


# ---------------------------------------------------------------- 快讯

def pull_flash(target: date) -> list:
    """两大快讯源，关键词过滤 + 每源限量 + 标题去重。"""
    window_start = target - timedelta(days=7)
    out, seen = [], set()

    def keep(title: str) -> bool:
        if not BANK_RE.search(title):
            return False
        if title in seen:      # 跨源按标题去重
            return False
        seen.add(title)
        return True

    # 东财全球快讯
    n = 0
    try:
        df = ak.stock_info_global_em()
        for _, r in df.iterrows():
            if n >= MAX_FLASH_PER_SOURCE:
                break
            t = str(r.get("发布时间", ""))
            day = t[:10]
            if day and day < window_start.isoformat():
                continue
            title = str(r.get("标题", ""))
            if keep(title):
                out.append({"source": "东财全球快讯", "time": t, "title": title,
                            "summary": str(r.get("摘要", ""))[:200],
                            "url": r.get("链接") or None})
                n += 1
        log(f"东财快讯: 命中 {n} 条")
    except Exception as e:  # noqa: BLE001
        note_missing("flash:stock_info_global_em", e)
    time.sleep(NOTICE_SLEEP)

    # 财联社电报
    n = 0
    try:
        df = ak.stock_info_global_cls()
        for _, r in df.iterrows():
            if n >= MAX_FLASH_PER_SOURCE:
                break
            day = str(r.get("发布日期", ""))
            if day and day < window_start.isoformat():
                continue
            title = str(r.get("标题", ""))
            if keep(title):
                out.append({"source": "财联社电报",
                            "time": f"{day} {r.get('发布时间', '')}".strip(),
                            "title": title,
                            "summary": str(r.get("内容", ""))[:200],
                            "url": None})  # 财联社接口无 URL 字段
                n += 1
        log(f"财联社电报: 命中 {n} 条")
    except Exception as e:  # noqa: BLE001
        note_missing("flash:stock_info_global_cls", e)

    return out


# ---------------------------------------------------------------- 公告

def pull_notices(target: date, a_codes: set) -> list:
    """过去 7 天每个工作日（周一至周五）的全市场公告，按 watchlist 过滤。"""
    out = []
    for i in range(7):
        d = target - timedelta(days=6 - i)   # 窗口 = (target-6) .. target
        if d.weekday() >= 5:                 # 0=Mon .. 4=Fri
            continue
        try:
            df = ak.stock_notice_report(symbol="全部", date=ymd(d))
            n_day = 0
            for _, r in df.iterrows():
                code = str(r.get("代码", "")).strip()
                if code not in a_codes:
                    continue
                title = str(r.get("公告标题", ""))
                out.append({
                    "code": code,
                    "name": str(r.get("名称", "")),
                    "date": str(r.get("公告日期", ""))[:10],
                    "title": title,
                    "category": categorize(title),
                    "url": r.get("网址") or None,
                })
                n_day += 1
            log(f"公告 {d}（{d:%a}）: watchlist 命中 {n_day} 条")
        except Exception as e:  # noqa: BLE001
            note_missing(f"notice:{ymd(d)}", e)
        if len(out) >= MAX_NOTICES:
            log(f"公告已达上限 {MAX_NOTICES} 条，提前停止")
            break
        time.sleep(NOTICE_SLEEP)
    return out[:MAX_NOTICES]


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="银行股每周新闻/公告")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--out", default=None, help="输出目录，默认 仓库根/data")
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    out_dir = Path(args.out) if args.out else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    watch = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    a_codes = {st["a_code"] for st in watch["stocks"]}
    log(f"目标日期 {target}，窗口 {target - timedelta(days=6)} ~ {target}")

    flash = pull_flash(target)
    notices = pull_notices(target, a_codes)

    result = {
        "meta": {"date": target.isoformat(), "window_days": 7, "missing": MISSING},
        "flash": flash,
        "notices": notices,
    }
    out_file = out_dir / f"news_{target.isoformat()}.json"
    tmp = out_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(out_file)
    log(f"已写出 {out_file}（flash={len(flash)}, notices={len(notices)}, missing={len(MISSING)}）")
    if MISSING:
        for m in MISSING:
            log(f"  missing: {m}")


if __name__ == "__main__":
    main()
