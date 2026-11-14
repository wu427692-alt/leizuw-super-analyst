import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  Activity, ArrowRight, BarChart3, BrainCircuit, Building2, ClipboardCheck,
  FileSearch, MessageSquareText, Newspaper, Pin,
  RefreshCw, ShieldAlert, Sparkles, Zap,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { homeDashboardApi } from '../api/homeDashboard';
import { invalidateCachedQueries } from '../api/requestCache';
import {
  AppPage, Badge, BarMeter, Card, EmptyState, EvidenceRail, HeatCell, MetricRow, PageTabs, ShareBar, Sparkline,
  type PageTabItem,
} from '../components/common';
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

/** 涨跌方向；只给 Metric / 文本上色，不表达重要性（§11）。 */
const trendOf = (value?: number | null): 'up' | 'down' | 'flat' => (
  (value ?? 0) > 0 ? 'up' : (value ?? 0) < 0 ? 'down' : 'flat'
);

/** history → 纯数值序列；无效点直接丢弃，避免画出假趋势。 */
const sparkValues = (points?: MarketPoint[] | null): number[] => (
  (points ?? []).map((point) => Number(point?.value)).filter((value) => Number.isFinite(value))
);

/** 卡片头统一结构：标题 + 右侧 meta（统计入标题栏，不另占一行统计条）。 */
function Panel({
  title, meta, action, children, className,
}: {
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className ? `market-panel market-panel--desk ${className}` : 'market-panel market-panel--desk'}>
      <header className="market-panel__head">
        <h2>{title}</h2>
        {meta ? <span className="market-panel__meta">{meta}</span> : null}
        {action ? <div className="market-panel__action">{action}</div> : null}
      </header>
      <div className="market-panel__body">{children}</div>
    </section>
  );
}

/** 涨跌幅分档 → 密度格矩阵：左跌右涨，A 股红涨绿跌。 */
function HeatMatrix({ buckets, ariaLabel }: { buckets: Array<{ label: string; count: number }>; ariaLabel: string }) {
  if (!buckets.length) return <p className="market-empty">暂无分布数据</p>;
  const max = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const midpoint = Math.floor(buckets.length / 2);
  return (
    <div className="market-heat-grid" role="img" aria-label={ariaLabel}>
      {buckets.map((bucket, index) => {
        const sign = index < midpoint ? -1 : index > midpoint ? 1 : 0;
        return (
          <div className="market-heat-grid__col" key={bucket.label}>
            <HeatCell size={20} intensity={sign === 0 ? 0 : (bucket.count / max) * sign} ariaLabel={`${bucket.label} ${bucket.count} 家`} />
            <span className={`market-heat-grid__count is-${sign < 0 ? 'down' : sign > 0 ? 'up' : 'flat'}`}>{bucket.count}</span>
            <span className="market-heat-grid__label">{bucket.label}</span>
          </div>
        );
      })}
    </div>
  );
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
    <Sparkline values={sparkValues(item.history)} width={64} height={18} ariaLabel={`${item.name}近期走势`} />
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
  // 嵌在「自选股」面板里：不再套第二层卡片，只保留 1px 分割的左右分栏（§12）。
  return <div className={`market-watch-focus grid gap-px bg-border/60 ${chartOpen ? 'is-chart-open' : ''} xl:grid-cols-[minmax(0,1fr)_310px]`}>
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
      <div className="mt-4"><div className="flex justify-between text-[12px]"><span className="text-secondary-text">情报结构</span><span>{card.opportunityScore >= card.riskScore ? '机会占优' : '风险占优'}</span></div><div className="mt-2 h-1 bg-elevated"><div className="h-full" style={{ width: `${Math.round((bullish / totalSentiment) * 100)}%`, background: 'var(--chart-1)' }} /></div><div className="mt-1 flex justify-between text-[12px] text-secondary-text"><span>机会 {card.opportunityScore}</span><span>风险 {card.riskScore}</span><span>{card.eventCount} 条</span></div></div>
      <div className="mt-4 space-y-3 border-t border-border/60 pt-3 text-[12px]"><div><p className="text-secondary-text">机构最新</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestInstitution} /></p></div><div><p className="flex items-center gap-1 text-danger"><Zap className="h-3 w-3" />最新催化</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestCatalyst} /></p></div><div><p className="flex items-center gap-1 text-warning"><ShieldAlert className="h-3 w-3" />最新风险</p><p className="mt-1 line-clamp-2"><IntelligenceLine event={card.latestRisk} /></p></div></div>
    </aside>
  </div>;
}

/**
 * 快讯标题常常是「【一句话标题】后面跟整段正文」。列表里只要冒号前那句话，
 * 没有方括号就退回标题本身。
 */
function newsHeadline(value?: string | null): string {
  const raw = stripHtmlText(value).trim();
  if (!raw) return '';
  const bracketed = raw.match(/^【([^】]{2,48})】/);
  if (bracketed) return bracketed[1];
  return raw;
}

/** 手机端新闻只要「时刻」，跨天才带日期，避免一行塞满换行。 */
function newsClock(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const clock = date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  const day = newsDayLabel(value);
  return day ? `${day} ${clock}` : clock;
}

function newsDayLabel(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const now = new Date();
  if (date.toDateString() === now.toDateString()) return '';
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
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
      <time dateTime={event.eventAt} title={new Date(event.eventAt).toLocaleString('zh-CN')}>{new Date(event.eventAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</time>
      <div className="market-news-copy"><h3>{newsHeadline(event.title) || stripHtmlText(event.summary)}</h3><div className="market-news-meta">{newsPublisher(event) && <span>{newsPublisher(event)}</span>}{newsDayLabel(event.eventAt) ? <span>{newsDayLabel(event.eventAt)}</span> : null}</div></div>
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
  // 桌面端主 Tab：概览 / 市场结构 / 快讯（§10 一屏只讲一件事）。手机端不用这套。
  const [tab, setTab] = useState<'overview' | 'structure' | 'news'>('overview');
  const tabsRef = useRef<HTMLDivElement | null>(null);
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
  ).filter(item => !pinnedNewsIds.has(item.id)).slice(0, 8);
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

  /* ---------------- 桌面端派生数据：全部只做展示编码，不改数据获取 ---------------- */
  const indexMetrics = cnIndices.slice(0, 8).map((item) => {
    const session = marketQuoteSession(item.updateTime ?? item.tradeDate, item.source);
    const verified = session.canShowChange ? item.changePct : null;
    return {
      label: item.name,
      value: formatNumber(item.close),
      delta: session.canShowChange ? percent(verified) : '待核验',
      trend: trendOf(verified),
      viz: <Sparkline values={sparkValues(item.history)} width={64} height={18} ariaLabel={`${item.name}近期走势`} />,
    };
  });
  const sectorRankRows = sectorDistributionAvailable
    ? [...(data?.sectorDistribution.leaders ?? []), ...(data?.sectorDistribution.laggards ?? [])]
      .sort((left, right) => right.changePct - left.changePct)
    : [];
  const sectorRankMax = Math.max(0.01, ...sectorRankRows.map((item) => Math.abs(item.changePct)));
  const bullishCount = data?.intelligenceSummary?.bullishCount ?? 0;
  const bearishCount = data?.intelligenceSummary?.bearishCount ?? 0;
  const fundMetrics = [
    {
      label: '北向净流入',
      value: data?.northbound?.northMoneyYi != null
        ? `${data.northbound.northMoneyYi.toFixed(2)} 亿`
        : (data?.northbound?.northMoney != null ? formatCompact(data.northbound.northMoney) : null),
      hint: data?.northbound?.tradeDate ? quoteDateKey(data.northbound.tradeDate) ?? undefined : undefined,
    },
    {
      label: '南向净流入',
      value: data?.northbound?.southMoney != null ? formatCompact(data.northbound.southMoney) : null,
    },
    { label: '近 7 日情报', value: data?.intelligenceSummary?.eventCount ?? null },
    { label: '高优先级', value: data?.intelligenceSummary?.highPriorityCount ?? null },
  ];
  const switchTab = (key: string) => {
    setTab(key as 'overview' | 'structure' | 'news');
    // 切 Tab 滚回内容顶部（§10）；jsdom 没有实现 scrollIntoView，用可选调用。
    tabsRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' });
  };
  const deskTabs: PageTabItem[] = [
    { key: 'overview', label: '行情概览' },
    { key: 'structure', label: '市场结构' },
    { key: 'news', label: '快讯', badge: (pinnedNews.length + latestNews.length) || undefined },
  ];

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
            ...pinnedNews.map(item => ({ id: String(item.id), title: newsHeadline(item.title) || (item.title ?? ''), time: newsClock(item.eventAt), source: newsPublisher(item) })),
            ...latestNews.map(item => ({ id: String(item.id), title: newsHeadline(item.title) || (item.title ?? ''), time: newsClock(item.eventAt), source: newsPublisher(item) })),
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
          updatedLabel={data?.marketTime ? String(data.marketTime).slice(5, 16) : undefined}
          onSearch={() => navigate('/super-watchlist')}
          indexValue={item => formatNumber(item.close)}
          indexChange={item => (item.session.canShowChange ? percent(item.changePct) : '—')}
        />
        {selectedNews ? <SourceRecordDrawer event={selectedNews} homeNews onClose={() => setSelectedNews(null)} /> : null}
      </AppPage>
    );
  }

  const watchRow = (card: HomeWatchlistCard) => {
    const quote = card.latestQuote;
    const selected = card.symbol === focusedWatchlist?.symbol;
    const bull = Math.max(0, card.opportunityScore ?? 0);
    const bear = Math.max(0, card.riskScore ?? 0);
    return <button
      key={card.symbol}
      type="button"
      aria-pressed={selected}
      className={`market-watch-row${selected ? ' is-selected' : ''}`}
      onClick={() => setSelectedSymbol(card.symbol)}
    >
      <span className="market-watch-row__name">
        <strong>{card.name}</strong>
        <small>{card.symbol}</small>
      </span>
      <Sparkline values={sparkValues(card.history)} width={72} height={18} ariaLabel={`${card.name}近期走势`} />
      <span className={`market-watch-row__quote tabular-nums ${tone(quote?.changePercent)}`}>
        {formatNumber(quote?.currentPrice)} {percent(quote?.changePercent)}
      </span>
      <span className="market-watch-row__meter">
        <ShareBar
          height={4}
          segments={[
            { key: 'opp', value: bull, color: 'var(--chart-1)', label: '机会' },
            { key: 'risk', value: bear, color: 'var(--chart-4)', label: '风险' },
          ]}
          ariaLabel={`${card.name}机会 ${bull} 风险 ${bear}`}
        />
        <small>机会 {bull} · 风险 {bear}</small>
      </span>
    </button>;
  };

  return <AppPage className="market-command-center market-command-center--desk max-w-none px-2 pb-4 pt-1.5 md:px-3 lg:px-4">
    <div className="market-desk">
      <header className="market-overview-header market-desk__head">
        <div className="market-desk__title">
          <h1 className="text-[length:var(--fs-h1)] font-semibold leading-[var(--lh-h1)]">行情</h1>
          <p className="market-desk__meta">{data?.marketTime ? `更新于 ${shortTime(data.marketTime)}` : '正在同步市场时间'}</p>
        </div>
        <div className="market-desk__actions">
          <span className="market-desk__meta">{leadIndexSession.label} · {sourceLabel(leadIndex?.source)}</span>
          <button className="btn-secondary inline-flex items-center gap-2" disabled={refreshing} onClick={() => void load(true)}>
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />刷新数据
          </button>
        </div>
      </header>

      {error ? <div role="status" className="market-desk__error">{error}</div> : null}

      {/* 数据溯源：每个面板自己标基线来源；这里只留页面级基准与时间，不再重复广度/自选统计（§11） */}
      <EvidenceRail className="market-evidence-rail" items={[
        { label: '行情基准', value: leadIndexSession.label, note: sourceLabel(leadIndex?.source), tone: 'verified' },
        { label: '数据时间', value: shortTime(data?.marketTime), note: '与行情同步', tone: 'live' },
      ]} />

      <div ref={tabsRef}>
        <PageTabs items={deskTabs} value={tab} onChange={switchTab} ariaLabel="行情视图切换" />
      </div>

      {tab === 'overview' ? (
        <div className="market-tab-body">
          <Panel
            title="主要指数"
            meta={cnIndices.length ? `${cnIndices.length} 个 · 点数 / 涨跌幅 / 近期走势` : '等待数据'}
          >
            {indexMetrics.length
              ? <MetricRow columns={4} items={indexMetrics} className="market-index-metrics" />
              : <p className="market-empty" role="status">指数数据暂缺。</p>}
          </Panel>

          <Panel
            title={leadIndex?.name ?? '上证指数'}
            meta={`${leadIndex?.code ?? '000001.SH'} · ${leadIndexSession.label} · ${sourceLabel(leadIndex?.source)}`}
            action={(
              <label className="market-index-pick">
                <span>切换指数</span>
                <select
                  aria-label="选择指数"
                  value={leadIndex?.code ?? ''}
                  onChange={(event) => setSelectedIndexCode(event.target.value)}
                >
                  {cnIndices.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
                </select>
              </label>
            )}
          >
            <div className="market-lead-quote">
              <b className={`tabular-nums ${tone(leadIndexChangePct)}`}>{formatNumber(leadIndex?.close)}</b>
              <span className={`tabular-nums ${tone(leadIndexChangePct)}`}>
                {leadIndexSession.canShowChange ? percent(leadIndexChangePct) : '涨跌幅待核验'}
              </span>
              <Sparkline values={sparkValues(leadIndex?.history)} width={120} height={22} ariaLabel={`${leadIndex?.name ?? '指数'}近期走势`} />
            </div>
            <MarketTimeframeChart
              key={leadIndex?.code ?? '000001.SH'}
              symbol={leadIndex?.code ?? '000001.SH'}
              assetType="index"
              initialPeriod="intraday"
              initialRange="1d"
              compact
            />
          </Panel>

          <Panel
            title="自选股"
            meta={liveWatchlist.length ? `${liveWatchlist.length} 只 · 走势 / 价格 / 情报结构` : undefined}
            action={(
              <button
                type="button"
                className="btn-secondary inline-flex items-center gap-1.5"
                onClick={() => navigate(focusedWatchlist ? `/investment-monitor/feed?symbol=${encodeURIComponent(focusedWatchlist.symbol)}` : '/investment-monitor/feed')}
              >
                <Building2 className="h-3.5 w-3.5" />证据原文<ArrowRight className="h-3.5 w-3.5" />
              </button>
            )}
          >
            {!watchlistReady ? (
              <p role="status" className="market-empty">{error ? '当前账号的自选股尚未读取成功，正在自动重试。' : '正在读取当前账号的自选股…'}</p>
            ) : !loading && !liveWatchlist.length ? (
              <EmptyState title="暂无自选股数据" description="请在设置中添加自选股，或等待情报台首次同步。" />
            ) : (
              <>
                <div className="market-watch-rows">{liveWatchlist.map(watchRow)}</div>
                {focusedWatchlist ? (
                  <WatchlistFocus
                    card={focusedWatchlist}
                    onAnalyze={() => navigate(`/chat?stock=${encodeURIComponent(focusedWatchlist.symbol.split('.')[0])}&name=${encodeURIComponent(focusedWatchlist.name)}`)}
                  />
                ) : null}
              </>
            )}
          </Panel>

          <Panel title="快捷分析" meta="复用本页行情上下文" className="market-panel--tools">
            <div className="market-quick-actions">
              {aiActions.map((item) => (
                <button key={item.label} type="button" onClick={item.action}>
                  <item.icon size={16} aria-hidden="true" />
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          </Panel>
        </div>
      ) : null}

      {tab === 'structure' ? (
        <div className="market-tab-body">
          <Panel
            title="市场广度"
            meta={breadthAvailable ? `${data?.breadth.total ?? 0} 只 · 涨停 ${data?.breadth.limitUp ?? 0} · 跌停 ${data?.breadth.limitDown ?? 0}` : '等待数据'}
          >
            {breadthAvailable && data?.breadth ? (
              <>
                <ShareBar
                  height={6}
                  ariaLabel={`上涨 ${data.breadth.up ?? 0} 家，平盘 ${data.breadth.flat ?? 0} 家，下跌 ${data.breadth.down ?? 0} 家`}
                  segments={[
                    { key: 'up', value: data.breadth.up ?? 0, color: 'var(--up)', label: '上涨' },
                    { key: 'flat', value: data.breadth.flat ?? 0, color: 'var(--flat)', label: '平盘' },
                    { key: 'down', value: data.breadth.down ?? 0, color: 'var(--down)', label: '下跌' },
                  ]}
                />
                <div className="market-legend">
                  <span className="is-up">上涨 {data.breadth.up ?? 0}</span>
                  <span className="is-flat">平盘 {data.breadth.flat ?? 0}</span>
                  <span className="is-down">下跌 {data.breadth.down ?? 0}</span>
                  <span className="market-legend__note">{breadthSession.label} · {sourceLabel(data.breadth.source)}</span>
                </div>
                <DistributionHistogram items={data.breadth.distribution ?? []} />
              </>
            ) : <p className="market-empty" role="status">等待市场广度数据。</p>}
          </Panel>

          <Panel
            title="行业强弱"
            meta={sectorDistributionAvailable ? `领涨 ${data?.sectorDistribution.leaders?.length ?? 0} · 领跌 ${data?.sectorDistribution.laggards?.length ?? 0}` : '等待数据'}
          >
            {sectorDistributionAvailable ? (
              <>
                <ShareBar
                  height={6}
                  ariaLabel={`行业上涨 ${data?.sectorDistribution.up ?? 0}，平盘 ${data?.sectorDistribution.flat ?? 0}，下跌 ${data?.sectorDistribution.down ?? 0}`}
                  segments={[
                    { key: 'up', value: data?.sectorDistribution.up ?? 0, color: 'var(--up)', label: '上涨' },
                    { key: 'flat', value: data?.sectorDistribution.flat ?? 0, color: 'var(--flat)', label: '平盘' },
                    { key: 'down', value: data?.sectorDistribution.down ?? 0, color: 'var(--down)', label: '下跌' },
                  ]}
                />
                <div className="market-rank">
                  {sectorRankRows.map((item) => (
                    <div className="market-rank__row" key={`${item.name}-${item.changePct}`}>
                      <span className="market-rank__name">{item.name}{item.companyCount ? <small>{item.companyCount} 家</small> : null}</span>
                      <span className={`market-rank__bar ${item.changePct < 0 ? 'is-down' : 'is-up'}`}>
                        <BarMeter
                          value={item.changePct}
                          max={sectorRankMax}
                          height={4}
                          baseline="center"
                          ariaLabel={`${item.name} ${percent(item.changePct)}`}
                        />
                      </span>
                      <b className={`tabular-nums ${tone(item.changePct)}`}>{percent(item.changePct)}</b>
                    </div>
                  ))}
                </div>
                <HeatMatrix buckets={data?.sectorDistribution.distribution ?? []} ariaLabel="行业涨跌幅分布" />
                <p className="market-source">{sectorSession.label} · {sourceLabel(data?.sectorDistribution.source)}</p>
              </>
            ) : <p className="market-empty" role="status">等待行业板块数据。</p>}
          </Panel>

          <Panel title="海外市场" meta={`最近收盘 · ${data?.globalIndices?.length ?? 0} 个指数`}>
            {data?.globalIndices?.length ? (
              <div className="market-overseas-list market-overseas-compact grid grid-cols-2 lg:grid-cols-3">
                {data.globalIndices.map((item) => <OverseasCard key={item.code} item={item} />)}
              </div>
            ) : <p className="market-empty">暂无海外指数数据。</p>}
          </Panel>

          <Panel title="资金与情报" meta={`活跃源 ${data?.intelligenceSummary?.activeSourceCount ?? 0} 个`}>
            <MetricRow columns={4} items={fundMetrics} className="market-fund-metrics" />
            <div className="market-sentiment">
              <div className="market-legend">
                <span className="is-up">看多 {bullishCount}</span>
                <span className="is-down">看空 {bearishCount}</span>
                <span className="market-legend__note">情报倾向，不代表涨跌预测</span>
              </div>
              <ShareBar
                height={6}
                ariaLabel={`看多 ${bullishCount}，看空 ${bearishCount}`}
                segments={[
                  { key: 'bull', value: bullishCount, color: 'var(--up)', label: '看多' },
                  { key: 'bear', value: bearishCount, color: 'var(--down)', label: '看空' },
                ]}
              />
            </div>
          </Panel>
        </div>
      ) : null}

      {tab === 'news' ? (
        <div className="market-tab-body">
          <NewsPanel
            pinned={pinnedNews}
            latest={latestNews}
            onOpen={openNews}
            onOpenAll={() => navigate('/investment-monitor/feed?channel=news', { state: { returnTo: '/app' } })}
          />
        </div>
      ) : null}

      {loading && !data ? <div role="status" className="market-desk__error">正在读取本地缓存并聚合市场数据，页面会自动完成加载…</div> : null}
    </div>
    {selectedNews ? <SourceRecordDrawer event={selectedNews} homeNews onClose={() => setSelectedNews(null)} /> : null}
  </AppPage>;
}

export default MarketDashboardPage;
