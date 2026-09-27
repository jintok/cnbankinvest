"""仓库级路径约定（唯一来源）。

模块内统一用绝对导入（需 `uv pip install -e .` 安装本包，或 PYTHONPATH=src）：

    from cnbankinvest.paths import DATA_DIR, WATCHLIST_PATH

注意：templates/ 随包内走（`Path(__file__).parent / "templates"`），不在此定义。
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]   # 仓库根（src/cnbankinvest/ 上两级）
DATA_DIR = ROOT_DIR / "data"                      # 拉数缓存 + 手工台账（原始快照不入库）
REGULATORY_PATH = DATA_DIR / "regulatory_indicators.json"   # 手工台账：行业监管指标
CURATED_PATH = DATA_DIR / "bank_fundamentals.json"          # 手工台账：个股专项指标
OUTPUT_DIR = ROOT_DIR / "output"                  # 有留存价值的产出（报告/评论，入库）
WEEKLY_DIR = OUTPUT_DIR / "weekly"                # 周报 Markdown
SINGLE_DIR = OUTPUT_DIR / "single"                # 个股报告 Markdown
NEWS_NOTES_DIR = OUTPUT_DIR / "news"              # 人工整理的新闻/事件笔记
BLOG_DIR = OUTPUT_DIR / "blog"                    # 人工观点文章
DOCS_DIR = ROOT_DIR / "docs"                      # GitHub Pages 静态站（Source=/docs）
WATCHLIST_PATH = ROOT_DIR / "watchlist.json"      # 标的唯一来源
METHODOLOGY_DIR = ROOT_DIR / "methodology"        # 方法论版本 spec（v{yyyyMMdd}.md，入库、脚本只读）
