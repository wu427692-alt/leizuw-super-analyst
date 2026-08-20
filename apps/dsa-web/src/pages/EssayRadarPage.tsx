import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle, Brain, CheckCircle2, CloudSun, Database, ExternalLink, FileText, GitCompareArrows,
  FlaskConical, Image, Play, RefreshCw, RotateCcw, Search, ShieldCheck, Sparkles, Square,
} from 'lucide-react';
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, Pie, PieChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import { essayRadarApi } from '../api/essayRadar';
import { AppPage, Badge, Card, Drawer, EmptyState, PageHeader, StatCard } from '../components/common';
import type { EssayAnalysis, EssayAnalysisList, EssayDailyReportList, EssayDashboard, EssayInsights, EssayStatus, EssayWordCloud } from '../types/essayRadar';

const SENTIMENT_LABELS: Record<string, string> = {
  bullish: '看多', bearish: '看空', neutral: '中性', mixed: '分歧',
};
const CATEGORY_LABELS: Record<string, string> = {
  company_research: '公司调研', broker_view: '券商观点', industry_chain: '产业链',
  macro_policy: '宏观政策', market_flow: '资金市场', event_catalyst: '事件催化',
  earnings: '业绩', risk_warning: '风险预警', rumor: '传闻', other: '其他',
};
const COLORS = ['#22d3ee', '#34d399', '#f59e0b', '#f87171', '#a78bfa'];

function sentimentBadge(value?: string | null) {
  if (value === 'bullish') return 'success';
  if (value === 'bearish') return 'danger';
  if (value === 'mixed') return 'warning';
  return 'default';
}

function formatTime(value?: string | null) {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function errorText(value: unknown, fallback: string) {
  return value instanceof Error ? value.message : fallback;
}

const EssayRadarPage = () => {
  const [dashboard, setDashboard] = useState<EssayDashboard | null>(null);
  const [insights, setInsights] = useState<EssayInsights | null>(null);
  const [status, setStatus] = useState<EssayStatus | null>(null);
  const [list, setList] = useState<EssayAnalysisList | null>(null);
  const [selected, setSelected] = useState<EssayAnalysis | null>(null);
  const [reports, setReports] = useState<EssayDailyReportList | null>(null);
  const [cloud, setCloud] = useState<EssayWordCloud | null>(null);
  const [cloudPeriod, setCloudPeriod] = useState<'day' | 'week' | 'month'>('day');
  const [cloudKind, setCloudKind] = useState<'stocks' | 'tags' | 'themes'>('stocks');
  const [query, setQuery] = useState('');
  const [sentiment, setSentiment] = useState('');
  const [category, setCategory] = useState('');
  const [historyYears, setHistoryYears] = useState<1 | 2>(1);
  const [page, setPage] = useState(1);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [listLoading, setListLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadOverview = useCallback(async (quiet = false) => {
    if (!quiet) setOverviewLoading(true);
    const results = await Promise.allSettled([
      essayRadarApi.dashboard(30),
      essayRadarApi.insights(30, 14),
      essayRadarApi.status(30),
      essayRadarApi.dailyReports(),
      essayRadarApi.wordCloud(cloudPeriod, cloudKind),
    ]);
    const setters = [setDashboard, setInsights, setStatus, setReports, setCloud] as const;
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') setters[index](result.value as never);
    });
    const failures = results.filter((result) => result.status === 'rejected');
    if (failures.length) {
      setError(`部分概览暂未更新（${failures.length}/5），已保留成功返回的数据。`);
    }
    if (!quiet) setOverviewLoading(false);
  }, [cloudKind, cloudPeriod]);

  const loadList = useCallback(async (quiet = false) => {
    if (!quiet) setListLoading(true);
    try {
      setList(await essayRadarApi.list({ days: 30, query, sentiment, category, page, pageSize: 20 }));
    } catch (caught) {
      setError(errorText(caught, '加载纪要分析流失败'));
    } finally {
      if (!quiet) setListLoading(false);
    }
  }, [category, page, query, sentiment]);

  const refreshAll = useCallback(async (quiet = false) => {
    setError(null);
    await Promise.all([loadOverview(quiet), loadList(quiet)]);
  }, [loadList, loadOverview]);

  const loading = overviewLoading || listLoading;

  useEffect(() => { void loadOverview(); }, [loadOverview]);
  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => {
    if (!status?.worker.running && !status?.progress.pending && !status?.progress.processing && !status?.mcpSync.historyBackfill?.running) return;
    const timer = window.setInterval(async () => {
      try {
        setStatus(await essayRadarApi.status(30));
      } catch {
        // Keep the last factual status on screen during a transient polling failure.
      }
    }, 10000);
    return () => window.clearInterval(timer);
  }, [status?.mcpSync.historyBackfill?.running, status?.progress.pending, status?.progress.processing, status?.worker.running]);

  const act = async (action: () => Promise<unknown>) => {
    setActionLoading(true);
    setError(null);
    try {
      await action();
      await refreshAll(true);
    } catch (caught) {
      setError(errorText(caught, '操作失败'));
    } finally {
      setActionLoading(false);
    }
  };

  const analyzeHistoryOnDemand = () => {
    const days = historyYears * 365;
    if (!window.confirm(`将近 ${historyYears} 年历史纪要加入 AI 分析队列，可能消耗较多模型额度。确认继续？`)) return;
    void act(() => essayRadarApi.backfill(days));
  };

  const sentimentData = useMemo(() => (dashboard?.sentiment ?? []).map((item) => ({
    ...item, label: SENTIMENT_LABELS[item.name] ?? item.name,
  })), [dashboard]);
  const categoryData = useMemo(() => (dashboard?.categories ?? []).slice(0, 8).map((item) => ({
    ...item, label: CATEGORY_LABELS[item.name] ?? item.name,
  })), [dashboard]);
  const progress = status?.progress;
  const totalPages = Math.max(1, Math.ceil((list?.total ?? 0) / 20));
  const yesterday = insights?.yesterday;
  const modelComparison = insights?.modelComparison;

  return (
    <AppPage>
      <div className="space-y-5">
        <PageHeader
          eyebrow="DEEPSEEK · KNOWLEDGE PLANET MCP"
          title="小作文雷达"
          description="新增纪要实时分析；1 年或 2 年历史默认只入库供检索与回测，需要时再手动加入 AI 队列。"
          actions={<>
            <a href="/essay-quant" className="btn-secondary inline-flex items-center gap-2"><FlaskConical className="h-4 w-4" />量化利用</a>
            <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => essayRadarApi.syncMcp())}>
              <RefreshCw className="h-4 w-4" />立即拉取 MCP
            </button>
            <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => essayRadarApi.backfill(30))}>
              <RotateCcw className="h-4 w-4" />回填 30 天
            </button>
            <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => status?.worker.running ? essayRadarApi.stopWorker() : essayRadarApi.startWorker())}>
              {status?.worker.running ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              {status?.worker.running ? '停止实时分析' : '启动实时分析'}
            </button>
            <button className="btn-primary inline-flex items-center gap-2" disabled={loading} onClick={() => void refreshAll()}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新
            </button>
          </>}
        />

        {error ? <div role="alert" className="rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">{error}</div> : null}

        <Card padding="sm" className="border-cyan/20 bg-cyan/5">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className={`h-2.5 w-2.5 rounded-full ${status?.worker.running ? 'animate-pulse bg-success' : 'bg-secondary-text'}`} />
              <div>
                <p className="font-medium text-foreground">{status?.worker.running ? '实时分析运行中' : '实时分析已停止'}</p>
                <p className="text-xs text-secondary-text">模型 {progress?.model ?? 'deepseek-v4-flash'} · MCP 新纪要入库后自动排队</p>
              </div>
            </div>
            <div className="min-w-[260px] flex-1 md:max-w-xl">
              <div className="mb-1.5 flex justify-between text-xs text-secondary-text"><span>近 30 天覆盖进度</span><span>{progress?.completed ?? 0} / {progress?.totalNotes ?? 0}（{progress?.coveragePercent ?? 0}%）</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-elevated"><div className="h-full rounded-full bg-primary-gradient transition-all" style={{ width: `${progress?.coveragePercent ?? 0}%` }} /></div>
              <p className="mt-1.5 text-right text-xs text-secondary-text">待处理 {progress?.pending ?? 0} · 处理中 {progress?.processing ?? 0} · 失败 {progress?.failed ?? 0}</p>
            </div>
          </div>
        </Card>

        <Card padding="sm" className="border-success/20 bg-success/5">
          <div className="flex flex-wrap items-center justify-between gap-4"><div className="flex items-center gap-3"><span className={`h-2.5 w-2.5 rounded-full ${status?.mcpSync.running ? 'animate-pulse bg-success' : 'bg-warning'}`} /><div><p className="font-medium text-foreground">{status?.mcpSync.running ? '知识星球 MCP 增量同步运行中' : '知识星球 MCP 增量同步已停止'}</p><p className="text-xs text-secondary-text">直接 MCP → SQLite · 附件仅存远端链接 · 每 {status?.mcpSync.pollSeconds ?? 30} 秒 · 最近成功 {formatTime(status?.mcpSync.groups[0]?.lastSuccessAt)}</p></div></div><button className="btn-secondary" disabled={actionLoading || !status?.mcpSync.available} onClick={() => void act(() => status?.mcpSync.running ? essayRadarApi.stopMcpWorker() : essayRadarApi.startMcpWorker())}>{status?.mcpSync.running ? '停止 MCP 同步' : '启动 MCP 同步'}</button></div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-success/20 bg-card/60 px-3 py-3 text-xs">
            <div><p className="font-medium text-foreground">历史纪要库</p><p className="mt-1 text-secondary-text">可回填 1 年或 2 年；默认只入库供检索与回测，不创建 AI 任务。</p>{status?.mcpSync.historyBackfill?.running ? <p className="mt-1 text-success">正在回填近 {(status.mcpSync.historyBackfill.lookbackDays ?? 365) / 365} 年历史…</p> : status?.mcpSync.historyBackfill?.error ? <p className="mt-1 text-danger">{status.mcpSync.historyBackfill.error}</p> : status?.mcpSync.historyBackfill?.result ? <p className="mt-1 text-secondary-text">最近完成：新增 {Number(status.mcpSync.historyBackfill.result.created ?? 0)} 条、更新 {Number(status.mcpSync.historyBackfill.result.updated ?? 0)} 条{Number(status.mcpSync.historyBackfill.result.incompleteGroups ?? 0) > 0 ? `；${Number(status.mcpSync.historyBackfill.result.incompleteGroups)} 个星球受分页上限影响，尚未完整覆盖` : '；已覆盖所选时间范围'}</p> : null}</div>
            <div className="flex flex-wrap items-center gap-2"><select aria-label="知识星球历史范围" value={historyYears} onChange={(event) => setHistoryYears(Number(event.target.value) as 1 | 2)} className="h-9 rounded border border-border bg-card px-3"><option value={1}>近 1 年</option><option value={2}>近 2 年</option></select><button className="btn-secondary" disabled={actionLoading || !status?.mcpSync.available || status?.mcpSync.historyBackfill?.running} onClick={() => void act(() => essayRadarApi.backfillMcpHistory(historyYears))}><Database className="mr-1 inline h-4 w-4" />只同步入库</button><button className="btn-secondary" disabled={actionLoading} onClick={analyzeHistoryOnDemand}><Brain className="mr-1 inline h-4 w-4" />按需 AI 分析</button></div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2">{status?.mcpSync.groups.map((group) => <div key={group.groupId} className="rounded-lg border border-border/50 bg-card/50 px-3 py-2 text-xs"><div className="flex justify-between gap-3"><span className="font-medium text-foreground">{group.groupName}</span><Badge variant={group.lastStatus === 'success' ? 'success' : group.lastStatus === 'failed' ? 'danger' : 'default'}>{group.lastStatus}</Badge></div><p className="mt-1 text-secondary-text">本轮 {group.lastReceived} 条 · 入库 {group.lastSaved} 条 · 附件按需查看 · 游标 {formatTime(group.lastTopicAt)}</p>{group.lastError ? <p className="mt-1 text-danger">{group.lastError}</p> : null}</div>)}</div>
        </Card>

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-6">
          <StatCard label="昨日新增" value={(yesterday?.analyzedCount ?? 0).toLocaleString()} hint={yesterday?.date || '前一交易日'} icon={<FileText className="h-5 w-5" />} tone="primary" />
          <StatCard label="高重要度信号" value={(yesterday?.highImportanceCount ?? 0).toLocaleString()} hint="重要度 ≥ 80" icon={<Sparkles className="h-5 w-5" />} tone="warning" />
          <StatCard label="证据覆盖" value={`${yesterday?.evidenceCoveragePercent ?? 0}%`} hint="含原文论据" icon={<ShieldCheck className="h-5 w-5" />} tone="success" />
          <StatCard label="低置信记录" value={(yesterday?.lowConfidenceCount ?? 0).toLocaleString()} hint="置信度 < 0.5" icon={<AlertTriangle className="h-5 w-5" />} tone="warning" />
          <StatCard label="传闻记录" value={(yesterday?.rumorCount ?? 0).toLocaleString()} hint="与事实分开展示" icon={<GitCompareArrows className="h-5 w-5" />} />
          <StatCard label="日报模型" value={`${insights?.coverage.completedReportModels ?? 0}/${insights?.coverage.configuredModels.length ?? 0}`} hint="逐模型独立生成" icon={<Brain className="h-5 w-5" />} tone="primary" />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.7fr_0.9fr]">
          <Card title="14 天提及量与情绪趋势" subtitle="REAL NOTES · SHANGHAI TIME" className="min-w-0">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-xs text-secondary-text"><span>柱形为当日完成分析的新增纪要；折线为平均重要度</span><span>最新数据 {formatTime(insights?.latestDataAt)}</span></div>
            <div className="h-80"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 840, height: 320 }}><ComposedChart data={insights?.trend ?? []}><CartesianGrid strokeDasharray="3 3" opacity={0.14} /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fill: 'currentColor', fontSize: 11 }} /><YAxis yAxisId="count" tick={{ fill: 'currentColor', fontSize: 11 }} /><YAxis yAxisId="score" orientation="right" domain={[0, 100]} tick={{ fill: 'currentColor', fontSize: 11 }} /><Tooltip /><Area yAxisId="count" type="monotone" dataKey="total" name="纪要数" fill="#0ea5e9" stroke="#22d3ee" fillOpacity={0.2} /><Line yAxisId="score" type="monotone" dataKey="averageImportance" name="平均重要度" stroke="#f59e0b" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>
            <div className="mt-3 grid grid-cols-4 gap-2 text-center text-xs"><span className="text-success">看多 {(insights?.trend ?? []).reduce((sum, item) => sum + item.bullish, 0)}</span><span className="text-danger">看空 {(insights?.trend ?? []).reduce((sum, item) => sum + item.bearish, 0)}</span><span className="text-secondary-text">中性 {(insights?.trend ?? []).reduce((sum, item) => sum + item.neutral, 0)}</span><span className="text-warning">分歧 {(insights?.trend ?? []).reduce((sum, item) => sum + item.mixed, 0)}</span></div>
          </Card>

          <Card title="模型共识与分歧" subtitle={modelComparison?.reportDate || '等待日报'}>
            <div className="mb-4 grid grid-cols-2 gap-3"><div className="border-l-2 border-success px-3"><p className="text-xs text-secondary-text">已完成模型</p><p className="mt-1 font-mono text-xl text-success">{modelComparison?.reports.filter((item) => item.status === 'completed').length ?? 0}</p></div><div className="border-l-2 border-warning px-3"><p className="text-xs text-secondary-text">配置模型</p><p className="mt-1 font-mono text-xl text-warning">{insights?.coverage.configuredModels.length ?? 0}</p></div></div>
            <div className="space-y-4"><section><p className="mb-2 flex items-center gap-2 text-sm font-medium text-success"><CheckCircle2 className="h-4 w-4" />模型共识</p>{modelComparison?.consensus.slice(0, 5).map((item) => <p key={item.text} className="mt-2 border-l border-success/30 pl-3 text-xs leading-5 text-secondary-text">{item.text}<span className="ml-2 font-mono text-success">{item.modelCount} 模型</span></p>)}{!modelComparison?.consensus.length ? <p className="text-xs text-secondary-text">当前仅有单模型或尚无可比较日报。</p> : null}</section><section><p className="mb-2 flex items-center gap-2 text-sm font-medium text-warning"><GitCompareArrows className="h-4 w-4" />观点分歧</p>{modelComparison?.divergences.slice(0, 5).map((item) => <p key={item.text} className="mt-2 border-l border-warning/30 pl-3 text-xs leading-5 text-secondary-text">{item.text}<span className="ml-2 font-mono text-warning">{item.modelCount} 模型</span></p>)}{!modelComparison?.divergences.length ? <p className="text-xs text-secondary-text">暂无跨模型分歧可比样本。</p> : null}</section></div>
          </Card>
        </div>

        <Card title="关注股信号" subtitle="华懋科技 · 胜宏科技 · 日 / 周 / 月">
          <div className="grid gap-0 overflow-hidden rounded-xl border border-border/60 lg:grid-cols-2">{insights?.watchlist.map((stock, index) => <section key={stock.symbol} className={`min-w-0 p-4 ${index ? 'border-t border-border/60 lg:border-l lg:border-t-0' : ''}`}><div className="flex items-start justify-between gap-4"><div><p className="text-lg font-semibold text-foreground">{stock.name}</p><p className="font-mono text-xs text-cyan">{stock.symbol}</p></div><div className="text-right"><p className="font-mono text-2xl text-cyan">{stock.mentionCount}</p><p className="text-[10px] text-secondary-text">近 30 天提及</p></div></div><div className="mt-4 grid grid-cols-3 border-y border-border/50 py-3 text-center text-xs"><div><p className="font-mono text-base text-foreground">{stock.dayMentions}</p><p className="text-secondary-text">今日</p></div><div><p className="font-mono text-base text-foreground">{stock.weekMentions}</p><p className="text-secondary-text">近 7 日</p></div><div><p className="font-mono text-base text-foreground">{stock.monthMentions}</p><p className="text-secondary-text">近 30 日</p></div></div><div className="mt-3 h-20"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 500, height: 80 }}><ComposedChart data={stock.trend}><Area type="monotone" dataKey="total" fill={index ? '#34d399' : '#22d3ee'} stroke={index ? '#34d399' : '#22d3ee'} fillOpacity={0.18} /><Tooltip /><XAxis dataKey="date" hide /><YAxis hide /></ComposedChart></ResponsiveContainer></div><p className="mt-3 line-clamp-3 text-sm leading-6 text-secondary-text">{stock.latestThesis || '近 30 天暂无小作文提及。'}</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><div><p className="text-xs font-medium text-success">催化剂</p>{stock.catalysts.slice(0, 3).map((item) => <p key={item} className="mt-1 truncate text-xs text-secondary-text">• {item}</p>)}</div><div><p className="text-xs font-medium text-danger">风险</p>{stock.risks.slice(0, 3).map((item) => <p key={item} className="mt-1 truncate text-xs text-secondary-text">• {item}</p>)}</div></div></section>)}</div>
        </Card>

        <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
          <Card title="证据质量矩阵" subtitle="近 30 天 · 不把观点当事实">
            <div className="space-y-5"><section><p className="mb-3 text-xs text-secondary-text">信源质量</p>{insights?.sourceQuality.map((item) => { const total = insights.coverage.analyzedCount || 1; return <div key={item.name} className="mt-2 grid grid-cols-[80px_1fr_56px] items-center gap-3 text-xs"><span className="text-foreground">{item.name}</span><div className="h-2 overflow-hidden rounded bg-elevated"><div className="h-full bg-cyan" style={{ width: `${item.count / total * 100}%` }} /></div><span className="text-right font-mono text-secondary-text">{item.count}</span></div>; })}</section><section><p className="mb-3 text-xs text-secondary-text">信息性质</p>{insights?.informationTypes.map((item) => { const total = insights.coverage.analyzedCount || 1; return <div key={item.name} className="mt-2 grid grid-cols-[120px_1fr_56px] items-center gap-3 text-xs"><span className="truncate text-foreground">{item.name}</span><div className="h-2 overflow-hidden rounded bg-elevated"><div className={`h-full ${item.name === 'market_rumor' ? 'bg-danger' : item.name === 'fact' ? 'bg-success' : 'bg-warning'}`} style={{ width: `${item.count / total * 100}%` }} /></div><span className="text-right font-mono text-secondary-text">{item.count}</span></div>; })}</section></div>
          </Card>
          <Card title="高信息增量信号" subtitle="昨日 · 新颖度优先，仍需原文核验">
            <div className="divide-y divide-border/50">{insights?.highNoveltySignals.map((item) => <button key={item.topicId} onClick={() => setSelected(item)} className="grid w-full gap-2 py-3 text-left sm:grid-cols-[88px_1fr_96px] sm:items-center"><div><p className="font-mono text-lg text-cyan">{item.noveltyScore}</p><p className="text-[10px] text-secondary-text">信息增量</p></div><div className="min-w-0"><p className="truncate text-sm font-medium text-foreground">{item.note.title}</p><p className="mt-1 line-clamp-2 text-xs leading-5 text-secondary-text">{item.summary}</p></div><div className="text-left sm:text-right"><Badge variant={sentimentBadge(item.sentiment)}>{SENTIMENT_LABELS[item.sentiment ?? ''] ?? '中性'}</Badge><p className="mt-1 text-[10px] text-secondary-text">置信 {Math.round((item.confidenceScore ?? 0) * 100)}%</p></div></button>)}{!insights?.highNoveltySignals.length ? <p className="py-8 text-center text-sm text-secondary-text">昨日暂无新增高增量信号。</p> : null}</div>
          </Card>
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
          <Card title="多模型每日小作文报告" subtitle="PREVIOUS DAY · ONE REPORT PER MODEL">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <p className="text-xs text-secondary-text">上海时区每日 {status?.dailyReportWorker.runHourShanghai ?? 7}:00 后，分别汇总前一日新增小作文。</p>
              <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => essayRadarApi.runDailyReports())}><Brain className="h-4 w-4" />立即生成昨日报告</button>
            </div>
            {!reports?.items.length ? <EmptyState title="暂无每日报告" description="点击立即生成，或等待每日任务自动运行。" icon={<Brain className="h-7 w-7" />} /> : null}
            <div className="space-y-3">{reports?.items.slice(0, 5).map((item) => <details key={`${item.reportDate}-${item.model}`} className="rounded-xl border border-border/60 bg-card/50 p-4" open={item === reports.items[0]}><summary className="cursor-pointer list-none"><div className="flex flex-wrap items-center justify-between gap-2"><div><p className="font-medium text-foreground">{item.reportDate} · {item.model}</p><p className="mt-1 text-xs text-secondary-text">汇总 {item.sourceCount} 篇 · {item.totalTokens.toLocaleString()} tokens</p></div><Badge variant={item.status === 'completed' ? 'success' : item.status === 'failed' ? 'danger' : 'warning'}>{item.status}</Badge></div></summary>{item.report ? <div className="mt-4 space-y-4 border-t border-border/50 pt-4"><div><p className="label-uppercase">核心结论</p><p className="mt-2 text-sm leading-7 text-foreground">{item.report.executiveSummary}</p></div><div><p className="label-uppercase">市场叙事</p><p className="mt-2 text-sm text-secondary-text">{item.report.marketRegime || '—'}</p></div><div className="grid gap-3 md:grid-cols-2"><section className="rounded-lg bg-success/5 p-3"><p className="text-sm font-medium text-success">新增信号</p>{item.report.novelSignals?.map((value) => <p key={value} className="mt-2 text-xs leading-5 text-secondary-text">• {value}</p>)}</section><section className="rounded-lg bg-danger/5 p-3"><p className="text-sm font-medium text-danger">风险与分歧</p>{[...(item.report.riskWatch ?? []), ...(item.report.divergences ?? [])].slice(0, 8).map((value) => <p key={value} className="mt-2 text-xs leading-5 text-secondary-text">• {value}</p>)}</section></div></div> : item.errorMessage ? <p className="mt-3 text-sm text-danger">{item.errorMessage}</p> : null}</details>)}</div>
          </Card>

          <Card title="个股与主题词云" subtitle="DAILY · WEEKLY · MONTHLY">
            <div className="mb-4 flex flex-wrap gap-2">{(['day', 'week', 'month'] as const).map((value) => <button key={value} onClick={() => setCloudPeriod(value)} className={cloudPeriod === value ? 'btn-primary' : 'btn-secondary'}>{value === 'day' ? '每日' : value === 'week' ? '每周' : '每月'}</button>)}<span className="mx-1 w-px bg-border" />{(['stocks', 'tags', 'themes'] as const).map((value) => <button key={value} onClick={() => setCloudKind(value)} className={cloudKind === value ? 'btn-primary' : 'btn-secondary'}>{value === 'stocks' ? '股票' : value === 'tags' ? '标签' : '主题'}</button>)}</div>
            <p className="mb-3 text-xs text-secondary-text">{cloud?.startDate} 至 {cloud?.endDate} · {cloud?.sourceCount ?? 0} 篇样本 · 数字为提及次数，箭头为环比变化</p>
            <div className="flex min-h-72 flex-wrap content-center items-center justify-center gap-x-4 gap-y-3 rounded-xl border border-border/50 bg-elevated/30 p-5">{cloud?.items.slice(0, 45).map((item, index) => { const max = cloud.items[0]?.count || 1; const size = 12 + Math.round((item.count / max) * 24); return <button key={item.name} aria-label={`${item.name}：本期 ${item.count}，上期 ${item.previousCount}`} onClick={() => { setQuery(item.name); setPage(1); }} className="transition hover:scale-110 hover:text-cyan" style={{ fontSize: `${size}px`, opacity: Math.max(0.48, 1 - index * 0.012), color: item.change > 0 ? '#34d399' : item.change < 0 ? '#f87171' : undefined }}><CloudSun className="mr-1 inline h-3 w-3" />{item.name}<sup className="ml-1 text-[10px]">{item.count}{item.change > 0 ? '↑' : item.change < 0 ? '↓' : ''}</sup></button>; })}</div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="观点情绪" subtitle="SENTIMENT" className="min-w-0">
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 600, height: 256 }}><PieChart><Pie data={sentimentData} dataKey="count" nameKey="label" innerRadius={52} outerRadius={86} paddingAngle={3}>{sentimentData.map((item, index) => <Cell key={item.name} fill={COLORS[index % COLORS.length]} />)}</Pie><Tooltip /></PieChart></ResponsiveContainer>
            </div>
            <div className="flex flex-wrap justify-center gap-3">{sentimentData.map((item, index) => <span key={item.name} className="text-xs text-secondary-text"><i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: COLORS[index % COLORS.length] }} />{item.label} {item.count}</span>)}</div>
          </Card>
          <Card title="内容类型" subtitle="CATEGORY" className="min-w-0">
            <div className="h-72"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 600, height: 288 }}><BarChart data={categoryData} layout="vertical" margin={{ left: 12 }}><CartesianGrid strokeDasharray="3 3" opacity={0.16} /><XAxis type="number" hide /><YAxis type="category" dataKey="label" width={72} tick={{ fill: 'currentColor', fontSize: 12 }} /><Tooltip /><Bar dataKey="count" fill="#22d3ee" radius={[0, 6, 6, 0]} /></BarChart></ResponsiveContainer></div>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1.05fr_1.95fr]">
          <Card title="热门标签" subtitle="TOP TAGS">
            <div className="flex flex-wrap gap-2">{dashboard?.topTags.map((tag, index) => <button key={tag.name} onClick={() => { setQuery(tag.name); setPage(1); }} className="rounded-full border border-cyan/20 bg-cyan/8 px-3 py-1.5 text-sm text-cyan transition hover:bg-cyan/15">#{tag.name} <span className="text-secondary-text">{tag.count}</span>{index < 3 ? ' ↗' : ''}</button>)}</div>
          </Card>
          <Card title="高频股票" subtitle="STOCK HEAT">
            <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs uppercase tracking-wider text-secondary-text"><tr><th className="pb-3">标的</th><th className="pb-3">提及</th><th className="pb-3">看多 / 看空</th><th className="pb-3">重要度</th></tr></thead><tbody>{dashboard?.topStocks.slice(0, 10).map((stock) => <tr key={stock.key} className="border-t border-border/50"><td className="py-3"><p className="font-medium text-foreground">{stock.name || stock.tsCode}</p><p className="text-xs text-secondary-text">{stock.tsCode}</p></td><td className="py-3 font-mono text-cyan">{stock.mentionCount}</td><td className="py-3"><span className="text-success">{stock.bullish}</span><span className="mx-2 text-secondary-text">/</span><span className="text-danger">{stock.bearish}</span></td><td className="py-3">{stock.averageImportance}</td></tr>)}</tbody></table></div>
          </Card>
        </div>

        <Card title="纪要分析流" subtitle="ESSAY FEED">
          <div className="mb-4 grid gap-3 md:grid-cols-[1fr_160px_180px]">
            <label className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-secondary-text" /><input aria-label="搜索纪要" className="input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent pl-9 pr-4 text-sm outline-none" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="搜索标题、摘要或标签" /></label>
            <select aria-label="筛选情绪" className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm outline-none" value={sentiment} onChange={(event) => { setSentiment(event.target.value); setPage(1); }}><option value="">全部情绪</option>{Object.entries(SENTIMENT_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
            <select aria-label="筛选类型" className="input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 text-sm outline-none" value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部类型</option>{Object.entries(CATEGORY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          </div>
          {!loading && !list?.items.length ? <EmptyState title="暂无匹配纪要" description="调整筛选条件，或启动 30 天回填。" icon={<Database className="h-7 w-7" />} /> : null}
          <div className="space-y-3">{list?.items.map((item) => <button key={item.topicId} className="w-full rounded-xl border border-border/60 bg-card/60 p-4 text-left transition hover:border-cyan/30 hover:bg-hover" onClick={() => setSelected(item)}><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><div className="mb-2 flex flex-wrap items-center gap-2"><Badge variant={sentimentBadge(item.sentiment)}>{SENTIMENT_LABELS[item.sentiment ?? ''] ?? item.status}</Badge><Badge>{CATEGORY_LABELS[item.primaryCategory ?? ''] ?? item.primaryCategory}</Badge><span className="text-xs text-secondary-text">{formatTime(item.note.createdAt)}</span></div><h3 className="font-medium text-foreground">{item.note.title || '无标题纪要'}</h3><p className="mt-2 line-clamp-2 text-sm leading-6 text-secondary-text">{item.summary || item.errorMessage || '等待分析'}</p><div className="mt-3 flex flex-wrap gap-1.5">{item.tags.slice(0, 6).map((tag) => <span key={tag} className="rounded bg-elevated px-2 py-1 text-xs text-secondary-text">#{tag}</span>)}</div></div><div className="min-w-14 text-right"><p className="font-mono text-2xl font-semibold text-cyan">{item.importanceScore ?? '—'}</p><p className="text-[10px] uppercase tracking-wider text-secondary-text">importance</p></div></div></button>)}</div>
          {list && list.total > 0 ? <div className="mt-4 flex items-center justify-between text-sm text-secondary-text"><span>共 {list.total.toLocaleString()} 篇</span><div className="flex items-center gap-2"><button className="btn-secondary" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>{page} / {totalPages}</span><button className="btn-secondary" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div> : null}
        </Card>
      </div>

      <Drawer isOpen={Boolean(selected)} onClose={() => setSelected(null)} title={selected?.note.title || '纪要详情'}>
        {selected ? <div className="space-y-6">
          <div className="flex flex-wrap gap-2"><Badge variant={sentimentBadge(selected.sentiment)}>{SENTIMENT_LABELS[selected.sentiment ?? ''] ?? selected.status}</Badge><Badge>{CATEGORY_LABELS[selected.primaryCategory ?? ''] ?? selected.primaryCategory}</Badge><Badge variant="info">重要度 {selected.importanceScore ?? '—'}</Badge><Badge>增量 {selected.noveltyScore ?? 0}</Badge><Badge>信源 {selected.sourceQuality || 'unknown'}</Badge></div>
          {selected.note.images.length ? <section><p className="label-uppercase">原帖图片</p><p className="mt-1 text-xs text-secondary-text">不自动加载、不保存本地；点击后临时获取知识星球链接。</p><div className="mt-3 grid grid-cols-2 gap-3">{selected.note.images.map((item, index) => <a key={item.imageId || index} href={item.viewUrl} target="_blank" rel="noreferrer" className="flex h-28 flex-col items-center justify-center gap-2 rounded-xl border border-border/60 bg-elevated text-secondary-text hover:border-cyan/30 hover:text-cyan"><Image className="h-6 w-6" /><span className="inline-flex items-center gap-1 text-xs">查看图片 {index + 1}<ExternalLink className="h-3.5 w-3.5" /></span></a>)}</div></section> : null}
          {selected.note.files.length ? <section><p className="label-uppercase">原帖附件</p><p className="mt-1 text-xs text-secondary-text">文件不会在同步阶段下载，点击时直接打开远端地址。</p><div className="mt-2 space-y-2">{selected.note.files.map((item) => <a key={item.fileId} href={item.viewUrl} target="_blank" rel="noreferrer" className="flex items-center justify-between rounded-lg border border-border/60 px-3 py-2 text-sm text-foreground hover:border-cyan/30"><span className="min-w-0 truncate">{item.name || item.fileId}</span><span className="ml-3 inline-flex items-center gap-1 text-cyan"><ExternalLink className="h-4 w-4" />{item.size ? `${(item.size / 1024 / 1024).toFixed(1)} MB · 查看` : '查看文件'}</span></a>)}</div></section> : null}
          <section><p className="label-uppercase">AI 深度摘要</p><p className="mt-2 leading-7 text-foreground">{selected.summary}</p></section>
          <section><p className="label-uppercase">关键观点与原文证据</p><div className="mt-2 space-y-2">{selected.evidence?.map((item) => <div key={`${item.claim}-${item.evidence}`} className="rounded-lg border border-border/60 p-3 text-sm"><div className="flex justify-between gap-3"><p className="font-medium text-foreground">{item.claim}</p><Badge>{item.strength}</Badge></div><p className="mt-2 leading-6 text-secondary-text">依据：{item.evidence}</p></div>)}</div></section>
          <div className="grid gap-4 sm:grid-cols-2"><section className="rounded-xl border border-success/20 bg-success/5 p-4"><p className="font-medium text-success">盈利影响</p><p className="mt-2 text-sm leading-6 text-secondary-text">{selected.earningsImpact}</p></section><section className="rounded-xl border border-cyan/20 bg-cyan/5 p-4"><p className="font-medium text-cyan">估值影响</p><p className="mt-2 text-sm leading-6 text-secondary-text">{selected.valuationImpact}</p></section></div>
          <div className="grid gap-4 sm:grid-cols-2"><section className="rounded-xl border border-success/20 bg-success/5 p-4"><p className="font-medium text-success">催化剂</p>{selected.catalysts.map((item) => <p key={item} className="mt-2 text-sm text-secondary-text">• {item}</p>)}</section><section className="rounded-xl border border-danger/20 bg-danger/5 p-4"><p className="font-medium text-danger">风险 / 矛盾 / 证伪条件</p>{[...selected.risks, ...(selected.contradictions ?? []), ...(selected.falsificationConditions ?? [])].map((item) => <p key={item} className="mt-2 text-sm text-secondary-text">• {item}</p>)}</section></div>
          <section><p className="label-uppercase">后续验证清单</p><ul className="mt-2 space-y-2 text-sm text-secondary-text">{selected.monitoringPoints?.map((item) => <li key={item}>• {item}</li>)}</ul></section>
          <section><p className="label-uppercase">提及股票</p><div className="mt-2 space-y-2">{selected.stockMentions.map((stock) => <div key={`${stock.tsCode}-${stock.name}`} className="rounded-lg bg-elevated p-3 text-sm"><div className="flex justify-between"><span className="font-medium text-foreground">{stock.name || stock.tsCode}</span><span className="text-cyan">{stock.tsCode}</span></div><p className="mt-1 text-secondary-text">{stock.rationale}</p></div>)}</div></section>
          <details className="rounded-xl border border-border/60 p-4"><summary className="cursor-pointer font-medium text-foreground">查看原文</summary><p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-secondary-text">{selected.note.content}</p></details>
        </div> : null}
      </Drawer>
    </AppPage>
  );
};

export default EssayRadarPage;
