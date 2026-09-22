"""M5/D-30 单测（mock，不联网）：定期报告运量的三种措辞解析 / 报告筛选 / 报告期推导 / 幂等。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import company_report_periodic as mod  # noqa: E402
from daqin.storage import db  # noqa: E402

# 句式型（**2008 半年报真实文本**：「同比增运」措辞）
_TEXT_SENT = ("…占全国铁路煤炭运输总量的21.31%，其中大秦线完成货物运输量17,096.4万吨，"
              "同比增运2,584.7万吨，增幅17.8%。")
# 表格/退化型（2008 年报 PDF 提取形态：本期 上年同期）
_TEXT_TABLE = "项目 2008年 2007年 货物运输量 40,588 37,866 周转量 ..."
# 「较上年同期减少」型
_TEXT_YEAR = ("全年大秦线完成货物运输量33,016.9万吨，较上年同期减少2,000.0万吨，降幅5.7%。")


def test_parse_sentence_form() -> None:
    got = mod.parse_periodic(_TEXT_SENT)
    assert got == {"dq_vol_cum": pytest.approx(17096.4), "dq_vol_cum_yoy": pytest.approx(17.8)}


def test_parse_rejects_unreasonable_ratio() -> None:
    """两数比值不合理时不得自算同比（曾把「同比增量」当上年值算出 561%）。"""
    text = "大秦线完成货物运输量17,096.4万吨，同比增运2,584.7万吨。"      # 无「增幅 X%」可退
    got = mod.parse_periodic(text)
    assert got is None or got["dq_vol_cum_yoy"] < 100.0


def test_parse_table_form_computes_yoy() -> None:
    got = mod.parse_periodic(_TEXT_TABLE)
    assert got is not None
    assert got["dq_vol_cum"] == pytest.approx(40588.0)
    assert got["dq_vol_cum_yoy"] == pytest.approx((40588.0 - 37866.0) / 37866.0 * 100.0)


def test_parse_year_form_negative() -> None:
    got = mod.parse_periodic(_TEXT_YEAR)
    assert got == {"dq_vol_cum": pytest.approx(33016.9), "dq_vol_cum_yoy": pytest.approx(-5.7)}


def test_parse_failure_returns_none() -> None:
    assert mod.parse_periodic("") is None
    assert mod.parse_periodic("本期无运量披露内容") is None


@pytest.mark.parametrize("title,expect", [
    ("2013年年度报告", "2013-12-31"),
    ("2013年半年度报告", "2013-06-30"),
    ("2008年年度报告", "2008-12-31"),
])
def test_period_of(title, expect) -> None:
    assert mod._period_of(title) == expect


def test_fetch_reports_skips_summary(monkeypatch) -> None:
    link = ("http://www.cninfo.com.cn/new/disclosure/detail?stockCode=601006"
            "&announcementId=42&announcementTime=2009-08-28")
    df = pd.DataFrame({
        "公告标题": ["2009年半年度报告", "2009年半年度报告摘要", "第二届董事会公告"],
        "公告时间": ["2009-08-28", "2009-08-28", "2009-08-28"],
        "公告链接": [link, link, link],
    })
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: df)
    got = mod.fetch_reports(2009, 2009)
    assert len(got) == 1                                  # 摘要与无关公告被排除
    assert got[0]["announcement_id"] == "42"
    assert got[0]["period"] == "2009-06-30"


def test_collect_only_pre_2014_and_idempotent(monkeypatch) -> None:
    """只采 2014 前（月度简报已覆盖其后），且重复写入幂等。"""
    reports = [
        {"title": "2008年半年度报告", "date": "2008-08-28", "announcement_id": "A", "period": "2008-06-30"},
        {"title": "2015年年度报告", "date": "2016-04-01", "announcement_id": "B", "period": "2015-12-31"},
    ]
    monkeypatch.setattr(mod, "fetch_reports", lambda s, e: reports)
    monkeypatch.setattr(mod, "fetch_pdf_text", lambda aid, d: _TEXT_SENT)
    monkeypatch.setattr(mod, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2006-01-01", end="2020-12-31") == 1     # 2015 年报被跳过
    row = conn.execute("SELECT period, release_date, dq_vol_cum_yoy FROM company_periodic").fetchone()
    assert row["period"] == "2008-06-30"
    assert row["release_date"] == "2008-08-28"                              # 可得日 = 公告发布日
    assert row["dq_vol_cum_yoy"] == pytest.approx(17.8)
    assert mod.collect(conn, start="2006-01-01", end="2020-12-31") == 1
    assert conn.execute("SELECT COUNT(*) FROM company_periodic").fetchone()[0] == 1
