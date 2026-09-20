"""命令行采集入口：python -m scripts.update_once [--full]

--full 忽略"近期已更新"跳过逻辑，强制全部重跑（幂等，安全）。
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="铜价看板数据采集")
    parser.add_argument("--full", action="store_true", help="强制重跑全部数据集")
    parser.add_argument("--names", nargs="*", default=None, help="只跑指定数据集")
    args = parser.parse_args()

    from copper import pipeline

    results = pipeline.update_all(only_stale=not args.full, names=args.names)
    width = max(len(r.name) for r in results)
    for r in results:
        flag = "OK  " if r.ok else "FAIL"
        info = f"{r.rows} 行, {r.secs:.1f}s" if r.ok else r.error[:120]
        print(f"[{flag}] {r.name:<{width}}  {info}")
    failed = [r for r in results if not r.ok]
    print(f"\n完成: {len(results) - len(failed)}/{len(results)} 成功")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
