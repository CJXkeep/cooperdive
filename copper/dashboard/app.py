"""Streamlit 看板入口：python -m streamlit run copper/dashboard/app.py"""

from __future__ import annotations

import streamlit as st

from copper import config
from copper.dashboard.views import events, overview, profit, stock

st.set_page_config(
    page_title="铜价追踪 · 铜陵有色",
    page_icon="🟠",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown(
    f"""
    <style>
        div[data-testid="stMetricValue"] {{ font-size: 1.5rem; }}
        div[data-testid="stMetricDelta"] {{ font-size: 0.9rem; }}
        .block-container {{ padding-top: 1.6rem; }}
        /* 隐藏 Streamlit 自带英文界面元素：Deploy 按钮 / 运行状态条 / 主菜单 / 页脚 */
        [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"],
        [data-testid="stDeployButton"], #MainMenu, footer {{ display: none !important; }}
        [data-testid="stHeader"] {{ height: 0.6rem; }}
    </style>
    """,
    unsafe_allow_html=True,
)

pages = {
    "总览": overview.render,
    "利润": profit.render,
    "股票": stock.render,
    "事件": events.render,
}

with st.sidebar:
    st.title("🟠 铜价追踪")
    st.caption(f"{config.STOCK_MAIN_NAME}（000630）每日看板")
    st.divider()
    choice = st.radio("页面", list(pages.keys()), label_visibility="collapsed")
    st.divider()
    st.caption(
        "数据源：新浪财经 / 东方财富 / 99期货 / 长江有色\n\n"
        "口径：沪铜=日盘收盘；LME=美元/吨；COMEX 折算美元/吨\n\n"
        "利润指数为趋势代理，非精确利润。"
    )

pages[choice]()
