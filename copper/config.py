"""全局配置：路径、标的、口径假设、时区。

所有金额/单位约定（重要）：
- 沪铜/现货/升贴水：人民币 元/吨
- LME：美元/吨；COMEX：美分/磅（入库时统一折算为 美元/吨，1 吨 = 2204.62 磅）
- TC/RC：美元/干吨（铜精矿加工费）
- USDCNY：美元兑人民币（中行折算价 / 100）
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 项目根目录：本地开发与容器内一致（容器挂载 /app）
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("COPPER_DATA_DIR", ROOT / "data"))
EVENTS_DIR = Path(os.environ.get("COPPER_EVENTS_DIR", ROOT / "events"))
DB_PATH = DATA_DIR / "copper.db"
EVENTS_FILE = EVENTS_DIR / "events.md"

# 是否屏蔽 Windows/桌面环境注册表里的系统代理（服务器上无代理，置 1 无副作用）
DISABLE_SYSTEM_PROXY = os.environ.get("COPPER_DISABLE_SYSTEM_PROXY", "1") == "1"

HISTORY_YEARS = 5  # 历史回补深度


def today_cn() -> date:
    """以北京时间计的"今天"。"""
    return datetime.now(TZ).date()


def history_start() -> date:
    return today_cn() - timedelta(days=365 * HISTORY_YEARS + 10)


def iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


# ---------------- 标的与代码 ----------------
STOCK_MAIN = "sz000630"  # 铜陵有色
STOCK_MAIN_NAME = "铜陵有色"
STOCK_PEERS = {
    "江西铜业": "sh600362",
    "云南铜业": "sz000878",
    "紫金矿业": "sh601899",
}

# ---------------- 利润指数假设参数（粗略口径，只求趋势方向正确） ----------------
# TC（美元/干吨）按铜精矿入炉品位折算成"每吨铜的加工费收入"：
#   1 吨阴极铜 ≈ 1/品位 干吨精矿，品位默认 25%
CONC_GRADE = 0.25
TC_PER_TON_CU = 1.0 / CONC_GRADE
# 吨铜副产硫酸（吨），行业常见 3~4 吨
H2SO4_PER_TON_CU = 3.5

# TC 为稀疏观测（新闻稿口径），前向填充的最大跨度（天），超过则视为断档
TC_FFILL_LIMIT_DAYS = 45

# ---------------- 数据新鲜度阈值（天）：超过则看板亮黄/红灯 ----------------
FRESH_WARN_DAYS = 2
FRESH_BAD_DAYS = 5

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
