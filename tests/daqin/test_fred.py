"""M0-4 单测（mock，不联网）：CSV 解析（"." 缺失丢弃）/ 合并入库幂等 / 异常 CSV 报错 / 增量起点。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import fred as mod  # noqa: E402
from daqin.storage import db  # noqa: E402

_CSV = """observation_date,DGS10
2020-01-02,1.88
2020-01-03,1.80
2020-01-06,.
2020-01-07,1.76
"""


def test_fetch_series_parses_and_drops_missing(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_text", lambda url, **kw: _CSV)
    s = mod.fetch_series("DGS10", "2020-01-01", "2020-01-10")
    assert s.index.tolist() == ["2020-01-02", "2020-01-03", "2020-01-07"]   # "." 行被丢弃
    assert s.tolist() == [1.88, 1.80, 1.76]


def test_fetch_series_bad_csv_raises(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_text", lambda url, **kw: "<html>404</html>")
    with pytest.raises(Exception):
        mod.fetch_series("BADID", "2020-01-01", "2020-01-10")


def test_collect_merges_and_is_idempotent(monkeypatch) -> None:
    series = {
        "us10y": pd.Series([1.88, 1.80], index=["2020-01-02", "2020-01-03"]),
        "us10y_real": pd.Series([0.05], index=["2020-01-02"]),
        "us10y_ie": pd.Series([1.75], index=["2020-01-02"]),
    }
    monkeypatch.setattr(mod, "fetch_series", lambda sid, s, e: series[{"DGS10": "us10y", "DFII10": "us10y_real", "T10YIE": "us10y_ie"}[sid]])
    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2020-01-01", end="2020-01-10") == 2
    rows = conn.execute("SELECT date, us10y, us10y_real, us10y_ie FROM macro_daily ORDER BY date").fetchall()
    assert rows[0]["us10y"] == pytest.approx(1.88)
    assert rows[0]["us10y_real"] == pytest.approx(0.05)
    assert rows[1]["us10y_real"] is None                    # 该日无观测 → NULL，不插值
    assert mod.collect(conn, start="2020-01-01", end="2020-01-10") == 2
    assert conn.execute("SELECT COUNT(*) FROM macro_daily").fetchone()[0] == 2


def test_incremental_start_overlap_and_floor() -> None:
    conn = db.connect(":memory:")
    assert mod._incremental_start(conn, "2006-01-01") == "2006-01-01"
    db.upsert_rows(conn, "macro_daily", [{"date": "2020-06-30", "us10y": 0.7}])
    assert mod._incremental_start(conn, "2006-01-01") == "2020-05-31"     # 回退 30 天
    assert mod._incremental_start(conn, "2020-07-01") == "2020-07-01"     # 不早于 hist_start
