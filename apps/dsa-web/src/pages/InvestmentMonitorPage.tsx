import { useCallback, useEffect, useRef, useState } from 'react';
import { DatabaseZap, FileCheck2, FileText, Play, RefreshCw, Search, Square } from 'lucide-react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { AppPage, Badge, EmptyState } from '../components/common';
import { InvestmentMonitorNav } from '../components/investmentMonitor/InvestmentMonitorNav';
import { SourceRecordDrawer } from '../components/investmentMonitor/SourceRecordDrawer';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type { MonitorEvent, MonitorEventList, MonitorStatus } from '../types/investmentMonitor';

type ChannelTab = { key: string; label: string; channel?: string; evidenceLevel?: string };
type Evidence = {
  evidenceLevel: string; channel: string; provider?: string; originApis?: string[];
  contentNature?: string; dataTimestamp?: string;
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
  official: { label: '官方披露', tone: 'success' }, licensed: { label: '授权数据', tone: 'info' },
  reported: { label: '媒体报道', tone: 'default' }, unverified: { label: '待核验观点', tone: 'warning' },
};

function time(value?: string | null, withDate = true) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', withDate
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit' });
}

function evidenceOf(event: MonitorEvent): Evidence {
  const raw = (event.metrics._evidence ?? event.metrics.evidence) as Partial<Evidence> | undefined;
  if (raw?.evidenceLevel) return raw as Evidence;
  if (event.sourceKey === 'cninfo.announcements') return { evidenceLevel: 'official', channel: 'company' };
  if (event.sourceKey === 'zsxq.essays') return { evidenceLevel: 'unverified', channel: 'essay' };
  if (event.sourceKey.startsWith('feeds.')) return { evidenceLevel: 'reported', channel: 'news' };
  return { evidenceLevel: 'licensed', channel: 'other' };
}

export default function InvestmentMonitorPage() {
  const location = useLocation();
  const fromHome = location.state?.returnTo === '/app';
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [events, setEvents] = useState<MonitorEventList | null>(null);
  const requestedChannel = searchParams.get('channel');
  const activeChannel = CHANNELS.some(item => item.key === requestedChannel) ? requestedChannel : 'facts';
  const sourceKey = searchParams.get('source') ?? '';
  const symbol = searchParams.get('symbol') ?? '';
  const [query, setQuery] = useState('');
  const deferredQuery = useDebouncedValue(query, 350);
  const [selected, setSelected] = useState<MonitorEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const eventRequestVersionRef = useRef(0);
  const tab = CHANNELS.find(item => item.key === activeChannel) ?? CHANNELS[0];

  const loadStatus = useCallback(async () => {
    try { setStatus(await investmentMonitorApi.status()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '渠道状态暂时不可用'); }
  }, []);

  const loadEvents = useCallback(async () => {
    const version = ++eventRequestVersionRef.current;
    setLoading(true); setNotice('');
    try {
      const next = await investmentMonitorApi.events({
        days: sourceKey ? 3650 : 7,
        query: deferredQuery || undefined,
        symbol: symbol || undefined,
        sourceKey: sourceKey || undefined,
        channel: sourceKey ? undefined : tab.channel,
        evidenceLevel: sourceKey ? undefined : tab.evidenceLevel,
        pageSize: 100,
      });
      if (version === eventRequestVersionRef.current) setEvents(next);
    } catch (caught) {
      if (version === eventRequestVersionRef.current) {
        setNotice('本次读取未完成，已保留上一组消息；系统会继续重试。');
        setError(caught instanceof Error ? caught.message : '实时流水暂时不可用');
      }
    } finally {
      if (version === eventRequestVersionRef.current) setLoading(false);
    }
  }, [deferredQuery, sourceKey, symbol, tab.channel, tab.evidenceLevel]);

  const refreshVisiblePage = useCallback(
    () => Promise.allSettled([loadStatus(), loadEvents()]),
    [loadEvents, loadStatus],
  );
  usePageActivationRefresh(refreshVisiblePage, {
    intervalMs: status?.worker.running ? 10_000 : 30_000,
    minIntervalMs: 2_000,
    runOnMount: false,
  });
  useEffect(() => { void loadStatus(); }, [loadStatus]);
  useEffect(() => {
    void loadEvents();
    return () => { eventRequestVersionRef.current += 1; };
  }, [loadEvents]);
  const requestedEventId = Number(searchParams.get('event'));
  const selectedId = Number.isInteger(requestedEventId) && requestedEventId > 0 ? requestedEventId : undefined;
  const act = async (action: () => Promise<unknown>) => {
    setActionLoading(true); setError('');
    try { await action(); await Promise.allSettled([loadStatus(), loadEvents()]); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '操作失败'); }
    finally { setActionLoading(false); }
  };
  const setFilter = (key: 'source' | 'symbol' | 'channel', value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    if (key === 'channel') next.delete('source');
    setSelected(null);
    next.delete('event'); setSearchParams(next, { replace: true, state: fromHome ? { returnTo: '/app' } : null });
  };
  const openEvent = (event: MonitorEvent) => {
    setSelected(event);
    const next = new URLSearchParams(searchParams); next.set('event', String(event.id));
    setSearchParams(next, { replace: true, state: fromHome ? { returnTo: '/app' } : null });
  };
  const closeEvent = () => {
    setSelected(null);
    if (!searchParams.has('event')) return;
    const next = new URLSearchParams(searchParams); next.delete('event'); setSearchParams(next, { replace: true, state: fromHome ? { returnTo: '/app' } : null });
  };

  return <AppPage className="intelligence-feed-page max-w-[1760px]">
    {fromHome ? <Link to="/app" className="btn-secondary mb-3 inline-flex">返回首页</Link> : null}
    <main className="intelligence-terminal overflow-hidden border border-[#242A31] bg-[#050605] text-[#D7DCE2]">
      <header className="flex flex-col gap-4 border-b border-[#242A31] bg-[#090B09] px-4 py-4 lg:flex-row lg:items-end lg:justify-between">
        <div><p className="text-[9px] uppercase tracking-[0.22em] text-[#00E676]">本地统一事件库 · 10 秒刷新</p><h1 className="mt-2 text-2xl font-black tracking-[-0.05em] text-white">实时流水</h1><p className="mt-1 text-[10px] text-[#717A84]">只展示按时间进入本地库的原始消息；渠道全景与同步状态已拆到“全渠道情报”。</p></div>
        <div className="flex flex-wrap gap-2">
          <button className="inline-flex h-8 items-center gap-1.5 border border-[#303740] px-3 text-[9px] hover:border-[#00E676]" disabled={actionLoading} onClick={() => void act(() => status?.worker.running ? investmentMonitorApi.stopWorker() : investmentMonitorApi.startWorker())}>{status?.worker.running ? <Square className="h-3.5 w-3.5"/> : <Play className="h-3.5 w-3.5"/>}{status?.worker.running ? '停止监控' : '启动监控'}</button>
          <button className="inline-flex h-8 items-center gap-1.5 border border-[#303740] px-3 text-[9px] hover:border-[#00E676]" disabled={actionLoading} onClick={() => void act(() => investmentMonitorApi.sync())}><DatabaseZap className="h-3.5 w-3.5"/>同步全部渠道</button>
          <button className="inline-flex h-8 items-center gap-1.5 border border-[#00E676] px-3 text-[9px] font-bold text-[#00E676]" disabled={loading} onClick={() => void loadEvents()}><RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`}/>刷新流水</button>
        </div>
      </header>

      <InvestmentMonitorNav />
      {error ? <div role="alert" className="border-b border-[#6B4423] bg-[#19130A] px-4 py-2 text-[10px] text-[#FFB800]">{error}</div> : null}

      <nav aria-label="情报渠道" className="flex overflow-x-auto border-b border-[#242A31] bg-[#090B09]">
        {CHANNELS.map(item => <button key={item.key} onClick={() => setFilter('channel', item.key)} className={`shrink-0 border-b-2 px-4 py-2.5 text-[10px] transition ${activeChannel === item.key && !sourceKey ? 'border-[#00E676] bg-[#0C1710] text-[#00E676]' : 'border-transparent text-[#717A84] hover:text-white'}`}>{item.label}</button>)}
      </nav>

      <section className="grid gap-2 border-b border-[#242A31] p-3 md:grid-cols-[210px_150px_minmax(240px,1fr)]">
        <select aria-label="筛选来源" value={sourceKey} onChange={event => setFilter('source', event.target.value)} className="h-9 border border-[#303740] bg-[#050605] px-3 text-[10px] text-white outline-none focus:border-[#00E676]"><option value="">来源：全部</option>{status?.sources.items.map(source => <option key={source.sourceKey} value={source.sourceKey}>{source.name}</option>)}</select>
        <input aria-label="筛选股票" value={symbol} onChange={event => setFilter('symbol', event.target.value)} placeholder="股票代码：全部" className="h-9 border border-[#303740] bg-[#050605] px-3 text-[10px] text-white outline-none focus:border-[#00E676]"/>
        <label className="relative"><Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-[#59616A]"/><input aria-label="搜索流水" value={query} onChange={event => setQuery(event.target.value)} placeholder="关键词 / 标题 / 摘要" className="h-9 w-full border border-[#303740] bg-[#050605] pl-9 pr-3 text-[10px] text-white outline-none focus:border-[#00E676]"/></label>
      </section>

      <div className="hidden grid-cols-[128px_80px_90px_minmax(180px,1fr)_minmax(220px,1.2fr)_58px] gap-3 border-b border-[#242A31] px-4 py-2 text-[9px] text-[#59616A] xl:grid"><span>来源</span><span>证据</span><span>数据时间</span><span>股票 / 标题</span><span>原始摘要</span><span>原文</span></div>
      {!loading && !events?.items.length ? <EmptyState title="暂无匹配消息" description="调整渠道、来源、股票或关键词后再查看。" icon={<FileCheck2 className="h-7 w-7"/>}/> : null}
      <div>{events?.items.map(event => { const evidence = evidenceOf(event); const label = EVIDENCE_LABELS[evidence.evidenceLevel] ?? EVIDENCE_LABELS.licensed; return <button key={event.id} onClick={() => openEvent(event)} aria-label={`查看资料：${event.title}`} className="grid w-full gap-2 border-b border-[#1C211E] px-4 py-3 text-left hover:bg-[#0D100E] xl:grid-cols-[128px_80px_90px_minmax(180px,1fr)_minmax(220px,1.2fr)_58px] xl:items-center xl:gap-3">
        <div className="min-w-0"><p className="truncate text-[10px] font-semibold text-[#D7DCE2]">{event.sourceName}</p><p className="mt-1 truncate text-[8px] text-[#59616A]">{evidence.provider || event.sourceType}</p></div>
        <div><Badge variant={label.tone}>{label.label}</Badge></div>
        <p className="text-[9px] text-[#717A84]">{time(evidence.dataTimestamp || event.eventAt)}</p>
        <div className="min-w-0"><div className="flex flex-wrap gap-1">{event.symbols.map(item => <span key={item} className="text-[8px] text-[#00E676]">{item}</span>)}</div><p className="mt-1 line-clamp-2 text-[11px] font-semibold leading-4 text-white">{event.title}</p></div>
        <p className="line-clamp-2 text-[10px] leading-4 text-[#8B949E]">{event.summary || '此条暂无正文摘录，可查看已保存的资料信息。'}</p>
        <span className="text-[#717A84]"><FileText className="h-3.5 w-3.5"/></span>
      </button>; })}</div>
      <div className="flex items-center justify-between px-4 py-2 text-[9px] text-[#59616A]" aria-live="polite"><span>{query !== deferredQuery ? '等待输入完成…' : loading ? '正在读取本地索引，保留当前结果…' : notice || `显示 ${events?.items.length ?? 0} / ${events?.total ?? 0} 条`}</span><span>每 10 秒刷新本地索引</span></div>
    </main>

    {selectedId ? <SourceRecordDrawer eventId={selectedId} event={selected?.id === selectedId ? selected : undefined} onClose={closeEvent} /> : null}
  </AppPage>;
}
