"""指标层数据对齐：所有外部数据（宏观 / 周频 / 月频）对齐到 A 股交易日。

防未来函数（D-13）：观测日 d 的值视为 **d + lag_days 个自然日之后才可用**；
对交易日 t，取「可用日 ≤ t」的最后一条观测（ffill 语义），未可得的交易日为 NaN。

实盘与回测**必须**调用本模块（02 §4.3 / D-21），禁止各自实现。
"""

from __future__ import annotations

import pandas as pd


def align_to_trading_days(external: pd.Series, trading_days: pd.DatetimeIndex,
                          lag_days: int = 1) -> pd.Series:
    """外部序列 → 交易日索引。

    - `external`：索引为观测日，值为观测值；
    - `trading_days`：目标交易日索引（A 股）；
    - `lag_days`：观测日到「可用日」之间的自然日滞后。
      FRED 美债为美东晚间发布，次日在 A 股盘前可用 → `lag_days=1`（默认）；
      若数据在观测日当天盘前已可得（如自定义发布时点）→ `lag_days=0`。
    """
    days = pd.DatetimeIndex(pd.to_datetime(trading_days)).normalize()
    if external.empty:
        return pd.Series(index=days, dtype=float, name=external.name)
    obs = external.copy()
    obs.index = pd.DatetimeIndex(pd.to_datetime(obs.index)).normalize() + pd.Timedelta(days=lag_days)
    obs = obs[~obs.index.duplicated(keep="last")].sort_index()
    union = obs.index.union(days)
    return obs.reindex(union).ffill().reindex(days)
