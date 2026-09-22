"""信号规则（纯函数，03 §2）：输入 metrics 单行 + 阈值 → `(是否触发, 原因)`。

约定：
- 每条规则独立函数；辅助规则（M2/M3/D6/K4）只供日报展示，不参与状态转换；
- 列值为 NULL/NaN 时规则恒为 False（数据缺失不误触发，04 §3）；
- qhd 增强项（D1-D3）在 `qhd_enabled=false` 时恒为 False（D-02/D-03）；
- S4/S5 的进入条件同样以纯函数形式提供（D-22 重构后）。
"""

from __future__ import annotations

import pandas as pd

from daqin.thresholds import Thresholds

Reason = tuple[bool, str]


def _val(row, col: str) -> float | None:
    """取出数值列；缺失/非数返回 None。"""
    getter = getattr(row, "get", None)
    if getter is None:
        return None
    v = getter(col)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


# ---------------- 宏观组（M） ----------------

def r_m1(row, cfg: Thresholds) -> Reason:
    """M1 美债高位（滞回：升级用 high，解除见 state_machine._demote）。"""
    v = _val(row, "us10y")
    return (v > cfg.macro.us10y_high, "M1 美债高位") if v is not None else (False, "")


def r_m2(row, cfg: Thresholds) -> Reason:
    """M2 美债快速上行（辅助）。"""
    v = _val(row, "us10y_20d_chg")
    return (v > cfg.macro.us10y_20d_chg_alert_bp, "M2 美债快速上行") if v is not None else (False, "")


def r_m3(row, cfg: Thresholds) -> Reason:
    """M3 利差倒挂（辅助）。"""
    v = _val(row, "spread_daqin_us10y")
    return (v < cfg.macro.spread_inferior, "M3 利差倒挂") if v is not None else (False, "")


def r_m4(row, cfg: Thresholds) -> Reason:
    """M4 系统性下跌。"""
    v = _val(row, "csi300_drawdown_20d")
    return (v < cfg.macro.csi300_drawdown_20d, "M4 系统性下跌") if v is not None else (False, "")


# ---------------- 需求组（D，D-03：自动核 D4/D5 + 增强 D1-D3） ----------------

def r_d1(row, cfg: Thresholds) -> Reason:
    """D1 库存绝对高位（qhd 增强项）。"""
    if not cfg.demand.qhd_enabled:
        return False, ""
    v = _val(row, "qhd_inv_level")
    if v is None:
        return False, ""
    if v > cfg.demand.qhd_inv_danger:
        return True, "D1 库存 danger"
    if v > cfg.demand.qhd_inv_warn:
        return True, "D1 库存 warn"
    return False, ""


def r_d2(row, cfg: Thresholds) -> Reason:
    """D2 库存连续累积（qhd 增强项）。"""
    if not cfg.demand.qhd_enabled:
        return False, ""
    streak, wow = _val(row, "qhd_inv_wow_streak"), _val(row, "qhd_inv_wow")
    if streak is None or wow is None:
        return False, ""
    ok = streak >= cfg.demand.qhd_inv_wow_weeks and wow > cfg.demand.qhd_inv_wow_pct
    return (ok, "D2 库存连续累积")


def r_d3(row, cfg: Thresholds) -> Reason:
    """D3 库存同比高增（qhd 增强项）。"""
    if not cfg.demand.qhd_enabled:
        return False, ""
    v = _val(row, "qhd_inv_yoy")
    return (v > cfg.demand.qhd_inv_yoy_pct, "D3 库存同比高增") if v is not None else (False, "")


def r_d4(row, cfg: Thresholds) -> Reason:
    """D4 运量同比转负（自动核）。

    口径（**D-30**）：月频 `dq_vol_yoy`（2014 起，月度简报）**或** 定期报告口径 `dq_vol_periodic_yoy`
    （半年/年频，2006 起，来自半年报/年报）——两者时间上不重叠，任一为负即触发。
    """
    for col in ("dq_vol_yoy", "dq_vol_periodic_yoy"):
        v = _val(row, col)
        if v is not None and v < cfg.demand.dq_vol_yoy_danger:
            return True, "D4 运量转负"
    return False, ""


def r_d5(row, cfg: Thresholds) -> Reason:
    """D5 电厂可用天数过高（沿海六大电，自动核）。"""
    v = _val(row, "pp_available_days")
    return (v > cfg.demand.pp_available_days_high, "D5 电厂需求弱") if v is not None else (False, "")


def r_d6(row, cfg: Thresholds) -> Reason:
    """D6 煤价走弱（ZC 代理，辅助；无有效成交时禁用，D-05）。"""
    if _val(row, "zc_valid") != 1.0:
        return False, ""
    v = _val(row, "zc_dev_20d")
    return (v < cfg.coal.zc_dev_20d_pct, "D6 煤价走弱(代理)") if v is not None else (False, "")


def demand_bad(row, cfg: Thresholds, release: bool = False) -> bool:
    """需求恶化（D-03）：自动核 = D4 ∨ D5；增强项 = D1 ∨ D2 ∨ D3（仅 qhd 开启）。

    `release=True` 用于降级判定：库存水平走滞回释放阈值（qhd_inv_level_release）。
    """
    if r_d4(row, cfg)[0] or r_d5(row, cfg)[0]:
        return True
    if cfg.demand.qhd_enabled:
        if release:
            level = _val(row, "qhd_inv_level")
            return level is not None and level > cfg.demand.qhd_inv_level_release
        return r_d1(row, cfg)[0] or r_d2(row, cfg)[0] or r_d3(row, cfg)[0]
    return False


# ---------------- 市场组（K） ----------------

def r_k1(row, cfg: Thresholds) -> Reason:
    """K1 相对强势松动：rel_strength_20d 转负，且此前 60 日内曾达峰值阈值。"""
    cur, peak = _val(row, "rel_strength_20d"), _val(row, "rs_60d_peak")
    if cur is None or peak is None:
        return False, ""
    ok = cur < cfg.market.rs_20d_turn_negative and peak > cfg.market.rs_60d_lookback_peak
    return (ok, "K1 相对强势松动")


def r_k2(row, cfg: Thresholds) -> Reason:
    """K2 放量下跌：单日跌幅超阈值 且 量比确认。"""
    ret, vr = _val(row, "daily_return_pct"), _val(row, "volume_ratio_5d")
    if ret is None or vr is None:
        return False, ""
    ok = ret < -cfg.market.single_day_drop_pct and vr > cfg.market.volume_ratio_confirm
    return (ok, "K2 放量下跌")


def r_k3(row, cfg: Thresholds) -> Reason:
    """K3 破位：连续 N 日收盘 < MA60（streak 列由指标层预计算）。"""
    streak = _val(row, "ma60_below_streak")
    if streak is None:
        return False, ""
    return (streak >= cfg.market.ma_below_days, "K3 破位 MA60")


def r_k4(row, cfg: Thresholds) -> Reason:
    """K4 筹码恶化（辅助，季频；M1 采集后实现）。"""
    return False, ""


def r_k5(row, cfg: Thresholds) -> Reason:
    """K5 底部特征：PB < 阈值 且 股息率 > 阈值。"""
    pb, dy = _val(row, "daqin_pb"), _val(row, "dividend_yield_ttm")
    if pb is None or dy is None:
        return False, ""
    ok = pb < cfg.bottom.pb_below and dy > cfg.bottom.div_yield_above
    return (ok, "K5 底部特征")


# ---------------- 状态进入条件（S4/S5；D-22 重构） ----------------

def s4_ready(row, cfg: Thresholds) -> Reason:
    """S4 进入：K5 ∧ 运量降幅连续收窄（月数阈值）。"""
    streak = _val(row, "dq_vol_yoy_narrow_streak")
    if not r_k5(row, cfg)[0] or streak is None:
        return False, ""
    return (streak >= cfg.bottom.decline_narrowing_months, "S4 底部观察")


def s5_ready(row, cfg: Thresholds) -> Reason:
    """S5 进入（D-22 → D-26 二次修复）：运量**连续 2 个月**转正；qhd 开启时叠加库存连续下降。

    D-26：原依赖的电厂可用天数（`pp_available_days`）数据源已停更（2019-06），
    改用月频运量的连续性做"需求恢复"确认（`dq_vol_yoy` 与 `dq_vol_yoy_lag1` 均为正）。
    """
    vol, lag1 = _val(row, "dq_vol_yoy"), _val(row, "dq_vol_yoy_lag1")
    if vol is None or lag1 is None or vol <= 0 or lag1 <= 0:
        return False, ""
    if cfg.demand.qhd_enabled:
        down = _val(row, "qhd_inv_down_streak")
        if down is None or down < cfg.repair.inventory_down_weeks:
            return False, ""
    return True, "S5 修复期"


# ---------------- 汇总（供 signal_log.triggered_rules） ----------------

ALL_RULES: dict[str, object] = {
    "M1": r_m1, "M2": r_m2, "M3": r_m3, "M4": r_m4,
    "D1": r_d1, "D2": r_d2, "D3": r_d3, "D4": r_d4, "D5": r_d5, "D6": r_d6,
    "K1": r_k1, "K2": r_k2, "K3": r_k3, "K4": r_k4, "K5": r_k5,
}


def fired_rules(row, cfg: Thresholds) -> list[str]:
    """当日触发的全部规则编号（含辅助，供日报与 signal_log 留痕）。"""
    out: list[str] = []
    for name, fn in ALL_RULES.items():
        ok, _ = fn(row, cfg)  # type: ignore[operator]
        if ok:
            out.append(name)
    return out


value_of = _val   # 公开别名（state_machine 等复用同一取值语义）
