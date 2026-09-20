"""M0-1 单测：阈值加载——正常加载 / 缺键 / 未知键 / 类型错 / 文件缺失 / 改文件即生效。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin import config  # noqa: E402
from daqin.thresholds import ThresholdsError, load_thresholds  # noqa: E402


def _valid_data() -> dict:
    """基于真实 thresholds.yaml 构造一份可变副本，避免测试与真实文件内容耦合。"""
    return yaml.safe_load(config.THRESHOLDS_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "thresholds.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_load_real_thresholds_ok() -> None:
    cfg = load_thresholds()
    assert cfg.macro.us10y_high == 5.0
    assert cfg.macro.s1_timeout_days == 60
    assert cfg.demand.qhd_enabled is False
    assert cfg.demand.energy_scope == "沿海六大电"
    assert cfg.repair.pp_avail_normal_days > 0
    assert cfg.data.fred_series["us10y"] == "DGS10"
    assert isinstance(cfg.change_log, list)


def test_missing_key_raises(tmp_path: Path) -> None:
    data = _valid_data()
    del data["macro"]["us10y_high"]
    with pytest.raises(ThresholdsError, match=r"macro.*us10y_high"):
        load_thresholds(_write(tmp_path, data))


def test_unknown_key_raises(tmp_path: Path) -> None:
    data = _valid_data()
    data["market"]["typo_key"] = 1.0
    with pytest.raises(ThresholdsError, match="typo_key"):
        load_thresholds(_write(tmp_path, data))


def test_unknown_section_raises(tmp_path: Path) -> None:
    data = _valid_data()
    data["misc"] = {}
    with pytest.raises(ThresholdsError, match="misc"):
        load_thresholds(_write(tmp_path, data))


def test_type_error_raises(tmp_path: Path) -> None:
    data = _valid_data()
    data["debounce"]["daily_confirm_days"] = 2.5
    with pytest.raises(ThresholdsError, match="整数"):
        load_thresholds(_write(tmp_path, data))


def test_bool_is_not_number(tmp_path: Path) -> None:
    data = _valid_data()
    data["macro"]["us10y_high"] = True
    with pytest.raises(ThresholdsError, match="数值"):
        load_thresholds(_write(tmp_path, data))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ThresholdsError, match="不存在"):
        load_thresholds(tmp_path / "nope.yaml")


def test_reload_picks_up_change(tmp_path: Path) -> None:
    """改文件立即生效（不缓存加载结果）。"""
    data = _valid_data()
    p = _write(tmp_path, data)
    assert load_thresholds(p).macro.us10y_high == 5.0
    data["macro"]["us10y_high"] = 5.5
    _write(tmp_path, data)
    assert load_thresholds(p).macro.us10y_high == 5.5
