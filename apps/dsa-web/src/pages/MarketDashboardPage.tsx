import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, Bot, BrainCircuit, Building2, CalendarClock,
  FileSearch, Globe2, Landmark, MessageSquareText, RadioTower, RefreshCw, ShieldAlert,
  Sparkles, Zap,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { homeDashboardApi } from '../api/homeDashboard';
import { AppPage, Badge, Card, EmptyState } from '../components/common';
import { MarketTimeframeChart } from '../components/market';
import { getMarketSeries } from '../api/marketSeries';
import type { HomeDashboard, HomeWatchlistCard, MarketIndexCard, MarketPoint } from '../types/homeDashboard';
import type { MonitorEvent } from '../types/investmentMonitor';
import { useRealtimeIndices, useRealtimeQuotes } from '../hooks/useRealtimeQuotes';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import { marketQuoteSession } from '../utils/marketQuoteDate';
import './MarketDashboardPage.css';

const SOURCE_LABELS: Record<string, string> = {
  investor: '投资者', company: '上市公司', institution: '机构',
};
const DATA_SOURCE_LABELS: Record<string, string> = {
  'tushare.index_daily': 'Tushare 指数日线',
  'tushare.index_global': 'Tushare 全球指数',
  'tushare.legacy_snapshot': '本地秒级快照',
  'tushare.daily': 'Tushare 全市场日线',
  'tushare.moneyflow_ind_ths': 'Tushare 同花顺行业',
  'tushare.moneyflow_ind_dc': 'Tushare 东方财富行业',
  'tushare.moneyflow_hsgt': 'Tushare 沪深港通',
  'akshare.sina_a_spot': '新浪全A股实时行情',
  'akshare.sina_sector_spot': '新浪行业板块实时行情',
  'tencent.snapshot': '腾讯实时行情',
};

const HOME_CACHE_KEY = 'dsa:home-dashboard:last-good:v2';
const HOME_CACHE_MAX_AGE_MS = 12 * 60 * 60 * 1_000;
let memoryDashboardCache: HomeDashboard | null = null;

function readDashboardCache(): HomeDashboard | null {
  if (memoryDashboardCache) return memoryDashboardCache;
  try {
    const raw = window.localStorage.getItem(HOME_CACHE_KEY);
    if (!raw) return null;
    const cached = JSON.parse(raw) as { storedAt?: number; data?: HomeDashboard };
    memoryDashboardCache = cached.data && cached.storedAt && Date.now() - cached.storedAt <= HOME_CACHE_MAX_AGE_MS
      ? cached.data
      : null;
  } catch {
    memoryDashboardCache = null;
  }
  return memoryDashboardCache;
}

function rememberDashboard(value: HomeDashboard) {
  memoryDashboardCache = value;
  try { window.localStorage.setItem(HOME_CACHE_KEY, JSON.stringify({ storedAt: Date.now(), data: value })); } catch { /* optional cache */ }
}

function sourceLabel(value?: string | null) {
  return value ? (DATA_SOURCE_LABELS[value] ?? value) : '来源未标注';
}

function shortTime(value?: string | null) {
  if (!value) return '时间未标注';
  if (/^\d{8}/.test(value)) {
    const date = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
    const suffix = value.slice(8).replace(/^T/, ' ');
    return `${date}${suffix}`;
  }
  return value.replace('T', ' ').slice(0, 19);
}

function tone(value?: number | null) {
  if ((value ?? 0) > 0) return 'text-danger';
  if ((value ?? 0) < 0) return 'text-success';
  return 'text-secondary-text';
}

function formatNumber(value?: number | null, digits = 2) {
  return value == null || Number.isNaN(value) ? '—' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function formatCompact(value?: number | null) {
  if (value == null || Number.isNaN(value)) return '—';
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(2)}万`;
  return value.toLocaleString('zh-CN');
}

function percent(value?: number | null) {
  if (value == null || Number.isNaN(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function Sparkline({ points, positive, height = 44 }: { points: MarketPoint[]; positive: boolean; height?: number }) {
  const values = points.map((point) => Number(point.value)).filter(Number.isFinite);
  if (values.length < 2) return <div className="flex h-11 items-center justify-center text-xs text-secondary-text">暂无趋势</div>;
  const min = Math.min(...values); const max = Math.max(...values); const range = max - min || 1;
  const coords = values.map((value, index) => `${(index / (values.length - 1)) * 100},${height - 4 - ((value - min) / range) * (height - 8)}`).join(' ');
  return <svg viewBox={`0 0 100 ${height}`} className="h-full w-full overflow-visible" preserveAspectRatio="none" aria-hidden="true">
    <polyline points={coords} fill="none" stroke={positive ? 'hsl(var(--danger))' : 'hsl(var(--success))'} strokeWidth="2" vectorEffect="non-scaling-stroke" />
  </svg>;
}

function IndexTicker({ item, selected, onSelect }: { item: MarketIndexCard; selected: boolean; onSelect: () => void }) {
  const quoteTime = item.updateTime ?? item.tradeDate;
  const session = marketQuoteSession(quoteTime, item.source);
  const verifiedChangePct = session.canShowChange ? item.changePct : null;
  const positive = (verifiedChangePct ?? 0) >= 0;
  return <button
    type="button"
    aria-pressed={selected}
    aria-label={`切换到${item.name}K线`}
    onClick={onSelect}
    className={`market-index-switch min-w-[188px] flex-1 border-r border-border/50 px-3 py-2.5 text-left last:border-r-0 ${selected ? 'is-selected' : ''}`}
  >
    <div className="flex items-start justify-between gap-3">
      <div><p className="text-[11px] text-secondary-text">{item.name}</p><p className="mt-0.5 text-[9px] text-secondary-text">{session.label}点位</p><p className={`mt-0.5 text-lg font-semibold tabular-nums ${tone(verifiedChangePct)}`}>{formatNumber(item.close)}</p>
        <p className={`text-[11px] tabular-nums ${tone(verifiedChangePct)}`}>{session.canShowChange ? percent(verifiedChangePct) : '涨跌幅待核验'}</p></div>
      <div className="mt-2 h-10 w-20"><Sparkline points={item.history} positive={positive} height={40} /></div>
    </div>
    <div className="mt-1 flex items-center justify-between gap-2 text-[9px] text-secondary-text"><span className="truncate">{sourceLabel(item.source)}</span><span className="market-index-switch__hint">{selected ? '当前K线' : '切换'}</span></div>
  </button>;
}

function OverseasCard({ item }: { item: MarketIndexCard }) {
  const session = marketQuoteSession(item.updateTime ?? item.tradeDate, item.source);
  const verifiedChangePct = session.canShowChange ? item.changePct : null;
  return <div className="market-overseas-row grid min-w-0 grid-cols-[96px_minmax(0,1fr)_72px] items-center gap-3 border-b border-border/55 px-3 py-2 last:border-b-0">
    <div><p className="truncate text-[11px] font-medium text-foreground">{item.name}</p><p className="text-[9px] text-secondary-text">{item.region}</p></div>
    <div><p className={`text-right text-sm font-semibold tabular-nums ${tone(verifiedChangePct)}`}>{formatNumber(item.close)}</p><p className="truncate text-right text-[9px] text-secondary-text">{session.label}</p></div>
    <span className={`text-right text-[11px] font-medium ${tone(verifiedChangePct)}`}>{session.canShowChange ? percent(verifiedChangePct) : '待核验'}</span>
  </div>;
}

function DistributionHistogram({ items }: { items: Array<{ label: string; count: number }> }) {
  const max = Math.max(1, ...items.map(item => item.count));
  const midpoint = Math.floor(items.length / 2);
  return <div className="market-histogram" role="img" aria-label={items.map(item => `${item.label}${item.count}家`).join('，')}>
    {items.map((item, index) => {
      const side = index < midpoint ? 'down' : index > midpoint ? 'up' : 'flat';
      return <div className="market-histogram__column" key={item.label}>
        <span className={`market-histogram__count is-${side}`}>{item.count}</span>
        <div className="market-histogram__track"><span className={`market-histogram__bar is-${side}`} style={{ height: `${Math.max(3, item.count / max * 100)}%` }} /></div>
        <span className="market-histogram__label">{item.label}</span>
      </div>;
    })}
  </div>;
}

function IntelligenceLine({ event }: { event?: MonitorEvent | null }) {
  if (!event) return <span className="text-secondary-text">暂无新增信息</span>;
  return <span aria-label={event.title}>{event.title}</span>;
}

function WatchlistFocus({ card, onAnalyze }: { card: HomeWatchlistCard; onAnalyze: () => void }) {
  const quote = card.latestQuote; const changePct = quote?.changePercent;
  const bullish = card.sentiment.bullish ?? 0; const bearish = card.sentiment.bearish ?? 0;
  const totalSentiment = Math.max(1, bullish + bearish + (card.sentiment.neutral ?? 0) + (card.sentiment.mixed ?? 0));
  return <div className="market-panel grid gap-px bg-border/60 xl:grid-cols-[minmax(0,1fr)_310px]">
    <div className="min-w-0 bg-card/90 p-3">
      <MarketTimeframeChart key={card.symbol} symbol={card.symbol} initialPeriod="intraday" initialRange="1d" />
    </div>
    <aside className="bg-card/95 p-4">
      <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h3 className="text-lg font-semibold">{card.name}</h3><Badge>{card.symbol}</Badge></div><div className="mt-2 flex items-baseline gap-2"><span className={`text-3xl font-semibold tabular-nums ${tone(changePct)}`}>{formatNumber(quote?.currentPrice)}</span><span className={`text-sm ${tone(changePct)}`}>{percent(changePct)}</span></div></div><button className="btn-primary inline-flex items-center gap-1.5 px-3 py-2" onClick={onAnalyze}><Sparkles className="h-3.5 w-3.5" />分析</button></div>
      <p className="mt-1 text-[9px] text-secondary-text">{shortTime(quote?.updateTime)} · {sourceLabel(quote?.source)}{quote?.isStale ? ' · 非连续竞价时段最新值' : ''}</p>
      <div className="mt-4 grid grid-cols-2 gap-px border border-border/60 bg-border/60 text-[10px]">
        {[["今开", quote?.open], ["最高", quote?.high], ["最低", quote?.low], ["成交额", formatCompact(quote?.amount)], ["秒量", quote?.secondVolume], ["秒成交额", formatCompact(quote?.secondAmount)]].map(([label, value]) => <div key={String(label)} className="flex justify-between bg-background/70 px-2 py-2"><span className="text-secondary-text">{label}</span><span className="tabular-nums">{typeof value === 'number' ? formatNumber(value, 0) : value ?? '—'}</span></div>)}
      </div>
      <div className="mt-4"><div className="flex justify-between text-[10px]"><span className="text-secondary-text">情报结构</span><span>{card.opportunityScore >= card.riskScore ? '机会占优' : '风险占优'}</span></div><div className="mt-2 h-1 bg-elevated"><div className="h-full bg-cyan" style={{ width: `${Math.round((bullish / totalSentiment) * 100)}%` }} /></div><div className="mt-1 flex justify-between text-[9px] text-secondary-text"><span>机会 {card.opportunityScore}</span><span>风险 {card.riskScore}</span><span>{card.eventCount} 条</span></div></div>
      <div className="mt-4 space-y-3 border-t border-border/60 pt-3 text-[10px]"><div><p className="text-secondary-text">机构最新</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestInstitution} /></p></div><div><p className="flex items-center gap-1 text-danger"><Zap className="h-3 w-3" />最新催化</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestCatalyst} /></p></div><div><p className="flex items-center gap-1 text-warning"><ShieldAlert className="h-3 w-3" />最新风险</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestRisk} /></p></div></div>
    </aside>
  </div>;
}

const MarketDashboardPage = () => {
  const navigate = useNavigate();
  const [data, setData] = useState<HomeDashboard | null>(() => readDashboardCache());
  const dataRef = useRef<HomeDashboard | null>(data);
  const [loading, setLoading] = useState(() => !data); const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedIndexCode, setSelectedIndexCode] = useState('000001.SH');
  const refreshProbeTimers = useRef<number[]>([]);
  const load = useCallback(async (force = false, refresh = false) => {
    const hasExistingData = dataRef.current != null;
    if (force) setRefreshing(true); else if (!hasExistingData) setLoading(true);
    try {
      const next = await homeDashboardApi.get(force, refresh);
      dataRef.current = next;
      setData(next);
      rememberDashboard(next);
      setError(null);
    }
    catch {
      setError(hasExistingData
        ? '市场数据暂时无法刷新，已保留上一次成功数据，系统会自动重试。'
        : '正在等待本地数据服务准备完成，系统会自动重试，不需要刷新页面。');
    }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  const refreshVisiblePage = useCallback(async () => {
    await load(false, true);
    refreshProbeTimers.current.forEach(timer => window.clearTimeout(timer));
    refreshProbeTimers.current = [5_000, 15_000, 30_000].map(delay => window.setTimeout(() => {
      void load(false, false);
    }, delay));
  }, [load]);
  usePageActivationRefresh(refreshVisiblePage, { intervalMs: 60_000, minIntervalMs: 10_000 });
  useEffect(() => () => {
    refreshProbeTimers.current.forEach(timer => window.clearTimeout(timer));
    refreshProbeTimers.current = [];
  }, []);
  useEffect(() => {
    if (!error) return undefined;
    const timer = window.setTimeout(() => void load(), 8_000);
    return () => window.clearTimeout(timer);
  }, [error, load]);
  useEffect(() => { document.title = '市场总览 - 乐子乌超级价值'; }, []);

  const watchlistSymbols = useMemo(() => (data?.watchlist ?? []).map(card => card.symbol), [data]);
  const watchlistSymbolKey = watchlistSymbols.join('|');
  useEffect(() => {
    const remainingSymbols = watchlistSymbols.slice(1, 5);
    if (!remainingSymbols.length) return undefined;
    let cancelled = false;
    let cursor = 0;
    const warmNext = () => {
      if (cancelled || document.visibilityState === 'hidden' || cursor >= remainingSymbols.length) return;
      const symbol = remainingSymbols[cursor++];
      void getMarketSeries(symbol, 'intraday', '1d', false, 'stock', false)
        .catch(() => undefined)
        .finally(() => {
          if (!cancelled && cursor < remainingSymbols.length) window.setTimeout(warmNext, 1_500);
        });
    };
    // The selected chart loads immediately. Warm a few alternative symbols
    // only after the public dashboard and live quote calls have settled.
    const timer = window.setTimeout(warmNext, 6_000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  // A primitive key prevents dashboard refreshes with the same symbols from
  // re-running the warmup effect.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchlistSymbolKey]);
  const { quotes: liveQuotes, keyFor: quoteKey } = useRealtimeQuotes(watchlistSymbols);
  const liveWatchlist = useMemo(() => (data?.watchlist ?? []).map(card => {
    const live = liveQuotes.get(quoteKey(card.symbol));
    return live && !live.isStale ? { ...card, latestQuote: {
      ...card.latestQuote, currentPrice: live.currentPrice, change: live.change ?? undefined,
      changePercent: live.changePercent ?? undefined, open: live.open ?? undefined,
      high: live.high ?? undefined, low: live.low ?? undefined, prevClose: live.prevClose ?? undefined,
      volume: live.volume ?? undefined, amount: live.amount ?? undefined, updateTime: live.updateTime ?? undefined,
      secondVolume: live.secondVolume ?? undefined, secondAmount: live.secondAmount ?? undefined,
      source: live.source ?? undefined, staleSeconds: live.staleSeconds ?? undefined,
      isStale: live.isStale ?? undefined,
    }} : card;
  }), [data, liveQuotes, quoteKey]);
  const focusedWatchlist = liveWatchlist.find(card => card.symbol === selectedSymbol) ?? liveWatchlist[0];

  const liveIndices = useRealtimeIndices((data?.cnIndices ?? []).map(item => item.code));
  const cnIndices = useMemo(() => (data?.cnIndices ?? []).map(item => {
    const live = liveIndices.get(item.code.toUpperCase());
    if (!live || live.isStale) return item;
    const history = live.close == null
      ? item.history
      : [...item.history, { date: live.updateTime, value: live.close }].slice(-12);
    return { ...item, close: live.close, changePct: live.changePct, history,
      source: live.source ?? item.source, updateTime: live.updateTime ?? item.updateTime,
      isStale: live.isStale };
  }), [data, liveIndices]);
  const leadIndex = cnIndices.find(item => item.code === selectedIndexCode) ?? cnIndices[0];
  const leadIndexSession = marketQuoteSession(leadIndex?.updateTime ?? leadIndex?.tradeDate, leadIndex?.source);
  const leadIndexChangePct = leadIndexSession.canShowChange ? leadIndex?.changePct : null;
  const breadthAvailable = Boolean(data?.breadth.available && (data.breadth.total ?? 0) > 0);
  const sectorDistributionAvailable = Boolean(data?.sectorDistribution.available && (data.sectorDistribution.total ?? 0) > 0);
  const breadthTotal = Math.max(1, data?.breadth.total ?? 0);
  const aiActions = [
    { label: '个股深度分析', note: '多维度研判', icon: BrainCircuit, action: () => navigate('/chat') },
    { label: '财报解读', note: '财务质量与预期', icon: FileSearch, action: () => navigate('/data-acquisition') },
    { label: '行业对比', note: '板块与竞争格局', icon: BarChart3, action: () => navigate('/screening') },
    { label: '事件影响评估', note: '催化与风险传导', icon: Activity, action: () => navigate('/investment-monitor') },
    { label: '智能问答', note: '带数据上下文提问', icon: MessageSquareText, action: () => navigate('/chat') },
  ];

  return <AppPage className="market-command-center max-w-none px-2 pb-6 pt-2 md:px-3 lg:px-4">
    <div className="space-y-2">
      <section className="market-panel overflow-hidden border border-border/70 bg-card/90">
        <div className="market-overview-header flex flex-col border-b border-border/60 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="flex items-center gap-2"><RadioTower className="h-4 w-4 text-cyan" /><h1 className="text-base font-semibold">市场总览</h1><Badge variant="info">本地秒库</Badge></div><p className="mt-0.5 text-[10px] text-secondary-text">{data?.marketTime ?? '正在同步市场时间'} · 自选与大盘读取 SQLite 秒快照 · 广度、资金和海外指数按所示交易日</p></div>
          <button className="btn-secondary mt-2 inline-flex items-center gap-2 sm:mt-0" disabled={refreshing} onClick={() => void load(true)}><RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />刷新数据</button>
        </div>
        <div className="market-index-rail flex overflow-x-auto">{cnIndices.map((item) => <IndexTicker key={item.code} item={item} selected={item.code === leadIndex?.code} onSelect={() => setSelectedIndexCode(item.code)} />)}
          <div className="min-w-[188px] flex-1 px-3 py-2.5"><p className="text-[11px] text-secondary-text">北向资金</p><p className={`mt-0.5 text-lg font-semibold tabular-nums ${tone(data?.northbound.northMoneyYi)}`}>{formatNumber(data?.northbound.northMoneyYi)}亿</p><p className="text-[9px] text-secondary-text">{shortTime(data?.northbound.tradeDate)} · {sourceLabel(data?.northbound.source)}</p></div></div>
      </section>

      {error ? <div role="status" className="border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-secondary-text">{error}</div> : null}
      <section className="market-panel border border-border/70 bg-card/90 p-3">
        <div className="market-main-quote-head flex flex-wrap items-end justify-between gap-3 border-b border-border/55 pb-2"><div><p className="text-[9px] text-secondary-text">大盘主行情 · K线 · {leadIndexSession.label} · {sourceLabel(leadIndex?.source)}</p><div className="mt-1 flex flex-wrap items-baseline gap-3"><h2 className="text-sm font-semibold">{leadIndex?.name ?? '上证指数'}</h2><Badge>{leadIndex?.code ?? '000001.SH'}</Badge><p className={`text-2xl font-semibold tabular-nums ${tone(leadIndexChangePct)}`}>{formatNumber(leadIndex?.close)}</p><span className={`text-xs ${tone(leadIndexChangePct)}`}>{leadIndexSession.canShowChange ? percent(leadIndexChangePct) : '涨跌幅待核验'}</span></div></div><div className="text-right text-[9px] text-secondary-text"><p>上方八个核心指数均可切换主K线</p><p>分时 / 日K / 周K / 月K / 年K均跟随当前指数</p></div></div>
        <MarketTimeframeChart key={leadIndex?.code ?? '000001.SH'} symbol={leadIndex?.code ?? '000001.SH'} assetType="index" initialPeriod="intraday" initialRange="1d" className="mt-2" />
      </section>

      {breadthAvailable || sectorDistributionAvailable ? <div className={`grid gap-2 ${breadthAvailable && sectorDistributionAvailable ? 'xl:grid-cols-2' : ''}`}>
        {breadthAvailable ? <section className="market-panel border border-border/70 bg-card/90 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">市场广度</h2><p className="mt-0.5 text-[9px] text-secondary-text">{shortTime(data?.breadth.updatedAt ?? data?.breadth.tradeDate)} 收盘 · {sourceLabel(data?.breadth.source)}</p></div><div className="flex gap-3 text-[10px]"><span className="text-danger">上涨 {data?.breadth.up}</span><span className="text-success">下跌 {data?.breadth.down}</span><span className="text-secondary-text">平盘 {data?.breadth.flat}</span></div></div>
          <>
            <div className="market-breadth-ratio mt-3" aria-label={`上涨${data?.breadth.up}家，下跌${data?.breadth.down}家，平盘${data?.breadth.flat}家`}><span className="is-up" style={{ width: `${(data?.breadth.up ?? 0) / breadthTotal * 100}%` }} /><span className="is-flat" style={{ width: `${(data?.breadth.flat ?? 0) / breadthTotal * 100}%` }} /><span className="is-down" style={{ width: `${(data?.breadth.down ?? 0) / breadthTotal * 100}%` }} /></div>
            <DistributionHistogram items={data?.breadth.distribution ?? []} />
            <div className="mt-2 grid grid-cols-3 gap-px border border-border/60 bg-border/60 text-center text-[9px]"><div className="bg-background/70 p-2"><p className="text-secondary-text">有效交易股票</p><p className="mt-1 text-xs">{data?.breadth.total}</p></div><div className="bg-background/70 p-2"><p className="text-secondary-text">涨停</p><p className="mt-1 text-xs text-danger">{data?.breadth.limitUp}</p></div><div className="bg-background/70 p-2"><p className="text-secondary-text">跌停</p><p className="mt-1 text-xs text-success">{data?.breadth.limitDown}</p></div></div>
          </>
        </section> : null}
        {sectorDistributionAvailable ? <section className="market-panel border border-border/70 bg-card/90 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">行业涨跌分布</h2><p className="mt-0.5 text-[9px] text-secondary-text">{shortTime(data?.sectorDistribution.updatedAt ?? data?.sectorDistribution.tradeDate)} 收盘 · {sourceLabel(data?.sectorDistribution.source)}</p></div><div className="flex gap-3 text-[10px]"><span className="text-danger">上涨 {data?.sectorDistribution.up}</span><span className="text-success">下跌 {data?.sectorDistribution.down}</span><span className="text-secondary-text">平盘 {data?.sectorDistribution.flat}</span></div></div>
          <>
            <DistributionHistogram items={data?.sectorDistribution.distribution ?? []} />
            <div className="market-sector-movers mt-2">
              <div><p className="market-sector-movers__title text-danger">领涨行业</p>{(data?.sectorDistribution.leaders ?? []).slice(0, 4).map(item => <div className="market-sector-movers__row" key={`up-${item.name}`}><span>{item.name}</span><span className="text-danger">{percent(item.changePct)}</span></div>)}</div>
              <div><p className="market-sector-movers__title text-success">领跌行业</p>{(data?.sectorDistribution.laggards ?? []).slice(0, 4).map(item => <div className="market-sector-movers__row" key={`down-${item.name}`}><span>{item.name}</span><span className="text-success">{percent(item.changePct)}</span></div>)}</div>
            </div>
          </>
        </section> : null}
      </div> : null}

      <section className="market-panel border border-border/70 bg-card/90"><div className="flex items-center justify-between border-b border-border/55 px-3 py-2"><div><h2 className="text-sm font-semibold">海外市场最近收盘</h2><p className="text-[9px] text-secondary-text">逐项显示实际交易日 · 不混入A股当日广度</p></div><Globe2 className="h-4 w-4 text-cyan" /></div><div className="market-overseas-list grid md:grid-cols-2 xl:grid-cols-3">{(data?.globalIndices ?? []).map((item) => <OverseasCard key={item.code} item={item} />)}</div></section>

      <section><div className="mb-2 flex flex-wrap items-end justify-between gap-2"><div><h2 className="text-base font-semibold">自选股行情与情报</h2><p className="mt-0.5 text-[9px] text-secondary-text">切换股票后，报价、K线、秒量、情报和来源保持同一标的</p></div><div className="market-watchlist-switcher flex flex-wrap items-center gap-1">{liveWatchlist.map(card => <button key={card.symbol} onClick={() => setSelectedSymbol(card.symbol)} className={`border px-3 py-2 text-left ${card.symbol === focusedWatchlist?.symbol ? 'border-cyan bg-cyan/10' : 'border-border bg-card'}`}><span className="text-[10px]">{card.name}</span><span className={`ml-2 text-[10px] tabular-nums ${tone(card.latestQuote?.changePercent)}`}>{formatNumber(card.latestQuote?.currentPrice)} {percent(card.latestQuote?.changePercent)}</span></button>)}<button className="btn-secondary inline-flex items-center gap-1.5" onClick={() => navigate('/investment-monitor')}><Building2 className="h-3.5 w-3.5" />全部情报<ArrowRight className="h-3.5 w-3.5" /></button></div></div>
        {!loading && !liveWatchlist.length ? <EmptyState title="暂无自选股数据" description="请在设置中添加自选股，或等待情报台首次同步。" /> : focusedWatchlist ? <WatchlistFocus card={focusedWatchlist} onAnalyze={() => navigate(`/chat?stock=${encodeURIComponent(focusedWatchlist.symbol.split('.')[0])}&name=${encodeURIComponent(focusedWatchlist.name)}`)} /> : null}
      </section>

      <div className="grid gap-2 xl:grid-cols-[minmax(0,1.25fr)_minmax(440px,.75fr)]">
        <Card padding="md"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">最新情报</h2><p className="text-[9px] text-secondary-text">来源名称优先，视角作为辅助标签</p></div><button className="text-[10px] text-cyan hover:underline" onClick={() => navigate('/investment-monitor')}>查看全部</button></div>
          <div className="mt-2 divide-y divide-border/55">{(data?.latestEvents ?? []).slice(0, 5).map((event) => <button key={event.id} className="market-latest-event grid w-full grid-cols-[42px_minmax(0,1fr)_auto] items-center gap-2 py-2 text-left" aria-label={`${event.url ? '打开来源原文' : '查看接口返回原文'}：${event.title}`} onClick={() => event.url ? window.open(event.url, '_blank', 'noopener,noreferrer') : navigate(`/investment-monitor/feed?event=${event.id}`)}><span className="text-[9px] tabular-nums text-secondary-text">{new Date(event.eventAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span><span className="truncate text-[11px]">{event.title}</span><div className="flex items-center gap-1"><Badge>{event.sourceName || event.sourceKey}</Badge><span className="text-[9px] text-secondary-text">{SOURCE_LABELS[event.perspective] ?? event.perspective}</span></div></button>)}{!data?.latestEvents.length ? <EmptyState title="暂无最新情报" description="情报同步后将在这里按来源展示。" /> : null}</div>
        </Card>
        <Card padding="md"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">分析入口</h2><p className="text-[9px] text-secondary-text">所有入口使用当前数据库真实上下文</p></div><Bot className="h-4 w-4 text-cyan" /></div><div className="market-ai-actions mt-2 grid grid-cols-5 gap-px bg-border/60">{aiActions.map((item) => <button key={item.label} onClick={item.action} className="group bg-background/60 p-2 text-left hover:bg-hover"><item.icon className="h-4 w-4 text-cyan" /><p className="mt-2 text-[10px] font-medium">{item.label}</p><p className="mt-0.5 text-[8px] leading-3 text-secondary-text">{item.note}</p></button>)}</div><div className="mt-2 grid grid-cols-3 gap-px bg-border/60 text-center text-[9px]"><div className="bg-elevated/60 p-2"><RadioTower className="mx-auto h-3.5 w-3.5 text-cyan" /><p className="mt-1 text-secondary-text">活跃源</p><p>{data?.intelligenceSummary.activeSourceCount ?? 0}</p></div><div className="bg-elevated/60 p-2"><CalendarClock className="mx-auto h-3.5 w-3.5 text-cyan" /><p className="mt-1 text-secondary-text">近7日情报</p><p>{data?.intelligenceSummary.eventCount ?? 0}</p></div><div className="bg-elevated/60 p-2"><Landmark className="mx-auto h-3.5 w-3.5 text-cyan" /><p className="mt-1 text-secondary-text">高优先级</p><p>{data?.intelligenceSummary.highPriorityCount ?? 0}</p></div></div></Card>
      </div>
      {loading && !data ? <div role="status" className="border border-cyan/20 bg-cyan/5 px-4 py-3 text-xs text-secondary-text">正在读取本地缓存并聚合市场数据，页面会自动完成加载…</div> : null}
    </div>
  </AppPage>;
};

export default MarketDashboardPage;
