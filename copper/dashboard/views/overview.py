"""总览页：铜价三市场 + 股票相对强弱 + 利润指数 + 库存。"""

from __future__ import annotations

import streamlit as st

from copper import config
from copper.dashboard import components, data


def render() -> None:
    st.subheader("总览 · 铜价与铜陵有色")
    components.freshness_banner()

    events = data.events_on_chart(data.load_events()) if st.toggle("在价格图上叠加事件", value=True) else []

    # ---- KPI ----
    shfe = data.load_series("cu_shfe", "close")
    lme = data.load_series("cu_lme", "close")
    comex = data.load_series("cu_comex", "close_usd_ton")
    stock = data.load_series("stock_000630", "close")
    fx = data.load_series("usdcny", "usdcny")

    shfe_v, shfe_d = data.chg(shfe)
    lme_v, lme_d = data.chg(lme)
    comex_v, comex_d = data.chg(comex)
    stock_v, stock_d = data.chg(stock)
    fx_v, _ = data.chg(fx)

    components.kpi_row([
        {"label": "沪铜主力", "value": f"{shfe_v:,.0f}" if shfe_v else None, "delta_pct": shfe_d, "help": "元/吨，日盘收盘"},
        {"label": "LME 铜", "value": f"{lme_v:,.0f}" if lme_v else None, "delta_pct": lme_d, "help": "美元/吨"},
        {"label": "COMEX 铜(折算)", "value": f"{comex_v:,.0f}" if comex_v else None, "delta_pct": comex_d, "help": "美分/磅 → 美元/吨"},
        {"label": config.STOCK_MAIN_NAME, "value": f"{stock_v:.2f}" if stock_v else None, "delta_pct": stock_d, "help": "前复权 元"},
        {"label": "USDCNY", "value": f"{fx_v:.4f}" if fx_v else None, "help": "中行折算价"},
    ])

    # ---- 三市场铜价 ----
    markets = data.load_markets_usd()
    if not markets.empty:
        components.show_chart(components.line_chart(markets, "三市场铜价（统一折算 美元/吨）", "美元/吨", events),
                        width="stretch")
        st.caption("沪铜报价含 13% 增值税，绝对水平高于 LME 属正常；重点看三条线的趋势与拐点是否背离。")
    else:
        components.empty_hint("铜价数据尚未采集，请点击右上角「立即更新数据」。")

    left, right = st.columns(2)
    with left:
        rs = data.load_relative_strength()
        if not rs.empty:
            components.show_chart(components.line_chart(rs, f"{config.STOCK_MAIN_NAME} 相对沪铜强弱（首日=100）", "指数", height=330),
                            width="stretch")
            st.caption("比值走低：跌幅超过行业贝塔，或有个股因素；走高：强于铜价。")
        else:
            components.empty_hint("相对强弱数据待采集。")
    with right:
        inv = pd_concat_inventories()
        if not inv.empty:
            components.show_chart(components.line_chart(inv, "铜库存（LME 日度 / 上期所 周度）", "吨", height=330),
                            width="stretch")
            st.caption("库存去化通常领先或同步于铜价走强；关注拐点而非绝对值。")
        else:
            components.empty_hint("库存数据待采集。")

    pi = data.load_profit_index()
    if not pi.empty and pi["index"].notna().sum() > 5:
        components.show_chart(components.line_chart(pi[["index"]], "冶炼利润代理指数（首日=100，含 TC 与副产硫酸假设）", "指数", events, height=300),
                        width="stretch")
        st.caption("指数为趋势参考：TC 沿用新闻稿口径（稀疏观测前向填充）。详见「利润」页说明。")
    else:
        components.empty_hint("利润代理指数正在积累数据（TC 依赖《铜日报》文章，从部署日起每日记录）。")


def pd_concat_inventories() -> "pd.DataFrame":
    import pandas as pd

    lme = data.load_series("inv_lme", "lme_stock")
    shfe = data.load_series("inv_shfe", "shfe_stock")
    if lme.empty and shfe.empty:
        return pd.DataFrame()
    return pd.DataFrame({"LME库存": lme, "上期所库存": shfe})
