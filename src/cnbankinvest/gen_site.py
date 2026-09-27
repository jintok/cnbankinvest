#!/usr/bin/env python3
"""GitHub Pages 静态站点生成器（银行研究系统）。

把 output/weekly/（板块周报）、output/single/（个股报告）渲染成自包含的离线 HTML 站点：
- 纯标准库实现的最小 Markdown→HTML 渲染器（见 SUPPORTED 注释）；
- 全站统一暗色主题（CSS 内联，无 CDN/外部资产，可离线打开）；
- 剥离内部批注：HTML 注释与【待人工撰写】等人工标记不进入网页版（待填/待补保留）；
- 输出：docs/index.html、docs/methodology.html、docs/reports/weekly/*.html、docs/reports/single/*.html。

用法：
    .venv/bin/python -m cnbankinvest.gen_site [--site-dir docs] [--weekly-dir output/weekly] [--single-dir output/single]

注意：本脚本只读 output/weekly、output/single，方法论页内容以内联 MARKDOWN 维护，
保证每次生成与最新口径同步。目录约定见 cnbankinvest.paths。
"""
import argparse
import html
import re
from datetime import datetime
from pathlib import Path

from cnbankinvest.paths import DOCS_DIR, SINGLE_DIR, WEEKLY_DIR

# ---------------------------------------------------------------- 渲染器支持的语法
# 标题 #..######、管道表（含对齐行）、-/* 无序列表（含 - [ ] 任务列表）、
# 有序列表、> 引用、**粗体**、`代码`、`[文字](链接)`、--- 分隔线、普通段落。
# 限制：列表不嵌套（源文档均为扁平列表）；表格单元格单行（源文档均满足）。

INTERNAL_MARKERS = ("【待人工撰写】", "【待人工修订】", "【人工补充】")

CSS = """
* { box-sizing: border-box; }
body { background:#0d1117; color:#c9d1d9; margin:0; font-family:-apple-system,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif; line-height:1.65; }
nav { background:#161b22; border-bottom:1px solid #30363d; padding:12px 20px; font-size:15px; }
nav a { color:#58a6ff; text-decoration:none; margin-right:18px; }
nav a:hover { text-decoration:underline; }
main { max-width:960px; margin:0 auto; padding:24px 20px 60px; }
h1 { border-bottom:1px solid #30363d; padding-bottom:10px; font-size:26px; }
h2 { border-bottom:1px solid #21262d; padding-bottom:6px; margin-top:36px; font-size:21px; }
h3 { font-size:17px; color:#c9d1d9; }
a { color:#58a6ff; }
strong { color:#e6edf3; }
code { background:#161b22; border:1px solid #30363d; border-radius:6px; padding:2px 6px; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:0.9em; }
blockquote { background:#161b22; border-left:4px solid #30363d; margin:12px 0; padding:8px 16px; color:#8b949e; }
table { border-collapse:collapse; margin:14px 0; width:100%; font-size:14px; }
th, td { border:1px solid #30363d; padding:7px 12px; text-align:left; }
th { background:#161b22; color:#e6edf3; white-space:nowrap; }
tr:nth-child(even) td { background:rgba(110,118,129,0.08); }
hr { border:none; border-top:1px solid #30363d; margin:28px 0; }
ul, ol { padding-left:24px; }
li { margin:4px 0; }
footer { border-top:1px solid #30363d; color:#8b949e; font-size:13px; text-align:center; padding:20px; }
"""


# ---------------------------------------------------------------- Markdown → HTML

def strip_internal(text: str) -> str:
    """剥离内部批注：HTML 注释 + 人工标记（网页版不展示）。"""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    for m in INTERNAL_MARKERS:
        text = text.replace(m, "")
    return text


def inline(text: str) -> str:
    """行内语法：先转义 HTML，再处理 code/bold/link。"""
    s = html.escape(text, quote=False)
    codes = []

    def code_sub(m):
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", code_sub, s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)
    return s


def render_table(rows: list) -> str:
    def cells(r):
        return [c.strip() for c in r.strip().strip("|").split("|")]

    header = cells(rows[0])
    body = [cells(r) for r in rows[1:]
            if not re.match(r"^\|[\s:\-|]+\|$", r)]  # 跳过对齐行
    out = ["<table><thead><tr>" + "".join(f"<th>{inline(h)}</th>" for h in header) + "</tr></thead><tbody>"]
    for r in body:
        out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def md_to_html(md: str) -> str:
    """块级渲染：标题/表格/列表/引用/分隔线/段落（见文件头 SUPPORTED）。"""
    lines = strip_internal(md).split("\n")
    parts, para, i, n = [], [], 0, len(lines)

    def flush_para():
        if para:
            parts.append("<p>" + "<br>\n".join(inline(x) for x in para) + "</p>")
            para.clear()

    while i < n:
        line = lines[i]
        if not line.strip():
            flush_para()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_para()
            lv = len(m.group(1))
            parts.append(f"<h{lv}>{inline(m.group(2))}</h{lv}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,})\s*$", line):
            flush_para()
            parts.append("<hr>")
            i += 1
            continue
        if line.startswith("|"):
            flush_para()
            tbl = []
            while i < n and lines[i].startswith("|"):
                tbl.append(lines[i])
                i += 1
            parts.append(render_table(tbl))
            continue
        if line.startswith(">"):
            flush_para()
            q = []
            while i < n and lines[i].startswith(">"):
                q.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            parts.append("<blockquote>" + "<br>\n".join(inline(x) for x in q) + "</blockquote>")
            continue
        if re.match(r"^[-*]\s+", line):
            flush_para()
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i]):
                items.append(re.sub(r"^[-*]\s+", "", lines[i]))
                i += 1
            lis = []
            for it in items:
                cm = re.match(r"^\[([ xX])\]\s+(.*)$", it.strip())
                if cm:
                    it = ("☑ " if cm.group(1).lower() == "x" else "☐ ") + cm.group(2)
                lis.append(f"<li>{inline(it)}</li>")
            parts.append("<ul>" + "".join(lis) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", line):
            flush_para()
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i]))
                i += 1
            parts.append("<ol>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ol>")
            continue
        para.append(line)
        i += 1
    flush_para()
    return "\n".join(parts)


# ---------------------------------------------------------------- 页面包装

def page(title: str, body: str, prefix: str, nav_weekly: str, nav_single: str,
         gen_time: str) -> str:
    nav = (f'<nav><a href="{prefix}index.html">首页</a> · '
           f'<a href="{prefix}methodology.html">方法论</a> · '
           f'<a href="{prefix}{nav_weekly}">周报</a> · '
           f'<a href="{prefix}{nav_single}">个股报告</a></nav>')
    footer = (f"<footer>生成时间：{html.escape(gen_time)} ｜ 个人研究用途，数据来自公开接口，"
              f"不构成投资建议</footer>")
    return (f"<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{html.escape(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
            f"{nav}\n<main>\n{body}\n</main>\n{footer}\n</body>\n</html>\n")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def md_title(md_text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", strip_internal(md_text), flags=re.M)
    return m.group(1).strip() if m else fallback


# ---------------------------------------------------------------- 方法论页（内联维护）

METHODOLOGY_MD = """# 研究方法与数据口径

## 研究范围

- **覆盖标的池**：12 家上市银行（国有大行 6：工农中建交邮储；股份行 3：招商/兴业/平安；城商行 3：宁波/江苏/成都），其中 7 家为 A+H 双重上市。
- **基准指数**：沪深300（000300，基准）、中证银行（399986）、申万银行（801780）、中证红利（000922，红利风格）、中证银行AH价格优选（931039，A/H 因子）。
- **产出**：每周板块周报（正文 3 分钟决策摘要 + 附录明细）与单只个股深度报告。

## 周报结构

- **正文（一至九节）**：核心观点（人工）、一周速览、本周变化、观点初稿（短/长期，自动）、A/H 因子、事件与政策、基本面摘要、下周关注、风险提示。
- **附录（备查明细）**：附1 个股行情估值、附2 个股基本面台账、附3 公告与快讯、附4 行业监管指标与本周待办。

## 指标口径

| 指标 | 口径 |
|---|---|
| 周涨跌 | 最新收盘（≤数据截止日的最后交易日）对比 7 个自然日前收盘，% |
| 年初至今 | 对比上一年最后交易日收盘，% |
| 股息率 TTM | 近 365 天除权除息现金分红（元/10股÷10）÷ 现价，%（自算口径） |
| 利差 | 核心池 12 家股息率中位数 − 10Y 国债收益率，pct |
| AH 溢价 | (H股收盘价×CNY/HKD − A股收盘价) ÷ A股收盘价，负值=H股折价 |
| 南向资金 | 港股通近 5 个交易日「当日成交净买额」合计，亿港元 |
| PB / PE-TTM | 东财 datacenter-web 日频估值，取 ≤截止日最新一日 |
| 营收/净利/ROE | 同花顺按报告期（累计值口径，ROE 为单季度） |
| 净息差/不良率/拨备/资本 | 手工台账：金融监管总局季度通报 + 各银行定期报告 |

## 数据源与可用性

- **主源**：新浪（A/H 个股日线、汇率牌价）、中证指数官网（000300/399986/000922/931039）、申万宏源（801780）、中国债券信息网（10Y 国债）、同花顺（财务摘要）、东方财富 datacenter-web（估值/公告，带重试）。
- **受限**：东财行情接口（push2/push2his）在部分网络环境（HTTP 代理）下不可达，不使用；新闻快讯接口仅覆盖最近 24–48 小时。

## 手工维护台账

- `data/regulatory_indicators.json`：行业监管指标（净息差/不良率/拨备覆盖率/资本充足率/利润同比），来源为金融监管总局季度《商业银行主要监管指标》通报。
- `data/bank_fundamentals.json`：个股 curated 字段（净息差/不良率/拨备覆盖率/核心一级/分红率），来源为各银行定期报告。
- 两处填报后，周报第七/附录节与「本周待办」清单自动更新。

## 已知限制

- 快讯仅 24–48 小时覆盖，周度事件以公告补齐；财联社接口无 URL。
- 指数级股息率不可得（akshare 无对应接口），指数股息率字段固定为空。
- akshare 无商业银行净息差/不良率自动数据源，仅能手工维护。
- 南向资金周合计为近 5 个交易日口径（非自然周）。
- 10Y 国债取值需过滤「中债国债收益率曲线」（同接口另返回中短期票据/商业银行普通债曲线）。
"""

INDEX_INTRO = """## 项目简介

银行业周度研究：覆盖 12 家上市银行（A股 + 其中 7 家 H股）与 5 个基准指数，每周产出
**板块周报**（正文 3 分钟决策摘要 + 附录明细）与**单只个股报告**，并跟踪
**A/H 因子**（中证银行AH价格优选指数 931039 对比中证银行 399986 + 7 家 A+H 溢价率 +
南向资金）。数据全部由 akshare 公开接口自动拉取，手工台账补充监管指标。

## 方法论摘要

- 标的池：12 家银行（国有大行/股份行/城商行）+ 沪深300、中证银行、申万银行、中证红利、中证银行AH优选 5 指数。
- 数据源：新浪/中证指数/申万/中债/同花顺为主源，东财 datacenter-web（估值/公告）带重试，行情接口在部分网络环境下不可用、不用。
- 报告结构：正文（速览/变化/观点初稿/AH 因子/事件/基本面摘要）供快速决策，附录承载全部个股明细。
- 估值体系：PB/PE-TTM（东财）+ 股息率 TTM（近 365 天分红自算）− 10Y 国债利差及历史分位。
- 台账：营收/净利/ROE 自动（同花顺），净息差/不良率/拨备等监管指标手工维护并生成「本周待办」。
- 口径与限制详见 [方法论](methodology.html)。
"""

DISCLAIMER = ("> **免责声明**：本站点为个人研究记录，所有内容基于公开数据接口自动生成，"
              "可能存在延迟、缺漏或口径偏差，不构成任何投资建议。")


# ---------------------------------------------------------------- 构建

def collect(md_dir: Path, pattern: str):
    """收集 md 文件并按文件名日期倒序（无日期的按名称）。"""
    files = sorted(md_dir.glob(pattern))
    def key(p):
        m = re.search(r"(20\d{6}|20\d{2}-\d{2}-\d{2})", p.stem)
        return m.group(1) if m else p.stem
    try:
        files.sort(key=key, reverse=True)
    except TypeError:
        files.sort(key=lambda p: p.stem, reverse=True)
    return files


def build_index_md(weeklies, singles):
    def w_link(f: Path) -> str:  # 根目录视角的周报链接
        return f"reports/weekly/{f.stem}.html"

    def s_link(f: Path) -> str:
        return f"reports/single/{f.stem}.html"

    md = [INDEX_INTRO, "## 最新周报\n"]
    if weeklies:
        title = md_title(weeklies[0].read_text(encoding="utf-8"), weeklies[0].stem)
        date_m = re.search(r"(20\d{2}-\d{2}-\d{2})", weeklies[0].stem)
        date_s = date_m.group(1) if date_m else ""
        md.append(f"- **[{title}]({w_link(weeklies[0])})** — {date_s} ｜ 正文 3 分钟摘要 + 附录明细")
    else:
        md.append("（暂无周报）")
    md.append("\n## 周报归档\n")
    if weeklies:
        for w in weeklies:
            t = md_title(w.read_text(encoding="utf-8"), w.stem)
            md.append(f"- [{t}]({w_link(w)})")
    else:
        md.append("（暂无）")
    md.append("\n## 个股报告\n")
    if singles:
        for s in singles:  # 文件名不含板块信息，按名称平铺
            md.append(f"- [{md_title(s.read_text(encoding='utf-8'), s.stem)}]({s_link(s)})")
    else:
        md.append("（暂无）")
    md.append("\n" + DISCLAIMER)
    return "\n".join(md)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 GitHub Pages 静态站点")
    ap.add_argument("--site-dir", default=None, help="站点输出目录，默认 仓库根/docs")
    ap.add_argument("--weekly-dir", default=None, help="周报目录，默认 output/weekly")
    ap.add_argument("--single-dir", default=None, help="个股报告目录，默认 output/single")
    args, _unknown = ap.parse_known_args()  # 兼容 refresh_weekly.sh 透传的 --date 等参数

    site_dir = Path(args.site_dir) if args.site_dir else DOCS_DIR
    weekly_dir = Path(args.weekly_dir) if args.weekly_dir else WEEKLY_DIR
    single_dir = Path(args.single_dir) if args.single_dir else SINGLE_DIR

    weeklies = collect(weekly_dir, "*.md") if weekly_dir.exists() else []
    singles = collect(single_dir, "*.md") if single_dir.exists() else []
    gen_time = datetime.now().isoformat(timespec="seconds")

    def w_link(f: Path) -> str:  # 根目录视角的周报链接
        return f"reports/weekly/{f.stem}.html"

    def s_link(f: Path) -> str:
        return f"reports/single/{f.stem}.html"

    nav_weekly = w_link(weeklies[0]) if weeklies else "#"
    nav_single = s_link(singles[0]) if singles else "#"

    written = []

    # 报告页（深度 2 前缀 ../../）
    for w in weeklies:
        out = site_dir / "reports" / "weekly" / f"{w.stem}.html"
        text = w.read_text(encoding="utf-8")
        body = md_to_html(text)
        write_atomic(out, page(md_title(text, w.stem), body, "../../", nav_weekly,
                               nav_single, gen_time))
        written.append(out)
    for s in singles:
        out = site_dir / "reports" / "single" / f"{s.stem}.html"
        text = s.read_text(encoding="utf-8")
        body = md_to_html(text)
        write_atomic(out, page(md_title(text, s.stem), body, "../../", nav_weekly,
                               nav_single, gen_time))
        written.append(out)

    # 首页与方法论（深度 0 前缀 ""）
    index_md = build_index_md(weeklies, singles)
    index_out = site_dir / "index.html"
    write_atomic(index_out, page("银行板块研究 · 首页", md_to_html(index_md), "",
                                 nav_weekly, nav_single, gen_time))
    written.append(index_out)

    meth_out = site_dir / "methodology.html"
    write_atomic(meth_out, page("研究方法与数据口径", md_to_html(METHODOLOGY_MD), "",
                                nav_weekly, nav_single, gen_time))
    written.append(meth_out)

    print(f"站点已生成: {site_dir}")
    for p in written:
        print(f"  {p.relative_to(site_dir)} ({p.stat().st_size} B)")


if __name__ == "__main__":
    main()
