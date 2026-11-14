# 后端 API 端点清单

> 本文件由 `scripts/list_api_endpoints.py` 自动生成，**不要手工编辑**；端点有增删改就重新跑一次脚本并提交 diff。

- 来源：`api.app.app.openapi()`
- 路径数：**280**
- 端点数（method + path）：**302**
- tag 数：**25**
- 鉴权分布：公开 9 / 前台用户 236 / 管理员 57

鉴权口径：`public` = 未登录也可访问；`user` = `USER_ACCESS_ENABLED=true` 时需要有效用户会话；`admin` = `ADMIN_AUTH_ENABLED=true` 时需要管理员会话（见 `api/middlewares/auth.py`）。

## 按 tag 汇总

| tag | 端点数 |
| --- | ---: |
| FinancialData | 45 |
| InvestmentMonitor | 37 |
| EssayRadar | 21 |
| Portfolio | 20 |
| ConceptThemes | 17 |
| DataAcquisition | 17 |
| EssayQuant | 16 |
| UserAuth | 16 |
| Stocks | 13 |
| SystemConfig | 12 |
| DecisionSignals | 11 |
| Alerts | 10 |
| Agent | 9 |
| History | 9 |
| Intelligence | 9 |
| AlphaSift | 8 |
| Analysis | 6 |
| Command | 6 |
| IndustryResearch | 6 |
| Auth | 5 |
| Backtest | 4 |
| Usage | 2 |
| AINews | 1 |
| Health | 1 |
| HomeDashboard | 1 |

## AINews（1）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/ai-news/feed` | user | - | - | - | Feed |

## Agent（9）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/agent/chat` | user | message | - | - | Agent Chat |
| POST | `/api/v1/agent/chat/send` | user | content | - | - | Send Chat To Notification |
| GET | `/api/v1/agent/chat/sessions` | user | - | - | - | List Chat Sessions |
| GET | `/api/v1/agent/chat/sessions/{session_id}` | user | - | - | session_id:string | Get Chat Session Messages |
| DELETE | `/api/v1/agent/chat/sessions/{session_id}` | user | - | - | session_id:string | Delete Chat Session |
| POST | `/api/v1/agent/chat/stream` | user | message | - | - | Agent Chat Stream |
| GET | `/api/v1/agent/models` | user | - | - | - | Get Agent Models |
| POST | `/api/v1/agent/research` | user | question | - | - | Agent Research |
| GET | `/api/v1/agent/skills` | user | - | - | - | Get Skills |

## Alerts（10）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/alerts/notifications` | user | - | - | - | List alert notification attempts |
| GET | `/api/v1/alerts/rules` | user | - | - | - | List alert rules |
| POST | `/api/v1/alerts/rules` | user | alert_type, target | - | - | Create alert rule |
| GET | `/api/v1/alerts/rules/{rule_id}` | user | - | - | rule_id:integer | Get alert rule |
| PATCH | `/api/v1/alerts/rules/{rule_id}` | user | 有（无必填字段） | - | rule_id:integer | Update alert rule |
| DELETE | `/api/v1/alerts/rules/{rule_id}` | user | - | - | rule_id:integer | Delete alert rule |
| POST | `/api/v1/alerts/rules/{rule_id}/disable` | user | - | - | rule_id:integer | Disable alert rule |
| POST | `/api/v1/alerts/rules/{rule_id}/enable` | user | - | - | rule_id:integer | Enable alert rule |
| POST | `/api/v1/alerts/rules/{rule_id}/test` | user | - | - | rule_id:integer | Dry-run alert rule |
| GET | `/api/v1/alerts/triggers` | user | - | - | - | List alert trigger history |

## AlphaSift（8）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/alphasift/hotspots` | user | - | - | - | Alphasift Hotspots |
| GET | `/api/v1/alphasift/hotspots/{topic}` | user | - | - | topic:string | Alphasift Hotspot Detail |
| POST | `/api/v1/alphasift/install` | admin | - | - | - | Alphasift Install |
| POST | `/api/v1/alphasift/screen` | user | 有（无必填字段） | - | - | Alphasift Screen |
| POST | `/api/v1/alphasift/screen/tasks` | user | 有（无必填字段） | - | - | Alphasift Start Screen Task |
| GET | `/api/v1/alphasift/screen/tasks/{task_id}` | user | - | - | task_id:string | Alphasift Screen Task Status |
| GET | `/api/v1/alphasift/status` | user | - | - | - | Alphasift Status |
| GET | `/api/v1/alphasift/strategies` | user | - | - | - | Alphasift Strategies |

## Analysis（6）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/analysis/analyze` | user | 有（无必填字段） | - | - | 触发股票分析 |
| POST | `/api/v1/analysis/market-review` | user | 有（无必填字段） | - | - | 触发大盘复盘 |
| GET | `/api/v1/analysis/status/{task_id}` | user | - | - | task_id:string | 查询分析任务状态 |
| GET | `/api/v1/analysis/tasks` | user | - | - | - | 获取分析任务列表 |
| GET | `/api/v1/analysis/tasks/stream` | user | - | - | - | 任务状态 SSE 流 |
| GET | `/api/v1/analysis/tasks/{task_id}/flow` | user | - | - | task_id:string | 获取分析任务运行流 |

## Auth（5）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/change-password` | admin | 有（无必填字段） | - | - | Change password |
| POST | `/api/v1/auth/login` | public | 有（无必填字段） | - | - | Login or set initial password |
| POST | `/api/v1/auth/logout` | admin | - | - | - | Logout |
| POST | `/api/v1/auth/settings` | admin | authEnabled | - | - | Update auth settings |
| GET | `/api/v1/auth/status` | public | - | - | - | Get auth status |

## Backtest（4）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/backtest/performance` | user | - | - | - | 获取整体回测表现 |
| GET | `/api/v1/backtest/performance/{code}` | user | - | - | code:string | 获取单股回测表现 |
| GET | `/api/v1/backtest/results` | user | - | - | - | 获取回测结果 |
| POST | `/api/v1/backtest/run` | user | 有（无必填字段） | - | - | 触发回测 |

## Command（6）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/command/catalog` | user | - | - | - | Command Catalog |
| POST | `/api/v1/command/chat` | user | messages | - | - | Command Chat |
| POST | `/api/v1/command/chat/stream` | user | messages | - | - | Command Chat Stream |
| GET | `/api/v1/command/clarifications/{question_id}` | user | - | - | question_id:string | Command Clarification |
| POST | `/api/v1/command/pause` | user | paused, session_id | - | - | Command Pause |
| GET | `/api/v1/command/task` | user | - | task_id | - | Command Task |

## ConceptThemes（17）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/concept-themes/cluster-detail` | user | - | family, cluster | - | 读取二级题材簇的聚合成分 |
| GET | `/api/v1/concept-themes/institution-radar` | user | - | - | - | 从机构语料发现新兴题材与股票线索 |
| GET | `/api/v1/concept-themes/leaders` | user | - | - | - | 读取跨题材市场共识股票雷达 |
| GET | `/api/v1/concept-themes/lifecycle` | user | - | - | - | 交叉分析题材市场轮动与机构语料生命周期 |
| GET | `/api/v1/concept-themes/membership-changes` | user | - | - | - | 读取供应商题材成分新增与退出账本 |
| GET | `/api/v1/concept-themes/overview` | user | - | - | - | 读取分层概念题材总览 |
| GET | `/api/v1/concept-themes/rotation` | user | - | - | - | 读取多源题材轮动强弱 |
| GET | `/api/v1/concept-themes/status` | user | - | - | - | 读取题材库同步状态 |
| GET | `/api/v1/concept-themes/stocks/{ts_code}` | user | - | - | ts_code:string | 读取个股题材暴露与独特Alpha证据 |
| GET | `/api/v1/concept-themes/stocks/{ts_code}/export.csv` | user | - | - | ts_code:string | 导出个股题材画像与归因 |
| POST | `/api/v1/concept-themes/stocks/{ts_code}/refresh` | user | - | - | ts_code:string | 直接更新单股题材与归因 |
| POST | `/api/v1/concept-themes/sync/catalog` | user | - | - | - | 唤醒题材库后台更新 |
| GET | `/api/v1/concept-themes/themes/{theme_id}` | user | - | - | theme_id:integer | 读取题材成分、权重及Beta/Alpha |
| GET | `/api/v1/concept-themes/themes/{theme_id}/export.csv` | user | - | - | theme_id:integer | 导出题材成分、权重与归因结果 |
| POST | `/api/v1/concept-themes/themes/{theme_id}/refresh` | user | - | - | theme_id:integer | 直接更新单一题材及归因 |
| GET | `/api/v1/concept-themes/watchlist-map` | user | - | - | - | 读取当前用户自选股的题材暴露地图 |
| GET | `/api/v1/concept-themes/watchlist-map/export.csv` | user | - | - | - | 导出当前用户自选股题材暴露 |

## DataAcquisition（17）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/data-acquisition/capabilities` | user | - | - | - | 列出一站式取数可用渠道与能力 |
| GET | `/api/v1/data-acquisition/jobs` | user | - | - | - | 列出最近生成的数据包 |
| GET | `/api/v1/data-acquisition/jobs/{job_id}` | user | - | - | job_id:string | 读取数据包来源清单与执行结果 |
| GET | `/api/v1/data-acquisition/jobs/{job_id}/download` | user | - | - | job_id:string | 下载包含 JSON、CSV、Excel 和来源清单的 ZIP |
| HEAD | `/api/v1/data-acquisition/jobs/{job_id}/download` | user | - | - | job_id:string | 读取数据包长度与续传校验标识 |
| POST | `/api/v1/data-acquisition/plan` | user | request | - | - | 由大模型把自然语言需求拆成可审计取数计划 |
| POST | `/api/v1/data-acquisition/research-reports/export-selected` | user | ids | - | - | 导出人工勾选研报及PDF链接Excel |
| GET | `/api/v1/data-acquisition/research-reports/facets` | user | - | - | - | 读取研报本地库可人工筛选标签 |
| GET | `/api/v1/data-acquisition/research-reports/search` | user | - | - | - | 只从本地SQLite研报库执行人工条件检索 |
| GET | `/api/v1/data-acquisition/research-reports/status` | user | - | - | - | 读取本地研报库覆盖范围与后台同步进度 |
| POST | `/api/v1/data-acquisition/research-reports/sync` | user | - | - | - | 后台补齐最近两年研报元数据与PDF链接 |
| POST | `/api/v1/data-acquisition/run` | user | request | - | - | 执行确认后的多渠道取数计划并生成统一数据包 |
| POST | `/api/v1/data-acquisition/run-async` | user | request | - | - | 提交后台取数任务并返回真实进度编号 |
| GET | `/api/v1/data-acquisition/tasks` | user | - | - | - | 列出当前用户的一站式取数后台任务 |
| GET | `/api/v1/data-acquisition/tasks/{task_id}` | user | - | - | task_id:string | 读取后台取数任务的真实渠道与打包进度 |
| POST | `/api/v1/data-acquisition/tasks/{task_id}/resume` | user | - | - | task_id:string | 在原任务内从文件断点继续，不重建数据包 |
| POST | `/api/v1/data-acquisition/validate-plan` | user | request | - | - | 无AI调用地复核公司代码、候选选择和各渠道执行条件 |

## DecisionSignals（11）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/decision-signals` | user | - | - | - | 查询决策信号列表 |
| POST | `/api/v1/decision-signals` | user | action, market, source_type, stock_code, trigger_source | - | - | 创建或去重决策信号 |
| GET | `/api/v1/decision-signals/latest/{stock_code}` | user | - | - | stock_code:string | 查询股票最新 active 决策信号 |
| GET | `/api/v1/decision-signals/outcomes` | user | - | - | - | 查询决策信号后验结果 |
| POST | `/api/v1/decision-signals/outcomes/run` | user | 有（无必填字段） | - | - | 触发决策信号后验评估 |
| GET | `/api/v1/decision-signals/outcomes/stats` | user | - | - | - | 查询决策信号后验统计 |
| GET | `/api/v1/decision-signals/{signal_id}` | user | - | - | signal_id:integer | 查询单条决策信号 |
| GET | `/api/v1/decision-signals/{signal_id}/feedback` | user | - | - | signal_id:integer | 查询决策信号用户反馈 |
| PUT | `/api/v1/decision-signals/{signal_id}/feedback` | user | feedback_value | - | signal_id:integer | 写入决策信号用户反馈 |
| GET | `/api/v1/decision-signals/{signal_id}/outcomes` | user | - | - | signal_id:integer | 查询单个决策信号后验结果 |
| PATCH | `/api/v1/decision-signals/{signal_id}/status` | user | status | - | signal_id:integer | 更新决策信号状态 |

## EssayQuant（16）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/essay-quant/dashboard` | user | - | - | - | 读取最近一次小作文量化回测结果 |
| GET | `/api/v1/essay-quant/institution-dashboard` | user | - | - | - | 读取后台全机构小作文量化基线 |
| POST | `/api/v1/essay-quant/natural-language/execute` | user | rule | - | - | 确认并执行已校验的自然语言研究方案 |
| POST | `/api/v1/essay-quant/natural-language/plan` | user | prompt | - | - | 用大模型生成受约束量化研究方案与模板代码 |
| POST | `/api/v1/essay-quant/precompute/run` | admin | - | - | - | 立即后台预计算全量机构排名 |
| GET | `/api/v1/essay-quant/precompute/status` | user | - | - | - | 读取机构排名后台预计算状态 |
| GET | `/api/v1/essay-quant/research-catalog` | user | - | - | - | 读取量化研究方法与真实本地数据资产 |
| GET | `/api/v1/essay-quant/rules` | user | - | - | - | 读取自定义小作文量化规则 |
| POST | `/api/v1/essay-quant/rules` | user | 有（无必填字段） | - | - | 保存自定义小作文量化规则 |
| PUT | `/api/v1/essay-quant/rules/{rule_id}` | user | 有（无必填字段） | - | rule_id:integer | 更新自定义小作文量化规则 |
| POST | `/api/v1/essay-quant/run` | user | 有（无必填字段） | - | - | 运行并保存小作文事件研究与组合回测 |
| GET | `/api/v1/essay-quant/runs` | user | - | - | - | 读取量化研究运行历史 |
| GET | `/api/v1/essay-quant/runs/{run_id}` | user | - | - | run_id:integer | 读取本人一次已完成的量化运行结果 |
| GET | `/api/v1/essay-quant/tasks` | user | - | - | - | 读取本人量化后台任务 |
| POST | `/api/v1/essay-quant/tasks` | user | 有（无必填字段） | - | - | 提交本人量化后台任务 |
| GET | `/api/v1/essay-quant/tasks/{task_id}` | user | - | - | task_id:string | 读取本人量化后台任务状态 |

## EssayRadar（21）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/essay-radar/analyses` | user | - | - | - | 筛选和分页读取小作文分析结果 |
| GET | `/api/v1/essay-radar/analyses/{topic_id}` | user | - | - | topic_id:string | 读取单篇小作文分析详情 |
| POST | `/api/v1/essay-radar/backfill` | admin | 有（无必填字段） | - | - | 将指定天数内纪要加入分析队列 |
| POST | `/api/v1/essay-radar/backfill-count` | admin | 有（无必填字段） | - | - | 按篇数选择尚未入队的历史小作文进行 AI 分析 |
| GET | `/api/v1/essay-radar/daily-reports` | user | - | - | - | 读取各模型的前一日小作文日报 |
| POST | `/api/v1/essay-radar/daily-reports/run` | admin | 有（无必填字段） | - | - | 立即生成指定日期的多模型小作文日报 |
| POST | `/api/v1/essay-radar/daily-reports/worker/start` | admin | - | - | - | 启动每日多模型报告任务 |
| POST | `/api/v1/essay-radar/daily-reports/worker/stop` | admin | - | - | - | 停止每日多模型报告任务 |
| GET | `/api/v1/essay-radar/dashboard` | user | - | - | - | 获取小作文标签和股票热度看板 |
| GET | `/api/v1/essay-radar/deep-insights` | user | - | - | - | 按研究窗口获取小作文主题、标的与行情验证洞察 |
| POST | `/api/v1/essay-radar/deep-insights/market-interpretation` | user | ts_code | - | - | 用大模型解释可复核的小作文与行情统计 |
| GET | `/api/v1/essay-radar/feed` | user | - | - | - | 检索全部已入库小作文，可选关联 AI 分析结果 |
| GET | `/api/v1/essay-radar/feed/export` | user | - | - | - | 按当前信息流筛选条件导出分析标签与完整原文 Excel |
| POST | `/api/v1/essay-radar/feed/export-selected` | user | topic_ids | - | - | 导出勾选小作文的分析标签与完整原文 Excel |
| GET | `/api/v1/essay-radar/historical-backlog` | user | - | - | - | 读取全部已入库小作文的 AI 分析与未入队数量 |
| GET | `/api/v1/essay-radar/insights` | user | - | - | - | 获取小作文趋势、证据质量、模型共识与关注股洞察 |
| POST | `/api/v1/essay-radar/retry-failed` | admin | 有（无必填字段） | - | - | 重置失败任务并可立即启动后台任务 |
| GET | `/api/v1/essay-radar/status` | user | - | - | - | 读取近 30 天分析进度与后台任务状态 |
| GET | `/api/v1/essay-radar/word-cloud` | user | - | - | - | 获取日、周、月股票/标签/主题词云 |
| POST | `/api/v1/essay-radar/worker/start` | admin | - | - | - | 启动实时 DeepSeek 分析任务 |
| POST | `/api/v1/essay-radar/worker/stop` | admin | - | - | - | 停止实时 DeepSeek 分析任务 |

## FinancialData（45）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/financial-data/announcements` | user | - | symbol | - | 查询 A 股公告列表 |
| GET | `/api/v1/financial-data/annual-report` | user | - | symbol | - | 查询或下载 A 股年报 PDF |
| GET | `/api/v1/financial-data/annual-report/file/{filename}` | user | - | - | filename:string | 下载已保存的年报 PDF |
| GET | `/api/v1/financial-data/documents/download` | user | - | url | - | 下载公告/PDF 全文 |
| GET | `/api/v1/financial-data/downloads/{filename}` | user | - | - | filename:string | 下载已打包的研报 ZIP |
| GET | `/api/v1/financial-data/downloads/{workspace}/{filename}` | user | - | - | workspace:string, filename:string | 下载工作区文件 |
| GET | `/api/v1/financial-data/essays/package/tasks` | user | - | - | - | 当前账号的持久小作文导出任务 |
| POST | `/api/v1/financial-data/essays/package/tasks` | user | 有（无必填字段） | - | - | 后台导出全部匹配小作文，持久保存进度 |
| GET | `/api/v1/financial-data/essays/package/tasks/{task_id}` | user | - | - | task_id:string | 真实导出进度、数量、字节与断点 |
| GET | `/api/v1/financial-data/essays/package/tasks/{task_id}/download` | user | - | - | task_id:string | 下载已完成校验的小作文 ZIP |
| GET | `/api/v1/financial-data/essays/package/tasks/{task_id}/focus-review` | user | - | - | task_id:string | 查看逐篇全文主旨判断与原文证据 |
| POST | `/api/v1/financial-data/essays/package/tasks/{task_id}/resume` | user | - | - | task_id:string | 从已保存的断点继续同一导出任务 |
| POST | `/api/v1/financial-data/query` | user | resource, source | - | - | 统一查询 Tushare、知识星球、巨潮、天眼查或监控事件 |
| POST | `/api/v1/financial-data/reports/package` | user | 有（无必填字段） | - | - | 把研报 PDF 打包成 ZIP |
| GET | `/api/v1/financial-data/reports/package/tasks/{task_id}` | user | - | - | task_id:string | 查询研报打包任务进度 |
| GET | `/api/v1/financial-data/research-notes` | user | - | - | - | 检索已通过 MCP 同步的调研纪要 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/capability` | user | - | - | - | 查看录音转写与纪要分析能力是否已配置 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/tasks` | user | - | - | - | 读取当前用户的录音纪要后台任务 |
| POST | `/api/v1/financial-data/research-notes/audio-analysis/tasks` | user | items | - | - | 提交所选录音的后台转写与 AI 纪要任务 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}` | user | - | - | task_id:string | 读取录音转写与纪要生成进度 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/download` | user | - | - | task_id:string | 下载录音纪要 Markdown、Word、JSON 或完整 ZIP |
| POST | `/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/retry` | user | - | - | task_id:string | 重试失败或中断的录音纪要任务 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/transcripts/{file_id}` | user | - | - | task_id:string, file_id:string | 读取一个录音的带时间戳逐字稿 |
| GET | `/api/v1/financial-data/research-notes/audio-analysis/tasks/{task_id}/transcripts/{file_id}/download` | user | - | - | task_id:string, file_id:string | 只下载一个录音转写后的原文字稿 TXT |
| GET | `/api/v1/financial-data/research-notes/audio-files` | user | - | - | - | 按录音文件而非帖子检索知识星球附件 |
| POST | `/api/v1/financial-data/research-notes/audio-files/batch-download` | user | items | - | - | 将勾选录音源文件临时打包为 ZIP |
| GET | `/api/v1/financial-data/research-notes/audio-files/batch-download-tasks` | user | - | - | - | 列出当前用户的录音后台打包任务 |
| POST | `/api/v1/financial-data/research-notes/audio-files/batch-download-tasks` | user | items | - | - | 提交录音源文件后台打包任务 |
| GET | `/api/v1/financial-data/research-notes/audio-files/batch-download-tasks/{task_id}` | user | - | - | task_id:string | 读取录音源文件后台下载与打包进度 |
| GET | `/api/v1/financial-data/research-notes/audio-files/batch-download-tasks/{task_id}/download` | user | - | - | task_id:string | 下载已完成的录音 ZIP |
| POST | `/api/v1/financial-data/research-notes/audio-files/transcripts/batch-download` | user | items | - | - | 只打包下载已转写录音的原文字稿 |
| GET | `/api/v1/financial-data/research-notes/{topic_id}` | user | - | - | topic_id:string | 读取单篇调研纪要详情 |
| GET | `/api/v1/financial-data/research-notes/{topic_id}/media/{kind}/{asset_id}` | user | - | - | topic_id:string, kind:string, asset_id:string | 按需获取纪要图片/附件远端链接 |
| GET | `/api/v1/financial-data/research-notes/{topic_id}/media/{kind}/{asset_id}/download` | user | - | - | topic_id:string, kind:string, asset_id:string | 按需流式下载知识星球源文件（服务器不落盘） |
| GET | `/api/v1/financial-data/sources` | user | - | - | - | 列出统一财经数据源状态 |
| POST | `/api/v1/financial-data/storage/optimize` | user | - | - | - | 安全优化 SQLite 查询计划并执行非阻塞 WAL 检查点 |
| GET | `/api/v1/financial-data/storage/status` | user | - | - | - | 查看统一财经事实库、逻辑数据域和新鲜度 |
| POST | `/api/v1/financial-data/tushare/{api_name}` | user | 有（无必填字段） | - | api_name:string | 调用任意 Tushare Pro 接口 |
| GET | `/api/v1/financial-data/workspace-files` | user | - | workspace | - | 按工作区列出已生成文件 |
| POST | `/api/v1/financial-data/zsxq/history/backfill` | admin | 有（无必填字段） | - | - | 后台同步近 1 年或 2 年知识星球历史纪要，默认不做 AI 分析 |
| POST | `/api/v1/financial-data/zsxq/import` | user | 有（无必填字段） | - | - | 导入知识星球 MCP 主题页或标准主题数组 |
| POST | `/api/v1/financial-data/zsxq/sync` | admin | - | - | - | 立即从知识星球 MCP 增量同步到本地 SQLite |
| GET | `/api/v1/financial-data/zsxq/sync/status` | user | - | - | - | 知识星球 MCP 增量同步游标与健康状态 |
| POST | `/api/v1/financial-data/zsxq/sync/worker/start` | admin | - | - | - | 启动知识星球 MCP 近实时增量同步 |
| POST | `/api/v1/financial-data/zsxq/sync/worker/stop` | admin | - | - | - | 停止知识星球 MCP 增量同步 |

## Health（1）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/health` | public | - | - | - | Health Check |

## History（9）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/history` | user | - | - | - | 获取历史分析列表 |
| DELETE | `/api/v1/history` | user | 有（无必填字段） | - | - | 删除历史分析记录 |
| DELETE | `/api/v1/history/by-code/{stock_code}` | user | - | - | stock_code:string | 按股票代码删除历史分析记录 |
| GET | `/api/v1/history/stocks` | user | - | - | - | 获取不重复个股列表 |
| GET | `/api/v1/history/{record_id}` | user | - | - | record_id:string | 获取历史报告详情 |
| GET | `/api/v1/history/{record_id}/diagnostics` | user | - | - | record_id:string | 获取历史报告运行诊断摘要 |
| GET | `/api/v1/history/{record_id}/flow` | user | - | - | record_id:string | 获取历史报告运行流 |
| GET | `/api/v1/history/{record_id}/markdown` | user | - | - | record_id:string | 获取历史报告 Markdown 格式 |
| GET | `/api/v1/history/{record_id}/news` | user | - | - | record_id:string | 获取历史报告关联新闻 |

## HomeDashboard（1）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/home-dashboard` | user | - | - | - | 聚合A股、海外市场、自选股和投资情报首页大看板 |

## IndustryResearch（6）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/industry-research/blueprint` | user | - | topic | - | 从本地全渠道数据生成课题证据蓝图 |
| GET | `/api/v1/industry-research/methodology` | user | - | - | - | 读取极速行业调研方法 |
| GET | `/api/v1/industry-research/projects` | user | - | - | - | 读取本人行业调研课题 |
| POST | `/api/v1/industry-research/projects` | user | topic | - | - | 创建本人后台行业调研课题 |
| GET | `/api/v1/industry-research/projects/{project_id}` | user | - | - | project_id:string | 读取本人行业调研课题详情 |
| GET | `/api/v1/industry-research/projects/{project_id}/download` | user | - | - | project_id:string | 下载本人行业或公司调研报告 |

## Intelligence（9）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/intelligence/items` | user | - | - | - | List persisted intelligence items |
| GET | `/api/v1/intelligence/sources` | user | - | - | - | List intelligence sources |
| POST | `/api/v1/intelligence/sources` | admin | name, url | - | - | Create intelligence source |
| POST | `/api/v1/intelligence/sources/defaults` | admin | 有（无必填字段） | - | - | Create built-in default intelligence sources |
| POST | `/api/v1/intelligence/sources/fetch-enabled` | admin | - | - | - | Fetch all enabled intelligence sources with fail-open semantics |
| GET | `/api/v1/intelligence/sources/templates` | user | - | - | - | List built-in intelligence source templates |
| POST | `/api/v1/intelligence/sources/templates/{template_id}` | admin | 有（无必填字段） | - | template_id:string | Create intelligence source from a built-in template |
| POST | `/api/v1/intelligence/sources/test` | admin | name, url | - | - | Dry-run an intelligence source payload |
| POST | `/api/v1/intelligence/sources/{source_id}/fetch` | admin | - | - | source_id:integer | Fetch one intelligence source |

## InvestmentMonitor（37）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/investment-monitor/announcements` | user | - | - | - | 查询已入库上市公司公告 |
| GET | `/api/v1/investment-monitor/announcements/categories` | user | - | - | - | 巨潮资讯公告分类 |
| GET | `/api/v1/investment-monitor/announcements/export` | user | - | - | - | 从已入库公告导出 Excel 索引 |
| POST | `/api/v1/investment-monitor/announcements/package` | user | event_ids | - | - | 从已入库公告下载并打包 PDF 与 TXT |
| POST | `/api/v1/investment-monitor/announcements/sync` | admin | end_date, start_date | - | - | 按日期、个股、分类或关键词抓取巨潮公告 |
| GET | `/api/v1/investment-monitor/announcements/watchlist-sync/status` | user | - | - | - | 查看本人自选股公告实时与历史回补状态 |
| POST | `/api/v1/investment-monitor/cloud/snapshot` | admin | - | - | - | 立即生成一致性 iCloud 知识库快照 |
| GET | `/api/v1/investment-monitor/cloud/snapshots/{filename}/verify` | user | - | - | filename:string | 校验云端知识库完整性 |
| GET | `/api/v1/investment-monitor/cloud/status` | user | - | - | - | iCloud 云端知识库状态与版本清单 |
| POST | `/api/v1/investment-monitor/cloud/worker/start` | admin | - | - | - | 启动 iCloud 知识库定时同步 |
| POST | `/api/v1/investment-monitor/cloud/worker/stop` | admin | - | - | - | 停止 iCloud 知识库定时同步 |
| GET | `/api/v1/investment-monitor/dashboard` | user | - | - | - | 自选股三视角投资情报总览 |
| GET | `/api/v1/investment-monitor/dragon-tiger/daily` | user | - | - | - | 龙虎榜单日全市场明细与营业部席位 |
| GET | `/api/v1/investment-monitor/dragon-tiger/history` | user | - | start_date, end_date | - | 查询本地龙虎榜历史库 |
| POST | `/api/v1/investment-monitor/dragon-tiger/sync` | admin | end_date, start_date | - | - | 按交易日历补齐龙虎榜历史摘要 |
| GET | `/api/v1/investment-monitor/events` | user | - | - | - | 筛选统一消息事件流 |
| GET | `/api/v1/investment-monitor/events/{event_id}` | user | - | - | event_id:integer | 读取单条情报事件已保存内容与来源信息 |
| GET | `/api/v1/investment-monitor/events/{event_id}/content` | user | - | - | event_id:integer | 读取单条快讯正文，缺失时按需抓取原文并缓存 |
| GET | `/api/v1/investment-monitor/intelligence-dashboard` | user | - | - | - | 投资情报台决策看板聚合 |
| GET | `/api/v1/investment-monitor/research-center` | user | - | - | - | 功能能力、数据健康与自选股证据决策工作台 |
| GET | `/api/v1/investment-monitor/source-bi` | user | - | - | - | 全部数据源存量、增量、时效与可调用能力 BI |
| GET | `/api/v1/investment-monitor/sources` | user | - | - | - | 列出内置和外部消息源 |
| POST | `/api/v1/investment-monitor/sources` | admin | name, source_key | - | - | 注册可扩展 API、Webhook 或 MCP 消息源 |
| POST | `/api/v1/investment-monitor/sources/{source_key}/events` | admin | events | - | source_key:string | 批量注入外部消息并幂等去重 |
| POST | `/api/v1/investment-monitor/sources/{source_key}/sync` | admin | - | - | source_key:string | 立即同步单个消息源 |
| GET | `/api/v1/investment-monitor/status` | user | - | - | - | 监控任务与全部消息源健康状态 |
| GET | `/api/v1/investment-monitor/stock-workspace/{symbol}` | user | - | - | symbol:string | 自选看板、问股与研究共用的个股事实和资料 |
| GET | `/api/v1/investment-monitor/super-watchlist` | user | - | - | - | 动态自选股全渠道证据工作台 |
| POST | `/api/v1/investment-monitor/super-watchlist/refresh` | user | - | - | - | 刷新共享行情并唤醒到期情报源 |
| POST | `/api/v1/investment-monitor/super-watchlist/{symbol}/backfill` | user | - | - | symbol:string | 按选定范围回补自选股渠道数据，最多两年 |
| GET | `/api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus` | user | - | - | symbol:string | 读取最近20篇小作文的独立 AI 一致预期快照 |
| POST | `/api/v1/investment-monitor/super-watchlist/{symbol}/essay-consensus/analyze` | user | - | - | symbol:string | 重新分析该股票最近20篇匹配小作文 |
| GET | `/api/v1/investment-monitor/symbols/{symbol}` | user | - | - | symbol:string | 单只自选股的三视角证据和评分 |
| POST | `/api/v1/investment-monitor/sync` | admin | 有（无必填字段） | - | - | 立即同步全部或指定类别消息源 |
| POST | `/api/v1/investment-monitor/watchdog/audit` | admin | - | - | - | 立即检查同步心跳并自动修复 |
| POST | `/api/v1/investment-monitor/worker/start` | admin | - | - | - | 启动实时监控轮询 |
| POST | `/api/v1/investment-monitor/worker/stop` | admin | - | - | - | 停止实时监控轮询 |

## Portfolio（20）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/portfolio/accounts` | user | - | - | - | List portfolio accounts |
| POST | `/api/v1/portfolio/accounts` | user | name | - | - | Create portfolio account |
| PUT | `/api/v1/portfolio/accounts/{account_id}` | user | 有（无必填字段） | - | account_id:integer | Update portfolio account |
| DELETE | `/api/v1/portfolio/accounts/{account_id}` | user | - | - | account_id:integer | Deactivate portfolio account |
| GET | `/api/v1/portfolio/cash-ledger` | user | - | - | - | List cash ledger events |
| POST | `/api/v1/portfolio/cash-ledger` | user | account_id, amount, direction, event_date | - | - | Record cash event |
| DELETE | `/api/v1/portfolio/cash-ledger/{entry_id}` | user | - | - | entry_id:integer | Delete cash ledger event |
| GET | `/api/v1/portfolio/corporate-actions` | user | - | - | - | List corporate action events |
| POST | `/api/v1/portfolio/corporate-actions` | user | account_id, action_type, effective_date, symbol | - | - | Record corporate action event |
| DELETE | `/api/v1/portfolio/corporate-actions/{action_id}` | user | - | - | action_id:integer | Delete corporate action event |
| POST | `/api/v1/portfolio/fx/refresh` | user | - | - | - | Refresh FX cache online with stale fallback |
| GET | `/api/v1/portfolio/imports/csv/brokers` | user | - | - | - | List supported broker CSV parsers |
| POST | `/api/v1/portfolio/imports/csv/commit` | user | account_id, broker, file | - | - | Parse and commit broker CSV with dedup |
| POST | `/api/v1/portfolio/imports/csv/parse` | user | broker, file | - | - | Parse broker CSV into normalized trade records |
| POST | `/api/v1/portfolio/positions/{symbol}/analysis` | user | 有（无必填字段） | - | symbol:string | Submit manual analysis for a held portfolio position |
| GET | `/api/v1/portfolio/risk` | user | - | - | - | Get portfolio risk report |
| GET | `/api/v1/portfolio/snapshot` | user | - | - | - | Get portfolio snapshot |
| GET | `/api/v1/portfolio/trades` | user | - | - | - | List trade events |
| POST | `/api/v1/portfolio/trades` | user | account_id, price, quantity, side, symbol, trade_date | - | - | Record trade event |
| DELETE | `/api/v1/portfolio/trades/{trade_id}` | user | - | - | trade_id:integer | Delete trade event |

## Stocks（13）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| POST | `/api/v1/stocks/extract-from-image` | user | 有（无必填字段） | - | - | 从图片提取股票代码 |
| GET | `/api/v1/stocks/market-data/index/{symbol}` | user | - | - | symbol:string | 获取本地优先的指数多周期行情 |
| GET | `/api/v1/stocks/market-data/realtime` | user | - | symbols | - | 批量读取本地秒级最新行情 |
| GET | `/api/v1/stocks/market-data/realtime-indices` | user | - | - | - | 批量读取本地秒级指数行情 |
| POST | `/api/v1/stocks/market-data/refresh` | admin | - | - | - | 立即采集自选股分钟行情到 SQLite |
| GET | `/api/v1/stocks/market-data/status` | user | - | - | - | 查看本地行情数据库与分钟采集状态 |
| POST | `/api/v1/stocks/parse-import` | user | - | - | - | 解析 CSV/Excel/剪贴板 |
| GET | `/api/v1/stocks/watchlist` | user | - | - | - | 获取自选队列 |
| POST | `/api/v1/stocks/watchlist/add` | user | stock_code | - | - | 加入自选队列 |
| POST | `/api/v1/stocks/watchlist/remove` | user | stock_code | - | - | 从自选队列删除 |
| GET | `/api/v1/stocks/{stock_code}/history` | user | - | - | stock_code:string | 获取股票历史行情 |
| GET | `/api/v1/stocks/{stock_code}/logo` | user | - | - | stock_code:string | 获取股票公司 logo |
| GET | `/api/v1/stocks/{stock_code}/quote` | user | - | - | stock_code:string | 获取股票实时行情 |

## SystemConfig（12）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/system/config` | admin | - | - | - | Get system configuration |
| PUT | `/api/v1/system/config` | admin | config_version, items | - | - | Update system configuration |
| GET | `/api/v1/system/config/export` | admin | - | - | - | Export env backup |
| POST | `/api/v1/system/config/import` | admin | config_version, content | - | - | Import env backup |
| POST | `/api/v1/system/config/llm/discover-models` | admin | 有（无必填字段） | - | - | Discover models for one LLM channel |
| POST | `/api/v1/system/config/llm/test-channel` | admin | 有（无必填字段） | - | - | Test one LLM channel |
| POST | `/api/v1/system/config/notification/test-channel` | admin | channel | - | - | Test one notification channel |
| GET | `/api/v1/system/config/schema` | admin | - | - | - | Get system configuration schema |
| GET | `/api/v1/system/config/setup/status` | admin | - | - | - | Get first-run setup status |
| POST | `/api/v1/system/config/validate` | admin | items | - | - | Validate system configuration |
| POST | `/api/v1/system/scheduler/run-now` | admin | - | - | - | Run scheduled analysis now |
| GET | `/api/v1/system/scheduler/status` | admin | - | - | - | Get runtime scheduler status |

## Usage（2）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/usage/dashboard` | admin | - | - | - | LLM token usage monitoring dashboard |
| GET | `/api/v1/usage/summary` | admin | - | - | - | LLM token usage summary |

## UserAuth（16）

| Method | Path | 鉴权 | 请求体必填 | 必填查询参数 | 路径参数 | Summary |
| --- | --- | --- | --- | --- | --- | --- |
| GET | `/api/v1/user-auth/admin/quota/requests` | admin | - | - | - | 管理员查看额度申请 |
| POST | `/api/v1/user-auth/admin/quota/requests/{request_id}/approve` | admin | 有（无必填字段） | - | request_id:integer | 管理员批准额度申请 |
| POST | `/api/v1/user-auth/admin/quota/requests/{request_id}/reject` | admin | - | - | request_id:integer | 管理员拒绝额度申请 |
| GET | `/api/v1/user-auth/admin/users` | admin | - | - | - | 管理员查看用户申请 |
| POST | `/api/v1/user-auth/admin/users/{user_id}/approve` | admin | - | - | user_id:integer | 管理员批准用户 |
| POST | `/api/v1/user-auth/admin/users/{user_id}/disable` | admin | - | - | user_id:integer | 管理员停用用户 |
| POST | `/api/v1/user-auth/admin/users/{user_id}/reject` | admin | - | - | user_id:integer | 管理员拒绝用户 |
| POST | `/api/v1/user-auth/login` | public | name, password | - | - | 普通用户密码登录并信任当前 IP |
| POST | `/api/v1/user-auth/logout` | public | - | - | - | 退出普通用户会话 |
| GET | `/api/v1/user-auth/quota` | user | - | - | - | 读取当前用户今日额度 |
| POST | `/api/v1/user-auth/quota/request` | user | amount, feature | - | - | 申请增加今日额度 |
| POST | `/api/v1/user-auth/register` | public | name, password | - | - | 提交姓名与密码注册申请 |
| POST | `/api/v1/user-auth/sms/bind` | user | challengeId, code, currentPassword, phone | - | - | 为原账号绑定已验证手机号 |
| POST | `/api/v1/user-auth/sms/login` | public | challengeId, code, phone | - | - | 手机号验证码登录或自助注册 |
| POST | `/api/v1/user-auth/sms/send` | public | phone | - | - | 发送手机号登录或绑定验证码 |
| GET | `/api/v1/user-auth/status` | public | - | - | - | 读取普通用户访问状态 |

## 使用方式

```bash
# 重新生成清单（改了路由后必须跑）
.venv/bin/python scripts/list_api_endpoints.py

# 校验清单是否过期（可进 CI，不阻断以外先做提示）
.venv/bin/python scripts/list_api_endpoints.py --check

# 输出「没有任何测试引用」的端点清单（提示项，默认不阻断）
.venv/bin/python scripts/list_api_endpoints.py --check-coverage

# 发布门禁里想看覆盖率缺口就加 --strict-coverage（存在两层都未覆盖的端点时 exit 1）
.venv/bin/python scripts/list_api_endpoints.py --check-coverage --strict-coverage
```

## 对应测试

| 文件 | 作用 |
| --- | --- |
| `tests/test_api_endpoint_smoke.py` | 全量冒烟：端点注册 / 未鉴权 401/403 / 合法调用不 5xx / 响应可序列化 |
| `tests/test_api_contract.py` | 关键业务端点结构化契约：agent/chat、investment-monitor、stocks（含 logo）、user-auth、home-dashboard、system |
| `tests/fixtures/api_endpoint_smoke_policy.json` | 冒烟跳过原因与已知问题（xfail）白名单 |
