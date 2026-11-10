import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, ArrowRight, BarChart3, Bot, BrainCircuit, Building2, ClipboardCheck,
  FileSearch, Globe2, Layers3, MessageSquareText, Newspaper, Pin, RadioTower,
  RefreshCw, ShieldAlert, Sparkles, Zap,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { homeDashboardApi } from '../api/homeDashboard';
import { invalidateCachedQueries } from '../api/requestCache';
import { AppPage, Badge, Card, EmptyState, EvidenceRail } from '../components/common';
import { MarketTimeframeChart } from '../components/market';
import { getMarketSeries } from '../api/marketSeries';
import type { HomeDashboard, HomeWatchlistCard, MarketIndexCard, MarketPoint } from '../types/homeDashboard';
import type { MonitorEvent } from '../types/investmentMonitor';
import { useRealtimeIndices, useRealtimeQuotes } from '../hooks/useRealtimeQuotes';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import { isAshareLiveWindow } from '../utils/marketSession';
import { marketQuoteSession, quoteDateKey, shouldPreferQuote } from '../utils/marketQuoteDate';
import { areStockCodesEquivalent } from '../utils/stockCode';
import { useUserAccess } from '../contexts/UserAccessContext';
import './MarketDashboardPage.css';
import { SourceRecordDrawer } from '../components/investmentMonitor/SourceRecordDrawer';
import { MobileHome } from '../components/mobile/MobileHome';
import { useIsMobile } from '../hooks/useIsMobile';
import { newsPublisher } from '../utils/newsPublisher';
import { stripHtmlText } from '../utils/plainText';

const DATA_SOURCE_LABELS: Record<string, string> = {
  'tushare.index_daily': 'Tushare 指数日线',
  'tushare.index_global': 'Tushare 全球指数',
  'tushare.legacy_snapshot': '收盘快照',
  'tushare.daily': 'Tushare 全市场日线',
  'tushare.moneyflow_ind_ths': 'Tushare 同花顺行业',
  'tushare.moneyflow_ind_dc': 'Tushare 东方财富行业',
  'akshare.sina_a_spot': '新浪全A股实时行情',
  'akshare.sina_sector_spot': '新浪行业板块实时行情',
  'tencent.snapshot': '腾讯实时行情',
};

const HOME_CACHE_KEY = 'dsa:home-dashboard:last-good:v3';
const HOME_CACHE_MAX_AGE_MS = 12 * 60 * 60 * 1_000;
type DashboardCache = { storedAt: number; data: HomeDashboard };
const memoryDashboardCache = new Map<string, DashboardCache>();
let requestCacheOwner: string | null = null;

function readDashboardCache(scope: string): HomeDashboard | null {
  const cached = memoryDashboardCache.get(scope);
  if (cached && Date.now() - cached.storedAt <= HOME_CACHE_MAX_AGE_MS) return cached.data;
  memoryDashboardCache.delete(scope);
  try {
    const raw = window.localStorage.getItem(`${HOME_CACHE_KEY}:${scope}`);
    if (!raw) return null;
    const stored = JSON.parse(raw) as Partial<DashboardCache>;
    if (stored.data && stored.storedAt && Date.now() - stored.storedAt <= HOME_CACHE_MAX_AGE_MS) {
      memoryDashboardCache.set(scope, { data: stored.data, storedAt: stored.storedAt });
      return stored.data;
    }
  } catch { /* Storage is optional; never fall back to the old unscoped cache. */ }
  return null;
}

function rememberDashboard(scope: string, value: HomeDashboard) {
  const cached = { storedAt: Date.now(), data: value };
  memoryDashboardCache.set(scope, cached);
  try { window.localStorage.setItem(`${HOME_CACHE_KEY}:${scope}`, JSON.stringify(cached)); } catch { /* optional cache */ }
}

function publicDashboard(value: HomeDashboard): HomeDashboard {
  // Share only market-wide fields. The watchlist and its evidence/summary must
  // never become another owner's initial snapshot, even during an outage.
  return {
    generatedAt: value.generatedAt, marketTime: value.marketTime, tradeDate: value.tradeDate,
    cnIndices: value.cnIndices, globalIndices: value.globalIndices, breadth: value.breadth,
    sectorDistribution: value.sectorDistribution, northbound: value.northbound,
    latestNews: value.latestNews, pinnedNews: value.pinnedNews, cache: value.cache,
    watchlist: [], latestEvents: [], intelligenceSummary: {}, warnings: [],
  };
}

function sourceLabel(value?: string | null) {
  return value ? (DATA_SOURCE_LABELS[value] ?? value) : '来源未标注';
}

function quoteMatchesStock(symbol: string, stockCode?: string | null): boolean {
  if (!stockCode || !areStockCodesEquivalent(symbol, stockCode)) return false;
  // Shared normalization supports naked codes and market aliases, but strips
  // CN exchanges. Retain an explicit venue on both sides: 000001.SH is not
  // 000001.SZ even when the realtime hook gives them the same lookup key.
  const exchange = (value: string) => {
    const code = value.trim().toUpperCase();
    const venue = code.match(/^\d+\.(SH|SS|SZ|BJ)$/)?.[1]
      ?? code.match(/^(SH|SZ|BJ)\.?\d{5,6}$/)?.[1];
    return venue === 'SS' ? 'SH' : venue;
  };
  const targetExchange = exchange(symbol);
  const candidateExchange = exchange(stockCode);
  return !targetExchange || !candidateExchange || targetExchange === candidateExchange;
}

function shortTime(value?: string | null) {
  const text = value?.trim();
  if (!text) return '时间未标注';
  // Daily rows represent a trading date, not an instant at midnight UTC.
  if (/^\d{4}[-/]?\d{2}[-/]?\d{2}$/.test(text)) return quoteDateKey(text) ?? '时间未标注';
  const clock = text.match(/[T\s]\d{2}:\d{2}(:\d{2})?/);
  if (!clock) return '时间未标注';
  const normalized = text.replaceAll('/', '-').replace(/^(\d{4})(\d{2})(\d{2})/, '$1-$2-$3').replace(/\s+/, 'T');
  // Match shouldPreferQuote: timezone-less market timestamps are Shanghai
  // local time; explicit offsets identify an instant in any source timezone.
  const instant = new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}+08:00`);
  if (Number.isNaN(instant.getTime())) return '时间未标注';
  const time = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', hourCycle: 'h23', hour: '2-digit', minute: '2-digit',
    ...(clock[1] ? { second: '2-digit' as const } : {}),
  }).format(instant);
  return `${quoteDateKey(instant.toISOString())} ${time}`;
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
    <div className="market-index-switch__body">
      <p className="market-index-switch__name">{item.name}</p>
      <div className="market-index-switch__value-row">
        <p className="market-index-switch__value tabular-nums">{formatNumber(item.close)}</p>
        <span className={`market-index-switch__change tabular-nums ${tone(verifiedChangePct)}`}>
          {session.canShowChange ? percent(verifiedChangePct) : '待核验'}
        </span>
      </div>
      <div className="market-index-switch__spark"><Sparkline points={item.history} positive={positive} height={28} /></div>
    </div>
  </button>;
}

function OverseasCard({ item }: { item: MarketIndexCard }) {
  const session = marketQuoteSession(item.updateTime ?? item.tradeDate, item.source);
  const verifiedChangePct = session.canShowChange ? item.changePct : null;
  // 面板标题已说明「最近收盘」，行内不再重复交易日，避免窄列里被省略和重叠
  return <div className="market-overseas-row border-b border-border/55 px-3 py-2 last:border-b-0">
    <div className="min-w-0">
      <p className="truncate text-[12px] font-medium text-foreground">{item.name}</p>
      <p className="truncate text-[12px] text-secondary-text">{item.region}</p>
    </div>
    <div className="text-right">
      <p className={`text-sm font-semibold tabular-nums ${tone(verifiedChangePct)}`}>{formatNumber(item.close)}</p>
      <p className={`text-[12px] font-medium tabular-nums ${tone(verifiedChangePct)}`}>{session.canShowChange ? percent(verifiedChangePct) : '待核验'}</p>
    </div>
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
  return <Link to={`/investment-monitor/feed?event=${encodeURIComponent(event.id)}`} aria-label={event.title}>{event.title}</Link>;
}

function WatchlistFocus({ card, onAnalyze }: { card: HomeWatchlistCard; onAnalyze: () => void }) {
  const [chartOpen, setChartOpen] = useState(false);
  const quote = card.latestQuote; const changePct = quote?.changePercent;
  const bullish = card.sentiment.bullish ?? 0; const bearish = card.sentiment.bearish ?? 0;
  const totalSentiment = Math.max(1, bullish + bearish + (card.sentiment.neutral ?? 0) + (card.sentiment.mixed ?? 0));
  return <div className={`market-panel market-watch-focus grid gap-px bg-border/60 ${chartOpen ? 'is-chart-open' : ''} xl:grid-cols-[minmax(0,1fr)_310px]`}>
    <div className="min-w-0 bg-card/90 p-3">
      <button type="button" className="market-watch-focus__toggle" aria-expanded={chartOpen} onClick={() => setChartOpen((open) => !open)}>
        {chartOpen ? `收起${card.name}分时走势` : `查看${card.name}分时走势`}
      </button>
      {/* 图始终挂载（避免重复请求与布局跳动），收起时只做视觉折叠 */}
      <div className={`market-watch-focus__chart${chartOpen ? '' : ' is-collapsed'}`}>
        <MarketTimeframeChart key={card.symbol} symbol={card.symbol} initialPeriod="intraday" initialRange="1d" compact />
      </div>
    </div>
    <aside className="bg-card/95 p-4">
      <div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h3 className="text-lg font-semibold">{card.name}</h3><Badge>{card.symbol}</Badge></div><div className="mt-2 flex items-baseline gap-2"><span className={`text-3xl font-semibold tabular-nums ${tone(changePct)}`}>{formatNumber(quote?.currentPrice)}</span><span className={`text-sm ${tone(changePct)}`}>{percent(changePct)}</span></div></div><button className="btn-primary inline-flex items-center gap-1.5 px-3 py-2" onClick={onAnalyze}><Sparkles className="h-3.5 w-3.5" />分析</button></div>
      <p className="mt-1 text-[12px] text-secondary-text">{shortTime(quote?.updateTime)} · {sourceLabel(quote?.source)}{quote?.isStale ? ' · 非连续竞价时段最新值' : ''}</p>
      <div className="mt-4 grid grid-cols-2 gap-px border border-border/60 bg-border/60 text-[12px]">
        {[["今开", quote?.open], ["最高", quote?.high], ["最低", quote?.low], ["成交额", formatCompact(quote?.amount)], ["秒量", quote?.secondVolume], ["秒成交额", formatCompact(quote?.secondAmount)]].map(([label, value]) => <div key={String(label)} className="flex justify-between bg-background/70 px-2 py-2"><span className="text-secondary-text">{label}</span><span className="tabular-nums">{typeof value === 'number' ? formatNumber(value, 0) : value ?? '—'}</span></div>)}
      </div>
      <div className="mt-4"><div className="flex justify-between text-[12px]"><span className="text-secondary-text">情报结构</span><span>{card.opportunityScore >= card.riskScore ? '机会占优' : '风险占优'}</span></div><div className="mt-2 h-1 bg-elevated"><div className="h-full bg-cyan" style={{ width: `${Math.round((bullish / totalSentiment) * 100)}%` }} /></div><div className="mt-1 flex justify-between text-[12px] text-secondary-text"><span>机会 {card.opportunityScore}</span><span>风险 {card.riskScore}</span><span>{card.eventCount} 条</span></div></div>
      <div className="mt-4 space-y-3 border-t border-border/60 pt-3 text-[12px]"><div><p className="text-secondary-text">机构最新</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestInstitution} /></p></div><div><p className="flex items-center gap-1 text-danger"><Zap className="h-3 w-3" />最新催化</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestCatalyst} /></p></div><div><p className="flex items-center gap-1 text-warning"><ShieldAlert className="h-3 w-3" />最新风险</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestRisk} /></p></div></div>
    </aside>
  </div>;
}

function NewsPanel({
  pinned, latest, onOpen, onOpenAll,
}: {
  pinned: MonitorEvent[];
  latest: MonitorEvent[];
  onOpen: (event: MonitorEvent) => void;
  onOpenAll: () => void;
}) {
  return <Card padding="none" className="market-news-panel p-3">
    <div className="market-news-heading">
      <div className="flex items-center gap-2"><Newspaper aria-hidden="true" size={17} /><h2>新闻速递</h2></div>
      <button type="button" onClick={onOpenAll}>查看全部新闻<ArrowRight aria-hidden="true" size={14} /></button>
    </div>
    {pinned.map(event => <button type="button" key={`pinned-${event.id}`} className="market-pinned-news" aria-label={`打开重要新闻：${event.title}`} onClick={() => onOpen(event)}>
      <div className="market-news-meta"><span className="market-news-priority"><Pin aria-hidden="true" size={12} />重点关注</span><time>{shortTime(event.eventAt)}</time>{newsPublisher(event) && <span>{newsPublisher(event)}</span>}</div>
      <h3>{stripHtmlText(event.title)}</h3>{stripHtmlText(event.summary) && <p>{stripHtmlText(event.summary)}</p>}
    </button>)}
    <div className="market-news-list">{latest.map(event => <button type="button" key={event.id} className="market-latest-event" aria-label={`查看新闻详情：${event.title}`} onClick={() => onOpen(event)}>
      <time dateTime={event.eventAt}>{new Date(event.eventAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}<small>{new Date(event.eventAt).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</small></time>
      <div className="market-news-copy"><h3>{stripHtmlText(event.title)}</h3><div className="market-news-meta">{newsPublisher(event) && <span>{newsPublisher(event)}</span>}{event.importanceScore >= 70 && <span className="market-news-priority">重要</span>}</div></div>
    </button>)}{!pinned.length && !latest.length ? <div className="market-news-empty"><p>暂无新闻</p><span>财经新闻同步后自动展示</span></div> : null}</div>
  </Card>;
}

const MarketDashboardPage = () => {
  const { user, loggedIn, isLoading, authMethod, accessEnabled } = useUserAccess();
  const owner = isLoading ? null : !accessEnabled ? 'local'
    : loggedIn && authMethod === 'admin' ? 'admin'
      : loggedIn && user?.status === 'approved' && user.id > 0 ? `user:${user.id}` : null;
  if (!owner) return <AppPage><p role="status">{isLoading ? '正在确认访问权限…' : '请登录已获准的账号查看个人自选股。'}</p></AppPage>;
  const scope = `${owner}:${authMethod ?? 'none'}:${accessEnabled}`;
  return <MarketDashboardContent key={scope} ownerScope={scope} />;
};

function MarketDashboardContent({ ownerScope }: { ownerScope: string }) {
  const navigate = useNavigate();
  const [selectedNews, setSelectedNews] = useState<MonitorEvent | null>(null);
  const [data, setData] = useState<HomeDashboard | null>(() => readDashboardCache(ownerScope) ?? readDashboardCache('public'));
  const [watchlistReady, setWatchlistReady] = useState(() => readDashboardCache(ownerScope) !== null);
  const dataRef = useRef<HomeDashboard | null>(data);
  const alive = useRef(false);
  const requestVersion = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const [loading, setLoading] = useState(() => !data); const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState('');
  const [selectedIndexCode, setSelectedIndexCode] = useState('000001.SH');
  const isMobile = useIsMobile();
  const load = useCallback(async (force = false, refresh = false) => {
    if (!alive.current) return;
    // The API cache predates personal dashboards and has one shared key. Drop
    // only that entry when the owner changes, retaining public quote caches.
    if (requestCacheOwner !== ownerScope) {
      invalidateCachedQueries('home:dashboard');
      requestCacheOwner = ownerScope;
    }
    const version = ++requestVersion.current;
    const hasExistingData = dataRef.current != null;
    if (force) setRefreshing(true); else if (!hasExistingData) setLoading(true);
    try {
      const next = await homeDashboardApi.get(force, refresh);
      if (!alive.current || version !== requestVersion.current) return;
      dataRef.current = next;
      setData(next);
      setWatchlistReady(true);
      rememberDashboard(ownerScope, next);
      rememberDashboard('public', publicDashboard(next));
      setError(null);
    }
    catch {
      if (!alive.current || version !== requestVersion.current) return;
      setError(hasExistingData
        ? '市场数据暂时无法刷新，已保留上一次成功数据，系统会自动重试。'
        : '正在等待本地数据服务准备完成，系统会自动重试，不需要刷新页面。');
    }
    finally {
      if (alive.current && version === requestVersion.current) { setLoading(false); setRefreshing(false); }
    }
  }, [ownerScope]);
  const refreshVisiblePage = useCallback(async () => {
    // The background worker already keeps the shared store current. Re-entering
    // the dashboard should read that store once, not synchronously wake every
    // upstream source and schedule three more duplicate probes.
    await load(false, false);
  }, [load]);
  usePageActivationRefresh(refreshVisiblePage, {
    intervalMs: 60_000, intervalGuard: isAshareLiveWindow, minIntervalMs: 10_000,
  });
  useEffect(() => {
    if (!error) return undefined;
    const timer = window.setTimeout(() => void load(), 8_000);
    return () => window.clearTimeout(timer);
  }, [error, load]);
  useEffect(() => { document.title = '行情 - 乐子乌智能投研平台'; }, []);

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
    const preferLive = live
      && quoteMatchesStock(card.symbol, live.stockCode)
      && Number.isFinite(live.currentPrice) && live.currentPrice > 0
      && shouldPreferQuote(live.updateTime, card.latestQuote?.updateTime, Boolean(live.isStale));
    return preferLive ? { ...card, latestQuote: {
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
    if (!live || !Number.isFinite(live.close) || (live.close ?? 0) <= 0
      || !shouldPreferQuote(live.updateTime, item.updateTime ?? item.tradeDate, Boolean(live.isStale))) return item;
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
  const breadthSession = marketQuoteSession(
    data?.breadth.updatedAt ?? data?.breadth.tradeDate,
    data?.breadth.source,
  );
  const sectorSession = marketQuoteSession(
    data?.sectorDistribution.updatedAt ?? data?.sectorDistribution.tradeDate,
    data?.sectorDistribution.source,
  );
  const breadthTotal = Math.max(1, data?.breadth.total ?? 0);
  const aiActions = [
    { label: '个股深度分析', note: '多维度研判', icon: BrainCircuit, action: () => navigate('/chat') },
    { label: '财报解读', note: '财务质量与预期', icon: FileSearch, action: () => navigate('/data-acquisition') },
    { label: '行业对比', note: '板块与竞争格局', icon: BarChart3, action: () => navigate('/screening') },
    { label: '事件影响评估', note: '催化与风险传导', icon: Activity, action: () => navigate('/chat') },
    { label: '智能问答', note: '带数据上下文提问', icon: MessageSquareText, action: () => navigate('/chat') },
  ];
  const pinnedNews = (data?.pinnedNews ?? []).slice(0, 1);
  const pinnedNewsIds = new Set(pinnedNews.map(item => item.id));
  const latestNews = (
    data?.latestNews?.length
      ? data.latestNews
      : (data?.latestEvents ?? []).filter(item => item.eventType === 'news' || item.eventType === 'long_news')
  ).filter(item => !pinnedNewsIds.has(item.id)).slice(0, 6);
  const openNews = (event: MonitorEvent) => setSelectedNews(event);

  const mobileIndexList = cnIndices.map(item => ({
    ...item,
    session: marketQuoteSession(item.updateTime ?? item.tradeDate, item.source),
  }));
  const mobileToneOf = (value?: number | null): 'up' | 'down' | 'flat' => ((value ?? 0) > 0 ? 'up' : (value ?? 0) < 0 ? 'down' : 'flat');
  const mobileValue = formatNumber(leadIndex?.close);
  const mobileChange = leadIndexSession.canShowChange
    ? percent(leadIndexChangePct)
    : '涨跌幅待核验';

  if (isMobile) {
    return (
      <AppPage className="market-command-center is-mobile">
        <MobileHome
          indices={mobileIndexList}
          activeCode={leadIndex?.code ?? '000001.SH'}
          onSelectIndex={setSelectedIndexCode}
          leadIndex={mobileIndexList.find(item => item.code === leadIndex?.code)}
          mainValue={mobileValue}
          mainChange={leadIndexSession.canShowChange ? mobileChange : '涨跌幅待核验'}
          mainTone={mobileToneOf(leadIndexChangePct)}
          changeLabel={leadIndexSession.label}
          watchlist={liveWatchlist.map(card => ({
            symbol: card.symbol,
            name: card.name,
            price: formatNumber(card.latestQuote?.currentPrice),
            change: card.latestQuote?.changePercent != null ? percent(card.latestQuote.changePercent) : '—',
            tone: mobileToneOf(card.latestQuote?.changePercent),
          }))}
          onOpenWatchlist={() => navigate('/super-watchlist')}
          onOpenStock={symbol => navigate(`/investment-monitor/feed?symbol=${encodeURIComponent(symbol)}`)}
          breadth={breadthAvailable && data?.breadth ? {
            up: data.breadth.up ?? 0,
            down: data.breadth.down ?? 0,
            flat: data.breadth.flat ?? 0,
            total: data.breadth.total ?? 0,
          } : null}
          breadthNote={breadthAvailable ? sourceLabel(data?.breadth.source) : ''}
          sectors={sectorDistributionAvailable ? {
            leaders: (data?.sectorDistribution.leaders ?? []).slice(0, 4).map(item => ({ name: item.name, value: percent(item.changePct) })),
            laggards: (data?.sectorDistribution.laggards ?? []).slice(0, 4).map(item => ({ name: item.name, value: percent(item.changePct) })),
          } : null}
          news={[
            ...pinnedNews.map(item => ({ id: String(item.id), title: item.title ?? '', time: shortTime(item.eventAt) })),
            ...latestNews.map(item => ({ id: String(item.id), title: item.title ?? '', time: shortTime(item.eventAt) })),
          ]}
          onOpenNews={id => {
            const found = [...pinnedNews, ...latestNews].find(item => String(item.id) === id);
            if (found) openNews(found);
          }}
          onOpenAllNews={() => navigate('/investment-monitor/feed?channel=news', { state: { returnTo: '/app' } })}
          quickActions={[
            { label: 'AI 问股', icon: MessageSquareText, onClick: () => navigate('/chat') },
            { label: '深度分析', icon: BrainCircuit, onClick: () => navigate(focusedWatchlist ? `/chat?stock=${encodeURIComponent(focusedWatchlist.symbol.split('.')[0])}&name=${encodeURIComponent(focusedWatchlist.name)}` : '/chat') },
            { label: '财报解读', icon: FileSearch, onClick: () => navigate('/data-acquisition') },
            { label: '任务', icon: ClipboardCheck, onClick: () => navigate('/tasks') },
          ]}
          onRefresh={() => void load(true)}
          refreshing={refreshing}
          loading={loading}
          error={error}
          updatedLabel={data?.marketTime ? `更新于 ${shortTime(data.marketTime)}` : undefined}
        />
        {selectedNews ? <SourceRecordDrawer event={selectedNews} homeNews onClose={() => setSelectedNews(null)} /> : null}
      </AppPage>
    );
  }

  return <AppPage className="market-command-center max-w-none px-2 pb-4 pt-1.5 md:px-3 lg:px-4">
    <div className="space-y-1.5">
      <section className="market-panel overflow-hidden border border-border/70 bg-card/90">
        <div className="market-overview-header flex flex-col border-b border-border/60 px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
          <div><div className="flex items-center gap-2"><RadioTower aria-hidden="true" className="h-[18px] w-[18px] text-text-2" /><h1 className="text-[length:var(--fs-h1)] font-semibold leading-[var(--lh-h1)]">行情</h1></div><p className="mt-1 text-[length:var(--fs-caption)] leading-[var(--lh-caption)] text-text-3">{data?.marketTime ? `更新于 ${shortTime(data.marketTime)}` : '正在同步市场时间'}</p></div>
          <button className="btn-secondary mt-2 inline-flex items-center gap-2 sm:mt-0" disabled={refreshing} onClick={() => void load(true)}><RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />刷新数据</button>
        </div>
        <div className="market-index-rail">{cnIndices.map((item) => <IndexTicker key={item.code} item={item} selected={item.code === leadIndex?.code} onSelect={() => setSelectedIndexCode(item.code)} />)}</div>
        {cnIndices.length ? <p className="market-index-source">行情来源：{[...new Set(cnIndices.map(item => sourceLabel(item.source)))].join(' / ')}</p> : null}
      </section>

      <EvidenceRail className="market-evidence-rail" items={[
        { label: '行情基准', value: leadIndexSession.label, note: sourceLabel(leadIndex?.source), tone: 'verified' },
        { label: '数据时间', value: shortTime(data?.marketTime), note: '与行情同步', tone: 'live' },
        { label: '市场广度', value: breadthAvailable ? `${data?.breadth.total ?? 0} 只股票` : '等待数据', note: sourceLabel(data?.breadth.source), tone: breadthAvailable ? 'verified' : 'warning' },
        { label: '自选股覆盖', value: `${liveWatchlist.length} 只`, note: '行情与情报使用同一标的', tone: liveWatchlist.length ? 'verified' : 'warning' },
      ]} />

      {error ? <div role="status" className="border border-warning/25 bg-warning/5 px-3 py-2 text-xs text-secondary-text">{error}</div> : null}
      <div className="market-primary-grid grid gap-1.5 xl:grid-cols-[minmax(0,1.28fr)_minmax(420px,.72fr)]">
        <section className="market-panel border border-border/70 bg-card/90 p-3">
          <div className="market-main-quote-head flex flex-wrap items-end justify-between gap-3 border-b border-border/55 pb-2"><div><div className="flex flex-wrap items-baseline gap-3"><h2 className="text-[length:var(--fs-h2)] font-semibold leading-[var(--lh-h2)]">{leadIndex?.name ?? '上证指数'}</h2><span className="text-[length:var(--fs-sm)] tabular-nums text-text-3">{leadIndex?.code ?? '000001.SH'}</span><p className={`text-[length:var(--fs-display)] font-bold leading-[var(--lh-display)] tabular-nums ${tone(leadIndexChangePct)}`}>{formatNumber(leadIndex?.close)}</p><span className={`text-xs ${tone(leadIndexChangePct)}`}>{leadIndexSession.canShowChange ? percent(leadIndexChangePct) : '涨跌幅待核验'}</span></div></div><p className="text-[length:var(--fs-caption)] leading-[var(--lh-caption)] text-text-3">{leadIndexSession.label} · {sourceLabel(leadIndex?.source)}</p></div>
          <MarketTimeframeChart key={leadIndex?.code ?? '000001.SH'} symbol={leadIndex?.code ?? '000001.SH'} assetType="index" initialPeriod="intraday" initialRange="1d" className="mt-2" compact />
        </section>
        <div className="market-right-rail">
          <NewsPanel pinned={pinnedNews} latest={latestNews} onOpen={openNews} onOpenAll={() => navigate('/investment-monitor/feed?channel=news', { state: { returnTo: '/app' } })} />
          {!pinnedNews.length && !latestNews.length ? (
            <Card padding="none" className="market-right-rail__fill p-3">
              <div className="market-news-heading"><div className="flex items-center gap-2"><Layers3 aria-hidden="true" size={17} /><h2>市场广度</h2></div></div>
              {breadthAvailable && data?.breadth ? (
                <>
                  <div className="market-breadth-ratio mt-3" aria-label={`上涨${data.breadth.up}家，下跌${data.breadth.down}家，平盘${data.breadth.flat}家`}>
                    <span className="is-up" style={{ width: `${(data.breadth.up / breadthTotal) * 100}%` }} />
                    <span className="is-down" style={{ width: `${(data.breadth.down / breadthTotal) * 100}%` }} />
                  </div>
                  <div className="mt-2 grid grid-cols-3 gap-px border border-border/60 bg-border/60 text-center text-[12px]">
                    <div className="bg-background/70 p-2"><p className="text-secondary-text">上涨</p><strong className="text-danger">{data.breadth.up}</strong></div>
                    <div className="bg-background/70 p-2"><p className="text-secondary-text">下跌</p><strong className="text-success">{data.breadth.down}</strong></div>
                    <div className="bg-background/70 p-2"><p className="text-secondary-text">平盘</p><strong>{data.breadth.flat}</strong></div>
                  </div>
                </>
              ) : <p className="mt-3 text-[12px] text-secondary-text">等待市场广度数据。</p>}
            </Card>
          ) : null}
        </div>
      </div>

      {breadthAvailable || sectorDistributionAvailable ? <div className={`grid gap-2 ${breadthAvailable && sectorDistributionAvailable ? 'xl:grid-cols-2' : ''}`}>
        {breadthAvailable ? <section className="market-panel border border-border/70 bg-card/90 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">市场广度</h2><p className="mt-0.5 text-[12px] text-secondary-text">{breadthSession.label} · {sourceLabel(data?.breadth.source)}</p></div><div className="flex gap-3 text-[12px]"><span className="text-danger">上涨 {data?.breadth.up}</span><span className="text-success">下跌 {data?.breadth.down}</span><span className="text-secondary-text">平盘 {data?.breadth.flat}</span></div></div>
          <>
            <div className="market-breadth-ratio mt-3" aria-label={`上涨${data?.breadth.up}家，下跌${data?.breadth.down}家，平盘${data?.breadth.flat}家`}><span className="is-up" style={{ width: `${(data?.breadth.up ?? 0) / breadthTotal * 100}%` }} /><span className="is-flat" style={{ width: `${(data?.breadth.flat ?? 0) / breadthTotal * 100}%` }} /><span className="is-down" style={{ width: `${(data?.breadth.down ?? 0) / breadthTotal * 100}%` }} /></div>
            <DistributionHistogram items={data?.breadth.distribution ?? []} />
            <div className="mt-2 grid grid-cols-3 gap-px border border-border/60 bg-border/60 text-center text-[12px]"><div className="bg-background/70 p-2"><p className="text-secondary-text">有效交易股票</p><p className="mt-1 text-xs">{data?.breadth.total}</p></div><div className="bg-background/70 p-2"><p className="text-secondary-text">涨停</p><p className="mt-1 text-xs text-danger">{data?.breadth.limitUp}</p></div><div className="bg-background/70 p-2"><p className="text-secondary-text">跌停</p><p className="mt-1 text-xs text-success">{data?.breadth.limitDown}</p></div></div>
          </>
        </section> : null}
        {sectorDistributionAvailable ? <section className="market-panel border border-border/70 bg-card/90 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold">行业涨跌分布</h2><p className="mt-0.5 text-[12px] text-secondary-text">{sectorSession.label} · {sourceLabel(data?.sectorDistribution.source)}</p></div><div className="flex gap-3 text-[12px]"><span className="text-danger">上涨 {data?.sectorDistribution.up}</span><span className="text-success">下跌 {data?.sectorDistribution.down}</span><span className="text-secondary-text">平盘 {data?.sectorDistribution.flat}</span></div></div>
          <>
            <DistributionHistogram items={data?.sectorDistribution.distribution ?? []} />
            <div className="market-sector-movers mt-2">
              <div><p className="market-sector-movers__title text-danger">领涨行业</p>{(data?.sectorDistribution.leaders ?? []).slice(0, 4).map(item => <div className="market-sector-movers__row" key={`up-${item.name}`}><span>{item.name}</span><span className="text-danger">{percent(item.changePct)}</span></div>)}</div>
              <div><p className="market-sector-movers__title text-success">领跌行业</p>{(data?.sectorDistribution.laggards ?? []).slice(0, 4).map(item => <div className="market-sector-movers__row" key={`down-${item.name}`}><span>{item.name}</span><span className="text-success">{percent(item.changePct)}</span></div>)}</div>
            </div>
          </>
        </section> : null}
      </div> : null}

      <div className="market-secondary-grid grid gap-1.5 xl:grid-cols-[minmax(0,1.4fr)_minmax(420px,.6fr)]">
        <section className="market-panel border border-border/70 bg-card/90"><div className="flex items-center justify-between border-b border-border/55 px-3 py-1.5"><div><h2 className="text-sm font-semibold">海外市场最近收盘</h2><p className="text-[12px] text-secondary-text">逐项显示实际交易日</p></div><Globe2 className="h-4 w-4 text-cyan" /></div><div className="market-overseas-list market-overseas-compact grid grid-cols-2 lg:grid-cols-3">{(data?.globalIndices ?? []).map((item) => <OverseasCard key={item.code} item={item} />)}</div></section>
        <Card padding="none" className="market-analysis-panel p-3"><div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold">分析入口</h2></div><Bot className="h-4 w-4 text-cyan" /></div><div className="market-ai-actions mt-2 grid grid-cols-3 gap-px bg-border/60">{aiActions.map((item) => <button key={item.label} onClick={item.action} className="market-ai-action group bg-background/60 p-2 text-left hover:bg-hover"><item.icon className="h-4 w-4 text-cyan" /><p className="market-ai-action__label mt-1.5 text-[12px] font-medium">{item.label}</p></button>)}</div><div className="mt-1.5 grid grid-cols-3 gap-px bg-border/60 text-center text-[12px]"><div className="bg-elevated/60 p-1.5"><p className="text-secondary-text">活跃源</p><p>{data?.intelligenceSummary.activeSourceCount ?? 0}</p></div><div className="bg-elevated/60 p-1.5"><p className="text-secondary-text">近7日情报</p><p>{data?.intelligenceSummary.eventCount ?? 0}</p></div><div className="bg-elevated/60 p-1.5"><p className="text-secondary-text">高优先级</p><p>{data?.intelligenceSummary.highPriorityCount ?? 0}</p></div></div></Card>
      </div>

      <section><div className="mb-2 flex flex-wrap items-end justify-between gap-2"><div><h2 className="text-base font-semibold">自选股行情与情报</h2></div><div className="market-watchlist-switcher flex flex-wrap items-center gap-1">{liveWatchlist.map(card => <button key={card.symbol} onClick={() => setSelectedSymbol(card.symbol)} className={`border px-3 py-2 text-left ${card.symbol === focusedWatchlist?.symbol ? 'border-cyan bg-cyan/10' : 'border-border bg-card'}`}><span className="text-[12px]">{card.name}</span><span className={`ml-2 text-[12px] tabular-nums ${tone(card.latestQuote?.changePercent)}`}>{formatNumber(card.latestQuote?.currentPrice)} {percent(card.latestQuote?.changePercent)}</span></button>)}<button className="btn-secondary inline-flex items-center gap-1.5" onClick={() => navigate(focusedWatchlist ? `/investment-monitor/feed?symbol=${encodeURIComponent(focusedWatchlist.symbol)}` : '/investment-monitor/feed')}><Building2 className="h-3.5 w-3.5" />证据原文<ArrowRight className="h-3.5 w-3.5" /></button></div></div>
        {!watchlistReady ? <p role="status" className="text-xs text-secondary-text">{error ? '当前账号的自选股尚未读取成功，正在自动重试。' : '正在读取当前账号的自选股…'}</p> : !loading && !liveWatchlist.length ? <EmptyState title="暂无自选股数据" description="请在设置中添加自选股，或等待情报台首次同步。" /> : focusedWatchlist ? <WatchlistFocus card={focusedWatchlist} onAnalyze={() => navigate(`/chat?stock=${encodeURIComponent(focusedWatchlist.symbol.split('.')[0])}&name=${encodeURIComponent(focusedWatchlist.name)}`)} /> : null}
      </section>

      {loading && !data ? <div role="status" className="border border-cyan/20 bg-cyan/5 px-4 py-3 text-xs text-secondary-text">正在读取本地缓存并聚合市场数据，页面会自动完成加载…</div> : null}
    </div>
    {selectedNews ? <SourceRecordDrawer event={selectedNews} homeNews onClose={() => setSelectedNews(null)} /> : null}
    <p className="market-disclaimer">数据仅供参考，不构成投资建议</p>
  </AppPage>;
}

export default MarketDashboardPage;
