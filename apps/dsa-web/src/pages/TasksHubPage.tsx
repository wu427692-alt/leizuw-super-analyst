import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { AudioLines, Boxes, ChevronRight, DatabaseZap, FlaskConical, RefreshCw, Search } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { essayQuantApi } from '../api/essayQuant';
import { essayRadarApi } from '../api/essayRadar';
import { essayExportsApi } from '../api/essayExports';
import type { EssayExportStatus, EssayExportTask } from '../api/essayExports';
import { industryResearchApi } from '../api/industryResearch';
import { AppPage, BarMeter, MetricRow, PageTabs, ShareBar, Sparkline } from '../components/common';
import { SegmentedChips } from '../components/mobile/SegmentedChips';
import { useIsMobile, useIsPhone } from '../hooks/useIsMobile';
import { useUserAccess } from '../contexts/UserAccessContext';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import './TasksHubPage.css';

type TaskState = 'queued' | 'running' | 'completed' | 'limited' | 'failed' | 'no_results';
type TaskKind = 'research' | 'quant' | 'audio-analysis' | 'audio-package' | 'acquisition' | 'essay-export';
type TaskTab = 'active' | 'completed' | 'failed' | 'scope';
type UnifiedTask = {
  id: string;
  kind: TaskKind;
  title: string;
  detail: string;
  status: TaskState;
  progress: number | null;
  stageLabel?: string;
  updatedAt: string;
  href: string;
};

const KIND_META: Record<TaskKind, { label: string; icon: typeof Search }> = {
  research: { label: '行业与公司调研', icon: Search },
  quant: { label: '量化研究', icon: FlaskConical },
  'audio-analysis': { label: '录音转写与纪要', icon: AudioLines },
  'audio-package': { label: '录音文件打包', icon: Boxes },
  acquisition: { label: '一站式取数', icon: DatabaseZap },
  'essay-export': { label: '小作文导出', icon: Boxes },
};

const ACTIVE_STATES = new Set<TaskState>(['queued', 'running']);
const FINISHED_STATES = new Set<TaskState>(['completed', 'limited', 'no_results']);
const TASK_KINDS = Object.keys(KIND_META) as TaskKind[];
const PAGE_SIZE = 20;
const RESEARCH_PAGE_SIZE = 30;
const CHANNEL_LINKS: Record<TaskKind, string> = {
  research: '/industry-research', quant: '/essay-quant?section=history',
  'audio-analysis': '/essay-radar/feed?tab=audio', 'audio-package': '/essay-radar/feed?tab=audio',
  acquisition: '/data-acquisition',
  'essay-export': '/meeting',
};
type ChannelResult = { items: UnifiedTask[]; total: number };
type ChannelState = { items: UnifiedTask[] | null; total: number | null; loading: boolean; error: 'unavailable' | 'permission' | 'incompatible' | null };
const initialChannels = () => Object.fromEntries(TASK_KINDS.map(kind => [kind, {
  items: null, total: null, loading: false, error: null,
}])) as Record<TaskKind, ChannelState>;

const EXPORT_STAGE_LABELS: Record<EssayExportStatus, string> = {
  queued: '排队中', indexing: '统计总量', collecting: '收集原文', packaging: '生成数据包',
  retrying: '等待续跑', completed: '已完成', failed: '需要重试', no_results: '无匹配结果',
};
const EXPORT_TASK_STATES: Record<EssayExportStatus, TaskState> = {
  queued: 'queued', indexing: 'running', collecting: 'running', packaging: 'running',
  retrying: 'running', completed: 'completed', failed: 'failed', no_results: 'no_results',
};

function countLabel(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '待确认';
}

function byteLabel(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '待确认';
  if (value < 1024) return `${value.toLocaleString('zh-CN')} B`;
  const power = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** power).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${['B', 'KB', 'MB', 'GB'][power]}`;
}

function exportDetail(task: EssayExportTask) {
  const scope = task.requestedLimit == null ? '全量导出，不设篇数上限' : `用户指定最多 ${countLabel(task.requestedLimit)} 篇`;
  return [task.message, scope, `匹配 ${countLabel(task.matchedTotal)} 篇`,
    `已收集 ${countLabel(task.retrievedCount)} / ${countLabel(task.targetTotal)} 篇`,
    `已打包 ${countLabel(task.packagedCount)} 篇`,
    `已处理原始字节 ${byteLabel(task.bytesWritten)} / ${byteLabel(task.bytesTotal)}`].filter(Boolean).join(' · ');
}

async function readChannel(kind: TaskKind, researchCount = RESEARCH_PAGE_SIZE, researchOffset = 0): Promise<ChannelResult> {
  switch (kind) {
    case 'research': {
      // Only this list API supports history offsets. Refresh all already-loaded
      // research pages so older running projects cannot remain stale forever.
      const pages = await Promise.all(Array.from({ length: Math.ceil(researchCount / RESEARCH_PAGE_SIZE) }, (_, index) => industryResearchApi.projects({
        limit: RESEARCH_PAGE_SIZE, offset: researchOffset + index * RESEARCH_PAGE_SIZE, force: true,
      })));
      return { total: pages[0].total, items: pages.flatMap(page => page.items).map(task => ({
      id: task.projectId, kind, title: task.topic, detail: task.message || task.stage,
      status: task.status === 'collecting' || task.status === 'analyzing' ? 'running' : task.status,
      progress: task.progress, updatedAt: task.updatedAt,
      href: `/industry-research?project=${encodeURIComponent(task.projectId)}`,
      })) };
    }
    case 'quant': {
      const result = await essayQuantApi.tasks();
      return { total: result.total, items: result.items.map(task => ({
      id: task.taskId, kind, title: task.name || '量化研究任务',
      detail: task.message || task.strategyType, status: task.status, progress: task.progress,
      updatedAt: task.completedAt || task.startedAt || task.createdAt || '',
      href: task.status === 'completed' && task.resultRunId
        ? `/essay-quant?section=results&run=${encodeURIComponent(task.resultRunId)}`
        : `/essay-quant?section=history&task=${encodeURIComponent(task.taskId)}`,
      })) };
    }
    case 'audio-analysis': {
      const result = await essayRadarApi.audioAnalysisTasks();
      return { total: result.total, items: result.items.map(task => ({
      id: task.taskId, kind, title: task.title || `录音纪要 · ${task.totalFiles} 个文件`,
      detail: task.message, status: task.status, progress: task.progress,
      updatedAt: task.updatedAt, href: `/essay-radar/feed?tab=audio&task=${encodeURIComponent(task.taskId)}`,
      })) };
    }
    case 'audio-package': {
      const result = await essayRadarApi.audioBatchTasks();
      return { total: result.total, items: result.items.map(task => ({
      id: task.taskId, kind, title: `录音打包 · ${task.totalFiles} 个文件`,
      detail: task.message, status: task.status, progress: task.progress,
      updatedAt: task.updatedAt, href: `/essay-radar/feed?tab=audio&package=${encodeURIComponent(task.taskId)}`,
      })) };
    }
    case 'acquisition': {
      const result = await dataAcquisitionApi.tasks();
      return { total: result.total, items: result.items.map(task => ({
      id: task.taskId, kind, title: task.result?.title || task.tasks?.map(item => item.label).slice(0, 2).join('、') || '数据获取任务',
      detail: task.message, status: task.status, progress: task.progress,
      updatedAt: task.updatedAt, href: `/data-acquisition?task=${encodeURIComponent(task.taskId)}`,
      })) };
    }
    case 'essay-export': {
      const result = await essayExportsApi.tasks();
      return { total: result.total, items: result.items.map(task => ({
        id: task.taskId, kind, title: task.title || (task.filters?.query ? `${task.filters.query} · 小作文导出` : '小作文导出任务'),
        detail: exportDetail(task),
        status: EXPORT_TASK_STATES[task.status],
        stageLabel: EXPORT_STAGE_LABELS[task.status],
        progress: typeof task.progress === 'number' && Number.isFinite(task.progress) ? task.progress : null,
        updatedAt: task.updatedAt || task.createdAt || '',
        href: `/meeting?export_task=${encodeURIComponent(task.taskId)}`,
      })) };
    }
  }
}

function safeDate(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || '时间未记录' : parsed.toLocaleString('zh-CN', { hour12: false });
}

function statusLabel(status: TaskState) {
  return ({ queued: '排队中', running: '执行中', completed: '已完成', limited: '部分完成', failed: '失败', no_results: '无匹配结果' })[status];
}

const TasksHubPage = () => {
  const { user, loggedIn, isLoading, authMethod, accessEnabled } = useUserAccess();
  // 单机模式（未启用访问控制）没有登录用户，但任务仍属于本机，不能因此挡住页面。
  const owner = isLoading ? null : !accessEnabled ? 'local'
    : loggedIn && user?.status === 'approved' && user.id > 0 ? `user:${user.id}` : null;
  if (!owner) {
    return <AppPage><p role="status">{isLoading ? '正在确认访问权限…' : '请登录已获准的账号查看个人任务。'}</p></AppPage>;
  }
  // Remount on identity/permission changes: never reuse another user's snapshots
  // or accept requests that were started under a previous authorization scope.
  return <TasksHubContent key={`${owner}:${authMethod}:${accessEnabled}`} />;
};

function TasksHubContent() {
  const isMobile = useIsMobile();
  const isPhone = useIsPhone();
  const navigate = useNavigate();
  useEffect(() => { document.title = '任务与验证 - 乐子乌智能投研平台'; }, []);
  const [channels, setChannels] = useState(initialChannels);
  const channelsRef = useRef(channels);
  useEffect(() => { channelsRef.current = channels; }, [channels]);
  const alive = useRef(false);
  const inFlight = useRef<Partial<Record<TaskKind, Promise<void>>>>({});
  const lastAttempt = useRef<Partial<Record<TaskKind, number>>>({});
  const [tab, setTab] = useState<TaskTab>('active');
  const [kindFilter, setKindFilter] = useState<TaskKind | 'all'>('all');
  const [page, setPage] = useState(1);
  const [mobileTab, setMobileTab] = useState<'active' | 'completed'>('completed');
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const loadChannel = useCallback((kind: TaskKind, appendResearch = false): Promise<void> => {
    if (inFlight.current[kind]) return inFlight.current[kind];
    const previousCount = channelsRef.current[kind].items?.length ?? 0;
    lastAttempt.current[kind] = Date.now();
    setChannels(current => ({ ...current, [kind]: { ...current[kind], loading: true } }));
    const request = readChannel(kind, appendResearch ? RESEARCH_PAGE_SIZE : Math.max(RESEARCH_PAGE_SIZE, previousCount), appendResearch ? previousCount : 0).then(result => {
      if (alive.current) setChannels(current => {
        const merged = appendResearch ? [...(current[kind].items ?? []), ...result.items] : result.items;
        // Offset pages can overlap when a new project arrives during pagination.
        const items = [...new Map(merged.map(item => [item.id, item])).values()];
        return { ...current, [kind]: { items, total: result.total, loading: false, error: null } };
      });
    }).catch(error => {
      if (!alive.current) return;
      const status = isAxiosError(error) ? error.response?.status : undefined;
      const denied = status === 401 || status === 403;
      const incompatible = status === 400 || status === 404 || status === 422;
      setChannels(current => ({ ...current, [kind]: {
        ...current[kind], items: denied ? null : current[kind].items, total: denied ? null : current[kind].total,
        loading: false, error: denied ? 'permission' : incompatible ? 'incompatible' : 'unavailable',
      } }));
    }).finally(() => { delete inFlight.current[kind]; });
    inFlight.current[kind] = request;
    return request;
  }, []);
  const load = useCallback(async () => { await Promise.all(TASK_KINDS.map(kind => loadChannel(kind))); }, [loadChannel]);
  usePageActivationRefresh(load, { minIntervalMs: 2_000 });
  const needsPolling = TASK_KINDS.some(kind => channels[kind].error === 'unavailable'
    || (channels[kind].error === null && channels[kind].items?.some(item => ACTIVE_STATES.has(item.status))));
  useEffect(() => {
    if (!needsPolling) return undefined;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      TASK_KINDS.forEach(kind => {
        const channel = channelsRef.current[kind];
        // Missing or incompatible endpoints cannot recover by polling. Keep
        // the last snapshot and allow a manual retry or page reactivation.
        if (channel.error === 'permission' || channel.error === 'incompatible') return;
        const interval = channel.error === 'unavailable' ? 30_000 : 10_000;
        const due = Date.now() - (lastAttempt.current[kind] ?? 0) >= interval;
        if (due && (channel.error === 'unavailable' || channel.items?.some(item => ACTIVE_STATES.has(item.status)))) void loadChannel(kind);
      });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [loadChannel, needsPolling]);
  const items = useMemo(() => TASK_KINDS.flatMap(kind => channels[kind].items ?? [])
    .sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0)), [channels]);
  const loading = TASK_KINDS.some(kind => channels[kind].loading);
  const failures = TASK_KINDS.filter(kind => channels[kind].error);
  const incompatibleCount = failures.filter(kind => channels[kind].error === 'incompatible').length;

  const counts = useMemo(() => ({
    active: items.filter((item) => ACTIVE_STATES.has(item.status)).length,
    completed: items.filter((item) => FINISHED_STATES.has(item.status)).length,
    failed: items.filter((item) => item.status === 'failed').length,
  }), [items]);
  const filtered = useMemo(() => {
    const byStatus = (item: UnifiedTask) => {
      if (tab === 'active') return ACTIVE_STATES.has(item.status);
      if (tab === 'completed') return FINISHED_STATES.has(item.status);
      if (tab === 'failed') return item.status === 'failed';
      return true;
    };
    return items.filter((item) => (kindFilter === 'all' || kindFilter === item.kind) && byStatus(item));
  }, [items, kindFilter, tab]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const missingHistory = TASK_KINDS.some(kind => (channels[kind].total ?? 0) > (channels[kind].items?.length ?? 0));
  // 近 7 天任务量：用已读取任务的更新时间做密度，补一条不占版面的时序线。
  const dailyCounts = useMemo(() => {
    const dayKey = (value: number) => new Date(value).toISOString().slice(0, 10);
    const buckets = new Map<string, number>();
    items.forEach((item) => {
      const parsed = Date.parse(item.updatedAt);
      if (Number.isNaN(parsed)) return;
      buckets.set(dayKey(parsed), (buckets.get(dayKey(parsed)) ?? 0) + 1);
    });
    return Array.from({ length: 7 }, (_, index) => {
      const day = new Date();
      day.setDate(day.getDate() - (6 - index));
      return buckets.get(dayKey(day.getTime())) ?? 0;
    });
  }, [items]);
  const kindComposition = useMemo(() => TASK_KINDS
    .map(kind => ({ key: kind, value: items.filter(item => item.kind === kind).length, label: KIND_META[kind].label }))
    .filter(segment => segment.value > 0), [items]);
  const kindMaxRead = Math.max(1, ...TASK_KINDS.map(kind => channels[kind].items?.length ?? 0));
  const readAll = items.length;
  const readTotal = TASK_KINDS.reduce((sum, kind) => sum + (channels[kind].total ?? 0), 0);
  const switchTab = (next: string) => { setTab(next as TaskTab); setPage(1); };

  // ---------- 手机端：研究记录（乐小研 09 版式）----------
  if (isPhone) {
    const activeTasks = items.filter((item) => ACTIVE_STATES.has(item.status));
    const doneTasks = items.filter((item) => FINISHED_STATES.has(item.status));
    const listed = mobileTab === 'active' ? activeTasks : doneTasks;
    return <AppPage className="tasks-hub is-mobile lx-rec">
      <SegmentedChips
        ariaLabel="按状态切换"
        value={mobileTab}
        onChange={(value) => setMobileTab(value as 'active' | 'completed')}
        items={[
          { value: 'active' as const, label: `进行中 ${counts.active}` },
          { value: 'completed' as const, label: `已完成 ${counts.completed}` },
        ]}
        className="lx-rec__seg"
      />

      {activeTasks.length ? (
        <button type="button" className="lx-rec__banner" onClick={() => setMobileTab('active')}>
          <span className="lx-rec__dot" aria-hidden="true" />
          <span>{activeTasks.length} 项任务进行中</span>
          <span className="lx-rec__banner-action">查看</span>
          <ChevronRight aria-hidden="true" />
        </button>
      ) : null}

      <section className="lx-section" aria-label="任务记录">
        <header className="lx-section__head">
          <h2>{mobileTab === 'active' ? '进行中' : '已完成'}</h2>
          <div className="lx-section__meta"><button type="button" onClick={() => void load()} disabled={loading}>{loading ? '刷新中' : '刷新'}</button></div>
        </header>
        <div className="lx-rec__list">
          {listed.length ? listed.map((task) => {
            const meta = KIND_META[task.kind]; const Icon = meta.icon;
            // 已完成的研究任务直接进报告页；其他任务仍回到各自模块。
            const target = task.kind === 'research' && task.status === 'completed' ? `/report/${task.id}` : task.href;
            return <button key={`${task.kind}-${task.id}`} type="button" className="lx-rec__row" onClick={() => navigate(target)}>
              <span className="lx-rec__icon"><Icon aria-hidden="true" /></span>
              <span className="lx-rec__main">
                <strong>{task.title}</strong>
                <small>{meta.label} · {safeDate(task.updatedAt)}</small>
              </span>
              <span className={`lx-rec__state is-${task.status}`}>{task.stageLabel || statusLabel(task.status)}</span>
              <ChevronRight aria-hidden="true" className="lx-rec__chev" />
            </button>;
          }) : <p className="lx-list__empty">{loading ? '正在汇总任务' : mobileTab === 'active' ? '暂无进行中的任务' : '暂无记录'}</p>}
        </div>
      </section>

      {failures.length > 0 ? <button type="button" className="lx-rec__warn" onClick={() => void load()}>
        {failures.length} 类任务读取异常，点击重试
      </button> : null}

      <button type="button" className="lx-rec__new" onClick={() => navigate('/industry-research')}>新建研究</button>
    </AppPage>;
  }

  const tabLabel = ({ active: '进行中', completed: '已完成', failed: '需要处理', scope: '读取范围' } as const)[tab];
  return <AppPage className={`tasks-hub max-w-none${isMobile ? ' is-mobile' : ''}`}>
    <header className="mb-[var(--gap-md)] flex flex-wrap items-end justify-between gap-[var(--gap-sm)]">
      <div>
        <h1 className="text-[length:var(--fs-h1)] font-semibold leading-[var(--lh-h1)] text-[color:var(--text-1)]">任务与验证</h1>
        <p className="mt-1 text-[length:var(--fs-sm)] leading-[var(--lh-sm)] text-[color:var(--text-3)]">查看自己的研究、转写、取数和小作文导出进度，点击任务继续处理或查看结果。</p>
      </div>
      <div className="flex items-center gap-[var(--gap-xs)]">
        <button type="button" className="tasks-hub__refresh" onClick={() => void load()}><RefreshCw aria-hidden="true" />刷新状态</button>
        <button type="button" className="btn-primary" onClick={() => navigate('/industry-research')}>新建调研</button>
      </div>
    </header>

    <MetricRow
      className="mb-[var(--gap-md)]"
      columns={4}
      items={[
        { label: '正在处理', value: counts.active, hint: '排队与执行中' },
        { label: '已结束', value: counts.completed, hint: '完成 / 部分完成 / 无结果' },
        { label: '需要处理', value: counts.failed, trend: counts.failed > 0 ? 'down' : 'flat', hint: '失败任务' },
        { label: '已读取任务', value: items.length, hint: missingHistory ? '历史任务未读取完' : '已全部读取' },
      ]}
    />

    <PageTabs
      ariaLabel="任务视图切换"
      value={tab}
      onChange={switchTab}
      items={[
        { key: 'active', label: '进行中', badge: counts.active },
        { key: 'completed', label: '已完成', badge: counts.completed },
        { key: 'failed', label: '需要处理', badge: counts.failed },
        { key: 'scope', label: '读取范围' },
      ]}
    />

    {failures.length > 0 ? <details className="tasks-hub__notice">
      <summary><span role="status">{failures.length} 类任务读取异常{incompatibleCount ? ` · ${incompatibleCount} 类接口版本不兼容` : ''}，已读取任务仍可查看。</span><span>异常详情</span></summary>
      <div className="tasks-hub__notice-channels">{failures.map(kind => <div key={kind}>
        <span>{KIND_META[kind].label}{channels[kind].error === 'permission'
          ? '访问权限已失效，已隐藏该类任务；请确认登录权限。'
          : channels[kind].error === 'incompatible'
            ? '接口版本不兼容，已停止自动重试；请确认后端版本，或手动重试。'
            : channels[kind].items !== null
              ? '暂时未更新，保留最近成功读取的任务状态；页面每 30 秒重试。'
              : '暂时无法读取，其他渠道不受影响；页面每 30 秒重试。'}</span>
        <button type="button" disabled={channels[kind].loading} onClick={() => void loadChannel(kind)}>{channels[kind].loading ? '重试中…' : `重试${KIND_META[kind].label}`}</button>
      </div>)}</div>
    </details> : null}

    {tab === 'scope' ? (
      <div className="flex flex-col gap-[var(--gap-md)]">
        <section className="border border-[color:var(--line-1)] bg-[var(--bg-1)] p-[var(--gap-md)]" aria-label="任务构成">
          <div className="flex flex-wrap items-center justify-between gap-[var(--gap-sm)]">
            <h2 className="text-[length:var(--fs-h2)] font-semibold text-[color:var(--text-1)]">已读取任务的类型构成</h2>
            <span className="text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">共 {readAll} 条 · 各渠道合计 {readTotal} 条</span>
          </div>
          <ShareBar
            className="mt-[var(--gap-sm)]"
            ariaLabel="任务类型构成"
            segments={kindComposition.map(segment => ({ key: segment.key, value: segment.value, label: segment.label }))}
          />
          <p className="mt-[var(--gap-xs)] flex flex-wrap gap-x-[var(--gap-md)] gap-y-[var(--gap-2xs)] text-[length:var(--fs-caption)] text-[color:var(--text-3)]">
            {kindComposition.length
              ? kindComposition.map(segment => <span key={segment.key}>{segment.label} <b className="tabular-nums text-[color:var(--text-2)]">{segment.value}</b></span>)
              : <span>尚未读取到任务</span>}
          </p>
        </section>

        <section className="border border-[color:var(--line-1)] bg-[var(--bg-1)] p-[var(--gap-md)]" aria-label="读取范围与历史任务">
          <div className="flex flex-wrap items-center justify-between gap-[var(--gap-sm)]">
            <h2 className="text-[length:var(--fs-h2)] font-semibold text-[color:var(--text-1)]">读取范围与历史任务</h2>
            <span className="text-[length:var(--fs-caption)] text-[color:var(--text-3)]">调研支持继续读取历史；其他类型接口仅返回最近记录</span>
          </div>
          <div className="mt-[var(--gap-sm)] flex flex-col">
            {TASK_KINDS.map(kind => (
              <div key={kind} className="grid grid-cols-[minmax(96px,0.6fr)_minmax(0,1fr)_120px_auto] items-center gap-[var(--gap-sm)] border-t border-[color:var(--line-1)] py-[var(--gap-xs)] first:border-t-0">
                <span className="truncate text-[length:var(--fs-sm)] text-[color:var(--text-2)]">{KIND_META[kind].label}</span>
                <BarMeter
                  value={channels[kind].items?.length ?? 0}
                  max={kindMaxRead}
                  log
                  height={4}
                  tone="neutral"
                  ariaLabel={`${KIND_META[kind].label} 已读取 ${channels[kind].items?.length ?? 0} 条`}
                />
                <span className="text-right text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{channels[kind].items === null ? '尚未读取' : `已读取 ${channels[kind].items?.length} / ${channels[kind].total ?? '未知'} 条`}</span>
                <button type="button" className="tasks-hub__more" onClick={() => navigate(CHANNEL_LINKS[kind])}>{kind === 'research' ? '查看全部调研' : '打开任务模块'}</button>
              </div>
            ))}
          </div>
          {(channels.research.total ?? 0) > (channels.research.items?.length ?? 0) ? (
            <button type="button" className="tasks-hub__more mt-[var(--gap-sm)]" disabled={channels.research.loading || channels.research.error === 'permission'} onClick={() => void loadChannel('research', true)}>{channels.research.loading ? '正在读取历史调研…' : '再读取 30 条历史调研'}</button>
          ) : null}
        </section>

        {failures.length > 0 ? <p className="text-[length:var(--fs-caption)] text-[color:var(--text-3)]">{failures.length} 类任务读取异常，各渠道互不影响。</p> : null}
      </div>
    ) : (
      <>
        <div className="tasks-hub__tools">
          <label>任务类型<select value={kindFilter} onChange={event => { setKindFilter(event.target.value as TaskKind | 'all'); setPage(1); }}><option value="all">所有类型</option>{TASK_KINDS.map(kind => <option key={kind} value={kind}>{KIND_META[kind].label}</option>)}</select></label>
          <span className="flex items-center gap-[var(--gap-sm)]">
            <span>筛选命中 {filtered.length} 条已读取任务{missingHistory ? ' · 尚有历史任务未列入' : ''}</span>
            <span className="flex items-center gap-[var(--gap-2xs)]">{tabLabel} · 近 7 日任务量<Sparkline values={dailyCounts} width={72} height={16} ariaLabel="近 7 日任务量" /></span>
          </span>
        </div>
        <section className="tasks-hub__list" aria-busy={loading} aria-label="任务记录">
          {visible.map((task) => {
            const meta = KIND_META[task.kind]; const Icon = meta.icon;
            return <button key={`${task.kind}-${task.id}`} className="tasks-hub__task" onClick={() => navigate(task.href)}>
              <span className="tasks-hub__task-icon"><Icon aria-hidden="true" /></span>
              <span className="tasks-hub__task-main"><span className="tasks-hub__task-meta">{meta.label} · {safeDate(task.updatedAt)}{channels[task.kind].error ? ' · 上次读取状态' : ''}</span><strong>{task.title}</strong><span>{task.detail}</span></span>
              <span className={`tasks-hub__status is-${task.status}`}>{task.stageLabel || statusLabel(task.status)}</span>
              {task.progress == null ? <span className="tasks-hub__progress" aria-label="进度尚未确认"><em>{ACTIVE_STATES.has(task.status) ? '总量确认中' : '未计算'}</em></span>
                : <span className="tasks-hub__progress" aria-label={`进度 ${task.progress}%`}>
                  <span className="absolute inset-x-0 bottom-0">
                    <BarMeter value={Math.max(0, Math.min(task.progress, 100))} max={100} height={4} tone={task.progress >= 100 ? 'accent' : 'auto'} ariaLabel={`任务进度条 ${task.progress}%`} />
                  </span>
                  <em>{task.progress}%</em>
                </span>}
            </button>;
          })}
          {!loading && !visible.length ? <div className="tasks-hub__empty"><Boxes /><strong>{failures.length ? '部分渠道尚未读取成功' : '当前筛选下没有任务'}</strong><span>{failures.length ? '点上方重试' : '可以从上方入口发起调研、量化、转写或取数任务。'}</span></div> : null}
          {loading && !items.length ? <div className="tasks-hub__empty"><RefreshCw className="animate-spin" /><strong>正在汇总任务</strong></div> : null}
        </section>
        {filtered.length > PAGE_SIZE ? <nav className="tasks-hub__pages" aria-label="任务列表分页"><span>第 {currentPage} / {totalPages} 页 · 本页 {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} 条</span><button disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button><button disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}>下一页</button></nav> : null}
      </>
    )}
  </AppPage>;
}

export default TasksHubPage;
