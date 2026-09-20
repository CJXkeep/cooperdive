"""大秦铁路防御-补跌监控（daqin-monitor）模块。

与仓库内 copper（铜陵有色看板）平级独立：
- 数据：data/daqin.db（独立 SQLite，与 data/copper.db 互不干扰）
- 阈值：daqin/thresholds.yaml
- 手工数据：daqin/manual_data/
- 回测输出：daqin/output/
- 设计文档：docs/daqin/

状态：M1 数据层待开发，当前仅含阈值配置。
"""

__version__ = "0.1.0"
