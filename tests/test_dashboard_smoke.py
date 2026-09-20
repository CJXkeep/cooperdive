"""看板冒烟测试：AppTest 依次渲染四个页面，断言无异常。

运行：python -m pytest tests/test_dashboard_smoke.py -q  （或直接 python tests/test_dashboard_smoke.py）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

APP = ROOT / "copper" / "dashboard" / "app.py"


def test_pages_render_without_exception() -> None:
    for page in ("总览", "利润", "股票", "事件"):
        at = AppTest.from_file(str(APP), default_timeout=120)
        at.run()
        assert not at.exception, f"页面[默认]渲染异常: {at.exception}"
        radios = at.radio
        if radios:
            radios[0].set_value(page).run()
        assert not at.exception, f"页面[{page}]渲染异常: {at.exception}"
        print(f"[OK] {page}")


if __name__ == "__main__":
    test_pages_render_without_exception()
    print("all pages OK")
