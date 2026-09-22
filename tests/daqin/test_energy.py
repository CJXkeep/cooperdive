"""M1-2 单测（mock，不联网）：六大电字段映射 / 区间过滤 / 幂等 / 接口改版报错。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import energy as mod  # noqa: E402
from daqin.storage import db  # noqa: E402


def _raw() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2016-01-04", "2019-06-21"],
        "沿海六大电库存": [1200.5, 1800.2],
        "日耗": [60.1, 62.3],
        "存煤可用天数": [19.97, 28.9],
    })


def test_fetch_maps_columns(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw())
    df = mod.fetch_energy()
    assert list(df.columns) == mod._COLS
    assert df["date"].tolist() == ["2016-01-04", "2019-06-21"]
    assert df["pp_available_days"].tolist() == pytest.approx([19.97, 28.9])


def test_fetch_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw().drop(columns=["存煤可用天数"]))
    with pytest.raises(ValueError, match="缺少列"):
        mod.fetch_energy()


def test_collect_range_and_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_energy", lambda: pd.DataFrame({
        "date": ["2016-01-04", "2019-06-21"],
        "pp_stock": [1200.5, 1800.2],
        "pp_daily_consume": [60.1, 62.3],
        "pp_available_days": [19.97, 28.9],
    }))
    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2016-01-01", end="2016-12-31") == 1
    assert mod.collect(conn, start="2016-01-01", end="2016-12-31") == 1
    assert conn.execute("SELECT COUNT(*) FROM energy_daily").fetchone()[0] == 1
    assert mod.collect(conn) == 2                                   # 全历史
    assert conn.execute("SELECT COUNT(*) FROM energy_daily").fetchone()[0] == 2
