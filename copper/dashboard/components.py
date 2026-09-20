"""看板通用组件：新鲜度横幅、KPI 行、图表辅助。"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from copper.dashboard import data

# 数据集 → 中文短名（新鲜度横幅展示用）
DATASET_LABELS = {
    "bps_000630": "每股净资产",
    "cu_comex": "COMEX铜",
    "cu_lme": "LME铜",
    "cu_shfe": "沪铜",
    "inv_lme": "LME库存",
    "inv_shfe": "上期所库存",
    "spot_basis": "现货基差",
    "spot_ccmn": "长江现货",
    "stock_000630": "铜陵有色",
    "stock_000630_raw": "铜陵不复权",
    "stock_000878": "云南铜业",
    "stock_600362": "江西铜业",
    "stock_601899": "紫金矿业",
    "usd_index": "美元指数",
    "usdcny": "汇率",
}

# 采集分组 → 看板页面（异常时告诉用户影响哪里）
_GROUP_PAGE = {"铜价": "总览", "库存": "总览", "宏观": "总览", "利润": "利润", "股票": "股票"}


def _dataset_page(dataset: str) -> str:
    from copper.collectors import COLLECTOR_MAP

    c = COLLECTOR_MAP.get(dataset)
    return _GROUP_PAGE.get(c.group, "") if c else ""


def _ds_name(dataset: str) -> str:
    return DATASET_LABELS.get(dataset, dataset)


_EVENT_COLORS = ["#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"]


def freshness_banner() -> None:
    """页面顶部：一句话数据状态 + 立即更新按钮。只报异常，正常时不多说一个字。"""
    status = data.freshness_status()
    last = data.last_update_time()
    head = st.columns([0.8, 0.2])
    with head[0]:
        if not status or last is None:
            st.warning("尚未采集过数据：点右侧按钮首采（约 5–10 分钟），或在服务器上运行 `python -m scripts.update_once`。")
        else:
            bad = [s for s in status if s["level"] == "bad"]
            warn = [s for s in status if s["level"] == "warn"]
            if not bad and not warn:
                st.markdown(f"📊 **数据更新至 {last}** · 全部 {len(status)} 个数据源正常")
            else:
                probs = bad + warn
                names = "、".join(
                    f"{_ds_name(p['dataset'])}（{_dataset_page(p['dataset'])}）" for p in probs
                )
                icon = "🔴" if bad else "🟡"
                st.markdown(
                    f"📊 **数据更新至 {last}** · {icon} {len(probs)} 个数据源异常：{names}，其余正常"
                )
            with st.expander("数据源详情", expanded=False):
                rows = [
                    {
                        "数据源": _ds_name(s["dataset"]),
                        "所属页面": _dataset_page(s["dataset"]),
                        "状态": {"good": "✅ 正常", "warn": "🟡 滞后", "bad": "🔴 异常"}[s["level"]],
                        "最近成功": (s["age_days"] is not None and f"{s['age_days']:.0f} 天前") or "从未成功",
                        "备注": s["error"][:60] if s["error"] else "",
                    }
                    for s in status
                ]
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    with head[1]:
        if st.button("🔄 立即更新数据", width="stretch"):
            from copper import pipeline

            with st.spinner("正在采集…（首次约数分钟）"):
                results = pipeline.update_all(only_stale=True)
            ok = sum(1 for r in results if r.ok)
            st.toast(f"采集完成：{ok}/{len(results)} 成功", icon="✅" if ok == len(results) else "⚠️")
            data.load_freshness.clear()
            st.rerun()


def kpi_row(items: list[dict]) -> None:
    """items: [{label, value, delta_pct, unit, help}]"""
    cols = st.columns(len(items))
    for col, it in zip(cols, items):
        delta = None
        if it.get("delta_pct") is not None:
            d = it["delta_pct"]
            delta = f"{d:+.2f}%" if abs(d) < 1000 else "—"
        col.metric(
            it["label"],
            it["value"] if it.get("value") is not None else "—",
            delta=delta,
            help=it.get("help"),
        )


def line_chart(df: pd.DataFrame, title: str, y_title: str, events: list[dict] | None = None,
               height: int = 380, percent: bool = False) -> go.Figure:
    fig = go.Figure()
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            continue
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=str(col), mode="lines",
                                 line={"width": 1.8}))
    _add_events(fig, events, df)
    fig.update_layout(
        title=dict(text=title, font={"size": 15}),
        height=height,
        margin={"l": 10, "r": 10, "t": 42, "b": 10},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hovermode="x unified",
        hoverlabel={"font": {"size": 12}},
        template="plotly_white",
    )
    # 日期刻度中文化：避免 Plotly 默认的英文月份（Sep 2025）
    fig.update_xaxes(
        tickformatstops=[
            dict(dtickrange=[None, 86400000 * 2], value="%m-%d"),
            dict(dtickrange=[86400000 * 2, 86400000 * 60], value="%Y-%m-%d"),
            dict(dtickrange=[86400000 * 60, None], value="%Y-%m"),
        ]
    )
    if percent:
        fig.update_yaxes(ticksuffix="%")
    else:
        fig.update_yaxes(title=y_title)
    fig.update_xaxes(rangeslider={"visible": False})
    return fig


def _add_events(fig: go.Figure, events: list[dict] | None, df: pd.DataFrame) -> None:
    if not events or df.empty:
        return
    x_min, x_max = df.index.min(), df.index.max()
    for i, ev in enumerate(events):
        x = pd.Timestamp(ev["date"])
        if not (x_min <= x <= x_max):
            continue
        color = _EVENT_COLORS[i % len(_EVENT_COLORS)]
        fig.add_vline(x=x, line={"width": 1, "color": color, "dash": "dot"})
        fig.add_annotation(x=x, y=1.02, yref="paper", text=f"📌{ev['title'][:12]}",
                           showarrow=False, font={"size": 9, "color": color},
                           xanchor="left" if i % 2 == 0 else "right")


def show_chart(fig: go.Figure, **kwargs) -> None:
    """统一渲染入口：去掉 Plotly 英文角标，保持看板纯中文观感。"""
    kwargs.setdefault("width", "stretch")
    kwargs.setdefault("config", {"displaylogo": False})
    st.plotly_chart(fig, **kwargs)


def empty_hint(text: str) -> None:
    st.info(text)
