"""方法论版本解析（唯一来源：methodology/v{yyyyMMdd}.md）。

当前版本 = 目录中日期最大者（yyyyMMdd 字典序即时间序）。
目录缺失或无版本文件时降级：current_version() 返回 "未知" 并打印警告，不中断流水线。
"""
import re
from pathlib import Path

from cnbankinvest.paths import METHODOLOGY_DIR

_VERSION_RE = re.compile(r"^v(\d{8})\.md$")


def _versions() -> list[tuple[str, Path]]:
    """返回 [(版本号, 路径)] 按版本号升序。"""
    if not METHODOLOGY_DIR.is_dir():
        return []
    found = []
    for p in METHODOLOGY_DIR.glob("v*.md"):
        m = _VERSION_RE.match(p.name)
        if m:
            found.append((f"v{m.group(1)}", p))
    found.sort(key=lambda t: t[0])
    return found


def current_path() -> Path | None:
    """当前版本文件路径；无版本时返回 None。"""
    vs = _versions()
    return vs[-1][1] if vs else None


def current_version() -> str:
    """当前版本号（如 "v20260927"）；无版本时打印警告并返回 "未知"。"""
    vs = _versions()
    if not vs:
        print("警告：未找到方法论版本文件（methodology/v{yyyyMMdd}.md），版本标注为「未知」")
        return "未知"
    return vs[-1][0]


def current_text() -> str | None:
    """当前版本全文（供 gen_site 渲染方法论页）；无版本时返回 None。"""
    p = current_path()
    return p.read_text(encoding="utf-8") if p else None
