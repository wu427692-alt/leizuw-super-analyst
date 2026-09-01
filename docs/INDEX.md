# 乐子乌超级价值 · 文档中心

这里收录当前产品、数据、部署和开发文档。第一次使用请先看 [GitHub 首页](../README.md) 和 [在线使用手册](https://app.leziwu.com/guide)；页面操作以在线手册为准，工程配置与接口契约以本目录为准。

## 先按目标找文档

| 你的目标 | 入口 |
| --- | --- |
| 了解整套产品与决策闭环 | [项目首页](../README.md) · [在线使用手册](https://app.leziwu.com/guide) |
| 跑通本地或云端服务 | [完整配置指南](full-guide.md) · [部署指南](DEPLOY.md) · [云端 Web 部署](deploy-webui-cloud.md) |
| 运维生产服务器 | [云端运维手册](cloud-operations.md) · [运行诊断](run-diagnostics-p3.md) |
| 配置模型与调用路由 | [LLM 配置](LLM_CONFIG_GUIDE.md) · [模型服务商](llm-providers.md) |
| 接入 Tushare 与统一数据 API | [统一财经数据 API](financial-data-api.md) · [Tushare 股票列表](TUSHARE_STOCK_LIST_GUIDE.md) |
| 检索、筛选与导出数据 | [数据一站式获取](data-acquisition.md) |
| 调研公司或行业 | [行业与公司调研](industry-research.md) |
| 研究题材、成分股与 Alpha/Beta | [概念题材共识引擎](concept-theme-consensus.md) |
| 用机构语料做事件研究 | [机构语料量化研究](essay-quant.md) |
| 理解行情口径与降级 | [行情数据](market-data.md) · [市场支持](market-support.md) · [数据源稳定性](data-source-stability.md) |
| 参与开发 | [贡献指南](CONTRIBUTING.md) · [API 规格](architecture/api_spec.json) |

## 产品工作区

| 工作区 | 说明 | 相关文档 |
| --- | --- | --- |
| 今日决策 | 核心指数、市场广度、行业分布、自选股变化与重要新闻 | [首页市场数据](home-dashboard.md) |
| 机会发现 | 题材层级、来源共识、成分股、题材 Beta 与个股 Alpha | [概念题材共识引擎](concept-theme-consensus.md) |
| 个股决策 | 行情、财务、公告、研报、机构语料、股评与事实时间线 | [统一财经数据 API](financial-data-api.md) |
| 深度研究 | 公司/行业后台研究、录音转写、证据矩阵、Word/PDF 导出 | [行业与公司调研](industry-research.md) |
| 任务与验证 | 录音、取数、调研与量化任务的进度、结果和复现 | [机构语料量化研究](essay-quant.md) |

## 数据与模型

| 文档 | 内容 |
| --- | --- |
| [统一财经数据 API](financial-data-api.md) | Tushare、知识星球 MCP 与统一查询契约 |
| [数据一站式获取](data-acquisition.md) | 研报本地链接库、精细筛选、取数计划与文件打包 |
| [资讯与情报源](intelligence-sources.md) | 资讯源接入、去重、存储与安全边界 |
| [行情数据](market-data.md) | 分钟、日线、实时快照与历史行情口径 |
| [数据源稳定性](data-source-stability.md) | 来源优先级、降级、缓存与故障处理 |
| [分析上下文包](analysis-context-pack.md) | 分析输入、质量状态、证据摘要与可见性 |
| [分析上下文包契约、运行态消费与可见性](analysis-context-pack.md) | P1/P2 内部契约、P3 Prompt 摘要消费、P4 历史/API/Web 低敏可见性、P5 数据质量评分、P6 迁移回滚；完整指南保留 #1386 阶段感知分析、迁移与回滚入口 |
| [LLM 配置](LLM_CONFIG_GUIDE.md) | 模型渠道、路由、超时和用量配置 |
| [模型服务商](llm-providers.md) | Provider 预设、兼容方式与诊断 |

## 部署与运维

| 文档 | 内容 |
| --- | --- |
| [完整配置指南](full-guide.md) | 环境变量、运行模式、数据源与功能配置 |
| [部署指南](DEPLOY.md) | 本地、Docker、systemd 与服务器部署 |
| [云端 Web 部署](deploy-webui-cloud.md) | 域名、HTTPS、反向代理与生产服务 |
| [云端运维手册](cloud-operations.md) | 健康检查、守护、日志、资源与恢复 |
| [macOS 自启动](macos-autostart.md) | LaunchAgent 与桌面入口 |
| [桌面端打包](desktop-package.md) | Electron 桌面端和安装包 |
| [FAQ](FAQ.md) | 常见运行、数据、模型与部署问题 |

## 开发与审计

| 文档 | 内容 |
| --- | --- |
| [贡献指南](CONTRIBUTING.md) | Issue、分支、测试、文档与安全要求 |
| [API 规格](architecture/api_spec.json) | FastAPI OpenAPI 规格产物 |
| [决策信号兼容契约](decision-signals.md) | 保留给既有后端与 API 消费方的兼容参考，不再作为当前前台一级功能 |
| [更新日志](CHANGELOG.md) | 产品、数据、部署与兼容性变化 |
| [运行诊断 P0](run-diagnostics-p0.md) · [P1](run-diagnostics-p1.md) · [P2](run-diagnostics-p2.md) · [P3](run-diagnostics-p3.md) | 从基础可用到生产恢复的分层诊断 |

## 文档边界

- GitHub 首页只保留项目定位、产品实机、核心架构与快速开始；
- 在线使用手册负责页面级操作和真实截图；
- 本目录负责配置、接口、方法、部署、排障与工程约束；
- 历史兼容文档和上游开源版权信息会保留，但不再作为乐子乌产品的主要入口。
