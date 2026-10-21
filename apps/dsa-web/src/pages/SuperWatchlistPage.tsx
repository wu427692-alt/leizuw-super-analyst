import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Archive, ArrowUpRight, BrainCircuit, CalendarClock, ChevronDown, ExternalLink, FileText, Image as ImageIcon, Layers3, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { essayRadarApi } from '../api/essayRadar';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { systemConfigApi } from '../api/systemConfig';
import { AppPage, ConfirmDialog, Drawer, EmptyState } from '../components/common';
import { MarketTimeframeChart } from '../components/market';
import { SourceRecordDrawer, type FinancialRecordSection } from '../components/investmentMonitor/SourceRecordDrawer';
import { evidenceTime, researchInstant, researchTime, sourceRecordUrl } from '../components/investmentMonitor/investmentMonitorMeta';
import { StockAutocomplete } from '../components/StockAutocomplete/StockAutocomplete';
import type { EssayConsensusAnalysis, EssayExpectationEstimate, FundamentalSource, FundamentalSourceKind, MonitorEvent, SuperWatchlistStock } from '../types/investmentMonitor';
import { useRealtimeQuotes } from '../hooks/useRealtimeQuotes';
import { useStockIndex } from '../hooks/useStockIndex';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import { useWatchlistNavigation, SECTIONS, PRIMARY_SECTIONS, MATERIAL_FILTERS, MESSAGE_FILTERS } from '../hooks/useWatchlistNavigation';
import type { Section, MaterialFilter, MessageFilter } from '../hooks/useWatchlistNavigation';
import type { RealtimeQuote } from '../api/realtimeQuotes';
import type { ResearchNoteDetail } from '../types/essayRadar';
import { formatMarketQuoteTimestamp, quoteDateKey, shouldPreferQuote } from '../utils/marketQuoteDate';
import { directResearchFirst, RESEARCH_MATCH_FIELDS, RESEARCH_RELATION_LABELS, stockResearchRelation } from '../utils/stockResearchRelation';
import './SuperWatchlistPage.css';

type WorkspaceView = 'overview' | 'full';
const workspaceKey = (symbol: string, view: WorkspaceView) => `${symbol}:${view}`;
function messageKind(event: MonitorEvent): Exclude<MessageFilter, 'all'> | 'other' {
  if (event.eventType === 'news' || event.eventType === 'long_news') return 'news';
  if (event.eventType === 'essay') return 'opinions';
  if (['enterprise_registration', 'enterprise_risk', 'enterprise_credit', 'enterprise_ipr', 'enterprise_history'].includes(event.eventType)) return 'enterprise';
  return 'other';
}

const SNAPSHOT_RECORD_TYPES = new Set([
  'realtime_quote', 'market_open_snapshot', 'market_snapshot', 'fundamental_snapshot',
  'capital_chip_snapshot', 'ownership_snapshot', 'technical_factor', 'stock_comment_snapshot',
]);
function isSnapshotRecord(event: MonitorEvent) {
  const evidence = event.metrics._evidence as { contentNature?: string } | undefined;
  return evidence?.contentNature === 'derived_summary' || SNAPSHOT_RECORD_TYPES.has(event.eventType);
}

function consensusIsRunning(analysis?: EssayConsensusAnalysis) {
  return analysis?.status === 'pending' || analysis?.status === 'processing';
}

function savedConsensusSupersedes(saved: EssayConsensusAnalysis | undefined, observed: EssayConsensusAnalysis | undefined) {
  if (!saved || !observed || saved.status === 'not_loaded' || saved.status === 'not_started') return false;
  // updatedAt is the job revision. completedAt can still belong to an earlier
  // report while a new job runs, so it must not decide which job is current.
  const savedAt = researchInstant(saved.updatedAt);
  const observedAt = researchInstant(observed.updatedAt);
  if (!Number.isFinite(savedAt) || !Number.isFinite(observedAt)) return false;
  if (savedAt !== observedAt) return savedAt > observedAt;
  if (consensusIsRunning(saved) && !consensusIsRunning(observed)) return false;
  // Discovery may update the candidate projection without modifying the job.
  // Prefer the full workspace at the same revision, not a permanent poll copy.
  return true;
}

function number(value: unknown, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—';
}
function percent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
}
function ratioPercent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '—';
}
function reportDate(value?: string | null) {
  return value?.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') || '未标注';
}
function capitalValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}
function capitalRate(value: unknown) {
  const rate = capitalValue(value);
  return rate !== null && rate >= 0 && rate <= 100 ? `${rate.toFixed(2)}%` : '—';
}
function capitalDifference(buy: unknown, sell: unknown): number | null {
  const left = capitalValue(buy), right = capitalValue(sell);
  return left !== null && right !== null && left >= 0 && right >= 0 ? capitalValue(left - right) : null;
}
function capitalMoney(value: unknown, inWan = false, signed = false) {
  const amount = capitalValue(value);
  const yuan = amount === null ? null : capitalValue(amount * (inWan ? 10_000 : 1));
  if (yuan === null) return '—';
  const unit = Math.abs(yuan) >= 100_000_000 ? 100_000_000 : Math.abs(yuan) >= 10_000 ? 10_000 : 1;
  return `${signed && yuan > 0 ? '+' : ''}${number(yuan / unit)} ${unit === 100_000_000 ? '亿元' : unit === 10_000 ? '万元' : '元'}`;
}

function CapitalTrading({ capital }: { capital: SuperWatchlistStock['capital'] }) {
  const flow = capital.moneyflow ?? {}, margin = capital.margin ?? {};
  // These are within-bucket differences, not a reconstruction of net_mf_amount.
  // Tushare moneyflow amounts are 万元; margin_detail amounts are 元.
  const orders = [
    { label: '小单', value: capitalDifference(flow.buySmAmount, flow.sellSmAmount) },
    { label: '中单', value: capitalDifference(flow.buyMdAmount, flow.sellMdAmount) },
    { label: '大单', value: capitalDifference(flow.buyLgAmount, flow.sellLgAmount) },
    { label: '特大单', value: capitalDifference(flow.buyElgAmount, flow.sellElgAmount) },
  ];
  const scale = Math.max(0, ...orders.map(row => Math.abs(row.value ?? 0)));
  const netFlow = capitalValue(flow.netMfAmount);
  const financingNet = capitalDifference(margin.rzmre, margin.rzche);
  return <>
    <header className="super-section-heading"><div><h3>资金交易观察</h3><p>已保存的交易日快照，不是盘中实时资金，也不推断连续流入。</p></div></header>
    <div className="super-capital-trading">
      <section aria-label="个股资金流" className="super-capital-source">
        <h4>个股资金流</h4><p>资金流日期 {reportDate(capital.moneyflowTradeDate)}</p>
        <Metric label="资金净流入" value={capitalMoney(netFlow, true, true)} />
        <dl className="super-order-flow" aria-label="按订单规模的买卖净额">
          {orders.map(row => <div key={row.label} className={row.value === null || row.value === 0 ? '' : row.value > 0 ? 'is-inflow' : 'is-outflow'}>
            <dt>{row.label}</dt><dd><span className="super-order-track" aria-hidden="true"><i style={{ width: `${scale > 0 && row.value !== null ? Math.abs(row.value) / scale * 50 : 0}%`, ...(row.value !== null && row.value < 0 ? { right: '50%' } : { left: '50%' }) }} /></span><span>{capitalMoney(row.value, true, true)}</span></dd>
          </div>)}
        </dl>
        <p>分档净额＝该档买入－卖出；不能相加替代上方资金净流入。订单大小不等于机构身份。</p>
      </section>
      <section aria-label="融资交易" className="super-capital-source">
        <h4>融资交易</h4><p>融资日期 {reportDate(capital.marginTradeDate)}</p>
        <div className="super-financing-metrics"><Metric label="融资净买入" value={capitalMoney(financingNet, false, true)} /><Metric label="融资余额" value={capitalMoney(capitalValue(margin.rzye) !== null && Number(margin.rzye) >= 0 ? margin.rzye : null)} /><Metric label="融资买入额" value={capitalMoney(capitalValue(margin.rzmre) !== null && Number(margin.rzmre) >= 0 ? margin.rzmre : null)} /><Metric label="融资偿还额" value={capitalMoney(capitalValue(margin.rzche) !== null && Number(margin.rzche) >= 0 ? margin.rzche : null)} /></div>
        <p>净买入＝买入－偿还；缺任一项不计算。余额是存量，不是本日流入；不与资金流直接相加。</p>
      </section>
    </div>
  </>;
}

function marketPresentation(market: SuperWatchlistStock['market']) {
  const hasPrice = typeof market.price === 'number' && Number.isFinite(market.price) && market.price > 0
    && market.quoteKind !== 'unavailable';
  const asOf = formatMarketQuoteTimestamp(market.updatedAt);
  const changePct = hasPrice && asOf ? market.changePct : null;
  const label = !hasPrice ? '行情尚未取得' : !asOf ? '报价时间未标注'
    : `${market.quoteKind === 'daily_close' ? '收盘行情' : market.isStale ? '历史行情快照' : '行情快照'} · ${asOf}`;
  return {
    label, price: hasPrice ? number(market.price) : '—', change: percent(changePct),
    tone: typeof changePct === 'number' && Number.isFinite(changePct) ? Math.sign(changePct) : 0,
  };
}

function quoteForSymbol(symbol: string, live?: RealtimeQuote): RealtimeQuote | undefined {
  if (!live) return undefined;
  const target = symbol.trim().toUpperCase();
  const candidate = live.stockCode?.trim().toUpperCase();
  if (!candidate) return undefined;
  // A map slot is not proof of identity: reject wrong-company/venue responses.
  return candidate === target || (/^\d{6}$/.test(candidate) && target.split('.')[0] === candidate)
    || (/^\d{6}$/.test(target) && candidate.split('.')[0] === target) ? live : undefined;
}

function applyRealtimeQuote(stock: SuperWatchlistStock, candidate?: RealtimeQuote): SuperWatchlistStock {
  const live = quoteForSymbol(stock.symbol, candidate);
  if (!live || !Number.isFinite(live.currentPrice) || live.currentPrice <= 0
    || !shouldPreferQuote(live.updateTime, stock.market.updatedAt, Boolean(live.isStale))) return stock;
  const sameDay = quoteDateKey(live.updateTime) === quoteDateKey(stock.market.updatedAt);
  return {
    ...stock,
    market: {
      ...stock.market,
      price: live.currentPrice,
      changePct: live.changePercent ?? (live.prevClose && live.prevClose > 0 ? (live.currentPrice / live.prevClose - 1) * 100 : null),
      prevClose: live.prevClose ?? null,
      change: live.change ?? (live.prevClose && live.prevClose > 0 ? live.currentPrice - live.prevClose : null),
      open: live.open ?? (sameDay ? stock.market.open : null),
      high: live.high ?? (sameDay ? stock.market.high : null),
      low: live.low ?? (sameDay ? stock.market.low : null),
      amount: live.amount ?? (sameDay ? stock.market.amount : null),
      updatedAt: live.updateTime,
      source: live.source ?? '共享行情快照',
      quoteKind: 'snapshot',
      isStale: Boolean(live.isStale),
      staleSeconds: live.staleSeconds ?? null,
    },
  };
}

function newerWorkspace(first?: SuperWatchlistStock, second?: SuperWatchlistStock) {
  if (!first || !second) return first ?? second;
  return shouldPreferQuote(second.market.updatedAt, first.market.updatedAt, Boolean(second.market.isStale)) ? second : first;
}

function EvidenceRow({ event, stockSymbol, showMessageKind = false, onEssayOpen, onForumOpen }: { event: MonitorEvent; stockSymbol: string; showMessageKind?: boolean; onEssayOpen?: (event: MonitorEvent) => void; onForumOpen?: (event: MonitorEvent) => void }) {
  const [reading, setReading] = useState(false);
  const evidence = (event.metrics._evidence ?? {}) as { evidenceLevel?: string };
  const isForumPost = event.eventType === 'stock_forum_post';
  const forumAuthor = typeof event.metrics.author === 'string' ? event.metrics.author : event.sourceName;
  const forumReplies = typeof event.metrics.replyCount === 'number' && Number.isFinite(event.metrics.replyCount) && event.metrics.replyCount >= 0 ? event.metrics.replyCount : null;
  const unverified = evidence.evidenceLevel === 'unverified' || event.eventType === 'essay' || isForumPost;
  const relation = stockResearchRelation(event, stockSymbol);
  const excerpt = relation?.kind !== 'unknown' && ['title', 'abstract', 'industry'].includes(relation?.matchField ?? '') ? relation?.matchText : null;
  const relationLabel = relation?.kind === 'other_company_mention' && excerpt && ['title', 'abstract'].includes(relation.matchField ?? '')
    ? '其他公司提及' : relation ? RESEARCH_RELATION_LABELS[relation.kind] : '';
  const body = <>
    <span className="super-evidence-copy">
      {relation ? <span className={`super-evidence-relation is-${relation.kind}`}><b>{relationLabel}</b>{(relation.subjectName || relation.subjectSymbol) && relation.kind === 'other_company_mention' ? <span>研报主体：{[relation.subjectName, relation.subjectSymbol].filter(Boolean).join(' · ')}</span> : null}</span> : null}
      <strong>{event.title}</strong><span className="super-evidence-meta"><time>{evidenceTime(event)}</time><span>{isForumPost ? forumAuthor : event.sourceName}</span>{showMessageKind ? <span>{MESSAGE_FILTERS.find(item => item.value === messageKind(event))?.label ?? '未分类消息'}</span> : null}<span className={unverified ? 'is-unverified' : ''}>{unverified ? '待核验' : '来源记录'}</span></span>
    </span>
    <span className="super-evidence-open">{isForumPost ? forumReplies === null ? '详情' : `${forumReplies}评 · 详情` : event.eventType === 'essay' ? '查看原文' : '查看资料'}<FileText size={14} /></span>
  </>;
  const cls = 'super-evidence-row';
  if (event.eventType === 'essay' && onEssayOpen) return <button type="button" className={`${cls} w-full`} onClick={() => onEssayOpen(event)}>{body}</button>;
  if (isForumPost && onForumOpen) return <button type="button" className={`${cls} w-full`} onClick={() => onForumOpen(event)}>{body}</button>;
  return <div className="super-evidence-item"><button type="button" className={`${cls} w-full`} onClick={() => setReading(true)}>{body}</button>{excerpt ? <details className="super-evidence-match"><summary>{RESEARCH_MATCH_FIELDS[relation?.matchField ?? ''] || '原文'}匹配依据</summary><p>{excerpt}</p><small>仅说明资料为何被关联，不代表其中观点已被验证。</small></details> : null}{reading ? <SourceRecordDrawer event={event} onClose={() => setReading(false)} /> : null}</div>;
}

function ForumPostDrawer({ event, onClose }: { event: MonitorEvent; onClose: () => void }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; detail: MonitorEvent | null; denied?: boolean } | null>(null);
  const [failedImages, setFailedImages] = useState<Record<string, boolean>>({});
  const request = useRef<{ key: string; promise: Promise<MonitorEvent> } | null>(null);
  const requestKey = `${event.id}:${attempt}`;
  const loading = result?.key !== requestKey;
  const failed = !loading && !result?.detail;
  const detail = !loading && result?.detail ? result.detail : event;
  useEffect(() => {
    let cancelled = false;
    if (request.current?.key !== requestKey) {
      request.current = { key: requestKey, promise: investmentMonitorApi.event(event.id) };
    }
    void request.current.promise.then(value => {
      if (value.id !== event.id) throw new Error('Mismatched event');
      if (!cancelled) setResult({ key: requestKey, detail: value });
    }).catch(error => {
      const status = error?.response?.status;
      if (!cancelled) setResult(previous => ({ key: requestKey, detail: null, denied: status === 401 || status === 403 || previous?.denied }));
    });
    return () => { cancelled = true; };
  }, [event.id, requestKey]);

  // A denied read revokes the seed too. Keep it hidden during subsequent retry
  // attempts until this mounted reader receives an authorized success.
  if (result?.denied) return <Drawer isOpen onClose={onClose} title="股评详情" width="max-w-3xl"><div className="space-y-4 text-[13px]"><p role="status">当前账号无法读取该股评，请重新登录或确认访问权限。</p>{loading ? <p role="status" className="text-secondary-text">正在重新读取详情…</p> : null}<button type="button" disabled={loading} className="min-h-10 border border-border/70 px-3 font-semibold hover:bg-hover disabled:opacity-50" onClick={() => setAttempt(value => value + 1)}>重试读取详情</button></div></Drawer>;
  const author = typeof detail.metrics.author === 'string' ? detail.metrics.author : detail.actors[0] || '作者未标注';
  const source = detail.sourceName || event.sourceName || '来源未标注';
  const originalUrl = sourceRecordUrl(detail.url);
  const counts = [
    { label: '浏览', value: detail.metrics.views },
    { label: '回复', value: detail.metrics.replyCount },
    { label: '点赞', value: detail.metrics.likeCount },
  ].filter(item => typeof item.value === 'number' && Number.isFinite(item.value) && item.value >= 0);
  const images = Array.isArray(detail.metrics.imageUrls)
    ? detail.metrics.imageUrls.map(sourceRecordUrl).filter((value): value is string => value !== null)
    : [];

  return <Drawer isOpen onClose={onClose} title="股评详情" width="max-w-3xl">
    <article className="space-y-5 text-[13px]">
      <header className="border-b border-border/70 pb-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-secondary-text"><span className="font-semibold text-foreground">{author}</span><span>{evidenceTime(detail)}</span><span>{source}</span><span>公开观点 · 待核验</span></div>
        {counts.length ? <dl aria-label="来源提供的互动数据" className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-[12px] text-secondary-text">{counts.map(item => <div key={item.label} className="flex gap-2"><dt>{item.label}</dt><dd>{number(item.value, 0)}</dd></div>)}</dl> : null}
      </header>
      {loading ? <p role="status" className="text-secondary-text">正在读取已入库详情，以下为列表摘录。</p> : null}
      {failed ? <div role="status" className="flex flex-wrap items-center justify-between gap-3 border border-border/70 p-3"><p>详情暂未读取成功，以下仍为列表摘录。</p><button type="button" className="min-h-10 border border-border/70 px-3 font-semibold hover:bg-hover" onClick={() => setAttempt(value => value + 1)}>重试读取详情</button></div> : null}
      <section><h3 className="mb-2 flex items-center gap-2 font-bold"><FileText className="h-4 w-4" />已入库股评摘录</h3><p className="mb-3 text-[12px] leading-6 text-secondary-text">来自公开列表的摘录，不保证包含完整帖子；请结合来源原帖核对。</p><div className="whitespace-pre-wrap break-words border border-border/70 bg-background/30 p-4 text-[14px] leading-7 text-foreground">{detail.summary || '这条记录没有正文摘录。'}</div></section>
      {images.length ? <section><h3 className="mb-3 flex items-center gap-2 font-bold"><ImageIcon className="h-4 w-4" />帖子图片</h3><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{images.map((image, index) => <a key={`${image}-${index}`} href={image} target="_blank" rel="noreferrer" className="overflow-hidden border border-border/70 bg-background/30">{failedImages[image] ? <p className="p-4 text-secondary-text">图片暂未载入，可打开原图查看。</p> : <img src={image} alt={`股评图片 ${index + 1}`} loading="lazy" onError={() => setFailedImages(value => ({ ...value, [image]: true }))} className="max-h-80 w-full object-contain" />}<span className="flex items-center justify-between px-3 py-2 text-[12px] text-secondary-text">查看原图<ExternalLink className="h-3 w-3" /></span></a>)}</div></section> : null}
      {originalUrl ? <footer className="border-t border-border/70 pt-4"><a href={originalUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-10 items-center gap-2 border border-border/70 px-3 font-semibold text-[#FF4F00] hover:bg-hover">查看来源原帖<ExternalLink className="h-3.5 w-3.5" /></a></footer> : null}
    </article>
  </Drawer>;
}

function EssayOriginalDrawer({ event, onClose }: { event: MonitorEvent; onClose: () => void }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; note: ResearchNoteDetail | null; denied?: boolean } | null>(null);
  const [failedImages, setFailedImages] = useState<Record<string, boolean>>({});
  const request = useRef<{ key: string; promise: Promise<ResearchNoteDetail> } | null>(null);
  const requestKey = `${event.externalId}:${attempt}`;
  const loading = result?.key !== requestKey;
  const note = !loading ? result?.note : null;
  const failed = !loading && !note;
  useEffect(() => {
    let cancelled = false;
    if (request.current?.key !== requestKey) {
      request.current = { key: requestKey, promise: essayRadarApi.note(event.externalId) };
    }
    void request.current.promise.then(value => {
      if (value.topicId !== event.externalId) throw new Error('Mismatched topic');
      if (!cancelled) setResult({ key: requestKey, note: value });
    }).catch(error => {
      const status = error?.response?.status;
      if (!cancelled) setResult(previous => ({ key: requestKey, note: null, denied: status === 401 || status === 403 || previous?.denied }));
    });
    return () => { cancelled = true; };
  }, [event.externalId, requestKey]);
  if (result?.denied) return <Drawer isOpen onClose={onClose} title="小作文原文" width="max-w-3xl"><div className="space-y-4 text-[13px]"><p role="status">当前账号无法读取该原文，请重新登录或确认访问权限。</p>{loading ? <p role="status" className="text-secondary-text">正在重新读取原文…</p> : null}<button type="button" disabled={loading} className="min-h-10 border border-border/70 px-3 font-semibold hover:bg-hover disabled:opacity-50" onClick={() => setAttempt(value => value + 1)}>重试读取原文</button></div></Drawer>;
  const assetUrl = (value: unknown): string | null => {
    if (typeof value !== 'string' || [...value].some(char => char === '\\' || char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) return null;
    if (!value.startsWith('/api/')) return sourceRecordUrl(value);
    // Only explicit same-origin API paths are accepted as relative media URLs;
    // resolving dot segments must not turn them into arbitrary site routes.
    try {
      const url = new URL(value, window.location.origin);
      return url.origin === window.location.origin && url.pathname.startsWith('/api/') ? url.href : null;
    } catch { return null; }
  };
  const originalUrl = sourceRecordUrl(event.url);
  return <Drawer isOpen onClose={onClose} title={note?.title || event.title || '小作文原文'} width="max-w-3xl">
    <div className="space-y-5 text-[13px]">
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-border/70 pb-4 text-secondary-text"><span>{note?.groupName || event.sourceName}</span><span>{note?.authorName || event.actors[0] || '作者未标注'}</span><span>{researchTime(note?.createdAt || event.eventAt)}</span><span>知识星球 · 待核验</span></div>
      {loading ? <p role="status" className="py-4 text-secondary-text">正在读取已入库原文…</p> : null}
      {failed ? <div role="status" className="flex flex-wrap items-center justify-between gap-3 border border-border/70 p-3"><p>原文暂未读取成功，可以重试或前往来源查看。</p><button type="button" className="min-h-10 border border-border/70 px-3 font-semibold hover:bg-hover" onClick={() => setAttempt(value => value + 1)}>重试读取原文</button></div> : null}
      {note ? <>
        <section><h3 className="mb-3 flex items-center gap-2 font-bold"><FileText className="h-4 w-4" />已入库原文</h3><div className="whitespace-pre-wrap break-words border border-border/70 bg-background/30 p-4 text-[14px] leading-7 text-foreground">{note.content || (note.images.length || note.files.length ? '本条没有文字正文，下方保留图片与附件记录。' : '本条未保存文字正文、图片或附件，请前往来源查看。')}</div></section>
        {note.images.length ? <section><h3 className="mb-3 flex items-center gap-2 font-bold"><ImageIcon className="h-4 w-4" />原文图片</h3><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{note.images.map((image, index) => {
          const url = assetUrl(image.viewUrl);
          return url ? <a key={image.imageId || index} href={url} target="_blank" rel="noreferrer" className="overflow-hidden border border-border/70 bg-background/30">{failedImages[url] ? <p className="p-4 text-secondary-text">图片暂未载入，可打开原图查看。</p> : <img src={url} alt={`小作文图片 ${index + 1}`} loading="lazy" onError={() => setFailedImages(value => ({ ...value, [url]: true }))} className="max-h-72 w-full object-contain" />}<span className="flex items-center justify-between px-3 py-2 text-[12px] text-secondary-text">图片 {index + 1}<ExternalLink className="h-3 w-3" /></span></a> : <p key={image.imageId || index} className="border border-border/70 p-3 text-secondary-text">图片 {index + 1} · 暂无可用链接</p>;
        })}</div></section> : null}
        {note.files.length ? <section><h3 className="mb-3 flex items-center gap-2 font-bold"><Archive className="h-4 w-4" />原文附件</h3><div className="space-y-2">{note.files.map((file, index) => {
          const viewUrl = assetUrl(file.viewUrl);
          const url = viewUrl || assetUrl(file.downloadUrl);
          const name = file.name || `附件 ${index + 1}`;
          const body = <><span className="flex min-w-0 items-start gap-2"><FileText className="mt-0.5 h-4 w-4 shrink-0" /><span className="break-words [overflow-wrap:anywhere]">{name}</span></span><span className="shrink-0 text-[12px] text-secondary-text">{url ? viewUrl ? '打开' : '下载' : '暂无链接'}</span></>;
          return url ? <a key={file.fileId || index} href={url} target="_blank" rel="noreferrer" className="flex items-start justify-between gap-3 border border-border/70 bg-background/30 px-3 py-3 hover:bg-hover">{body}</a> : <div key={file.fileId || index} className="flex items-start justify-between gap-3 border border-border/70 px-3 py-3">{body}</div>;
        })}</div></section> : null}
      </> : null}
      {originalUrl ? <div className="border-t border-border/70 pt-4"><a href={originalUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-10 items-center gap-2 border border-border/70 px-3 font-semibold text-[#FF4F00] hover:bg-hover">在知识星球打开<ExternalLink className="h-3.5 w-3.5" /></a></div> : null}
    </div>
  </Drawer>;
}

function Metric({ label, value, tone = '', note }: { label: string; value: string; tone?: string; note?: string }) {
  return <div className="super-metric"><p>{label}</p><strong className={tone}>{value}</strong>{note ? <small>{note}</small> : null}</div>;
}

function financialSource(financial: SuperWatchlistStock['fundamentals'], kind: FundamentalSourceKind): FundamentalSource | undefined {
  // Only old responses without the per-table contract may use the shared id.
  if (financial.sources !== undefined) return financial.sources?.[kind];
  return {
    eventId: financial.sourceEventId ?? null, source: financial.source ?? null,
    period: financial[`${kind}Period`] ?? null, announcedAt: null, reportType: null, selectionStatus: 'selected',
  };
}

function financialReaderKey(stock: SuperWatchlistStock) {
  const financial = stock.fundamentals;
  const sources = financial.sources === undefined ? ['legacy', financial.sourceEventId] : ['tables', ...(['income', 'cashflow', 'indicator'] as const).map(kind => {
    const source = financial.sources?.[kind];
    return source ? [source.eventId, source.selectionStatus, sourceIds(source.candidateEventIds).slice(0, 5), source.period, source.announcedAt, source.reportType, source.source] : null;
  })];
  return JSON.stringify([sources, stock.valuation.sourceEventId]);
}

function OverviewFinancial({ stock, onOpen }: { stock: SuperWatchlistStock; onOpen: () => void }) {
  const financial = stock.fundamentals;
  const income = financialSource(financial, 'income'), cashflow = financialSource(financial, 'cashflow'), indicator = financialSource(financial, 'indicator');
  const incomeConflict = income?.selectionStatus === 'conflict', cashflowConflict = cashflow?.selectionStatus === 'conflict', indicatorConflict = indicator?.selectionStatus === 'conflict';
  const revenueLabel = financial.revenueYoyField === 'q_sales_yoy' ? '单季收入同比'
    : financial.revenueYoyField === 'or_yoy' ? '累计收入同比'
    : financial.revenueYoyField === 'tr_yoy' ? '累计总收入同比' : '收入同比（来源口径）';
  const profitLabel = financial.netProfitYoyField === 'q_netprofit_yoy' ? '单季归母利润同比'
    : financial.netProfitYoyField === 'netprofit_yoy' ? '累计归母利润同比' : '利润同比（来源口径）';
  const metrics = [
    { label: '营业收入', value: capitalMoney(incomeConflict ? null : financial.revenue), date: income?.period, conflict: incomeConflict, growthLabel: revenueLabel, growth: indicatorConflict ? null : financial.revenueYoy },
    { label: '归母净利润', value: capitalMoney(incomeConflict ? null : financial.netProfit), date: income?.period, conflict: incomeConflict, growthLabel: profitLabel, growth: indicatorConflict ? null : financial.netProfitYoy },
    { label: '经营现金流净额', value: capitalMoney(cashflowConflict ? null : financial.operatingCashflow), date: cashflow?.period, conflict: cashflowConflict },
    { label: 'PE(TTM)', value: capitalValue(stock.valuation.peTtm) === null ? '—' : `${number(stock.valuation.peTtm)} 倍`, date: stock.valuation.asOf, valuation: true },
  ];
  return <section className="super-brief-financial" aria-label="经营与估值摘要">
    <div><h3>经营与估值</h3><p>金额、增长指标和估值分别标注日期</p></div>
    <button type="button" onClick={onOpen}>查看财务与资金 <ArrowUpRight size={14} /></button>
    <div className="super-brief-metrics">{metrics.map(metric => <section key={metric.label} className="super-business-metric" aria-label={metric.label}>
      <h4>{metric.label}</h4><strong>{metric.value}</strong>
      <p>{metric.valuation ? '估值日期' : '报告期'} {reportDate(metric.date)}</p>
      {metric.conflict ? <p className="super-finance-conflict">同期间存在不同版本，待核对</p> : null}
      {metric.growthLabel ? <div className="super-business-growth"><span>{metric.growthLabel} <b>{percent(metric.growth)}</b></span><span>指标期 {reportDate(indicator?.period)}{indicatorConflict ? ' · 不同版本待核对' : ''}</span></div> : null}
    </section>)}</div>
  </section>;
}

function Overview({ stock, onSection, onEssayOpen, onForumOpen }: {
  stock: SuperWatchlistStock; onSection: (section: Section, material?: MaterialFilter) => void;
  onEssayOpen: (event: MonitorEvent) => void; onForumOpen: (event: MonitorEvent) => void;
}) {
  const groups = stock.overview?.sections ?? [
    { key: 'announcements', label: '公司公告', items: stock.company.announcements },
    { key: 'research', label: '券商研报', items: stock.institution.latest },
    { key: 'opinions', label: '机构段子', items: stock.alternative.essays },
    { key: 'news', label: '相关新闻', items: stock.messages.items.filter(item => messageKind(item) === 'news') },
  ];
  const routes: Record<string, Section> = { announcements: 'institution', research: 'institution', opinions: 'essay', news: 'messages' };
  const groupMaterial = (key: string): MaterialFilter => key === 'announcements' ? 'announcements' : key === 'research' ? 'research' : 'all';
  return <section className="super-brief" aria-label="当前股票研究摘要">
    <header className="super-section-heading"><div><h3>值得先看的资料</h3><p>近183天已入库预览 · 每类最多2条，本公司研报优先；不是全库清单。</p></div><button type="button" onClick={() => onSection('evidence')}>更多资料 <ArrowUpRight size={14} /></button></header>
    <div className="super-brief-grid">{groups.map(group => <section key={group.key} className="super-brief-channel" aria-label={`${group.label}预览`}><header><h4>{group.label}</h4><button type="button" onClick={() => onSection(routes[group.key] ?? 'evidence', groupMaterial(group.key))} aria-label={`查看${group.label}`}>更多 <ArrowUpRight size={14} /></button></header><div className="super-brief-channel-items">{group.items.length ? (group.key === 'research' ? directResearchFirst(group.items, stock.symbol) : group.items).slice(0, 2).map(event => <EvidenceRow key={event.id} event={event} stockSymbol={stock.symbol} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />) : <p className="super-quiet-empty">当前预览未载入</p>}</div></section>)}</div>
  </section>;
}

const EXPECTATION_METRIC_LABELS: Record<string, string> = {
  revenue: '收入', net_profit: '净利润', eps: 'EPS', target_price: '目标价', market_cap: '目标市值',
  valuation_multiple: '估值倍数', cash_flow: '现金流', margin: '利润率', growth_rate: '增速', other: '其他推测',
};
const CONSENSUS_STATUS_LABELS: Record<string, string> = {
  not_started: '尚未分析', pending: '等待分析', processing: 'AI 分析中', completed: '分析完成', failed: '分析失败', stale: '有新小作文待更新',
};
const SUBJECT_RELATION_LABELS: Record<string, string> = {
  target_stock: '上市公司', consolidated: '合并口径', subsidiary: '子公司', acquisition_target: '收购标的', business_segment: '业务分部',
};

function expectationAt(item: EssayExpectationEstimate) {
  return Number.isFinite(researchInstant(item.proposedAt)) ? item.proposedAt : item.eventAt;
}

function compareExpectations(a: EssayExpectationEstimate, b: EssayExpectationEstimate) {
  return (researchInstant(expectationAt(b)) || 0) - (researchInstant(expectationAt(a)) || 0);
}

function ExpectationMatrix({ essay, stockName, onOpen }: {
  essay: EssayConsensusAnalysis; stockName: string; onOpen: (item: EssayExpectationEstimate) => void;
}) {
  const [metric, setMetric] = useState('all');
  const [page, setPage] = useState(1);
  const metrics = [...new Set(essay.estimates.map(item => item.metric))];
  const activeMetric = metric === 'all' || metrics.includes(metric) ? metric : 'all';
  const ordered = [...essay.estimates].sort(compareExpectations);
  const filtered = ordered.filter(item => activeMetric === 'all' || item.metric === activeMetric);
  const pageCount = Math.max(1, Math.ceil(filtered.length / 6));
  const currentPage = Math.min(page, pageCount);
  return <section className="super-expectation-matrix" aria-label="预期证据">
    <header><div><Layers3 /><div><h4>关键预期证据</h4><p>按提出时间排列；点击核对原文，不同预测期不混算。</p></div></div><span>{essay.estimates.length} 条</span></header>
    {essay.estimates.length > 0 ? <>
      <div className="super-matrix-tools"><label>筛选指标<select aria-label="筛选预期指标" value={activeMetric} onChange={event => { setMetric(event.target.value); setPage(1); }}><option value="all">全部指标</option>{metrics.map(value => <option key={value} value={value}>{EXPECTATION_METRIC_LABELS[value] || value}</option>)}</select></label><span>{filtered.length} 条 · 每页 6 条</span></div>
      <div className="super-matrix-scroll"><table><thead><tr>{['提出时间', '预测期', '指标', '原文预期', '主体口径', '样本'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{filtered.slice((currentPage - 1) * 6, currentPage * 6).map((item, index) => <tr key={`${item.topicId}-${item.metric}-${index}`} role="button" tabIndex={0} aria-label={`查看原文 ${item.title || ''} ${item.valueText}`} onClick={() => onOpen(item)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen(item); } }}>
        <td data-label="提出时间"><time>{researchTime(expectationAt(item))}</time></td><td data-label="预测期"><b>{item.period || '未注明'}</b></td><td data-label="指标"><span>{EXPECTATION_METRIC_LABELS[item.metric] || item.metric}</span></td><td className="super-matrix-claim" data-label="原文预期"><strong>{item.valueText}</strong><small>{item.unit ? `原文单位：${item.unit}` : '单位未注明，不换算'} · {item.evidence}</small></td><td data-label="主体口径">{item.subject || stockName}<small>{SUBJECT_RELATION_LABELS[item.subjectRelation ?? 'target_stock'] || '关联主体'}</small></td><td data-label="样本"><em className={item.sourceKind === 'dedicated' ? 'is-dedicated' : ''}>{item.sourceKind === 'dedicated' ? '单股专属' : '相关'}</em></td>
      </tr>)}</tbody></table></div>
      {pageCount > 1 ? <nav className="super-library-pages" aria-label="预期证据分页"><button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>上一页</button><span>第 {currentPage} / {pageCount} 页</span><button type="button" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button></nav> : null}
    </> : <EmptyState title="报告未提取到明确预期" description="这份报告未提取到可追溯的数值预期，可核对报告结论与引用原文。" />}
  </section>;
}

type BrokerMetric = 'eps' | 'np' | 'pe' | 'roe';
const BROKER_METRICS: Record<BrokerMetric, string> = { eps: 'EPS', np: '净利润', pe: 'PE', roe: 'ROE' };
const sourceIds = (value?: number[]) => Array.isArray(value)
  ? [...new Set(value.filter(id => typeof id === 'number' && Number.isSafeInteger(id) && id > 0))] : [];

function BrokerSources({ notes }: { notes: NonNullable<SuperWatchlistStock['consensus']['brokerSample']>['sourceNotes'] }) {
  const [sourcePage, setSourcePage] = useState(1);
  const [readingId, setReadingId] = useState<number | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const pages = Math.max(1, Math.ceil(notes.length / 12));
  const page = Math.min(sourcePage, pages);
  const changePage = (next: number) => {
    setSourcePage(next);
    listRef.current?.focus({ preventScroll: true });
    listRef.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
  };
  return <>
    <ol ref={listRef} tabIndex={-1} aria-label="券商计算来源列表" className="super-broker-sources">{notes.slice((page - 1) * 12, page * 12).map((note, index) => <li key={`${note.eventId}-${index}`}>
      {sourceIds([note.eventId]).length ? <button type="button" onClick={() => setReadingId(note.eventId)}><strong>{note.title || '研报来源记录'}</strong><span>{note.orgName || '机构未标注'} · {researchTime(note.eventAt)} · 页内核对 <ArrowUpRight size={13} /></span></button>
        : <div><strong>{note.title || '研报来源记录'}</strong><span>未保存有效来源标识，无法打开详情</span></div>}
    </li>)}</ol>
    {pages > 1 ? <nav className="super-library-pages" aria-label="券商来源分页"><button type="button" disabled={page === 1} onClick={() => changePage(page - 1)}>上一页</button><span>第 {page} / {pages} 页</span><button type="button" disabled={page === pages} onClick={() => changePage(page + 1)}>下一页</button></nav> : null}
    {readingId !== null ? <SourceRecordDrawer eventId={readingId} onClose={() => setReadingId(null)} /> : null}
  </>;
}

function BrokerForecastPanel({ consensus }: { consensus: SuperWatchlistStock['consensus'] }) {
  const [selection, setSelection] = useState<{ period: string; metric: BrokerMetric } | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const sourceRegion = useRef<HTMLDivElement>(null);
  const focusSources = useRef(false);
  const sample = consensus.brokerSample;
  const hasDocumentMedian = Object.hasOwn(consensus.targetPrice, 'documentMedian');
  const medianLabel = hasDocumentMedian ? '文档目标价中位（元）' : '目标价端点中位（元）';
  const median = hasDocumentMedian ? consensus.targetPrice.documentMedian : consensus.targetPrice.median;
  const notes = sample?.sourceNotes ?? [];
  const selectedRow = consensus.forecasts.find(row => row.period === selection?.period);
  const selectedIds = selection ? sourceIds(selectedRow?.metricSourceEventIds?.[selection.metric]) : [];
  const selectedNotes = selectedIds.map(id => notes.find(note => note.eventId === id) ?? { eventId: id, title: '研报来源记录' });
  const selectionLabel = selection ? `${selection.period} · ${BROKER_METRICS[selection.metric]}` : '';
  const visibleNotes = selection ? selectedNotes : notes;
  const chooseMetric = (period: string, metric: BrokerMetric) => {
    focusSources.current = true;
    setSourcesOpen(true);
    setSelection({ period, metric });
  };
  useEffect(() => {
    if (!focusSources.current) return;
    focusSources.current = false;
    sourceRegion.current?.focus({ preventScroll: true });
    sourceRegion.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
  }, [selection]);
  const numericCell = (row: SuperWatchlistStock['consensus']['forecasts'][number], metric: BrokerMetric, value: unknown) => {
    const count = row.metricSampleCounts?.[metric];
    const conflicts = row.conflictingMetricCounts?.[metric];
    const ids = sourceIds(row.metricSourceEventIds?.[metric]);
    const label = `${row.period} ${BROKER_METRICS[metric]} 计算来源`;
    const unit = metric === 'np' ? '亿元' : metric === 'eps' ? '元' : metric === 'pe' ? '倍' : '（源值）';
    const content = <><span className="super-broker-number">{number(value)}</span>{count != null ? <small>{count} 份有效样本</small> : null}</>;
    return <>{ids.length && typeof value === 'number' && Number.isFinite(value)
      ? <button type="button" className="super-broker-value" aria-label={`${label}：${number(value)}${unit}${count != null ? `，${count}份有效样本` : ''}`} aria-pressed={selection?.period === row.period && selection.metric === metric} onClick={() => chooseMetric(row.period, metric)}>{content}</button> : content}
      {typeof conflicts === 'number' && conflicts > 0 ? <small>剔除 {conflicts} 份冲突样本</small> : null}</>;
  };
  const hasTargets = [consensus.targetPrice.min, median, consensus.targetPrice.max].some(value => typeof value === 'number' && Number.isFinite(value) && value > 0);
  const hasBrokerMaterials = consensus.forecasts.length > 0 || notes.length > 0 || consensus.brokerReportCount > 0
    || (sample?.inputRecordCount ?? 0) > 0 || (sample?.loadedRecordCount ?? 0) > 0
    || hasTargets;
  if (!hasBrokerMaterials) return <section className="super-broker-panel"><header><div><h4>券商预测样本</h4><p>当前响应暂无可用券商预测；与小作文分析独立，可在资料中核对已入库研报。</p></div></header></section>;
  return <section className="super-broker-panel">
    <header><div><h4>券商预测样本</h4><p>同预测期分别统计；点击带下划线的数字，核对参与该项计算的研报。</p></div></header>
    <p className="super-broker-scope">{sample?.loaded
      ? `本次读取 ${sample.loadedRecordCount ?? sample.inputRecordCount} 条，${(sample.relationExcludedCount ?? 0) > 0 ? `排除 ${sample.relationExcludedCount} 条他股或标的冲突记录后，` : ''}去重后 ${sample.reportCount} 份研报，其中 ${sample.validReportCount} 份含有效数字。${sample.truncated ? `仅取最近 ${sample.sampleLimit ?? sample.loadedRecordCount ?? sample.inputRecordCount} 条，非窗口全部材料。` : ''} 可确认 ${sample.knownInstitutionCount} 家机构；另有 ${sample.unknownInstitutionReportCount} 份未标注机构。`
      : '旧接口未记录本次计算来源和去重文档数，不用已收录总数代替。'}
      {sample?.loaded ? ` 材料日期 ${researchTime(sample.oldestAt)} 至 ${researchTime(sample.asOf)}。` : ''}
      {(sample?.missingPeriodReportCount ?? 0) > 0 ? ` ${sample!.missingPeriodReportCount} 份含未标注预测期的记录，不混入分期中位数。` : ''}
      {(sample?.unknownRelationCount ?? 0) > 0 ? ` ${sample!.unknownRelationCount} 份旧记录尚不能确认标的归属，仍纳入样本；使用结果前需核对来源。` : ''}
    </p>
    {hasTargets ? <><div className="super-broker-targets"><Metric label="原文目标价下限（元）" value={number(consensus.targetPrice.min)} /><Metric label={medianLabel} value={number(median)} /><Metric label="原文目标价上限（元）" value={number(consensus.targetPrice.max)} /></div>
    <p className="super-broker-scope">{hasDocumentMedian ? `目标价基于 ${consensus.targetPrice.documentCount ?? 0} 份研报：每篇区间先取中点，再按文档等权求中位；不是按机构等权。` : '旧口径按目标价区间端点求中位，不是文档或机构等权。'} 下限与上限保留原文区间；目标价不是收益承诺。</p></> : null}
    <div className="super-broker-table" role="region" aria-label="券商分期预测，可横向滚动" tabIndex={0}><table><thead><tr><th>预测期</th><th>{sample?.loaded ? '文档' : '旧计数'}</th><th>EPS（元）</th><th>净利润（亿元）</th><th>PE（倍）</th><th>ROE（源值）</th></tr></thead><tbody>{consensus.forecasts.map(row => <tr key={row.period}>
      <td>{row.period}</td><td>{row.reportCount ?? row.sampleCount}</td><td>{numericCell(row, 'eps', row.epsMedian)}</td><td>{numericCell(row, 'np', typeof row.npMedian === 'number' ? row.npMedian / 10_000 : null)}</td><td>{numericCell(row, 'pe', row.peMedian)}</td><td>{numericCell(row, 'roe', row.roeMedian)}</td>
    </tr>)}</tbody></table>{!consensus.forecasts.length ? <p>暂无券商结构化预测，等待 report_rc 同步。</p> : null}</div>
    <p className="super-broker-scope">净利润由 report_rc 的万元换算为亿元；ROE 保留源值，不推定百分比缩放。不同指标只统计各自有效样本，不代表全市场一致预期。{!sample?.loaded ? '旧计数是该期单个指标样本数的最大值，不是机构数。' : ''}</p>
    <div ref={sourceRegion} tabIndex={-1} className="super-broker-evidence" aria-label="当前券商计算来源">
      {notes.length || selection ? <details className="super-reading-help" open={sourcesOpen} onToggle={event => setSourcesOpen(event.currentTarget.open)}>
        <summary>{selection ? `${selectionLabel}的计算来源 · ${selectedIds.length} 份` : `查看计算来源 · ${notes.length} 份`}</summary>
        {selection ? <div className="super-broker-filter"><p>仅显示参与本项计算的记录，不包含该期其他指标的样本。</p><button type="button" onClick={() => { focusSources.current = true; setSourcesOpen(true); setSelection(null); }}>查看全部来源</button></div> : null}
        <p className="super-broker-scope">打开的是来源记录当前保存的字段，不是完整 PDF；同步可能修订同一记录，请核对预测期与各自日期。</p>
        {selection && !selectedIds.length ? <p role="status" className="super-broker-scope">当前响应没有保存本项来源标识，不用其他研报替代。</p> : null}
        <BrokerSources key={`${selectionLabel}:${visibleNotes.map(note => note.eventId).join(',')}`} notes={visibleNotes} />
      </details> : null}
    </div>
  </section>;
}

function ConsensusSection({ stock, onEssayOpen, onAnalyze, analyzing, analysisMessage }: {
  stock: SuperWatchlistStock; onEssayOpen: (event: MonitorEvent) => void;
  onAnalyze: () => void; analyzing: boolean; analysisMessage?: string;
}) {
  const consensus = stock.consensus;
  const essay = consensus.essayAnalysis;
  const hasReport = essay.hasReport ?? Boolean(essay.summary || essay.profitOutlook || essay.valuationOutlook
    || essay.estimates.length || essay.consensusPoints.length || essay.conflicts.length || essay.caveats.length
    || essay.timeObservations?.length || essay.verificationConditions?.length);
  const hasReportProvenance = typeof essay.hasReport === 'boolean';
  const reportSourceCount = hasReportProvenance ? essay.reportSourceCount : essay.analyzedCount || null;
  const candidateNotes = essay.candidateSourceNotes ?? essay.sourceNotes;
  const displayedSourceNotes = hasReportProvenance && hasReport ? essay.sourceNotes : candidateNotes;
  const reportSourcesDisplayed = hasReportProvenance && hasReport;
  const inProgress = analyzing || essay.status === 'processing' || essay.status === 'pending';
  // sourceCount is the current selected input count, not the previous report's
  // source count. An empty new window must not trigger an empty AI job.
  const candidateCount = Number.isFinite(essay.sourceCount) ? Math.max(0, Math.floor(essay.sourceCount)) : 0;
  const canAnalyze = !inProgress && candidateCount > 0;
  const analysisLabel = inProgress ? '正在分析小作文' : candidateCount === 0 ? '暂无可分析小作文'
    : hasReport ? `更新报告 · ${candidateCount} 篇` : essay.status === 'failed' ? `重试分析 · ${candidateCount} 篇` : `分析现有 ${candidateCount} 篇`;
  const dedicatedNotes = displayedSourceNotes.filter(note => note.sourceKind === 'dedicated');
  const relatedNotes = displayedSourceNotes.filter(note => note.sourceKind !== 'dedicated');
  const timeline = [...essay.estimates]
    .sort(compareExpectations)
    .slice(0, 8);
  const toEssayEvent = (topicId?: string | null, eventId?: number | null, title?: string | null, eventAt?: string | null) => (
    stock.alternative.essays.find(event => String(event.externalId) === String(topicId))
    ?? stock.alternative.essays.find(event => event.id === eventId)
    ?? {
      id: eventId ?? 0, sourceKey: 'zsxq.essays', sourceName: '知识星球小作文（待核验）', sourceType: 'mcp',
      externalId: String(topicId || ''), eventType: 'essay', perspective: 'investor' as const,
      title: title || '小作文原文', summary: '', url: null, symbols: [stock.symbol], sentiment: 'neutral' as const,
      importanceScore: 0, confidenceScore: 0.5, tags: [], actors: [], metrics: {}, eventAt: eventAt || '',
    }
  );
  const renderSourceGroup = (title: string, subtitle: string, notes: typeof essay.sourceNotes, kind: 'dedicated' | 'related') => <section className="super-source-group">
    <header><div><h5>{title}</h5><p>{subtitle}</p></div><span>{notes.length}</span></header>
    <div className="super-source-stack">{notes.map(note => <button type="button" key={note.topicId} onClick={() => onEssayOpen(toEssayEvent(note.topicId, note.eventId, note.title, note.eventAt))}>
      <span className={`super-source-kind is-${kind}`}>{kind === 'dedicated' ? '单股专属' : '相关样本'}</span>
      <strong>{note.title || '未命名小作文'}</strong>
      <span className="super-source-meta"><time>{researchTime(note.eventAt)}</time><em>{note.authorName || '作者未标注'}</em>{reportSourcesDisplayed ? <b>{note.estimateCount} 条预期</b> : <b>待分析材料</b>}</span>
    </button>)}{!notes.length ? <p className="super-source-empty">当前样本中暂无此类小作文</p> : null}</div>
  </section>;

  return <section className="super-consensus-workbench">
    <header className="super-consensus-hero">
      <div className="super-consensus-title"><span className="super-consensus-orb"><BrainCircuit /></span><div><p>{stock.name} · {stock.symbol}</p><div><h3>一致预期</h3><span className={`super-consensus-status is-${essay.status}`}>小作文：<span>{CONSENSUS_STATUS_LABELS[essay.status] ?? essay.status}</span></span></div><small>券商预测直接展示；小作文需单独分析，再核对来源、提出时间和预测期间。</small></div></div>
      <div className="super-consensus-actions"><Link to={`/essay-radar/feed?query=${encodeURIComponent(stock.name || stock.symbol)}`}>检索原文 <ExternalLink /></Link><button type="button" onClick={() => { if (canAnalyze) onAnalyze(); }} disabled={!canAnalyze} className="super-consensus-run"><RefreshCw className={inProgress ? 'animate-spin' : ''} />{analysisLabel}</button></div>
      {analysisMessage ? <p className="super-consensus-message">{analysisMessage}</p> : null}
      {essay.status === 'failed' && essay.error && essay.error !== analysisMessage ? <p className="super-consensus-message" role="status">小作文分析失败：{essay.error}</p> : null}
      {hasReport && candidateCount === 0 && !inProgress ? <p className="super-consensus-message">当前没有可供重新分析的小作文，以下历史报告和可追溯原文仍可查看。</p> : null}
    </header>

    <section className="super-consensus-scope" aria-label="研究样本范围">
      <div><span>本次可分析 · 相关</span><strong>{essay.relatedSourceCount ?? candidateNotes.filter(note => note.sourceKind !== 'dedicated').length}</strong><small>最多 20 篇 · 多标的或关键词相关</small></div>
      <div><span>本次可分析 · 单股</span><strong>{essay.dedicatedSourceCount ?? candidateNotes.filter(note => note.sourceKind === 'dedicated').length}</strong><small>最多 5 篇 · 标签仅含 {stock.name}</small></div>
      <div><span>{consensus.brokerSample?.loaded ? '本次研报样本' : '已收录预测记录'}</span><strong>{consensus.brokerSample?.loaded ? consensus.brokerSample.reportCount : consensus.brokerReportCount}</strong><small>{consensus.brokerSample?.loaded ? '按文档去重，非机构数量' : '窗口原始记录数，非计算样本数'}</small></div>
      <div><span>报告生成</span><strong className="is-date">{researchTime(essay.completedAt)}</strong><small>{hasReport ? `${reportSourceCount != null ? `已读取 ${reportSourceCount} 篇` : '历史样本数未记录'} · ${essay.estimates.length} 条预期` : '尚未生成专项报告'}</small></div>
    </section>
    {hasReport ? <p className="super-consensus-provenance">{essay.status === 'completed' ? 'AI 归纳结果，仍需核对原文。' : '以下保留上次 AI 分析，不代表已分析本次全部候选材料。'} {hasReportProvenance && essay.analysisCutoffAt ? `报告材料截至 ${researchTime(essay.analysisCutoffAt)}。` : '历史报告完整材料截止尚未记录。'}{hasReportProvenance && essay.candidateCutoffAt ? ` 最新候选材料 ${researchTime(essay.candidateCutoffAt)}。` : ''}</p> : null}

    {hasReport && timeline.length > 0 ? <details className="super-forecast-timeline"><summary>按提出时间查看预期变化 · {timeline.length} 条</summary>
      <header><div><CalendarClock /><h4>预测时间线</h4></div><p>上行是观点提出时间，下行是预测对应期间；旧观点不会冒充最新预期。</p></header>
      <div className="super-timeline-track">{timeline.map((item, index) => <button type="button" key={`${item.topicId}-${index}`} onClick={() => onEssayOpen(toEssayEvent(item.topicId, item.eventId, item.title, item.proposedAt || item.eventAt))}>
        <time>{researchTime(expectationAt(item))}</time><span /><strong>{EXPECTATION_METRIC_LABELS[item.metric] ?? item.metric}</strong><b>{item.valueText}</b><small>预测期 {item.period || '未注明'}</small>
      </button>)}</div>
    </details> : null}

    <div className="super-consensus-body">
      <main>
        {hasReport ? <>
        <section className="super-consensus-conclusion"><div className="super-section-kicker"><Sparkles /><span>综合结论</span></div><p>{essay.summary || '报告未提供综合结论，可核对下面的预期证据与原文。'}</p><div className="super-outlook-grid"><article><span>利润与经营预期</span><p>{essay.profitOutlook || '当前没有可追溯的利润、收入或 EPS 预期。'}</p></article><article><span>估值与市值推测</span><p>{essay.valuationOutlook || '当前没有可追溯的目标价、市值或估值倍数推测。'}</p></article></div></section>

        <ExpectationMatrix essay={essay} stockName={stock.name} onOpen={item => onEssayOpen(toEssayEvent(item.topicId, item.eventId, item.title, item.proposedAt || item.eventAt))} />
        </> : <section className="super-consensus-conclusion" aria-label="小作文分析状态"><h4>{inProgress ? '小作文正在后台分析' : essay.status === 'failed' ? '本次小作文分析未完成' : '尚未生成小作文报告'}</h4><p>{inProgress ? '可以先查看券商预测与候选原文，或离开本页；后台任务会继续。' : candidateCount > 0 ? `当前有 ${candidateCount} 篇候选小作文。点击上方分析按钮后，将提取利润、估值和对应预测期间；分析前也可先核对候选原文。` : '当前没有匹配的候选小作文。可先使用券商预测、查看资料，或检索原文核对材料；不会提交空分析任务。'}</p></section>}

        <BrokerForecastPanel consensus={consensus} />
      </main>

      {displayedSourceNotes.length > 0 || hasReport ? <aside className="super-consensus-sources"><details><summary>{reportSourcesDisplayed ? '报告引用原文' : '当前候选原文'} · {displayedSourceNotes.length} 篇</summary><header><div><h4>{reportSourcesDisplayed ? '报告引用原文' : '当前候选原文'}</h4><p>{reportSourcesDisplayed ? (essay.reportSourcesComplete === false ? '历史输入清单不完整，仅展示可追溯的引用。' : '这里是生成当前报告实际使用的来源。') : hasReport ? '旧接口返回候选清单，不将它冒充历史报告的实际引用。' : '这是本次可分析的材料，尚未生成报告。'} 点击在页内阅读。</p></div></header>{renderSourceGroup('单股专属小作文', `标签中仅有 ${stock.name}`, dedicatedNotes, 'dedicated')}{renderSourceGroup('相关小作文', '多标的、产业链或关键词匹配', relatedNotes, 'related')}</details></aside> : null}
    </div>

    <section className="super-consensus-review">
      <ExpectationList title="时间变化" items={essay.timeObservations ?? []} tone="cyan" />
      <ExpectationList title="共同指向" items={essay.consensusPoints} tone="cyan" />
      <ExpectationList title="口径冲突" items={essay.conflicts} tone="rose" />
      <ExpectationList title="使用限制" items={essay.caveats} tone="amber" />
    </section>
    {(essay.verificationConditions?.length ?? 0) > 0 ? <section className="super-verification-panel"><header><ShieldCheck /><div><h4>验证、证伪与失效条件</h4><p>只展示原文能够支持的验证窗口，不自动编造期限。</p></div></header><div>{essay.verificationConditions?.map((item, index) => <article key={`${item.condition}-${index}`}><strong>{item.condition}</strong><p>{item.impact}</p><span>验证窗口 {item.window} · 失效时间 {item.expiryAt}</span></article>)}</div></section> : null}
    <footer>{consensus.method} · 单股专属材料仅代表聚焦度更高，不代表事实等级更高。</footer>
  </section>;
}

function ExpectationList({ title, items, tone }: { title: string; items: string[]; tone: 'cyan' | 'rose' | 'amber' }) {
  const toneClass = tone === 'cyan' ? 'text-cyan' : tone === 'rose' ? 'text-rose-400' : 'text-amber-400';
  if (!items.length) return null;
  return <details className="super-expectation-list-card"><summary><span className={toneClass}>{title}</span><span>{items.length} 条 <ChevronDown size={14} aria-hidden="true" /></span></summary><ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></details>;
}

type LibraryNavigation = {
  query: string; page: number;
  onQueryChange: (query: string) => void; onPageChange: (page: number) => void; onClear: () => void;
};

function LibraryPagination({ current, pages, onChange, bottom = false }: { current: number; pages: number; onChange: (page: number) => void; bottom?: boolean }) {
  if (pages <= 1) return null;
  return <nav className="super-library-pages" aria-label={bottom ? '资料底部分页' : '资料分页'}>
    <button type="button" aria-label={bottom ? '底部上一页' : undefined} disabled={current <= 1} onClick={() => onChange(current - 1)}>上一页</button>
    <span aria-live={bottom ? 'off' : 'polite'}>第 {current} / {pages} 页</span>
    <button type="button" aria-label={bottom ? '底部下一页' : undefined} disabled={current >= pages} onClick={() => onChange(current + 1)}>下一页</button>
  </nav>;
}

function EventSection({ events, stockSymbol, library, researchFilter = false, material = 'all', onMaterialChange, message, onMessageChange, emptyTitle, emptyDescription, note, fullSearchQuery, onEssayOpen, onForumOpen }: { events: MonitorEvent[]; stockSymbol: string; library: LibraryNavigation; researchFilter?: boolean; material?: MaterialFilter; onMaterialChange?: (material: MaterialFilter) => void; message?: MessageFilter; onMessageChange?: (message: MessageFilter) => void; emptyTitle: string; emptyDescription: string; note?: string; fullSearchQuery?: string; onEssayOpen: (event: MonitorEvent) => void; onForumOpen: (event: MonitorEvent) => void }) {
  const { query, page, onQueryChange, onPageChange, onClear } = library;
  const resultsRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);
  const pageFocusRequested = useRef(false);
  const ordered = useMemo(() => [...new Map(events.map(event => [`${event.sourceKey}:${event.externalId || event.id}`, event])).values()]
    .sort((a, b) => (researchInstant(b.eventAt) || 0) - (researchInstant(a.eventAt) || 0)), [events]);
  const hasSnapshots = ordered.some(isSnapshotRecord);
  const filtered = ordered.filter(event => {
    const relation = stockResearchRelation(event, stockSymbol);
    const matchesMaterial = material === 'all' ? !isSnapshotRecord(event)
      : material === 'snapshots' ? isSnapshotRecord(event) : (material === 'announcements' ? event.eventType === 'company_announcement'
      : material === 'research' ? relation !== null : relation?.kind === material);
    return (!message || message === 'all' || messageKind(event) === message)
      && (!researchFilter || matchesMaterial)
      && (!query.trim() || `${event.title} ${event.summary ?? ''} ${event.sourceName} ${relation?.subjectName ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  });
  const pages = Math.max(1, Math.ceil(filtered.length / 12));
  const current = Math.min(page, pages);
  const hasFilter = Boolean(query.trim()) || (researchFilter && material !== 'all');
  const changePage = (next: number) => { pageFocusRequested.current = true; onPageChange(next); };
  useEffect(() => {
    if (!pageFocusRequested.current) return;
    pageFocusRequested.current = false;
    resultsRef.current?.focus({ preventScroll: true });
    // The toolbar wraps on phones. Scroll its actual top into view instead of
    // assuming a fixed height that can cut off counts or the page controls.
    summaryRef.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
  }, [current]);
  return <section className="super-library">
    {message ? <div className="super-message-types" role="group" aria-label="消息类型，数量为已载入条数">
      {MESSAGE_FILTERS.map(option => {
        const count = option.value === 'all' ? ordered.length : ordered.filter(event => messageKind(event) === option.value).length;
        return <button key={option.value} type="button" aria-pressed={message === option.value} aria-label={`${option.label}，${count}条已载入`} onClick={() => onMessageChange?.(option.value)}><span>{option.label}</span><b aria-hidden="true">{count}</b></button>;
      })}
    </div> : null}
    <div className="super-library-tools">
      <input type="search" aria-label="筛选已载入资料" placeholder="筛选已载入资料的标题、摘录或来源" value={query} onChange={event => onQueryChange(event.target.value)} />
      {researchFilter ? <select aria-label="资料筛选" value={material} onChange={event => onMaterialChange?.(event.target.value as MaterialFilter)}>
        {MATERIAL_FILTERS.filter(option => option.value !== 'snapshots' || hasSnapshots || material === 'snapshots').map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select> : null}
      {hasFilter ? <button type="button" className="super-library-clear" onClick={onClear}>{message ? '清除关键词' : '清除资料筛选'}</button> : null}
    </div>
    <div ref={summaryRef} className="super-library-summary">
      <span aria-live="polite">{filtered.length} 条已载入资料</span>
      {note ? <details className="super-library-scope"><summary>读取范围</summary><p className="super-library-note">{note}</p></details> : null}
      {fullSearchQuery ? <Link className="super-library-full-search" to={`/essay-radar/feed?query=${encodeURIComponent(fullSearchQuery)}`}>用“{fullSearchQuery}”检索全库 <ArrowUpRight size={13} /></Link> : null}
      <LibraryPagination current={current} pages={pages} onChange={changePage} />
    </div>
    <div ref={resultsRef} className="super-library-results" tabIndex={-1} aria-label="资料列表">{filtered.slice((current - 1) * 12, current * 12).map(event => <EvidenceRow key={`${event.sourceKey}:${event.id}`} event={event} stockSymbol={stockSymbol} showMessageKind={message === 'all'} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />)}{!filtered.length ? <EmptyState title={hasFilter ? '没有匹配的已载入资料' : emptyTitle} description={hasFilter ? '可修改关键词或资料筛选；此筛选不代表全库检索结果。' : emptyDescription} /> : null}</div>
    {pages > 1 ? <footer className="super-library-footer"><span>本页 {(current - 1) * 12 + 1}–{Math.min(current * 12, filtered.length)} / {filtered.length} 条</span><LibraryPagination current={current} pages={pages} onChange={changePage} bottom /></footer> : null}
  </section>;
}

function FinancialSection({ stock }: { stock: SuperWatchlistStock }) {
  const [readingSource, setReadingSource] = useState<{ eventId: number; financialSection?: FinancialRecordSection } | null>(null);
  const financial = stock.fundamentals, valuation = stock.valuation;
  const sourceButton = (id: unknown, label: string, financialSection?: FinancialRecordSection) => typeof id === 'number' && Number.isSafeInteger(id) && id > 0
    ? <button type="button" aria-label={label} onClick={() => setReadingSource({ eventId: id, financialSection })}><FileText size={14} />核对来源</button> : null;
  const multiple = (value: unknown) => capitalValue(value) === null ? '—' : `${number(value)} 倍`;
  const income = financialSource(financial, 'income'), cashflow = financialSource(financial, 'cashflow'), indicator = financialSource(financial, 'indicator');
  const selectedValue = (source: FundamentalSource | undefined, value: unknown) => source?.selectionStatus === 'conflict' ? null : value;
  const heading = (title: string, periodLabel: string, source: FundamentalSource | undefined, financialSection: FinancialRecordSection) => {
    const conflict = source?.selectionStatus === 'conflict';
    const candidates = conflict ? sourceIds(source.candidateEventIds).slice(0, 5) : [];
    const candidateCount = typeof source?.candidateCount === 'number' && Number.isSafeInteger(source.candidateCount) ? source.candidateCount : 0;
    return <>
      <header className="super-section-heading"><div><h3>{title}</h3><p>{periodLabel} {reportDate(source?.period)} · {source?.source || '财务来源未标注'}{source?.announcedAt ? ` · 公告 ${reportDate(source.announcedAt)}` : ''}</p></div>{conflict ? null : sourceButton(source?.eventId, `核对${title}来源`, financialSection)}</header>
      {conflict ? <div className="super-finance-conflict"><p>同期间存在不同版本，待核对{candidateCount > candidates.length ? `（共 ${candidateCount} 个版本，展示 ${candidates.length} 个）` : ''}</p>{candidates.length ? <div className="super-finance-versions" role="group" aria-label={`${title}来源版本`}>{candidates.map((id, index) => <button key={id} type="button" aria-label={`${title}：核对版本 ${index + 1}`} onClick={() => setReadingSource({ eventId: id, financialSection })}><FileText size={14} />版本 {index + 1}</button>)}</div> : null}</div> : null}
    </>;
  };
  const amountsKnown = capitalValue(selectedValue(income, financial.netProfit)) !== null && capitalValue(selectedValue(cashflow, financial.operatingCashflow)) !== null;
  const periodNote = !amountsKnown ? null : !income?.period || !cashflow?.period
    ? '部分报表未标注期间，暂不跨表比较利润与现金流。'
    : reportDate(income.period) !== reportDate(cashflow.period)
      ? '利润表与现金流量表期间不同，不能直接用这两个金额比较现金回收情况。' : null;
  return <section className="super-finance-panel" aria-label="财务与估值详情">
    <section className="super-finance-block" aria-label="收入与利润">
      {heading('收入与利润', '利润表期', income, 'income')}
      <div className="super-finance-metrics super-finance-income">
        <Metric label="营业收入" value={capitalMoney(selectedValue(income, financial.revenue))} />
        <Metric label="归母净利润" value={capitalMoney(selectedValue(income, financial.netProfit))} />
      </div>
    </section>
    <section className="super-finance-block" aria-label="经营现金流">
      {heading('经营现金流', '现金流量表期', cashflow, 'cashflow')}
      <div className="super-finance-metrics super-finance-cashflow"><Metric label="经营现金流净额" value={capitalMoney(selectedValue(cashflow, financial.operatingCashflow))} /></div>
    </section>
    {periodNote ? <p className="super-finance-period-note">{periodNote}</p> : null}
    <section className="super-finance-block" aria-label="财务指标">
    {heading('财务指标', '财务指标期', indicator, 'indicator')}
    <div className="super-finance-metrics super-finance-ratios">
      <Metric label="净利率" value={ratioPercent(selectedValue(indicator, financial.netMargin))} />
      <Metric label="毛利率" value={ratioPercent(selectedValue(indicator, financial.grossMargin))} />
      <Metric label="ROE" value={ratioPercent(selectedValue(indicator, financial.roe))} note="净资产收益率" />
      <Metric label="资产负债率" value={ratioPercent(selectedValue(indicator, financial.debtRatio))} />
      <Metric label="流动比率" value={multiple(selectedValue(indicator, financial.currentRatio))} />
    </div>
    </section>
    <header className="super-section-heading"><div><h3>市场给了多少估值</h3><p>估值日期 {reportDate(valuation.asOf)} · {valuation.source || '估值来源未标注'}，不使用行情日期替代。</p></div>{sourceButton(valuation.sourceEventId, '核对估值来源')}</header>
    <div className="super-finance-metrics"><Metric label="总市值" value={capitalMoney(valuation.totalMv, true)} /><Metric label="PE(TTM)" value={multiple(valuation.peTtm)} note="滚动市盈率" /><Metric label="PB" value={multiple(valuation.pb)} note="市净率" /><Metric label="PS(TTM)" value={multiple(valuation.psTtm)} note="滚动市销率" /></div>
    <details className="super-reading-help"><summary>如何使用这些数据</summary><p>把收入、利润和经营现金流结合起来核对盈利质量；比较公司自己的历史和同行，单个倍数不能判断便宜或昂贵。财报与市场估值的日期分别列示，缺失值不按零处理。</p><p>PE(TTM) 是股价相对滚动盈利的倍数，PB 是市净率，PS(TTM) 是滚动市销率。ROE 衡量净资产盈利能力；比率本身不是同比变化，这些都是历史口径，不是盈利预测。</p><p>“核对来源”打开该区块对应的已保存字段，并非财报 PDF。各表的报告期与公告日期分别核对；完整公告在资料库查看。旧数据没有来源记录标识时不提供推测链接。</p></details>
    {typeof stock.company.profile.mainBusiness === 'string' && stock.company.profile.mainBusiness ? <details className="super-reading-help"><summary>公司主营业务</summary><p>{stock.company.profile.mainBusiness}</p></details> : null}
    {readingSource ? <SourceRecordDrawer eventId={readingSource.eventId} financialSection={readingSource.financialSection} onClose={() => setReadingSource(null)} /> : null}
  </section>;
}

function DetailSection({ section, stock, library, material, onMaterialChange, message, onMessageChange, onEssayOpen, onForumOpen, onAnalyzeConsensus, analyzingConsensus, consensusMessage }: { section: Section; stock: SuperWatchlistStock; library: LibraryNavigation; material: MaterialFilter; onMaterialChange: (material: MaterialFilter) => void; message: MessageFilter; onMessageChange: (message: MessageFilter) => void; onEssayOpen: (event: MonitorEvent) => void; onForumOpen: (event: MonitorEvent) => void; onAnalyzeConsensus: () => void; analyzingConsensus: boolean; consensusMessage?: string }) {
  if (section === 'overview') return null;
  if (section === 'fundamental') return <FinancialSection key={financialReaderKey(stock)} stock={stock} />;
  if (section === 'capital') return <section className="super-finance-panel">
    <CapitalTrading capital={stock.capital} />
    <header className="super-section-heading"><div><h3>筹码分布</h3><p>筹码日期 {reportDate(stock.capital.chipTradeDate)} · {stock.capital.source || '筹码来源未标注'}</p><p>数据源估算，不是账户持仓；未标注日期时不作为当日筹码使用。</p></div></header>
    <div className="super-finance-metrics"><Metric label="估算获利比例" value={capitalRate(stock.capital.winnerRate)} /><Metric label="加权成本" value={number(stock.capital.weightedCost)} /><Metric label="50%成本分位" value={number(stock.capital.cost50Pct)} /><Metric label="85%成本分位" value={number(stock.capital.cost85Pct)} /></div>
    <details className="super-reading-help"><summary>价格状态与技术指标</summary>
    <header className="super-section-heading"><div><h3>价格状态</h3><p>技术指标日期 {reportDate(typeof stock.technical.tradeDate === 'string' ? stock.technical.tradeDate : null)}，与筹码日期分别核对。</p></div></header>
    <div className="super-finance-metrics"><Metric label="RSI(6)" value={number(stock.technical.rsi6)} /><Metric label="MACD" value={number(stock.technical.macd, 3)} /><Metric label="布林中轨" value={number(stock.technical.bollMid)} /><Metric label="CCI" value={number(stock.technical.cci)} /></div>
    </details>
    {capitalRate(stock.capital.northbound?.ratio) !== '—' ? <details className="super-reading-help"><summary>最近披露北向持股</summary><p>披露日期 {reportDate(stock.capital.northboundTradeDate)} · 持股比例 {capitalRate(stock.capital.northbound.ratio)}，不是当日净买入，也不代表此刻持仓。</p></details> : null}
    <details className="super-reading-help"><summary>这些指标能说明什么</summary><p>资金流使用已保存的 Tushare moneyflow（源金额为万元），融资使用 margin_detail（源金额为元）；界面已换算并标注单位。单日净额不代表资金持续流入，不能据此认定机构买入。</p><p>成本分位和获利比例描述模型估计的筹码分布，不是未来获胜概率；RSI、MACD、布林线和 CCI 描述过去价格状态。应与当期成交、公司公告和预期变化交叉核对，不能单独作为买卖指令。</p></details>
  </section>;
  if (section === 'consensus') return <ConsensusSection stock={stock} onEssayOpen={onEssayOpen} onAnalyze={onAnalyzeConsensus} analyzing={analyzingConsensus} analysisMessage={consensusMessage} />;
  if (section === 'essay') return <EventSection library={library} stockSymbol={stock.symbol} events={stock.alternative.essays} emptyTitle="暂无匹配小作文" emptyDescription="按股票全称、简称和代码匹配；新增自选股会自动重建关键词索引。" note={`共匹配 ${stock.alternative.essayCount} 篇，当前列表为近期已载入记录；点击查看原文。`} fullSearchQuery={stock.name || stock.symbol} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  if (section === 'messages') return <EventSection library={library} stockSymbol={stock.symbol} events={stock.messages.items} message={message} onMessageChange={onMessageChange} emptyTitle={MESSAGE_FILTERS.find(item => item.value === message)!.empty} emptyDescription="当前载入范围没有这类消息，可切换其他类型查看；这不代表全库没有相关资料。" note="当前时间范围内，新闻最多载入20条、机构观点10条、企业提示10条，分别取近期记录，不互相挤占。类型数量是已载入条数，关键词只筛选当前类型，不是全库检索。新闻不等于公司披露，机构观点需核验，企业提示也不都代表风险。" onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  if (section === 'comments') return <EventSection library={library} stockSymbol={stock.symbol} events={stock.stockComments.items} emptyTitle="暂无公开股评" emptyDescription="等待东方财富股吧真实公开帖子增量同步。" note={stock.stockComments.sourceNote} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  const events = section === 'institution' ? [...stock.company.announcements, ...stock.institution.latest] : [...stock.timeline, ...stock.company.announcements, ...stock.institution.latest, ...stock.alternative.essays, ...stock.messages.items].filter(event => event.eventType !== 'stock_forum_post');
  return <EventSection library={library} stockSymbol={stock.symbol} researchFilter material={material} onMaterialChange={onMaterialChange} events={events} note={section === 'institution' ? '已载入的公告与研报，按原始时间排序；可筛选公告、券商研究及研报关联范围。关联不代表内容已核验，也不是全库完整清单。' : '当前已载入的研究资料，按原始时间排序；默认不混入重复行情、财务和指标快照，可在资料筛选中选择“数据快照”核对。股友讨论单列在“股评监控”。这不是全库清单。'} emptyTitle="暂无已载入研究资料" emptyDescription="可调整资料筛选，或前往机构段子与录音、数据一站式获取检索；这里不代表全库没有相关材料。" onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
}

export default function SuperWatchlistPage() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [stockCache, setStockCache] = useState<Record<string, SuperWatchlistStock>>({});
  const [listReady, setListReady] = useState(false);
  const [listError, setListError] = useState('');
  const [workspaceErrors, setWorkspaceErrors] = useState<Record<string, string>>({});
  const [loadingSymbol, setLoadingSymbol] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [refreshingShared, setRefreshingShared] = useState(false);
  const [newSymbol, setNewSymbol] = useState('');
  const [expandedChartSection, setExpandedChartSection] = useState<Section | null>(null);
  const [selectedEssay, setSelectedEssay] = useState<MonitorEvent | null>(null);
  const [selectedForumPost, setSelectedForumPost] = useState<MonitorEvent | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{ symbol: string; name: string } | null>(null);
  const [deletingSymbol, setDeletingSymbol] = useState<string | null>(null);
  const [consensusOverrides, setConsensusOverrides] = useState<Record<string, EssayConsensusAnalysis>>({});
  const [consensusTasks, setConsensusTasks] = useState<Record<string, { busy: boolean; message: string; submitting?: boolean }>>({});
  const consensusRuns = useRef<Record<string, number>>({});
  const { index: stockIndex } = useStockIndex();
  const navigate = useNavigate();
  const {
    activeSymbol, section, primarySection, materialFilter, messageFilter, query, page,
    selectSymbol, selectPrimary, selectSection, openSection, selectMaterial, selectMessage,
    setQuery, setPage, clearFilters,
  } = useWatchlistNavigation(symbols);
  const workspaceView: WorkspaceView = section === 'overview' ? 'overview' : 'full';
  const library: LibraryNavigation = {
    query, page, onQueryChange: setQuery, onPageChange: setPage, onClear: clearFilters,
  };
  const workspaceInFlight = useRef(new Set<string>());
  const workspaceRefreshQueued = useRef(new Set<string>());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const sectionNavRef = useRef<HTMLElement>(null);
  const materialNavRef = useRef<HTMLElement>(null);
  const listRevision = useRef(0);
  const listInFlight = useRef<number | null>(null);
  const listMutationInFlight = useRef(false);
  const stockCacheRef = useRef(stockCache);
  useEffect(() => { stockCacheRef.current = stockCache; }, [stockCache]);
  useEffect(() => { document.title = '自选股超级看板 - 乐子乌智能投研平台'; }, []);
  const loadWatchlist = useCallback(async () => {
    const revision = listRevision.current;
    if (listInFlight.current === revision || listMutationInFlight.current) return;
    listInFlight.current = revision;
    try {
      const values = await systemConfigApi.getWatchlist();
      if (alive.current && revision === listRevision.current) { setSymbols(values); setListError(''); }
    } catch (err) {
      if (alive.current && revision === listRevision.current) setListError(err instanceof Error ? err.message : '自选股列表加载失败');
    } finally {
      if (listInFlight.current === revision) listInFlight.current = null;
      if (alive.current && revision === listRevision.current) setListReady(true);
    }
  }, []);
  const loadWorkspace = useCallback(async (symbol: string, force = false, view: WorkspaceView = 'overview'): Promise<void> => {
    if (!symbol) return;
    const key = workspaceKey(symbol, view);
    if (workspaceInFlight.current.has(key)) {
      if (force) workspaceRefreshQueued.current.add(key);
      return;
    }
    workspaceInFlight.current.add(key);
    setWorkspaceErrors(previous => ({ ...previous, [key]: '' }));
    if (!stockCacheRef.current[key]) setLoadingSymbol(symbol);
    try {
      const workspace = await investmentMonitorApi.stockWorkspace(symbol, 183, force, view);
      // Legacy watchlists can contain a bare six-digit code. Never attach a
      // different company's response to this slot, even if an endpoint errs.
      const sameSymbol = workspace.stock.symbol === symbol
        || (/^\d{6}$/.test(symbol) && workspace.stock.symbol.split('.')[0] === symbol);
      if (!sameSymbol) throw new Error('返回的股票与当前选择不一致，请重试。');
      if (alive.current) setStockCache(previous => ({ ...previous, [key]: workspace.stock }));
    } catch (err) {
      if (alive.current) setWorkspaceErrors(previous => ({ ...previous, [key]: err instanceof Error ? err.message : '个股工作台加载失败' }));
    } finally {
      workspaceInFlight.current.delete(key);
      if (alive.current) {
        setLoadingSymbol(current => current === symbol ? null : current);
        if (workspaceRefreshQueued.current.delete(key)) void loadWorkspace(symbol, true, view);
      }
    }
  }, []);
  useEffect(() => { if (activeSymbol) void loadWorkspace(activeSymbol, false, workspaceView); }, [activeSymbol, loadWorkspace, workspaceView]);
  const refreshVisibleData = useCallback(async () => {
    await Promise.all([loadWatchlist(), activeSymbol ? loadWorkspace(activeSymbol, false, workspaceView) : Promise.resolve()]);
  }, [activeSymbol, loadWatchlist, loadWorkspace, workspaceView]);
  usePageActivationRefresh(refreshVisibleData, {
    intervalMs: 60_000, minIntervalMs: 5_000,
  });
  const { quotes: liveQuotes, keyFor: quoteKey } = useRealtimeQuotes(symbols);
  const indexedNames = useMemo(() => new Map(stockIndex.map(item => [item.canonicalCode.toUpperCase(), item.nameZh?.trim() || item.nameEn?.trim()])), [stockIndex]);
  const railStocks = useMemo(() => symbols.map(symbol => {
    const overview = stockCache[workspaceKey(symbol, 'overview')];
    const full = stockCache[workspaceKey(symbol, 'full')];
    const cached = newerWorkspace(overview, full);
    const quote = quoteForSymbol(symbol, liveQuotes.get(quoteKey(symbol)));
    const resolved = cached ? applyRealtimeQuote(cached, quote) : undefined;
    // A cold workspace or an upstream code-as-name is not a reason to show
    // an unnamed watchlist. Reuse the already loaded, venue-qualified index.
    const name = [cached?.name, quote?.stockName, indexedNames.get(symbol.toUpperCase())]
      .find(value => value?.trim() && ![symbol.toUpperCase(), symbol.split('.')[0].toUpperCase()].includes(value.trim().toUpperCase()));
    return {
      symbol,
      name: name?.trim() || symbol,
      display: marketPresentation(resolved?.market ?? {
        price: quote?.currentPrice, changePct: quote?.changePercent, updatedAt: quote?.updateTime,
        isStale: Boolean(quote?.isStale), quoteKind: 'snapshot',
      }),
    };
  }), [indexedNames, liveQuotes, quoteKey, stockCache, symbols]);
  const loadedDetail = stockCache[workspaceKey(activeSymbol, workspaceView)];
  const baseActive = loadedDetail ?? stockCache[workspaceKey(activeSymbol, 'overview')]
    ?? stockCache[workspaceKey(activeSymbol, 'full')];
  useEffect(() => {
    const revealSelected = () => {
      for (const nav of [sectionNavRef.current, materialNavRef.current]) {
        const selected = nav?.querySelector<HTMLElement>('[aria-pressed="true"]');
        if (!nav || !selected) continue;
        const bounds = nav.getBoundingClientRect();
        const item = selected.getBoundingClientRect();
        // Reveal the chosen tab without scrolling the whole page on mobile.
        if (item.right > bounds.right) nav.scrollLeft += item.right - bounds.right;
        else if (item.left < bounds.left) nav.scrollLeft -= bounds.left - item.left;
      }
    };
    revealSelected();
    window.addEventListener('resize', revealSelected);
    return () => window.removeEventListener('resize', revealSelected);
  }, [section, activeSymbol, baseActive]);
  const freshestWorkspace = newerWorkspace(stockCache[workspaceKey(activeSymbol, 'overview')], stockCache[workspaceKey(activeSymbol, 'full')]);
  const active = useMemo(() => baseActive ? applyRealtimeQuote(
    { ...baseActive, market: freshestWorkspace?.market ?? baseActive.market }, liveQuotes.get(quoteKey(activeSymbol)),
  ) : null, [activeSymbol, baseActive, freshestWorkspace, liveQuotes, quoteKey]);
  const workspaceError = activeSymbol ? workspaceErrors[workspaceKey(activeSymbol, workspaceView)] : '';
  const loading = !listReady || Boolean(activeSymbol && !active && !workspaceError);
  const savedConsensusIsNewer = Boolean(active && savedConsensusSupersedes(active.consensus.essayAnalysis, consensusOverrides[active.symbol]));
  const activeForDetail = useMemo(() => {
    if (!active || !consensusOverrides[active.symbol] || savedConsensusIsNewer) return active;
    return { ...active, consensus: { ...active.consensus, essayAnalysis: consensusOverrides[active.symbol], essayExpectationCount: consensusOverrides[active.symbol].estimates.length, essayExpectations: consensusOverrides[active.symbol].estimates.map(item => ({ ...item, text: item.valueText })) } };
  }, [active, consensusOverrides, savedConsensusIsNewer]);
  const savedTaskFinished = savedConsensusIsNewer && !consensusIsRunning(active?.consensus.essayAnalysis);
  const analyzingConsensus = Boolean(active && consensusTasks[active.symbol]?.busy
    && (consensusTasks[active.symbol]?.submitting || !savedTaskFinished));
  const consensusMessage = active && !(savedTaskFinished && !consensusTasks[active.symbol]?.submitting) ? consensusTasks[active.symbol]?.message ?? '' : '';
  const submitStock = async (rawSymbol: string) => {
    const symbol = rawSymbol.trim();
    if (!symbol || listMutationInFlight.current) return;
    listMutationInFlight.current = true;
    listRevision.current += 1;
    setBusy(true); setError('');
    let failed = false;
    try {
      const nextSymbols = await systemConfigApi.addToWatchlist(symbol);
      if (!alive.current) return;
      setSymbols(nextSymbols); setNewSymbol(''); setListReady(true); setListError('');
      const fromIndex = stockIndex.filter(item => item.canonicalCode.toUpperCase() === symbol.toUpperCase()
        || item.displayCode === symbol || item.nameZh === symbol || item.nameEn?.toUpperCase() === symbol.toUpperCase());
      const requested = fromIndex.length === 1 ? fromIndex[0].canonicalCode : symbol;
      const additions = nextSymbols.filter(value => !symbols.includes(value));
      const requestedCode = requested.toUpperCase();
      const bareRequest = /^\d{6}$/.test(requestedCode);
      const qualifiedRequest = /^\d{6}\.(SH|SZ|BJ)$/.test(requestedCode);
      // Legacy lists can contain bare codes in either direction. Only accept a
      // unique match, and never equate two explicitly different exchanges.
      const legacyMatches = nextSymbols.filter(value => {
        const storedCode = value.toUpperCase();
        return ((bareRequest && /^\d{6}\.(SH|SZ|BJ)$/.test(storedCode))
          || (qualifiedRequest && /^\d{6}$/.test(storedCode)))
          && storedCode.split('.')[0] === requestedCode.split('.')[0];
      });
      const added = nextSymbols.find(value => value.toUpperCase() === requestedCode)
        ?? (legacyMatches.length === 1 ? legacyMatches[0] : undefined)
        ?? (!bareRequest && !qualifiedRequest && additions.length === 1 ? additions[0] : undefined);
      if (added) { selectSymbol(added, true); void loadWorkspace(added, true, workspaceView); }
    }
    catch (err) { failed = true; if (alive.current) setError(err instanceof Error ? err.message : '添加失败'); }
    finally {
      listMutationInFlight.current = false;
      if (alive.current) { setBusy(false); if (failed) void loadWatchlist(); }
    }
  };
  const addStock = (event: FormEvent) => { event.preventDefault(); void submitStock(newSymbol); };
  const removeStock = async () => {
    if (!pendingDelete || listMutationInFlight.current) return;
    listMutationInFlight.current = true;
    listRevision.current += 1;
    const removed = pendingDelete;
    setDeletingSymbol(removed.symbol);
    setError('');
    let failed = false;
    try {
      const remaining = await systemConfigApi.removeFromWatchlist(removed.symbol);
      if (!alive.current) return;
      setSymbols(remaining); setListReady(true); setListError('');
      setStockCache(previous => { const next = { ...previous }; delete next[workspaceKey(removed.symbol, 'overview')]; delete next[workspaceKey(removed.symbol, 'full')]; return next; });
      setPendingDelete(null);
      if (active?.symbol === removed.symbol) {
        const next = remaining[0];
        selectSymbol(next, true);
      }
    } catch (err) {
      failed = true;
      if (alive.current) setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      listMutationInFlight.current = false;
      if (alive.current) { setDeletingSymbol(null); if (failed) void loadWatchlist(); }
    }
  };
  const refreshShared = async () => {
    if (refreshingShared) return;
    setRefreshingShared(true); setError('');
    try {
      await investmentMonitorApi.refreshSuperWatchlist();
      if (activeSymbol) await loadWorkspace(activeSymbol, true, workspaceView);
    }
    catch (err) { setError(err instanceof Error ? err.message : '共享数据刷新失败'); }
    finally { setRefreshingShared(false); }
  };
  const analyzeConsensus = async () => {
    if (!active || analyzingConsensus) return;
    const symbol = active.symbol;
    const run = (consensusRuns.current[symbol] ?? 0) + 1;
    consensusRuns.current[symbol] = run;
    const isCurrentRun = () => alive.current && consensusRuns.current[symbol] === run;
    const taskMessage = (message: string, busy = true, submitting = false) => {
      if (isCurrentRun()) setConsensusTasks(previous => ({ ...previous, [symbol]: { busy, message, submitting } }));
    };
    const savedCompletion = (observed: EssayConsensusAnalysis) => {
      const saved = stockCacheRef.current[workspaceKey(symbol, 'full')]?.consensus.essayAnalysis;
      return saved && savedConsensusSupersedes(saved, observed) && !consensusIsRunning(saved) ? saved : null;
    };
    taskMessage('正在提交当前股票的 20+5 篇原文预期分析，可切换股票继续浏览。', true, true);
    try {
      const queued = await investmentMonitorApi.analyzeEssayConsensus(symbol);
      if (!isCurrentRun()) return;
      setConsensusOverrides(previous => ({ ...previous, [symbol]: queued.consensus }));
      taskMessage('后台正在分析，切换股票不会中断任务。');
      let observed = queued.consensus;
      for (let attempt = 0; attempt < 36; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 5000));
        if (!isCurrentRun()) return;
        if (savedCompletion(observed)) return;
        const current = await investmentMonitorApi.essayConsensus(symbol);
        if (!isCurrentRun()) return;
        if (savedConsensusSupersedes(observed, current.consensus) && !savedConsensusSupersedes(current.consensus, observed)) continue;
        if (savedCompletion(current.consensus)) return;
        observed = current.consensus;
        setConsensusOverrides(previous => ({ ...previous, [symbol]: current.consensus }));
        if (current.consensus.status === 'completed') {
          taskMessage(`分析完成：读取 ${current.consensus.relatedSourceCount ?? 0} 篇相关、${current.consensus.dedicatedSourceCount ?? 0} 篇单股专属小作文，提取 ${current.consensus.estimates.length} 条可追溯预期。`, false);
          return;
        }
        if (current.consensus.status === 'failed') throw new Error(current.consensus.error || '小作文一致预期分析失败');
      }
      taskMessage('后台仍在分析，可离开页面；返回一致预期即可查看保存的结果。', false);
    } catch (err) {
      taskMessage(err instanceof Error ? err.message : '小作文一致预期分析失败', false);
    } finally {
      if (isCurrentRun()) setConsensusTasks(previous => ({ ...previous, [symbol]: { ...previous[symbol], busy: false, submitting: false } }));
    }
  };
  const activeMarket = marketPresentation(active?.market ?? {});
  const openModel = () => {
    if (!active) return;
    const query = new URLSearchParams({
      stock: active.symbol, name: active.name,
      prompt: `请研究${active.name}（${active.symbol}）。先调取当前股票的行情、最新财报、公告和机构观点，说明每项数据日期与来源；比较已披露事实和市场预期，列出需要继续验证的问题。不要把小作文推测当事实，不要仅凭涨跌幅给买卖结论。当前页面报价时间：${active.market.updatedAt || '未取得'}。`,
    });
    navigate(`/chat?${query}`);
  };
  return <AppPage className="super-watchlist-page max-w-[1900px]"><div className="super-watchlist-shell border border-[#C9CCD2] bg-white text-[#17181A]">
    <header className="super-watchlist-header"><div><h1>自选股超级看板</h1><p>关注的公司，放在一起研究。</p></div><div className="super-header-actions"><Link to="/tasks">我的研究任务 <ArrowUpRight size={14} /></Link><button onClick={() => void refreshShared()} disabled={refreshingShared} aria-label="刷新共享行情并唤醒到期数据源" className="super-refresh-button"><RefreshCw size={14} className={refreshingShared ? 'animate-spin' : ''} />{refreshingShared ? '正在更新' : '刷新'}</button></div></header>
    {error ? <div className="border-b border-[#FDA29B] bg-[#FEF3F2] px-4 py-2 text-[11px] text-[#B42318]">{error}</div> : null}
    {listError ? <div role="status" className="super-load-message"><span>自选列表暂未更新，已有内容会保留。</span><button type="button" onClick={() => void loadWatchlist()}>重试列表</button></div> : null}
    <div className={`super-watchlist-grid grid grid-cols-1 ${section === 'consensus' ? 'is-consensus' : ''}`}>
      <aside className="super-watchlist-sidebar border-b border-r border-[#D8DADF] bg-[#FAFBFC] xl:border-b-0">
        <form onSubmit={addStock} className="super-add-stock"><h2>我的自选 <span>{symbols.length}</span></h2><div className="flex"><div className="min-w-0 flex-1"><StockAutocomplete value={newSymbol} onChange={setNewSymbol} onSubmit={(symbol) => void submitStock(symbol)} disabled={busy || Boolean(deletingSymbol)} placeholder="输入股票名称或代码" className="h-10 px-2 text-[12px]" /></div><button type="submit" disabled={busy || Boolean(deletingSymbol) || !newSymbol.trim()} aria-label="加入自选股"><Plus size={17} /></button></div><p>添加后自动收集行情与研究资料</p></form>
        <div className="super-watchlist-stock-rail">{railStocks.map(stock => {
          const selected = stock.symbol === activeSymbol;
          return <div key={stock.symbol} className={`super-watchlist-item flex border-b border-[#D8DADF] ${selected ? 'is-selected bg-[#EEF4FF]' : ''}`}>
            <button type="button" aria-pressed={selected} onClick={() => selectSymbol(stock.symbol)} className="super-watchlist-select min-w-0 flex-1 p-3 text-left hover:bg-white">
              <div className="flex items-center justify-between gap-2"><span className="truncate text-[13px] font-bold">{stock.name}</span><span className={`shrink-0 font-mono text-[12px] font-bold ${stock.display.tone > 0 ? 'text-[#B42318]' : stock.display.tone < 0 ? 'text-[#027A48]' : ''}`}>{stock.display.price}</span></div>
              <div className="mt-1 flex justify-between font-mono text-[9px] text-[#62666D]"><span>{stock.symbol}</span><span>{stock.display.change}</span></div>
            </button>
            <button type="button" onClick={() => setPendingDelete(stock)} disabled={busy || Boolean(deletingSymbol)} aria-label={`删除自选股 ${stock.name}`} className="super-watchlist-delete m-2 ml-0 inline-flex w-8 shrink-0 items-center justify-center self-stretch rounded-lg text-[#7B7F87] hover:bg-[#FEF3F2] hover:text-[#B42318] disabled:cursor-wait disabled:opacity-40"><Trash2 className="h-3.5 w-3.5" /></button>
          </div>;
        })}</div>
      </aside>
      <main className="super-watchlist-main min-w-0">{active ? <>
        <section className="super-market-band"><div className="super-stock-identity"><h2>{active.name}</h2><span>{active.symbol}</span><p title={active.market.updatedAt ? `北京时间；数据源时间 ${active.market.updatedAt}` : undefined}>{activeMarket.label}</p></div><div className={`super-stock-price ${activeMarket.tone > 0 ? 'is-up' : activeMarket.tone < 0 ? 'is-down' : ''}`}><strong>{activeMarket.price}</strong><span>{activeMarket.change}</span></div><button onClick={openModel} className="super-ask-button"><Sparkles size={16} />问 AI 这只股票</button></section>
        <nav ref={sectionNavRef} aria-label="个股信息栏目" className="super-detail-nav">{PRIMARY_SECTIONS.map(item => <button key={item.key} type="button" aria-pressed={primarySection.key === item.key} onClick={() => selectPrimary(item.key)}>{item.label}</button>)}</nav>
        {primarySection.members.length > 1 ? <div className="super-section-tools"><nav ref={materialNavRef} className="super-subnav" aria-label={primarySection.key === 'evidence' ? '资料类型' : '财务资金类型'}>{primarySection.members.map(key => SECTIONS.find(item => item.key === key)!).map(item => <button key={item.key} type="button" aria-pressed={section === item.key} onClick={() => selectSection(item.key)}>{item.label}</button>)}</nav><button className="super-chart-toggle" type="button" aria-expanded={expandedChartSection === section} onClick={() => setExpandedChartSection(previous => previous === section ? null : section)}>{expandedChartSection === section ? '收起行情图' : '展开行情图'}</button></div> : null}
        {workspaceError ? <div role="status" className="super-load-message"><span>数据更新暂未成功，正在展示上次读取的内容。</span><button type="button" onClick={() => void loadWorkspace(activeSymbol, true, workspaceView)}>重试当前股票</button></div> : null}
        {section === 'overview' ? <OverviewFinancial stock={active} onOpen={() => openSection('fundamental')} /> : null}
        {section === 'overview' || expandedChartSection === section ? <section className="super-chart-panel">
          {section === 'overview' ? <header className="super-chart-heading"><h3>行情走势</h3><button type="button" className="super-chart-toggle" aria-pressed={expandedChartSection === 'overview'} onClick={() => setExpandedChartSection(previous => previous === 'overview' ? null : 'overview')}>{expandedChartSection === 'overview' ? '还原紧凑图' : '放大行情图'}</button></header> : null}
          <MarketTimeframeChart key={active.symbol} symbol={active.symbol} compact={section === 'overview' && expandedChartSection !== 'overview'} initialPeriod="intraday" initialRange="1d" />
        </section> : null}
        {section === 'overview' ? <Overview stock={active} onSection={openSection} onEssayOpen={setSelectedEssay} onForumOpen={setSelectedForumPost} /> : loadedDetail ? <DetailSection key={`${active.symbol}:${section}`} section={section} library={library} stock={activeForDetail ?? active} material={materialFilter} onMaterialChange={selectMaterial} message={messageFilter} onMessageChange={selectMessage} onEssayOpen={setSelectedEssay} onForumOpen={setSelectedForumPost} onAnalyzeConsensus={() => void analyzeConsensus()} analyzingConsensus={analyzingConsensus} consensusMessage={consensusMessage} /> : !workspaceError ? <div role="status" className="super-detail-loading">正在读取{primarySection.label}，行情和其他栏目仍可使用。</div> : null}
        <details className="super-data-notes"><summary>数据来源与使用说明</summary><p>行情来源：{active.market.source || '未注明'}；实际时间：{active.market.updatedAt || '未取得'}。财务指标期：{reportDate(financialSource(active.fundamentals, 'indicator')?.period)}。不同资料按各自披露日期展示；观点、预测不代表已实现业绩，也不构成买卖指令。</p><p>页面可见时自动更新。资料库展示当前已载入资料，完整原文检索请使用 <Link to={`/essay-radar/feed?query=${encodeURIComponent(active.name || active.symbol)}`}>检索{active.name || active.symbol}的机构段子与录音</Link> 或 <Link to="/data-acquisition">数据一站式获取</Link>。</p></details>
      </> : workspaceError ? <section role="status" className="super-workspace-retry"><h2>当前股票暂未加载完成</h2><p>可以重试，或先切换其他股票；不会清空已读取的数据。</p><button type="button" onClick={() => void loadWorkspace(activeSymbol, true, workspaceView)}>重试当前股票</button><details><summary>查看原因</summary><p>{workspaceError}</p></details></section> : loading ? <div role="status" className="space-y-4 p-5" aria-label={`正在加载 ${activeSymbol || loadingSymbol || '自选股'} 数据`}><div className="h-16 animate-pulse rounded-xl bg-border/45" /><div className="h-[320px] animate-pulse rounded-xl bg-border/30" /><p className="text-center text-[12px] text-secondary-text">正在读取当前股票，其他股票不会阻塞本页。</p></div> : listError ? <EmptyState title="自选列表暂未读取" description="请点击上方“重试列表”，无需重新添加已有自选股。" /> : <EmptyState title="暂无自选股" description="输入股票名称或代码加入，行情与研究资料会自动归档。" />}</main>
    </div>
    {selectedEssay ? <EssayOriginalDrawer key={selectedEssay.externalId} event={selectedEssay} onClose={() => setSelectedEssay(null)} /> : null}
    {selectedForumPost ? <ForumPostDrawer key={selectedForumPost.id} event={selectedForumPost} onClose={() => setSelectedForumPost(null)} /> : null}
    <ConfirmDialog
      isOpen={Boolean(pendingDelete)}
      title="删除自选股"
      message={`确认将 ${pendingDelete?.name ?? ''}（${pendingDelete?.symbol ?? ''}）移出你的自选吗？已入库的历史行情、公告和研究资料会保留，不影响其他用户的自选。`}
      confirmText={deletingSymbol ? '删除中…' : '确认删除'}
      cancelText="取消"
      confirmDisabled={busy || Boolean(deletingSymbol)}
      cancelDisabled={Boolean(deletingSymbol)}
      isDanger
      onConfirm={() => void removeStock()}
      onCancel={() => setPendingDelete(null)}
    />
  </div></AppPage>;
}
