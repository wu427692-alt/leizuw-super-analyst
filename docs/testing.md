# 测试与发布门禁

本文说明本仓库「一整套测试」怎么组织、怎么跑、哪一层负责什么，以及每次大版本更新前应该执行什么。
发布门禁脚本是唯一入口：**`bash scripts/release_gate.sh`**，不需要记住一堆零散命令。

- 门禁脚本：`scripts/release_gate.sh`
- 报告产物：仓库根目录 `release-gate-report.md`（每次运行覆盖）
- 详细日志：脚本会打印临时日志目录（默认在系统临时目录，可用 `--logs-dir` 指定）
- 页面预算基线：`scripts/audit_page_budget.py` 输出「N 个页面超出 §10 预算」，脚本内基线常量 `PAGE_BUDGET_BASELINE`，当前为 **2**

> 本文与脚本行为必须保持一致。改脚本的分层、白名单、基线或参数时，同一 PR 内同步更新本文。

---

## 1. 分层设计

门禁按「改动影响面」分层，越靠前越便宜、越先跑；每层独立给出 **通过 / 失败 / 跳过**，失败不会静默中断后续层，最后统一汇总。

| 层 | 名称 | 内容 | 失败是否阻断发布 | 典型耗时 |
| --- | --- | --- | --- | --- |
| 1 | 静态检查 | `py_compile`、`flake8`（E9/F63/F7/F82）、`tsc -b --noEmit`、`eslint .` | 阻断 | ~40s |
| 2 | 后端功能 | `pytest -m "not network"` 全量（含 API 端点冒烟与契约测试） | 阻断 | 数分钟 |
| 3 | 前端使用 | `vitest run` 全量（页面、组件、store、关键流程） | 阻断 | ~1.5min |
| 4 | 界面规范 | `scripts/audit_page_budget.py` 页面预算 + UI 治理断言（原生 `title`、主题令牌） | 阻断（预算变差时） | ~2s |
| 5 | 构建 | `npm run build`（`tsc -b` + `vite build`）+ 后端 `import api.app, server` smoke | 阻断 | ~1min |
| 6 | E2E 主流程 | Playwright：真实登录后的主流程 + 手机/桌面界面硬约束 | 不阻断（需要真实环境） | 1–3min |

### 每层覆盖什么 / 不覆盖什么

**层 1 静态检查**

- 覆盖：Python 语法（`main.py`、`server.py`、`api/`、`src/`、`data_provider/`、`bot/`）、Python 阻断级 lint、前端类型错误、前端 lint。
- 不覆盖：运行期语义、样式正确性、API 契约。
- 注意：`flake8` 不在 `requirements.txt` 里；**未安装时该子项会跳过并提示**，其余子项照常阻断。

**层 2 后端功能**

- 覆盖：`pytest` 全量离线用例（`-m "not network"`），包括：
  - 各服务/仓储/数据源/通知/调度的单元与集成测试；
  - `tests/test_api_endpoint_smoke.py` 的全量端点冒烟（路由已注册、鉴权返回 401/403 而非 500、最小合法参数不返回 5xx、响应可 JSON 序列化）；
  - 关键业务端点契约（`/api/v1/agent/chat/*`、`/api/v1/investment-monitor/*`、`/api/v1/stocks/*`、`/api/v1/user-auth/*`、`/api/v1/home-dashboard`、`/api/v1/system/*`）。
- 不覆盖：真实外部数据源、真实网络、LLM 真实调用（这些在 `network-smoke.yml` 观测项里，不在发布门禁里跑）。
- **后台 worker 隔离**：本机 `.env` 常常连着真实数据源，`app_lifespan` 会把十几个后台 worker（行情/纪要/MCP/监控/RSS…）一起拉起来，lifespan 测试就变成真实抓取，容易「卡十几分钟不结束」。门禁默认在 pytest 进程里把 `*_AUTO_START` 全部设为 `false`，保证结果可复现；想验证真实启动链路时设 `RELEASE_GATE_ALLOW_WORKERS=1`（此时可能很慢）。
- **xfail 单列**：用 `pytest.mark.xfail` 标记的已知未修问题（当前 6 条）会在报告「已知 xfail」一节单独列出，**既不算通过也不算失败**；它们都是已定位根因、但还没修的后端问题（详见 4.5）。
- 端点清单脚本 `scripts/list_api_endpoints.py` 存在时会额外跑两条**提示项（不阻断）**：
  - `--check`：端点基线 `tests/fixtures/api_endpoints.json` 是否与 `openapi()` 漂移（路由增删后需要重新生成）；
  - `--check-coverage`：两层覆盖口径（冒烟实际调用的端点 / 专项测试引用过的端点、真空端点数量）。
  两条结论都写进报告「附加提示项」，不改变门禁结论。

**层 3 前端使用**

- 覆盖：`vitest` 全量，包括页面渲染冒烟（`allPagesRender.test.tsx` 自动从路由表派生页面清单）、关键用户流程（`keyFlows.test.tsx`）、组件/store/hook 单测。
- 不覆盖：真实浏览器布局、真实 API 联调（jsdom 下 mock），这部分交给层 6 E2E。
- 前端页面覆盖率脚本 `scripts/report_frontend_test_coverage.py` 存在时，会额外输出「没有任何测试引用的页面」清单：**提示项，不阻断**。

**层 4 界面规范**

- 覆盖：
  - `docs/ui-design-system.md` §10 页面内容预算（一级区块、并列指标卡、表格行、说明文字）——超标页面数超过基线即阻断；
  - UI 治理断言：`apps/dsa-web/tests/ui_governance.test.ts`（不重新引入原生 `title`、不引入 `input-terminal`）、`login-theme-tokens.test.ts`、`index.theme-bootstrap.test.ts`。
- 不覆盖：真实渲染后的字号/溢出/热区——这些在层 6 用真实浏览器断言。
- 基线调整：如果一次改造让超标页面数下降，**在同一 PR 里把脚本内 `PAGE_BUDGET_BASELINE` 调到新值**；基线只允许变小，禁止为了让门禁变绿而上调。

**层 5 构建**

- 覆盖：前端生产构建（`tsc -b` + `vite build`）能否通过；后端真实入口 `import api.app, server` 能否装配。
- 不覆盖：Docker 镜像构建、桌面端 Electron 打包（分别由 `.github/workflows/ci.yml` 的 `docker-build` 与桌面打包脚本覆盖）。

**层 6 E2E 主流程**

- 覆盖（`apps/dsa-web/e2e/user-flows.spec.ts`，另含 `smoke.spec.ts`）：
  - 桌面端：登录 → 总览 `/home` → 切 Tab → 点快讯开抽屉看到正文（且不跳路由）；
  - 桌面端：主要页面无横向溢出；可见文本字号不小于 12px；
  - 手机端 390×844：`/meeting` 渲染团队角色条与对话输入框；`/chat` 重定向到 `/meeting` 且保留 query；
  - 手机端：主要可点热区不小于 44×44（底部主导航严格断言，刻意紧凑的控件按设计白名单豁免）。
- 不覆盖：完整业务流程（发起真实研究/等 LLM 产出）、多浏览器矩阵（当前只跑 chromium）。
- **没有 `DSA_WEB_SMOKE_PASSWORD` 时整层跳过**，不算失败；有密码但环境问题导致失败时该层标红但**不阻断**发布，作为信号暴露。

---

## 2. 三种用法

### 日常提交：`--fast`

```bash
bash scripts/release_gate.sh --fast
```

只跑 层 1（静态检查）+ 层 4（界面规范）+ 层 5（构建），约 1 分钟。适合每次提交前或本地开发收尾。
不跑全量测试，所以**不能替代发布前的 `--full`**。

### 发布前：`--full`

```bash
bash scripts/release_gate.sh --full
```

六层全跑，发布前必跑。建议顺序：

1. 先把工作区里无关的临时文件（探针 test、调试脚本、截图）清掉，避免被全量扫描计入失败；
2. 设置好登录密码再跑，这样 E2E 层才有意义：
   ```bash
   DSA_WEB_SMOKE_PASSWORD='<你的登录密码>' bash scripts/release_gate.sh --full
   ```
3. 跑完检查 `release-gate-report.md`：结论必须是「通过」，且「已知遗留失败」一节没有新增条目。

### 单层调试：`--layer=N`

```bash
bash scripts/release_gate.sh --layer=2          # 只跑后端全量
bash scripts/release_gate.sh --layer=3,6        # 跑前端全量 + E2E
bash scripts/release_gate.sh --layer=6          # 只跑 E2E（需要密码）
```

其他参数：

| 参数 | 作用 |
| --- | --- |
| `--report PATH` | 报告输出路径（相对路径按仓库根解析） |
| `--logs-dir PATH` | 保留本次运行的分层日志到指定目录 |
| `RELEASE_GATE_OUT_DIR=...` | 等价于 `--logs-dir` 的环境变量形式 |
| `PYTHON_BIN=...` | 指定 Python 解释器（默认优先 `.venv/bin/python`） |
| `PAGE_BUDGET_BASELINE=N` | 临时调整页面预算基线（只用于验证基线本身，不要用它在 PR 里放过退化） |
| `RELEASE_GATE_ALLOW_WORKERS=1` | 层 2 不隔离后台 worker（默认隔离）。用于验证真实启动链路，会很慢且依赖本机 `.env` |
| `PYTEST_TIMEOUT` / `VITEST_TIMEOUT` / `BUILD_TIMEOUT` / `E2E_TIMEOUT` | 各层超时秒数（默认 3600 / 1800 / 1200 / 2400），超时按该层失败处理 |
| `VITEST_TEST_TIMEOUT` | 前端单个测试的超时毫秒数（默认 30000）。vitest 默认 5s，机器有负载时 1–5s 的 UI 用例会被误判超时 |

退出码：`0` = 所有阻断层通过；`1` = 有阻断层失败；`2` = 参数错误。
E2E 失败、`flake8` 未安装、覆盖率提示项都不会让退出码变 1。

---

## 3. 环境要求

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.11+（仓库 `.venv` 为 3.13） | 门禁默认用 `.venv/bin/python`；没有 venv 时回退 `python3` |
| Python 依赖 | `pip install -r requirements.txt` | 缺依赖时层 1/2 会失败 |
| flake8 | 可选 | 未安装时层 1 该子项跳过；想启用就 `.venv/bin/pip install flake8` |
| Node.js | `apps/dsa-web/package.json` 要求 `>=20.19 <27` | 当前验证环境 v24 |
| 前端依赖 | `cd apps/dsa-web && npm ci` | 没有 `node_modules` 时层 1/3/4/5 会直接失败并给出提示 |
| Playwright 浏览器 | `cd apps/dsa-web && npx playwright install chromium` | 只影响层 6；未安装时层 6 会失败但不会阻断 |
| E2E 登录密码 | `DSA_WEB_SMOKE_PASSWORD` | 没有就跳过层 6 |
| 后端 E2E 角色 | `.venv/bin/python` 或 `DSA_WEB_SMOKE_BACKEND_CMD` | 层 6 由 Playwright 配置自动拉起后端（`main.py --webui-only`）与前端 dev server |

> 层 2/层 3 都是全量测试，机器上同时跑别的重任务会导致超时类假失败。跑 `--full` 时不要同时开别的全量测试。

---

## 4. 已知遗留失败白名单

以下用例在**本次门禁引入之前就失败**，与当前改动无关，因此门禁把它们单独列出、不计入阻断。
白名单只允许「历史遗留 + 有明确修复条件」的条目；**新增条目必须在 PR 描述里说明为什么不是本次改动引入的**。
每条都要写清「为什么暂时可以忽略」和「什么时候必须修」；修好后条目不会报错，只是不再命中——顺手删掉对应行。

### 4.1 任务台账里已登记的遗留失败

| 用例 | 为什么暂时可以忽略 | 什么时候必须修 |
| --- | --- | --- |
| `tests/test_investment_monitor.py::test_source_bi_*` | 日期炸弹：该模块 fixture 硬编码 `2026-08-19`，跨过该日期后断言漂移 | 把 fixture 改成相对当前时间的日期后立刻移除本条 |
| `test_essay_analysis.py::test_queue_claim_save_and_dashboard_are_restart_safe` | 历史遗留的重启安全断言，门禁引入前即失败 | 纪要分析存储层统一 queue claim/save 语义时修复 |
| `test_essay_quant_service.py::test_cross_section_refresh_*` | 历史遗留的横截面刷新断言，门禁引入前即失败 | quant 服务返回结构与 freshness 语义收敛后修复 |
| `tests/test_market_data_service.py` 的 intraday/index bucket 断言（本机可复现 2 条：`test_intraday_series_reads_only_latest_second_bucket_when_minutes_exist`、`test_index_intraday_reads_only_latest_second_bucket_when_minutes_exist`） | 历史遗留的分桶断言（分时/指数「latest second bucket」规则），门禁引入前即失败 | 分时与指数数据分桶规则统一后修复 |

### 4.2 仓库基线遗留（与本任务无关，需要单独 issue 处理）

| 用例 | 为什么暂时可以忽略 | 什么时候必须修 |
| --- | --- | --- |
| `tests/test_daily_analysis_workflow_notification_env.py`（7 条）、`tests/test_daily_analysis_workflow_llm_env.py`（6 条）、`tests/test_searxng_env_docs.py::test_daily_analysis_workflow_matches_documented_searxng_variable_mapping` | 这些测试读取的 `.github/workflows/00-daily-analysis.yml` 已在提交 `d91e9fca`「rebrand and streamline repository」里被删除，但测试没有同步 | 恢复该工作流、或把断言迁移到现有 `ci.yml` 后移除本条 |
| `tests/test_essay_analysis.py` 的 `test_deep_insights_merges_raw_topics_and_expands_feed_search`、`test_essay_radar_read_api`；`tests/test_essay_quant_service.py` 的 `test_event_study_*`、`test_unanalyzed_history_*`、`test_default_rule_*`、`test_research_catalog_*`、`test_source_query_*` | 固定日期夹具（2026-08）与当前日期漂移，产出 0 条导致 `StopIteration`/`IndexError`/`assert 0 == 1` | 把夹具日期改为相对当前时间后修复 |
| `tests/test_config_registry.py::TestEnvExampleWebSettingsCoverage::test_active_env_example_keys_are_registered_or_hidden_from_web_ui` | `.env.example` 新增键（`AI_NEWS_*` 等）未注册进 Web 设置注册表/隐藏清单 | 补注册表条目后修复 |
| `tests/test_macmini_compose_contract.py` 的 `test_macmini_disables_every_known_api_auto_start_switch`、`test_macmini_healthcheck_executes_expected_status_contract[...]` | docker macmini compose 契约未跟上 `AI_NEWS_AUTO_START` 开关，健康检查仍指向 `/api/health`（实际为 `/api/v1/health`） | 补 compose 条目、统一 health 路径后修复 |
| `tests/test_jev_standalone_assets.py::test_independent_bundle_keeps_original_index_and_requires_auth` | 独立打包产物与用例期望的 index 内容不一致（重构后未同步产物/断言） | 打包流程稳定后同步产物或断言 |
| `tests/test_agent_models_api.py::AgentSkillsEndpointTestCase::test_chat_request_empty_skills_clears_context_without_triggering_activate_all` | 测试桩与 Python 3.13 不兼容：`_ImmediateLoop.run_in_executor()` 被以 4 个参数调用（实现只接受 3 个） | 升级测试桩后修复 |

### 4.3 需要「修完就删」的临时条目（不得带病发布）

| 用例 | 为什么暂时可以忽略 | 什么时候必须修 |
| --- | --- | --- |
| `tests/test_financial_data_service.py` 的 `test_import_is_idempotent_searchable_and_strips_signed_urls`、`test_audio_assets_are_searchable_downloadable_metadata_and_never_claimed_for_ai` | 当前工作区 `src/repositories/research_note_repo.py` 有未提交的 FTS5 检索改造，中文关键词检索命中 0（关掉 FTS 走 LIKE 时通过）；该文件不在本任务写入范围 | 检索改造合入前必须复跑；改造方修好中文检索后立刻删除此条 |
| `tests/test_api_endpoint_smoke.py::test_authenticated_endpoint_does_not_return_5xx[GET /api/v1/financial-data/reports/package/tasks/{task_id}]` | **真实 bug**：新增的全量端点冒烟发现该 GET 在参数合法时抛未处理异常返回 500（应为 404/422） | 修后端错误处理后删除此条；这是发布阻断项，白名单只是让门禁能跑完其余层并把它单独暴露出来 |

### 4.4 前端（层 3 / 层 4）

| 用例 | 为什么暂时可以忽略 | 什么时候必须修 |
| --- | --- | --- |
| `src/pages/__tests__/ChatPage.test.tsx` 的 22 条 placeholder 断言 | 断言的是旧占位文案（`例如：分析 600519`），UI 已改为「输入研究问题」；按约定不为让测试通过而回退 UI | ChatPage 测试整体迁移到新文案的同一 PR 内移除本条 |
| `apps/dsa-web/tests/ui_governance.test.ts` 的「原生 title」1 条 | 业务代码 `src/components/common/InlineViz.tsx` 里 `title={segment.label}` 违反治理断言；属于组件层改动，不在门禁任务范围内 | 组件负责人替换为 `Tooltip`/`aria-label` 后移除本条 |

### 4.5 已知 xfail（不参与判定，但必须可见）

用 `@pytest.mark.xfail` 标记的后端已知未修问题，门禁把它们**单列**在报告「已知 xfail」一节，不计入通过/失败。
当前 6 条（由 `tests/api_smoke_policy.py` + `tests/fixtures/api_endpoint_smoke_policy.json` 管理）：

| 用例 | 根因 | 什么时候必须修 |
| --- | --- | --- |
| `financial-data/reports/package/tasks/*` 等 4 个端点冒烟 | 非 Docker 部署下代码里硬编码 `/app/data` 路径，缺目录时抛 500（应为 404/503） | 路径改为配置项后移除 xfail |
| 公告/年报端点冒烟 | 上游不可达时返回 500，语义应为 503 | 上游异常映射统一后移除 |
| `dragon-tiger/daily` 冒烟 | 未配置 `TUSHARE_TOKEN` 时 500（应为 503/400 并给提示） | 缺配置分支补齐后移除 |
| logo 404 冒烟（自定义头） | `HTTPException(headers=...)` 的自定义头被统一 error_handler 丢弃，`Cache-Control: no-store` 不生效 | error_handler 透传 headers 后移除 |

### 4.6 全量运行时被前序用例干扰（测试隔离问题）

这些用例单独运行/同文件运行都通过，只有全量顺序执行时因为全局状态（DB 单例、进程环境）被前序用例改写而失败。
门禁对此有**自动复检**：某条非白名单失败产生后，会对「该失败用例所在的测试文件」单独再跑一次；单独执行通过则降级为「已知遗留（套件顺序干扰）」并在报告里保留复检记录，仍然失败的继续阻断。所以下面这类问题不会被静默放过——它们每次运行都会重新被复检一遍。

| 用例 | 为什么暂时可以忽略 | 什么时候必须修 |
| --- | --- | --- |
| `tests/test_api_contract.py` 的 `test_system_config_contract`、`test_system_config_schema_contract`、`test_system_scheduler_status_contract` | 全量运行时被前序用例的全局状态/DB 单例干扰，单跑通过 | 夹具层做状态隔离后移除 |
| `tests/test_storage.py::TestStorage::test_get_instance_waits_for_cold_start_initialization`、`test_get_visible_conversation_messages_limit_returns_ordered_tail` | 全量运行时 `DatabaseManager` 单例/数据被前序用例污染，单跑通过 | 夹具层做状态隔离后移除 |

## 5. 新增功能时怎么补测试（checklist）

按改动面勾选，缺哪项就在 PR 描述里写清原因：

**后端**

- [ ] 新逻辑有对应的 `tests/test_*.py` 单测；外部数据源/LLM 一律 mock，保证 `-m "not network"` 能跑。
- [ ] 新增或修改 API 端点：重跑 `python scripts/list_api_endpoints.py` 生成基线（`tests/fixtures/api_endpoints.json` + `docs/api-endpoints.md`），并在 `tests/test_api_endpoint_smoke.py` 的冒烟/契约断言里覆盖；跑 `--check` 确认基线不过期、`--check-coverage` 看缺口。
- [ ] 改动字段/枚举/错误码：补契约断言（前端依赖的字段不能悄悄改名或消失）。
- [ ] 改了报告结构/通知模板/数据源 fallback：在 PR 描述里写明验证路径。

**前端**

- [ ] 新增页面：`src/pages/__tests__/` 下至少有渲染冒烟；`scripts/report_frontend_test_coverage.py` 的未覆盖清单里不应出现它。
- [ ] 改动交互/路由/状态：在 `keyFlows.test.tsx` 或页面自己的测试里加行为断言（不是只断言「渲染不报错」）。
- [ ] 改手机端布局：补/更新移动端断言（`useIsMobile` 分支、热区、横向溢出）。
- [ ] 改 UI 结构：跑一次 `python scripts/audit_page_budget.py`，确认没有让超标页面数变差；`docs/ui-design-system.md` §10–§15 是判定依据。
- [ ] 新增/修改用户可见页面：在 `apps/dsa-web/e2e/user-flows.spec.ts` 的页面清单或流程里补一条。

**通用**

- [ ] 改动用户可见能力 / CLI/API 行为 / 部署方式 / 报告结构：同步更新对应 `docs/*.md`，并在 `docs/CHANGELOG.md` 的 `[Unreleased]` 加一条扁平格式记录（`- [类型] 描述`）。
- [ ] PR 前跑 `bash scripts/release_gate.sh --fast`；发布前跑 `--full`。
- [ ] 如果新增了「暂时无法修复」的失败：不要加白名单，先在 PR 描述里说明并请维护者确认。

---

## 6. 与 CI 的关系

| 入口 | 内容 | 何时跑 |
| --- | --- | --- |
| `scripts/ci_gate.sh` | 后端 `py_compile` + flake8 + `scripts/test.sh` 确定性检查 + `pytest -m "not network"` | CI 的 `backend-gate`，PR 必跑 |
| `scripts/release_gate.sh` | 六层发布门禁（本文） | 本地/发布前；比 CI 多前端全量、界面规范、构建、E2E |

`release_gate.sh` 不替代 CI，两者是「PR 必过的最小集」与「发布前更完整的门禁」的关系。
`release-gate-report.md` 是本地产物，默认不入库（跑完可自行删除或加进 `.gitignore` 的本地忽略规则）。

---

## 7. 常见问题

**Q：`flake8` 那行显示「跳过」，算通过吗？**
算。`flake8` 不在运行依赖里，未安装时只跳过该子项并在报告里注明；`py_compile`、`tsc`、`eslint` 仍然是阻断项。

**Q：E2E 一直跳过？**
设置 `DSA_WEB_SMOKE_PASSWORD` 并安装 Playwright 浏览器：
```bash
cd apps/dsa-web && npx playwright install chromium
DSA_WEB_SMOKE_PASSWORD='<密码>' bash scripts/release_gate.sh --layer=6
```

**Q：前端全量为什么把单测超时放宽到 30s？**
vitest 默认单测超时 5s，而本仓库有若干 UI 用例正常就要 1–5s（`ConceptThemesPage` 有一条 4.8s）。机器有负载时会成片超时（报 `STACK_TRACE_ERROR` / 找不到元素），看起来像功能坏了。门禁不是性能门禁，统一放宽到 30s（`VITEST_TEST_TIMEOUT` 可覆盖），换来稳定的发布结论。

**Q：层 2 为什么要关掉后台 worker？会不会漏测？**
本机 `.env` 连接真实数据库/数据源时，`app_lifespan` 的十几个后台 worker 会真的出去抓数据，lifespan 相关测试不再可复现（实测单个测试文件从 6 秒变成十几分钟不结束）。门禁默认隔离它们，把这些 worker 自己的行为留给 `tests/test_*_worker*.py` 等专项用例覆盖（这些用例会自己 `monkeypatch.setenv` 打开需要的开关）。要验证真实启动链路，用 `RELEASE_GATE_ALLOW_WORKERS=1 bash scripts/release_gate.sh --layer=2`，但请注意它依赖本机环境。

**Q：失败后怎么只复现一条？**
报告「失败明细」里每条都带复现命令，直接复制执行即可；后端单条形如
`cd "<repo>" && .venv/bin/python -m pytest "tests/test_x.py::test_y" -q`，
前端单条形如 `cd apps/dsa-web && npx vitest run <文件> -t "<用例名>"`。

**Q：页面预算基线为什么是 2？**
`docs/ui-design-system.md` §10 改造后，全站只剩 2 个页面轻微超标。基线用于防退化：只要不超过 2 就不阻断；想让基线下降，必须先修好对应页面再改脚本常量。

**Q：E2E 里为什么有的可点元素允许小于 44px？**
`docs/ui-design-system.md` §15 有意把对话发送键（34px）、技能 chip、弹层把手等做成紧凑控件；E2E 对这些按类名白名单豁免，但**底部主导航严格 ≥44×44**，其余页面级按钮也按 44 断言。
