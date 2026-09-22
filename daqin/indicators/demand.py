"""需求指标模块（06 §3 `metrics_daily` 需求段）。

**自动核**（默认可用）：
- 月频运量 `dq_vol_yoy`：大秦线月度运量同比，视为**次月 10 日可得**（公告发布窗口）；
  `dq_vol_yoy_lag1` / `_lag2` 先在月频取滞后月再对齐；`dq_vol_yoy_narrow_streak` = 同比回升（降幅收窄）的连续月数（S4 用）；
- 日频电厂 `pp_available_days`：当日可得，**含停更保护**（D-26）；`pp_avail_high_streak`（D5）、
  `pp_avail_normal_streak`（D-22 的 S5 备选自动核）。

**可选增强**（`demand.qhd_enabled`，需 `industry_weekly` 周频数据；默认关闭，表空时列保持 NULL）：
- `qhd_inv_level` / `qhd_inv_wow` / `qhd_inv_yoy`：库存水平与周环比/同比（D1/D2/D3）；
- `qhd_inv_wow_streak`：周环比 > `qhd_inv_wow_pct` 的连续周数（D2）；
- `qhd_inv_down_streak`：库存周环比为负的连续周数（S5 增强项）。

**筹码**（仅展示，不参与状态转换）：`inst_holding_ratio`（季频，`holder_quarterly`）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators.align import align_to_trading_days
from daqin.indicators.common import STALE_DAYS, streak
from daqin.thresholds import load_thresholds

DEMAND_COLS = [
    "dq_vol_yoy", "dq_vol_yoy_lag1", "dq_vol_yoy_lag2", "dq_vol_yoy_narrow_streak",
    "pp_available_days", "pp_avail_high_streak", "pp_avail_normal_streak",
    "asof_company",
]
QHD_COLS = [
    "qhd_inv_level", "qhd_inv_wow", "qhd_inv_yoy",
    "qhd_inv_wow_streak", "qhd_inv_down_streak",
]
HOLDER_COLS = ["inst_holding_ratio"]

QHD_YOY_WEEKS = 52      # 库存同比的滞后周数
HOLDER_LAG_DAYS = 45    # 季度末 → 筹码数据可用日的保守滞后


def add_demand(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """依次写入运量、电厂、qhd 增强与筹码列（原地）。"""
    add_volume(conn, m)
    add_energy(conn, m)
    add_qhd(conn, m)
    add_holders(conn, m)


def add_volume(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """月频运量同比（M1-3）→ 交易日对齐。"""
    cm = pd.read_sql_query("SELECT month, dq_line_volume_yoy FROM company_monthly ORDER BY month", conn)
    if cm.empty:
        return
    yoy = pd.Series(
        pd.to_numeric(cm["dq_line_volume_yoy"], errors="coerce").to_numpy(),
        index=pd.to_datetime(cm["month"] + "-01"),
    )
    avail = yoy.index + pd.DateOffset(months=1) + pd.Timedelta(days=9)
    idx = pd.DatetimeIndex(m.index)
    for col, series in (
        ("dq_vol_yoy", yoy),
        ("dq_vol_yoy_lag1", yoy.shift(1)),
        ("dq_vol_yoy_lag2", yoy.shift(2)),
    ):
        m[col] = align_to_trading_days(pd.Series(series.to_numpy(), index=avail), idx, lag_days=0).to_numpy()
    narrowing = yoy.diff() > 0
    m["dq_vol_yoy_narrow_streak"] = align_to_trading_days(
        pd.Series(narrowing.groupby((~narrowing).cumsum()).cumsum().to_numpy(), index=avail), idx, lag_days=0
    ).to_numpy()
    m["asof_company"] = str(cm["month"].max())


def add_energy(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """日频电厂可用天数（M1-2）→ 交易日对齐 + 停更保护。"""
    en = pd.read_sql_query("SELECT date, pp_available_days FROM energy_daily ORDER BY date", conn)
    if en.empty:
        return
    cfg = load_thresholds()
    idx = pd.DatetimeIndex(m.index)
    s = pd.Series(
        pd.to_numeric(en["pp_available_days"], errors="coerce").to_numpy(),
        index=pd.to_datetime(en["date"]),
    )
    aligned = align_to_trading_days(s, idx, lag_days=0)
    # 停更保护：源站停更（六大电止于 2019-06）后不得继续沿用旧值，否则 D5 会误触发（D-26）
    aligned = aligned.where(idx <= s.index.max() + pd.Timedelta(days=STALE_DAYS))
    m["pp_available_days"] = aligned.to_numpy()
    m["pp_avail_high_streak"] = streak(m["pp_available_days"] > cfg.demand.pp_available_days_high)
    m["pp_avail_normal_streak"] = streak(m["pp_available_days"] < cfg.repair.pp_avail_normal_days)


def add_qhd(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """qhd 库存增强项（需 `demand.qhd_enabled`；`industry_weekly` 空表时全部保持 NULL）。"""
    cfg = load_thresholds()
    if not cfg.demand.qhd_enabled:
        return
    w = pd.read_sql_query("SELECT week_end, qhd_inventory FROM industry_weekly ORDER BY week_end", conn)
    if w.empty:
        return
    inv = pd.Series(
        pd.to_numeric(w["qhd_inventory"], errors="coerce").to_numpy(),
        index=pd.to_datetime(w["week_end"]),
    )
    frame = pd.DataFrame({
        "qhd_inv_level": inv,
        "qhd_inv_wow": inv.pct_change(fill_method=None) * 100.0,
    })
    frame["qhd_inv_yoy"] = inv.pct_change(QHD_YOY_WEEKS, fill_method=None) * 100.0
    frame["qhd_inv_wow_streak"] = streak(frame["qhd_inv_wow"] > cfg.demand.qhd_inv_wow_pct)
    frame["qhd_inv_down_streak"] = streak(frame["qhd_inv_wow"] < 0)
    idx = pd.DatetimeIndex(m.index)
    for col in QHD_COLS:
        m[col] = align_to_trading_days(frame[col], idx, lag_days=0).to_numpy()


def add_holders(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """筹码季频（仅展示，不参与转换）→ 按「季末 + 45 天」估计可得日。"""
    hq = pd.read_sql_query(
        "SELECT quarter, inst_holding_ratio FROM holder_quarterly ORDER BY quarter", conn
    )
    if hq.empty:
        return
    avail = pd.to_datetime(hq["quarter"]) + pd.Timedelta(days=HOLDER_LAG_DAYS)
    s = pd.Series(pd.to_numeric(hq["inst_holding_ratio"], errors="coerce").to_numpy(), index=avail)
    m["inst_holding_ratio"] = align_to_trading_days(s, pd.DatetimeIndex(m.index), lag_days=0).to_numpy()
