# 投资情报台

## 独立自选股超级看板

`/super-watchlist` 是独立一级页面，围绕当前自选股提供“总览、财务与资金、一致预期、资料库”四个主入口。总览先看行情、经营摘要和各渠道最新资料，其他入口按需读取详细工作区。完整操作、日期口径与缓存边界见[自选股研究工作区](watchlist-workspace.md)。

### 浏览与恢复

- 四个主栏目位于股票名称下方；“财务与资金”内含财务估值和资金筹码，“资料库”内含近期汇总、公告研报、小作文、消息渠道与股评监控。“总览”默认展示行情图，财务与资料子栏目按需展开行情图，一致预期使用独立研究布局。
- 当前股票和栏目保存在地址中，例如 `/super-watchlist?symbol=603306.SH&section=essay`。刷新、浏览器前进/后退仍定位同一栏目；手机横向栏目栏会露出当前选项。
- 首次加载失败在原位置提供重试，不会无限显示骨架屏；已有数据的后台刷新失败会保留已加载内容。错误按股票分别保存，不会把上一只股票的失败显示在当前股票上。
- 自选股列表和当前工作区可独立加载，普通查看不会触发付费 AI。每类财务、资金、技术快照独立取最新事件，避免高频行情挤掉低频财务事实；最新仍按事件时间定义，并非自动判断财报期优先级。
- 报价、估值、财务指标、利润表和现金流量表各自保留实际日期或报告期；估值日期缺失时不借用报价日期，单季与累计同比也不混写。非实时行情、未知日期和缺失值明确展示，不当作今日数据或零值。
- 行情图按资产、股票、周期和区间识别数据；切换时旧曲线与数值退出显示，迟到结果不覆盖新选择，同一查询的后台刷新才保留旧图。图表显示实际最后数据时间，不因轮询就宣称实时；周、月、年线比较的是“上期收盘”，不是“昨收”。
- 公告、研报与字段型资料使用共享页内阅读窗口，旧流水的 `?event=` 深链接也读取同一详情；来源网页/PDF 仅为可选入口，不把保存摘要说成全文。字段按来源含义、独立日期分组，长表就地分页且保留有效尾部记录；详细边界见[页内资料核对](watchlist-workspace.md#页内资料如何核对)。
- `GET /events/{id}` 不再初始化来源配置或加载不用的完整 `raw_payload`，保留既有事件响应契约；读取失败在窗口内重试，权限失败清空摘录与链接，切换条目不接受迟到或错 ID 的响应。接口不自动获取外部原文，不代表生产鉴权与上游可用性已通过验收。

超级看板本身不维护私有行情副本：最新价、分时和日线读取 `market_ticks` / `market_intraday` / `stock_daily` 共享行情库；公告、研报、财务、资金、知识星球和天眼查读取 `monitoring_events` 统一事实库。页面显式查询最近 183 天：`overview` 只读最新快照和每类最多三条资料，不执行完整计数；`full` 通过 SQL 聚合取得范围内统计，但只载入限量近期正文。快照按类型独立读取，不依赖流水前 200 条，因此高频新闻不会挤掉范围内的财务快照。工作区轮询只读本地库，后台行情与投资情报 worker 负责增量写入；独立行情图接口在本地数据缺失或分时不完整时仍可能按既有逻辑向上游补采，不应把整个浏览页面称为严格离线。

资料库的默认研究资料汇总不混入公开股评，股评仍在“股评监控”独立保留，未删除原始记录。搜索和每页 12 条分页仅作用于当前已载入条目，不代表全库检索。完整工作区的计数可大于可见列表；总览 `evidence.counts_complete=false` 时不能把零解释为全库没有记录。

小作文栏目的“检索全库”与底部机构段子入口使用公司名称（缺失时用股票代码）作为 `/essay-radar/feed?query=...`，目的页实际消费该关键词。它是已入库机构资料的文本检索，不是所有来源全覆盖或证券 ID 精确过滤；更多来源可进入独立流水或取数模块核对股票、关键词和时间范围。

`POST /api/v1/investment-monitor/super-watchlist/refresh` 会立即刷新共享行情快照、重建本地小作文关键词索引并唤醒到期情报源，不会为超级看板创建一套独立抓取器。完整工作区 API 保留各数据域的数量、新鲜/陈旧/缺失状态与来源同步信息；四主入口页面优先展示实际资料与各自日期，来源健康可到全渠道情报查看。财务、天眼查、收盘后股评等低频事实按其合理周期更新，不能伪装成秒级实时。

小作文不再只依赖 AI 主题分类或显式股票代码。系统针对当前自选股同时匹配六位代码、股票全称和去除常见公司后缀的简称，例如“华懋”可关联华懋科技、“胜宏”可关联胜宏科技。该匹配只在用户自选股范围内执行，避免对全市场两字简称做无界模糊匹配；服务启动会幂等重建既有小作文索引，新自选股则在半年回填和后续知识星球增量同步时自动套用同一规则。点击小作文条目会先在超级看板的站内侧边窗口读取本地 SQLite 原文，并展示正文、图片和附件；知识星球 topic URL 只作为窗口内的可选外部入口。

“一致预期”保持两类口径分离：券商数字来自 Tushare `report_rc`，同预测期按研报去重后计算 EPS、净利润、PE、ROE 和目标价区间/中位数；小作文则由独立专项任务读取最多 20 篇相关原文和 5 篇股票标签仅含当前股票的专属原文，按 topic 去重后交给配置的分析器。普通浏览只读取候选元数据与已保存的结果，不加载全部历史正文；AI 真正执行时才读取选中的原文。候选按事件时间和 ID 向前分页，直到两个配额取满或现有十年检索窗口耗尽，不以固定候选条数截断较早的专属原文。每个结果必须保存预测主体及其与上市公司的关系、预测期、原文表述、原文证据、来源 topic 和置信度；同行噪声会被排除，子公司、收购标的和业务分部不能冒充上市公司合并口径。没有明确表述时返回空结果，不能用券商数字或程序推算补齐。结果按股票保存，并用选中 topic、创建时间和样本类型组成的来源哈希识别新样本；新小作文进入后会标记为待更新，由用户在全宽研究工作台中重新分析。页面始终并列展示两类结果，不把未经核验的小作文数字混入券商统计。

“消息渠道”合并与当前股票匹配的相关新闻、知识星球和天眼查新增提示，每条仍保留来源与证据等级。“股评监控”只展示 `eastmoney.guba_posts` 的真实公开帖子：每 30 秒读取每只动态自选股公开列表的最新一页，保存帖子摘要、作者、发布时间、浏览/回复/点赞数、图片链接和原文地址，并用帖子 ID 幂等增量入库；点击帖子在超级看板当前页面打开详情抽屉，外部原帖链接仅作为可选入口。`akshare.stock_comments` 仍可保存东方财富千股千评结构化指标，但不再获取雪球讨论热度，也不混入股评帖子列表。公开用户帖子一律标为 `unverified`，不进入事实口径。适配器不会登录、破解验证码、翻旧页或批量抓取回复；上游要求安全验证时本轮明确失败，不用缓存冒充实时成功。

通过现有“加入自选”入口首次把股票加入共享采集池时，系统会按已有规则创建持久化的 183 天历史回填任务；已在共享池中的股票新增个人关注时复用已有采集。任务按 Tushare 行情/财务/资金/研报/新闻、巨潮公告、知识星球、天眼查及公开股评分别执行并幂等入库；公开股评只回填当前公开列表，不宣称覆盖半年历史。不支持可靠历史查询的 RSS/NewsNow 会明确显示“无历史接口”，随后仍由增量监控任务持续更新。失败渠道不会阻断其他渠道，已有单股回填 API 可重新排队；当前四入口页面不提供单独的半年回填按钮，不能把普通浏览或重试当作补齐历史。

后台回填接口支持 `POST /api/v1/investment-monitor/super-watchlist/{symbol}/backfill?days=730`，默认仍为183天，允许30～730天。请求和台账保存同一个天数，执行时不再截为一年；已有183天活动任务不能代替新请求的730天任务，会保留旧范围并创建更大范围的后续任务，重复的同范围活动任务复用同一编号。台账与渠道结果持久化在 `watchlist_backfill_jobs`，正常服务启动会重新排队未完成任务。它是公共公司事实采集，不是用户私有分析任务；各用户的自选归属仍独立。

两年任务不代表每种来源都有两年完整序列：日线会将返回历史保存进 `stock_daily`；财务和部分资金适配器目前主要保留快照与原始返回，不能据此宣称逐期报表/逐日因子全覆盖；快讯按30天分页窗回看且有分页上限；公开股评、RSS/NewsNow、企业风险接口不保证历史可得。通用任务按渠道保存结果，但未实现新闻每页恢复，重启可能重取未完成渠道，入库仍按来源ID幂等。研报链接库和知识星球历史应分别使用其专门的持久断点任务，避免把本地已有资料投影当成远端历史已经补齐。

看板默认只展示少量高价值事实，完整记录进入“事实流水”，避免长信息流占满页面。

投资情报台把不同来源的财经信息标准化为统一事件。Web 的 `/investment-monitor` 是“全渠道情报”首页，逐渠道展示存量、最新事实时间、新鲜度、同步状态和该渠道原始消息；`/investment-monitor/bi` 是独立数据源 BI，展示全源存量、近 30 日事实量、最新时间、上轮收到/新增/更新、耗时、轮询频率、底层接口和可直接调用的事件 API；`/investment-monitor/feed` 是独立实时流水，只按时间展示进入本地统一事件库的消息。自选股超级看板继续作为独立一级页面存在，不再混入情报栏目。

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
INVESTMENT_MONITOR_MAX_WORKERS=16
INVESTMENT_MONITOR_NEWS_OVERLAP_MINUTES=360
INVESTMENT_MONITOR_NEWS_LIVE_OVERLAP_MINUTES=10
CNINFO_WATCHLIST_AUTO_START=true
CNINFO_WATCHLIST_POLL_SEC=60
CNINFO_WATCHLIST_HISTORY_DAYS=365
CNINFO_WATCHLIST_RECENT_DAYS=3
CNINFO_WATCHLIST_WINDOW_DAYS=30
CNINFO_WATCHLIST_WINDOWS_PER_CYCLE=8
SYNC_WATCHDOG_AUTO_START=true
SYNC_WATCHDOG_INTERVAL_SEC=30
SYNC_WATCHDOG_STALE_MULTIPLIER=4
```

轮询器每 10 秒检查一次到期来源。到期渠道默认最多 16 路并发读取上游，任何适配器完成后都会立即在主线程串行写入 SQLite；慢速企业或公告接口不会再扣住已经抓到的快讯，也不会把并发写锁压力转移到事实库。Tushare 快讯正常周期只回看最近 10 分钟并幂等去重，服务中断后会从最后成功游标自动扩窗补漏，最长回看 6 小时，避免“高频”退化成每 15 秒重复处理整段历史。知识星球由独立 MCP worker 每 10 秒增量入库。盘中实时行情属于状态数据，不进入投资情报流，也不参与事件数量和信号评分；行情适配器只在开盘窗口固化一条开盘快照，Tushare 日线保存收盘快照。其他内置来源默认频率为：六路 Tushare 财经快讯与可配置媒体流 15 秒、东方财富公开股评 30 秒、巨潮公告/市场题材/技术因子 60 秒、机构调研/席位异动/上市公司事项/资金筹码/长篇新闻 2 分钟、财务/股权/券商研报/千股千评 5 分钟、公司治理与天眼查企业事实 10 分钟。RSS/Atom/NewsNow 没有启用来源时明确显示“未配置”。各适配器故障隔离，一个来源失败不会中断其他来源。

来源状态使用两套互不混淆的口径：`monitoring_status`、`last_check_at` 表示后台是否按目标频率真实访问了上游，`freshness_status`、`latest_event_at` 表示上游最后一次真正发布事实的时间。最近巡检成功但上游没有新公告时显示“实时巡检·上游静默”，而不是误报为系统陈旧。每次运行另外记录 `last_received_count`、`last_created_count`、`last_updated_count` 和 `last_duration_ms`；上游返回 100 条但全部已存在时会显示“收到 100、新增 0”，不会把幂等去重误报成没有抓取。

上市公司、财务、筹码、巨潮和天眼查适配器只处理 A/B 股公司证券，自动跳过可转债和基金代码；这些证券仍可保留在自选股和行情采集范围内，避免单个不兼容代码导致整条公司数据源失败。

### 服务端同步自修复

API 进程默认启动同步看门狗。它每 30 秒检查投资情报、知识星球 MCP、盘中行情和自选股巨潮公告 worker 的线程状态与最后心跳，并读取 `monitoring_sources` 的到期计划。线程退出会自动重启；线程仍在但超过其正常轮询周期长期没有完成同步，或事实来源已经到期未运行，则唤醒原有增量 worker 立即补抓最新数据。看门狗不直接写库，也不另建抓取逻辑，因此仍沿用各来源原有游标、幂等键、增量去重和 SQLite 串行写入约束。

来源失败后不会无间隔轰击上游：失败来源至少等待 15 秒并按来源周期退避，最长 5 分钟；未配置来源每小时复查一次。一旦成功，立即回到目标周期。行情仅在 A 股连续竞价时间判定心跳，休市、午休和周末不会被误报为停止同步。知识星球只有在 MCP 已配置且自动同步开启时才纳入修复。

`GET /api/v1/investment-monitor/status` 同时返回 `watchdog`、轮询 worker 和全部来源状态；`POST /api/v1/investment-monitor/watchdog/audit` 可立即执行一次检查。`watchdog.last_result.repairs` 会列出本轮重启或唤醒了哪些同步器以及哪些来源已经到期。

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

已登录的官方 `tyc` CLI 每 10 分钟针对自选股上市主体查询一次企业登记、企业风险、信用评价、知识产权能力和历史工商变更。公司全称来自 Tushare `stock_company.com_name`，避免用股票简称误匹配企业。每个维度独立生成事实事件并保留天眼查结构化响应；CLI 不可用或单个查询失败时，该来源标记失败但其他来源继续运行。

## 主要 API

- `GET /api/v1/investment-monitor/intelligence-dashboard?days=14`：多页投资情报台的趋势、渠道、来源健康、高价值信号与证据冲突聚合。
- `GET /api/v1/investment-monitor/source-bi?days=30`：全部来源的资产清单、存量、时效、近 30 日趋势、上轮抓取遥测与直接调用契约。
- `GET /api/v1/investment-monitor/super-watchlist?days=365`：围绕当前账号自选股生成行情、估值、财务、资金、技术、股权、机构、公告、企业和知识星球全景；管理员或旧模式保留配置自选股。高频快照在决策层去重，原始事实流不删除。
- `GET /api/v1/investment-monitor/stock-workspace/{symbol}?days=183&view=overview`：当前自选页的轻量总览；`view=full` 读取详细研究上下文。接口默认 `days=365&view=full`，`refresh=true` 只绕过本地组装缓存，不请求外部数据源；与共享刷新 POST 不同。
- `GET /api/v1/home-dashboard`：市场首页聚合，个人自选按当前账号完整列表只读组装，不再与全局 `STOCK_LIST` 取交集。HTTP 响应禁止共享缓存，前端按账号隔离成功快照并拒绝旧账号迟到结果，公共行情可单独复用。
- `GET /api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus`：读取该自选股最近 20 篇小作文的专项预期分析快照、证据与处理状态。
- `POST /api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus/analyze`：强制把该股票最近 20 篇匹配原文重新排队给 DeepSeek 分析；任务异步执行并持久化，前端只轮询这一块状态。
- `GET /api/v1/investment-monitor/dashboard?days=7`：自选股评分、三视角数量、高优先级事件和来源状态。
- `GET /api/v1/investment-monitor/events`：按股票、视角、事件类型、来源、频道、证据等级、关键词和重要度筛选事件。`evidence_level=factual` 排除待核验观点；`channel` 可传逗号分隔的多个频道。
- `GET /api/v1/investment-monitor/symbols/{symbol}`：单只股票的三视角证据流。
- `POST /api/v1/investment-monitor/sync`：立即同步全部或指定类别来源。
- `GET /api/v1/investment-monitor/status`：轮询器和来源健康状态。
- `POST /api/v1/investment-monitor/watchdog/audit`：立即检查同步心跳，自动重启停止的 worker 并唤醒到期来源。
- `POST /api/v1/investment-monitor/worker/start`、`/stop`：运行时启停轮询。

## 龙虎榜工作台

`/investment-monitor/dragon-tiger` 是全市场独立页面，不受自选股过滤。每日榜单直接按交易日调用 Tushare `top_list`，席位明细调用 `top_inst`；同一股票同一天因不同上榜原因产生的记录分别保留，不互相覆盖。买入、卖出和净额均按接口原始元口径保存。

- `GET /api/v1/investment-monitor/dragon-tiger/daily?trade_date=2026-08-19&refresh=true`：直连刷新指定交易日榜单和全部席位；不传日期时自动回退到最近有数据的交易日。
- `GET /api/v1/investment-monitor/dragon-tiger/history?start_date=2026-08-01&end_date=2026-08-19`：查询本地已入库历史，可按股票和关键词筛选。
- `POST /api/v1/investment-monitor/dragon-tiger/sync`：按交易日历增量补齐一个日期范围的每日榜单；单次最多 120 个自然日，席位明细在打开某日时按需补齐。

Tushare 的两个龙虎榜接口要求精确 `trade_date`，不接受起止日期直接批量返回，因此历史同步先读取交易日历，再逐日幂等入库。页面明确显示“缓存交易日数”和同步结果，未同步日期不会用推算数据填充。

## 超级自选股工作区

Web 的“自选股超级看板”始终以当前动态自选股为中心，不生成全市场噪声。华懋科技 `603306.SH`、胜宏科技 `300476.SZ` 或后续新增股票都使用同一套回填、简称匹配、增量同步和证据聚合规则。

使用路径是“选择股票 → 核对行情和原文 → 比较经营与预期 → 提出并验证研究问题”，四入口与细节见[自选股研究工作区](watchlist-workspace.md)。财务字段来自 `fina_indicator`、`income` 和 `cashflow`，保留独立报告期；筹码来自 `cyq_perf` / `cyq_chips`，估值来自独立日期的 `daily_basic`；公告研报子栏目合并可用研报、盈利预测、机构调研与券商金股。知识星球和公开股评始终是待核验线索。

有公开链接的证据打开来源原文，无外链的记录通过 `/investment-monitor/feed?event=<事件ID>` 查看本地详情，小作文与股评在页内打开原文。点击“问 AI 这只股票”仅预填含公司、代码、报价时间与核验要求的问题，用户发送后才执行；当前按钮不自动附带全部事件 ID 或原文包。市场首页进入流水的 `symbol`、`channel=news` 与 `event` 参数会实际用于股票、频道或具体记录定位。

“我的研究任务”进入 `/tasks`，按五类任务汇总已读取结果，可按类型与状态筛选、每页 20 条浏览。顶部数量明确是已读取记录，不是全部历史；调研可每次继续读取 30 条历史，其他类型保留接口最近记录的限制并提供原模块入口。运行类型可见时约每 10 秒更新，失败类型约每 30 秒重试，空列表与已完成类型在页面激活时更新。查看任务只返回已有项目、结果或文件任务，不自动发起分析。

当前浏览器证据范围为本地 `4176` 前端配合旧 `8000` 后端的桌面两主题财务页和移动端小作文原文流程；不代表新后端 HTTP、云端或全功能验收。后续实测边界与工作区服务层只读样本见[专题验证说明](watchlist-workspace.md#已有验证与边界)。

## 官方上市公司公告（巨潮与交易所备用）

自选股公告的自动链路保留巨潮为首选；巨潮查询失败时，沪市 A 股改查上交所官方公司公告接口，深市 A 股改查深交所官方上市公司公告接口。两条备用链路已从生产服务器验证证券筛选、日期窗口、分页和官方原文地址；北交所暂不在本轮备用覆盖范围。手动指定巨潮的现场查询及一站式取数仍严格使用巨潮，不在用户不知情时更换来源。

- 来源分别为 `cninfo.announcements`、`sse.announcements`、`szse.announcements`，统一进入公告列表、自选股证据与按需打包。备用元数据保留真实来源、披露日和原文链接，不伪装成巨潮恢复。巨潮失败次数、错误和退避仍保留，任务状态为 `partial` / `degraded`，单项备用成功为 `fallback_success`。
- 增量巨潮查询设置 20 秒任务预算，历史窗口 60 秒；备用单窗口最多 60 秒。失败退避期间不重复请求巨潮；当轮备用增量成功后可继续有限个历史窗口。窗口未取全、分页异常或两源都失败不标记完成，下轮继续补齐。上游 Retry-After 不被主动缩短。
- SSE `SSEDATE` 为披露日期，不把 `ADDDATE` 当披露时间；深交所 `publishTime` 为上海时间。数据存 UTC，日期筛选及 Excel 公告日期按上海时区处理，元数据提供 `disclosure_date` / `time_precision`。
- 仅抓取元数据，不自动下载附件、不额外调用 AI。PDF 提供 `pdf_url`；官方合法 DOC/DOCX/TXT/HTML/HTM/XLS/XLSX 保留 `original_url` 和真实类型，不冒充 PDF。PDF 打包仅接受巨潮、上交所、深交所固定官方 HTTPS 文件域名，不跟随重定向。
- 不删除旧公告；对已确认同一公告做保守跨源幂等归并，并在 `metrics._source_provenance` 保留其他来源链接。证券、日期或标题存在歧义时不强行合并。备用接入不是全市场覆盖或完整性的保证，回补完成以每只股票持久化窗口和真实分页校验为准。

生产暂时停用备用来源时，可在管理员数据源配置关闭相应来源；回滚发布使用保留的上一版镜像与源文件，不改数据库或附件。

Web 投资情报台可按起止日期、股票代码、公告分类和标题关键词抓取公告；抓取结果使用巨潮公告 ID 幂等入库，映射到上市公司视角，并保留 HTTPS PDF 原文链接。自动链路以动态自选股为强制范围：每 60 秒逐只股票精确查询最近 3 天，避免“全市场前 N 页”在公告高峰漏掉自选股；新增自选股或服务启动时，会把最近 365 天拆成 30 天分片自动回补。每个成功分片、失败次数、下次重试和完成区间都持久化到 SQLite，进程重启后从未完成分片继续，失败按 30～300 秒退避且成功后恢复实时周期。系统只保存标题、公司、公告时间、分类和官方原文链接，不自动下载 PDF；用户明确打包时才下载所选文件。

历史恢复可设置 `CNINFO_WATCHLIST_HISTORY_DAYS=730`，分片和失败恢复机制不变；已存在的目标范围只扩大不缩小。公告采集池是全局自选与所有已批准账号自选的去重并集，未批准/停用账号不加入，指数、基金、转债不冒充公司公告目标。该并集只供后台采集使用：`/announcements/watchlist-sync/status` 按当前账号的自选过滤目标、进度和错误；公共守护状态不返回其他账号的股票清单或逐股错误。内部健康守护仍保留全池状态。`history_progress` 根据已完整完成的日期分片计算，同时返回 `target_start` / `target_end`、成功/失败时间与实际新增数，不以接口返回200就算补齐。

- `GET /api/v1/investment-monitor/announcements/categories`：列出年报、业绩预告、权益分派、董事会、风险提示等 26 类公告。
- `GET /api/v1/investment-monitor/announcements/watchlist-sync/status`：查看每只自选股的一年目标区间、历史分片进度、最新增量成功时间和失败重试状态。
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

数据源使用巨潮公开披露页面同源接口，设置超时、有限重试、分页上限和轻量请求间隔，不绕过访问控制。自动回补按股票、按 30 天分片，只有上游请求完整成功才记录该分片完成；空结果是有效成功，网络错误或异常响应不会推进游标。手工全市场长日期查询仍可能在 `max_pages` 达到后截断，建议按月或按股票分段同步。

### 公告同步健康与上游故障

- 历史回填完成只代表已覆盖历史区间，不能覆盖或清除最近增量查询的 `retry`、失败次数、错误和下次重试时间；必须等到真实请求成功后才解除失败状态。
- 每轮结果区分 `success`（本轮请求成功）、`partial`（部分成功）、`failed`（请求全部失败）和 `idle`（没有到期工作）。空闲、失败不推进最近成功时间；空闲期间已有失败仍显示 `degraded`。最近成功时间表示至少有一次真实同步成功，不代表所有股票均已补齐，需结合逐股状态判断。
- 同步看门狗会将上游失败列为 `degraded_workers`，但不会仅因巨潮 504、限流或单股查询失败重启正常线程或网页容器。线程停止与上游不可用是两类故障。
- 巨潮错误保留接口类型（证券检索／公告查询）、HTTP 状态码、实际请求次数和合法的 `Retry-After`。错误文案不返回响应正文、Cookie、请求参数或密钥。TLS 验证失败、非临时 4xx、无效 JSON 不做密集重试；超时和临时 HTTP 错误有限退避重试。
- 上游等待时间不能为赶任务期限而缩短：短等待可在请求内退避，超过 5 秒交回持久任务；总时间／次数预算仍生效。HTTP 200 但缺少公告列表不是成功，明确的空列表／空值才表示查询成功但没有新公告。
- 上游持续不可用时保留已有公告和未完成区间，不伪造“最新”或完成状态；恢复后自动继续幂等增量和回填。无需清库或反复重启网页。

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
