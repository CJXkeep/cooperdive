"""股票页：铜陵有色 K 线/收盘、PB 分位、相对强弱、同行对比。"""

from __future__ import annotations

import streamlit as st

from copper import config
from copper.dashboard import components, data


def render() -> None:
    st.subheader(f"股票 · {config.STOCK_MAIN_NAME}（000630）")
    components.freshness_banner()

    stock = data.load_series("stock_000630", "close")
    stock_v, stock_d = data.chg(stock)
    pb = data.load_pb()
    pb_v = float(pb["pb"].iloc[-1]) if not pb.empty and pb["pb"].notna().sum() else None
    pctl_v = float(pb["pctl"].iloc[-1]) if not pb.empty and pb["pctl"].notna().sum() else None

    components.kpi_row([
        {"label": "收盘价(前复权)", "value": f"{stock_v:.2f}" if stock_v else None, "delta_pct": stock_d},
        {"label": "PB", "value": f"{pb_v:.2f}" if pb_v else None, "help": "不复权价 / 最新报告期每股净资产"},
        {"label": "PB 五年分位", "value": f"{pctl_v:.0f}%" if pctl_v is not None else None,
         "help": "当前 PB 在自窗口起点以来历史中的百分位"},
    ])

    if not stock.empty:
        components.show_chart(components.line_chart(stock.to_frame(), "铜陵有色收盘（前复权，元）", "元", height=340),
                        width="stretch")
    else:
        components.empty_hint("股票数据待采集。")

    left, right = st.columns(2)
    with left:
        if not pb.empty:
            components.show_chart(components.line_chart(pb[["pb"]], "市净率 PB", "倍", height=320),
                            width="stretch")
            components.show_chart(components.line_chart(pb[["pctl"]], "PB 历史分位（%）", "%", height=260),
                            width="stretch")
        else:
            components.empty_hint("PB 数据待采集（依赖财务指标接口）。")
    with right:
        rs = data.load_relative_strength()
        if not rs.empty:
            components.show_chart(components.line_chart(rs, "相对沪铜强弱（首日=100）", "指数", height=320),
                            width="stretch")
        peers = data.load_peers()
        if not peers.empty:
            components.show_chart(components.line_chart(peers, "同行股价对比（归一化，首日=100）", "指数", height=320),
                            width="stretch")
        else:
            components.empty_hint("同行对比数据待采集。")
    st.caption("同行口径：江西铜业/云南铜业为纯冶炼对照，紫金矿业为矿山股对照——铜价上行期矿山股通常更强。")
