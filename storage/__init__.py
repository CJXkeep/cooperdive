"""仓库级存储基座（06 D-20 方案 A′）。

`copper`（长表）与 `daqin`（宽表）保留各自 DDL 与 upsert 逻辑，
只共用「连接管理」与「freshness 读写」——这两块结构完全一致；
按 one home per fact，freshness 的语义只在本包维护。
"""

from storage.sqlite import (
    CN_TZ,
    DATETIME_FMT,
    FRESHNESS_DDL,
    connect,
    get_freshness,
    now_str,
    set_freshness,
)

__all__ = [
    "CN_TZ",
    "DATETIME_FMT",
    "FRESHNESS_DDL",
    "connect",
    "get_freshness",
    "now_str",
    "set_freshness",
]
