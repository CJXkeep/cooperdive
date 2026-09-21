"""状态机（纯函数）：S0-S5 状态转换，回测与实盘共用；**本模块不落库**（D-21）。

来源：03 §3.2 转换矩阵 + D-10（S4/S5 → S3 降级）+ D-22（S5 自动核）。
机制：向上允许跳级（快变量优先 K > D > M）；向下逐级 + 滞回；日频条件连续 N 日防抖（03 §3.3）。

接口契约（M0 冻结）：
- `next_state(current, row, cfg, deb, state_days=0)`：**每日恰好调用一次**（内部会喂入全部防抖器）；
- `run_daily(metrics, cfg)`：整段回放，返回状态序列 DataFrame；落库职责在 cli 层（D-21）。
"""

from __future__ import annotations

import pandas as pd

from daqin.signals import rules
from daqin.thresholds import Thresholds

ORDER = {"S0": 0, "S1": 1, "S2": 2, "S3": 3}   # 风险序；S4/S5 独立判定

_DEBOUNCED = {"m1": rules.r_m1, "m4": rules.r_m4, "k1": rules.r_k1, "k2": rules.r_k2, "k3": rules.r_k3}


class Debouncer:
    """日频条件连续 N 次成立才放行（03 §3.3）。"""

    def __init__(self, days: int) -> None:
        self.days = max(1, int(days))
        self.count = 0

    def feed(self, condition: bool) -> bool:
        self.count = self.count + 1 if condition else 0
        return self.count >= self.days


def new_debouncers(cfg: Thresholds) -> dict[str, Debouncer]:
    days = cfg.debounce.daily_confirm_days
    return {name: Debouncer(days) for name in _DEBOUNCED}


def next_state(current: str, row, cfg: Thresholds,
               deb: dict[str, Debouncer], state_days: int = 0) -> str:
    """单日转换：当前状态 + 当日 metrics 行 → 新状态（每日恰好调用一次）。

    `state_days`：当前状态已持续的交易日数（S1 超时判定用）。
    """
    # 统一喂入全部日频防抖器（先全部喂，避免短路导致计数不连续）
    passed = {name: deb[name].feed(fn(row, cfg)[0]) for name, fn in _DEBOUNCED.items()}

    # 0) S4/S5 分支：D-10 降级优先（风险方向 → 立即生效、不经防抖），其次 S4 → S5
    if current in ("S4", "S5"):
        if rules.r_k2(row, cfg)[0] or rules.r_k3(row, cfg)[0]:
            return "S3"
        if current == "S4" and rules.s5_ready(row, cfg)[0]:
            return "S5"
        return current   # S5 → S0「回补满」需仓位跟踪，待 M2/M4 定义（M0 §6 遗留）

    # 1) S3 → S4（底部观察）
    if current == "S3" and rules.s4_ready(row, cfg)[0]:
        return "S4"

    # 2) 向上（允许跳级）：K 快变量 → D∧K1 → M
    target = None
    if passed["k2"] or passed["k3"]:
        target = "S3"
    elif rules.demand_bad(row, cfg) and passed["k1"]:
        target = "S2"
    elif passed["m1"] or passed["m4"]:
        target = "S1"
    if target is not None and ORDER[target] > ORDER[current]:
        return target

    # 3) 向下（逐级 + 滞回）
    return _demote(current, row, cfg, state_days)


def _demote(current: str, row, cfg: Thresholds, state_days: int) -> str:
    if current == "S0":
        return "S0"
    if current == "S1":
        us10y = rules.value_of(row, "us10y")
        released = (
            us10y is not None
            and us10y < cfg.macro.us10y_low_release
            and not rules.r_m4(row, cfg)[0]
        )
        if released or state_days >= cfg.macro.s1_timeout_days:
            return "S0"
        return "S1"
    if current == "S2":
        return "S1" if not rules.demand_bad(row, cfg, release=True) else "S2"
    if current == "S3":
        # 03 §3.2 字面：K1 复现 ∧ D 未修复（demand_bad 仍成立）→ 回 S2。
        # ⚠️ 语义存疑：D 未修复时降级意味着加仓（20%→40%），待 M3 参数化复核（M0 §6 已登记）。
        if rules.r_k1(row, cfg)[0] and rules.demand_bad(row, cfg):
            return "S2"
        return "S3"
    return current


def position_advice(state: str, cfg: Thresholds) -> float:
    """状态 → 仓位建议（示例口径，03 §3.1 + thresholds.position）。"""
    p = cfg.position
    table = {
        "S0": p.base,
        "S1": p.base,                                       # 不追高
        "S2": p.base * (1 - p.s2_reduce_ratio),             # 减 1/3
        "S3": p.s3_floor,                                   # 一次性减至下限
        "S4": p.s3_floor,                                   # 停止减仓
        "S5": min(p.base, p.s3_floor + p.s4_rebuild_step),  # 分批回补（M0 简化：一步）
    }
    return round(table[state], 4)


def run_daily(metrics: pd.DataFrame, cfg: Thresholds) -> pd.DataFrame:
    """逐日回放（实盘每日一次 / 回测整段）；纯函数，不落库（D-21）。

    `metrics`：含 date（列或索引）+ 指标列。返回列：
    date, state, prev_state, changed, triggered_rules, position_advice。
    """
    df = metrics.set_index("date") if "date" in metrics.columns else metrics
    deb = new_debouncers(cfg)
    state, state_days = "S0", 0
    records = []
    for ts, row in df.iterrows():
        prev = state
        state = next_state(state, row, cfg, deb, state_days)
        state_days = state_days + 1 if state == prev else 1
        records.append({
            "date": pd.Timestamp(ts).strftime("%Y-%m-%d"),
            "state": state,
            "prev_state": prev,
            "changed": int(state != prev),
            "triggered_rules": ",".join(rules.fired_rules(row, cfg)),
            "position_advice": position_advice(state, cfg),
        })
    return pd.DataFrame(records)
