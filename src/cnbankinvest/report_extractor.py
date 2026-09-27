#!/usr/bin/env python3
"""银行定期报告 PDF 指标提取器（pdfplumber，锚点正则 + 众数取值）。

策略：
- 默认指标模式（label 正则）内置，`src/cnbankinvest/report_anchors/{code}.json`
  可按行覆盖（patterns / page_range）——各行报告版式不同时的「每行 reader」配置。
- 逐页提取文本，收集「label 后最近数字」的全部命中，取**众数**为该指标值
  （银行报告同一指标在摘要/正文/附注重复出现，众数天然抗版式噪声）。
- --code 提供时，与最新 fin_indicators 缓存交叉验证并输出差异。

用法：
    .venv/bin/python -m cnbankinvest.report_extractor --pdf data/reports/601398_2026中报.pdf [--code 601398]

输出：data/reports/extracts/{pdf名}.json + stdout 摘要。
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from cnbankinvest.paths import DATA_DIR

EXTRACTS_DIR = DATA_DIR / "reports" / "extracts"
ANCHOR_DIR = Path(__file__).resolve().parent / "report_anchors"
DEFAULT_PAGE_RANGE = (1, 60)   # 指标集中于摘要/主要会计数据/管理层讨论，前 60 页足够

NUM = r"(\d[\d,]*\.?\d*)"      # 数字（含千分位）
# label → (可选脚注号（N）) → 短非数字间隔（不含 %，防止串到前一百分比）→ 数字
GAP = rf"(?:[（(]\d+[)）])?[^\d\-+％%]{{0,20}}"
DEFAULT_PATTERNS = {
    "净息差": rf"(?:净利息收益率|净息差){GAP}{NUM}",
    "净利差": rf"(?:净利息差|净利差){GAP}{NUM}",
    "不良贷款率": rf"不良贷款率{GAP}{NUM}",
    "拨备覆盖率": rf"拨备覆盖率{GAP}{NUM}",
    "拨贷比": rf"拨贷比{GAP}{NUM}",
    "核心一级资本充足率": rf"核心一级资本充足率{GAP}{NUM}",
    "一级资本充足率": rf"(?<!核心)一级资本充足率{GAP}{NUM}",
    "资本充足率": rf"(?<!一级)(?<!核心一级)资本充足率{GAP}{NUM}",
    "杠杆率": rf"(?<!一级)杠杆率{GAP}{NUM}",
    "成本收入比": rf"成本收入比{GAP}{NUM}",
    "贷存比": rf"贷存比{GAP}{NUM}",
}


def load_anchor_overrides(code: str | None) -> dict:
    if not code:
        return {}
    f = ANCHOR_DIR / f"{code}.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def collect_hits(pdf, patterns: dict, page_range) -> dict:
    """逐页扫描，返回 {label: [ (value, page), ... ]}。"""
    hits = {k: [] for k in patterns}
    p0, p1 = page_range
    for i, page in enumerate(pdf.pages, start=1):
        if i < p0 or i > p1:
            continue
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 个别页解析失败跳过
            continue
        for label, pat in patterns.items():
            for m in re.finditer(pat, text):
                raw = m.group(1).replace(",", "")
                try:
                    hits[label].append((float(raw), i))
                except ValueError:
                    continue
    return hits


def mode_value(vals):
    """众数优先；并列过多（离散噪声，如监管红线/多口径混入）时退回首现值
    （最早页 = 摘要/主要会计数据表，为法定口径）。"""
    if not vals:
        return None
    cnt = Counter(vals)
    top = max(cnt.values())
    winners = [v for v, c in cnt.items() if c == top]
    if len(winners) == 1:
        return winners[0]
    if len(winners) > 3:        # 过于离散 → 首现值
        return vals[0]
    return winners[0]


def extract(pdf_path: Path, code: str | None):
    import pdfplumber

    overrides = load_anchor_overrides(code)
    patterns = dict(DEFAULT_PATTERNS)
    patterns.update({k: re.compile(v) for k, v in (overrides.get("patterns") or {}).items()})
    page_range = overrides.get("page_range") or list(DEFAULT_PAGE_RANGE)

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        hits = collect_hits(pdf, patterns, page_range)

    metrics = {}
    for label, found in hits.items():
        if not found:
            continue
        vals = [v for v, _ in found]
        metrics[label] = {
            "value": mode_value(vals),
            "hits": len(vals),
            "pages": sorted({p for _, p in found}),
        }
    return metrics, n_pages, page_range


def cross_check(code: str, metrics: dict) -> dict:
    """与最新 fin_indicators 缓存比对（容差按指标量级）。"""
    files = sorted(DATA_DIR.glob("fin_indicators_*.json"))
    if not files:
        return {}
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    rec = next((b for b in data.get("banks", []) if b.get("a_code") == code), None)
    if not rec:
        return {}
    pairs = {  # pdf 指标 → (缓存字段, 绝对容差)
        "净息差": ("nim_pct", 0.05),
        "不良贷款率": ("npl_ratio_pct", 0.05),
        "拨备覆盖率": ("provision_coverage_pct", 5),
        "核心一级资本充足率": ("cet1_pct", 0.1),
        "一级资本充足率": ("tier1_pct", 0.1),
        "资本充足率": ("car_pct", 0.1),
    }
    out = {}
    for label, (field, tol) in pairs.items():
        pv = metrics.get(label, {}).get("value")
        av = rec.get(field)
        if pv is None or av is None:
            continue
        out[label] = {"pdf": pv, "api": av, "match": abs(pv - av) <= tol}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="银行定期报告 PDF 指标提取")
    parser.add_argument("--pdf", required=True, help="PDF 路径，如 data/reports/601398_2026中报.pdf")
    parser.add_argument("--code", default=None, help="A股代码（启用锚点覆盖 + 交叉验证）")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF 不存在: {pdf_path}")
    code = args.code or re.match(r"(\d{6})_", pdf_path.name).group(1) \
        if re.match(r"(\d{6})_", pdf_path.name) else args.code

    metrics, n_pages, page_range = extract(pdf_path, code)
    check = cross_check(code, metrics) if code else {}

    EXTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    result = {
        "pdf": str(pdf_path),
        "code": code,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pages_total": n_pages,
        "pages_scanned": page_range,
        "metrics": metrics,
        "cross_check_vs_fin_indicators": check,
    }
    out = EXTRACTS_DIR / f"{pdf_path.stem}.json"
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, out)

    print(f"提取完成: {out}")
    for label, m in sorted(metrics.items()):
        pages = ",".join(map(str, m["pages"][:6]))
        val = "未定" if m["value"] is None else m["value"]
        print(f"  {label:<12} {val:>10}  （{m['hits']} 次命中, 页 {pages}）")
    if check:
        bad = [k for k, v in check.items() if not v["match"]]
        print(f"交叉验证: {len(check) - len(bad)}/{len(check)} 一致"
              + (f"，不一致: {bad}" if bad else ""))


if __name__ == "__main__":
    main()
