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

## 统一事实库状态与安全优化

行情、知识星球、情报、分析和持仓继续使用同一个 SQLite 事实库，避免多个业务数据库之间复制、对账和读到不同版本的数据；API 将这些表按逻辑数据域展示：

- `GET /api/v1/financial-data/storage/status`：返回数据库/WAL 文件体积、关键 PRAGMA、各表记录数、最新数据时间、目标刷新周期和 `fresh/stale/market_closed/empty` 状态。
- `GET /api/v1/financial-data/storage/status?include_integrity=true`：额外执行只读 `quick_check`，适合人工巡检，不建议高频调用。
- `POST /api/v1/financial-data/storage/optimize`：执行有界的查询规划统计维护与被动 WAL 检查点；不会 `VACUUM`、删除业务记录或打断实时采集。

文件型 SQLite 默认使用 WAL、`synchronous=NORMAL`、外键校验、5 秒 busy timeout 和 1000 页自动检查点。后台默认每 60 分钟做一次安全维护，可用 `DATA_STORAGE_MAINTENANCE_AUTO_START` 和 `DATA_STORAGE_MAINTENANCE_MINUTES` 调整。

“实时”按上游能力分级：盘中自选股和指数可按秒采集，知识星球 MCP 与财经快讯近实时增量，公告和技术/资金数据按分钟到小时轮询，财报、股东与企业工商数据按披露或上游更新频率同步。低频披露数据会显示来源最新时间，不会用重复请求或模拟值伪装秒级实时。

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
按星球已完整覆盖的时间游标增量拉取，并按 `topic_id` 只插入尚未入库的主题：

```dotenv
ZSXQ_MCP_AUTO_START=true
ZSXQ_MCP_POLL_SEC=30
ZSXQ_MCP_GROUPS=28855458518111:调研纪要
```

`ZSXQ_MCP_URL` 属于敏感配置，优先由部署环境注入；本机未配置该变量时会读取 Codex
`mcp_servers.zsxq.url`，不会把 URL 或密钥写入日志和 SQLite。`ZSXQ_MCP_GROUPS` 留空时复用
SQLite 中已经存在的真实数字 ID 星球，不把本地生成的录音纪要来源当成远端星球；本地没有可用来源时才向 MCP 查询已加入星球。生产恢复应显式配置需要回补的已授权星球 ID，避免意外扩大范围。

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
作者、文件元数据、图片元数据和可检索股票代码；点赞等互动统计不参与信息内容更新，临时签名参数不会写入 SQLite。
图片和文件内容不会下载到本地。页面只展示“查看”入口，用户点击后才向 MCP 申请新的
临时签名链接，并由浏览器直接访问知识星球远端地址。当前不执行图片 OCR。

直接 MCP 增量同步只自动分析原文创建时间在最近 24 小时内、首次发现且有正文信息的主题。断线追赶时首次发现的旧文只入库，不自动分析；纯录音、文件或图片附件主题同样只入库，已有 ID 不重新刷新正文、点赞或既有分析。显式 JSONL / `zsxq/import` 导入仍保留原有按内容哈希新增或更新的能力，其分析入队参数与后台历史任务独立，不应把批量旧资料误提交到默认自动分析的导入入口。
知识星球登录态和抓取能力仍由 MCP 持有；本服务不保存 Cookie，也不会绕过 MCP 直接抓取。

## 机构段子与录音的实时分析

配置 `DEEPSEEK_API_KEY` 后，可通过 Web 左侧的“机构段子与录音”查看近 30 天结果。生产或本机
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
- `GET /api/v1/essay-radar/deep-insights`：返回来源→主题→个股→催化/风险的真实同文共现关系、14 日情绪脉冲、主题热力、个股讨论动量、多空分歧、待核验队列与证据漏斗；`days` 控制语料窗口，`trend_days` 控制趋势窗口。
- `GET /api/v1/essay-radar/feed`：以全部已入库原始纪要为主表检索，`days=0` 查询整个 SQLite 小作文库；关键词覆盖标题、原文、作者、知识星球、股票代码以及已有 AI 摘要/标签，未入队、排队中、失败或尚未完成 AI 分析的纪要也会返回。可用 `analysis_status` 区分 `completed`、`uncompleted`、`not_queued`、`pending`、`processing` 和 `failed`。
- `GET /api/v1/essay-radar/analyses`：仅查询已创建 AI 任务的分析记录，保留用于情绪、类型、标签、股票与重要度等结构化筛选。
- `GET /api/v1/essay-radar/word-cloud`：股票、标签、主题的日/周/月词云及前周期变化。
- `GET /api/v1/essay-radar/daily-reports`：读取各模型独立生成的前一日小作文报告。
- `POST /api/v1/essay-radar/daily-reports/run`：立即生成或补跑指定日期日报。
- `GET /api/v1/financial-data/research-notes/audio-files`：严格按录音文件名检索，一个源文件一行；服务端按稳定文件 ID 去重并排除 AI 生成纪要，避免生成结果再次被当成源录音。
- `POST /api/v1/financial-data/research-notes/audio-files/batch-download-tasks`：将最多 100 个勾选录音提交到后台下载与 ZIP 打包，立即返回持久化任务编号。
- `GET /api/v1/financial-data/research-notes/audio-files/batch-download-tasks/{task_id}`：读取逐文件、源文件字节与压缩包进度；页面离开后任务继续运行。
- `GET /api/v1/financial-data/research-notes/audio-files/batch-download-tasks/{task_id}/download`：下载已完成 ZIP；任务与压缩包默认保留 48 小时。
- `GET /api/v1/financial-data/research-notes/audio-analysis/capability`：检查语音转写与文本分析上游是否完整配置。
- `POST /api/v1/financial-data/research-notes/audio-analysis/tasks`：把最多 `AUDIO_ANALYSIS_MAX_FILES` 个勾选录音提交为当前用户独立的后台任务；任务依次临时下载、语音转写、DeepSeek 分段提取与合并分析。
- `GET /api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}`：读取下载、转写、分析和报告生成的真实进度及页面内报告数据。
- `POST /api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/retry`：使用持久化的原录音清单重试失败任务；排队或执行中的任务直接返回当前状态。
- `GET /api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/transcripts/{file_id}`：按文件读取带时间戳、说话人的逐字稿，供页面内证据回溯。
- `GET /api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/download?format=zip|md|docx|json`：下载完整资料包、录音小作文 Markdown、Word 报告或结构化结果；完整包同时包含逐录音转写文本，不包含源音频。

录音打包及转写任务的状态读取遇到临时网络错误时，页面保留任务编号和最近已知进度，按 5、10、20、30 秒退避重试，最长间隔 30 秒；不会因为一次状态请求失败就认定后台任务失败或清空下载入口。页面隐藏期间暂停查询，重新可见后恢复。只有接口明确返回 404/410 才清理当前失效任务句柄并提示检查任务记录；服务器返回的真实失败状态仍显示原原因，供用户重试。此恢复机制不重复提交 AI 或打包任务。

创建任务时可额外传入 `hotwords`（最多 100 个金融、公司或产品术语）和 `speaker_count`（2～20）；任务输入会随状态持久化，服务器重启后自动从原录音清单续跑。登录用户创建的纪要只保留在其本人任务台账，不写入公共 `research_notes` 或 `monitoring_events`；只有明确以系统身份运行的共享任务才可发布到统一检索与情报库，防止自定义标题和关注问题跨用户可见。

录音 AI 纪要是明确按需功能，不会对全部录音自动消耗额度。默认转写链路是阿里云百炼
`qwen-audio-3.0-asr-flash-filetrans` 异步录音文件识别：配置 `DASHSCOPE_API_KEY` 即可，
也会复用已有 `LLM_DASHSCOPE_API_KEY`。系统向阿里云提交知识星球临时音频地址，轮询真实任务
状态，取回带句级时间戳与说话人编号的转写文本；`DASHSCOPE_ASR_DIARIZATION_ENABLED=true`
默认开启说话人分离。需要切回其他兼容服务时，可设置
`AUDIO_TRANSCRIPTION_PROVIDER=openai_compatible`，并配置原有
`AUDIO_TRANSCRIPTION_API_KEY`、`AUDIO_TRANSCRIPTION_BASE_URL` 和
`AUDIO_TRANSCRIPTION_MODEL`。

文本整理继续使用 `DEEPSEEK_API_KEY` 和 `AUDIO_MEMO_ANALYSIS_MODEL`（留空复用
`ESSAY_ANALYSIS_MODEL`）。源音频只存在于任务临时目录，完成转写或任务失败后都会删除；
报告按用户隔离，纪要资料包默认保留 7 天，逐字稿及其可发现索引长期保留并继续支持页内查看与下载。阿里云文件转写需要公网可访问的录音 URL；若知识星球临时
地址无法被阿里云读取，任务会保留上游错误而不会生成空纪要。

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

近实时链路为：常驻 worker 按 `ZSXQ_MCP_POLL_SEC`（默认 10 秒，上方示例为 30 秒）调用知识星球 MCP → 游标增量拉取 → SQLite 幂等入库
→ 图片/文件元数据与索引 → 符合实时正文条件的新增记录进入分析队列 → 原始纪要进入统一监控事件流 →
AI 结果完成后更新同一事件。“机构段子与录音”会分别显示 MCP 拉取状态和 DeepSeek 分析状态，
不再把“只轮询本地数据库”描述成实时获取。

### 一年 / 两年历史纪要与固定范围恢复

历史恢复是“扫描指定范围、只插缺失 ID”，不是“从库中最早一条继续往前”。即使最早和最新日期都已存在，任务仍扫描中间日期，补齐迁移、断线造成的空档；每页只请求主题列表，不为已入库主题逐条取详情，不刷新点赞，不覆盖正文或已有 AI 分析。图片、PDF、录音只保存索引和源文件元数据，不批量下载、不自动转写。

以下写入口需要管理员会话；已审核的普通用户也不能启动全库回填。沿用管理员接口现有拒绝契约：非管理员返回 HTTP 401，且不会创建后台任务。

- `POST /api/v1/financial-data/zsxq/history/backfill`：兼容 `{"years":1}` / `{"years":2}`；新任务把相对范围冻结为确定的 UTC 起止时间，重试同一未完成范围时复用原断点。
- 也可同时传 `start_date`、`end_date`，格式为 `YYYY-MM-DD`，表示 **Asia/Shanghai 自然日，两端都包含**。两项必须一起提供，起止顺序必须有效，范围最多两年（兼容闰年与结束日）。明确日期优先于相对 `years`。
- 接口不接受单次 `group_id` 筛选；会处理 `ZSXQ_MCP_GROUPS` 配置的全部星球。若要恢复两个星球，先确认配置恰好包含两个已授权 ID；在任务状态 `groups` 中逐个核对，不以总进度代替每个星球的完成情况。

固定两年范围示例，使用已登录管理员的 Cookie 文件，不把会话值写入代码或文档：

```bash
curl --fail-with-body -b /path/to/admin-cookie.txt \
  -X POST http://127.0.0.1:8000/api/v1/financial-data/zsxq/history/backfill \
  -H 'Content-Type: application/json' \
  -d '{"years":2,"start_date":"2024-09-15","end_date":"2026-09-15"}'
```

上述请求的持久范围为 UTC `[2024-09-14T16:00:00Z, 2026-09-15T16:00:00Z)`，不会随重启或跨日变化。返回 `started` 仅表示已启动后台处理，不代表两年数据已全部恢复；来源若未保存或无权限提供某段数据，不会凭空生成。

#### 断点与实时同步互不阻塞

每个星球在 `zsxq_sync_states` 中分别保存 `history_checkpoint_json` 和 `incremental_checkpoint_json`。每页成功提交资料后才保存下一页游标；请求失败、响应缺字段、分页游标不前进或落库失败均不会跳过失败页。资料已提交但断点写失败时，下次重放该页并跳过已有 ID，保证不漏数据。相同固定范围已经 `completed` 后再次调用不会重复扫描；需要检查新日期时创建新的固定范围。

持久范围仍使用 UTC；发送给 MCP 的 `end_time` 单独转换为接口接受的上海毫秒格式，例如 `2026-09-16T00:00:00.000+0800`，不能直接发送 UTC 微秒 `Z` 格式（接口会返回无效时间错误）。转换时对不足一毫秒的排他上界向上取整，召回后仍按原 UTC 半开区间过滤，避免截断末毫秒造成漏文。此修复不修改已保存断点，旧失败任务由常驻 worker 原位续跑，无需重置或重新入库。

历史和实时采用不同的跨进程执行锁；只有很短的页面提交阶段共享写锁，远端请求、限流等待和分页间隔不占实时锁。文件锁位于数据库同目录的 `.zsxq-sync-locks/`，API 与采集进程必须挂载同一数据目录；进程退出后内核自动释放锁，不需要删除锁文件，也不提供跨主机分布式锁保证。

`ZSXQ_MCP_HISTORY_MAX_PAGES` 控制每批分页安全上限；未配置时为 `max(500, lookback_days * 20)`，最终限制在 1～50,000 页。达到上限但未覆盖完，状态为 `incomplete`，常驻 worker 在约 60 秒退避加下一次轮询后继续原断点。短暂失败也保留断点后重试，不会跳过失败窗口把任务标成完成。

实时增量每轮仍受 `ZSXQ_MCP_MAX_PAGES_PER_SYNC`（默认 10 页，每页 30 条）限制。超过 300 条时，先保存未补完区间，**不提前推进已完整覆盖水位**；随后每轮先取最新头页保证新消息可见，再继续补旧缺口。发现时间超过 24 小时的旧文不进入自动 AI 队列。

正常 FastAPI lifespan 在 `ZSXQ_MCP_AUTO_START=true` 时调用 `ZsxqMcpSyncWorker.start()`；每次后台轮询都会先检查磁盘中未完成、失败或暂停的历史断点，再以其原始起止时间恢复历史线程，同时继续实时线程。守护进程也可通过同一 `start()` 路径恢复停止的 worker。如果关闭了自动启动且没有其他常驻进程启动该 worker，单次手动任务到分页上限后不会自行拥有轮询器，需要管理员显式调用 `/zsxq/sync/worker/start`。重启能恢复的是已持久化断点；若在连接 MCP、尚未发现星球并首次写入断点前就中止，需重新提交同一固定日期请求。

#### 如何验证真实进度与入库日期

`GET /api/v1/financial-data/zsxq/sync/status` 的 `available=true` 只表示配置了 MCP 地址，不是远端请求成功证明。应逐个检查 `groups[].history_checkpoint`：

| 字段 | 核验含义 |
| --- | --- |
| `start_at` / `end_at` | 本次冻结的 UTC 半开区间 |
| `status` | `incomplete`、`paused`、`failed` 或 `completed`；必须两个星球分别完成 |
| `next_end_time` / `pages_fetched` / `updated_at` | 分页游标向更早日期推进，已提交页数和断点时间持续变化 |
| `coverage_percent` | 已扫描的日期跨度覆盖率，不是已知全量文件数百分比；日期分布稀疏时增长不均匀 |
| `received` / `unchanged` | 已确认在库的主题计次、跳过的已有 ID；可能包含重放页/重复项，不等于全库去重总数 |
| `created` / `updated` | 随断点成功确认的新增/更新计数；恢复任务不更新已有记录。若先插入成功、断点失败后重放，`created` 可能低于实际净增，应以数据库去重统计为准 |
| `error` | 安全错误摘要；失败不得当成完成 |

`history_backfill.running` 是当前 API 进程内的线程状态；采集由另一个进程执行时，应以共享 `groups` 断点推进和实际入库时间判断，不把一个进程的 `running=false` 当成全局停止。`last_topic_at` 是实时已完整覆盖水位，历史任务不会用旧日期覆盖它。

核验原文日期可以按星球和日期调用 `GET /api/v1/financial-data/research-notes?group_id=实际ID&created_from=20240915&created_to=20240916&page_size=20`，查看响应中的 `created_at`，不能把当前 `synced_at` 当成录音/纪要原始日期。`GET /api/v1/essay-radar/historical-backlog` 提供全库总数、原文日期范围和最近入库时间；最早/最晚日期齐全仍不能单独证明中间没有缺口。

需要确认迁移后的实际净增时，可在数据库文件上用 SQLite **只读模式**执行以下聚合，不初始化应用、不运行维护脚本：

```sql
PRAGMA query_only=ON;
SELECT group_id, COUNT(*) AS unique_topics,
       MIN(created_at) AS earliest_original_utc,
       MAX(created_at) AS latest_original_utc,
       MAX(synced_at) AS latest_ingested_utc
FROM research_notes
WHERE group_id IN ('第一个实际星球ID', '第二个实际星球ID')
  AND created_at >= '2024-09-14 16:00:00'
  AND created_at <  '2026-09-15 16:00:00'
GROUP BY group_id;
```

恢复前后分别保存上述聚合；还可以按 `date(created_at, '+8 hours')` 分组比较每日数量，结合每组已完成断点检查中间月份是否实际补齐。无需查询用户账号、密钥或正文内容来做进度验证。

历史恢复始终 `enqueue_analysis=false`，包括扫描区间内最近日期的主题；不清除已有分析。需要分析未处理旧文时，使用下述按篇数补分析入口，明确选择数量后再产生模型消耗，不能把恢复任务当成全量 AI 重跑。

### 按篇数补分析历史小作文

数据管理页将“新增小作文实时分析”和“历史小作文补分析”分开。历史补分析只查询 SQLite 中已经
入库且尚未创建 `essay_analysis_records` 任务的纪要，不重新请求知识星球，也不会把失败、排队中或
已经完成的任务重复加入队列。用户可选择 50、100、500、1000、2000 或 5000 篇，并选择最近优先
或最早优先；实际剩余量少于所选篇数时只处理剩余量。

- `GET /api/v1/essay-radar/historical-backlog`：返回全部历史的总数、未入队、已完成、待处理、处理中、失败及未入队日期范围。
- 同一接口同时返回知识库最早/最新原文时间、最近入库时间、知识星球数量，以及近 24 小时、7 日、30 日新增篇数，供信息流全库看板使用。
- `POST /api/v1/essay-radar/backfill-count`：请求体为 `{"count":500,"order":"newest"}`；`count` 范围为 1～5000，`order` 支持 `newest` / `oldest`。

该入口启动分析 worker 时不会附带原有的近 30 天启动补队列，确保所选篇数不被隐式扩大。新 MCP
纪要仍按实时增量链路自动入队；失败任务继续通过独立的“重试失败记录”入口处理。

## 数据源状态

`GET /api/v1/financial-data/sources` 返回 Tushare 是否配置、已同步的知识星球、纪要数量、
最新纪要时间和最近同步时间。交互式 OpenAPI 文档位于 `/docs`。

## 量化研究 API

量化模块默认把知识星球 AI 观点与本地 Tushare 日线、复权因子和基准行情连接，并在结果中
返回数据质量漏斗。原始未分析语料只有在 `raw_note_policy=include` 时进入探索回测；默认
`exclude`。`dedupe_window_days` 控制同股同机构同方向观点聚类，`transaction_cost_bps`
控制事件收益扣减成本，`validation_method` 支持 `walk_forward`、`time_split` 和 `none`。

- `GET /api/v1/essay-quant/research-catalog`：各本地事实表记录数、最近数据时间、用途与研究方法。
- `GET /api/v1/essay-quant/runs?limit=30`：不可变运行快照摘要。
- `POST /api/v1/essay-quant/run`：执行受控事件研究，返回收益/超额、组合、95% 置信区间、月度队列与成本敏感性。
- `POST /api/v1/essay-quant/natural-language/plan`：请求体 `{"prompt":"..."}`，返回结构化任务、规则、假设、暂不支持项、安全边界和服务器模板代码。
- `POST /api/v1/essay-quant/natural-language/execute`：请求体包含 `rule` 与 `refresh_prices`，确认后执行经过 schema 与服务层双重归一化的规则。

自然语言接口不会执行模型自由文本。模型无权调用 Shell、任意 SQL、文件系统、密钥、下单或
未授权网络；可执行代码由固定模板渲染并只调用 `EssayQuantService.run()`。
