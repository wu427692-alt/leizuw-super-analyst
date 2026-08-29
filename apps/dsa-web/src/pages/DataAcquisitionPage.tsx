import { useCallback, useEffect, useState } from 'react';
import { Bot, CheckCircle2, CircleDashed, DatabaseZap, Download, FileArchive, PackageCheck, Play, RefreshCw, Send, ShieldCheck, XCircle } from 'lucide-react';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { AppPage, Badge, Card, EmptyState, EvidenceRail, PageHeader, StatCard } from '../components/common';
import ResearchReportLibrary from '../components/data-acquisition/ResearchReportLibrary';
import type { AcquisitionCapabilities, AcquisitionDownloadProgress, AcquisitionJob, AcquisitionPlan, AcquisitionRunTask } from '../types/dataAcquisition';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';

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
const ACTIVE_TASK_STORAGE_KEY = 'dsa:data-acquisition:active-task';

type DownloadState = AcquisitionDownloadProgress & {
  jobId: string;
  status: 'downloading' | 'completed';
};

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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

function DownloadProgress({ state }: { state: DownloadState }) {
  const label = state.total
    ? `${formatBytes(state.loaded)} / ${formatBytes(state.total)}`
    : `已接收 ${formatBytes(state.loaded)}`;
  return <div className="mt-3" aria-live="polite">
    <div className="flex items-center justify-between gap-3 text-[11px] text-secondary-text">
      <span>{state.status === 'completed' ? '下载完成' : '正在传输真实文件字节'}</span>
      <span className="font-mono">{state.percent !== undefined ? `${state.percent}% · ` : ''}{label}</span>
    </div>
    <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-border/70" role="progressbar" aria-label="数据包下载进度"
      aria-valuemin={0} aria-valuemax={100} aria-valuenow={state.percent}>
      <div className={`h-full rounded-full bg-primary transition-[width] duration-200 ${state.percent === undefined ? 'w-1/3 animate-pulse' : ''}`}
        style={state.percent === undefined ? undefined : { width: `${state.percent}%` }} />
    </div>
  </div>;
}

const DataAcquisitionPage = () => {
  const [request, setRequest] = useState(EXAMPLES[0]);
  const [capabilities, setCapabilities] = useState<AcquisitionCapabilities | null>(null);
  const [plan, setPlan] = useState<AcquisitionPlan | null>(null);
  const [jobs, setJobs] = useState<AcquisitionJob[]>([]);
  const [activeJob, setActiveJob] = useState<AcquisitionJob | null>(null);
  const [runTask, setRunTask] = useState<AcquisitionRunTask | null>(null);
  const [activeTaskId, setActiveTaskId] = useState(() => window.localStorage.getItem(ACTIVE_TASK_STORAGE_KEY) ?? '');
  const [planning, setPlanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [downloadProgress, setDownloadProgress] = useState<DownloadState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [sourceData, jobData] = await Promise.allSettled([
        dataAcquisitionApi.capabilities(), dataAcquisitionApi.jobs(),
      ]);
      if (sourceData.status === 'fulfilled') setCapabilities(sourceData.value);
      if (jobData.status === 'fulfilled') setJobs(jobData.value.items);
      if (sourceData.status === 'rejected' && jobData.status === 'rejected') throw sourceData.reason;
      if (sourceData.status === 'rejected' || jobData.status === 'rejected') {
        setError('部分数据暂时不可用，已保留成功加载的渠道或任务记录。');
      } else {
        setError(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载取数工作台失败');
    }
  }, []);

  usePageActivationRefresh(load, { intervalMs: 30_000, minIntervalMs: 2_000 });

  useEffect(() => {
    if (!activeTaskId) return undefined;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await dataAcquisitionApi.task(activeTaskId);
        if (cancelled) return;
        setRunTask(next);
        if (next.status === 'completed' && next.result) {
          setActiveJob(next.result);
          setJobs((current) => [next.result!, ...current.filter((item) => item.jobId !== next.result!.jobId)]);
          window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
          setActiveTaskId('');
          return;
        }
        if (next.status === 'failed') {
          window.localStorage.removeItem(ACTIVE_TASK_STORAGE_KEY);
          setActiveTaskId('');
          return;
        }
      } catch {
        // A transient poll failure must not discard a still-running server task.
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [activeTaskId]);

  const createPlan = async () => {
    if (request.trim().length < 2) return;
    setPlanning(true); setError(null); setPlan(null); setActiveJob(null);
    try { setPlan(await dataAcquisitionApi.plan(request.trim())); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '大模型生成取数计划失败'); }
    finally { setPlanning(false); }
  };

  const runPlan = async () => {
    if (!plan) return;
    setRunning(true); setError(null); setActiveJob(null); setRunTask(null);
    try {
      const task = await dataAcquisitionApi.runAsync(request.trim(), plan);
      setRunTask(task);
      setActiveTaskId(task.taskId);
      window.localStorage.setItem(ACTIVE_TASK_STORAGE_KEY, task.taskId);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '执行取数任务失败'); }
    finally { setRunning(false); }
  };

  const download = async (job: AcquisitionJob) => {
    setDownloading(job.jobId); setError(null);
    setDownloadProgress({ jobId: job.jobId, loaded: 0, status: 'downloading' });
    try {
      const blob = await dataAcquisitionApi.download(job.jobId, (progress) => {
        setDownloadProgress({ jobId: job.jobId, status: 'downloading', ...progress });
      });
      setDownloadProgress({ jobId: job.jobId, loaded: blob.size, total: blob.size, percent: 100, status: 'completed' });
      saveBlob(blob, `财经数据包_${job.jobId}.zip`);
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : '下载数据包失败'); }
    finally { setDownloading(''); }
  };

  const runActive = running || runTask?.status === 'queued' || runTask?.status === 'running';

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader eyebrow="AI ORCHESTRATION · MULTI-SOURCE EXPORT" title="数据一站式获取"
          description="研报先进入两年本地链接库供人工精确筛选；其他跨渠道需求仍可由大模型生成可审计取数计划。"
          actions={<button className="btn-secondary inline-flex items-center gap-2" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />刷新任务
          </button>} />

        <EvidenceRail items={[
          { label: '可用渠道', value: `${capabilities?.sources.filter((source) => source.available).length ?? 0} / ${capabilities?.sources.length ?? 0}`, note: '每个渠道独立筛选与导出', tone: capabilities?.sources.some((source) => source.available) ? 'verified' : 'warning' },
          { label: '规划模型', value: capabilities?.planner.available ? capabilities.planner.model : '等待配置', note: capabilities?.planner.available ? `最多 ${capabilities.planner.maxTasks} 个子任务` : '仍可使用本地研报库', tone: capabilities?.planner.available ? 'verified' : 'warning' },
          { label: '当前任务', value: runTask ? PHASE_LABELS[runTask.phase] : '无运行任务', note: runTask ? `${runTask.progress}% · ${runTask.completedTasks}/${runTask.totalTasks}` : '任务会在后台持续执行', tone: runActive ? 'live' : runTask?.status === 'failed' ? 'warning' : 'default' },
          { label: '历史数据包', value: `${jobs.length} 个`, note: '保留真实任务与下载记录', tone: jobs.length ? 'verified' : 'default' },
        ]} />

        {error ? <div className="rounded-2xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div> : null}

        <ResearchReportLibrary />

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,.7fr)]">
          <Card className="relative overflow-hidden" padding="lg">
            <div className="pointer-events-none absolute -right-24 -top-24 h-56 w-56 rounded-full bg-primary/10 blur-3xl" />
            <div className="relative">
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground"><Bot className="h-5 w-5 text-primary" />告诉 AI 你要什么数据</div>
              <textarea value={request} onChange={(event) => setRequest(event.target.value)} rows={6}
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
                <button className="btn-primary inline-flex items-center gap-2" disabled={planning || runActive || request.trim().length < 2} onClick={() => void createPlan()}>
                  <Send className={`h-4 w-4 ${planning ? 'animate-pulse' : ''}`} />{planning ? 'AI 正在规划…' : '生成取数计划'}
                </button>
              </div>
            </div>
          </Card>

          <Card title="已接入渠道" subtitle="SOURCE REGISTRY">
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
          <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {plan.tasks.map((task, index) => <div key={task.id} className="rounded-2xl border border-border/70 bg-background/55 p-4">
              <div className="flex items-start justify-between gap-3"><span className="text-xs font-semibold text-primary">{String(index + 1).padStart(2, '0')}</span><Badge>{SOURCE_LABELS[task.source] ?? task.source}</Badge></div>
              <h3 className="mt-3 font-semibold text-foreground">{task.label}</h3>
              <p className="mt-1 text-xs leading-5 text-secondary-text">{task.reason || '按用户需求获取该数据集'}</p>
              {task.resource === 'research_report' ? <div className="mt-3 space-y-2 rounded-xl border border-primary/20 bg-primary/5 p-3 text-[11px] text-secondary-text">
                <p className="font-medium text-foreground">本地库条件召回 → 标签筛选 → 人工确认 → PDF链接/原文</p>
                <div className="flex flex-wrap gap-1.5">
                  {(Array.isArray(task.params.topics) ? task.params.topics : []).map((topic) => <Badge key={String(topic)}>{String(topic)}</Badge>)}
                  <Badge>{String(task.params.keywordMode ?? task.params.keyword_mode ?? 'any').toLowerCase() === 'any' ? '任一主题命中' : '全部主题命中'}</Badge>
                  <Badge variant="success">人工确认优先</Badge>
                </div>
              </div> : null}
              <code className="mt-3 block truncate rounded-lg bg-card px-2 py-1.5 text-[11px] text-secondary-text">{task.source}.{task.resource}</code>
            </div>)}
          </div>
          {plan.caveats.length ? <div className="mt-4 rounded-xl border border-warning/25 bg-warning/10 px-4 py-3 text-xs text-secondary-text">{plan.caveats.join('；')}</div> : null}
          <div className="mt-5 flex justify-end"><button className="btn-primary inline-flex items-center gap-2" disabled={runActive} onClick={() => void runPlan()}>
            <Play className={`h-4 w-4 ${runActive ? 'animate-pulse' : ''}`} />{runActive ? '后台任务执行中…' : `确认执行 ${plan.tasks.length} 个任务`}
          </button></div>
        </Card> : null}

        {runTask ? <Card title="真实任务进度" subtitle={runTask.taskId} padding="lg">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                {runTask.status === 'failed' ? <XCircle className="h-5 w-5 text-danger" /> :
                  runTask.status === 'completed' ? <CheckCircle2 className="h-5 w-5 text-success" /> :
                    <CircleDashed className="h-5 w-5 animate-spin text-primary" />}
                <span className="font-semibold text-foreground">{PHASE_LABELS[runTask.phase] ?? runTask.phase}</span>
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
          <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {((runTask.tasks?.length ? runTask.tasks : plan?.tasks) ?? []).map((task, index) => {
              const completed = index < runTask.completedTasks;
              const current = task.id === runTask.currentTaskId && runTask.status === 'running';
              return <div key={task.id} className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 ${completed ? 'border-success/25 bg-success/5' : current ? 'border-primary/35 bg-primary/5' : 'border-border/70 bg-background/40'}`}>
                {completed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" /> : current ? <CircleDashed className="h-4 w-4 shrink-0 animate-spin text-primary" /> : <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-border text-[9px] text-secondary-text">{index + 1}</span>}
                <div className="min-w-0"><p className="truncate text-xs font-medium text-foreground">{task.label}</p><p className="truncate text-[10px] text-secondary-text">{SOURCE_LABELS[task.source] ?? task.source}</p></div>
              </div>;
            })}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[11px] text-secondary-text">
            <span>已完成 {runTask.completedTasks}/{runTask.totalTasks || plan?.tasks.length || 0} 个渠道任务</span>
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
          <Card title="数据包已生成" subtitle={activeJob.jobId}>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div><p className="text-sm text-foreground">按渠道分目录，分别包含 JSON、CSV、Excel，并附任务范围与来源清单。</p><p className="mt-1 text-xs text-secondary-text">状态：{activeJob.status} · {formatTime(activeJob.generatedAt)}{activeJob.summary.includeFiles ? ` · 原始文件 ${activeJob.summary.downloadedFileCount ?? 0} 个` : ''}</p></div>
              <button className="btn-primary inline-flex items-center gap-2" disabled={downloading === activeJob.jobId} onClick={() => void download(activeJob)}>
                <Download className="h-4 w-4" />{downloading === activeJob.jobId ? `${downloadProgress?.percent ?? '—'}% 下载中` : '下载完整 ZIP'}
              </button>
            </div>
            {downloadProgress?.jobId === activeJob.jobId ? <DownloadProgress state={downloadProgress} /> : null}
          </Card>
        </div> : null}

        <Card title="最近数据包" subtitle="LOCAL PACKAGE HISTORY">
          {!jobs.length ? <EmptyState icon={<FileArchive className="h-6 w-6" />} title="还没有数据包" description="描述需求并执行计划后，生成记录会出现在这里。" /> :
            <div className="divide-y divide-border/60">{jobs.map((job) => <div key={job.jobId} className="py-4 first:pt-0 last:pb-0">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="min-w-0"><div className="flex items-center gap-2"><p className="truncate text-sm font-medium text-foreground">{job.title}</p><Badge variant={job.status === 'success' ? 'success' : job.status === 'partial' ? 'warning' : 'danger'}>{job.status}</Badge>{!job.contractVersion?.startsWith('channel-scoped-v') ? <Badge variant="warning">旧版未分渠道</Badge> : <Badge variant="info">渠道独立包</Badge>}</div>
                  <p className="mt-1 truncate text-xs text-secondary-text">{job.summary.rowCount} 行 · {job.summary.successCount}/{job.summary.taskCount} 数据集 · {formatTime(job.generatedAt)}</p></div>
                <button className="btn-secondary inline-flex shrink-0 items-center gap-2" disabled={downloading === job.jobId} onClick={() => void download(job)}><Download className="h-4 w-4" />{downloading === job.jobId ? `${downloadProgress?.percent ?? '—'}%` : '下载'}</button>
              </div>
              {downloadProgress?.jobId === job.jobId ? <DownloadProgress state={downloadProgress} /> : null}
            </div>)}</div>}
        </Card>
      </div>
    </AppPage>
  );
};

export default DataAcquisitionPage;
