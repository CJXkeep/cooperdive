"""回测事件定义（06 §4.6；M4 只做 2020，M5 扩到三事件）。

`anchor` 为事件锚点（危机显性化的交易日），用于验收「状态是否在锚点后 N 个交易日内出现」。
`warmup_days` 保证回测窗口前有 ≥120 个交易日的指标预热（06 §4.6 + D-17）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    name: str
    start: str          # 回测窗口起点
    end: str            # 回测窗口终点
    anchor: str         # 事件锚点（验收用）
    note: str


EVENTS: dict[str, Event] = {
    "2020": Event(
        name="2020_covid",
        start="2020-01-10",
        end="2020-12-31",
        anchor="2020-02-03",     # 春节后首个交易日，跳空大跌
        note="新冠疫情：节后跳空 + 3 月全球流动性冲击；运量同比转负（D4）+ K3 破位",
    ),
}


def get_event(key: str) -> Event:
    """按键取事件（键即 CLI 的 `--event`）。"""
    if key not in EVENTS:
        raise KeyError(f"未知事件 {key!r}，可选：{', '.join(EVENTS)}")
    return EVENTS[key]
