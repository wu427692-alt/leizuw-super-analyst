# 投资情报台

## 独立自选股超级看板

`/super-watchlist` 是独立一级页面。页面按股票展示最近半年日线、估值、财务、资金筹码、公告研报、知识星球、企业信息和统一事实时间线。

通过任何现有“加入自选”入口新增股票时，系统会创建持久化的 183 天历史回填任务。任务按 Tushare 行情/财务/资金/研报/新闻、巨潮公告、知识星球、天眼查分别执行并幂等入库；不支持可靠历史查询的 RSS/NewsNow 会明确显示“无历史接口”，随后仍由增量监控任务持续更新。失败渠道不会阻断其他渠道，可在超级看板中单独重跑整只股票的半年回填。

看板默认只展示少量高价值事实，完整记录进入“事实流水”，避免长信息流占满页面。

投资情报台把不同来源的财经信息标准化为统一事件，并围绕自选股提供“投资者、上市公司、机构”三个视角的监控与分析。Web 默认进入“全部事实”，按公司公告、财经快讯、券商研报、机构调研、资金席位、股权事项、财务业绩、企业风险和知识星球分流展示。

## 事实与观点边界

每条事件都保存证据等级、提供方、原始 API、数据时间、入库时间、结构化原始字段和原文链接可用性：

- `official`：巨潮公告等官方披露。
- `licensed`：Tushare、天眼查和行情源返回的授权结构化记录。
- `reported`：财经媒体与券商发布记录；发布行为可追溯，但报道内容不等同于上市公司确认。
- `unverified`：知识星球、外部投稿等观点或线索。它们保留在独立频道，不进入默认“全部事实”。

行情、财务、筹码和技术快照若含程序计算的摘要，会标记为 `derived_summary`；原始接口字段仍保留在 `metrics` 中。系统不把情绪标签、重要度或机会/风险评分描述成客观事实。

## 实时更新

设置以下环境变量后，API 进程会自动启动独立轮询任务：

```dotenv
INVESTMENT_MONITOR_AUTO_START=true
INVESTMENT_MONITOR_POLL_SEC=10
```

轮询器每 10 秒只检查哪些来源已经到期，并不会每 10 秒重新请求股票数据。知识星球由独立 MCP worker 默认每 30 秒增量入库。盘中实时行情属于状态数据，不进入投资情报流，也不参与事件数量和信号评分；行情适配器只在开盘窗口固化一条开盘快照，Tushare 日线在收盘后固化一条收盘快照。其他内置来源默认频率为：六路 Tushare 财经快讯 1 分钟、技术因子 15 分钟、资金筹码 1 小时、席位异动与机构调研 30 分钟、财务/股权/公司治理/完整研报 6 小时、巨潮公告 15 分钟、天眼查企业事实 12 小时。各适配器故障隔离，一个来源失败不会中断其他来源。

## Tushare 高权限数据域

Tushare 数据不再只是通用 API 转发，以下领域会归一化进 `monitoring_events`，并在 Web 的“消息渠道分流”中独立展示：

- 行情与估值：`daily`、`daily_basic`。
- 财务质量：`fina_indicator`、`income`、`balancesheet`、`cashflow`、`forecast`。
- 筹码与资金：`cyq_perf`、`cyq_chips`、`moneyflow`、`margin_detail`、`hk_hold`。
- 交易异动与席位：`top_list`、`top_inst`、`block_trade`、`limit_list_d`、`suspend_d`、`broker_recommend`。
- 技术面：`stk_factor`。
- 股权与资本动作：`pledge_stat`、`share_float`、`stk_holdertrade`、`repurchase`。
- 机构与研报：`report_rc`、`stk_surv`、`research_report`。完整研报会扫描摘要以覆盖提到自选股的行业/策略报告，并保留 PDF URL。
- 公司治理与股东：`stock_company`、`stk_holdernumber`、`top10_holders`、`top10_floatholders`、`dividend`、`stk_rewards`、`fina_audit`。
- 资讯：`news` 覆盖财联社、新浪财经、华尔街见闻、同花顺、东方财富和第一财经；另接 `major_news`、`cctv_news`。

## 天眼查企业事实

已登录的官方 `tyc` CLI 每 12 小时针对自选股上市主体查询一次企业登记、企业风险、信用评价、知识产权能力和历史工商变更。公司全称来自 Tushare `stock_company.com_name`，避免用股票简称误匹配企业。每个维度独立生成事实事件并保留天眼查结构化响应；CLI 不可用或单个查询失败时，该来源标记失败但其他来源继续运行。

## 主要 API

- `GET /api/v1/investment-monitor/intelligence-dashboard?days=14`：多页投资情报台的趋势、渠道、来源健康、高价值信号与证据冲突聚合。
- `GET /api/v1/investment-monitor/super-watchlist?days=365`：只围绕配置自选股生成行情、估值、财务、资金、技术、股权、机构、公告、企业和知识星球全景；高频快照在决策层去重，原始事实流不删除。
- `GET /api/v1/investment-monitor/dashboard?days=7`：自选股评分、三视角数量、高优先级事件和来源状态。
- `GET /api/v1/investment-monitor/events`：按股票、视角、事件类型、来源、频道、证据等级、关键词和重要度筛选事件。`evidence_level=factual` 排除待核验观点；`channel` 可传逗号分隔的多个频道。
- `GET /api/v1/investment-monitor/symbols/{symbol}`：单只股票的三视角证据流。
- `POST /api/v1/investment-monitor/sync`：立即同步全部或指定类别来源。
- `GET /api/v1/investment-monitor/status`：轮询器和来源健康状态。
- `POST /api/v1/investment-monitor/worker/start`、`/stop`：运行时启停轮询。

## 超级关注股五轮重塑

Web 的“超级关注股”固定以当前自选股为中心，不生成全市场噪声。目前配置为华懋科技 `603306.SH` 和胜宏科技 `300476.SZ`。五轮产品化重塑分别对应：数据归一、单股全景、快照去重、双股同口径比较、证据约束的大模型研判。

页面提供核心结论、财务与估值、资金与技术、公告与机构、另类情报和全部证据六个工作区。财务字段来自最新 `fina_indicator`、`income` 和 `cashflow`；筹码成本来自 `cyq_perf` / `cyq_chips`；估值来自 `daily_basic`；机构页合并可下载研报、盈利预测、机构调研与券商金股；知识星球催化和风险始终标记为待核验。点击“大模型深度研判”只预填带事件 ID 的证据包，由用户确认后再发送。

## 巨潮资讯上市公司公告

Web 投资情报台可按起止日期、股票代码、公告分类和标题关键词抓取公告；抓取结果使用巨潮公告 ID 幂等入库，映射到上市公司视角，并保留 HTTPS PDF 原文链接。后台轮询仅查询自选股近两日公告，空自选股不会退化为全市场抓取。

- `GET /api/v1/investment-monitor/announcements/categories`：列出年报、业绩预告、权益分派、董事会、风险提示等 26 类公告。
- `POST /api/v1/investment-monitor/announcements/sync`：手工抓取并入库；日期格式为 `YYYY-MM-DD`，可选 `symbols`、`categories`、`keyword`、`max_pages`。
- `GET /api/v1/investment-monitor/announcements`：按精确起止日期、股票、分类和关键词查询已入库公告。
- `GET /api/v1/investment-monitor/announcements/export`：从当前已入库结果生成 Excel 索引，最多 500 条。
- `POST /api/v1/investment-monitor/announcements/package`：按已入库事件 ID 下载并打包 PDF、TXT 和 Excel 索引，单次最多 20 条。

示例：

```json
{
  "start_date": "2025-01-01",
  "end_date": "2025-12-31",
  "symbols": ["600519.SH"],
  "categories": ["category_ndbg_szsh"],
  "keyword": "年度报告",
  "max_pages": 20
}
```

数据源使用巨潮公开披露页面同源接口，设置超时、有限重试、分页上限和轻量请求间隔，不绕过访问控制。全市场长日期范围可能在 `max_pages` 达到后截断，建议按月或按股票分段同步。

原 GUI 的“Excel、PDF 下载、PDF 转 TXT”已经重写为服务端接口：Excel 直接从统一公告库生成；PDF/TXT 只按用户明确选择的已入库事件下载，不会由后台轮询批量下载。PDF 仅允许 `https://static.cninfo.com.cn/*.pdf`，默认单文件上限 40 MB，缓存目录为 `./data/announcements`，可分别通过 `ANNOUNCEMENT_FILE_DIR` 和 `ANNOUNCEMENT_PDF_MAX_MB` 调整。ZIP 内含公告 PDF、提取文本、Excel 索引和逐文件状态清单；扫描版 PDF 没有文本层时 TXT 可能为空，当前不执行 OCR。

## 接入其他知识星球或 API

先注册来源：

```http
POST /api/v1/investment-monitor/sources
Content-Type: application/json

{
  "source_key": "api.partner",
  "name": "合作资讯 API",
  "adapter_type": "api",
  "provider": "partner",
  "category": "news",
  "poll_interval_seconds": 60
}
```

再批量写入标准事件：

```http
POST /api/v1/investment-monitor/sources/api.partner/events
Content-Type: application/json

{
  "events": [{
    "external_id": "partner-20260819-001",
    "event_type": "news",
    "perspective": "institution",
    "title": "某机构上调盈利预测",
    "symbols": ["600519.SH"],
    "sentiment": "bullish",
    "importance_score": 82,
    "event_at": "2026-08-19T10:00:00+08:00"
  }]
}
```

`source_key + external_id` 是幂等键。Token、API Key 等凭据必须通过环境变量或服务端密钥管理提供，来源配置接口会拒绝保存常见凭据字段。新知识星球 MCP 连接器可复用同一事件写入接口。

## 数据与回退边界

统一事件保留来源、原始载荷、数据时间、入库时间、关联股票、证据等级、原始 API、重要度、置信度和结构化指标。机会/风险分是基于事实事件的排序工具，不是事实本身，也不是交易建议。关闭 `INVESTMENT_MONITOR_AUTO_START` 可停止自动更新；删除 `monitoring_events` 和 `monitoring_sources` 两张投影表即可清理情报台索引，不影响原始 Tushare、知识星球或既有情报数据。

## iCloud 云端知识数据库

macOS 可把一致性的只读知识库版本写入 iCloud Drive：

```dotenv
ICLOUD_KNOWLEDGE_AUTO_START=true
ICLOUD_KNOWLEDGE_SYNC_INTERVAL_MINUTES=15
ICLOUD_KNOWLEDGE_RETENTION=12
```

默认目录为 `~/Library/Mobile Documents/com~apple~CloudDocs/Daily Stock Analysis/Knowledge`，也可用 `ICLOUD_KNOWLEDGE_DIR` 指定。每个版本是独立 SQLite 文件，并带 JSON 清单、SHA-256、表记录数和设备标识；写入先使用临时文件，再原子改名。云端版本仅包含行情、资讯、纪要、DeepSeek 分析、统一监控事件、基本面和决策信号，不包含配置密钥、账号、对话、LLM 用量和持仓交易。

- `GET /api/v1/investment-monitor/cloud/status`：可用性、定时任务和版本清单。
- `POST /api/v1/investment-monitor/cloud/snapshot`：立即创建一个版本。
- `GET /api/v1/investment-monitor/cloud/snapshots/{filename}/verify`：重新计算校验值并执行 SQLite 完整性检查。

不要将 `DATABASE_PATH` 直接指向 iCloud Drive。iCloud Documents 会产生离线版本和冲突，SQLite WAL 也需要协调多个关联文件；本功能始终让活动库保持本地单写者，再向 iCloud 发布不可变快照。当前属于备份、跨设备查阅和灾备模式，不提供多设备同时写入。真正的增量双向同步需要为桌面应用配置 Apple Developer Team、iCloud entitlement 和 CloudKit Container，再实现记录级变更合并。
