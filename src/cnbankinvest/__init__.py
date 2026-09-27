"""cnbankinvest — 中国银行板块投研周报系统。

流水线模块（均支持 --date YYYY-MM-DD）：
    data_puller → news_puller → fin_report_analysis → gen_weekly_report / gen_single_report → gen_site
统一入口见 scripts/refresh_weekly.sh。
"""
