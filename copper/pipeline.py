"""采集流程编排：跑所有采集器，逐个记录 freshness，单个失败不影响其余。"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import timedelta

from copper import collectors, config, db


@dataclass
class CollectResult:
    name: str
    group: str
    desc: str
    ok: bool
    rows: int
    error: str
    secs: float


def _is_fresh(conn: sqlite3.Connection, dataset: str, within_hours: float) -> bool:
    fr = db.get_freshness(conn)
    row = fr[fr["dataset"] == dataset]
    if row.empty:
        return False
    ls = row.iloc[0]["last_success"]
    if not ls:
        return False
    from datetime import datetime

    dt = datetime.strptime(ls, config.DATETIME_FMT).replace(tzinfo=config.TZ)
    age = datetime.now(config.TZ) - dt
    return age <= timedelta(hours=within_hours)


def update_all(only_stale: bool = False, stale_hours: float = 6.0,
               names: list[str] | None = None) -> list[CollectResult]:
    """跑一遍全部采集器。

    only_stale: 跳过 stale_hours 内已成功过的数据集（看板"立即更新"按钮用）。
    names: 只跑指定数据集（测试用）。
    """
    results: list[CollectResult] = []
    conn = db.connect()
    try:
        for c in collectors.COLLECTORS:
            if names and c.name not in names:
                continue
            if only_stale and _is_fresh(conn, c.name, stale_hours):
                results.append(CollectResult(c.name, c.group, c.desc, True, 0, "skipped(近期已更新)", 0.0))
                continue
            t0 = time.time()
            try:
                n = c.fn(conn)
                db.set_freshness(c.name, True, rows=n, conn=conn)
                results.append(CollectResult(c.name, c.group, c.desc, True, n, "", time.time() - t0))
            except Exception as exc:  # noqa: BLE001 单点失败不拖垮整轮
                db.set_freshness(c.name, False, error=f"{type(exc).__name__}: {exc}", conn=conn)
                results.append(
                    CollectResult(c.name, c.group, c.desc, False, 0, f"{type(exc).__name__}: {exc}", time.time() - t0)
                )
    finally:
        conn.close()
    return results
