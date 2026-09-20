"""阈值加载：`daqin/thresholds.yaml` → 冻结 dataclass。

约定（06 D-11 / M0-1）：
- 代码不得内嵌阈值，一律经本模块读取；
- **缺键 / 未知键 / 类型错 → 启动即报错**（`ThresholdsError`），绝不静默取默认值；
- 每次调用都重新读文件（不缓存），改阈值立即生效；
- 新增阈值时同步更新 `thresholds.yaml` 与本文件的 dataclass。
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from daqin import config


class ThresholdsError(ValueError):
    """阈值文件不合法：文件缺失 / 缺键 / 未知键 / 类型错。"""


@dataclass(frozen=True)
class MacroThresholds:
    us10y_high: float
    us10y_low_release: float
    s1_timeout_days: int
    us10y_20d_chg_alert_bp: float
    spread_inferior: float
    spread_advantage: float
    csi300_drawdown_20d: float


@dataclass(frozen=True)
class DemandThresholds:
    qhd_enabled: bool
    qhd_inv_warn: float
    qhd_inv_danger: float
    qhd_inv_level_release: float
    qhd_inv_wow_pct: float
    qhd_inv_wow_weeks: int
    qhd_inv_yoy_pct: float
    dq_vol_yoy_danger: float
    energy_scope: str
    pp_available_days_high: float


@dataclass(frozen=True)
class CoalThresholds:
    zc_min_volume_ratio: float
    zc_dev_20d_pct: float


@dataclass(frozen=True)
class MarketThresholds:
    rs_20d_turn_negative: float
    rs_60d_lookback_peak: float
    single_day_drop_pct: float
    volume_ratio_confirm: float
    ma_break: str
    ma_below_days: int


@dataclass(frozen=True)
class BottomThresholds:
    pb_below: float
    div_yield_above: float
    decline_narrowing_months: int


@dataclass(frozen=True)
class RepairThresholds:
    inventory_down_weeks: int
    pp_avail_normal_days: float   # D-22：S5 自动核——可用天数回落至该值以下视为需求恢复正常


@dataclass(frozen=True)
class DebounceThresholds:
    daily_confirm_days: int


@dataclass(frozen=True)
class PositionThresholds:
    base: float
    s2_reduce_ratio: float
    s3_floor: float
    s4_rebuild_step: float


@dataclass(frozen=True)
class DataThresholds:
    fred_csv_endpoint: str
    fred_series: dict[str, str]
    fred_api_key_env: str
    hist_start: str
    manual_data_dir: str


@dataclass(frozen=True)
class Thresholds:
    macro: MacroThresholds
    demand: DemandThresholds
    coal: CoalThresholds
    market: MarketThresholds
    bottom: BottomThresholds
    repair: RepairThresholds
    debounce: DebounceThresholds
    position: PositionThresholds
    data: DataThresholds
    change_log: list[dict]


_SECTIONS: dict[str, type] = {
    "macro": MacroThresholds,
    "demand": DemandThresholds,
    "coal": CoalThresholds,
    "market": MarketThresholds,
    "bottom": BottomThresholds,
    "repair": RepairThresholds,
    "debounce": DebounceThresholds,
    "position": PositionThresholds,
    "data": DataThresholds,
}


def load_thresholds(path: Path | None = None) -> Thresholds:
    """读取并校验阈值文件；任何不合法都抛 `ThresholdsError`。"""
    p = Path(path) if path is not None else config.THRESHOLDS_PATH
    if not p.exists():
        raise ThresholdsError(f"阈值文件不存在：{p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ThresholdsError(f"阈值文件顶层应为映射（键值对），实际：{type(raw).__name__}")

    expected_top = set(_SECTIONS) | {"change_log"}
    missing = sorted(expected_top - set(raw))
    unknown = sorted(set(raw) - expected_top)
    if missing or unknown:
        raise ThresholdsError(f"顶层段不匹配：缺 {missing or '无'}；未知 {unknown or '无'}")

    sections = {name: _build(cls, raw[name], name) for name, cls in _SECTIONS.items()}
    change_log = raw["change_log"]
    if not isinstance(change_log, list):
        raise ThresholdsError(f"change_log：应为列表，实际 {type(change_log).__name__}")
    return Thresholds(change_log=change_log, **sections)


def _build(cls: type, mapping: Any, where: str) -> Any:
    if not isinstance(mapping, dict):
        raise ThresholdsError(f"{where}：应为映射，实际 {type(mapping).__name__}")
    declared = {f.name: f for f in fields(cls)}
    missing = sorted(set(declared) - set(mapping))
    unknown = sorted(set(mapping) - set(declared))
    if missing or unknown:
        raise ThresholdsError(f"{where}：缺键 {missing or '无'}；未知键 {unknown or '无'}")
    return cls(
        **{name: _coerce(mapping[name], f.type, f"{where}.{name}") for name, f in declared.items()}
    )


def _coerce(value: Any, declared: str, where: str) -> Any:
    """按字段声明做基础类型校验。

    声明类型来自 `from __future__ import annotations`，是字符串（如 "float"、"dict[str, str]"）。
    支持：float / int / bool / str / dict[...] / list[...]；其余声明直接报错，防止静默漏校验。
    """
    if declared == "float":
        return float(_require_number(value, where))
    if declared == "int":
        n = _require_number(value, where)
        if isinstance(n, float) and not n.is_integer():
            raise ThresholdsError(f"{where}：应为整数，实际 {value!r}")
        return int(n)
    if declared == "bool":
        if not isinstance(value, bool):
            raise ThresholdsError(f"{where}：应为 true/false，实际 {value!r}")
        return value
    if declared == "str":
        if not isinstance(value, str):
            raise ThresholdsError(f"{where}：应为字符串，实际 {value!r}")
        return value
    if declared == "dict[str, str]":
        if not isinstance(value, dict):
            raise ThresholdsError(f"{where}：应为映射，实际 {type(value).__name__}")
        bad = {k: v for k, v in value.items() if not isinstance(k, str) or not isinstance(v, str)}
        if bad:
            raise ThresholdsError(f"{where}：应为 str→str 映射，异常项 {bad!r}")
        return value
    if declared == "list[dict]":
        if not isinstance(value, list):
            raise ThresholdsError(f"{where}：应为列表，实际 {type(value).__name__}")
        return value
    raise ThresholdsError(f"{where}：字段声明了不支持校验的类型 {declared!r}（支持 float/int/bool/str/dict/list）")


def _require_number(value: Any, where: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThresholdsError(f"{where}：应为数值，实际 {value!r}")
    return value
