"""事件页：手动维护的事件时间线 + 叠加到沪铜价格图。"""

from __future__ import annotations

import streamlit as st

from copper import config
from copper.dashboard import components, data


def render() -> None:
    st.subheader("事件 · 公告 / 检修 / 矿端进展")
    components.freshness_banner()

    events = data.load_events()
    if events.empty:
        st.info(
            f"还没有事件记录。请编辑 `{config.EVENTS_FILE}`，每行一条：\n\n"
            "```markdown\n"
            "- 2026-09-18 | 公告 | 三季度业绩预告发布 | 简要备注\n"
            "- 2026-09-01 | 检修 | 金冠铜业阴极铜系统检修一周 |\n"
            "```"
        )
    else:
        display = events.rename(columns={"date": "日期", "type": "类型", "title": "标题", "detail": "详情"})
        st.dataframe(display, width="stretch", hide_index=True)
        st.caption("编辑方法见上方提示；改完刷新页面即可生效。")

    cu = data.load_series("cu_shfe", "close")
    if not cu.empty and not events.empty:
        ev = data.events_on_chart(events)
        components.show_chart(components.line_chart(cu.to_frame(), "事件叠加在沪铜价格上（近 400 天）", "元/吨", events=ev, height=380),
                        width="stretch")
    elif not events.empty:
        components.empty_hint("沪铜数据待采集，事件暂时无法叠加到价格图。")

    with st.expander("事件来源建议（手动记录哪些东西？）"):
        st.markdown(
            """
            - **公司公告**：业绩预告/快报、增发、回购、大股东变动（巨潮资讯网 000630 页面）
            - **生产端**：冶炼系统检修、投产与爬产（如米拉多铜矿产量节奏）、硫酸装置动态
            - **行业**：CSPT 联合减产、加工费谈判节点、进出口政策/关税
            - **宏观**：美联储议息、国内电网投资数据、地产竣工数据
            """
        )
