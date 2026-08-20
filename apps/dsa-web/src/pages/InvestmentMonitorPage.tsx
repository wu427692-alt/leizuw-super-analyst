import { useCallback, useDeferredValue, useEffect, useMemo, useState } from 'react';
import {
  Archive, CheckCircle2, ChevronRight, CircleAlert, Cloud, DatabaseZap, Download,
  ExternalLink, FileCheck2, FileText, Filter, Play, RefreshCw, Search, ShieldCheck, Square,
} from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, Badge, Drawer, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import type {
  AnnouncementCategory, CloudKnowledgeStatus, InvestmentMonitorDashboard, MonitorEvent,
  MonitorEventList, MonitoringSource, MonitorStatus,
} from '../types/investmentMonitor';

type ChannelTab = { key: string; label: string; channel?: string; evidenceLevel?: string };
type Evidence = {
  evidenceLevel: string; channel: string; provider?: string; originApis?: string[];
  hasOriginalLink?: boolean; contentNature?: string; dataTimestamp?: string;
};

const CHANNELS: ChannelTab[] = [
  { key: 'facts', label: '全部事实', evidenceLevel: 'factual' },
  { key: 'company', label: '公司公告', channel: 'company', evidenceLevel: 'factual' },
  { key: 'news', label: '财经快讯', channel: 'news', evidenceLevel: 'factual' },
  { key: 'research', label: '券商研报', channel: 'research', evidenceLevel: 'factual' },
  { key: 'institution', label: '机构调研', channel: 'institution', evidenceLevel: 'factual' },
  { key: 'capital', label: '资金席位', channel: 'capital', evidenceLevel: 'factual' },
  { key: 'ownership', label: '股权事项', channel: 'ownership,governance', evidenceLevel: 'factual' },
  { key: 'fundamental', label: '财务业绩', channel: 'fundamental', evidenceLevel: 'factual' },
  { key: 'enterprise', label: '企业风险', channel: 'enterprise', evidenceLevel: 'factual' },
  { key: 'essay', label: '知识星球', channel: 'essay', evidenceLevel: 'unverified' },
];

const EVIDENCE_LABELS: Record<string, { label: string; tone: 'success' | 'info' | 'warning' | 'default' }> = {
  official: { label: '官方披露', tone: 'success' },
  licensed: { label: '授权数据', tone: 'info' },
  reported: { label: '媒体报道', tone: 'default' },
  unverified: { label: '待核验观点', tone: 'warning' },
};

const SOURCE_GROUPS = [
  { key: 'official', title: '官方披露', description: '公告与法定披露' },
  { key: 'licensed', title: '授权数据', description: '行情、财务与企业数据' },
  { key: 'reported', title: '研究与新闻', description: '媒体和券商发布记录' },
  { key: 'unverified', title: '另类情报', description: '观点型内容，需二次核验' },
];

function time(value?: string | null, withDate = true) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', withDate
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' });
}

function localDate(offsetDays = 0) {
  const value = new Date(); value.setDate(value.getDate() + offsetDays);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function evidenceOf(event: MonitorEvent): Evidence {
  const raw = (event.metrics._evidence ?? event.metrics.evidence) as Partial<Evidence> | undefined;
  if (raw?.evidenceLevel) return raw as Evidence;
  if (event.sourceKey === 'cninfo.announcements') return { evidenceLevel: 'official', channel: 'company' };
  if (event.sourceKey === 'zsxq.essays') return { evidenceLevel: 'unverified', channel: 'essay' };
  if (event.sourceKey.startsWith('feeds.')) return { evidenceLevel: 'reported', channel: 'news' };
  return { evidenceLevel: 'licensed', channel: 'other' };
}

function cleanMetrics(event: MonitorEvent) {
  const result = { ...event.metrics };
  delete result._evidence; delete result.evidence;
  return result;
}

function sourceLevel(source: MonitoringSource) {
  return source.config?.evidenceLevel || (source.sourceKey === 'cninfo.announcements' ? 'official'
    : source.sourceKey === 'zsxq.essays' ? 'unverified'
      : source.category === 'news' || source.category === 'research' ? 'reported' : 'licensed');
}

const InvestmentMonitorPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const [dashboard, setDashboard] = useState<InvestmentMonitorDashboard | null>(null);
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [events, setEvents] = useState<MonitorEventList | null>(null);
  const [cloud, setCloud] = useState<CloudKnowledgeStatus | null>(null);
  const [announcements, setAnnouncements] = useState<MonitorEventList | null>(null);
  const [announcementCategories, setAnnouncementCategories] = useState<AnnouncementCategory[]>([]);
  const [announcementStart, setAnnouncementStart] = useState(localDate(-7));
  const [announcementEnd, setAnnouncementEnd] = useState(localDate());
  const [announcementCategory, setAnnouncementCategory] = useState('');
  const [activeChannel, setActiveChannel] = useState('facts');
  const [sourceKey, setSourceKey] = useState('');
  const [symbol, setSymbol] = useState('');
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [selected, setSelected] = useState<MonitorEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tab = CHANNELS.find((item) => item.key === activeChannel) ?? CHANNELS[0];
  const captureError = useCallback((caught: unknown) => {
    setError(caught instanceof Error ? caught.message : '加载投资情报失败');
  }, []);
  const loadOverview = useCallback(async (includeCloud = false) => {
    const requests: Promise<unknown>[] = [
      investmentMonitorApi.dashboard(7).then(setDashboard),
      investmentMonitorApi.status().then(setStatus),
    ];
    if (includeCloud) {
      requests.push(investmentMonitorApi.cloudStatus().then(setCloud));
      requests.push(investmentMonitorApi.announcementCategories().then((value) => setAnnouncementCategories(value.items)));
    }
    await Promise.all(requests);
  }, []);
  const loadEvents = useCallback(async () => {
    const value = await investmentMonitorApi.events({ days: sourceKey ? 3650 : 7,
      query: deferredQuery || undefined, symbol: symbol || undefined, sourceKey: sourceKey || undefined,
      channel: tab.channel, evidenceLevel: tab.evidenceLevel, pageSize: 100 });
    setEvents(value);
  }, [deferredQuery, sourceKey, symbol, tab.channel, tab.evidenceLevel]);
  const loadAnnouncements = useCallback(async () => {
    const value = await investmentMonitorApi.announcements({ startDate: announcementStart,
      endDate: announcementEnd, symbol: symbol || undefined, category: announcementCategory || undefined });
    setAnnouncements(value);
  }, [announcementCategory, announcementEnd, announcementStart, symbol]);
  const refreshAll = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    try { await Promise.all([loadOverview(true), loadEvents(), loadAnnouncements()]); }
    catch (caught) { captureError(caught); }
    finally { if (showLoading) setLoading(false); }
  }, [captureError, loadAnnouncements, loadEvents, loadOverview]);

  useEffect(() => { void loadOverview(true).catch(captureError); }, [captureError, loadOverview]);
  useEffect(() => {
    setLoading(true);
    void loadEvents().catch(captureError).finally(() => setLoading(false));
  }, [captureError, loadEvents]);
  useEffect(() => { void loadAnnouncements().catch(captureError); }, [captureError, loadAnnouncements]);
  useEffect(() => {
    const eventId = Number(searchParams.get('event'));
    if (!Number.isInteger(eventId) || eventId <= 0) return;
    let cancelled = false;
    investmentMonitorApi.event(eventId)
      .then((item) => { if (!cancelled) setSelected(item); })
      .catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : '原文加载失败'); });
    return () => { cancelled = true; };
  }, [searchParams]);
  useEffect(() => {
    if (!status?.worker.running) return;
    const timer = window.setInterval(() => {
      void Promise.all([loadOverview(false), loadEvents()]).catch(captureError);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [captureError, loadEvents, loadOverview, status?.worker.running]);

  const act = async (action: () => Promise<unknown>) => {
    setActionLoading(true); setError(null);
    try { await action(); await refreshAll(false); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '操作失败'); }
    finally { setActionLoading(false); }
  };

  const sourceGroups = useMemo(() => SOURCE_GROUPS.map((group) => ({
    ...group, items: (status?.sources.items ?? []).filter((source) => sourceLevel(source) === group.key),
  })), [status?.sources.items]);

  const syncAnnouncements = () => act(() => investmentMonitorApi.syncAnnouncements({
    startDate: announcementStart, endDate: announcementEnd, symbols: symbol ? [symbol] : [],
    categories: announcementCategory ? [announcementCategory] : [], maxPages: 20,
  }));
  const exportAnnouncements = () => act(async () => saveBlob(await investmentMonitorApi.exportAnnouncements({
    startDate: announcementStart, endDate: announcementEnd, symbol: symbol || undefined,
    category: announcementCategory || undefined,
  }), `上市公司公告_${announcementStart}_${announcementEnd}.xlsx`));
  const packageAnnouncements = () => act(async () => saveBlob(await investmentMonitorApi.packageAnnouncements(
    (announcements?.items ?? []).slice(0, 20).map((item) => item.id), true,
  ), `上市公司公告_PDF_TXT_${announcementStart}_${announcementEnd}.zip`));
  const openEvent = (event: MonitorEvent) => {
    if (event.url) {
      window.open(event.url, '_blank', 'noopener,noreferrer');
      return;
    }
    setSelected(event);
  };
  const closeEvent = () => {
    setSelected(null);
    if (!searchParams.has('event')) return;
    const next = new URLSearchParams(searchParams);
    next.delete('event');
    setSearchParams(next, { replace: true });
  };

  return (
    <AppPage>
      <div className="min-w-0 space-y-4">
        <header className="flex flex-col justify-between gap-4 lg:flex-row lg:items-center">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-foreground">事实流水</h1>
            <p className="mt-1 text-sm text-secondary-text">按渠道检索全部原始事件；来源、原始时间与入库时间可追溯。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => status?.worker.running ? investmentMonitorApi.stopWorker() : investmentMonitorApi.startWorker())}>
              {status?.worker.running ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}{status?.worker.running ? '停止监控' : '启动监控'}
            </button>
            <button className="btn-secondary inline-flex items-center gap-2" disabled={actionLoading} onClick={() => void act(() => investmentMonitorApi.sync())}><DatabaseZap className="h-4 w-4" />同步事实数据</button>
            <button className="btn-primary inline-flex items-center gap-2" disabled={loading} onClick={() => void refreshAll()}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新</button>
          </div>
        </header>

        <InvestmentMonitorNav />

        {error ? <div role="alert" className="border border-danger/30 bg-danger/8 px-4 py-3 text-sm text-danger">{error}</div> : null}

        <section className="grid grid-cols-2 border border-border/60 bg-card/40 xl:grid-cols-6">
          {[
            ['事实记录', dashboard?.summary.factualCount ?? 0, '近 7 天'],
            ['覆盖来源', status?.sources.enabled ?? 0, `${status?.sources.withData ?? 0} 个已有数据`],
            ['原文链接覆盖率', `${dashboard?.summary.originalLinkCoverage ?? 0}%`, `${dashboard?.summary.originalLinkCount ?? 0} 条可直达`],
            ['待核验观点', dashboard?.summary.unverifiedCount ?? 0, '不混入事实流'],
            ['最后同步', time(status?.worker.lastSyncAt, false), status?.worker.running ? '监控运行中' : '监控已停止'],
            ['数据源新鲜度', `${status?.sources.fresh ?? 0}/${status?.sources.enabled ?? 0}`, `${status?.sources.stale ?? 0} 陈旧 · ${status?.sources.empty ?? 0} 空源`],
          ].map(([label, value, hint]) => <div key={String(label)} className="border-b border-r border-border/50 px-4 py-3"><p className="text-[11px] text-secondary-text">{label}</p><p className="mt-1 font-mono text-lg font-semibold text-foreground">{value}</p><p className="text-[10px] text-secondary-text">{hint}</p></div>)}
        </section>

        <nav aria-label="情报渠道" className="flex overflow-x-auto border border-border/60 bg-card/30">
          {CHANNELS.map((item) => <button key={item.key} onClick={() => { setActiveChannel(item.key); setSourceKey(''); }} className={`shrink-0 border-b-2 px-4 py-3 text-sm transition ${activeChannel === item.key ? 'border-cyan bg-cyan/8 text-cyan' : 'border-transparent text-secondary-text hover:text-foreground'}`}>{item.label}</button>)}
        </nav>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
          <div className="min-w-0 border border-border/60 bg-card/30">
            <div className="grid gap-2 border-b border-border/60 p-3 md:grid-cols-[150px_150px_1fr_auto]">
              <select aria-label="筛选来源" value={sourceKey} onChange={(event) => setSourceKey(event.target.value)} className="input-surface h-10 border bg-transparent px-3 text-xs outline-none"><option value="">来源：全部</option>{status?.sources.items.map((source) => <option key={source.sourceKey} value={source.sourceKey}>{source.name}</option>)}</select>
              <select aria-label="筛选股票" value={symbol} onChange={(event) => setSymbol(event.target.value)} className="input-surface h-10 border bg-transparent px-3 text-xs outline-none"><option value="">股票：全部</option>{dashboard?.watchlist.map((stock) => <option key={stock.symbol} value={stock.symbol}>{stock.name} {stock.symbol}</option>)}</select>
              <label className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-secondary-text" /><input aria-label="搜索事实" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="关键词 / 标题 / 摘要" className="input-surface h-10 w-full border bg-transparent pl-9 pr-3 text-xs outline-none" /></label>
              <button className="btn-secondary inline-flex h-10 items-center justify-center gap-2"><Filter className="h-4 w-4" />筛选</button>
            </div>

            <div className="hidden grid-cols-[105px_70px_76px_minmax(125px,1fr)_minmax(130px,1.1fr)_38px_60px] gap-2 border-b border-border/60 px-4 py-2 text-[11px] text-secondary-text xl:grid">
              <span>来源</span><span>证据</span><span>数据时间</span><span>股票 / 标题</span><span>事实摘要</span><span>原文</span><span>入库时间</span>
            </div>
            {!loading && !events?.items.length ? <EmptyState title="暂无匹配事实" description="调整渠道、来源或股票筛选，或执行同步。" icon={<FileCheck2 className="h-7 w-7" />} /> : null}
            <div className="divide-y divide-border/50">
              {events?.items.map((event) => {
                const evidence = evidenceOf(event); const label = EVIDENCE_LABELS[evidence.evidenceLevel] ?? EVIDENCE_LABELS.licensed;
                return <button key={event.id} onClick={() => openEvent(event)} aria-label={`${event.url ? '打开来源原文' : '查看接口返回原文'}：${event.title}`} className="grid w-full gap-2 px-4 py-3 text-left transition hover:bg-hover xl:grid-cols-[105px_70px_76px_minmax(125px,1fr)_minmax(130px,1.1fr)_38px_60px] xl:items-center xl:gap-2">
                  <div className="min-w-0"><p className="truncate text-xs font-medium text-foreground">{event.sourceName}</p><p className="truncate text-[10px] text-secondary-text">{evidence.provider || event.sourceType}</p></div>
                  <div><Badge variant={label.tone}>{label.label}</Badge>{evidence.contentNature === 'derivedSummary' ? <p className="mt-1 text-[9px] text-secondary-text">数据汇总</p> : null}</div>
                  <p className="font-mono text-[11px] text-secondary-text">{time(evidence.dataTimestamp || event.eventAt)}</p>
                  <div className="min-w-0"><div className="flex flex-wrap gap-1">{event.symbols.map((item) => <span key={item} className="text-[10px] text-cyan">{item}</span>)}</div><p className="mt-1 line-clamp-2 text-sm font-medium leading-5 text-foreground">{event.title}</p></div>
                  <p className="line-clamp-2 text-xs leading-5 text-secondary-text">{event.summary || '该来源是数值型记录，请查看接口原始数据。'}</p>
                  <span className={event.url ? 'text-cyan' : 'text-secondary-text'}>{event.url ? <ExternalLink className="h-4 w-4" /> : <FileText className="h-4 w-4" />}</span>
                  <p className="font-mono text-[11px] text-secondary-text">{time(event.ingestedAt, false)}</p>
                </button>;
              })}
            </div>
            <div className="flex items-center justify-between border-t border-border/60 px-4 py-3 text-xs text-secondary-text"><span>当前条件共 {events?.total ?? 0} 条，展示前 {events?.items.length ?? 0} 条</span><span>每 30 秒刷新本地索引</span></div>
          </div>

          <aside className="space-y-3">
            {dashboard?.watchlist.map((stock) => <section key={stock.symbol} className="border border-border/60 bg-card/35">
              <div className="flex items-start justify-between border-b border-border/50 p-4"><div><h2 className="font-semibold text-foreground">{stock.name}</h2><p className="font-mono text-xs text-secondary-text">{stock.symbol}</p></div><button onClick={() => setSymbol(stock.symbol)} className="text-xs text-cyan">只看该股 <ChevronRight className="inline h-3.5 w-3.5" /></button></div>
              <div className="p-4"><p className="text-sm font-medium text-cyan">今日新增 {stock.todayEventCount ?? 0} 条</p><div className="mt-3 grid grid-cols-3 gap-2 text-center"><div><p className="font-mono text-lg text-foreground">{stock.perspectives.investor ?? 0}</p><p className="text-[10px] text-secondary-text">投资者</p></div><div><p className="font-mono text-lg text-foreground">{stock.perspectives.company ?? 0}</p><p className="text-[10px] text-secondary-text">公司</p></div><div><p className="font-mono text-lg text-foreground">{stock.perspectives.institution ?? 0}</p><p className="text-[10px] text-secondary-text">机构</p></div></div><div className="mt-3 flex justify-between border-t border-border/50 pt-3 text-xs"><span className="text-success">机会 {stock.opportunityScore}</span><span className="text-danger">风险 {stock.riskScore}</span><span className="text-secondary-text">共 {stock.eventCount}</span></div></div>
            </section>)}

            <section className="border border-border/60 bg-card/35 p-4"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-success" /><h2 className="font-medium text-foreground">事实口径</h2></div><ul className="mt-3 space-y-2 text-xs leading-5 text-secondary-text"><li>官方披露：公告及法定信息。</li><li>授权数据：Tushare、天眼查及行情源原始记录。</li><li>媒体报道：保留发布来源，不等同于公司确认。</li><li className="text-warning">待核验观点：知识星球与外部投稿，不进入默认事实流。</li></ul></section>

            <section className="border border-border/60 bg-card/35 p-4"><div className="flex items-center gap-2"><Cloud className="h-4 w-4 text-cyan" /><h2 className="font-medium text-foreground">iCloud 知识库</h2></div><p className="mt-2 text-xs text-secondary-text">{cloud?.storage.snapshotCount ?? 0} 个版本 · 最新 {time(cloud?.storage.latest?.createdAt)}</p><button className="btn-secondary mt-3 w-full" disabled={actionLoading || !cloud?.storage.available} onClick={() => void act(() => investmentMonitorApi.createCloudSnapshot())}>立即云备份</button></section>
          </aside>
        </section>

        {activeChannel === 'company' ? <section className="border border-border/60 bg-card/30 p-4">
          <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-center"><div><h2 className="font-semibold text-foreground">巨潮公告工具</h2><p className="text-xs text-secondary-text">按真实发布日期、股票和公告分类增量抓取；支持原始 PDF、Excel 与文本包。</p></div><div className="flex flex-wrap gap-2"><input aria-label="公告开始日期" type="date" value={announcementStart} onChange={(event) => setAnnouncementStart(event.target.value)} className="input-surface h-10 border bg-transparent px-3 text-xs" /><input aria-label="公告结束日期" type="date" value={announcementEnd} onChange={(event) => setAnnouncementEnd(event.target.value)} className="input-surface h-10 border bg-transparent px-3 text-xs" /><select aria-label="公告分类" value={announcementCategory} onChange={(event) => setAnnouncementCategory(event.target.value)} className="input-surface h-10 border bg-transparent px-3 text-xs"><option value="">全部分类</option>{announcementCategories.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select><button className="btn-primary" disabled={actionLoading} onClick={() => void syncAnnouncements()}><FileText className="mr-1 inline h-4 w-4" />抓取公告</button><button className="btn-secondary" disabled={!announcements?.items.length} onClick={() => void exportAnnouncements()}><Download className="mr-1 inline h-4 w-4" />Excel</button><button className="btn-secondary" disabled={!announcements?.items.length} onClick={() => void packageAnnouncements()}><Archive className="mr-1 inline h-4 w-4" />PDF+TXT</button></div></div>
        </section> : null}

        <section className="border border-border/60 bg-card/30">
          <div className="flex flex-col justify-between gap-2 border-b border-border/60 px-4 py-3 sm:flex-row sm:items-center"><div><h2 className="font-semibold text-foreground">来源矩阵</h2><p className="text-xs text-secondary-text">每个渠道独立调度、独立失败、独立记录最后成功时间。</p></div><div className="flex items-center gap-2 text-xs text-secondary-text"><span className={`h-2 w-2 rounded-full ${status?.worker.running ? 'animate-pulse bg-success' : 'bg-secondary-text'}`} />最近调度 {time(status?.worker.lastSyncAt)}</div></div>
          <div className="grid lg:grid-cols-2 2xl:grid-cols-4">{sourceGroups.map((group, groupIndex) => <div key={group.key} className={`min-w-0 ${groupIndex ? 'border-t border-border/60 lg:border-l lg:border-t-0' : ''}`}><div className="min-w-0 border-b border-border/50 px-4 py-3"><p className="truncate font-medium text-foreground">{group.title} <span className="font-mono text-xs text-secondary-text">({group.items.length})</span></p><p className="truncate text-[10px] text-secondary-text">{group.description}</p></div><div className="min-w-0 divide-y divide-border/40">{group.items.map((source) => { const freshnessLabel = source.freshnessStatus === 'fresh' ? '数据新鲜' : source.freshnessStatus === 'stale' ? '数据陈旧' : '尚无数据'; return <button key={source.sourceKey} aria-label={`${source.name}，${source.lastStatus === 'success' ? '最近同步成功' : '最近同步异常'}，点击立即同步`} onClick={() => void act(() => investmentMonitorApi.syncSource(source.sourceKey))} className="flex w-full min-w-0 items-center justify-between gap-3 overflow-hidden px-4 py-3 text-left hover:bg-hover"><div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-foreground">{source.name}</p><p className="mt-0.5 truncate text-[10px] text-secondary-text">{source.config?.originApis?.join(' · ') || source.adapterType} · 同步 {time(source.lastSuccessAt)}</p><p className={`mt-0.5 truncate text-[10px] ${source.freshnessStatus === 'fresh' ? 'text-success' : source.freshnessStatus === 'stale' ? 'text-warning' : 'text-secondary-text'}`}>{freshnessLabel} · 最新事实 {time(source.latestEventAt)}</p></div><div className="shrink-0 text-right"><span className={`inline-block h-2 w-2 rounded-full ${source.lastStatus === 'success' ? 'bg-success' : source.lastStatus === 'failed' ? 'bg-danger' : 'bg-secondary-text'}`} /><p className="mt-1 font-mono text-[10px] text-secondary-text">{source.storedEventCount ?? 0}</p></div></button>; })}{!group.items.length ? <p className="px-4 py-5 text-xs text-secondary-text">暂无来源</p> : null}</div></div>)}</div>
        </section>
      </div>

      <Drawer isOpen={Boolean(selected)} onClose={closeEvent} title={selected?.title || '原文'}>
        {selected ? (() => { const evidence = evidenceOf(selected); const label = EVIDENCE_LABELS[evidence.evidenceLevel] ?? EVIDENCE_LABELS.licensed; return <div className="space-y-5">
          <div className="flex flex-wrap gap-2"><Badge variant={label.tone}>{label.label}</Badge><Badge>{selected.sourceName}</Badge>{evidence.contentNature === 'derivedSummary' ? <Badge variant="warning">基于原始数据汇总</Badge> : <Badge variant="success">源记录</Badge>}</div>
          {evidence.evidenceLevel === 'unverified' ? <div className="border border-warning/30 bg-warning/8 p-3 text-sm text-warning"><CircleAlert className="mr-2 inline h-4 w-4" />该内容是观点或线索，不代表已被公司、监管机构或独立来源证实。</div> : <div className="border border-success/25 bg-success/5 p-3 text-sm text-success"><CheckCircle2 className="mr-2 inline h-4 w-4" />来源与数据时间已记录，可用下方原始字段复核。</div>}
          <dl className="grid grid-cols-[88px_1fr] gap-x-3 gap-y-2 text-sm"><dt className="text-secondary-text">提供方</dt><dd className="text-foreground">{evidence.provider || selected.sourceName}</dd><dt className="text-secondary-text">原始 API</dt><dd className="font-mono text-xs text-foreground">{evidence.originApis?.join(', ') || '—'}</dd><dt className="text-secondary-text">数据时间</dt><dd className="text-foreground">{time(evidence.dataTimestamp || selected.eventAt)}</dd><dt className="text-secondary-text">入库时间</dt><dd className="text-foreground">{time(selected.ingestedAt)}</dd><dt className="text-secondary-text">关联股票</dt><dd className="text-cyan">{selected.symbols.join(' · ') || '全市场'}</dd></dl>
          {selected.url ? <a href={selected.url} target="_blank" rel="noreferrer" className="btn-primary inline-flex items-center gap-2"><ExternalLink className="h-4 w-4" />打开来源原文 / PDF</a> : <p className="border border-border/60 bg-elevated/40 p-3 text-xs leading-5 text-secondary-text">该数据源没有返回公开网页链接，下面展示接口直接返回的原文内容；不会用结构化指标冒充原文。</p>}
          <section><h3 className="flex items-center gap-2 font-medium text-foreground"><FileText className="h-4 w-4 text-cyan" />{selected.url ? '原文摘录' : '接口返回原文'}</h3><p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-secondary-text">{selected.summary || '该来源是行情、财务或资金等数值型记录，没有独立文章正文。'}</p></section>
          <section><h3 className="font-medium text-foreground">参与者与标签</h3><p className="mt-2 text-sm text-secondary-text">{[...selected.actors, ...selected.tags].join(' · ') || '—'}</p></section>
          <details className="border border-border/60 p-4"><summary className="cursor-pointer font-medium text-foreground">数据字段（辅助核验）</summary><p className="mt-2 text-xs leading-5 text-secondary-text">这里是从来源 API 保留的数值和分类字段，不是文章原文。</p><pre className="mt-3 max-h-[420px] overflow-auto whitespace-pre-wrap text-xs leading-5 text-secondary-text">{JSON.stringify(cleanMetrics(selected), null, 2)}</pre></details>
        </div>; })() : null}
      </Drawer>
    </AppPage>
  );
};

export default InvestmentMonitorPage;
