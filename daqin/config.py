"""daqin 全局配置：路径与常量。

与 `copper/config.py` 的路径约定一致：
- 数据目录支持 `DAQIN_DATA_DIR` 环境变量覆盖（容器内挂载 `/app/data`）；
- **阈值不在此文件**：一律从 `daqin/thresholds.yaml` 读取（见 `daqin/thresholds.py`，06 D-11/M0-1）。
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

PACKAGE_DIR = Path(__file__).resolve().parent          # daqin/
ROOT = PACKAGE_DIR.parent                              # 仓库根

DATA_DIR = Path(os.environ.get("DAQIN_DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "daqin.db"

THRESHOLDS_PATH = PACKAGE_DIR / "thresholds.yaml"
MANUAL_DATA_DIR = PACKAGE_DIR / "manual_data"
OUTPUT_DIR = PACKAGE_DIR / "output"

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
