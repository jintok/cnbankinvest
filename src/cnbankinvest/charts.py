#!/usr/bin/env python3
"""图表模块：```chart 围栏的 JSON spec → 内联 SVG。纯标准库、零 JS、零外部资产。

两部分：
- 渲染器（gen_site 调用）：render_chart(spec) / fence(spec)；
  支持三种图：line 多序列折线（归一化走势）、bar 横向 ±条形、grouped_bar 分组柱状。
- spec 构建辅助（报告生成器调用）：hist_close_series 读 data/hist/*.json 日线缓存，
  relative_line_spec 对齐多标的日期序列并归一化（起点=100）。

安全约定：spec 虽由自家生成器产出（非外部文本），渲染时仍对全部文本 html.escape、
数值 float() 强转；非法 spec 抛 ValueError，由 gen_site 捕获后降级为 JSON 代码块。
"""
import html
import json
import math

from cnbankinvest.paths import DATA_DIR

PALETTE = ["#58a6ff", "#3fb950", "#e3b341", "#bc8cff", "#f78166", "#39c5cf"]
POS_COLOR = "#f85149"  # A股习惯：红涨
NEG_COLOR = "#3fb950"  # 绿跌
GRID_COLOR = "#21262d"
AXIS_COLOR = "#30363d"
TXT_DIM = "#8b949e"
TXT_MAIN = "#e6edf3"
FONT_STACK = ("-apple-system,'Segoe UI','PingFang SC','Hiragino Sans GB',"
              "'Microsoft YaHei',sans-serif")


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _num(v):
    """转有限浮点；None/非法 → None（折线断点 / 柱子缺省）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _nd(lo: float, hi: float) -> int:
    return 1 if (hi - lo) < 8 else 0


def _legend(p: list, series, y_swatch: float = 32, x0: float = 48) -> None:
    lx = x0
    for i, name in enumerate(series):
        color = PALETTE[i % len(PALETTE)]
        p.append(f'<rect x="{lx:.0f}" y="{y_swatch:.0f}" width="12" height="3" fill="{color}"/>')
        p.append(f'<text x="{lx + 16:.0f}" y="{y_swatch + 5:.0f}" fill="{TXT_DIM}" '
                 f'font-size="11">{_esc(name)}</text>')
        lx += 16 + len(name) * 11 + 18


# ---------------------------------------------------------------- 折线（归一化走势）

def _render_line(spec: dict) -> str:
    x = [str(v) for v in (spec.get("x") or [])]
    raw = spec.get("series") or []
    if not x or not raw:
        raise ValueError("line 图需要非空 x 与 series")
    series = []
    for s in raw:
        vals = [_num(v) for v in (s.get("vals") or [])]
        if len(vals) != len(x):
            raise ValueError(f"序列 {s.get('name', '?')} 点数与 x 不一致")
        series.append((str(s.get("name", "")), vals))
    allv = [v for _, vals in series for v in vals if v is not None]
    if not allv:
        raise ValueError("line 图无有效数值")
    base = _num(spec.get("base"))
    if base is not None:
        allv.append(base)
    lo, hi = min(allv), max(allv)
    if lo == hi:
        lo, hi = lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad

    W, H = 760, 320
    pl, pr, pt, pb = 48, 14, 54, 28
    iw, ih = W - pl - pr, H - pt - pb
    n, nd = len(x), _nd(lo, hi)

    def X(k):
        return pl + iw * k / (n - 1)

    def Y(v):
        return pt + ih * (1 - (v - lo) / (hi - lo))

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="{FONT_STACK}" role="img">',
         f'<text x="{pl}" y="20" fill="{TXT_MAIN}" font-size="13" '
         f'font-weight="600">{_esc(spec.get("title", ""))}</text>']
    _legend(p, [name for name, _ in series], x0=pl)
    for g in range(5):
        v = lo + (hi - lo) * g / 4
        y = Y(v)
        p.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W - pr}" y2="{y:.1f}" '
                 f'stroke="{GRID_COLOR}"/>')
        p.append(f'<text x="{pl - 6}" y="{y + 3.5:.1f}" fill="{TXT_DIM}" font-size="10" '
                 f'text-anchor="end">{v:.{nd}f}</text>')
    for k in sorted({round(kk * (n - 1) / 5) for kk in range(6)}):
        lbl = x[k]
        lbl = lbl[5:] if len(lbl) > 5 else lbl  # MM-DD
        p.append(f'<text x="{X(k):.1f}" y="{H - 8}" fill="{TXT_DIM}" font-size="10" '
                 f'text-anchor="middle">{_esc(lbl)}</text>')
    if base is not None:
        p.append(f'<line x1="{pl}" y1="{Y(base):.1f}" x2="{W - pr}" y2="{Y(base):.1f}" '
                 f'stroke="{AXIS_COLOR}" stroke-dasharray="4 3"/>')
    for i, (name, vals) in enumerate(series):
        color = PALETTE[i % len(PALETTE)]
        segs, cur = [], []
        for k, v in enumerate(vals):
            if v is None:
                if len(cur) > 1:
                    segs.append(cur)
                cur = []
            else:
                cur.append(f"{X(k):.1f},{Y(v):.1f}")
        if len(cur) > 1:
            segs.append(cur)
        for seg in segs:
            p.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.8" '
                     f'points="{" ".join(seg)}"><title>{_esc(name)}</title></polyline>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------- 横向 ±条形

def _render_bar(spec: dict) -> str:
    items = []
    for it in spec.get("items") or []:
        v = _num(it.get("value"))
        if v is not None:
            items.append((str(it.get("name", "")), v))
    if not items:
        raise ValueError("bar 图需要非空 items")
    items.sort(key=lambda t: t[1], reverse=True)
    W = 760
    pl, pr, pt, pb, gap, bh = 96, 60, 44, 10, 7, 16
    n = len(items)
    H = pt + pb + n * (bh + gap)
    iw = W - pl - pr
    vmin = min(0.0, min(v for _, v in items))
    vmax = max(0.0, max(v for _, v in items))
    if vmin == vmax:
        vmax = vmin + 1.0

    def X(v):
        return pl + iw * (v - vmin) / (vmax - vmin)

    x0 = X(0)
    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="{FONT_STACK}" role="img">',
         f'<text x="{pl}" y="20" fill="{TXT_MAIN}" font-size="13" '
         f'font-weight="600">{_esc(spec.get("title", ""))}</text>']
    for v in (vmin, vmax):
        if abs(v) > 1e-9:
            p.append(f'<line x1="{X(v):.1f}" y1="{pt - 6}" x2="{X(v):.1f}" y2="{H - pb}" '
                     f'stroke="{GRID_COLOR}"/>')
    p.append(f'<line x1="{x0:.1f}" y1="{pt - 6}" x2="{x0:.1f}" y2="{H - pb}" '
             f'stroke="{AXIS_COLOR}" stroke-width="1.5"/>')
    for i, (name, v) in enumerate(items):
        y = pt + i * (bh + gap)
        color = POS_COLOR if v >= 0 else NEG_COLOR
        xa, xb = sorted((x0, X(v)))
        p.append(f'<rect x="{xa:.1f}" y="{y}" width="{max(xb - xa, 1.0):.1f}" height="{bh}" '
                 f'fill="{color}" rx="2"><title>{_esc(name)} {v:+.2f}</title></rect>')
        p.append(f'<text x="{pl - 8}" y="{y + bh / 2 + 3.5:.1f}" fill="{TXT_DIM}" '
                 f'font-size="11" text-anchor="end">{_esc(name)}</text>')
        if v >= 0:
            p.append(f'<text x="{xb + 5:.1f}" y="{y + bh / 2 + 3.5:.1f}" fill="{TXT_DIM}" '
                     f'font-size="10">{v:+.2f}</text>')
        else:  # 负值标签放在零轴右侧（该行右侧为空白区，避免与名称重叠）
            p.append(f'<text x="{x0 + 5:.1f}" y="{y + bh / 2 + 3.5:.1f}" fill="{TXT_DIM}" '
                     f'font-size="10">{v:+.2f}</text>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------- 分组柱状

def _render_grouped_bar(spec: dict) -> str:
    x = [str(v) for v in (spec.get("x") or [])]
    raw = spec.get("series") or []
    if not x or not raw:
        raise ValueError("grouped_bar 图需要非空 x 与 series")
    series = []
    for s in raw:
        vals = [_num(v) for v in (s.get("vals") or [])]
        if len(vals) != len(x):
            raise ValueError(f"序列 {s.get('name', '?')} 点数与 x 不一致")
        series.append((str(s.get("name", "")), vals))
    allv = [v for _, vals in series for v in vals if v is not None]
    if not allv:
        raise ValueError("grouped_bar 图无有效数值")
    lo = min(0.0, min(allv))
    hi = max(0.0, max(allv))
    if lo == hi:
        hi = lo + 1.0
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad

    W, H = 760, 320
    pl, pr, pt, pb = 48, 14, 54, 30
    iw, ih = W - pl - pr, H - pt - pb
    m, nc, nd = len(series), len(x), _nd(lo, hi)
    catw = iw / nc
    barw = min(26.0, catw * 0.66 / m)

    def Y(v):
        return pt + ih * (1 - (v - lo) / (hi - lo))

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="{FONT_STACK}" role="img">',
         f'<text x="{pl}" y="20" fill="{TXT_MAIN}" font-size="13" '
         f'font-weight="600">{_esc(spec.get("title", ""))}</text>']
    _legend(p, [name for name, _ in series], x0=pl)
    for g in range(5):
        v = lo + (hi - lo) * g / 4
        y = Y(v)
        p.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{W - pr}" y2="{y:.1f}" '
                 f'stroke="{GRID_COLOR}"/>')
        p.append(f'<text x="{pl - 6}" y="{y + 3.5:.1f}" fill="{TXT_DIM}" font-size="10" '
                 f'text-anchor="end">{v:.{nd}f}</text>')
    p.append(f'<line x1="{pl}" y1="{Y(0):.1f}" x2="{W - pr}" y2="{Y(0):.1f}" '
             f'stroke="{AXIS_COLOR}" stroke-width="1.5"/>')
    for ci, cat in enumerate(x):
        cx = pl + catw * ci + catw / 2
        x0g = cx - barw * m / 2
        for si, (name, vals) in enumerate(series):
            v = vals[ci]
            if v is None:
                continue
            color = PALETTE[si % len(PALETTE)]
            top = min(Y(v), Y(0))
            hgt = abs(Y(v) - Y(0))
            bx = x0g + si * barw
            p.append(f'<rect x="{bx:.1f}" y="{top:.1f}" width="{barw:.1f}" height="{hgt:.1f}" '
                     f'fill="{color}" rx="2"><title>{_esc(cat)} {_esc(name)} {v:+.2f}</title></rect>')
            if nc <= 10:  # 柱顶/柱底数值标签，类目过多时省略
                ly = top - 4 if v >= 0 else top + hgt + 10
                p.append(f'<text x="{bx + barw / 2:.1f}" y="{ly:.1f}" fill="{TXT_DIM}" '
                         f'font-size="9.5" text-anchor="middle">{v:+.1f}</text>')
        p.append(f'<text x="{cx:.1f}" y="{H - 9}" fill="{TXT_DIM}" font-size="10" '
                 f'text-anchor="middle">{_esc(cat)}</text>')
    p.append("</svg>")
    return "".join(p)


# ---------------------------------------------------------------- 对外接口

def render_chart(spec: dict) -> str:
    """spec dict → 内联 SVG 字符串；非法 spec 抛 ValueError。"""
    t = str(spec.get("type", ""))
    if t == "line":
        return _render_line(spec)
    if t == "bar":
        return _render_bar(spec)
    if t == "grouped_bar":
        return _render_grouped_bar(spec)
    raise ValueError(f"不支持的图表类型: {t!r}")


def fence(spec) -> str:
    """spec → ```chart 围栏文本（None/空 → 数据不足提示）。报告生成器用。"""
    if not spec:
        return "（图表数据不足，略）"
    return ("```chart\n"
            + json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
            + "\n```")


# ---------------------------------------------------------------- hist 序列辅助

def hist_close_series(key: str, end_iso: str, window: int | None = None) -> dict:
    """读 data/hist/{key}.json，返回 {date: close}（≤ end_iso，可选尾部窗口）。"""
    p = DATA_DIR / "hist" / f"{key}.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows", [])
    except Exception:  # noqa: BLE001
        return {}
    dmap = {r[0]: r[1] for r in rows if len(r) >= 2}
    dates = sorted(d for d in dmap if d <= end_iso)
    if window:
        dates = dates[-window:]
    return {d: dmap[d] for d in dates}


def relative_line_spec(title: str, entries: list, end_iso: str,
                       window: int = 120, base: float = 100, min_points: int = 30):
    """多标的归一化走势 spec：entries 为 [(hist key, 名称), ...]。

    取各序列日期交集，起点归一为 base；可用标的 <2 或交集样本过少返回 None。
    """
    maps, names = [], []
    for key, name in entries:
        m = hist_close_series(key, end_iso, window)
        if m:
            maps.append(m)
            names.append(name)
    if len(maps) < 2:
        return None
    common = set(maps[0])
    for m in maps[1:]:
        common &= set(m)
    dates = sorted(common)
    if len(dates) < min_points:
        return None
    d0 = dates[0]
    series = [{"name": nm, "vals": [round(m[d] / m[d0] * base, 2) for d in dates]}
              for m, nm in zip(maps, names)]
    if len(dates) > 160:  # 等距抽稀，控制 md 体积
        step = math.ceil(len(dates) / 160)
        idx = list(range(0, len(dates), step))
        if idx[-1] != len(dates) - 1:
            idx.append(len(dates) - 1)
        dates = [dates[i] for i in idx]
        series = [{"name": s["name"], "vals": [s["vals"][i] for i in idx]} for s in series]
    return {"type": "line", "title": title, "x": dates, "series": series,
            "base": base}
