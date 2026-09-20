"""常驻调度器：容器/服务器内运行，启动即补跑一轮，此后每天北京时间 21:30 采集。

21:30 的理由：A股与沪铜日盘已收盘、LME 当日主要交易时段已过、长江现货日报已发布。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.blocking import BlockingScheduler

from copper import config, pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt=config.DATETIME_FMT,
)
log = logging.getLogger("scheduler")


def run_once() -> None:
    log.info("开始采集…")
    results = pipeline.update_all()
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    log.info("采集完成 %d/%d 成功", len(ok), len(results))
    for r in bad:
        log.warning("  失败: %s — %s", r.name, r.error[:200])


def main() -> None:
    log.info("调度器启动：启动补跑一轮，然后每天 %s 时区 21:30 采集", config.TZ)
    run_once()

    sched = BlockingScheduler(timezone=str(config.TZ))
    sched.add_job(
        run_once,
        "cron",
        hour=21,
        minute=30,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,  # 错过 1 小时内仍然补跑
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器退出")


if __name__ == "__main__":
    time.sleep(1)  # 给 dashboard 服务留出启动时间，避免首次同时写库
    main()
