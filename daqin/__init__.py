"""大秦铁路防御-补跌监控（daqin-monitor）模块。

与仓库内 copper（铜陵有色看板）平级独立：
- 数据：data/daqin.db（独立 SQLite，与 data/copper.db 互不干扰）
- 阈值：daqin/thresholds.yaml
- 手工数据：daqin/manual_data/
- 回测输出：daqin/output/
- 设计文档：docs/daqin/（06-设计决议与迭代计划.md 为开发基准）

状态：设计已定稿 v0.2（含 19 项设计决议与 M0-M6 迭代计划），
      代码未开工，下一步执行 docs/daqin/06 §4.1 的 Gate 0 可行走骨架。
"""

__version__ = "0.2.0"
