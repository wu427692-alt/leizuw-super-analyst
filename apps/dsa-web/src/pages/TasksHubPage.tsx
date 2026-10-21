import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Activity, AudioLines, Boxes, CheckCircle2, Clock3, DatabaseZap, FlaskConical, RefreshCw, Search, XCircle } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { essayQuantApi } from '../api/essayQuant';
import { essayRadarApi } from '../api/essayRadar';
import { industryResearchApi } from '../api/industryResearch';
import { AppPage } from '../components/common';
import { useUserAccess } from '../contexts/UserAccessContext';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import './TasksHubPage.css';

type TaskState = 'queued' | 'running' | 'completed' | 'limited' | 'failed';
type TaskKind = 'research' | 'quant' | 'audio-analysis' | 'audio-package' | 'acquisition';
type UnifiedTask = {
  id: string;
  kind: TaskKind;
  title: string;
  detail: string;
  status: TaskState;
  progress: number;
  updatedAt: string;
  href: string;
};

const KIND_META: Record<TaskKind, { label: string; icon: typeof Search }> = {
  research: { label: '行业与公司调研', icon: Search },
  quant: { label: '量化研究', icon: FlaskConical },
  'audio-analysis': { label: '录音转写与纪要', icon: AudioLines },
  'audio-package': { label: '录音文件打包', icon: Boxes },
  acquisition: { label: '一站式取数', icon: DatabaseZap },
};

const ACTIVE_STATES = new Set<TaskState>(['queued', 'running']);
const TASK_KINDS = Object.keys(KIND_META) as TaskKind[];
const PAGE_SIZE = 20;
const RESEARCH_PAGE_SIZE = 30;
const CHANNEL_LINKS: Record<TaskKind, string> = {
  research: '/industry-research', quant: '/essay-quant?section=history',
  'audio-analysis': '/essay-radar/feed?tab=audio', 'audio-package': '/essay-radar/feed?tab=audio',
  acquisition: '/data-acquisition',
};
type ChannelResult = { items: UnifiedTask[]; total: number };
type ChannelState = { items: UnifiedTask[] | null; total: number | null; loading: boolean; error: 'unavailable' | 'permission' | 'incompatible' | null };
const initialChannels = () => Object.fromEntries(TASK_KINDS.map(kind => [kind, {
  items: null, total: null, loading: false, error: null,
}])) as Record<TaskKind, ChannelState>;

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
  }
}

function safeDate(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value || '时间未记录' : parsed.toLocaleString('zh-CN', { hour12: false });
}

function statusLabel(status: TaskState) {
  return ({ queued: '排队中', running: '执行中', completed: '已完成', limited: '部分完成', failed: '失败' })[status];
}

const TasksHubPage = () => {
  const { user, loggedIn, isLoading, authMethod, accessEnabled } = useUserAccess();
  if (isLoading || !loggedIn || !user || user.status !== 'approved') {
    return <AppPage><p role="status">{isLoading ? '正在确认访问权限…' : '请登录已获准的账号查看个人任务。'}</p></AppPage>;
  }
  // Remount on identity/permission changes: never reuse another user's snapshots
  // or accept requests that were started under a previous authorization scope.
  return <TasksHubContent key={`${user.id}:${user.status}:${authMethod}:${accessEnabled}`} />;
};

function TasksHubContent() {
  const navigate = useNavigate();
  useEffect(() => { document.title = '任务与验证 - 乐子乌智能投研平台'; }, []);
  const [channels, setChannels] = useState(initialChannels);
  const channelsRef = useRef(channels);
  useEffect(() => { channelsRef.current = channels; }, [channels]);
  const alive = useRef(false);
  const inFlight = useRef<Partial<Record<TaskKind, Promise<void>>>>({});
  const lastAttempt = useRef<Partial<Record<TaskKind, number>>>({});
  const [filter, setFilter] = useState<'all' | 'active' | 'completed' | 'failed'>('all');
  const [kindFilter, setKindFilter] = useState<TaskKind | 'all'>('all');
  const [page, setPage] = useState(1);
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
    completed: items.filter((item) => item.status === 'completed' || item.status === 'limited').length,
    failed: items.filter((item) => item.status === 'failed').length,
  }), [items]);
  const filtered = useMemo(() => items.filter((item) => (kindFilter === 'all' || kindFilter === item.kind) && (
    filter === 'all' || (filter === 'active' && ACTIVE_STATES.has(item.status))
    || (filter === 'completed' && (item.status === 'completed' || item.status === 'limited'))
    || (filter === 'failed' && item.status === 'failed')
  )), [filter, items, kindFilter]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const missingHistory = TASK_KINDS.some(kind => (channels[kind].total ?? 0) > (channels[kind].items?.length ?? 0));
  const selectFilter = (value: typeof filter) => { setFilter(value); setPage(1); };

  return <AppPage className="tasks-hub max-w-none">
    <header className="tasks-hub__header">
      <div><h1>任务与验证</h1><p>查看自己的研究、转写、回测和取数进度，完成后点击任务查看结果。</p></div>
      <button type="button" className="tasks-hub__refresh" onClick={() => void load()}><RefreshCw aria-hidden="true" />刷新状态</button>
    </header>

    <section className="tasks-hub__summary" aria-label="任务摘要">
      <button onClick={() => selectFilter('active')} aria-pressed={filter === 'active'} className={filter === 'active' ? 'is-active' : ''}><Activity aria-hidden="true" /><span>正在处理</span><strong>{counts.active}</strong></button>
      <button onClick={() => selectFilter('completed')} aria-pressed={filter === 'completed'} className={filter === 'completed' ? 'is-active' : ''}><CheckCircle2 aria-hidden="true" /><span>已完成</span><strong>{counts.completed}</strong></button>
      <button onClick={() => selectFilter('failed')} aria-pressed={filter === 'failed'} className={filter === 'failed' ? 'is-active' : ''}><XCircle aria-hidden="true" /><span>需要处理</span><strong>{counts.failed}</strong></button>
      <button onClick={() => selectFilter('all')} aria-pressed={filter === 'all'} className={filter === 'all' ? 'is-active' : ''}><Clock3 aria-hidden="true" /><span>已读取任务</span><strong>{items.length}</strong></button>
    </section>
    <p className="tasks-hub__scope">以上计数仅统计已读取记录{missingHistory ? '，尚有历史任务未列入' : ''}。运行任务每 10 秒更新；空列表与已完成任务在回到页面时更新。</p>

    <nav className="tasks-hub__launch" aria-label="新建任务">
      <button onClick={() => navigate('/industry-research')}>新建调研</button>
      <button onClick={() => navigate('/essay-quant')}>新建量化研究</button>
      <button onClick={() => navigate('/essay-radar/feed?tab=audio')}>转写录音</button>
      <button onClick={() => navigate('/data-acquisition')}>获取数据</button>
    </nav>

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

    <div className="tasks-hub__tools"><label>任务类型<select value={kindFilter} onChange={event => { setKindFilter(event.target.value as TaskKind | 'all'); setPage(1); }}><option value="all">所有类型</option>{TASK_KINDS.map(kind => <option key={kind} value={kind}>{KIND_META[kind].label}</option>)}</select></label><span>筛选命中 {filtered.length} 条已读取任务</span></div>
    <section className="tasks-hub__list" aria-busy={loading} aria-label="任务记录">
      {visible.map((task) => {
        const meta = KIND_META[task.kind]; const Icon = meta.icon;
        return <button key={`${task.kind}-${task.id}`} className="tasks-hub__task" onClick={() => navigate(task.href)}>
          <span className="tasks-hub__task-icon"><Icon aria-hidden="true" /></span>
          <span className="tasks-hub__task-main"><span className="tasks-hub__task-meta">{meta.label} · {safeDate(task.updatedAt)}{channels[task.kind].error ? ' · 上次读取状态' : ''}</span><strong>{task.title}</strong><span>{task.detail}</span></span>
          <span className={`tasks-hub__status is-${task.status}`}>{statusLabel(task.status)}</span>
          <span className="tasks-hub__progress" aria-label={`进度 ${task.progress}%`}><i style={{ width: `${Math.max(0, Math.min(task.progress, 100))}%` }} /><em>{task.progress}%</em></span>
        </button>;
      })}
      {!loading && !visible.length ? <div className="tasks-hub__empty"><Boxes /><strong>{failures.length ? '部分渠道尚未读取成功' : '当前筛选下没有任务'}</strong><span>{failures.length ? '可重试上方未返回的渠道，未读取成功不代表没有任务。' : '可以从上方入口发起调研、量化、转写或取数任务。'}</span></div> : null}
      {loading && !items.length ? <div className="tasks-hub__empty"><RefreshCw className="animate-spin" /><strong>正在汇总任务</strong><span>五类后台任务并行读取，已返回的任务会先显示。</span></div> : null}
    </section>
    {filtered.length > PAGE_SIZE ? <nav className="tasks-hub__pages" aria-label="任务列表分页"><span>第 {currentPage} / {totalPages} 页 · 本页 {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} 条</span><button disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button><button disabled={currentPage >= totalPages} onClick={() => setPage(currentPage + 1)}>下一页</button></nav> : null}
    <details className="tasks-hub__coverage"><summary>读取范围与历史任务</summary><p>调研支持继续读取历史；其他类型的现有列表接口仅返回最近记录，不支持历史翻页。</p>{TASK_KINDS.map(kind => <div key={kind}><span>{KIND_META[kind].label}</span><span>{channels[kind].items === null ? '尚未读取' : `已读取 ${channels[kind].items?.length} / ${channels[kind].total ?? '未知'} 条`}</span><button type="button" onClick={() => navigate(CHANNEL_LINKS[kind])}>{kind === 'research' ? '查看全部调研' : '打开任务模块'}</button></div>)}{(channels.research.total ?? 0) > (channels.research.items?.length ?? 0) ? <button type="button" className="tasks-hub__more" disabled={channels.research.loading || channels.research.error === 'permission'} onClick={() => void loadChannel('research', true)}>{channels.research.loading ? '正在读取历史调研…' : '再读取 30 条历史调研'}</button> : null}</details>
  </AppPage>;
}

export default TasksHubPage;
