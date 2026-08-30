import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Archive, BrainCircuit, CalendarClock, ExternalLink, FileText, Image as ImageIcon, Layers3, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { essayRadarApi } from '../api/essayRadar';
import { investmentMonitorApi } from '../api/investmentMonitor';
import { systemConfigApi } from '../api/systemConfig';
import { AppPage, ConfirmDialog, Drawer, EmptyState, EvidenceRail } from '../components/common';
import { eventTime } from '../components/investmentMonitor/investmentMonitorMeta';
import { MarketTimeframeChart } from '../components/market';
import { StockAutocomplete } from '../components/StockAutocomplete/StockAutocomplete';
import type { EssayConsensusAnalysis, MonitorEvent, SuperWatchlistDashboard, SuperWatchlistStock } from '../types/investmentMonitor';
import { useRealtimeQuotes } from '../hooks/useRealtimeQuotes';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import type { RealtimeQuote } from '../api/realtimeQuotes';
import type { ResearchNoteDetail } from '../types/essayRadar';
import { shouldPreferQuote } from '../utils/marketQuoteDate';
import './SuperWatchlistPage.css';

type Section = 'overview' | 'fundamental' | 'capital' | 'institution' | 'essay' | 'consensus' | 'messages' | 'comments' | 'evidence';
const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: 'overview', label: '全景' }, { key: 'fundamental', label: '财务估值' },
  { key: 'capital', label: '资金筹码' }, { key: 'institution', label: '公告研报' },
  { key: 'essay', label: '小作文' }, { key: 'consensus', label: '一致预期' },
  { key: 'messages', label: '消息渠道' }, { key: 'comments', label: '股评监控' },
  { key: 'evidence', label: '全部证据' },
];

function number(value: unknown, digits = 2) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: digits }) : '—';
}
function percent(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(2)}%` : '—';
}
function money(value: unknown, tushareWan = false) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const yuan = tushareWan ? value * 10_000 : value;
  return Math.abs(yuan) >= 100_000_000 ? `${(yuan / 100_000_000).toFixed(2)} 亿` : Math.abs(yuan) >= 10_000 ? `${(yuan / 10_000).toFixed(2)} 万` : number(yuan, 0);
}

function applyRealtimeQuote(stock: SuperWatchlistStock, live?: RealtimeQuote): SuperWatchlistStock {
  if (!live || !Number.isFinite(live.currentPrice) || live.currentPrice <= 0
    || !shouldPreferQuote(live.updateTime, stock.market.updatedAt, Boolean(live.isStale))) return stock;
  return {
    ...stock,
    market: {
      ...stock.market,
      price: live.currentPrice,
      changePct: live.changePercent ?? stock.market.changePct,
      open: live.open ?? stock.market.open,
      high: live.high ?? stock.market.high,
      low: live.low ?? stock.market.low,
      amount: live.amount ?? stock.market.amount,
      updatedAt: live.updateTime ?? stock.market.updatedAt,
      source: live.source ?? stock.market.source,
      isStale: live.isStale ?? stock.market.isStale,
      staleSeconds: live.staleSeconds ?? stock.market.staleSeconds,
    },
  };
}

function EvidenceRow({ event, onEssayOpen, onForumOpen }: { event: MonitorEvent; onEssayOpen?: (event: MonitorEvent) => void; onForumOpen?: (event: MonitorEvent) => void }) {
  const evidence = (event.metrics._evidence ?? {}) as { evidenceLevel?: string };
  const isForumPost = event.eventType === 'stock_forum_post';
  const forumAuthor = typeof event.metrics.author === 'string' ? event.metrics.author : event.sourceName;
  const forumReplies = typeof event.metrics.replyCount === 'number' ? event.metrics.replyCount : 0;
  const body = <>
    <span className="font-mono text-[10px] text-[#6B7078]">{eventTime(event.eventAt)}</span>
    <span className="truncate text-[11px] text-[#4B5058]">{isForumPost ? forumAuthor : event.sourceName}</span>
    <span className="min-w-0 truncate text-[12px] font-medium text-[#17181A]">{event.title}</span>
    <span className={`text-[10px] ${evidence.evidenceLevel === 'unverified' ? 'text-[#B54708]' : 'text-[#027A48]'}`}>{evidence.evidenceLevel === 'unverified' ? '待核验' : '事实'}</span>
    <span className="inline-flex items-center justify-end gap-1 text-[10px] font-semibold text-[#155EEF]">{isForumPost ? `${forumReplies}评 · 详情` : '查看原文'}{event.eventType === 'essay' || isForumPost ? <FileText className="h-3 w-3" /> : <ExternalLink className="h-3 w-3" />}</span>
  </>;
  const cls = 'super-evidence-row grid items-center gap-3 border-t border-[#E1E3E7] px-3 py-2 text-left hover:bg-[#F7F8FA]';
  if (event.eventType === 'essay' && onEssayOpen) return <button type="button" className={`${cls} w-full`} onClick={() => onEssayOpen(event)}>{body}</button>;
  if (isForumPost && onForumOpen) return <button type="button" className={`${cls} w-full`} onClick={() => onForumOpen(event)}>{body}</button>;
  return event.url ? <a className={cls} href={event.url} target="_blank" rel="noreferrer">{body}</a> : <Link className={cls} to={`/investment-monitor/feed?event=${event.id}`}>{body}</Link>;
}

function ForumPostDrawer({ event, onClose }: { event: MonitorEvent; onClose: () => void }) {
  const [detail, setDetail] = useState(event);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    void investmentMonitorApi.event(event.id).then(value => {
      if (!cancelled) setDetail(value);
    }).catch(() => {
      // The compact row already contains the saved excerpt and attribution. Keep
      // it visible if the optional detail refresh is temporarily unavailable.
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [event]);

  const author = typeof detail.metrics.author === 'string' ? detail.metrics.author : detail.actors[0] || '作者未标注';
  const views = typeof detail.metrics.views === 'number' ? detail.metrics.views : 0;
  const replies = typeof detail.metrics.replyCount === 'number' ? detail.metrics.replyCount : 0;
  const likes = typeof detail.metrics.likeCount === 'number' ? detail.metrics.likeCount : 0;
  const images = Array.isArray(detail.metrics.imageUrls)
    ? detail.metrics.imageUrls.filter((value): value is string => typeof value === 'string' && Boolean(value))
    : [];

  return <Drawer isOpen onClose={onClose} title="股评详情" width="max-w-3xl">
    <article className="space-y-5 text-[11px]">
      <header className="border-b border-border/70 pb-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-secondary-text"><span className="font-semibold text-foreground">{author}</span><span>{eventTime(detail.eventAt)}</span><span>东方财富股吧 · 公开观点 · 待核验</span>{loading ? <span className="text-[#155EEF]">正在校验详情…</span> : null}</div>
        <div className="mt-3 grid grid-cols-3 overflow-hidden rounded-xl border border-border/70 bg-background/30 py-3"><Metric label="浏览" value={number(views, 0)} /><Metric label="回复" value={number(replies, 0)} /><Metric label="点赞" value={number(likes, 0)} /></div>
      </header>
      <section><div className="mb-3 flex items-center gap-2 text-[12px] font-bold"><FileText className="h-4 w-4" />帖子内容</div><div className="whitespace-pre-wrap break-words rounded-xl border border-border/70 bg-background/30 p-4 text-[13px] leading-7 text-foreground">{detail.summary || detail.title || '帖子正文摘录为空。'}</div></section>
      {images.length ? <section><div className="mb-3 flex items-center gap-2 text-[12px] font-bold"><ImageIcon className="h-4 w-4" />帖子图片</div><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{images.map((image, index) => <a key={`${image}-${index}`} href={image} target="_blank" rel="noreferrer" className="group overflow-hidden rounded-xl border border-border/70 bg-background/30"><img src={image} alt={`股评图片 ${index + 1}`} loading="lazy" className="max-h-80 w-full object-contain" /><span className="flex items-center justify-between px-3 py-2 text-[10px] text-secondary-text">查看原图<ExternalLink className="h-3 w-3" /></span></a>)}</div></section> : null}
      {detail.url ? <footer className="border-t border-border/70 pt-4"><a href={detail.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-2 rounded-lg border border-border/70 px-3 font-semibold text-[#155EEF] hover:bg-hover">在东方财富查看原帖<ExternalLink className="h-3.5 w-3.5" /></a></footer> : null}
    </article>
  </Drawer>;
}

function EssayOriginalDrawer({ event, onClose }: { event: MonitorEvent; onClose: () => void }) {
  const [note, setNote] = useState<ResearchNoteDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    void essayRadarApi.note(event.externalId).then(value => {
      if (!cancelled) setNote(value);
    }).catch(caught => {
      if (!cancelled) setError(caught instanceof Error ? caught.message : '原文暂时无法读取');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [event]);
  return <Drawer isOpen onClose={onClose} title={note?.title || event.title || '小作文原文'} width="max-w-3xl">
    <div className="space-y-5 text-[11px]">
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-border/70 pb-4 text-secondary-text"><span>{note?.groupName || event.sourceName}</span><span>{note?.authorName || event.actors[0] || '作者未标注'}</span><span>{eventTime(note?.createdAt || event.eventAt)}</span><span>知识星球 · 待核验</span></div>
      {loading ? <div className="space-y-3 py-4" aria-label="正在读取小作文原文"><div className="h-4 w-2/3 animate-pulse bg-border/70" /><div className="h-3 w-full animate-pulse bg-border/50" /><div className="h-3 w-5/6 animate-pulse bg-border/50" /></div> : null}
      {error ? <div className="border border-destructive/40 bg-destructive/10 p-3 text-destructive">{error}</div> : null}
      {note && !loading ? <>
        <section><div className="mb-3 flex items-center gap-2 text-[12px] font-bold"><FileText className="h-4 w-4" />原文正文</div><div className="whitespace-pre-wrap break-words border border-border/70 bg-background/30 p-4 text-[12px] leading-6 text-foreground">{note.content || '本条正文为空，请查看下方图片或附件。'}</div></section>
        {note.images.length ? <section><div className="mb-3 flex items-center gap-2 text-[12px] font-bold"><ImageIcon className="h-4 w-4" />原文图片</div><div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{note.images.map((image, index) => image.viewUrl ? <a key={image.imageId || index} href={image.viewUrl} target="_blank" rel="noreferrer" className="group overflow-hidden border border-border/70 bg-background/30"><img src={image.viewUrl} alt={`小作文图片 ${index + 1}`} loading="lazy" className="max-h-72 w-full object-contain" /><span className="flex items-center justify-between px-3 py-2 text-[10px] text-secondary-text">图片 {index + 1}<ExternalLink className="h-3 w-3" /></span></a> : null)}</div></section> : null}
        {note.files.length ? <section><div className="mb-3 flex items-center gap-2 text-[12px] font-bold"><Archive className="h-4 w-4" />原文附件</div><div className="space-y-2">{note.files.map((file, index) => file.viewUrl ? <a key={file.fileId || index} href={file.viewUrl} target="_blank" rel="noreferrer" className="flex items-center justify-between border border-border/70 bg-background/30 px-3 py-3 hover:bg-hover"><span className="flex min-w-0 items-center gap-2"><FileText className="h-4 w-4 shrink-0" /><span className="truncate">{file.name || `附件 ${index + 1}`}</span></span><ExternalLink className="h-3.5 w-3.5 shrink-0" /></a> : null)}</div></section> : null}
      </> : null}
      {event.url ? <div className="border-t border-border/70 pt-4"><a href={event.url} target="_blank" rel="noreferrer" className="inline-flex h-9 items-center gap-2 border border-border/70 px-3 font-semibold text-[#155EEF] hover:bg-hover">在知识星球打开<ExternalLink className="h-3.5 w-3.5" /></a></div> : null}
    </div>
  </Drawer>;
}

function Metric({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className="border-r border-[#E1E3E7] px-3 last:border-r-0"><p className="text-[9px] text-[#7B7F87]">{label}</p><p className={`mt-1 font-mono text-[14px] font-bold ${tone}`}>{value}</p></div>;
}

function Overview({ stock }: { stock: SuperWatchlistStock }) {
  return <div className="grid min-h-[220px] grid-cols-2">
    <section className="border-r border-[#D8DADF] p-4"><h3 className="text-[12px] font-bold">事实驱动观察</h3><div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2">{stock.signals.slice(0, 6).map((item, i) => <div key={`${item.title}-${i}`} className={`border-l-2 py-1 pl-2 ${item.kind === 'risk' ? 'border-[#B42318]' : item.kind === 'catalyst' ? 'border-[#027A48]' : 'border-[#155EEF]'}`}><p className="truncate text-[11px] font-semibold">{item.title}</p><p className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-[#62666D]">{item.detail}</p></div>)}</div></section>
    <section className="p-4"><h3 className="text-[12px] font-bold">基本面与估值快照</h3><div className="mt-3 grid grid-cols-4 gap-y-4"><Metric label="PE(TTM)" value={number(stock.valuation.peTtm)} /><Metric label="PB" value={number(stock.valuation.pb)} /><Metric label="ROE" value={percent(stock.fundamentals.roe)} /><Metric label="毛利率" value={percent(stock.fundamentals.grossMargin)} /><Metric label="收入同比" value={percent(stock.fundamentals.revenueYoy)} /><Metric label="利润同比" value={percent(stock.fundamentals.netProfitYoy)} /><Metric label="筹码获利" value={percent(stock.capital.winnerRate)} /><Metric label="事实证据" value={String(stock.evidence.factualCount)} /></div></section>
  </div>;
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

function ConsensusSection({ stock, onEssayOpen, onAnalyze, analyzing, analysisMessage }: {
  stock: SuperWatchlistStock; onEssayOpen: (event: MonitorEvent) => void;
  onAnalyze: () => void; analyzing: boolean; analysisMessage?: string;
}) {
  const consensus = stock.consensus;
  const essay = consensus.essayAnalysis;
  const dedicatedNotes = essay.sourceNotes.filter(note => note.sourceKind === 'dedicated');
  const relatedNotes = essay.sourceNotes.filter(note => note.sourceKind !== 'dedicated');
  const timeline = [...essay.estimates]
    .sort((a, b) => String(b.proposedAt || b.eventAt || '').localeCompare(String(a.proposedAt || a.eventAt || '')))
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
      <span className="super-source-meta"><time>{eventTime(note.eventAt)}</time><em>{note.authorName || '作者未标注'}</em><b>{note.estimateCount} 条预期</b></span>
    </button>)}{!notes.length ? <p className="super-source-empty">当前样本中暂无此类小作文</p> : null}</div>
  </section>;

  return <section className="super-consensus-workbench min-h-[720px]">
    <header className="super-consensus-hero">
      <div className="super-consensus-title"><span className="super-consensus-orb"><BrainCircuit /></span><div><p>{stock.name} · {stock.symbol}</p><div><h3>一致预期研究工作台</h3><span className={`super-consensus-status is-${essay.status}`}>{CONSENSUS_STATUS_LABELS[essay.status] ?? essay.status}</span></div><small>将券商结构化预测与知识星球原文证据分开呈现，所有结论保留提出时间与预测周期。</small></div></div>
      <div className="super-consensus-actions"><Link to={`/essay-radar/feed?stock=${encodeURIComponent(stock.symbol)}`}>查看全部原文 <ExternalLink /></Link><button type="button" onClick={onAnalyze} disabled={analyzing || essay.status === 'processing'} className="super-consensus-run"><RefreshCw className={analyzing || essay.status === 'processing' ? 'animate-spin' : ''} />{analyzing || essay.status === 'processing' ? '正在分析 20+5 篇' : '重新分析 20+5 篇'}</button></div>
      {analysisMessage ? <p className="super-consensus-message">{analysisMessage}</p> : null}
    </header>

    <section className="super-consensus-scope" aria-label="研究样本范围">
      <div><span>相关小作文</span><strong>{essay.relatedSourceCount ?? relatedNotes.length}</strong><small>最多 20 篇 · 多标的或关键词相关</small></div>
      <div><span>单股专属</span><strong>{essay.dedicatedSourceCount ?? dedicatedNotes.length}</strong><small>最多 5 篇 · 标签仅含 {stock.name}</small></div>
      <div><span>券商研报</span><strong>{consensus.brokerReportCount}</strong><small>Tushare report_rc 去重样本</small></div>
      <div><span>分析截止</span><strong className="is-date">{eventTime(essay.analysisCutoffAt || essay.completedAt || consensus.asOf)}</strong><small>{essay.analyzedCount ? `已读取 ${essay.analyzedCount} 篇，提取 ${essay.estimates.length} 条预期` : '等待建立当前研究快照'}</small></div>
    </section>

    <section className="super-forecast-timeline">
      <header><div><CalendarClock /><h4>预测时间线</h4></div><p>上行是观点提出时间，下行是预测对应期间；旧观点不会冒充最新预期。</p></header>
      <div className="super-timeline-track">{timeline.map((item, index) => <button type="button" key={`${item.topicId}-${index}`} onClick={() => onEssayOpen(toEssayEvent(item.topicId, item.eventId, item.title, item.proposedAt || item.eventAt))}>
        <time>{eventTime(item.proposedAt || item.eventAt)}</time><span /><strong>{EXPECTATION_METRIC_LABELS[item.metric] ?? item.metric}</strong><b>{item.valueText}</b><small>预测期 {item.period || '未注明'}</small>
      </button>)}{!timeline.length ? <p className="super-timeline-empty">运行专项分析后，这里会按“何时提出 → 预测何时”排列真实原文预期。</p> : null}</div>
    </section>

    <div className="super-consensus-body">
      <main>
        <section className="super-consensus-conclusion"><div className="super-section-kicker"><Sparkles /><span>综合结论</span></div><p>{essay.summary || '尚未形成专项结论。运行分析后，系统会按提出时间、预测期间与主体口径整理这批原文。'}</p><div className="super-outlook-grid"><article><span>利润与经营预期</span><p>{essay.profitOutlook || '当前没有可追溯的利润、收入或 EPS 预期。'}</p></article><article><span>估值与市值推测</span><p>{essay.valuationOutlook || '当前没有可追溯的目标价、市值或估值倍数推测。'}</p></article></div></section>

        <section className="super-expectation-matrix"><header><div><Layers3 /><div><h4>关键预期证据矩阵</h4><p>每个数字都回到原文；单股专属与相关样本不重复计数。</p></div></div><span>{essay.estimates.length} 条</span></header><div className="super-matrix-scroll"><table><thead><tr><th>提出时间</th><th>预测期</th><th>指标</th><th>原文预期</th><th>主体口径</th><th>样本</th></tr></thead><tbody>{essay.estimates.map((item, index) => {
          const openOriginal = () => onEssayOpen(toEssayEvent(item.topicId, item.eventId, item.title, item.proposedAt || item.eventAt));
          return <tr key={`${item.topicId}-${index}`} role="button" tabIndex={0} aria-label={`查看原文 ${item.title || ''} ${item.valueText}`} onClick={openOriginal} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') openOriginal(); }}><td><time>{eventTime(item.proposedAt || item.eventAt)}</time></td><td><b>{item.period || '未注明'}</b></td><td><span>{EXPECTATION_METRIC_LABELS[item.metric] ?? item.metric}</span></td><td><strong>{item.valueText}</strong><small>{item.evidence}</small></td><td>{item.subject || stock.name}<small>{SUBJECT_RELATION_LABELS[item.subjectRelation ?? 'target_stock'] ?? '关联主体'}</small></td><td><em className={item.sourceKind === 'dedicated' ? 'is-dedicated' : ''}>{item.sourceKind === 'dedicated' ? '单股专属' : '相关'}</em></td></tr>;
        })}</tbody></table>{!essay.estimates.length ? <EmptyState title={essay.status === 'processing' || essay.status === 'pending' ? '正在提取时间化预期' : '尚无明确预期'} description={essay.status === 'failed' ? (essay.error || '专项分析失败，可重新运行。') : '点击“重新分析 20+5 篇”，系统会提取预测提出时间、预测期、数值和原文证据。'} /> : null}</div></section>

        <section className="super-broker-panel"><header><div><h4>券商一致预期</h4><p>结构化研报口径单列，不与小作文推测混算。</p></div><div><span>目标价中位</span><strong>{number(consensus.targetPrice.median)}</strong></div></header><div className="super-broker-targets"><Metric label="目标价下限" value={number(consensus.targetPrice.min)} /><Metric label="目标价中位" value={number(consensus.targetPrice.median)} /><Metric label="目标价上限" value={number(consensus.targetPrice.max)} /></div><div className="super-broker-table"><table><thead><tr><th>预测期</th><th>样本</th><th>EPS中位</th><th>净利润中位</th><th>PE中位</th><th>ROE中位</th></tr></thead><tbody>{consensus.forecasts.map(row => <tr key={row.period}><td>{row.period}</td><td>{row.sampleCount}</td><td>{number(row.epsMedian)}</td><td>{number(row.npMedian)}</td><td>{number(row.peMedian)}</td><td>{number(row.roeMedian)}</td></tr>)}</tbody></table>{!consensus.forecasts.length ? <p>暂无券商结构化预测，等待 report_rc 同步。</p> : null}</div></section>
      </main>

      <aside className="super-consensus-sources"><header><div><h4>引用原文</h4><p>点击在当前页面打开全文、图片与附件。</p></div><span>{essay.sourceCount} 篇</span></header>{renderSourceGroup('单股专属小作文', `标签中仅有 ${stock.name}`, dedicatedNotes, 'dedicated')}{renderSourceGroup('相关小作文', '多标的、产业链或关键词匹配', relatedNotes, 'related')}</aside>
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
  return <article className="super-expectation-list-card"><h5 className={toneClass}>{title}</h5>{items.length ? <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p>当前样本未形成明确条目</p>}</article>;
}

function EventSection({ events, emptyTitle, emptyDescription, note, onEssayOpen, onForumOpen }: { events: MonitorEvent[]; emptyTitle: string; emptyDescription: string; note?: string; onEssayOpen: (event: MonitorEvent) => void; onForumOpen: (event: MonitorEvent) => void }) {
  return <div>{note ? <div className="border-b border-[#D8DADF] bg-[#F7F8FA] px-3 py-2 text-[9px] leading-4 text-[#62666D]">{note}</div> : null}<div className="max-h-[340px] overflow-auto">{events.map(event => <EvidenceRow key={event.id} event={event} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />)}{!events.length ? <EmptyState title={emptyTitle} description={emptyDescription} /> : null}</div></div>;
}

function DetailSection({ section, stock, onEssayOpen, onForumOpen, onAnalyzeConsensus, analyzingConsensus, consensusMessage }: { section: Section; stock: SuperWatchlistStock; onEssayOpen: (event: MonitorEvent) => void; onForumOpen: (event: MonitorEvent) => void; onAnalyzeConsensus: () => void; analyzingConsensus: boolean; consensusMessage?: string }) {
  if (section === 'overview') return <Overview stock={stock} />;
  if (section === 'fundamental') return <div className="grid grid-cols-4 gap-y-5 p-5"><Metric label="营业收入" value={money(stock.fundamentals.revenue)} /><Metric label="归母净利润" value={money(stock.fundamentals.netProfit)} /><Metric label="经营现金流" value={money(stock.fundamentals.operatingCashflow)} /><Metric label="总市值" value={money(stock.valuation.totalMv, true)} /><Metric label="收入同比" value={percent(stock.fundamentals.revenueYoy)} /><Metric label="利润同比" value={percent(stock.fundamentals.netProfitYoy)} /><Metric label="净利率" value={percent(stock.fundamentals.netMargin)} /><Metric label="资产负债率" value={percent(stock.fundamentals.debtRatio)} /></div>;
  if (section === 'capital') return <div className="grid grid-cols-4 gap-y-5 p-5"><Metric label="获利比例" value={percent(stock.capital.winnerRate)} /><Metric label="加权成本" value={number(stock.capital.weightedCost)} /><Metric label="RSI(6)" value={number(stock.technical.rsi6)} /><Metric label="MACD" value={number(stock.technical.macd, 3)} /><Metric label="50%成本" value={number(stock.capital.cost50pct)} /><Metric label="85%成本" value={number(stock.capital.cost85pct)} /><Metric label="布林中轨" value={number(stock.technical.bollMid)} /><Metric label="CCI" value={number(stock.technical.cci)} /></div>;
  if (section === 'consensus') return <ConsensusSection stock={stock} onEssayOpen={onEssayOpen} onAnalyze={onAnalyzeConsensus} analyzing={analyzingConsensus} analysisMessage={consensusMessage} />;
  if (section === 'essay') return <EventSection events={stock.alternative.essays} emptyTitle="暂无匹配小作文" emptyDescription="按股票全称、简称和代码匹配；新增自选股会自动重建关键词索引。" note={`共匹配 ${stock.alternative.essayCount} 篇；点击任意一条在当前页面查看原文。`} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  if (section === 'messages') return <EventSection events={stock.messages.items} emptyTitle="暂无渠道消息" emptyDescription="等待相关新闻、知识星球或天眼查增量同步。" note={`相关新闻、知识星球、天眼查共 ${stock.messages.count} 条；各渠道保持原始来源和证据等级。`} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  if (section === 'comments') return <EventSection events={stock.stockComments.items} emptyTitle="暂无公开股评" emptyDescription="等待东方财富股吧真实公开帖子增量同步。" note={stock.stockComments.sourceNote} onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
  const events = section === 'institution' ? [...stock.company.announcements, ...stock.institution.latest] : stock.timeline;
  return <EventSection events={events.slice(0, section === 'evidence' ? 30 : 20)} emptyTitle="暂无数据" emptyDescription="等待对应渠道完成回填。" onEssayOpen={onEssayOpen} onForumOpen={onForumOpen} />;
}

export default function SuperWatchlistPage() {
  const [data, setData] = useState<SuperWatchlistDashboard | null>(null);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [refreshingShared, setRefreshingShared] = useState(false);
  const [section, setSection] = useState<Section>('overview'); const [newSymbol, setNewSymbol] = useState('');
  const [selectedEssay, setSelectedEssay] = useState<MonitorEvent | null>(null);
  const [selectedForumPost, setSelectedForumPost] = useState<MonitorEvent | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SuperWatchlistStock | null>(null);
  const [deletingSymbol, setDeletingSymbol] = useState<string | null>(null);
  const [consensusOverrides, setConsensusOverrides] = useState<Record<string, EssayConsensusAnalysis>>({});
  const [analyzingConsensus, setAnalyzingConsensus] = useState(false);
  const [consensusMessage, setConsensusMessage] = useState('');
  const [params, setParams] = useSearchParams(); const navigate = useNavigate();
  const requestInFlight = useRef(false);
  useEffect(() => { document.title = '自选股超级看板 - 乐子乌超级价值'; }, []);
  const load = useCallback(async (force = false) => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    setLoading(true); setError('');
    try { setData(await investmentMonitorApi.superWatchlist(183, force)); }
    catch (err) { setError(err instanceof Error ? err.message : '加载失败'); }
    finally { requestInFlight.current = false; setLoading(false); }
  }, []);
  usePageActivationRefresh(load, { intervalMs: 60_000, minIntervalMs: 5_000 });
  const symbols = useMemo(() => (data?.stocks ?? []).map(row => row.symbol), [data]);
  const { quotes: liveQuotes, keyFor: quoteKey } = useRealtimeQuotes(symbols);
  const stocks = useMemo(() => (data?.stocks ?? []).map(stock => (
    applyRealtimeQuote(stock, liveQuotes.get(quoteKey(stock.symbol)))
  )), [data, liveQuotes, quoteKey]);
  const active = useMemo(() => stocks.find(row => row.symbol === params.get('symbol')) ?? stocks[0] ?? null, [stocks, params]);
  const activeForDetail = useMemo(() => {
    if (!active || !consensusOverrides[active.symbol]) return active;
    return { ...active, consensus: { ...active.consensus, essayAnalysis: consensusOverrides[active.symbol], essayExpectationCount: consensusOverrides[active.symbol].estimates.length, essayExpectations: consensusOverrides[active.symbol].estimates.map(item => ({ ...item, text: item.valueText })) } };
  }, [active, consensusOverrides]);
  const activeLiveQuote = active ? liveQuotes.get(quoteKey(active.symbol)) : undefined;
  const hasActiveLiveQuote = Boolean(active && activeLiveQuote && activeLiveQuote.currentPrice > 0
    && shouldPreferQuote(activeLiveQuote.updateTime, data?.stocks.find(row => row.symbol === active.symbol)?.market.updatedAt, Boolean(activeLiveQuote.isStale)));
  const submitStock = async (rawSymbol: string) => {
    const symbol = rawSymbol.trim();
    if (!symbol || busy) return;
    setBusy(true); setError('');
    try { await systemConfigApi.addToWatchlist(symbol); setNewSymbol(''); await load(true); }
    catch (err) { setError(err instanceof Error ? err.message : '添加失败'); }
    finally { setBusy(false); }
  };
  const addStock = (event: FormEvent) => { event.preventDefault(); void submitStock(newSymbol); };
  const removeStock = async () => {
    if (!pendingDelete) return;
    const removed = pendingDelete;
    setDeletingSymbol(removed.symbol);
    setError('');
    try {
      await systemConfigApi.removeFromWatchlist(removed.symbol);
      const remaining = stocks.filter(row => row.symbol !== removed.symbol);
      setPendingDelete(null);
      if (active?.symbol === removed.symbol) {
        const next = remaining[0];
        setParams(next ? { symbol: next.symbol } : {}, { replace: true });
      }
      await load(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setDeletingSymbol(null);
    }
  };
  const refreshShared = async () => {
    if (refreshingShared) return;
    setRefreshingShared(true); setError('');
    try { await investmentMonitorApi.refreshSuperWatchlist(); await load(true); }
    catch (err) { setError(err instanceof Error ? err.message : '共享数据刷新失败'); }
    finally { setRefreshingShared(false); }
  };
  const analyzeConsensus = async () => {
    if (!active || analyzingConsensus) return;
    const symbol = active.symbol;
    setAnalyzingConsensus(true); setConsensusMessage('已提交最多 20 篇相关小作文与 5 篇单股专属小作文，正在按提出时间和预测期提取预期。');
    try {
      const queued = await investmentMonitorApi.analyzeEssayConsensus(symbol);
      setConsensusOverrides(previous => ({ ...previous, [symbol]: queued.consensus }));
      for (let attempt = 0; attempt < 36; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 5000));
        const current = await investmentMonitorApi.essayConsensus(symbol);
        setConsensusOverrides(previous => ({ ...previous, [symbol]: current.consensus }));
        if (current.consensus.status === 'completed') {
          setConsensusMessage(`分析完成：读取 ${current.consensus.relatedSourceCount ?? 0} 篇相关、${current.consensus.dedicatedSourceCount ?? 0} 篇单股专属小作文，提取 ${current.consensus.estimates.length} 条可追溯预期。`);
          return;
        }
        if (current.consensus.status === 'failed') throw new Error(current.consensus.error || '小作文一致预期分析失败');
      }
      setConsensusMessage('后台仍在分析，可以继续浏览其他页面；完成结果会保存在本地数据库。');
    } catch (err) {
      setConsensusMessage(err instanceof Error ? err.message : '小作文一致预期分析失败');
    } finally {
      setAnalyzingConsensus(false);
    }
  };
  const openModel = () => { if (!active) return; const evidence = active.timeline.slice(0, 12).map(row => `[${row.id}] ${row.sourceName}｜${row.title}`).join('\n'); navigate(`/chat?prompt=${encodeURIComponent(`请对${active.name}（${active.symbol}）基于以下事实证据做多空、催化、风险和证伪条件分析，不得补造数据。\n${evidence}`)}`); };
  return <AppPage className="super-watchlist-page max-w-[1900px]"><div className="super-watchlist-shell border border-[#C9CCD2] bg-white text-[#17181A]">
    <header className="super-watchlist-header flex h-14 flex-wrap items-center justify-between gap-3 border-b border-[#D8DADF] px-5 py-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan">共享行情库 · 统一事实库 · 后台增量更新</p><h1 className="mt-1 text-[20px] font-bold tracking-[-0.03em]">自选股超级看板</h1></div><button onClick={() => void refreshShared()} disabled={refreshingShared} aria-label="刷新共享行情并唤醒到期数据源" className="super-refresh-button inline-flex h-8 items-center gap-1.5 rounded-lg border border-border/70 px-2.5 text-[10px] font-semibold disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${refreshingShared || loading ? 'animate-spin' : ''}`} />刷新行情与情报</button></header>
    <EvidenceRail className="super-evidence-rail" items={[
      { label: '当前标的', value: active ? `${active.name} · ${active.symbol}` : '等待选择', note: active ? eventTime(active.market.updatedAt) : '加入自选后自动建档', tone: active ? 'verified' : 'warning' },
      { label: '行情口径', value: hasActiveLiveQuote ? '实时快照' : '最近事实快照', note: active?.market.source || '来源等待同步', tone: hasActiveLiveQuote ? 'live' : 'default' },
      { label: '证据规模', value: active ? `${active.evidence.rawEventCount} 条` : '—', note: active ? `原文覆盖 ${active.evidence.originalLinkCoverage}%` : '公告、研报、段子、股评', tone: active?.evidence.rawEventCount ? 'verified' : 'warning' },
    ]} />
    {error ? <div className="border-b border-[#FDA29B] bg-[#FEF3F2] px-4 py-2 text-[11px] text-[#B42318]">{error}</div> : null}
    <div className={`super-watchlist-grid grid grid-cols-1 ${section === 'consensus' ? 'is-consensus' : ''}`}>
      <aside className="super-watchlist-sidebar border-b border-r border-[#D8DADF] bg-[#FAFBFC] xl:border-b-0">
        <form onSubmit={addStock} className="border-b border-[#D8DADF] p-3"><div className="flex"><div className="min-w-0 flex-1"><StockAutocomplete value={newSymbol} onChange={setNewSymbol} onSubmit={(symbol) => void submitStock(symbol)} disabled={busy} placeholder="输入股票名称或代码" className="h-8 rounded-none border-[#C9CCD2] px-2 text-[11px] focus:border-[#155EEF]" /></div><button type="submit" disabled={busy || !newSymbol.trim()} aria-label="加入自选股" className="flex h-8 w-8 shrink-0 items-center justify-center bg-[#155EEF] text-white disabled:opacity-40"><Plus className="h-4 w-4" /></button></div><p className="mt-1.5 text-[9px] text-[#7B7F87]">支持中文名称、拼音简称和股票代码；加入后自动建立行情与全渠道信息档案</p></form>
        <div className="super-watchlist-stock-rail">{stocks.map(stock => {
          const selected = stock.symbol === active?.symbol;
          return <div key={stock.symbol} className={`super-watchlist-item flex border-b border-[#D8DADF] ${selected ? 'is-selected bg-[#EEF4FF]' : ''}`}>
            <button type="button" onClick={() => setParams({ symbol: stock.symbol }, { replace: true })} className="super-watchlist-select min-w-0 flex-1 p-3 text-left hover:bg-white">
              <div className="flex items-center justify-between gap-2"><span className="truncate text-[13px] font-bold">{stock.name}</span><span className={`shrink-0 font-mono text-[12px] font-bold ${(stock.market.changePct ?? 0) >= 0 ? 'text-[#B42318]' : 'text-[#027A48]'}`}>{number(stock.market.price)}</span></div>
              <div className="mt-1 flex justify-between font-mono text-[9px] text-[#62666D]"><span>{stock.symbol}</span><span>{percent(stock.market.changePct)}</span></div>
            </button>
            <button type="button" onClick={() => setPendingDelete(stock)} disabled={deletingSymbol === stock.symbol} aria-label={`删除自选股 ${stock.name}`} className="super-watchlist-delete m-2 ml-0 inline-flex w-8 shrink-0 items-center justify-center self-stretch rounded-lg text-[#7B7F87] hover:bg-[#FEF3F2] hover:text-[#B42318] disabled:cursor-wait disabled:opacity-40"><Trash2 className="h-3.5 w-3.5" /></button>
          </div>;
        })}</div>
      </aside>
      <main className="super-watchlist-main min-w-0">{active ? <>
        <section className="super-market-band flex h-20 items-center justify-between gap-4 border-b border-[#D8DADF] px-5 py-3"><div><div className="flex flex-wrap items-center gap-3"><h2 className="text-[22px] font-bold">{active.name}</h2><span className="rounded-full border border-border/70 bg-card/50 px-2 py-1 font-mono text-[10px] text-[#62666D]">{active.symbol}</span><span className={`font-mono text-[24px] font-bold ${(active.market.changePct ?? 0) >= 0 ? 'text-[#B42318]' : 'text-[#027A48]'}`}>{number(active.market.price)} <small className="text-[12px]">{percent(active.market.changePct)}</small></span></div><p className="mt-1 text-[10px] text-[#7B7F87]">{hasActiveLiveQuote || active.market.source?.includes('snapshot') ? '最新行情 · 共享行情库' : '日线事实快照'} {eventTime(active.market.updatedAt)} · 半年日线 {active.history.length} 条 · 完整证据 {active.evidence.rawEventCount} 条 · 原文覆盖 {active.evidence.originalLinkCoverage}%</p></div><button onClick={openModel} className="btn-primary inline-flex h-9 shrink-0 items-center gap-1.5 px-4 text-[11px] font-semibold"><Sparkles className="h-3.5 w-3.5" />深度研判</button></section>
        {section !== 'consensus' ? <section className="super-chart-panel border-b border-[#D8DADF] p-3"><div className="grid grid-cols-2 overflow-hidden rounded-xl border border-[#E1E3E7] bg-card/35 py-3 sm:grid-cols-3 md:grid-cols-6"><Metric label="开盘" value={number(active.market.open)} /><Metric label="最高" value={number(active.market.high)} /><Metric label="最低" value={number(active.market.low)} /><Metric label="成交额" value={money(active.market.amount)} /><Metric label="PE(TTM)" value={number(active.valuation.peTtm)} /><Metric label="PB" value={number(active.valuation.pb)} /></div><MarketTimeframeChart symbol={active.symbol} initialPeriod="intraday" initialRange="1d" className="mt-3" /></section> : null}
        <nav className="super-detail-nav flex h-10 border-b border-[#D8DADF] px-2">{SECTIONS.map(item => <button key={item.key} onClick={() => setSection(item.key)} className={`border-b-2 px-4 text-[11px] font-semibold ${section === item.key ? 'border-[#155EEF] text-[#155EEF]' : 'border-transparent text-[#62666D]'}`}>{item.label}</button>)}</nav>
        <DetailSection section={section} stock={activeForDetail ?? active} onEssayOpen={setSelectedEssay} onForumOpen={setSelectedForumPost} onAnalyzeConsensus={() => void analyzeConsensus()} analyzingConsensus={analyzingConsensus} consensusMessage={consensusMessage} />
        {section !== 'consensus' ? <section className="overflow-x-auto border-t border-[#D8DADF]"><div className="flex items-center justify-between px-3 py-2"><h3 className="text-[12px] font-bold">事实时间线</h3><Link to={`/investment-monitor/feed?symbol=${encodeURIComponent(active.symbol)}`} className="text-[10px] font-semibold text-[#155EEF]">查看全部证据</Link></div>{active.timeline.slice(0, 6).map(event => <EvidenceRow key={event.id} event={event} onEssayOpen={setSelectedEssay} onForumOpen={setSelectedForumPost} />)}</section> : null}
      </> : !loading ? <EmptyState title="暂无自选股" description="在左侧输入股票名称或代码加入，系统会自动建立行情与全渠道信息档案。" /> : null}</main>
    </div>
    {selectedEssay ? <EssayOriginalDrawer key={selectedEssay.externalId} event={selectedEssay} onClose={() => setSelectedEssay(null)} /> : null}
    {selectedForumPost ? <ForumPostDrawer key={selectedForumPost.id} event={selectedForumPost} onClose={() => setSelectedForumPost(null)} /> : null}
    <ConfirmDialog
      isOpen={Boolean(pendingDelete)}
      title="删除自选股"
      message={`确认将 ${pendingDelete?.name ?? ''}（${pendingDelete?.symbol ?? ''}）移出自选吗？删除后会停止后续自动监控，但已入库的历史行情、公告和研究资料会保留。`}
      confirmText={deletingSymbol ? '删除中…' : '确认删除'}
      cancelText="取消"
      confirmDisabled={Boolean(deletingSymbol)}
      cancelDisabled={Boolean(deletingSymbol)}
      isDanger
      onConfirm={() => void removeStock()}
      onCancel={() => setPendingDelete(null)}
    />
  </div></AppPage>;
}
