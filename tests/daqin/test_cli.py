"""M0-7 CLI 端到端：collect 失败隔离 + freshness / compute / signal 落库（D-21）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin import cli  # noqa: E402
from daqin.storage import db  # noqa: E402
from storage import get_freshness  # noqa: E402


@pytest.fixture
def dbpath(monkeypatch, tmp_path: Path) -> Path:
    """cli 的 db.connect 指向临时文件库（CLI 每个命令会 close 连接，内存库不适用）。"""
    p = tmp_path / "daqin.db"
    real = db.connect
    monkeypatch.setattr(cli.db, "connect", lambda *a, **kw: real(p))
    return p


def _seed_stock(conn, days: int = 70) -> str:
    """70 天平价 + 末日放量大跌 → 触发 K2（连续 2 日）。"""
    dates = pd.date_range("2019-10-01", periods=days, freq="D").strftime("%Y-%m-%d").tolist()
    closes = [10.0] * (days - 2) + [9.45, 8.88]
    amounts = [100.0] * (days - 2) + [500.0, 500.0]
    db.upsert_rows(conn, "stock_daily", [
        {"date": d, "daqin_close": c, "daqin_amount": a, "csi300_close": 4000.0}
        for d, c, a in zip(dates, closes, amounts)
    ])
    return dates[-1]


def test_collect_failure_isolation_and_freshness(dbpath: Path, monkeypatch, capsys) -> None:
    def ok(conn, start=None, end=None):
        return db.upsert_rows(conn, "stock_daily", [{"date": "2020-01-02", "daqin_close": 7.0}])

    def bad(conn, start=None, end=None):
        raise RuntimeError("boom")

    monkeypatch.setitem(cli.COLLECTORS, "stock_daily", ok)
    monkeypatch.setitem(cli.COLLECTORS, "macro_daily", bad)
    # 限定范围，避免触发其他真实网络采集器
    assert cli.main(["collect", "--names", "stock_daily", "macro_daily"]) == 1
    out = capsys.readouterr().out
    assert "[OK]   stock_daily" in out and "[FAIL] macro_daily" in out

    conn = db.connect(dbpath)
    fr = get_freshness(conn).set_index("dataset")
    assert fr.loc["stock_daily", "rows_last"] == 1
    assert fr.loc["stock_daily", "last_success"] is not None
    assert "boom" in fr.loc["macro_daily", "last_error"]


def test_full_pipeline_compute_then_signal(dbpath: Path, capsys) -> None:
    conn = db.connect(dbpath)
    last = _seed_stock(conn)
    conn.close()

    assert cli.main(["compute"]) == 0
    assert "metrics_daily: 70 rows" in capsys.readouterr().out

    assert cli.main(["signal", "--date", last]) == 0
    out = capsys.readouterr().out
    assert "状态 S3" in out                                  # K2 连续 2 日 → 跳级 S0→S3
    assert "K2" in out

    conn = db.connect(dbpath)
    assert conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0] == 70
    row = conn.execute("SELECT state, position_advice, note FROM signal_log WHERE date=?", (last,)).fetchone()
    assert row["state"] == "S3"
    assert row["position_advice"] == pytest.approx(0.2)
    assert "S3" in row["note"]
    # metrics 的 freshness 也应有记录
    fr = get_freshness(conn).set_index("dataset")
    assert fr.loc["metrics_daily", "rows_last"] == 70


def test_signal_without_metrics_is_graceful(dbpath: Path, capsys) -> None:
    assert cli.main(["signal"]) == 1
    assert "metrics_daily 为空" in capsys.readouterr().out
