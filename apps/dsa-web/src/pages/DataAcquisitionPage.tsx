import { useCallback, useEffect, useState } from 'react';
import { Bot, CheckCircle2, DatabaseZap, Download, FileArchive, PackageCheck, Play, RefreshCw, Send, ShieldCheck, XCircle } from 'lucide-react';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import { AppPage, Badge, Card, EmptyState, PageHeader, StatCard } from '../components/common';
import type { AcquisitionCapabilities, AcquisitionJob, AcquisitionPlan } from '../types/dataAcquisition';

const EXAMPLES = [
  '打包华懋科技和胜宏科技近90天行情、估值、资金、公告、研报、新闻和知识星球小作文，并补充工商与风险信息',
  '获取胜宏科技最近8个季度财务三表、财务指标、机构研报和股东增减持，按数据源分别导出',
  '整理华懋科技本月所有公告、新闻、小作文和机构观点，并下载公告PDF、研报及相关附件一起打包',
];

const SOURCE_LABELS: Record<string, string> = {
  tushare: 'Tushare Pro', zsxq: '知识星球', cninfo: '巨潮资讯', monitor: '其他情报', tianyancha: '天眼查',
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

const DataAcquisitionPage = () => {
  const [request, setRequest] = useState(EXAMPLES[0]);
  const [capabilities, setCapabilities] = useState<AcquisitionCapabilities | null>(null);
  const [plan, setPlan] = useState<AcquisitionPlan | null>(null);
  const [jobs, setJobs] = useState<AcquisitionJob[]>([]);
  const [activeJob, setActiveJob] = useState<AcquisitionJob | null>(null);
  const [planning, setPlanning] = useState(false);
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [sourceData, jobData] = await Promise.all([dataAcquisitionApi.capabilities(), dataAcquisitionApi.jobs()]);
      setCapabilities(sourceData); setJobs(jobData.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '加载取数工作台失败');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const createPlan = async () => {
    if (request.trim().length < 2) return;
    setPlanning(true); setError(null); setPlan(null); setActiveJob(null);
    try { setPlan(await dataAcquisitionApi.plan(request.trim())); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '大模型生成取数计划失败'); }
    finally { setPlanning(false); }
  };

  const runPlan = async () => {
    if (!plan) return;
    setRunning(true); setError(null);
    try {
      const job = await dataAcquisitionApi.run(request.trim(), plan);
      setActiveJob(job); setJobs((current) => [job, ...current.filter((item) => item.jobId !== job.jobId)]);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '执行取数任务失败'); }
    finally { setRunning(false); }
  };

  const download = async (job: AcquisitionJob) => {
    setDownloading(job.jobId); setError(null);
    try { saveBlob(await dataAcquisitionApi.download(job.jobId), `财经数据包_${job.jobId}.zip`); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '下载数据包失败'); }
    finally { setDownloading(''); }
  };

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader eyebrow="AI ORCHESTRATION · MULTI-SOURCE EXPORT" title="数据一站式获取"
          description="用自然语言描述数据需求，由大模型生成可审计计划，跨渠道取数并打包为 JSON、CSV、Excel 和 ZIP。"
          actions={<button className="btn-secondary inline-flex items-center gap-2" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />刷新任务
          </button>} />

        {error ? <div className="rounded-2xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div> : null}

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
                <button className="btn-primary inline-flex items-center gap-2" disabled={planning || running || request.trim().length < 2} onClick={() => void createPlan()}>
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
              <code className="mt-3 block truncate rounded-lg bg-card px-2 py-1.5 text-[11px] text-secondary-text">{task.source}.{task.resource}</code>
            </div>)}
          </div>
          {plan.caveats.length ? <div className="mt-4 rounded-xl border border-warning/25 bg-warning/10 px-4 py-3 text-xs text-secondary-text">{plan.caveats.join('；')}</div> : null}
          <div className="mt-5 flex justify-end"><button className="btn-primary inline-flex items-center gap-2" disabled={running} onClick={() => void runPlan()}>
            <Play className={`h-4 w-4 ${running ? 'animate-pulse' : ''}`} />{running ? '正在跨渠道取数并打包…' : `确认执行 ${plan.tasks.length} 个任务`}
          </button></div>
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
                <Download className="h-4 w-4" />{downloading === activeJob.jobId ? '下载中…' : '下载完整 ZIP'}
              </button>
            </div>
          </Card>
        </div> : null}

        <Card title="最近数据包" subtitle="LOCAL PACKAGE HISTORY">
          {!jobs.length ? <EmptyState icon={<FileArchive className="h-6 w-6" />} title="还没有数据包" description="描述需求并执行计划后，生成记录会出现在这里。" /> :
            <div className="divide-y divide-border/60">{jobs.map((job) => <div key={job.jobId} className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0"><div className="flex items-center gap-2"><p className="truncate text-sm font-medium text-foreground">{job.title}</p><Badge variant={job.status === 'success' ? 'success' : job.status === 'partial' ? 'warning' : 'danger'}>{job.status}</Badge>{!job.contractVersion?.startsWith('channel-scoped-v') ? <Badge variant="warning">旧版未分渠道</Badge> : <Badge variant="info">渠道独立包</Badge>}</div>
                <p className="mt-1 truncate text-xs text-secondary-text">{job.summary.rowCount} 行 · {job.summary.successCount}/{job.summary.taskCount} 数据集 · {formatTime(job.generatedAt)}</p></div>
              <button className="btn-secondary inline-flex shrink-0 items-center gap-2" disabled={downloading === job.jobId} onClick={() => void download(job)}><Download className="h-4 w-4" />下载</button>
            </div>)}</div>}
        </Card>
      </div>
    </AppPage>
  );
};

export default DataAcquisitionPage;
