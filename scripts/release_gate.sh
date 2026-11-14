#!/usr/bin/env bash
# =============================================================================
# 全站发布门禁（release gate）
#
# 一条命令按层串起：静态检查 / 后端功能 / 前端使用 / 界面规范 / 构建 / E2E。
# 每层独立给出 通过 / 失败 / 跳过，失败时打印失败用例名与单条复现命令，
# 最后在仓库根目录产出 `release-gate-report.md`。
#
# 用法（详见 docs/testing.md）：
#   bash scripts/release_gate.sh            # 等同 --full，发布前跑
#   bash scripts/release_gate.sh --fast     # 层 1+4+5，日常提交跑，约 1 分钟
#   bash scripts/release_gate.sh --full     # 层 1..6，发布前跑
#   bash scripts/release_gate.sh --layer=2  # 只跑某一层（可逗号分隔多层）
#   bash scripts/release_gate.sh --layer=3,6
#
# 约定：
#   - 已知历史遗留失败进白名单：在报告里单列，不算阻断（见脚本内 WHITELIST_*）。
#   - E2E 需要 DSA_WEB_SMOKE_PASSWORD；没有密码时整层跳过，不算失败。
#   - flake8 未安装时该子项跳过（不阻断），其余静态检查照常。
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/apps/dsa-web"
REPORT="$ROOT/release-gate-report.md"
MODE_LABEL=""
START_EPOCH="$(date +%s)"

# 页面预算基线：`scripts/audit_page_budget.py` 当前超标页面数。
# 数字变差（多于基线）即阻断；改造让数字变好时把基线下调到新值。
PAGE_BUDGET_BASELINE="${PAGE_BUDGET_BASELINE:-2}"

OUT_DIR="${RELEASE_GATE_OUT_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/release-gate.XXXXXX")}"
mkdir -p "$OUT_DIR" 2>/dev/null || OUT_DIR="$ROOT"

# ---------------------------------------------------------------- 颜色
if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_DIM=""; C_BOLD=""; C_OFF=""
fi

# ---------------------------------------------------------------- 层元数据
ALL_LAYERS="1 2 3 4 5 6"
LAYER_TITLE_1="静态检查"; LAYER_TITLE_2="后端功能"; LAYER_TITLE_3="前端使用"
LAYER_TITLE_4="界面规范"; LAYER_TITLE_5="构建";     LAYER_TITLE_6="E2E 主流程"
LAYER_BLOCKING_1=1; LAYER_BLOCKING_2=1; LAYER_BLOCKING_3=1
LAYER_BLOCKING_4=1; LAYER_BLOCKING_5=1; LAYER_BLOCKING_6=0

layer_title() { eval "printf '%s' \"\$LAYER_TITLE_$1\""; }
layer_blocking() { eval "printf '%s' \"\$LAYER_BLOCKING_$1\""; }

# 结果容器（bash 3.2 只有索引数组）
LAYER_STATUS_1="pending"; LAYER_STATUS_2="pending"; LAYER_STATUS_3="pending"
LAYER_STATUS_4="pending"; LAYER_STATUS_5="pending"; LAYER_STATUS_6="pending"
LAYER_TIME_1="0"; LAYER_TIME_2="0"; LAYER_TIME_3="0"
LAYER_TIME_4="0"; LAYER_TIME_5="0"; LAYER_TIME_6="0"
LAYER_NOTE_1=""; LAYER_NOTE_2=""; LAYER_NOTE_3=""
LAYER_NOTE_4=""; LAYER_NOTE_5=""; LAYER_NOTE_6=""

layer_status() { eval "printf '%s' \"\$LAYER_STATUS_$1\""; }
layer_time() { eval "printf '%s' \"\$LAYER_TIME_$1\""; }
layer_note() { eval "printf '%s' \"\$LAYER_NOTE_$1\""; }

# ---------------------------------------------------------------- 参数
SELECTED_LAYERS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fast)
      SELECTED_LAYERS="1 4 5"; MODE_LABEL="--fast"
      ;;
    --full)
      SELECTED_LAYERS="$ALL_LAYERS"; MODE_LABEL="--full"
      ;;
    --layer=*)
      value="${1#--layer=}"
      norm=""
      for part in $(printf '%s' "$value" | tr ',' ' '); do
        case "$part" in
          1|2|3|4|5|6) norm="$norm $part" ;;
          *) echo "release-gate: 无效层号 '$part'（可选 1-6）" >&2; exit 2 ;;
        esac
      done
      SELECTED_LAYERS="$SELECTED_LAYERS$norm"; MODE_LABEL="${MODE_LABEL:+$MODE_LABEL }--layer=$value"
      ;;
    --layer)
      echo "release-gate: 请用 --layer=N 形式" >&2; exit 2
      ;;
    --report)
      shift
      [ $# -gt 0 ] || { echo "release-gate: --report 需要路径" >&2; exit 2; }
      case "$1" in
        /*) REPORT="$1" ;;
        *) REPORT="$ROOT/$1" ;;
      esac
      ;;
    --logs-dir)
      shift
      [ $# -gt 0 ] || { echo "release-gate: --logs-dir 需要路径" >&2; exit 2; }
      OUT_DIR="$1"; mkdir -p "$OUT_DIR" 2>/dev/null || true
      ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "release-gate: 未知参数 '$1'（--fast / --full / --layer=N / --report PATH / --logs-dir PATH）" >&2
      exit 2
      ;;
  esac
  shift
done

if [ -z "$SELECTED_LAYERS" ]; then
  SELECTED_LAYERS="$ALL_LAYERS"
  MODE_LABEL="--full（默认）"
else
  # 去重并排序，保持 1..6 顺序
  dedup=""
  for candidate in $ALL_LAYERS; do
    for chosen in $SELECTED_LAYERS; do
      if [ "$candidate" = "$chosen" ]; then dedup="$dedup $candidate"; break; fi
    done
  done
  SELECTED_LAYERS="$dedup"
fi

layer_selected() {
  for chosen in $SELECTED_LAYERS; do
    [ "$chosen" = "$1" ] && return 0
  done
  return 1
}

# ---------------------------------------------------------------- 工具
have_cmd() { command -v "$1" >/dev/null 2>&1; }

resolve_python() {
  if [ -n "${PYTHON_BIN:-}" ]; then
    if ! have_cmd "$PYTHON_BIN"; then
      echo "release-gate: PYTHON_BIN 不可执行：$PYTHON_BIN" >&2; exit 127
    fi
    return
  fi
  if [ -n "${VIRTUAL_ENV:-}" ] && have_cmd python; then
    PYTHON_BIN="python"
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT/.venv/bin/python"
  elif have_cmd python3; then
    PYTHON_BIN="python3"
  elif have_cmd python; then
    PYTHON_BIN="python"
  else
    echo "release-gate: 找不到 Python 解释器（可用 PYTHON_BIN 指定）" >&2; exit 127
  fi
}
resolve_python
PY="$PYTHON_BIN"

NODE_VER="$(node --version 2>/dev/null || echo '未安装')"
TSC="$WEB_DIR/node_modules/.bin/tsc"
ESLINT="$WEB_DIR/node_modules/.bin/eslint"
VITEST="$WEB_DIR/node_modules/.bin/vitest"
VITE="$WEB_DIR/node_modules/.bin/vite"
PLAYWRIGHT="$WEB_DIR/node_modules/.bin/playwright"
NPM_BIN="$(command -v npm 2>/dev/null || true)"

# 失败/白名单/提示项记录：每行 "用例<TAB>复现命令" / "用例<TAB>原因"
failure_file() { printf '%s/layer-%s-failures.tsv' "$OUT_DIR" "$1"; }
known_file() { printf '%s/layer-%s-known.tsv' "$OUT_DIR" "$1"; }
advisory_file() { printf '%s/layer-%s-advisories.tsv' "$OUT_DIR" "$1"; }
xfail_file() { printf '%s/pytest-xfails.tsv' "$OUT_DIR"; }

emit_advisory() { # $1 层 $2 提示标题 $3 结论
  printf '%s\t%s\n' "$2" "$3" >>"$(advisory_file "$1")"
  printf '  %sℹ%s %s：%s\n' "$C_YELLOW" "$C_OFF" "$2" "$3"
}

emit_failure() { # $1 层 $2 用例 $3 复现命令
  printf '%s\t%s\n' "$2" "$3" >> "$(failure_file "$1")"
  printf '  %s✗%s %s\n' "$C_RED" "$C_OFF" "$2"
  printf '    %s复现：%s%s\n' "$C_DIM" "$3" "$C_OFF"
}

emit_known() { # $1 层 $2 用例 $3 原因
  printf '%s\t%s\n' "$2" "$3" >> "$(known_file "$1")"
  printf '  %s!%s %s\n' "$C_YELLOW" "$C_OFF" "$2"
  printf '    %s白名单原因：%s%s\n' "$C_DIM" "$3" "$C_OFF"
}

set_layer() { # $1 层 $2 状态(pass|fail|skip) $3 说明 $4 开始时间
  local elapsed=$(( $(date +%s) - $4 ))
  eval "LAYER_STATUS_$1=\"\$2\""
  eval "LAYER_TIME_$1=\"\$elapsed\""
  eval "LAYER_NOTE_$1=\"\$3\""
  local depth=$1
  case "$2" in
    pass) printf '\n%s层 %s %s：%s通过%s（%ss）%s\n' "$C_BOLD" "$depth" "$(layer_title "$depth")" "$C_GREEN" "$C_OFF" "$elapsed" "${3:+ — $3}" ;;
    fail) printf '\n%s层 %s %s：%s失败%s（%ss）%s\n' "$C_BOLD" "$depth" "$(layer_title "$depth")" "$C_RED" "$C_OFF" "$elapsed" "${3:+ — $3}" ;;
    skip) printf '\n%s层 %s %s：%s跳过%s %s\n' "$C_BOLD" "$depth" "$(layer_title "$depth")" "$C_YELLOW" "$C_OFF" "$3" ;;
  esac
}

fail_count() { # $1 层
  local file; file="$(failure_file "$1")"
  [ -f "$file" ] && wc -l <"$file" | tr -d ' ' || echo 0
}
known_count() { # $1 层
  local file; file="$(known_file "$1")"
  [ -f "$file" ] && wc -l <"$file" | tr -d ' ' || echo 0
}

run_web() { # 在 apps/dsa-web 里执行
  ( cd "$WEB_DIR" && "$@" )
}

# 带超时执行：macOS 默认没有 timeout/gtimeout，用 Python 起独立进程组再超时 kill。
# 返回 124 表示超时（调用方按失败处理，并给出复现命令）。
run_bounded() { # $1 超时秒数 $2 工作目录 $3 日志文件，其余为命令
  local timeout_seconds="$1" workdir="$2" log_path="$3"
  shift 3
  "$PY" - "$timeout_seconds" "$workdir" "$log_path" "$@" <<'PYTHON'
import os
import signal
import subprocess
import sys

timeout = float(sys.argv[1])
workdir = sys.argv[2]
log_path = sys.argv[3]
command = sys.argv[4:]

with open(log_path, "wb") as log:
    process = subprocess.Popen(
        command,
        cwd=workdir,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        sys.exit(124)
sys.exit(code)
PYTHON
}

# pytest 隔离环境：默认关掉后台 worker 自动启动，避免本机 .env 让 lifespan 测试变真实抓取。
pytest_isolation_exports() {
  if [ "${RELEASE_GATE_ALLOW_WORKERS:-0}" = "1" ]; then
    return 0
  fi
  export DATA_STORAGE_MAINTENANCE_AUTO_START=false
  export MARKET_DATA_AUTO_START=false
  export ESSAY_ANALYSIS_AUTO_START=false
  export ESSAY_DAILY_REPORT_AUTO_START=false
  export ZSXQ_MCP_AUTO_START=false
  export ESSAY_QUANT_AUTO_START=false
  export CONCEPT_THEME_AUTO_START=false
  export INVESTMENT_MONITOR_AUTO_START=false
  export CNINFO_WATCHLIST_AUTO_START=false
  export ICLOUD_KNOWLEDGE_AUTO_START=false
  export SYNC_WATCHDOG_AUTO_START=false
  export AI_NEWS_AUTO_START=false
}

# 各层超时（秒），可用同名环境变量覆盖。全量测试默认给足时间，避免慢机器误判。
PYTEST_TIMEOUT="${PYTEST_TIMEOUT:-3600}"
VITEST_TIMEOUT="${VITEST_TIMEOUT:-1800}"
# 前端单测默认 5s/例，机器有负载时 1~5s 的 UI 用例会被误判超时（表现为 STACK_TRACE_ERROR /
# 元素找不到）。门禁统一放宽到 30s；这不是性能门禁，稳定性优先。
VITEST_TEST_TIMEOUT="${VITEST_TEST_TIMEOUT:-30000}"
# 全量跑 104+ 个测试文件时，默认吃满所有核会让 UI 类用例因 CPU 争用假超时
# （实测 SuperWatchlistPage 单独跑 179/179 全过，全量并行时偶发 1 条超时）。
# 默认限制到 2 个 worker；机器空闲想加速可覆盖 VITEST_MAX_WORKERS。
VITEST_MAX_WORKERS="${VITEST_MAX_WORKERS:-2}"
BUILD_TIMEOUT="${BUILD_TIMEOUT:-1200}"
E2E_TIMEOUT="${E2E_TIMEOUT:-2400}"

# 从 junit（pytest）或 vitest JSON 里提取失败用例。输出 "用例<TAB>首行错误"。
extract_pytest_failures() { # $1 pytest 日志
  "$PY" - "$1" <<'PYTHON'
import sys

path = sys.argv[1]
found = 0
try:
    handle = open(path, encoding="utf-8", errors="replace")
except OSError as exc:
    print("__PARSE_ERROR__\t%s" % exc)
    raise SystemExit(0)

with handle:
    for line in handle:
        line = line.rstrip("\n")
        if line.startswith("FAILED "):
            body = line[len("FAILED "):]
        elif line.startswith("ERROR "):
            body = line[len("ERROR "):]
        else:
            continue
        node_id, _, message = body.partition(" - ")
        node_id = node_id.strip()
        # 只认 pytest 的节点 id（含 ::）；日志里恰好以 FAILED/ERROR 开头的应用日志行要排除
        if "::" not in node_id:
            continue
        found += 1
        print("%s\t%s" % (node_id, message.strip()[:200]))
if not found:
    print("__NO_FAILURE_LINES__\t")
PYTHON
}

extract_pytest_xfails() { # $1 pytest 日志
  "$PY" - "$1" <<'PYTHON'
import sys

path = sys.argv[1]
try:
    handle = open(path, encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)
with handle:
    for line in handle:
        line = line.rstrip("\n")
        if not line.startswith("XFAIL "):
            continue
        body = line[len("XFAIL "):]
        node_id, _, message = body.partition(" - ")
        node_id = node_id.strip()
        if "::" not in node_id:
            continue
        print("%s\t%s" % (node_id, message.strip()[:200]))
PYTHON
}

extract_vitest_failures() { # $1 vitest.json
  "$PY" - "$1" <<'PYTHON'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except Exception as exc:
    print("__PARSE_ERROR__\t%s" % exc)
    raise SystemExit(0)

for suite in data.get("testResults", []):
    file_name = suite.get("name") or "?"
    marker = "/apps/dsa-web/"
    if marker in file_name:
        file_name = file_name.split(marker, 1)[1]
    for case in suite.get("assertionResults", []):
        if case.get("status") != "failed":
            continue
        title = case.get("fullName") or case.get("title") or "?"
        messages = case.get("failureMessages") or []
        first = (messages[0].splitlines() or [""])[0] if messages else ""
        print("%s::%s\t%s" % (file_name, title, first[:200]))
PYTHON
}

# ---------------------------------------------------------------- 白名单
# 已知历史遗留失败：只在这里登记「改动前就失败、且不属于本次改动范围」的用例。
# 格式：匹配串|原因。原因里要写清「为什么可以暂时忽略」和「什么时候该修」。
PYTEST_WHITELIST=(
  # —— 任务台账里已登记的已知遗留失败 ——
  "tests/test_investment_monitor.py::test_source_bi_|日期炸弹：该模块 fixture 硬编码 2026-08-19，跨过该日期后结果漂移；应把 fixture 改成相对当前时间的日期"
  "test_essay_analysis.py::test_queue_claim_save_and_dashboard_are_restart_safe|历史遗留重启安全断言，改动前即失败；待纪要分析存储层统一 queue claim/save 语义后修复"
  "test_essay_quant_service.py::test_cross_section_refresh_|历史遗留横截面刷新断言，改动前即失败；待 quant 服务返回结构与 freshness 语义收敛后修复"
  "tests/test_market_data_service.py::test_intraday_series_reads_only_latest_second_bucket_when_minutes_exist|历史遗留 intraday bucket 断言（分时只读最新秒桶），改动前即失败；待分时数据分桶规则统一后修复"
  "tests/test_market_data_service.py::test_index_intraday_reads_only_latest_second_bucket_when_minutes_exist|历史遗留 index bucket 断言（指数分时只读最新秒桶），改动前即失败；待指数分时规则统一后修复"
  # —— 仓库基线遗留：测试读取的工作流文件已在 d91e9fca 被删除 ——
  "tests/test_daily_analysis_workflow_notification_env.py::|仓库基线遗留：依赖的 .github/workflows/00-daily-analysis.yml 已在提交 d91e9fca「rebrand and streamline repository」中删除，但测试未同步；应恢复工作流或把断言迁移到现有 ci.yml"
  "tests/test_daily_analysis_workflow_llm_env.py::|同上：依赖已删除的 .github/workflows/00-daily-analysis.yml，属仓库基线遗留"
  "tests/test_searxng_env_docs.py::test_daily_analysis_workflow_matches_documented_searxng_variable_mapping|同上：依赖已删除的 .github/workflows/00-daily-analysis.yml，属仓库基线遗留"
  # —— 仓库基线遗留：固定日期/夹具与当前日期漂移 ——
  "tests/test_essay_analysis.py::test_deep_insights_merges_raw_topics_and_expands_feed_search|固定日期夹具（2026-08）与当前日期漂移，产出 0 条导致 StopIteration；属基线遗留"
  "tests/test_essay_analysis.py::test_essay_radar_read_api|固定日期夹具漂移：断言 total_notes==1 实得 0；属基线遗留"
  "tests/test_essay_quant_service.py::test_event_study_uses_next_open_and_nth_session_close|固定日期夹具漂移导致 IndexError；属基线遗留"
  "tests/test_essay_quant_service.py::test_unanalyzed_history_with_explicit_symbol_is_available_to_backtest|固定日期夹具漂移导致 StopIteration；属基线遗留"
  "tests/test_essay_quant_service.py::test_default_rule_excludes_raw_notes_and_reports_robustness|固定日期夹具漂移导致 IndexError；属基线遗留"
  "tests/test_essay_quant_service.py::test_research_catalog_and_run_history_use_real_local_tables|固定日期夹具漂移：run 产出 0 event；属基线遗留"
  "tests/test_essay_quant_service.py::test_source_query_uses_terms_and_does_not_require_direction_word|固定日期夹具漂移：断言 0==1；属基线遗留"
  # —— 当前工作区未提交改动（并行任务）引起的失败，合入前必须复跑 ——
  "tests/test_financial_data_service.py::test_import_is_idempotent_searchable_and_strips_signed_urls|当前工作区 src/repositories/research_note_repo.py 有未提交的 FTS5 检索改造，中文关键词检索命中 0（关掉 FTS 走 LIKE 时该用例通过）；该文件不在本任务写入范围，改造者修好后删除此条"
  "tests/test_financial_data_service.py::test_audio_assets_are_searchable_downloadable_metadata_and_never_claimed_for_ai|同上：未提交的 FTS5 检索改造导致检索命中 0；修复后删除此条"
  # —— 仓库基线遗留：配置/打包契约漂移 ——
  "tests/test_config_registry.py::TestEnvExampleWebSettingsCoverage::test_active_env_example_keys_are_registered_or_hidden_from_web_ui|仓库基线遗留：.env.example 新增键（AI_NEWS_* 等）未注册进 Web 设置注册表/隐藏清单；应补注册表条目后修复"
  "tests/test_macmini_compose_contract.py::test_macmini_disables_every_known_api_auto_start_switch|仓库基线遗留：docker macmini compose 契约未跟上 AI_NEWS_AUTO_START 开关；应补 compose 条目后修复"
  "tests/test_macmini_compose_contract.py::test_macmini_healthcheck_executes_expected_status_contract|仓库基线遗留：compose 健康检查仍指向 /api/health，服务实际为 /api/v1/health；应统一路径后修复"
  "tests/test_jev_standalone_assets.py::test_independent_bundle_keeps_original_index_and_requires_auth|仓库基线遗留：独立打包产物与用例期望的 index 内容不一致（重构后未同步产物/断言）；应在打包流程稳定后修复"
  # —— 测试基建在 Python 3.13 下的兼容问题 ——
  "tests/test_agent_models_api.py::AgentSkillsEndpointTestCase::test_chat_request_empty_skills_clears_context_without_triggering_activate_all|测试桩与 Python 3.13 不兼容：_ImmediateLoop.run_in_executor() 被以 4 个参数调用（实现只接受 3 个）；属测试基建问题，应随测试桩升级修复"
  # —— 全量运行时被前序用例全局状态干扰（单独运行通过），属测试隔离问题 ——
  "tests/test_api_contract.py::test_system_config_contract|全量运行时被前序用例的全局状态/DB 单例干扰（单独运行通过），属测试隔离问题；应在夹具层隔离后修复"
  "tests/test_api_contract.py::test_system_config_schema_contract|同上：全量运行时被前序用例全局状态干扰，单跑通过"
  "tests/test_api_contract.py::test_system_scheduler_status_contract|同上：全量运行时被前序用例全局状态干扰，单跑通过"
  "tests/test_storage.py::TestStorage::test_get_instance_waits_for_cold_start_initialization|全量运行时 DatabaseManager 单例被前序用例改写（单独运行通过）；属测试隔离问题"
  "tests/test_storage.py::TestStorage::test_get_visible_conversation_messages_limit_returns_ordered_tail|同上：全量运行时 DB 单例/数据被前序用例污染，单跑通过"
)

# 前端 vitest 白名单，同样为「匹配串|原因」。
VITEST_WHITELIST=(
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage sends multiple selected skills in order|placeholder 断言过时（旧占位文案「例如：分析 600519」），改动 UI 前即失败；应在 ChatPage 测试整体迁移到「输入研究问题」时统一修"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage collapses the mobile skill picker by default and keeps selected skills when sending|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage omits skills when all concrete skills are cleared|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage allows sending with base follow-up context before report hydration completes|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage falls back to base stock context when recordId is missing|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage switches active stock context for explicit switch messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage switches to the single new stock when the current stock appears first|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage keeps active stock context for compare messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage keeps active stock context for difference-style compare messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage keeps active stock context when the compared stock appears first|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage keeps active stock context for choice-style multi-stock messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage switches active stock context for single-stock difference phrasing|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage switches active stock context for lowercase US ticker switch messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage keeps active stock context when clicking the current session|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage restores active stock context from loaded session messages|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage clears active stock context when starting a new chat or switching sessions|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage clears active stock context when deleting the current session|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::ChatPage ignores malformed follow-up query params|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::watchlist button with code variants shows \"从自选删除\" when canonical code is in watchlist and user inputs variant|placeholder 断言过时（旧占位文案「例如」），改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::watchlist button with code variants shows \"从自选删除\" for HK variant codes|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::watchlist button with code variants matches raw HK watchlist entries before rendering the watchlist action|placeholder 断言过时，改动前即失败"
  "src/pages/__tests__/ChatPage.test.tsx::watchlist button with code variants removes the matched raw HK watchlist entry instead of adding a duplicate variant|placeholder 断言过时，改动前即失败"
)

match_whitelist() { # $1 用例 $2 whitelist 数组名（bash 3.2 不能按名取数组，用 eval）
  local target="$1"
  local list_name="$2"
  local count entry pat why i
  eval "count=\${#${list_name}[@]}"
  i=0
  while [ "$i" -lt "$count" ]; do
    eval "entry=\${${list_name}[$i]}"
    if [ -n "$entry" ]; then
      pat="${entry%%|*}"
      why="${entry#*|}"
      case "$target" in
        *"$pat"*|"$pat"*) printf '%s' "$why"; return 0 ;;
      esac
    fi
    i=$((i + 1))
  done
  return 1
}

classify_pytest_failures() { # $1 提取结果文件
  while IFS=$'\t' read -r node_id message; do
    [ -n "$node_id" ] || continue
    case "$node_id" in
      __NO_FAILURE_LINES__) continue ;;
      __PARSE_ERROR__)
        emit_failure 2 "pytest 报告解析失败：$message" "cd \"$ROOT\" && $PY -m pytest -m 'not network' -q"
        continue
        ;;
    esac
    if reason="$(match_whitelist "$node_id" PYTEST_WHITELIST)"; then
      emit_known 2 "$node_id" "$reason"
    else
      emit_failure 2 "$node_id${message:+ — $message}" "cd \"$ROOT\" && $PY -m pytest \"$node_id\" -q"
    fi
  done <"$1"
}

classify_vitest_failures() { # $1 层号 $2 提取结果文件
  local layer="$1" file="$2" node_id message reason
  while IFS=$'\t' read -r node_id message; do
    [ -n "$node_id" ] || continue
    if [ "$node_id" = "__PARSE_ERROR__" ]; then
      emit_failure "$layer" "vitest 报告解析失败：$message" "cd apps/dsa-web && npx vitest run"
      continue
    fi
    if reason="$(match_whitelist "$node_id" VITEST_WHITELIST)"; then
      emit_known "$layer" "$node_id" "$reason"
    else
      local file_part="${node_id%%::*}"
      local test_part="${node_id#*::}"
      emit_failure "$layer" "$node_id${message:+ — $message}" "cd apps/dsa-web && npx vitest run \"$file_part\" -t \"$test_part\""
    fi
  done <"$file"
}

# ---------------------------------------------------------------- 顺序干扰复检
# 全量顺序跑时，个别用例会被前序用例留下的全局状态（DB 单例、进程环境）带崩，
# 但单独跑该文件是通的。这类失败不是代码回归，也不该直接放过：
# 门禁对「失败用例所在的文件」单独复跑一次，通过则降级为「已知遗留（套件顺序干扰）」
# 并在报告里保留复检证据；仍然失败的继续阻断。
recheck_pytest_order_flakes() {
  local failures moved file log rc files attempt
  failures="$(failure_file 2)"
  [ -s "$failures" ] || return 0
  moved="$(mktemp)"
  : >"$moved"
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    [ -f "$ROOT/$file" ] || continue
    log="$OUT_DIR/recheck-pytest-$(printf '%s' "$file" | tr '/.' '__').log"
    rc=1
    attempt=1
    while [ "$attempt" -le 2 ]; do
      (
        pytest_isolation_exports
        run_bounded 900 "$ROOT" "$log" "$PY" -m pytest "$file" -q
      )
      rc=$?
      [ "$rc" -eq 0 ] && break
      printf '  %s…%s %s：单独复跑第 %s 次未通过，重试一次\n' "$C_DIM" "$C_OFF" "$file" "$attempt"
      attempt=$((attempt + 1))
    done
    if [ "$rc" -eq 0 ]; then
      printf '  %s↺%s %s：全量顺序执行失败、单独执行通过（判为套件顺序干扰）\n' "$C_YELLOW" "$C_OFF" "$file"
      cut -f1 "$failures" | grep -F "$file::" >>"$moved" || true
    fi
  done < <(cut -f1 "$failures" | sed 's/::.*//' | sort -u)
  if [ -s "$moved" ]; then
    while IFS= read -r node_id; do
      [ -n "$node_id" ] || continue
      emit_known 2 "$node_id" "全量顺序执行时被前序用例的全局状态（DB 单例/进程环境）干扰；单独执行该测试文件通过（复检：$PY -m pytest <文件> -q）"
    done <"$moved"
    awk -F '\t' 'NR==FNR{m[$1]=1;next} !($1 in m){print}' "$moved" "$failures" >"$failures.tmp"
    mv "$failures.tmp" "$failures"
  fi
  rm -f "$moved"
}

recheck_vitest_order_flakes() { # $1 层
  local layer="$1" failures moved file log json rc files attempt target
  failures="$(failure_file "$layer")"
  [ -s "$failures" ] || return 0
  moved="$(mktemp)"
  : >"$moved"
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    local target="$file"
    case "$target" in
      /*) [ -f "$target" ] || continue ;;
      *) target="$WEB_DIR/$file"; [ -f "$target" ] || continue ;;
    esac
    log="$OUT_DIR/recheck-vitest-$(printf '%s' "$file" | tr '/.' '__').log"
    json="$log.json"
    rm -f "$json"
    rc=1
    attempt=1
    while [ "$attempt" -le 2 ]; do
      run_bounded 900 "$WEB_DIR" "$log" \
        "$VITEST" run --testTimeout="$VITEST_TEST_TIMEOUT" --hookTimeout="$VITEST_TEST_TIMEOUT" \
        "$target" --reporter=default --reporter=json --outputFile="$json"
      rc=$?
      [ "$rc" -eq 0 ] && break
      printf '  %s…%s %s：单独复跑第 %s 次未通过，重试一次\n' "$C_DIM" "$C_OFF" "$file" "$attempt"
      attempt=$((attempt + 1))
    done
    if [ "$rc" -eq 0 ]; then
      printf '  %s↺%s %s：全量并行时失败、单独执行通过（判为套件顺序/并发干扰）\n' "$C_YELLOW" "$C_OFF" "$file"
      cut -f1 "$failures" | grep -F "$file::" >>"$moved" || true
    fi
  done < <(cut -f1 "$failures" | sed 's/::.*//' | sort -u)
  if [ -s "$moved" ]; then
    while IFS= read -r node_id; do
      [ -n "$node_id" ] || continue
      emit_known "$layer" "$node_id" "全量并行执行时被其它用例干扰；单独执行该测试文件通过（复检：cd apps/dsa-web && npx vitest run <文件>）"
    done <"$moved"
    awk -F '\t' 'NR==FNR{m[$1]=1;next} !($1 in m){print}' "$moved" "$failures" >"$failures.tmp"
    mv "$failures.tmp" "$failures"
  fi
  rm -f "$moved"
}

# ---------------------------------------------------------------- 层 1：静态检查
layer_1() {
  local started fails=0 notes="" sub_status
  started="$(date +%s)"
  echo "==> 层 1：静态检查（py_compile / flake8 / tsc / eslint）"

  # 1a. Python 语法
  if ( cd "$ROOT" && "$PY" -m compileall -q main.py server.py api src data_provider bot >"$OUT_DIR/py_compile.log" 2>&1 ); then
    echo "  ✓ py_compile"
  else
    fails=1; sub_status="py_compile 失败"
    emit_failure 1 "py_compile（Python 语法错误）" "cd \"$ROOT\" && $PY -m compileall -q main.py server.py api src data_provider bot"
    sed -n '1,20p' "$OUT_DIR/py_compile.log" | sed 's/^/     /'
  fi

  # 1b. flake8（未安装时跳过，不阻断）
  if "$PY" -m flake8 --version >/dev/null 2>&1; then
    if ( cd "$ROOT" && "$PY" -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics >"$OUT_DIR/flake8.log" 2>&1 ); then
      echo "  ✓ flake8（E9/F63/F7/F82）"
    else
      fails=1
      emit_failure 1 "flake8（阻断级错误 E9/F63/F7/F82）" "cd \"$ROOT\" && $PY -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics"
      sed -n '1,20p' "$OUT_DIR/flake8.log" | sed 's/^/     /'
    fi
  else
    echo "  ${C_YELLOW}!${C_OFF} flake8 未安装，跳过（pip install flake8 后启用）"
    notes="flake8 未安装已跳过"
  fi

  # 1c. TypeScript 类型检查
  if [ ! -x "$TSC" ]; then
    fails=1
    emit_failure 1 "tsc 不可用（缺少 apps/dsa-web/node_modules）" "cd apps/dsa-web && npm ci && npx tsc -b --noEmit"
  elif ( cd "$WEB_DIR" && "$TSC" -b --noEmit >"$OUT_DIR/tsc.log" 2>&1 ); then
    echo "  ✓ tsc -b --noEmit"
  else
    fails=1
    local tsc_detail
    tsc_detail="$(grep -m3 'error TS' "$OUT_DIR/tsc.log" | sed 's/^ *//' | tr '\n' ' ' | cut -c1-220)"
    emit_failure 1 "tsc 类型错误${tsc_detail:+：$tsc_detail}" "cd apps/dsa-web && npx tsc -b --noEmit"
    sed -n '1,30p' "$OUT_DIR/tsc.log" | sed 's/^/     /'
  fi

  # 1d. ESLint
  if [ ! -x "$ESLINT" ]; then
    fails=1
    emit_failure 1 "eslint 不可用（缺少 apps/dsa-web/node_modules）" "cd apps/dsa-web && npm ci && npx eslint ."
  elif ( cd "$WEB_DIR" && "$ESLINT" . --format stylish >"$OUT_DIR/eslint.log" 2>&1 ); then
    echo "  ✓ eslint"
  else
    fails=1
    local eslint_detail
    eslint_detail="$(grep -m3 ' error ' "$OUT_DIR/eslint.log" | sed 's/^ *//' | tr '\n' ' ' | cut -c1-220)"
    emit_failure 1 "eslint 错误${eslint_detail:+：$eslint_detail}" "cd apps/dsa-web && npx eslint ."
    sed -n '1,25p' "$OUT_DIR/eslint.log" | sed 's/^/     /'
  fi

  if [ "$fails" -eq 0 ]; then
    set_layer 1 pass "$notes" "$started"
  else
    set_layer 1 fail "见下方失败用例" "$started"
  fi
}

# ---------------------------------------------------------------- 层 2：后端功能
layer_2() {
  local started rc summary junit="$OUT_DIR/pytest-junit.xml" extracted="$OUT_DIR/pytest-failures.tsv"
  started="$(date +%s)"
  echo "==> 层 2：后端功能（pytest -m \"not network\" 全量）"
  : >"$extracted"
  rm -f "$junit"

  # 本机 .env 往往连着真实数据源，lifespan 测试会把十几个后台 worker 一起拉起来
  # （真实抓取/同步），导致「测试挂住十几分钟」而不是失败。门禁默认在 pytest 进程内
  # 关闭这些后台 worker 的自动启动，保证可复现；需要验证真实启动链路时设
  # RELEASE_GATE_ALLOW_WORKERS=1。
  (
    pytest_isolation_exports
    run_bounded "$PYTEST_TIMEOUT" "$ROOT" "$OUT_DIR/pytest.log" \
      "$PY" -m pytest -m "not network" -q --junitxml="$junit" -rfx
  )
  rc=$?

  if [ "$rc" -eq 124 ]; then
    emit_failure 2 "pytest 超过 ${PYTEST_TIMEOUT}s 未结束（后台 worker 或外部依赖疑似阻塞）" "cd \"$ROOT\" && $PY -m pytest -m 'not network' -q  # 或设 RELEASE_GATE_ALLOW_WORKERS=1 复现真实启动链路"
    set_layer 2 fail "pytest 超时" "$started"
    return
  fi

  summary="$(grep -E '(passed|failed|error)' "$OUT_DIR/pytest.log" | tail -1 | sed 's/^[= ]*//; s/[= ]*$//')"
  extract_pytest_failures "$OUT_DIR/pytest.log" >"$extracted"
  extract_pytest_xfails "$OUT_DIR/pytest.log" >"$(xfail_file)"
  if [ "$rc" -ne 0 ] && ! grep -q '__NO_FAILURE_LINES__\|__PARSE_ERROR__' "$extracted" && [ ! -s "$extracted" ]; then
    printf '%s\t%s\n' "__PARSE_ERROR__" "pytest 退出码 $rc 但日志里没有 FAILED 行（可能是收集中断，见 pytest.log）" >"$extracted"
  fi

  echo "  ${C_DIM}${summary:-无 pytest 汇总行}${C_OFF}"
  classify_pytest_failures "$extracted"
  recheck_pytest_order_flakes

  # 端点基线 / 覆盖率提示（不阻断）：api-tests 交付的清单脚本存在时顺手跑一次
  local coverage_note=""
  if [ -f "$ROOT/scripts/list_api_endpoints.py" ]; then
    if ( cd "$ROOT" && "$PY" scripts/list_api_endpoints.py --check ) >"$OUT_DIR/api-endpoints-check.log" 2>&1; then
      emit_advisory 2 "API 端点基线" "最新（与 openapi() 一致）"
    else
      emit_advisory 2 "API 端点基线" "已过期：路由有增删但 tests/fixtures/api_endpoints.json 未重新生成（重跑 python scripts/list_api_endpoints.py）"
    fi
    ( cd "$ROOT" && "$PY" scripts/list_api_endpoints.py --check-coverage --show 5 ) >"$OUT_DIR/api-coverage.log" 2>&1
    local coverage_line
    coverage_line="$(grep -E '端点 [0-9]+ 个|冒烟层实际调用|真空端点' "$OUT_DIR/api-coverage.log" \
      | sed 's/^\[coverage\] //' | head -6 | tr '\n' ' ' | sed 's/ *$//' | cut -c1-420)"
    emit_advisory 2 "API 端点覆盖率（不阻断）" "${coverage_line:-见 $OUT_DIR/api-coverage.log}"
    coverage_note="端点覆盖率见报告提示项"
  fi

  local failed xfail_count; failed="$(fail_count 2)"
  xfail_count="$(wc -l <"$(xfail_file)" 2>/dev/null | tr -d ' ')"
  xfail_count="${xfail_count:-0}"
  if [ "$failed" -eq 0 ]; then
    set_layer 2 pass "${summary}；xfail ${xfail_count} 条单列" "$started"
  else
    set_layer 2 fail "$failed 条非白名单失败（${summary}；xfail ${xfail_count} 条单列）" "$started"
  fi
}

# ---------------------------------------------------------------- 层 3：前端使用
layer_3() {
  local started rc summary failure_tsv="$OUT_DIR/vitest-failures.tsv" json="$OUT_DIR/vitest.json"
  started="$(date +%s)"
  echo "==> 层 3：前端使用（vitest run 全量）"
  : >"$failure_tsv"
  rm -f "$json"

  if [ ! -x "$VITEST" ]; then
    emit_failure 3 "vitest 不可用（缺少 apps/dsa-web/node_modules）" "cd apps/dsa-web && npm ci && npx vitest run"
    set_layer 3 fail "前端依赖未安装" "$started"
    return
  fi

  run_bounded "$VITEST_TIMEOUT" "$WEB_DIR" "$OUT_DIR/vitest.log" \
    "$VITEST" run --testTimeout="$VITEST_TEST_TIMEOUT" --hookTimeout="$VITEST_TEST_TIMEOUT" \
    --maxWorkers="$VITEST_MAX_WORKERS" \
    --reporter=default --reporter=json --outputFile="$json"
  rc=$?
  if [ "$rc" -eq 124 ] && [ ! -s "$json" ]; then
    emit_failure 3 "vitest 超过 ${VITEST_TIMEOUT}s 未结束" "cd apps/dsa-web && npx vitest run"
    set_layer 3 fail "vitest 超时" "$started"
    return
  fi

  summary="$(grep -E '^ *Tests ' "$OUT_DIR/vitest.log" | tail -1 | sed 's/^ *//')"
  if [ -f "$json" ]; then
    extract_vitest_failures "$json" >"$failure_tsv"
  elif [ "$rc" -ne 0 ]; then
    printf '%s\t%s\n' "__PARSE_ERROR__" "vitest 退出码 $rc 且无 JSON 报告（见 $OUT_DIR/vitest.log）" >"$failure_tsv"
  fi

  echo "  ${C_DIM}${summary:-无 vitest 汇总行}${C_OFF}"
  classify_vitest_failures 3 "$failure_tsv"
  recheck_vitest_order_flakes 3

  # 前端页面测试覆盖率缺口（提示项，不阻断）
  local coverage_note=""
  if [ -f "$ROOT/scripts/report_frontend_test_coverage.py" ]; then
    ( cd "$ROOT" && "$PY" scripts/report_frontend_test_coverage.py ) >"$OUT_DIR/frontend-coverage.log" 2>&1
    local coverage_line
    coverage_line="$(grep -E '^页面总数' "$OUT_DIR/frontend-coverage.log" | head -1)"
    emit_advisory 3 "前端页面测试覆盖率（不阻断）" "${coverage_line:-见 $OUT_DIR/frontend-coverage.log}"
    coverage_note="页面覆盖缺口见报告提示项"
  fi

  local failed; failed="$(fail_count 3)"
  if [ "$failed" -eq 0 ]; then
    set_layer 3 pass "$summary${coverage_note:+；$coverage_note}" "$started"
  else
    set_layer 3 fail "$failed 条非白名单失败（${summary}）" "$started"
  fi
}

# ---------------------------------------------------------------- 层 4：界面规范
layer_4() {
  local started fails=0 notes=""
  started="$(date +%s)"
  echo "==> 层 4：界面规范（页面预算 + UI 治理断言）"

  # 4a. 页面内容预算（§10）
  if [ -f "$ROOT/scripts/audit_page_budget.py" ]; then
    ( cd "$ROOT" && "$PY" scripts/audit_page_budget.py ) >"$OUT_DIR/page-budget.log" 2>&1
    local offending total
    offending="$(grep -o '[0-9][0-9]* 个超出' "$OUT_DIR/page-budget.log" | tail -1 | grep -o '[0-9][0-9]*')"
    total="$(grep -o '共 [0-9][0-9]* 个页面' "$OUT_DIR/page-budget.log" | tail -1 | grep -o '[0-9][0-9]*')"
    offending="${offending:-0}"
    echo "  ${C_DIM}页面预算：${total:-?} 个页面，${offending} 个超出（基线 ${PAGE_BUDGET_BASELINE}）${C_OFF}"
    if [ "$offending" -gt "$PAGE_BUDGET_BASELINE" ]; then
      fails=1
      awk 'NF && $1 != "页面" && $1 !~ /^-+$/ && $1 != "共" && $NF != "—"' "$OUT_DIR/page-budget.log" | sed 's/^/     /'
      emit_failure 4 "页面预算超标 ${offending} 个 > 基线 ${PAGE_BUDGET_BASELINE}" "cd \"$ROOT\" && $PY scripts/audit_page_budget.py --top 10"
    else
      echo "  ✓ 页面预算未变差（${offending} ≤ ${PAGE_BUDGET_BASELINE}）"
    fi
  else
    notes="页面预算脚本缺失，已跳过"
    echo "  ${C_YELLOW}!${C_OFF} scripts/audit_page_budget.py 不存在，跳过"
  fi

  # 4b. UI 治理断言（原生 title、令牌、主题引导）
  if [ -x "$VITEST" ]; then
    local json="$OUT_DIR/ui-governance.json" tsv="$OUT_DIR/ui-governance-failures.tsv"
    : >"$tsv"
    rm -f "$json"
    run_bounded "$VITEST_TIMEOUT" "$WEB_DIR" "$OUT_DIR/ui-governance.log" \
      "$VITEST" run --testTimeout="$VITEST_TEST_TIMEOUT" --hookTimeout="$VITEST_TEST_TIMEOUT" \
      tests/ui_governance.test.ts tests/login-theme-tokens.test.ts tests/index.theme-bootstrap.test.ts \
      --reporter=default --reporter=json --outputFile="$json"
    local rc=$?
    if [ -f "$json" ]; then
      extract_vitest_failures "$json" >"$tsv"
      classify_vitest_failures 4 "$tsv"
    elif [ "$rc" -ne 0 ]; then
      printf '%s\t%s\n' "__PARSE_ERROR__" "UI 治理断言退出码 $rc 且无 JSON 报告" >"$tsv"
      classify_vitest_failures 4 "$tsv"
    fi
    if [ "$(fail_count 4)" -eq 0 ]; then
      echo "  ✓ UI 治理断言（原生 title / 主题令牌）"
    else
      fails=1
    fi
  else
    fails=1
    emit_failure 4 "vitest 不可用，UI 治理断言未执行" "cd apps/dsa-web && npm ci && npx vitest run tests/ui_governance.test.ts"
  fi

  if [ "$fails" -eq 0 ]; then
    set_layer 4 pass "$notes" "$started"
  else
    set_layer 4 fail "见下方失败用例" "$started"
  fi
}

# ---------------------------------------------------------------- 层 5：构建
layer_5() {
  local started fails=0 notes=""
  started="$(date +%s)"
  echo "==> 层 5：构建（vite build + 后端 import smoke）"

  # 5a. 前端生产构建
  if [ -n "$NPM_BIN" ]; then
    local build_rc
    run_bounded "$BUILD_TIMEOUT" "$WEB_DIR" "$OUT_DIR/web-build.log" "$NPM_BIN" run build
    build_rc=$?
    if [ "$build_rc" -eq 0 ]; then
      echo "  ✓ npm run build（tsc -b + vite build）"
    else
      fails=1
      emit_failure 5 "前端构建失败（npm run build，退出码 $build_rc）" "cd apps/dsa-web && npm run build"
      tail -25 "$OUT_DIR/web-build.log" | sed 's/^/     /'
    fi
  elif [ -x "$VITE" ] && [ -x "$TSC" ]; then
    local build_rc
    run_bounded "$BUILD_TIMEOUT" "$WEB_DIR" "$OUT_DIR/web-build.log" "$TSC" -b
    build_rc=$?
    [ "$build_rc" -eq 0 ] && run_bounded "$BUILD_TIMEOUT" "$WEB_DIR" "$OUT_DIR/web-build.log" "$VITE" build
    build_rc=$?
    if [ "$build_rc" -eq 0 ]; then
      echo "  ✓ tsc -b + vite build"
    else
      fails=1
      emit_failure 5 "前端构建失败（tsc -b + vite build，退出码 $build_rc）" "cd apps/dsa-web && npx tsc -b && npx vite build"
      tail -25 "$OUT_DIR/web-build.log" | sed 's/^/     /'
    fi
  else
    fails=1
    emit_failure 5 "前端构建工具不可用（缺少 node/npm 或 node_modules）" "cd apps/dsa-web && npm ci && npm run build"
  fi

  # 5b. 后端 import smoke：真实入口能否装配，避免「测试过了但服务起不来」
  run_bounded 300 "$ROOT" "$OUT_DIR/backend-import.log" \
    "$PY" -c "import api.app, server; print('backend import ok')"
  if [ $? -eq 0 ]; then
    echo "  ✓ 后端 import smoke（api.app + server）"
  else
    fails=1
    notes="后端 import smoke 失败"
    emit_failure 5 "后端 import smoke（import api.app, server）" "cd \"$ROOT\" && $PY -c 'import api.app, server'"
    tail -20 "$OUT_DIR/backend-import.log" | sed 's/^/     /'
  fi

  if [ "$fails" -eq 0 ]; then
    set_layer 5 pass "$notes" "$started"
  else
    set_layer 5 fail "构建未通过" "$started"
  fi
}

# ---------------------------------------------------------------- 层 6：E2E
layer_6() {
  local started
  started="$(date +%s)"
  echo "==> 层 6：E2E 主流程（Playwright）"

  if [ -z "${DSA_WEB_SMOKE_PASSWORD:-}" ]; then
    echo "  ${C_YELLOW}!${C_OFF} 未设置 DSA_WEB_SMOKE_PASSWORD，E2E 整层跳过（不阻断发布）"
    echo "    ${C_DIM}启用方式：DSA_WEB_SMOKE_PASSWORD='<登录密码>' bash scripts/release_gate.sh --layer=6${C_OFF}"
    set_layer 6 skip "未设置 DSA_WEB_SMOKE_PASSWORD（需要真实登录密码才能起后端 + 前端）" "$started"
    return
  fi

  if [ ! -x "$PLAYWRIGHT" ]; then
    echo "  ${C_YELLOW}!${C_OFF} Playwright 未安装，E2E 跳过（不阻断发布）"
    set_layer 6 skip "apps/dsa-web 未安装 Playwright（npm ci 后执行 npx playwright install chromium 启用）" "$started"
    return
  fi

  run_bounded "$E2E_TIMEOUT" "$WEB_DIR" "$OUT_DIR/playwright.log" \
    "$PLAYWRIGHT" test e2e/smoke.spec.ts e2e/user-flows.spec.ts --reporter=list
  local rc=$?
  tail -20 "$OUT_DIR/playwright.log" | sed 's/^/     /'
  if [ "$rc" -eq 0 ]; then
    echo "  ✓ Playwright E2E 通过"
    set_layer 6 pass "smoke + user-flows 全部通过" "$started"
  else
    # E2E 不阻断：真实登录/浏览器环境差异较大，先作为发布信号暴露出来
    echo "  ${C_YELLOW}!${C_OFF} Playwright E2E 未通过（本层不阻断，见日志）"
    grep -E '✘|×|failed|Error:' "$OUT_DIR/playwright.log" | head -15 | sed 's/^/     /'
    set_layer 6 fail "E2E 未通过（不阻断发布；需要 DSA_WEB_SMOKE_PASSWORD 与真实服务环境）" "$started"
  fi
}

# ---------------------------------------------------------------- 汇总与报告
status_icon() {
  case "$1" in
    pass) printf '%s✅ 通过%s' "$C_GREEN" "$C_OFF" ;;
    fail) printf '%s❌ 失败%s' "$C_RED" "$C_OFF" ;;
    skip) printf '%s⏭  跳过%s' "$C_YELLOW" "$C_OFF" ;;
    *) printf '—' ;;
  esac
}

status_word() {
  case "$1" in
    pass) printf '通过' ;;
    fail) printf '失败' ;;
    skip) printf '跳过' ;;
    *) printf '未执行' ;;
  esac
}

git_info() {
  local sha branch dirty
  sha="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
  branch="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')"
  dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  printf '%s@%s（工作区变更 %s 项）' "$sha" "$branch" "$dirty"
}

write_report() { # $1 结论文本 $2 阻断失败层数 $3 总耗时
  local conclusion="$1" blocking_failures="$2" total_time="$3"
  {
    echo "# 发布门禁报告（release gate）"
    echo
    echo "- 生成时间：$(date '+%Y-%m-%d %H:%M:%S %z')"
    echo "- 模式：\`${MODE_LABEL:-$SELECTED_LAYERS}\`（层：${SELECTED_LAYERS# }）"
    echo "- Git：$(git_info)"
    echo "- Python：\`$PY\`（$("$PY" --version 2>&1 | head -1)）/ Node：$NODE_VER"
    echo "- 结论：**$conclusion**"
    echo "- 总耗时：${total_time}s（层 2/3 为全量测试，耗时以实际为准）"
    echo
    echo "## 分层结论"
    echo
    echo "| 层 | 内容 | 结果 | 阻断 | 耗时 | 说明 |"
    echo "| --- | --- | --- | --- | --- | --- |"
    for n in $SELECTED_LAYERS; do
      blocking="否"
      [ "$(layer_blocking "$n")" = "1" ] && blocking="是"
      note="$(layer_note "$n")"
      note="${note//|/\\|}"
      echo "| $n | $(layer_title "$n") | $(status_word "$(layer_status "$n")") | $blocking | $(layer_time "$n")s | ${note:-—} |"
    done
    echo
    echo "## 失败明细"
    echo
    local any_failure=""
    for n in $SELECTED_LAYERS; do
      if [ "$(fail_count "$n")" -gt 0 ]; then
        any_failure=1
        echo "### 层 $n $(layer_title "$n")（$( [ "$(layer_blocking "$n")" = "1" ] && echo '阻断' || echo '不阻断' )）"
        echo
        echo "| 失败用例 | 复现命令 |"
        echo "| --- | --- |"
        while IFS=$'\t' read -r case_id repro; do
          [ -n "$case_id" ] || continue
          case_escaped="${case_id//|/\\|}"
          echo "| $case_escaped | \`$repro\` |"
        done <"$(failure_file "$n")"
        echo
      fi
    done
    [ -z "$any_failure" ] && { echo "无。"; echo; }

    # 抖动单列：全量并行时失败、单独复跑通过的用例，不算失败也不算历史遗留。
    echo "## 本次抖动（全量并行时失败、单独复跑通过，不阻断）"
    echo
    local any_flaky=""
    for n in $SELECTED_LAYERS; do
      if [ "$(known_count "$n")" -gt 0 ]; then
        local flaky_rows
        flaky_rows="$(awk -F '\t' 'index($2,"全量并行") || index($2,"全量顺序"){print $1 "\t" $2}' "$(known_file "$n")")"
        if [ -n "$flaky_rows" ]; then
          any_flaky=1
          echo "### 层 $n $(layer_title "$n")"
          echo
          echo "| 用例 | 说明 |"
          echo "| --- | --- |"
          while IFS=$'\t' read -r case_id reason; do
            [ -n "$case_id" ] || continue
            case_escaped="${case_id//|/\\|}"
            echo "| $case_escaped | $reason |"
          done <<<"$flaky_rows"
          echo
          echo "> 同一文件反复出现在这里，说明该测试对并发/顺序敏感，应该去修测试本身，而不是长期依赖复跑。"
          echo
        fi
      fi
    done
    [ -z "$any_flaky" ] && { echo "无。"; echo; }

    echo "## 已知遗留失败（白名单，不阻断）"
    echo
    local any_known=""
    for n in $SELECTED_LAYERS; do
      if [ "$(known_count "$n")" -gt 0 ]; then
        local known_rows
        known_rows="$(awk -F '\t' '!index($2,"全量并行") && !index($2,"全量顺序"){print $1 "\t" $2}' "$(known_file "$n")")"
        if [ -n "$known_rows" ]; then
          any_known=1
          echo "### 层 $n $(layer_title "$n")"
          echo
          echo "| 用例 | 为什么暂时可以忽略 |"
          echo "| --- | --- |"
          while IFS=$'\t' read -r case_id reason; do
            [ -n "$case_id" ] || continue
            case_escaped="${case_id//|/\\|}"
            echo "| $case_escaped | $reason |"
          done <<<"$known_rows"
          echo
        fi
      fi
    done
    [ -z "$any_known" ] && { echo "本次未命中任何白名单条目。"; echo; }

    echo "## 已知 xfail（不参与通过/失败判定）"
    echo
    if [ -s "$(xfail_file)" ]; then
      echo "| 用例 | 标记原因 |"
      echo "| --- | --- |"
      while IFS=$'\t' read -r case_id reason; do
        [ -n "$case_id" ] || continue
        echo "| ${case_id//|/\\|} | ${reason:-—} |"
      done <"$(xfail_file)"
    else
      echo "本次无 xfail。"
    fi
    echo
    echo "## 附加提示项（不阻断）"
    echo
    local any_advisory=""
    for n in $SELECTED_LAYERS; do
      local adv_file; adv_file="$(advisory_file "$n")"
      if [ -s "$adv_file" ]; then
        any_advisory=1
        echo "### 层 $n $(layer_title "$n")"
        echo
        echo "| 提示 | 结论 |"
        echo "| --- | --- |"
        while IFS=$'\t' read -r label detail; do
          [ -n "$label" ] || continue
          echo "| ${label//|/\\|} | ${detail//|/\\|} |"
        done <"$adv_file"
        echo
      fi
    done
    [ -z "$any_advisory" ] && { echo "无。"; echo; }

    echo "## 进一步排查"
    echo
    echo "- 完整日志目录：\`$OUT_DIR\`"
    echo "- 只跑失败层：\`bash scripts/release_gate.sh --layer=<层号>\`"
    echo "- 单条后端用例：\`cd \"$ROOT\" && $PY -m pytest \"<用例>\" -q\`"
    echo "- 单条前端用例：\`cd apps/dsa-web && npx vitest run <文件> -t \"<用例名>\"\`"
    echo "- 文档：\`docs/testing.md\`"
  } >"$REPORT"
}

print_summary() { # $1 阻断失败层数
  local blocking_failures="$1" total_time
  total_time=$(( $(date +%s) - START_EPOCH ))
  echo
  printf '%s\n' "${C_BOLD}================ 发布门禁汇总 ================${C_OFF}"
  printf '%-4s %-10s %-8s %-6s %-7s %s\n' "层" "内容" "结果" "阻断" "耗时" "说明"
  printf '%s\n' "--------------------------------------------------------------------------------"
  for n in $SELECTED_LAYERS; do
    blocking="否"
    [ "$(layer_blocking "$n")" = "1" ] && blocking="是"
    printf '%-4s %-10s %-8s %-6s %-7s %s\n' \
      "$n" "$(layer_title "$n")" \
      "$(status_word "$(layer_status "$n")")" \
      "$blocking" "$(layer_time "$n")s" "$(layer_note "$n")"
  done
  printf '%s\n' "--------------------------------------------------------------------------------"
  if [ "$blocking_failures" -eq 0 ]; then
    printf '%s结论：门禁通过%s（总耗时 %ss）\n' "$C_GREEN" "$C_OFF" "$total_time"
  else
    printf '%s结论：门禁未通过%s（%s 个阻断层失败；总耗时 %ss）\n' "$C_RED" "$C_OFF" "$blocking_failures" "$total_time"
  fi
  printf '报告：%s\n' "$REPORT"
  printf '日志：%s\n' "$OUT_DIR"
  echo "${C_BOLD}==============================================${C_OFF}"
}

# ---------------------------------------------------------------- 主流程
echo "${C_BOLD}release-gate${C_OFF} 模式：${MODE_LABEL:-$SELECTED_LAYERS}｜层：${SELECTED_LAYERS# }"
echo "${C_DIM}仓库：$ROOT${C_OFF}"
echo "${C_DIM}Python：${PY}｜Node：${NODE_VER}｜日志：${OUT_DIR}${C_OFF}"

# 清理旧记录
for n in $ALL_LAYERS; do
  rm -f "$(failure_file "$n")" "$(known_file "$n")" "$(advisory_file "$n")"
done
rm -f "$(xfail_file)"

for n in $SELECTED_LAYERS; do
  case "$n" in
    1) layer_1 ;;
    2) layer_2 ;;
    3) layer_3 ;;
    4) layer_4 ;;
    5) layer_5 ;;
    6) layer_6 ;;
  esac
done

BLOCKING_FAILURES=0
for n in $SELECTED_LAYERS; do
  if [ "$(layer_status "$n")" = "fail" ] && [ "$(layer_blocking "$n")" = "1" ]; then
    BLOCKING_FAILURES=$((BLOCKING_FAILURES + 1))
  fi
done

TOTAL_TIME=$(( $(date +%s) - START_EPOCH ))
if [ "$BLOCKING_FAILURES" -eq 0 ]; then
  CONCLUSION="通过"
else
  CONCLUSION="未通过（$BLOCKING_FAILURES 个阻断层失败）"
fi
write_report "$CONCLUSION" "$BLOCKING_FAILURES" "$TOTAL_TIME"
print_summary "$BLOCKING_FAILURES"

if [ "$BLOCKING_FAILURES" -gt 0 ]; then
  exit 1
fi
exit 0
