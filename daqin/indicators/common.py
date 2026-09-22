"""指标层公共工具与常量。"""

from __future__ import annotations

import pandas as pd

STALE_DAYS = 30   # 日频外部数据的最长前向填充：源站停更/长时间缺数后不得继续沿用旧值（D-26）


def streak(cond: pd.Series) -> pd.Series:
    """连续满足条件的计数（中断归零）。"""
    grp = (~cond).cumsum()
    return cond.groupby(grp).cumsum()
