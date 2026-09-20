# 铜价追踪看板 · 铜陵有色（000630）

> 本仓库含两个独立模块：本看板（`copper/`）＋ **大秦铁路防御-补跌监控**（`daqin/`，开发中）。
> daqin 的设计文档在 `docs/daqin/`、阈值在 `daqin/thresholds.yaml`、数据独立存储于 `data/daqin.db`；
> 两者互不干扰，共用 `scripts/scheduler.py` 调度与容器部署（M1/M6 逐步落地）。

一个为「持有铜陵有色」场景定制的每日追踪看板：不只盯铜价，而是把**冶炼利润**（TC、硫酸、升贴水）、
**库存**、**股票估值与同行对比**、**事件催化剂**放在一起看。

## 为什么不能只看铜价

铜陵有色以冶炼为主、矿山自给率有限，利润 ≈ **加工费（TC/RC）+ 副产硫酸 + 库存损益**。
铜价高而 TC 低时，冶炼利润反而可能被挤压。所以本看板把「价格端」和「利润端」分开呈现。

## 看板内容

| 页面 | 内容 |
|---|---|
| 总览 | 三市场铜价（统一折算美元/吨）、铜陵有色相对沪铜强弱、利润代理指数、LME/上期所库存 |
| 利润 | 冶炼利润代理指数、TC 加工费、长江现货升贴水、上期所基差、硫酸价（有数据时） |
| 股票 | 000630 收盘、PB 及五年分位、相对强弱、与江铜/云铜/紫金归一化对比 |
| 事件 | 手动维护的事件时间线（markdown），可叠加到价格图 |

页面顶部有**数据新鲜度横幅**：每个数据集距上次成功采集的时间一目了然，断更自动亮红灯。

## 架构

```
采集（akshare + ccmn 爬虫）  →  SQLite（幂等长表）  →  Streamlit 看板
        ↑ 每天 21:30（北京时间）定时采集，启动补跑，单点失败不影响其余
```

- **存储**：`data/copper.db`，主键 (dataset, date, key)，重跑不重不漏，缺口自动回补。
- **调度**：`scheduler` 服务每天 21:30 跑一轮（A股/沪铜日盘已收盘、LME 主时段已过、铜日报已发布）。
- **新鲜度**：每个数据集记录 last_success/last_error，看板横幅直接展示。

## 数据源与口径

| 数据 | 数据集名 | 来源 | 口径 |
|---|---|---|---|
| 沪铜主力日线 | `cu_shfe` | 新浪期货 | 元/吨，主力连续，日盘收盘 |
| LME 铜日线 | `cu_lme` | 新浪外盘（CAD） | 美元/吨 |
| COMEX 铜日线 | `cu_comex` | 新浪外盘（HG） | 美分/磅 ×22.0462/100 折算美元/吨 |
| 长江现货均价/升贴水 | `spot_ccmn` | 长江有色《铜日报》文章解析 | 元/吨；从部署日起每日积累 |
| TC / 硫酸价 | `spot_ccmn` | 同上（文章提及数字时才有） | 美元/干吨、元/吨，稀疏观测 |
| 上期所现货与基差 | `spot_basis` | 99 期货期现表 | 元/吨，约 5 年日度 |
| LME 铜库存 | `inv_lme` | 东方财富数据中心 | 吨（日度，含注册/注销仓单） |
| 上期所铜库存 | `inv_shfe` | 东方财富 | 吨（周频口径） |
| A 股日线 | `stock_*` | 新浪 | qfq 前复权；000630 另存不复权价用于 PB |
| 每股净资产 | `bps_000630` | 新浪财务指标 | 季频，看板内前向填充 |
| USDCNY | `usdcny` | 中行折算价 | 每日 |
| 美元指数 | `usd_index` | 东方财富 | best-effort，源不稳定时横幅会亮黄/红灯 |

### 已知限制（诚实说明）

1. **TC 与硫酸没有稳定免费 API**。当前依赖《铜日报》文章中出现具体数字时解析入库，属稀疏观测；
   分析层按 45 天上限前向填充。数据从部署日起积累，历史无法回补。
2. **COMEX 铜库存**没有找到免费接口（akshare 的 COMEX 库存仅金/银），故库存模块只含 LME + 上期所。
3. **利润代理指数**是趋势代理：`TC ×(1/0.25) ×汇率 + 硫酸 ×3.5 吨/吨铜`，参数为行业粗略假设，
   只看方向与拐点，不代表精确利润。参数在 `copper/config.py` 可调。
4. 三市场对比图上，沪铜折美元高于 LME 是因为**沪铜报价含 13% 增值税**，属正常，看趋势背离即可。

## 本地开发（Windows）

```bash
pip install -r requirements.txt
python -m scripts.update_once          # 首次采集（约 5–10 分钟，含 5 年回补）
streamlit run copper/dashboard/app.py  # 打开 http://localhost:8501
```

注意：本机若开着系统代理（如 clash），`copper/netutil.py` 默认屏蔽注册表代理直连；
东财系接口（美元指数、库存）在部分代理环境下会失败，服务器上通常正常。

## 服务器部署（Docker Compose）

```bash
# 1. 上传项目目录到服务器，安装 Docker 后：
cp .env.example .env && vim .env        # 填 DOMAIN / ACME_EMAIL

# 2. 生成看板登录密码哈希（bcrypt），粘贴回 .env 的 CADDY_HASH
docker compose --profile prod run --rm caddy hash-password

# 3. 启动（dashboard + scheduler + caddy）
docker compose --profile prod up -d --build

# 4. 首次采集（也可以等 scheduler 启动时自动补跑，二选一）
docker compose exec scheduler python -m scripts.update_once

# 查看日志
docker compose logs -f scheduler dashboard
```

完成后浏览器访问 `https://你的域名`，用户名 `admin` + 你设置的密码。

> 只在内网试跑：`docker compose up -d dashboard scheduler`，然后访问服务器 `127.0.0.1:8501`。

## 事件维护

编辑 `events/events.md`，每行一条：

```markdown
- 2026-09-18 | 公告 | 三季度业绩预告发布 | 简要备注
- 2026-09-01 | 检修 | 金冠铜业阴极铜系统检修一周 |
```

保存后刷新看板即生效；总览页可勾选把事件叠加到价格图（近 400 天）。

## 日常维护

- **某个数据集断更**：看板顶部横幅会标红。数据源接口偶尔改版属正常，
  先点「立即更新数据」重试；持续失败时 `docker compose logs scheduler | grep FAIL` 看具体报错。
- **手动补历史**：`docker compose exec scheduler python -m scripts.update_once --full`（幂等，随便跑）。
- **只跑某个数据集**：`python -m scripts.update_once --full --names cu_shfe spot_ccmn`。
- **改假设参数**：TC 折算品位、吨铜副酸量、新鲜度阈值都在 `copper/config.py`。
- **二期预告**：推送告警层（沪钢单日涨跌 >2%、TC 创新低、股价破均线 → 微信/邮件），架构已预留。

## 项目结构

```
copper/
  config.py        全局配置与口径假设
  netutil.py       代理处理 / 重试 HTTP / akshare 重试
  db.py            SQLite 幂等存储 + freshness
  sources/
    ak_api.py      akshare 接口标准化封装
    ccmn.py        长江有色《铜日报》爬虫
  collectors.py    采集器注册表（15 个数据集）
  pipeline.py      采集编排与失败隔离
  analytics.py     利润指数 / 相对强弱 / PB / 同行对比
  dashboard/       Streamlit 看板（app + 4 页 + 组件 + 数据层）
scripts/
  update_once.py   手动采集入口
  scheduler.py     常驻调度（21:30 北京时间）
tests/             看板冒烟测试（streamlit AppTest）
daqin/             大秦铁路防御-补跌监控（开发中，与 copper/ 平级，独立 data/daqin.db）
  thresholds.yaml  策略阈值（外置可调）
docs/daqin/        大秦模块设计文档集（00-README ~ 05-开发计划）
```
