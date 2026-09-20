"""利润页：TC/升贴水/基差/硫酸 与 冶炼利润代理指数。"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from copper.dashboard import components, data


def render() -> None:
    st.subheader("利润 · 冶炼端才是这只股票的命门")
    components.freshness_banner()

    with st.expander("📖 为什么铜陵有色不能只看铜价？（先读我）", expanded=False):
        st.markdown(
            f"""
            铜陵有色以**冶炼**为主、矿山自给率有限，利润核心是**加工费（TC/RC）+ 副产品（硫酸）+ 库存损益**：

            - **TC/RC 加工费**：冶炼厂把铜精矿炼成阴极铜收取的加工费（美元/干吨）。铜价涨不等于它赚——
              TC 低甚至为负时（如 2024–2025），铜价越高、冶炼反而可能越难受。
            - **硫酸**：冶炼副产硫酸（约 {3.5:.1f} 吨/吨铜），酸价高时显著增厚利润。
            - **库存损益**：铜价快速上行期，原料与在产品库存带来一次性收益。
            - **升贴水/基差**：现货升水走高 = 现货紧、下游急，是冶炼厂议价能力的风向标。

            **利润代理指数** = TC ×(1/入炉品位) ×汇率 + 硫酸价 ×吨铜副酸量，归一化（首日=100）。
            参数是行业粗略假设，只看趋势方向，不看绝对金额。
            """
        )

    pi = data.load_profit_index()
    if not pi.empty and pi["index"].notna().sum() > 5:
        components.show_chart(components.line_chart(pi[["index"]], "冶炼利润代理指数（首日=100）", "指数", height=340),
                        width="stretch")
        st.caption(f"TC 数据口径：新闻稿稀疏观测，缺失区间前向填充（上限 {45} 天）。"
                   + ("含副产硫酸项。" if bool(pi["with_acid"].iloc[-1]) else "硫酸价暂无数据，当前指数仅含加工费项。"))
    else:
        components.empty_hint("利润指数正在积累 TC 数据（依赖《铜日报》每日文章，从部署日起记录）。"
                              "下面的基差/升贴水数据可以先作为冶炼环境的代理观察。")

    left, right = st.columns(2)
    with left:
        premium = data.load_series("spot_ccmn", "premium")
        if not premium.empty:
            components.show_chart(components.line_chart(premium.to_frame(), "长江现货升贴水（+升水 / −贴水，元/吨）", "元/吨", height=320),
                            width="stretch")
        else:
            components.empty_hint("长江升贴水从部署日起每日积累（来自《铜日报》）。")
    with right:
        basis = data.load_series("spot_basis", "basis_main")
        if not basis.empty:
            components.show_chart(components.line_chart(basis.to_frame(), "上期所基差（现货 − 主力，元/吨）", "元/吨", height=320),
                            width="stretch")
        else:
            components.empty_hint("基差数据待采集。")

    tc = data.load_series("spot_ccmn", "tc")
    if not tc.empty:
        components.show_chart(components.line_chart(tc.to_frame(), "TC 铜精矿加工费（美元/干吨，观测点）", "美元/干吨", height=300),
                        width="stretch")
        st.caption("来源为新闻稿中提及的具体数字，天然稀疏；观测点之间看板以前向填充辅助画线。")

    h2so4 = data.load_series("spot_ccmn", "h2so4")
    if not h2so4.empty:
        components.show_chart(components.line_chart(h2so4.to_frame(), "硫酸价（元/吨，观测点）", "元/吨", height=280),
                        width="stretch")

    spot = data.load_series("spot_basis", "spot_price")
    cu = data.load_series("cu_shfe", "close")
    if not spot.empty and not cu.empty:
        df = pd.DataFrame({"现货价": spot, "沪铜主力": cu}).dropna(how="all")
        components.show_chart(components.line_chart(df, "现货价 vs 沪铜主力（元/吨）", "元/吨", height=320),
                        width="stretch")
