# 统一财经数据 API

`/api/v1/financial-data` 把多个事实与观点渠道放在同一套查询契约中：

- Tushare Pro：在线按需调用，`resource` 可使用当前 Token 有权访问的任意 `api_name`。
- 知识星球调研纪要：由知识星球 MCP 增量同步到本地 SQLite 后检索。
- 巨潮公告：自选股每 15 分钟增量同步，也支持按日期、股票、分类和关键词查询本地公告库。
- 天眼查：查询已同步的企业事实、风险与知识产权等事件。
- 统一监控事件：检索 Tushare、公告、新闻、知识星球和外部 API 归一化后的事实/观点事件。

服务沿用项目认证策略。`ADMIN_AUTH_ENABLED=true` 时，请先通过管理员登录取得
`dsa_session` Cookie；关闭认证时，调用方应只在可信内网或本机暴露端口。

## 启动

```bash
uvicorn server:app --host 127.0.0.1 --port 8000
```

需要在 `.env` 配置 `TUSHARE_TOKEN`。可选参数：

```dotenv
TUSHARE_API_TIMEOUT_SEC=30
TUSHARE_API_RATE_LIMIT_PER_MINUTE=480
```

默认 480 次/分钟，为 500 次/分钟权限留出余量；服务端会把配置值限制在 1～500。

## 统一查询入口

### Tushare 任意接口

```bash
curl -X POST http://127.0.0.1:8000/api/v1/financial-data/query \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "tushare",
    "resource": "daily",
    "params": {
      "ts_code": "600519.SH",
      "start_date": "20260801",
      "end_date": "20260819"
    },
    "fields": ["ts_code", "trade_date", "open", "high", "low", "close"]
  }'
```

也可以调用简化路径 `POST /api/v1/financial-data/tushare/{api_name}`，请求体只需
`params` 和 `fields`。接口不维护一份容易过时的白名单，因此新 Tushare 接口也能直接
通过；最终权限和字段规则以 Tushare 返回为准。

### 知识星球调研纪要

```bash
curl -X POST http://127.0.0.1:8000/api/v1/financial-data/query \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "zsxq",
    "resource": "research_notes",
    "params": {
      "query": "宇树科技",
      "symbol": "688836.SH",
      "page": 1,
      "page_size": 20
    }
  }'
```

专用检索入口为 `GET /api/v1/financial-data/research-notes`，支持：

- `group_id`
- `query`（标题和正文关键词）
- `symbol`（六位 A 股代码或 Tushare 代码）
- `digested`
- `created_from` / `created_to`（ISO 8601 或 `YYYYMMDD`）
- `page` / `page_size`

单篇详情使用 `GET /api/v1/financial-data/research-notes/{topic_id}`。

### 巨潮、天眼查与统一监控事件

统一入口还支持以下组合：

- `source=cninfo, resource=announcements`
- `source=tianyancha, resource=enterprise_events`
- `source=monitor, resource=events`
- `source=monitor, resource=announcements`

例如按自选股读取巨潮公告：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/financial-data/query \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "cninfo",
    "resource": "announcements",
    "params": {"symbol": "603306.SH", "days": 30, "page_size": 50}
  }'
```

`GET /api/v1/financial-data/sources` 会同时返回统一监控中的全部渠道、后台轮询周期、最近成功
时间、累计记录数和 `fresh/never/stale/failed` 新鲜度判断。新鲜度允许两个完整抓取周期和一分钟
网络宽限，避免把短暂排队误报为过期；历史成功但长期未更新的数据会明确标记为 `stale`。

其中 `tushare.market_themes` 每 30 分钟更新一次同花顺口径的概念资金流、行业资金流和
涨停池/炸板池/跌停池结构，对应 `moneyflow_cnt_ths`、`moneyflow_ind_ths` 和
`limit_list_ths`。这些数据以全市场事实快照入库，统一标记为中性，不自动推导买卖建议。
公司治理、资金和技术渠道还分别使用 `stk_managers`、市场汇总 `margin` 与
`stk_nineturn`，补充管理层名录、两融市场总量和神奇九转原始信号。开盘/收盘集合竞价
`stk_auction_o`、`stk_auction_c` 需要单独权限；当前部署凭证实测无权调用，因此没有用空值或
模拟结果伪装接入，待权限开通后再加入监控源。

## 从知识星球 MCP 同步

知识星球登录态留在 MCP，不写入本项目。常驻服务现在会直接连接 Streamable HTTP MCP，
按星球最新主题时间游标增量拉取，并按 `topic_id` 幂等新增或更新：

```dotenv
ZSXQ_MCP_AUTO_START=true
ZSXQ_MCP_POLL_SEC=30
ZSXQ_MCP_GROUPS=28855458518111:调研纪要
```

`ZSXQ_MCP_URL` 属于敏感配置，优先由部署环境注入；本机未配置该变量时会读取 Codex
`mcp_servers.zsxq.url`，不会把 URL 或密钥写入日志和 SQLite。`ZSXQ_MCP_GROUPS` 留空时复用
SQLite 中已经存在的星球，避免首次启动意外回填所有已加入星球的大量历史主题。

图片和文件采用远端按需模式：同步时只保存附件 ID、名称、尺寸等元数据，不请求文件内容，
也不写入本地目录；用户点击查看后，服务才通过 MCP 获取短期有效的知识星球签名链接并跳转。

主要接口：

- `GET /api/v1/financial-data/zsxq/sync/status`：MCP 可用性、每个星球游标、最近成功时间和错误。
- `POST /api/v1/financial-data/zsxq/sync`：立即执行一次 MCP 增量同步。
- `POST /api/v1/financial-data/zsxq/sync/worker/start` / `stop`：启停近实时轮询。
- `GET /api/v1/financial-data/research-notes/{topic_id}/media/{kind}/{asset_id}`：点击查看时获取最新远端签名并跳转，不在服务端落盘。

原来的离线 JSONL 导入仍作为恢复和批量迁移入口保留：

```bash
python scripts/import_zsxq_mcp_pages.py < zsxq-pages.jsonl
```

JSONL 每行既可以是 MCP 原始响应，也可以是带星球信息的包装对象：

```json
{"group_id":"288...","group_name":"调研纪要","payload":{"success":true,"topics_brief":[]}}
```

也可向 `POST /api/v1/financial-data/zsxq/import` 发送同样的 `mcp_page`。同步时保存正文、
作者、文件元数据、图片元数据、统计信息和可检索股票代码；临时签名参数不会写入 SQLite。
图片和文件内容不会下载到本地。页面只展示“查看”入口，用户点击后才向 MCP 申请新的
临时签名链接，并由浏览器直接访问知识星球远端地址。当前不执行图片 OCR。

导入成功后，每篇新增或正文发生变化的纪要会按内容哈希自动进入 DeepSeek 分析队列。
知识星球登录态和抓取能力仍由 MCP 持有；本服务不保存 Cookie，也不会绕过 MCP 直接抓取。

## 小作文雷达与实时分析

配置 `DEEPSEEK_API_KEY` 后，可通过 Web 左侧的“小作文雷达”查看近 30 天结果。生产或本机
常驻服务建议开启：

```dotenv
ESSAY_ANALYSIS_AUTO_START=true
ESSAY_ANALYSIS_MODEL=deepseek-v4-flash
ESSAY_ANALYSIS_BATCH_SIZE=12
ESSAY_ANALYSIS_CONCURRENCY=50
ESSAY_ANALYSIS_BACKFILL_DAYS=30
```

分析任务落入 SQLite，程序重启后会恢复；失败任务会按退避时间重试，模型、Prompt 版本或
正文哈希变化会自动重新分析。主要接口：

- `GET /api/v1/essay-radar/status`：覆盖率、队列和后台任务状态。
- `POST /api/v1/essay-radar/backfill`：回填指定天数并启动任务。
- `POST /api/v1/essay-radar/worker/start` / `worker/stop`：启停实时分析。
- `GET /api/v1/essay-radar/dashboard`：标签、情绪、股票热度和高重要度纪要。
- `GET /api/v1/essay-radar/insights`：14 日趋势、证据质量、模型共识/分歧、关注股日周月信号和高信息增量纪要。
- `GET /api/v1/essay-radar/analyses`：按关键词、情绪、类型、标签、股票与重要度筛选。
- `GET /api/v1/essay-radar/word-cloud`：股票、标签、主题的日/周/月词云及前周期变化。
- `GET /api/v1/essay-radar/daily-reports`：读取各模型独立生成的前一日小作文报告。
- `POST /api/v1/essay-radar/daily-reports/run`：立即生成或补跑指定日期日报。

日报使用 Asia/Shanghai 自然日口径。`ESSAY_DAILY_REPORT_MODELS` 以逗号配置多个模型；每个
日期与模型独立落入 `essay_daily_reports`，来源没有变化时不会重复消耗模型额度。日报 v3
先对前一日全部记录做确定性聚合，再按“关注股优先、来源/类别/信息性质分层、高重要度与
信息增量补齐”的策略选择最多 240 条代表性证据交给模型定性归纳；华懋科技与胜宏科技的
低频证据不会再被热门主题挤出。报告明确返回总记录数、关注股入选数、代表来源/类别数、
证据覆盖率、低置信记录和传闻记录，避免把样本归纳误称为全量逐篇阅读。跨模型共识和分歧
优先比较结构化主题方向与个股立场，不再要求不同模型生成完全相同的句子；只统计已完成日报，
不会在重算期间混入旧结果。

`ESSAY_WATCHLIST` 可配置小作文专项关注股，格式为 `ts_code:名称`、逗号分隔；默认值是
`603306.SH:华懋科技,300476.SZ:胜宏科技`。驾驶舱分别给出当日、近 7 日、近 30 日提及量，
并汇总最新论点、催化剂、风险、立场、重要度与置信度。当前实际有多少模型由配置决定；只有
一个模型时界面如实显示单模型结论，不虚构跨模型共识。

近实时链路为：常驻 worker 每 30 秒调用知识星球 MCP → 游标增量拉取 → SQLite 幂等入库
→ 图片/文件缓存与索引 → 自动进入 DeepSeek 队列 → 原始纪要立即进入统一监控事件流 →
AI 结果完成后更新同一事件。小作文雷达会分别显示 MCP 拉取状态和 DeepSeek 分析状态，
不再把“只轮询本地数据库”描述成实时获取。

## 数据源状态

`GET /api/v1/financial-data/sources` 返回 Tushare 是否配置、已同步的知识星球、纪要数量、
最新纪要时间和最近同步时间。交互式 OpenAPI 文档位于 `/docs`。
