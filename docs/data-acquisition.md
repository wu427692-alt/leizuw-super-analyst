# 数据一站式获取

“数据一站式获取”把自然语言需求转换为可审计的数据任务，并将不同渠道的结果统一打包。公告、Tushare 和天眼查任务以官方接口现场查询为准；知识星球继续使用 MCP 增量同步后的本地索引。

## 工作流程

1. 用户描述股票、日期范围、数据维度和交付口径。
2. DeepSeek 规划器只根据能力目录生成最多 12 个任务；页面先展示数据源、资源、参数和用途。
3. 用户确认后，每个渠道分别接收股票、公司、关键词和日期范围；执行器逐项现场调用 Tushare、巨潮和天眼查，知识星球查询 MCP 增量索引，并对返回数据再次做范围过滤。
4. 单一渠道失败不会丢弃其他结果；清单中会标记成功、失败、行数和安全错误说明。
5. ZIP 按 `tushare/`、`cninfo/`、`zsxq/`、`monitor/`、`tianyancha/` 分目录；每个数据集生成 JSON 与 CSV，每个渠道单独生成 Excel 工作簿，再附 `manifest.json` 和 `README.md`。
6. 用户明确说“下载/文件/附件/PDF/原文文件”时，计划设置 `include_files=true`，公告 PDF、Tushare 可下载研报、知识星球图片附件及结果中的 PDF 会写入 `attachments/` 后一起压缩；未请求时只保留链接。

除非计划中的 `scope.market_wide=true` 且用户明确要求全市场数据，否则市场级新闻等接口返回的结果必须命中目标公司、股票代码或关键词。没有明确筛选范围的公告/情报任务会被拒绝，不再用全量数据填充。

规划器遵循“只取明确要求维度”的约束；用户写明“只/仅/不要”时，不会为了展示平台能力而追加其他渠道。
执行器还会按渠道契约归一化参数，例如将股票代码解析为巨潮 `orgId`、将日期转换为各官方接口要求的格式，并删除新闻接口不接受的公司关键词参数。当前 Tushare 规划目录已包含概念/行业
资金流、涨停/炸板/跌停池、市场两融汇总、神奇九转和管理层名录；是否返回数据仍以当前 Token
的真实权限为准。自然语言中出现已配置关注股名称时，范围层会补齐对应证券代码，例如
“华懋科技”补为 `603306.SH`、“胜宏科技”补为 `300476.SZ`，避免公告等结构化查询仅靠标题模糊匹配。

## 已接入渠道

- Tushare Pro：每个任务直接调用对应 `api_name`。`research_report` 用于带 URL 的个股/行业/策略研报并显式请求摘要，`report_rc` 用于卖方盈利预测；`news`、`major_news` 和研报接口按上游单页上限分页。
- 知识星球：查询 MCP 增量同步到 SQLite 的调研纪要，结果保留图片和附件的本地访问链接。
- 巨潮资讯：每次任务直接调用 `topSearch/query` 解析证券与 `hisAnnouncement/query` 重新检索公告；需要文件时从 `static.cninfo.com.cn` 下载 PDF。
- 统一情报：只用于用户明确要求的其他情报事件，不再代替公告、Tushare 新闻或研报。
- 天眼查：先用 L0 企业搜索把简称锚定为登记全称，再通过官方 `tyc` CLI 查询。`company_full` 同时覆盖登记、上市、财务、年报、股东、实控人、受益所有人、投资、高管、关系图、司法/行政风险、经营、舆情、招投标、供应商客户、产品、专利商标和历史信息共 24 个维度。

## API

- `GET /api/v1/data-acquisition/capabilities`：数据源、资源和规划器状态。
- `POST /api/v1/data-acquisition/plan`：请求体 `{"request":"..."}`，生成取数计划。
- `POST /api/v1/data-acquisition/run`：传入自然语言需求和已确认计划，执行并生成数据包。
- `POST /api/v1/data-acquisition/run-async`：提交后台取数任务，立即返回 `task_id`、真实阶段和初始进度；Web 工作台默认使用此接口。
- `GET /api/v1/data-acquisition/tasks/{task_id}`：读取已完成渠道数、当前渠道、导出/压缩阶段和最终数据包结果。任务状态持久化，切换页面后可以继续查看；服务重启中断的任务会明确标记失败，不会永久停在“运行中”。
- `GET /api/v1/data-acquisition/jobs`：最近数据包。
- `GET /api/v1/data-acquisition/jobs/{job_id}/download`：下载 ZIP。

Web 端的取数进度来自服务端已完成任务数和实际导出/压缩阶段，不使用计时器模拟。ZIP 下载进度来自浏览器收到的真实字节数与响应 `Content-Length`；若上游代理未提供总大小，则展示已接收字节数而不伪造百分比。

所有文件默认保存在主数据库同级的 `data_acquisition/` 目录。数据包不写入 API Token、OAuth 凭证或天眼查本地认证配置。

## 可选配置

```dotenv
DATA_ACQUISITION_LLM_MODEL=deepseek-v4-flash
DATA_ACQUISITION_LLM_BASE_URL=https://api.deepseek.com
DATA_ACQUISITION_LLM_TIMEOUT_SEC=120
DATA_ACQUISITION_OUTPUT_DIR=./data/data_acquisition
```

规划器复用现有 `DEEPSEEK_API_KEY`/`DEEPSEEK_API_KEYS`；Tushare 复用 `TUSHARE_TOKEN`；天眼查复用 `tyc login` 保存的本机 OAuth 状态。
