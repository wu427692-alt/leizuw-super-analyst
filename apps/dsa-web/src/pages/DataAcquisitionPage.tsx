import { useCallback, useEffect, useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Bot, CheckCircle2, CircleDashed, DatabaseZap, Download, FileArchive, PackageCheck, Play, RefreshCw, Send, ShieldCheck, XCircle } from 'lucide-react';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { getRequestCacheGeneration } from '../api/requestCache';
import { AppPage, Badge, Card, EmptyState, EvidenceRail, PageHeader, StatCard } from '../components/common';
import ResearchReportLibrary from '../components/data-acquisition/ResearchReportLibrary';
import type { AcquisitionCapabilities, AcquisitionDownloadProgress, AcquisitionJob, AcquisitionPlan, AcquisitionPreflight, AcquisitionRunTask } from '../types/dataAcquisition';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import { useAcquisitionTask } from '../hooks/useAcquisitionTask';
import { useUserAccess } from '../contexts/UserAccessContext';
import { Link, useSearchParams } from 'react-router-dom';

const EXAMPLES = [
  '打包华懋科技和胜宏科技近90天行情、估值、资金、公告、研报、新闻和知识星球小作文，并补充工商与风险信息',
  '获取胜宏科技最近8个季度财务三表、财务指标、机构研报和股东增减持，按数据源分别导出',
  '整理华懋科技本月所有公告、新闻、小作文和机构观点，并下载公告PDF、研报及相关附件一起打包',
  '获取最近30天低空经济相关公告、新闻和知识星球文件，按渠道分别打包并保留原文链接',
];

const SOURCE_LABELS: Record<string, string> = {
  tushare: 'Tushare Pro', zsxq: '知识星球', cninfo: '巨潮资讯', monitor: '其他情报', tianyancha: '天眼查',
};

const PHASE_LABELS: Record<AcquisitionRunTask['phase'], string> = {
  queued: '排队中', starting: '启动任务', validating: '校验范围', fetching: '跨渠道获取',
  exporting: '生成数据文件', packaging: '压缩 ZIP', finalizing: '完成校验', completed: '已完成',
  failed: '执行失败', interrupted: '任务中断',
};
const TASK_STATUS_LABELS = { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败' };
const JOB_STATUS_LABELS = { success: '全部成功', partial: '部分未完成', failed: '取数失败' };
const jobStatusLabel = (job: AcquisitionJob) => job.summary.rowCount === 0 ? '未取得数据' : JOB_STATUS_LABELS[job.status];
const jobTitle = (job: AcquisitionJob) => job.summary.rowCount === 0 ? '未取得符合条件的数据' : job.status === 'partial' ? '取数尚未完整完成' : job.status === 'failed' ? '取数未完成' : '数据包已生成';
const ENTITY_STATUS_LABELS = { resolved: '已核验', ambiguous: '有同名候选', unresolved: '暂未识别', conflict: '名称与代码不一致' };

function PlanPreflight({ preflight, selections, disabled, onSelectionChange }: {
  preflight: AcquisitionPreflight;
  selections: Record<string, string>;
  disabled: boolean;
  onSelectionChange: (query: string, value: string) => void;
}) {
  return <section aria-label="取数目标校验" className="mt-4 space-y-3 rounded-2xl border border-border/70 bg-background/55 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-foreground">取数目标与股票代码</h3>
      <Badge variant={preflight.canExecute ? 'success' : 'warning'}>{preflight.canExecute ? '执行前校验通过' : '请先确认目标'}</Badge>
    </div>
    <p className="text-xs leading-5 text-secondary-text">先核验公司身份，再按各渠道所需代码取数；无法确认时不会扩大到全市场。</p>
    {preflight.entities.map((entity, index) => <div key={`${entity.query}:${index}`} className="rounded-xl border border-border/70 bg-card/60 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium text-foreground">{entity.name || entity.query}</span>
        <Badge variant={entity.status === 'resolved' ? 'success' : 'warning'}>{ENTITY_STATUS_LABELS[entity.status]}</Badge>
      </div>
      <p className="mt-1 break-words text-xs text-secondary-text">需求中的名称：{entity.query}{entity.source ? ` · 核验来源：${entity.source}` : ''}</p>
      {entity.symbol ? <p className="mt-2 font-mono text-sm text-primary">{entity.symbol}</p> : null}
      {entity.status !== 'resolved' ? <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {entity.candidates.length ? <label className="space-y-1 text-xs text-secondary-text">
          <span>确认候选：{entity.query}</span>
          <select aria-label={`确认候选：${entity.query}`} disabled={disabled}
            value={entity.candidates.some(candidate => candidate.symbol === selections[entity.query]) ? selections[entity.query] : ''}
            onChange={event => onSelectionChange(entity.query, event.target.value)}
            className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground">
            <option value="">请选择准确的公司与代码</option>
            {entity.candidates.map(candidate => <option key={candidate.symbol} value={candidate.symbol}>{candidate.name} · {candidate.symbol}</option>)}
          </select>
        </label> : null}
        <label className="space-y-1 text-xs text-secondary-text">
          <span>或补充准确股票代码</span>
          <input aria-label={`股票代码：${entity.query}`} value={selections[entity.query] ?? ''} disabled={disabled} maxLength={40}
            onChange={event => onSelectionChange(entity.query, event.target.value)} placeholder="输入证券代码，重新校验身份"
            className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground" />
        </label>
      </div> : null}
    </div>)}
    {preflight.blockers.length ? <div role="status" className="rounded-xl border border-warning/25 bg-warning/10 p-3 text-sm leading-6 text-warning">
      <p className="font-medium">执行前需要解决</p>
      <ul className="mt-1 list-inside list-disc">{preflight.blockers.map((blocker, index) => <li key={`${blocker}:${index}`}>{blocker}</li>)}</ul>
    </div> : null}
  </section>;
}

function readRememberedTask(key: string) {
  try { return window.localStorage.getItem(key) || ''; } catch { return ''; }
}

function rememberTask(key: string, taskId: string) {
  try { window.localStorage.setItem(key, taskId); } catch { /* URL and server ledger remain usable. */ }
}

function forgetTask(key: string, taskId: string) {
  try { if (window.localStorage.getItem(key) === taskId) window.localStorage.removeItem(key); } catch { /* Storage may be disabled. */ }
}

type TransferMetrics = { bytesPerSecond?: number; estimatedSecondsRemaining?: number };
type DownloadState = AcquisitionDownloadProgress & TransferMetrics & {
  jobId: string;
  status: 'downloading' | 'completed' | 'failed';
};

function triggerBrowserSave(url: string, filename: string) {
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.hidden = true;
  document.body.appendChild(anchor);
  try { anchor.click(); } finally { anchor.remove(); }
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  try { triggerBrowserSave(url, filename); }
  finally { window.setTimeout(() => URL.revokeObjectURL(url), 1000); }
}

function measuredProgress(progress: AcquisitionDownloadProgress, elapsedMs: number): AcquisitionDownloadProgress & TransferMetrics {
  const metrics = progress as AcquisitionDownloadProgress & TransferMetrics;
  const loaded = Number.isFinite(progress.loaded) && progress.loaded >= 0 ? progress.loaded : 0;
  const total = Number.isFinite(progress.total) && progress.total! > 0 ? progress.total : undefined;
  const suppliedSpeed = metrics.bytesPerSecond;
  const bytesPerSecond = Number.isFinite(suppliedSpeed) && suppliedSpeed! >= 0 ? suppliedSpeed
    : elapsedMs >= 100 && loaded > 0 ? loaded / (elapsedMs / 1000) : undefined;
  const remaining = total === undefined ? undefined : Math.max(0, total - loaded);
  const suppliedEta = metrics.estimatedSecondsRemaining;
  const estimatedSecondsRemaining = remaining === undefined ? undefined
    : Number.isFinite(suppliedEta) && suppliedEta! >= 0 ? suppliedEta
      : bytesPerSecond && bytesPerSecond > 0 ? remaining / bytesPerSecond : undefined;
  return { loaded, total,
    percent: Number.isFinite(progress.percent) ? Math.max(0, Math.min(99, progress.percent!)) : undefined,
    bytesPerSecond, estimatedSecondsRemaining };
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatRemaining(seconds: number) {
  const rounded = Math.ceil(seconds);
  if (rounded >= 86400) return '超过1天';
  if (rounded >= 3600) return `${Math.floor(rounded / 3600)}小时${Math.floor(rounded % 3600 / 60)}分`;
  return rounded >= 60 ? `${Math.floor(rounded / 60)}分${rounded % 60}秒` : `${rounded}秒`;
}

function DownloadProgress({ state }: { state: DownloadState }) {
  const label = state.total
    ? `${formatBytes(state.loaded)} / ${formatBytes(state.total)}`
    : `已接收 ${formatBytes(state.loaded)}`;
  return <div className="mt-3" aria-live="polite">
    <div className="flex items-center justify-between gap-3 text-[11px] text-secondary-text">
      <span>{state.status === 'completed' ? '下载完成' : state.status === 'failed' ? '下载中断，可重新下载；后台数据包仍保留' : '正在下载；连接中断将自动重连，已完成分块不会重复下载'}</span>
      <span className="font-mono">{state.percent !== undefined ? `${state.percent}% · ` : ''}{label}</span>
    </div>
    <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-border/70" role="progressbar" aria-label="数据包下载进度"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={state.percent}>
      <div className={`h-full rounded-full ${state.status === 'failed' ? 'bg-danger' : 'bg-primary'} transition-[width] duration-200 ${state.percent === undefined ? `w-1/3 ${state.status === 'downloading' ? 'animate-pulse' : ''}` : ''}`}
        style={state.percent === undefined ? undefined : { width: `${state.percent}%` }} />
    </div>
    {state.status === 'downloading' ? <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-secondary-text" aria-label="下载传输情况">
      <span>{state.bytesPerSecond === undefined ? '速度统计中' : `速度 ${formatBytes(Math.round(state.bytesPerSecond))}/秒`}</span>
      {state.total && state.loaded >= state.total ? <span>正在校验完整文件</span>
        : state.estimatedSecondsRemaining !== undefined && state.estimatedSecondsRemaining > 0
          ? <span>预计剩余 {formatRemaining(state.estimatedSecondsRemaining)}</span> : null}
    </p> : null}
  </div>;
}

const DataAcquisitionPage = () => {
  const { user, loggedIn, isLoading, authMethod, accessEnabled } = useUserAccess();
  if (isLoading || !loggedIn || !user || user.status !== 'approved') {
    return <AppPage><p role="status">{isLoading ? '正在确认访问权限…' : '请登录已获准的账号查看个人取数任务。'}</p></AppPage>;
  }
  return <DataAcquisitionContent key={`${user.id}:${user.status}:${authMethod}:${accessEnabled}`} userId={user.id} />;
};

function DataAcquisitionContent({ userId }: { userId: number }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTaskId = searchParams.get('task')?.trim() ?? '';
  const storageKey = `dsa:data-acquisition:v2:user:${userId}:active-task`;
  const [rememberedTaskId, setRememberedTaskId] = useState(() => readRememberedTask(storageKey));
  const activeTaskId = requestedTaskId || rememberedTaskId;
  const view = searchParams.get('view') || (activeTaskId ? 'tasks' : 'reports');
  const showTasks = view === 'tasks';
  const [reportsOpened, setReportsOpened] = useState(!showTasks);
  const [request, setRequest] = useState(EXAMPLES[0]);
  const [plannedRequest, setPlannedRequest] = useState('');
  const [capabilities, setCapabilities] = useState<AcquisitionCapabilities | null>(null);
  const [plan, setPlan] = useState<AcquisitionPlan | null>(null);
  const [jobs, setJobs] = useState<AcquisitionJob[] | null>(null);
  const [tasks, setTasks] = useState<AcquisitionRunTask[] | null>(null);
  const [viewedJob, setActiveJob] = useState<AcquisitionJob | null>(null);
  const [viewedJobTaskId, setViewedJobTaskId] = useState('');
  useEffect(() => { setActiveJob(null); }, [activeTaskId]);
  const [planning, setPlanning] = useState(false);
  const [validating, setValidating] = useState(false);
  const [entitySelections, setEntitySelections] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [nativeDownloadJobId, setNativeDownloadJobId] = useState('');
  const [downloadProgress, setDownloadProgress] = useState<DownloadState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({});
  const alive = useRef(false);
  const pendingLoads = useRef<Record<string, Promise<void> | undefined>>({});
  const submitting = useRef(false);
  const downloadBusy = useRef(false);
  useEffect(() => {
    alive.current = true;
    document.title = '数据一站式获取 - 乐子乌智能投研平台';
    return () => { alive.current = false; };
  }, []);

  const loadChannel = useCallback((kind: 'sources' | 'jobs' | 'tasks') => {
    if (pendingLoads.current[kind]) return pendingLoads.current[kind];
    const labels = { sources: '渠道信息', jobs: '数据包记录', tasks: '后台任务' };
    const operation = async () => {
      try {
        if (kind === 'sources') {
          const value = await dataAcquisitionApi.capabilities();
          if (alive.current) setCapabilities(value);
        } else if (kind === 'jobs') {
          const value = await dataAcquisitionApi.jobs();
          if (alive.current) setJobs(value.items);
        } else {
          const value = await dataAcquisitionApi.tasks();
          if (alive.current) setTasks(value.items);
        }
        if (alive.current) setLoadErrors(current => ({ ...current, [kind]: '' }));
      } catch (caught) {
        if (!alive.current) return;
        const denied = isAxiosError(caught) && [401, 403].includes(caught.response?.status ?? 0);
        if (denied) {
          if (kind === 'jobs') { setJobs(null); setActiveJob(null); }
          if (kind === 'tasks') setTasks(null);
        }
        setLoadErrors(current => ({ ...current, [kind]: denied ? `${labels[kind]}访问权限已失效。` : `${labels[kind]}暂未更新，已加载的内容保留。` }));
      }
    };
    const promise = operation().finally(() => { delete pendingLoads.current[kind]; });
    pendingLoads.current[kind] = promise;
    return promise;
  }, []);
  const load = useCallback(async () => { await Promise.all([loadChannel('sources'), loadChannel('jobs'), loadChannel('tasks')]); }, [loadChannel]);

  usePageActivationRefresh(load, { enabled: !downloading, intervalMs: 30_000, minIntervalMs: 2_000 });

  const onSnapshot = useCallback((task: AcquisitionRunTask) => {
    setTasks(current => [task, ...(current ?? []).filter(item => item.taskId !== task.taskId)]);
    if (task.status === 'running' || task.status === 'queued') rememberTask(storageKey, task.taskId);
    else forgetTask(storageKey, task.taskId);
    if (task.result) setJobs(current => [task.result!, ...(current ?? []).filter(item => item.jobId !== task.result!.jobId)]);
  }, [storageKey]);
  const tracked = useAcquisitionTask(activeTaskId, onSnapshot);
  useEffect(() => {
    if (tracked.unavailable) forgetTask(storageKey, activeTaskId);
  }, [tracked.unavailable, storageKey, activeTaskId]);
  const runTask = tracked.task;
  const activeJob = (viewedJobTaskId === activeTaskId ? viewedJob : null) ?? runTask?.result ?? null;
  const runActive = running || tracked.loading || runTask?.status === 'queued' || runTask?.status === 'running';
  const planChanged = Boolean(plan && request.trim() !== plannedRequest);
  const selectionsChanged = Boolean(plan && Object.entries(entitySelections).some(([query, symbol]) => symbol !== (plan.scope.entitySelections?.[query] ?? '')));
  const planBlocked = Boolean(plan?.preflight && (!plan.preflight.canExecute || plan.preflight.status !== 'ready' || plan.preflight.checks.some(check => check.status === 'blocked')));
  const selectTask = (taskId: string) => {
    setRememberedTaskId(taskId); setActiveJob(null);
    setSearchParams(current => { const next = new URLSearchParams(current); next.set('task', taskId); next.set('view', 'tasks'); return next; });
  };
  const newTask = () => {
    forgetTask(storageKey, activeTaskId);
    setRememberedTaskId(''); setActiveJob(null); setPlan(null); setError(null); setEntitySelections({});
    setSearchParams(current => {
      const next = new URLSearchParams(current); next.delete('task'); next.set('view', 'tasks'); return next;
    });
  };

  const createPlan = async () => {
    if (request.trim().length < 2 || planning || validating || runActive) return;
    const sourceRequest = request.trim();
    setPlanning(true); setError(null); setPlan(null); setEntitySelections({}); setActiveJob(null);
    try {
      const next = await dataAcquisitionApi.plan(sourceRequest);
      if (alive.current) { setPlan(next); setPlannedRequest(sourceRequest); setEntitySelections(next.scope.entitySelections ?? {}); }
    }
    catch (caught) { if (alive.current) setError(caught instanceof Error ? caught.message : '大模型生成取数计划失败'); }
    finally { if (alive.current) setPlanning(false); }
  };

  const validatePlan = async () => {
    if (!plan || planChanged || planning || validating || runActive) return;
    setValidating(true); setError(null);
    try {
      const next = await dataAcquisitionApi.validatePlan(plannedRequest, {
        ...plan, scope: { ...plan.scope, entitySelections },
      });
      if (alive.current) { setPlan(next); setEntitySelections(next.scope.entitySelections ?? {}); }
    } catch (caught) {
      if (alive.current) setError(caught instanceof Error ? caught.message : '目标身份暂未校验成功，请重试；未提交取数任务。');
    } finally { if (alive.current) setValidating(false); }
  };

  const runPlan = async () => {
    if (!plan || planChanged || planBlocked || selectionsChanged || validating || runActive || submitting.current) return;
    submitting.current = true;
    setRunning(true); setError(null); setActiveJob(null);
    try {
      const task = await dataAcquisitionApi.runAsync(plannedRequest, plan);
      // Save only an opaque ID; never store request text or a result under a shared browser key.
      rememberTask(storageKey, task.taskId);
      if (alive.current) { onSnapshot(task); selectTask(task.taskId); }
    } catch { if (alive.current) setError('提交结果未确认，请先检查后台任务列表，避免重复执行同一需求。'); }
    finally { submitting.current = false; if (alive.current) setRunning(false); }
  };

  const download = async (job: AcquisitionJob) => {
    if (downloadBusy.current) return;
    downloadBusy.current = true;
    setDownloading(job.jobId); setNativeDownloadJobId(''); setError(null);
    setDownloadProgress({ jobId: job.jobId, loaded: 0, status: 'downloading' });
    const startedAt = performance.now();
    const ownerGeneration = getRequestCacheGeneration();
    let lastPaintAt = -Infinity;
    try {
      const blob = await dataAcquisitionApi.download(job.jobId, (progress) => {
        if (!alive.current || ownerGeneration !== getRequestCacheGeneration()) return;
        const now = performance.now();
        if (now - lastPaintAt < 250) return;
        lastPaintAt = now;
        setDownloadProgress({ jobId: job.jobId, status: 'downloading', ...measuredProgress(progress, now - startedAt) });
      });
      if (ownerGeneration !== getRequestCacheGeneration()) throw new Error('登录状态已变化，已停止保存数据包，请在当前账号重新下载');
      // The transfer is authorized and complete even if this SPA route was left.
      // Only React updates depend on the component still being mounted.
      saveBlob(blob, `财经数据包_${job.jobId}.zip`);
      if (alive.current) setDownloadProgress({ jobId: job.jobId, loaded: blob.size, total: blob.size, percent: 100, status: 'completed' });
    }
    catch (caught) {
      if (alive.current) {
        setError(caught instanceof Error ? caught.message : '下载数据包失败');
        setDownloadProgress(current => current ? { ...current, status: 'failed' } : null);
      }
    }
    finally { downloadBusy.current = false; if (alive.current) setDownloading(''); }
  };

  const browserDownload = (job: AcquisitionJob) => {
    if (downloadBusy.current) return;
    // A manifest URL is data, not a navigation instruction. Always construct
    // the authenticated same-origin endpoint from the opaque package identity.
    setError(null);
    try {
      triggerBrowserSave(`/api/v1/data-acquisition/jobs/${encodeURIComponent(job.jobId)}/download`, `财经数据包_${job.jobId}.zip`);
      setNativeDownloadJobId(job.jobId);
      setDownloadProgress(current => current?.jobId === job.jobId ? null : current);
    } catch { setError('浏览器暂未接收下载，请使用旁边的下载按钮重试。'); }
  };

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader eyebrow="AI ORCHESTRATION · MULTI-SOURCE EXPORT" title="数据一站式获取"
          description="手动研报筛选使用本地链接库；跨渠道取数按已确认的目标与日期，每次重新调用渠道 API，返回实际匹配结果与原文链接。"
          actions={<button className="btn-secondary inline-flex items-center gap-2" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />刷新任务
          </button>} />

        <EvidenceRail items={[
          { label: '可用渠道', value: capabilities ? `${capabilities.sources.filter((source) => source.available).length} / ${capabilities.sources.length}` : '—', note: '每个渠道独立筛选与导出', tone: capabilities?.sources.some((source) => source.available) ? 'verified' : 'default' },
          { label: '规划模型', value: capabilities ? capabilities.planner.available ? capabilities.planner.model : '等待配置' : '读取中', note: capabilities?.planner.available ? `最多 ${capabilities.planner.maxTasks} 个子任务` : '仍可使用本地研报库', tone: capabilities?.planner.available ? 'verified' : 'default' },
          { label: '所选任务', value: runTask ? PHASE_LABELS[runTask.phase] : tracked.loading ? '正在恢复进度' : tracked.unavailable ? '暂不可查看' : '未选择', note: runTask ? `${runTask.progress}% · ${runTask.completedTasks}/${runTask.totalTasks}` : '离开页面不影响后台执行', tone: runActive ? 'live' : runTask?.status === 'failed' ? 'warning' : 'default' },
          { label: '最近数据包', value: jobs ? `${jobs.length} 个` : '—', note: '当前账号的最近生成记录', tone: jobs?.length ? 'verified' : 'default' },
        ]} />

        {error ? <div className="rounded-2xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div> : null}

        <nav className="flex flex-wrap gap-2" aria-label="取数工作区">
          <button className={showTasks ? 'btn-secondary' : 'btn-primary'} aria-pressed={!showTasks}
            onClick={() => { setReportsOpened(true); setSearchParams(current => { const next = new URLSearchParams(current); next.set('view', 'reports'); return next; }); }}>研报筛选下载</button>
          <button className={showTasks ? 'btn-primary' : 'btn-secondary'} aria-pressed={showTasks}
            onClick={() => setSearchParams(current => { const next = new URLSearchParams(current); next.set('view', 'tasks'); return next; })}>跨渠道取数与任务</button>
          <Link className="btn-secondary" to="/tasks">全部后台任务</Link>
        </nav>

        <div hidden={showTasks}>{!showTasks || reportsOpened ? <ResearchReportLibrary /> : null}</div>

        {showTasks ? <>
        <section aria-label="后台取数任务">
          <Card title="后台取数任务">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs leading-5 text-secondary-text">点击任务查看真实进度与数据包；可离开页面，稍后回来继续查看。</p>
              {activeTaskId ? <button className="btn-secondary" disabled={planning || validating || running} onClick={newTask}>新建取数任务</button> : null}
            </div>
            {activeTaskId ? <p className="mb-3 text-xs text-secondary-text">新建任务只切换工作区，不中止已提交的后台任务。</p> : null}
            {loadErrors.tasks ? <p role="status" className="mb-3 text-sm text-warning">{loadErrors.tasks} <button className="underline" onClick={() => void loadChannel('tasks')}>重试后台任务</button></p> : null}
            {tasks === null ? <p role="status" className="text-sm text-secondary-text">{loadErrors.tasks ? '任务列表尚未读取成功' : '正在读取后台任务…'}</p> : tasks.length ?
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{tasks.slice(0, 9).map(task => <button key={task.taskId} onClick={() => selectTask(task.taskId)}
                aria-pressed={task.taskId === activeTaskId} className={`min-w-0 rounded-xl border p-3 text-left ${task.taskId === activeTaskId ? 'border-primary bg-primary/10' : 'border-border'}`}>
                <div className="flex justify-between gap-2 text-xs text-secondary-text"><span>{TASK_STATUS_LABELS[task.status]}</span><span>{task.progress}%</span></div>
                <p className="mt-1 truncate text-sm font-medium">{task.result?.title || task.tasks.map(item => item.label).slice(0, 2).join('、') || '数据获取任务'}</p>
                <p className="mt-1 truncate text-xs text-secondary-text">{task.message}</p>
                <p className="mt-2 text-[11px] text-secondary-text">{formatTime(task.updatedAt)}</p>
              </button>)}</div> : <p className="text-sm text-secondary-text">还没有后台取数任务，先在下方生成计划并确认执行。</p>}
            {tracked.loading ? <p className="mt-3 text-sm text-secondary-text" role="status">正在恢复所选任务进度…</p> : null}
            {tracked.notice ? <p className="mt-3 text-sm text-warning" role="status">{tracked.notice} <button className="underline" onClick={tracked.retry}>重试所选任务</button></p> : null}
            {runTask ? <div className="mt-3 flex flex-wrap items-center gap-3 text-sm"><span>{runTask.message}</span><a className="text-primary underline" href="#acquisition-task-detail">查看执行明细{runTask.result ? '与下载' : ''}</a></div> : null}
          </Card>
        </section>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,.7fr)]">
          <Card className="relative overflow-hidden" padding="lg">
            <div className="pointer-events-none absolute -right-24 -top-24 h-56 w-56 rounded-full bg-primary/10 blur-3xl" />
            <div className="relative">
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground"><Bot className="h-5 w-5 text-primary" />告诉 AI 你要什么数据</div>
              <textarea aria-label="取数需求" value={request} onChange={(event) => setRequest(event.target.value)} rows={6}
                className="mt-4 w-full resize-y rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm leading-6 text-foreground outline-none transition focus:border-primary/60 focus:ring-2 focus:ring-primary/10"
                placeholder="例如：获取两只自选股近三个月行情、公告、机构研报、小作文与工商风险，按来源打包……" />
              <div className="mt-3 flex flex-wrap gap-2">
                {EXAMPLES.map((example, index) => <button key={example} type="button" onClick={() => setRequest(example)}
                  className="rounded-full border border-border bg-card/70 px-3 py-1.5 text-xs text-secondary-text transition hover:border-primary/40 hover:text-foreground">
                  示例 {index + 1}
                </button>)}
              </div>
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs text-secondary-text"><ShieldCheck className="h-4 w-4 text-success" />先审计划，再执行；密钥不会写入数据包</div>
                <button className="btn-primary inline-flex items-center gap-2" disabled={planning || validating || runActive || request.trim().length < 2} onClick={() => void createPlan()}>
                  <Send className={`h-4 w-4 ${planning ? 'animate-pulse' : ''}`} />{planning ? 'AI 正在规划…' : '生成取数计划'}
                </button>
              </div>
            </div>
          </Card>

          <Card title="已接入渠道" subtitle="SOURCE REGISTRY">
            <div aria-label="股票目录同步状态" className="mb-3 text-xs leading-5 text-secondary-text">
              <p className="flex flex-wrap gap-x-2 gap-y-1">
                <span className="font-medium text-foreground">股票目录 · {capabilities?.stockDirectory?.source || '未核验'}</span>
                {capabilities?.stockDirectory?.entryCount != null ? <span>{capabilities.stockDirectory.entryCount.toLocaleString('zh-CN')} 只证券</span> : null}
                <span>{capabilities?.stockDirectory?.lastSuccessAt ? `最近成功同步：${formatTime(capabilities.stockDirectory.lastSuccessAt)}` : '最近成功同步：未核验'}</span>
                <span>{capabilities?.stockDirectory?.refreshing ? '后台自动更新中' : capabilities?.stockDirectory?.stale ? '等待后台自动更新' : capabilities?.stockDirectory?.lastSuccessAt ? '目录已更新 · 后台自动维护' : '目录状态待核验'}</span>
              </p>
              {capabilities?.stockDirectory?.error ? <p className="text-warning">目录更新暂未成功，等待后台自动重试。</p> : null}
            </div>
            {loadErrors.sources ? <p role="status" className="mb-3 text-sm text-warning">{loadErrors.sources} <button className="underline" onClick={() => void loadChannel('sources')}>重试渠道信息</button></p> : null}
            {!capabilities && !loadErrors.sources ? <p role="status" className="text-sm text-secondary-text">正在读取渠道信息…</p> : null}
            <div className="space-y-3">
              {(capabilities?.sources ?? []).map((source) => <div key={source.key} className="rounded-2xl border border-border/70 bg-background/45 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">{source.name}</span>
                  <Badge variant={source.available ? 'success' : 'warning'}>{source.available ? '可用' : '待配置'}</Badge>
                </div>
                <p className="mt-1 text-xs text-secondary-text">{source.mode} · {source.resources.length} 类能力</p>
              </div>)}
            </div>
          </Card>
        </div>

        {plan ? <Card title={plan.title} subtitle={`${plan.model} · 可审计执行计划`} variant="gradient" padding="lg">
          <p className="text-sm text-secondary-text">{plan.objective}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-secondary-text">
            {[...plan.scope.companyNames, ...plan.scope.symbols, ...plan.scope.keywords].map((value) => <Badge key={value}>{value}</Badge>)}
            {plan.scope.startDate || plan.scope.endDate ? <Badge>{plan.scope.startDate || '—'} → {plan.scope.endDate || '—'}</Badge> : null}
            <Badge variant={plan.scope.marketWide ? 'warning' : 'success'}>{plan.scope.marketWide ? '全市场范围' : '严格按目标过滤'}</Badge>
            <Badge variant={plan.includeFiles ? 'warning' : 'info'}>{plan.includeFiles ? '下载原始文件并打包' : '仅结构化数据与原文链接'}</Badge>
          </div>
          {plan.preflight ? <PlanPreflight preflight={plan.preflight} selections={entitySelections}
            disabled={validating || runActive || planChanged}
            onSelectionChange={(query, value) => setEntitySelections(current => ({ ...current, [query]: value }))} /> : null}
          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {plan.tasks.map((task, index) => <div key={task.id} className="rounded-2xl border border-border/70 bg-background/55 p-4">
              <div className="flex items-start justify-between gap-3"><span className="text-xs font-semibold text-primary">{String(index + 1).padStart(2, '0')}</span><Badge>{SOURCE_LABELS[task.source] ?? task.source}</Badge></div>
              <h3 className="mt-3 font-semibold text-foreground">{task.label}</h3>
              <p className="mt-1 text-xs leading-5 text-secondary-text">{task.reason || '按用户需求获取该数据集'}</p>
              {task.resource === 'research_report' ? <div className="mt-3 space-y-2 rounded-xl border border-primary/20 bg-primary/5 p-3 text-[11px] text-secondary-text">
                <p className="font-medium text-foreground">按目标与日期现场调用研报 API → 严格筛选 → PDF链接/原文</p>
                <div className="flex flex-wrap gap-1.5">
                  {(Array.isArray(task.params.topics) ? task.params.topics : []).map((topic) => <Badge key={String(topic)}>{String(topic)}</Badge>)}
                  <Badge>{String(task.params.keywordMode ?? task.params.keyword_mode ?? 'any').toLowerCase() === 'any' ? '任一主题命中' : '全部主题命中'}</Badge>
                  <Badge variant="success">人工确认优先</Badge>
                </div>
              </div> : null}
              <code className="mt-3 block truncate rounded-lg bg-card px-2 py-1.5 text-[11px] text-secondary-text">{task.source}.{task.resource}</code>
              {plan.preflight?.checks.filter(check => check.taskId === task.id).map((check, checkIndex) => <p key={checkIndex}
                className={`mt-2 text-xs leading-5 ${check.status === 'blocked' ? 'text-warning' : 'text-secondary-text'}`}>{check.message}</p>)}
            </div>)}
          </div>
          {plan.caveats.length ? <div className="mt-4 rounded-xl border border-warning/25 bg-warning/10 px-4 py-3 text-xs text-secondary-text">{plan.caveats.join('；')}</div> : null}
          {planChanged ? <p role="status" className="mt-4 text-sm text-warning">需求已修改，请重新生成并核对计划后再执行，避免按旧条件取数。</p> : null}
          {selectionsChanged ? <p role="status" className="mt-4 text-sm text-warning">目标选择已修改，请重新校验股票代码与各渠道参数后再执行。</p> : null}
          <div className="mt-5 flex flex-wrap justify-end gap-3">
            {plan.preflight && (planBlocked || selectionsChanged) ? <button className="btn-secondary inline-flex items-center gap-2"
              disabled={runActive || planChanged || planning || validating} onClick={() => void validatePlan()}>
              <ShieldCheck className={`h-4 w-4 ${validating ? 'animate-pulse' : ''}`} />{validating ? '正在校验目标…' : '重新校验目标与代码'}
            </button> : null}
            <button className="btn-primary inline-flex items-center gap-2" disabled={runActive || planChanged || planning || validating || planBlocked || selectionsChanged} onClick={() => void runPlan()}>
            <Play className={`h-4 w-4 ${runActive ? 'animate-pulse' : ''}`} />{runActive ? '后台任务执行中…' : `确认执行 ${plan.tasks.length} 个任务`}
          </button></div>
        </Card> : null}

        <div id="acquisition-task-detail" className="scroll-mt-6" />
        {runTask ? <Card title="真实任务进度" subtitle={runTask.taskId} padding="lg">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                {runTask.status === 'failed' ? <XCircle className="h-5 w-5 text-danger" /> :
                  runTask.status === 'completed' && runTask.result?.status === 'success' && runTask.result.summary.rowCount > 0 ? <CheckCircle2 className="h-5 w-5 text-success" /> : runTask.status === 'completed' ? <XCircle className="h-5 w-5 text-warning" /> :
                    <CircleDashed className="h-5 w-5 animate-spin text-primary" />}
                <span className="font-semibold text-foreground">{runTask.result && ['completed', 'failed'].includes(runTask.status) ? jobStatusLabel(runTask.result) : PHASE_LABELS[runTask.phase] ?? runTask.phase}</span>
              </div>
              <p className={`mt-1 text-sm ${runTask.status === 'failed' ? 'text-danger' : 'text-secondary-text'}`}>{runTask.message}</p>
            </div>
            <div className="text-right font-mono text-2xl font-semibold text-foreground">{runTask.progress}%</div>
          </div>
          <div className="mt-4 h-3 overflow-hidden rounded-full bg-border/70" role="progressbar" aria-label="跨渠道取数与打包进度"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={runTask.progress}>
            <div className={`h-full rounded-full transition-[width] duration-300 ${runTask.status === 'failed' ? 'bg-danger' : 'bg-primary'}`}
              style={{ width: `${runTask.progress}%` }} />
          </div>
          <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-label="渠道任务处理状态">
            {(runTask.tasks ?? []).map((task, index) => {
              const dataset = runTask.result?.datasets.find(item => item.taskId === task.id);
              const completed = dataset?.status === 'success' && dataset.rowCount > 0;
              const failed = Boolean(dataset && (dataset.status === 'failed' || dataset.rowCount === 0));
              const current = task.id === runTask.currentTaskId && runTask.status === 'running';
              return <div key={task.id} className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 ${completed ? 'border-success/25 bg-success/5' : failed ? 'border-warning/25 bg-warning/5' : current ? 'border-primary/35 bg-primary/5' : 'border-border/70 bg-background/40'}`}>
                {completed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" /> : failed ? <XCircle className="h-4 w-4 shrink-0 text-warning" /> : current ? <CircleDashed className="h-4 w-4 shrink-0 animate-spin text-primary" /> : <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[9px] text-secondary-text">{index + 1}</span>}
                <div className="min-w-0"><p className="truncate text-xs font-medium text-foreground">{task.label}</p><p className="truncate text-[10px] text-secondary-text">{SOURCE_LABELS[task.source] ?? task.source}</p></div>
              </div>;
            })}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-secondary-text">
            <span>已处理 {runTask.completedTasks}/{runTask.totalTasks || 0} 个渠道任务</span>
            <span>最后更新 {formatTime(runTask.updatedAt)}</span>
          </div>
        </Card> : null}

        {activeJob ? <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="数据集" value={activeJob.summary.taskCount} icon={<DatabaseZap className="h-5 w-5" />} />
            <StatCard label="成功" value={activeJob.summary.successCount} icon={<CheckCircle2 className="h-5 w-5" />} />
            <StatCard label="失败" value={activeJob.summary.failedCount} icon={<XCircle className="h-5 w-5" />} />
            <StatCard label="总行数" value={activeJob.summary.rowCount} icon={<PackageCheck className="h-5 w-5" />} />
          </div>
          <Card title={jobTitle(activeJob)} subtitle={`${activeJob.title} · ${activeJob.jobId}`}>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div><p className="text-sm text-foreground">{activeJob.summary.rowCount === 0 ? '本次查询未取得符合条件的数据。请检查范围与各渠道说明；可下载查询结果与诊断，不代表资料已获取。' : '按渠道分目录，分别包含 JSON、CSV、Excel，并附任务范围与来源清单。'}</p><p className="mt-1 text-xs text-secondary-text">状态：{jobStatusLabel(activeJob)} · {formatTime(activeJob.generatedAt)}{activeJob.summary.includeFiles ? ` · 原始文件成功 ${activeJob.summary.downloadedFileCount ?? 0} 个 / 失败 ${activeJob.summary.failedFileCount ?? 0} 个` : ''}</p></div>
              <div className="flex flex-wrap gap-2"><button className="btn-primary inline-flex items-center gap-2" disabled={Boolean(downloading)} onClick={() => void download(activeJob)}>
                <Download className="h-4 w-4" />{downloading === activeJob.jobId ? `${downloadProgress?.percent ?? '—'}% 下载中` : activeJob.summary.rowCount === 0 ? '下载查询结果与诊断' : '下载 ZIP 数据包'}
              </button>
              {activeJob.summary.rowCount > 0 && activeJob.status !== 'failed' ? <button className="btn-secondary inline-flex items-center gap-2" disabled={Boolean(downloading)} onClick={() => browserDownload(activeJob)}>
                <Download className="h-4 w-4" />浏览器下载
              </button> : null}</div>
            </div>
            <ul className="mt-3 space-y-2" aria-label="数据包渠道结果">{activeJob.datasets.map(dataset => <li key={dataset.taskId} className="rounded-lg border border-border p-3 text-xs">
              <span className="font-medium">{dataset.label} · {SOURCE_LABELS[dataset.source] || dataset.source}</span>
              <span className="ml-2">{dataset.rowCount === 0 ? '未取得数据' : dataset.status === 'success' ? `${dataset.rowCount} 行` : '取数未完成'}</span>
              {dataset.error ? <p className="mt-1 break-words text-danger">{dataset.error}</p> : null}
              {dataset.attachmentError ? <p className="mt-1 break-words text-warning">{dataset.attachmentError}</p> : null}
            </li>)}</ul>
            {downloadProgress?.jobId === activeJob.jobId ? <DownloadProgress state={downloadProgress} /> : null}
            {nativeDownloadJobId === activeJob.jobId ? <p role="status" className="mt-3 text-xs text-secondary-text">已交给浏览器，请在下载列表查看进度</p> : null}
          </Card>
        </div> : null}

        <Card title="最近数据包" subtitle="LOCAL PACKAGE HISTORY">
          {loadErrors.jobs ? <p role="status" className="mb-3 text-sm text-warning">{loadErrors.jobs} <button className="underline" onClick={() => void loadChannel('jobs')}>重试数据包记录</button></p> : null}
          {jobs === null ? <p role="status" className="text-sm text-secondary-text">{loadErrors.jobs ? '数据包记录尚未读取成功' : '正在读取数据包记录…'}</p> : !jobs.length ? <EmptyState icon={<FileArchive className="h-6 w-6" />} title="还没有数据包" description="描述需求并执行计划后，生成记录会出现在这里。" /> :
            <div className="divide-y divide-border/60">{jobs.map((job) => <div key={job.jobId} className="py-4 first:pt-0 last:pb-0">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="truncate text-sm font-medium text-foreground">{job.title}</p><Badge variant={job.status === 'success' && job.summary.rowCount > 0 ? 'success' : job.summary.rowCount === 0 || job.status === 'partial' ? 'warning' : 'danger'}>{jobStatusLabel(job)}</Badge>{!job.contractVersion?.startsWith('channel-scoped-v') ? <Badge variant="warning">旧版未分渠道</Badge> : <Badge variant="info">渠道独立包</Badge>}</div>
                  <p className="mt-1 truncate text-xs text-secondary-text">{job.summary.rowCount} 行 · {job.summary.successCount}/{job.summary.taskCount} 数据集 · {formatTime(job.generatedAt)}</p></div>
                <div className="flex gap-2"><button className="btn-secondary" onClick={() => { setActiveJob(job); setViewedJobTaskId(activeTaskId); document.getElementById('acquisition-task-detail')?.scrollIntoView({ block: 'start' }); }}>查看渠道结果</button>
                <button className="btn-secondary inline-flex shrink-0 items-center gap-2" disabled={Boolean(downloading)} onClick={() => void download(job)}><Download className="h-4 w-4" />{downloading === job.jobId ? `${downloadProgress?.percent ?? '—'}%` : '下载'}</button>
                {job.summary.rowCount > 0 && job.status !== 'failed' ? <button className="btn-secondary inline-flex shrink-0 items-center gap-2" disabled={Boolean(downloading)} onClick={() => browserDownload(job)}><Download className="h-4 w-4" />浏览器下载</button> : null}</div>
              </div>
              {downloadProgress?.jobId === job.jobId ? <DownloadProgress state={downloadProgress} /> : null}
              {nativeDownloadJobId === job.jobId ? <p role="status" className="mt-3 text-xs text-secondary-text">已交给浏览器，请在下载列表查看进度</p> : null}
            </div>)}</div>}
        </Card>
        </> : null}
      </div>
    </AppPage>
  );
};

export default DataAcquisitionPage;
