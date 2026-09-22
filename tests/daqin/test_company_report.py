"""M1-3 单测（mock，不联网）：正文解析（增长/减少/累计干扰）/ 公告筛选 / 失败留痕 / 入库幂等。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import company_report as mod  # noqa: E402
from daqin.storage import db  # noqa: E402

# 与巨潮 PDF 实际提取文本同构（含换行，用于验证正则前会拍平空白）
_TEXT_NOV = """证券代码：601006 股票简称：大秦铁路 公告编号：【临2020-039】
大秦铁路股份有限公司
2020 年 11 月大秦线生产经营数据简报
本公司董事会及全体董事保证本公告内容不存在任何虚假记载、误导性陈述或者重大遗
漏，并对其内容的真实性、准确性和完整性承担个别及连带责任。
2020年11月，公司核心经营资产大秦线完成货物运输量3779万吨，同比增长
6.72%。日均运量125.97万吨。大秦线日均开行重车83.3列，其中：日均开行2
万吨列车57.4列。2020年1-11月，大秦线累计完成货物运输量36708万吨，同比
减少7.19%。
"""

_TEXT_FEB = "2020年2月，公司核心经营资产大秦线完成货物运输量2,371万吨，同比减少25.68%。\n"


def _notice_df() -> pd.DataFrame:
    link = ("http://www.cninfo.com.cn/new/disclosure/detail?stockCode=601006"
            "&announcementId=1208857805&orgId=9900000261&announcementTime=2020-12-05")
    other = "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1&announcementTime=2020-12-03"
    return pd.DataFrame({
        "代码": ["601006", "601006"],
        "简称": ["大秦铁路", "大秦铁路"],
        "公告标题": ["2020年11月大秦线生产经营数据简报", "关于控股股东增持公司股份的进展公告"],
        "公告时间": ["2020-12-05", "2020-12-03"],
        "公告链接": [link, other],
    })


def test_parse_monthly_growth() -> None:
    got = mod.parse_monthly(_TEXT_NOV)
    assert got == {"month": "2020-11", "dq_line_volume": 3779.0, "dq_line_volume_yoy": pytest.approx(6.72)}


def test_parse_monthly_decline_and_comma() -> None:
    got = mod.parse_monthly(_TEXT_FEB)
    assert got == {"month": "2020-02", "dq_line_volume": 2371.0, "dq_line_volume_yoy": pytest.approx(-25.68)}


def test_parse_monthly_cumulative_ignored() -> None:
    """累计表述「1-11月…累计完成」不应被当成单月（当月值为 3779 而非 36708）。"""
    got = mod.parse_monthly(_TEXT_NOV)
    assert got is not None and got["dq_line_volume"] == pytest.approx(3779.0)


def test_parse_monthly_word_order_variant() -> None:
    """2022 年中起的措辞变体：「货物运输量完成 3636 万吨」（完成移到数字前）。"""
    text = "2023年5月，公司核心经营资产大秦线货物运输量完成3636万吨，同比增长7.48%。日均运量117.29万吨。"
    assert mod.parse_monthly(text) == {"month": "2023-05", "dq_line_volume": 3636.0, "dq_line_volume_yoy": pytest.approx(7.48)}


def test_parse_monthly_flat() -> None:
    """「同比持平」应解析为 0%（2021-10 公告的真实措辞）。"""
    text = "2021年10月，公司核心经营资产大秦线完成货物运输量3286万吨，同比持平。日均运量106.00万吨。"
    assert mod.parse_monthly(text) == {"month": "2021-10", "dq_line_volume": 3286.0, "dq_line_volume_yoy": 0.0}


def test_parse_monthly_without_yoy() -> None:
    """2023 年下半年起当月同比被省略（只给累计同比）→ yoy 为 None（由 collect 用去年同月自算）。"""
    text = "2023年10月，公司核心经营资产大秦线货物运输量完成3484万吨，日均运量112.39万吨。"
    got = mod.parse_monthly(text)
    assert got == {"month": "2023-10", "dq_line_volume": 3484.0, "dq_line_volume_yoy": None}


def test_yoy_from_history(monkeypatch) -> None:
    conn = db.connect(":memory:")
    db.upsert_rows(conn, "company_monthly", [
        {"month": "2022-10", "dq_line_volume": 4000.0, "dq_line_volume_yoy": 1.0},
    ], key="month")
    assert mod._yoy_from_history(conn, "2023-10", 3484.0) == pytest.approx((3484.0 - 4000.0) / 4000.0 * 100)
    assert mod._yoy_from_history(conn, "2023-01", 3000.0) is None      # 无去年同月 → None


def test_parse_monthly_cross_sentence() -> None:
    """2022-11 变体：当月运量写在下一句（「全月完成货物运输量 2290 万吨」）。"""
    text = ("2022年11月，公司高效统筹疫情防控和运输组织，在各方努力下，核心经营资产大秦线运输秩序有序恢复，"
            "日运量逐步回升。全月完成货物运输量2290万吨，同比减少39.99%。日均运量76.33万吨，环比增长26.60%。")
    assert mod.parse_monthly(text) == {"month": "2022-11", "dq_line_volume": 2290.0, "dq_line_volume_yoy": pytest.approx(-39.99)}


def test_incremental_start_lookback() -> None:
    """增量起点：库内最新月往前 3 个月；库空时用全量起点。"""
    conn = db.connect(":memory:")
    assert mod._incremental_start(conn, "2006-01-01") == "2006-01-01"
    db.upsert_rows(conn, "company_monthly", [{"month": "2026-08", "dq_line_volume": 3000.0}], key="month")
    assert mod._incremental_start(conn, "2006-01-01") == "2026-05-01"
    assert mod._incremental_start(conn, "2026-07-01") == "2026-07-01"      # 不早于 hist_start


def test_parse_monthly_empty() -> None:
    assert mod.parse_monthly("") is None
    assert mod.parse_monthly("其他内容的公告") is None


def test_fetch_notices_filters_keyword(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _notice_df())
    got = mod.fetch_notices(2020)
    assert len(got) == 1
    assert got[0]["announcement_id"] == "1208857805"
    assert got[0]["date"] == "2020-12-05"


def test_fetch_notices_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _notice_df().drop(columns=["公告链接"]))
    with pytest.raises(ValueError, match="缺少列"):
        mod.fetch_notices(2020)


def test_collect_filters_and_persists(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_notices", lambda year: [
        {"title": "2020年11月大秦线生产经营数据简报", "date": "2020-12-05", "announcement_id": "1208857805"},
        {"title": "2020年10月大秦线生产经营数据简报", "date": "2020-11-10", "announcement_id": "1208800001"},
    ] if year == 2020 else [])
    monkeypatch.setattr(mod, "fetch_pdf_text", lambda aid, d: _TEXT_NOV if aid == "1208857805" else _TEXT_FEB.replace("2020年2月", "2020年10月"))

    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2020-01-01", end="2020-12-31") == 2
    rows = conn.execute("SELECT month, dq_line_volume, dq_line_volume_yoy, source FROM company_monthly ORDER BY month").fetchall()
    assert [r["month"] for r in rows] == ["2020-10", "2020-11"]
    assert rows[1]["dq_line_volume"] == pytest.approx(3779.0)
    assert rows[1]["source"] == "cninfo_pdf"
    assert mod.collect(conn, start="2020-01-01", end="2020-12-31") == 2          # 幂等
    assert conn.execute("SELECT COUNT(*) FROM company_monthly").fetchone()[0] == 2


def test_collect_range_filter(monkeypatch) -> None:
    """公告发布日跨年：上年 12 月的数据在次年 1 月发布，应按发布年窗口采到。"""
    monkeypatch.setattr(mod, "fetch_notices", lambda year: [
        {"title": "2021年12月大秦线生产经营数据简报", "date": "2022-01-10", "announcement_id": "A"}
    ] if year == 2022 else [])
    monkeypatch.setattr(mod, "fetch_pdf_text", lambda aid, d: "2021年12月，公司核心经营资产大秦线完成货物运输量3000万吨，同比增长1.00%。")
    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2021-01-01", end="2021-12-31") == 0          # 只跑 2021 窗口 → 拿不到
    assert mod.collect(conn, start="2022-01-01", end="2022-12-31") == 1          # 跑 2022 窗口 → 采到 2021-12


def test_collect_all_failures_raises(monkeypatch) -> None:
    """有公告但全部失败 → 抛错（触发 freshness 红灯），不静默丢月。"""
    monkeypatch.setattr(mod, "fetch_notices", lambda year: [
        {"title": "2020年11月大秦线生产经营数据简报", "date": "2020-12-05", "announcement_id": "A"}
    ] if year == 2020 else [])
    monkeypatch.setattr(mod, "fetch_pdf_text", lambda aid, d: "完全改版的公告正文，没有运量数字")
    with pytest.raises(ValueError, match="全部失败"):
        mod.collect(db.connect(":memory:"), start="2020-01-01", end="2020-12-31")


def test_collect_partial_failure_warns_but_persists(monkeypatch, capsys) -> None:
    """部分失败：打印警告但仍入库其余月份。"""
    monkeypatch.setattr(mod, "fetch_notices", lambda year: [
        {"title": "2020年11月大秦线生产经营数据简报", "date": "2020-12-05", "announcement_id": "OK"},
        {"title": "2020年10月大秦线生产经营数据简报", "date": "2020-11-10", "announcement_id": "BAD"},
    ] if year == 2020 else [])

    def _fetch(aid: str, d: str) -> str:
        if aid == "BAD":
            raise RuntimeError("boom")
        return _TEXT_NOV

    monkeypatch.setattr(mod, "fetch_pdf_text", _fetch)
    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2020-01-01", end="2020-12-31") == 1
    assert "[WARN]" in capsys.readouterr().out
