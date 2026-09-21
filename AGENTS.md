# AGENTS.md

> 本仓库的 Agent 入口规则（Codex / Claude Code / Copilot / Cursor 等均识别本文件名）。
> **保持短小**：这里只放「不可违反的约束」与「去哪读细节」；详细设计一律在 `docs/`，不在本文件展开。

## 1. 仓库结构与边界

- `copper/`：铜陵有色看板（**已上线运行**）；`daqin/`：大秦铁路监控（**设计已定稿、代码未开工**）。
- 两模块**业务代码零交叉**；只共用仓库级设施：`storage/`（连接与 freshness，D-20）、`copper/netutil.py`（重试/代理，D-19）。
- 动手改 `daqin/` 前，先读 `docs/daqin/06-设计决议与迭代计划.md`（开发基准）与 `docs/daqin/iterations/` 中对应迭代文档。

## 2. 硬约束（违反即返工）

1. **阈值不得内嵌**：`daqin` 所有阈值/参数从 `daqin/thresholds.yaml` 读取（经 `daqin/thresholds.py` 校验）；代码内出现数字阈值即违规。
2. **信号层纯函数**：`signals/` 只读 `metrics_daily` 单行，禁止 IO 与回查历史表；`run_daily` 不落库，落库只发生在 `cli.py`（D-21）。
3. **回测与实盘共用同一套函数**：对齐（`indicators/align.py`）与信号严禁分叉实现；回测引擎的防未来函数断言不得绕过（D-13）。
4. **数据不得伪造**：缺失数据置 False/NULL 并在报告标注来源缺失；禁止插值伪造（04 §3）。
5. **改动 `copper/` 后必须**：跑 `python -m pytest tests/test_dashboard_smoke.py -q` 并通过；不改变既有数据口径（口径假设集中在 `copper/config.py`）。
6. **依赖**：新增依赖同步 `requirements.txt`；升级 `akshare`/`pandas`/`streamlit` 前先跑冒烟测试。
7. **凭据不入库**：密钥/连接串一律走环境变量；`data/`、`.env` 不入 git。

## 3. 文档规则（决策留痕）

- **非平凡变更**（改行为/接口/表结构/流程/测试策略）必须在**同一次提交**中更新文档：
  - 新决策 → `docs/daqin/06` 追加 `D-xx`（编号连续，含理由与备选）；
  - 实现过程与偏差 → 对应 `docs/daqin/iterations/Mx-*.md` 的「实现记录」；
  - 被否决的路线 → `docs/daqin/06` §8「已否决方案」（不删除）。
- **one home per fact**：表结构的唯一权威是 06 §3；同一事实不要在别处重复定义，只放链接。
- **Kill Spec-speak**：写「已实现的事实 + 为什么」，不写「计划/预计」；没完成的一律进「遗留与偏差」。

## 4. 验收命令

```bash
python -m pytest tests/test_dashboard_smoke.py -q          # copper 冒烟（改 copper 必跑）
# daqin（M0 起，Gate 0）：
python -m daqin.cli collect --names stock_daily macro_daily
python -m daqin.cli compute --start 2020-01-01 --end 2020-12-31
python -m daqin.cli signal --date 2020-03-23
pytest tests/daqin -q
```

## 5. 指路

| 要做什么 | 先读 |
|---|---|
| daqin 设计与决议 | `docs/daqin/00-README.md`（索引）→ `06`（基准） |
| 开始/继续某个迭代 | `docs/daqin/iterations/README.md`（模板与约定）+ `Mx-*.md` |
| 改 copper 看板 | `README.md`、`copper/config.py` |
| 部署与运维 | `README.md`（部署章节）、`docker-compose.yml` |
