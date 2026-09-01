import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Archive, ArrowRight, BarChart3, Brain, Database, Download, ExternalLink, FileText, GitBranch, Headphones, Image,
  ListFilter, RefreshCw, Search, Settings2, ShieldAlert, Sparkles, TrendingUp,
} from 'lucide-react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { essayRadarApi } from '../api/essayRadar';
import { AppPage, Badge, Drawer, EmptyState } from '../components/common';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import type {
  EssayAnalysis, EssayAnalysisList, EssayDailyReport, EssayDailyReportList, EssayDashboard, EssayDeepInsights, EssayInsights,
  EssayAudioAnalysisCapability, EssayAudioAnalysisTask, EssayAudioBatchTask, EssayAudioDownloadProgress, EssayAudioFile, EssayAudioFileList, EssayAudioTranscript, EssayHistoricalBacklog, EssayStatus, EssayWordCloud,
} from '../types/essayRadar';
import './EssayRadarPage.css';

type RadarView = 'overview' | 'atlas' | 'feed' | 'trends' | 'reports' | 'system';
type AtlasHorizon = 'short' | 'medium' | 'long' | 'custom';

const VIEW_META: Array<{ view: RadarView; path: string; label: string; icon: typeof Sparkles }> = [
  { view: 'overview', path: '/essay-radar', label: '今日研判', icon: Sparkles },
  { view: 'atlas', path: '/essay-radar/insights', label: '洞察图谱', icon: GitBranch },
  { view: 'feed', path: '/essay-radar/feed', label: '检索与获取', icon: ListFilter },
  { view: 'trends', path: '/essay-radar/trends', label: '趋势追踪', icon: TrendingUp },
  { view: 'reports', path: '/essay-radar/reports', label: '每日报告', icon: Brain },
  { view: 'system', path: '/essay-radar/system', label: '数据管理', icon: Settings2 },
];

const SENTIMENT_LABELS: Record<string, string> = {
  bullish: '看多', bearish: '看空', neutral: '中性', mixed: '分歧',
};
const CATEGORY_LABELS: Record<string, string> = {
  company_research: '公司调研', broker_view: '券商观点', industry_chain: '产业链',
  macro_policy: '宏观政策', market_flow: '资金市场', event_catalyst: '事件催化',
  earnings: '业绩', risk_warning: '风险预警', rumor: '传闻', other: '其他',
};
const ANALYSIS_STATUS_LABELS: Record<string, string> = {
  not_queued: '未分析', pending: '排队中', processing: '分析中', failed: '分析失败', completed: '已分析',
  media_only: '录音资料',
};
// Each feed page contains original text and media metadata. Keeping dozens of
// pages alive made long research sessions consume hundreds of MB in the tab.
const FEED_PAGE_CACHE_LIMIT = 16;
const AUDIO_BATCH_TASK_STORAGE_KEY = 'dsa:essay-radar:audio-batch-task';
const AUDIO_ANALYSIS_TASK_STORAGE_KEY = 'dsa:essay-radar:audio-analysis-task';

function radarView(pathname: string): RadarView {
  if (pathname.endsWith('/insights')) return 'atlas';
  if (pathname.endsWith('/feed')) return 'feed';
  if (pathname.endsWith('/trends')) return 'trends';
  if (pathname.endsWith('/reports')) return 'reports';
  if (pathname.endsWith('/system')) return 'system';
  return 'overview';
}

function formatTime(value?: string | null, withYear = false) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    ...(withYear ? { year: 'numeric' as const } : {}),
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function formatDate(value?: string | null) {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function formatClock(value?: string | null) {
  if (!value) return '等待首轮数据';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDuration(seconds?: number) {
  if (!seconds || seconds <= 0) return '';
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return `${minutes}:${String(remain).padStart(2, '0')}`;
}

function formatAssetSize(bytes?: number) {
  if (!bytes || bytes <= 0) return '';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.ceil(bytes / 1024)} KB`;
}

function errorText(value: unknown, fallback: string) {
  return value instanceof Error ? value.message : fallback;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function sentimentVariant(value?: string | null) {
  if (value === 'bullish') return 'success' as const;
  if (value === 'bearish') return 'danger' as const;
  if (value === 'mixed') return 'warning' as const;
  return 'default' as const;
}

function RadarNavigation({ active }: { active: RadarView }) {
  return (
    <nav className="essay-tabs" aria-label="机构段子与录音工作台页面">
      {VIEW_META.map(({ view, path, label, icon: Icon }) => (
        <Link key={view} to={path} className={active === view ? 'is-active' : ''}>
          <Icon className="h-4 w-4" />
          <span>{label}</span>
        </Link>
      ))}
    </nav>
  );
}

function Metric({ label, value, note, tone = 'plain' }: { label: string; value: string | number; note?: string; tone?: 'plain' | 'signal' | 'danger' }) {
  return (
    <div className={`essay-metric essay-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  );
}

function heatLevel(score: number, sortedScores: number[]): number {
  if (score <= 0 || sortedScores.length === 0) return 0;
  let upperRank = 0;
  for (const candidate of sortedScores) {
    if (candidate <= score) upperRank += 1;
  }
  return Math.max(1, Math.min(5, Math.ceil(upperRank / sortedScores.length * 5)));
}

function DeepInsightsView({ data, horizon, startDate, endDate, onPeriodChange, onFilter }: {
  data: EssayDeepInsights | null;
  horizon: AtlasHorizon;
  startDate: string;
  endDate: string;
  onPeriodChange: (horizon: AtlasHorizon, startDate?: string, endDate?: string) => void;
  onFilter: (term: string) => void;
}) {
  const [selectedCode, setSelectedCode] = useState('');
  const [draftStart, setDraftStart] = useState(startDate);
  const [draftEnd, setDraftEnd] = useState(endDate);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResults, setAiResults] = useState<Record<string, {
    conclusion: string; evidence: string[]; limitations: string[]; nextChecks: string[];
  }>>({});
  useEffect(() => {
    if (!data?.marketImpact.items.length) return;
    if (!data.marketImpact.items.some((item) => item.tsCode === selectedCode)) {
      setSelectedCode(data.marketImpact.items[0].tsCode);
    }
  }, [data, selectedCode]);
  useEffect(() => { setDraftStart(startDate); setDraftEnd(endDate); }, [startDate, endDate]);
  if (!data) return null;
  const selectedMarket = data.marketImpact.items.find((item) => item.tsCode === selectedCode)
    ?? data.marketImpact.items[0];
  const selectedAi = selectedMarket ? aiResults[selectedMarket.tsCode] : undefined;
  const layers = [
    { key: 'sources' as const, label: '来源', note: '知识星球' },
    { key: 'themes' as const, label: '主题', note: 'AI 提取' },
    { key: 'stocks' as const, label: '个股', note: '明确提及' },
    { key: 'outcomes' as const, label: '行情验证', note: '事件后5日' },
  ];
  const relationCount = (stage: string, key: string) => data.layers.edges
    .filter((edge) => (edge.fromStage === stage && edge.from === key) || (edge.toStage === stage && edge.to === key))
    .reduce((sum, edge) => sum + edge.count, 0);
  const heatScores = data.themeHeatmap.items
    .flatMap((item) => item.points.map((point) => point.concentrationScore ?? 0))
    .filter((score) => score > 0)
    .sort((left, right) => left - right);
  const funnelMax = Math.max(data.evidenceFunnel[0]?.count ?? 0, 1);
  const periodLabel = data.period.granularity === 'day' ? '日' : data.period.granularity === 'week' ? '周' : '月';
  const runAiInterpretation = async () => {
    if (!selectedMarket) return;
    setAiLoading(true);
    setAiError(null);
    try {
      const result = await essayRadarApi.interpretMarketImpact({
        tsCode: selectedMarket.tsCode,
        horizon,
        startDate: horizon === 'custom' ? data.period.startDate : undefined,
        endDate: horizon === 'custom' ? data.period.endDate : undefined,
      });
      setAiResults((current) => ({ ...current, [selectedMarket.tsCode]: result.interpretation }));
    } catch (caught) {
      setAiError(errorText(caught, 'AI 解读暂时不可用，统计结果仍可正常查看。'));
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="essay-view essay-atlas-view">
      <section className="essay-period-rail" aria-label="洞察研究时间窗口">
        <div><span>研究窗口</span><strong>{data.period.startDate} — {data.period.endDate}</strong><small>{data.windowDays} 个自然日 · 按{periodLabel}聚合</small></div>
        <nav>{([
          ['short', '短期', '14日'], ['medium', '中期', '90日'], ['long', '长期', '180日'],
        ] as const).map(([value, label, note]) => <button key={value} type="button" className={horizon === value ? 'is-active' : ''} onClick={() => onPeriodChange(value)}><strong>{label}</strong><small>{note}</small></button>)}</nav>
        <div className={`essay-custom-period ${horizon === 'custom' ? 'is-active' : ''}`}>
          <label>开始<input type="date" value={draftStart} onChange={(event) => setDraftStart(event.target.value)} /></label>
          <label>结束<input type="date" value={draftEnd} onChange={(event) => setDraftEnd(event.target.value)} /></label>
          <button type="button" disabled={!draftStart || !draftEnd || draftStart > draftEnd} onClick={() => onPeriodChange('custom', draftStart, draftEnd)}>应用</button>
        </div>
      </section>

      <section className="essay-atlas-summary" aria-label="所选窗口语料洞察摘要">
        <Metric label="已分析语料" value={data.summary.analyzedCount.toLocaleString()} note={`${data.windowDays} 日窗口`} tone="signal" />
        <Metric label="真实来源" value={data.summary.sourceCount} note="按知识星球去重" />
        <Metric label="主题 / 个股" value={`${data.summary.themeCount} / ${data.summary.stockCount}`} note="AI结构化实体" />
        <Metric label="行情覆盖" value={`${data.marketImpact.coverage.eventCoveragePercent}%`} note={`${data.marketImpact.coverage.coveredEventDayCount}/${data.marketImpact.coverage.eventDayCount} 个事件日`} />
        <Metric label="已验证股票" value={data.marketImpact.coverage.pricedStockCount} note={`候选 ${data.marketImpact.coverage.candidateStockCount} 只`} />
        <Metric label="多空分歧" value={data.summary.divergenceCount} note="同一标的双向观点" tone="danger" />
      </section>

      <div className="essay-atlas-primary">
        <section className="essay-panel essay-market-link-panel">
          <div className="essay-panel-head"><div><span>真实日线 × 小作文事件日</span><h2>个股行情与提及关联</h2></div><strong>{selectedMarket?.dataSource || '本地行情库'}</strong></div>
          <div className="essay-market-stock-tabs">{data.marketImpact.items.map((item) => <button type="button" key={item.tsCode} className={item.tsCode === selectedMarket?.tsCode ? 'is-active' : ''} onClick={() => { setSelectedCode(item.tsCode); setAiError(null); }}><strong>{item.name}</strong><small>{item.mentionCount}篇</small></button>)}</div>
          {selectedMarket ? <>
            <div className="essay-market-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 720, height: 300 }}><ComposedChart data={selectedMarket.series}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.14)" /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fontSize: 9 }} minTickGap={24} /><YAxis yAxisId="price" tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tick={{ fontSize: 9 }} width={42} /><YAxis yAxisId="mention" orientation="right" allowDecimals={false} tick={{ fontSize: 9 }} width={28} /><Tooltip contentStyle={{ background: '#0b0c0a', border: '1px solid rgba(198,255,74,.25)', fontSize: 10 }} formatter={(value, name) => [name === '阶段涨跌' ? `${Number(value).toFixed(2)}%` : `${value} 篇`, name]} labelFormatter={(value) => `交易日 ${value}`} /><Bar yAxisId="mention" dataKey="mentionCount" name="小作文" fill="#22d3ee" opacity={0.55} /><Line yAxisId="price" type="monotone" dataKey="priceReturn" name="阶段涨跌" stroke="#c6ff4a" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>
            <div className="essay-event-metrics">{selectedMarket.metrics.map((metric) => <div key={metric.period} aria-label={`95%置信区间：${metric.confidenceInterval95[0] == null ? '样本不足' : `${metric.confidenceInterval95[0]}% ~ ${metric.confidenceInterval95[1]}%`}`}><span>事件后 {metric.period} 日</span><strong className={(metric.averageReturn ?? 0) >= 0 ? 'is-up' : 'is-down'}>{metric.averageReturn == null ? '未成熟' : `${metric.averageReturn >= 0 ? '+' : ''}${metric.averageReturn.toFixed(2)}%`}</strong><small>胜率 {metric.winRate == null ? '—' : `${metric.winRate.toFixed(1)}%`} · 样本 {metric.sampleCount}</small><small>超额 {metric.averageExcessReturn == null ? '—' : `${metric.averageExcessReturn >= 0 ? '+' : ''}${metric.averageExcessReturn.toFixed(2)}%`}</small></div>)}</div>
            <p className="essay-method-note">{selectedMarket.insight} {data.marketImpact.causalityNote}</p>
          </> : <EmptyState title="当前窗口缺少行情匹配" description="需要小作文明确匹配股票代码，且本地行情库覆盖事件后的交易日。" icon={<BarChart3 className="h-7 w-7" />} />}
        </section>

        <section className="essay-panel essay-atlas-chain">
          <div className="essay-panel-head"><div><span>原文共现 + 真实行情结果</span><h2>来源 → 主题 → 个股 → 行情验证</h2></div><strong>{data.layers.edges.length} 条关系</strong></div>
          <div className="essay-chain-grid">
            {layers.map((layer, layerIndex) => {
              const nodes = data.layers[layer.key];
              const max = Math.max(...nodes.map((node) => node.count), 1);
              return <div className="essay-chain-stage" key={layer.key}>
                <header><span>0{layerIndex + 1}</span><div><strong>{layer.label}</strong><small>{layer.note}</small></div>{layerIndex < layers.length - 1 ? <ArrowRight /> : null}</header>
                <div>{nodes.map((node) => <button key={node.key} type="button" onClick={() => layer.key === 'outcomes' ? setSelectedCode(node.tsCode || '') : onFilter(node.label)} aria-label={layer.key === 'outcomes' ? `查看 ${node.stockName} 的行情验证` : `检索原文：${node.label}`}>
                  <span>{node.kind ? <i className={`is-${node.kind}`} /> : null}{layer.key === 'outcomes' ? `${node.stockName} · ${node.label}` : node.label}</span>
                  <b><i style={{ width: `${Math.max(5, node.count / max * 100)}%` }} /></b>
                  <small>{node.count} 篇 · {relationCount(layer.key, node.key)} 次关联</small>
                </button>)}</div>
              </div>;
            })}
          </div>
        </section>
      </div>

      <div className="essay-atlas-secondary">
        <section className="essay-panel essay-heatmap-panel">
          <div className="essay-panel-head"><div><span>按每{periodLabel}主题占比与阶段峰值计算 · 颜色越亮越集中</span><h2>主题热力迁移</h2></div><strong>{data.themeHeatmap.dates.length} 个{periodLabel}段</strong></div>
          <div className="essay-heatmap-meta">
            <span>{data.themeHeatmap.taxonomy ? `${data.themeHeatmap.taxonomy.rawThemeCount} 个原始标签 → ${data.themeHeatmap.taxonomy.canonicalThemeCount} 个规范主题` : '规范主题聚合'}</span>
            <div aria-label="主题集中度图例"><small>低</small>{[1, 2, 3, 4, 5].map((level) => <i key={level} className={`heat-level-${level}`} />)}<small>高</small></div>
          </div>
          <div className="essay-heatmap-axis" style={{ gridTemplateColumns: `110px repeat(${data.themeHeatmap.dates.length}, minmax(9px, 1fr))` }}><span />{data.themeHeatmap.dates.map((day, index) => <small key={day} className={index % Math.max(1, Math.ceil(data.themeHeatmap.dates.length / 5)) ? 'is-muted' : ''}>{day.slice(5)}</small>)}</div>
          <div className="essay-heatmap">{data.themeHeatmap.items.map((item) => {
            const aliases = item.aliases?.map((alias) => alias.name) ?? [];
            return <div key={item.name} style={{ gridTemplateColumns: `110px repeat(${data.themeHeatmap.dates.length}, minmax(9px, 1fr))` }}><button type="button" onClick={() => onFilter(item.name)} aria-label={aliases.length ? `已合并：${aliases.join('、')}` : item.name}><strong>{item.name}</strong><small>{item.total}</small></button>{item.points.map((point) => {
              const level = heatLevel(point.concentrationScore ?? 0, heatScores);
              return <i key={point.date} aria-label={`${point.date} · ${point.count} 篇 · 当日主题提及 ${point.dailyTotal ?? 0} 次 · 占比 ${(point.sharePercent ?? 0).toFixed(1)}% · 集中度 ${(point.concentrationScore ?? 0).toFixed(1)}`} className={`heat-level-${level}`} />;
            })}</div>;
          })}</div>
        </section>

        <section className="essay-panel essay-market-ranking-panel">
          <div className="essay-panel-head"><div><span>同股同日去重 · 按5日结果比较</span><h2>小作文后行情验证</h2></div><strong>仅展示成熟样本</strong></div>
          <div className="essay-market-ranking"><header><span>标的</span><span>提及</span><span>样本</span><span>5日均值</span><span>胜率</span><span>超额</span></header>{data.marketImpact.items.map((item) => {
            const metric = item.metrics.find((row) => row.period === 5) ?? item.metrics[0];
            return <button key={item.tsCode} type="button" className={item.tsCode === selectedMarket?.tsCode ? 'is-active' : ''} onClick={() => setSelectedCode(item.tsCode)}><span><strong>{item.name}</strong><small>{item.tsCode}</small></span><b>{item.mentionCount}</b><span>{metric.sampleCount}</span><strong className={(metric.averageReturn ?? 0) >= 0 ? 'is-up' : 'is-down'}>{metric.averageReturn == null ? '—' : `${metric.averageReturn >= 0 ? '+' : ''}${metric.averageReturn.toFixed(2)}%`}</strong><span>{metric.winRate == null ? '—' : `${metric.winRate.toFixed(1)}%`}</span><span>{metric.averageExcessReturn == null ? '—' : `${metric.averageExcessReturn >= 0 ? '+' : ''}${metric.averageExcessReturn.toFixed(2)}%`}</span></button>;
          })}</div>
        </section>
      </div>

      <div className="essay-atlas-tertiary">
        <section className="essay-panel essay-lead-lag-panel">
          <div className="essay-panel-head"><div><span>提及量领先未来收益 · 皮尔逊相关</span><h2>领先 / 滞后检验</h2></div><strong>{selectedMarket?.name || '—'}</strong></div>
          <div className="essay-lead-lag">{selectedMarket?.leadLag.map((row) => <div key={row.lagSessions}><span>{row.lagSessions === 0 ? '同日' : `领先 ${row.lagSessions} 日`}</span><i><b className={(row.correlation ?? 0) >= 0 ? 'is-positive' : 'is-negative'} style={{ width: `${Math.min(Math.abs(row.correlation ?? 0) * 100, 100)}%` }} /></i><strong>{row.correlation == null ? '样本不足' : row.correlation.toFixed(3)}</strong><small>n={row.sampleCount}</small></div>)}</div>
          <div className="essay-attention-compare">{selectedMarket?.attentionComparison.map((row) => <div key={row.level}><span>{row.level}</span><strong className={(row.averageReturn5D ?? 0) >= 0 ? 'is-up' : 'is-down'}>{row.averageReturn5D == null ? '未成熟' : `${row.averageReturn5D >= 0 ? '+' : ''}${row.averageReturn5D.toFixed(2)}%`}</strong><small>5日胜率 {row.winRate5D == null ? '—' : `${row.winRate5D.toFixed(1)}%`} · n={row.sampleCount}</small></div>)}</div>
          <p className="essay-method-note">相关系数只描述关注强度和后续收益是否同步变化，不能解释因果；绝对值越接近1，同向或反向关系越强。</p>
        </section>

        <section className="essay-panel essay-ai-market-panel">
          <div className="essay-panel-head"><div><span>模型只解释上方统计，不补造行情</span><h2>AI 统计解读</h2></div><button type="button" disabled={!selectedMarket || aiLoading} onClick={() => void runAiInterpretation()}><Brain className={`h-3.5 w-3.5 ${aiLoading ? 'animate-pulse' : ''}`} />{aiLoading ? '分析中' : '生成解读'}</button></div>
          {selectedAi ? <div className="essay-ai-market-result"><strong>{selectedAi.conclusion}</strong><h3>统计依据</h3>{selectedAi.evidence.map((value) => <p key={value}>{value}</p>)}<h3>限制</h3>{selectedAi.limitations.map((value) => <p key={value}>{value}</p>)}<h3>后续验证</h3>{selectedAi.nextChecks.map((value) => <p key={value}>{value}</p>)}</div> : <p className="essay-method-note">选择股票后按需调用 DeepSeek。模型收到的是收益、胜率、样本量、置信区间与相关系数，不接收未来数据，也不把相关关系写成因果。</p>}
          {aiError ? <p className="essay-inline-error">{aiError}</p> : null}
        </section>

        <section className="essay-panel essay-funnel-panel">
          <div className="essay-panel-head"><div><span>行情 {data.marketImpact.coverage.priceStart || '—'} — {data.marketImpact.coverage.priceEnd || '—'}</span><h2>数据可用性</h2></div><strong>{data.marketImpact.coverage.benchmarkAvailable ? '沪深300可用' : '无基准'}</strong></div>
          <div className="essay-funnel">{data.evidenceFunnel.map((item, index) => <div key={item.name}><span><b>0{index + 1}</b>{item.name}</span><strong>{item.count.toLocaleString()}</strong><i><b style={{ width: `${item.count / funnelMax * 100}%` }} /></i></div>)}</div>
          <p className="essay-method-note">{data.marketImpact.entryRule}；{data.marketImpact.exitRule}。{data.marketImpact.priceBasis}；{data.marketImpact.dedupeRule}。来源：{data.marketImpact.coverage.sources.join('、') || '本地行情库'}。</p>
        </section>
      </div>
    </div>
  );
}

function DailyReportArticle({ item }: { item: EssayDailyReport }) {
  const report = item.report;
  if (!report) return <p className="essay-error-inline">{item.errorMessage || '报告尚未完成。'}</p>;
  const implications = [
    { title: '盈利传导', values: report.earningsImplications ?? [] },
    { title: '估值影响', values: report.valuationImplications ?? [] },
  ];
  return <div className="essay-report-body">
    <section className="essay-report-lead">
      <div className="essay-report-kicker"><span>{report.marketRegime || '市场叙事待识别'}</span><small>{item.sourceCount} 篇语料 · {item.totalTokens.toLocaleString()} tokens</small></div>
      <h3>核心研判</h3>
      <p>{report.executiveSummary || '—'}</p>
    </section>
    {report.marketNarrative ? <section className="essay-report-narrative"><h3>主线结构与证据传导</h3><p>{report.marketNarrative}</p></section> : null}
    {report.keyThemes?.length ? <section><h3>主题拆解</h3><div className="essay-report-theme-grid">{report.keyThemes.map((theme) => <article key={theme.name}>
      <header><strong>{theme.name}</strong><Badge variant={sentimentVariant(theme.direction)}>{SENTIMENT_LABELS[theme.direction] || theme.direction}</Badge><small>{theme.count || 0} 次</small></header>
      <p>{theme.thesis}</p>
      {theme.evidence ? <dl><dt>支持证据</dt><dd>{theme.evidence}</dd></dl> : null}
      {theme.counterEvidence ? <dl className="is-risk"><dt>反面证据</dt><dd>{theme.counterEvidence}</dd></dl> : null}
    </article>)}</div></section> : null}
    {report.stockFocus?.length ? <section className="essay-report-stock-section">
      <div className="essay-report-section-title"><div><h3>重点研究候选</h3><p>来自当日明确提及与证据链，不构成买卖建议。</p></div><strong>{report.stockFocus.length} 只</strong></div>
      <div className="essay-report-stock-grid">{report.stockFocus.map((stock) => <article key={`${stock.tsCode}-${stock.name}`}>
        <header><div><h4>{stock.name || stock.tsCode}</h4><span>{stock.tsCode || '代码未核验'}</span></div><Badge variant={sentimentVariant(stock.stance)}>{SENTIMENT_LABELS[stock.stance] || stock.stance}</Badge></header>
        <div className="essay-report-stock-facts"><span>提及 {stock.mentionCount || 0}</span><span>置信 {stock.conviction || '未分级'}</span><span>{stock.timeHorizon || '周期未明确'}</span></div>
        {stock.whyNow ? <p className="is-why"><strong>为何关注</strong>{stock.whyNow}</p> : null}
        <p>{stock.thesis}</p>
        {stock.earningsPath ? <p><strong>盈利路径</strong>{stock.earningsPath}</p> : null}
        {stock.valuationView ? <p><strong>估值约束</strong>{stock.valuationView}</p> : null}
        <div className="essay-report-stock-evidence"><div><strong>催化 / 验证</strong>{[...(stock.catalysts ?? []), ...(stock.validationPoints ?? [])].map((value) => <span key={value}>{value}</span>)}</div><div className="is-risk"><strong>风险</strong>{stock.risks?.map((value) => <span key={value}>{value}</span>)}</div></div>
      </article>)}</div>
    </section> : null}
    <div className="essay-report-implications">{implications.map((group) => <section key={group.title}><h3>{group.title}</h3>{group.values.length ? group.values.map((value) => <p key={value}>{value}</p>) : <p>当日语料没有形成可验证结论。</p>}</section>)}</div>
    <div className="essay-report-implications"><section><h3>新增信号</h3>{report.novelSignals?.length ? report.novelSignals.map((value) => <p key={value}>{value}</p>) : <p>暂无高置信新增信号。</p>}</section><section className="is-risk"><h3>风险与分歧</h3>{[...(report.riskWatch ?? []), ...(report.divergences ?? [])].length ? [...(report.riskWatch ?? []), ...(report.divergences ?? [])].map((value) => <p key={value}>{value}</p>) : <p>暂无新增风险条目。</p>}</section></div>
    {report.nextDayWatchlist?.length ? <section className="essay-report-next"><h3>下一交易日验证清单</h3><ol>{report.nextDayWatchlist.map((value) => <li key={value}>{value}</li>)}</ol></section> : null}
    {report.dataQuality?.limitations?.length ? <footer><strong>数据边界</strong>{report.dataQuality.limitations.join('；')}</footer> : null}
  </div>;
}

function SignalRow({
  item, onOpen, selectable = false, selected = false, onToggle,
}: {
  item: EssayAnalysis;
  onOpen: (item: EssayAnalysis) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggle?: (topicId: string) => void;
}) {
  const analyzed = item.status === 'completed';
  const audioFiles = item.note.files.filter(file => file.assetKind === 'audio');
  const ordinaryFiles = item.note.files.filter(file => file.assetKind !== 'audio');
  const assetNames = [...audioFiles, ...ordinaryFiles].map(file => file.name).filter(Boolean).slice(0, 3);
  const row = (
    <button type="button" className="essay-signal-row" onClick={() => onOpen(item)}>
      <div className="essay-score"><strong>{analyzed ? (item.noveltyScore ?? 0) : '—'}</strong><span>{analyzed ? '增量' : item.status === 'media_only' ? '资料' : '待研判'}</span></div>
      <div className="min-w-0">
        <div className="essay-row-meta">
          <Badge variant={analyzed ? sentimentVariant(item.sentiment) : item.status === 'failed' ? 'danger' : 'default'}>
            {analyzed ? (SENTIMENT_LABELS[item.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[item.status] ?? item.status)}
          </Badge>
          {audioFiles.length ? <Badge variant="info"><Headphones className="h-3 w-3" />{audioFiles.length} 份录音</Badge> : null}
          {ordinaryFiles.length ? <Badge><Archive className="h-3 w-3" />{ordinaryFiles.length} 份文件</Badge> : null}
          <span>{analyzed ? (CATEGORY_LABELS[item.primaryCategory ?? ''] ?? item.primaryCategory ?? '未分类') : item.note.groupName}</span>
          <span>{formatTime(item.note.createdAt)}</span>
        </div>
        <h3>{item.note.title || '无标题纪要'}</h3>
        <p>{item.summary || (item.status === 'media_only' ? assetNames.join(' · ') : '') || item.errorMessage || item.note.content || '原文正文为空，点击查看图片或附件。'}</p>
      </div>
      <div className="essay-confidence"><strong>{analyzed ? `${Math.round((item.confidenceScore ?? 0) * 100)}%` : '原文'}</strong><span>{analyzed ? '置信' : '可检索'}</span></div>
    </button>
  );
  if (!selectable) return row;
  return (
    <div className={`essay-selectable-row ${selected ? 'is-selected' : ''}`}>
      <label className="essay-row-check">
        <input type="checkbox" checked={selected} onChange={() => onToggle?.(item.topicId)} aria-label={`选择小作文 ${item.note.title || item.topicId}`} />
      </label>
      {row}
    </div>
  );
}

function AudioFileRow({ item, selected, onToggle, onOpenTranscript }: { item: EssayAudioFile; selected: boolean; onToggle: () => void; onOpenTranscript: () => void }) {
  return (
    <article className={`essay-audio-row ${selected ? 'is-selected' : ''}`}>
      <label className="essay-row-check">
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label={`选择录音 ${item.name}`} />
      </label>
      <div className="essay-audio-icon"><Headphones className="h-5 w-5" /></div>
      <div className="essay-audio-main">
        <h3>{item.name}</h3>
        <p>{item.noteTitle && item.noteTitle !== item.name ? `所属帖子：${item.noteTitle}` : '知识星球录音源文件'}</p>
        <div><span>{item.groupName}</span><span>{item.authorName || '作者未标注'}</span><span>{formatTime(item.createdAt)}</span></div>
      </div>
      <div className="essay-audio-facts">
        <strong>{formatDuration(item.durationSeconds ?? undefined) || '时长未知'}</strong>
        <span>{formatAssetSize(item.size ?? undefined) || '大小未知'}</span>
        <span className={item.transcribed ? 'is-transcribed' : 'is-untranscribed'}>{item.transcribed ? `已转写${item.transcriptLineCount ? ` · ${item.transcriptLineCount} 行` : ''}` : '未转写'}</span>
      </div>
      <div className="essay-audio-row-actions">
        {item.transcribed && item.transcriptTaskId ? <button type="button" className="essay-audio-transcript-open" onClick={onOpenTranscript}><FileText className="h-4 w-4" />查看原文</button> : null}
        {item.downloadUrl ? <a className="essay-audio-download" href={item.downloadUrl} aria-label={`下载录音 ${item.name}`}><Download className="h-4 w-4" />下载录音</a> : <span className="essay-audio-unavailable">链接待恢复</span>}
      </div>
    </article>
  );
}

function AudioMemoDrawer({ task, initialTab, onClose, onDownload }: {
  task: EssayAudioAnalysisTask | null;
  initialTab: 'memo' | 'transcript';
  onClose: () => void;
  onDownload: (format: 'zip' | 'md' | 'docx' | 'json') => void;
}) {
  const result = task?.result;
  const [tab, setTab] = useState<'memo' | 'evidence' | 'transcript'>('memo');
  const [activeFileId, setActiveFileId] = useState('');
  const [transcript, setTranscript] = useState<EssayAudioTranscript | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const artifacts = useMemo(() => task?.transcriptArtifacts ?? [], [task?.transcriptArtifacts]);
  const selectedFileId = activeFileId || artifacts[0]?.fileId || '';
  const transcriptOnly = task?.generateMemo === false || result?.transcriptOnly === true;
  useEffect(() => {
    setTab(transcriptOnly || initialTab === 'transcript' ? 'transcript' : 'memo');
    setActiveFileId('');
    setTranscript(null);
    setTranscriptError(null);
  }, [initialTab, task?.taskId, transcriptOnly]);
  useEffect(() => {
    if (!task?.taskId || tab !== 'transcript') return;
    const fileId = selectedFileId;
    if (!fileId) return;
    let cancelled = false;
    const loadTranscript = async () => {
      setTranscriptLoading(true);
      setTranscriptError(null);
      try {
        const next = await essayRadarApi.audioAnalysisTranscript(task.taskId, fileId);
        if (!cancelled) setTranscript(next);
      } catch (caught) {
        if (!cancelled) setTranscriptError(errorText(caught, '逐字稿暂时无法读取。'));
      } finally {
        if (!cancelled) setTranscriptLoading(false);
      }
    };
    void loadTranscript();
    return () => { cancelled = true; };
  }, [selectedFileId, tab, task?.taskId]);
  return (
    <Drawer isOpen={Boolean(task && (result || artifacts.length))} onClose={onClose} title={result?.title || '录音逐字稿'} width="max-w-6xl">
      {task ? <article className="essay-audio-memo">
        <header><div><span>{transcriptOnly ? 'ASR TRANSCRIPT' : 'AI 录音纪要'}</span><h2>{result?.title || '录音逐字稿'}</h2></div><small>{formatTime(result?.generatedAt || task.updatedAt, true)} · {transcriptOnly ? '仅语音转写，未调用 AI 分析' : result?.model || '当前分析模型'}</small></header>
        <nav className={`essay-audio-workbench-tabs ${transcriptOnly ? 'is-transcript-only' : ''}`} aria-label="录音纪要工作台">
          {!transcriptOnly ? <button type="button" className={tab === 'memo' ? 'is-active' : ''} onClick={() => setTab('memo')}>研判结论</button> : null}
          {!transcriptOnly ? <button type="button" className={tab === 'evidence' ? 'is-active' : ''} onClick={() => setTab('evidence')}>证据链 <span>{result?.evidenceIndex?.length || 0}</span></button> : null}
          <button type="button" className={tab === 'transcript' ? 'is-active' : ''} onClick={() => setTab('transcript')}>带时间戳逐字稿 <span>{artifacts.length}</span></button>
        </nav>
        {tab === 'memo' && result ? <>
        <section className="essay-audio-memo-lead"><h3>核心摘要</h3><p>{result.executiveSummary || '摘要正在完善。'}</p></section>
        {result.keyConclusions?.length ? <section><h3>核心结论</h3><ol>{result.keyConclusions.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol></section> : null}
        {result.companyMentions?.length ? <section><h3>公司与标的</h3><div className="essay-audio-company-grid">{result.companyMentions.map((item, index) => <article key={`${item.name}-${index}`}><strong>{item.name || '未命名标的'}</strong><p>{item.view || '未形成明确方向'}</p><small>原音依据：{item.evidence || '待回听核验'}</small></article>)}</div></section> : null}
        {result.financialForecasts?.length ? <section><h3>业绩 / 市值 / 数字预测</h3><div className="essay-audio-forecast-list">{result.financialForecasts.map((item, index) => <div key={`${item.subject}-${index}`}><strong>{item.subject || '预测项'}</strong><span>{[item.period, item.metric, item.value].filter(Boolean).join(' · ')}</span><small>{item.evidence || '口径需回听核验'}</small></div>)}</div></section> : null}
        <div className="essay-audio-memo-columns">
          {result.industryChain?.length ? <section><h3>产业链脉络</h3><ul>{result.industryChain.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
          {result.risks?.length ? <section className="is-risk"><h3>风险与反例</h3><ul>{result.risks.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
          {result.followUps?.length ? <section><h3>后续跟踪</h3><ul>{result.followUps.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
          {result.disagreements?.length ? <section className="is-risk"><h3>观点分歧</h3><ul>{result.disagreements.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
        </div>
        {result.monitoringItems?.length ? <section><h3>可执行跟踪清单</h3><div className="essay-audio-monitor-grid">{result.monitoringItems.map((item, index) => <article key={`${item.item}-${index}`}><strong>{item.item || '跟踪项'}</strong><span>{[item.metric, item.timeWindow].filter(Boolean).join(' · ') || '口径待明确'}</span><p>触发：{item.trigger || '待定义'}</p><small>依据：{item.evidence || '待回听'}</small></article>)}</div></section> : null}
        {result.speakerViews?.length ? <section><h3>发言者观点</h3><div className="essay-audio-speaker-grid">{result.speakerViews.map((item, index) => <article key={`${item.speaker}-${index}`}><strong>{item.speaker || `发言者 ${index + 1}`}</strong><p>{item.summary || '未形成摘要'}</p><small>{item.keyPoints?.join(' / ')}</small></article>)}</div></section> : null}
        </> : null}
        {tab === 'evidence' && result ? <section className="essay-audio-evidence-workbench"><header><div><span>TRACEABLE CLAIMS</span><h3>结论—原音证据索引</h3></div><small>数字与专有名词仍需回听核验</small></header><div>{result.evidenceIndex?.length ? result.evidenceIndex.map((item, index) => <button type="button" key={`${item.claim}-${index}`} onClick={() => { const matched = artifacts.find((source) => source.filename === item.sourceFile); if (matched) setActiveFileId(matched.fileId); setTab('transcript'); }}><b>{String(index + 1).padStart(2, '0')}</b><div><strong>{item.claim || '未命名结论'}</strong><p>{item.sourceFile || '来源未标注'} · {item.timestamp || '时间未定位'} · {item.speaker || '说话人未标注'}</p></div><span>{item.category || '证据'}<small>{typeof item.confidence === 'number' ? `${Math.round(item.confidence * 100)}%` : '—'}</small></span></button>) : <p>当前模型未返回证据索引，可从逐字稿回听核验。</p>}</div></section> : null}
        {tab === 'transcript' ? <section className="essay-audio-transcript-workbench"><header><div>{artifacts.map((item) => <button type="button" className={selectedFileId === item.fileId ? 'is-active' : ''} key={item.fileId} onClick={() => setActiveFileId(item.fileId)}>{item.filename || item.fileId}<small>{item.lineCount || 0} 行</small></button>)}</div></header>{transcriptLoading ? <div className="essay-audio-transcript-state"><RefreshCw className="h-4 w-4 animate-spin" />正在读取带时间戳逐字稿…</div> : transcriptError ? <div className="essay-audio-transcript-state is-error">{transcriptError}</div> : <div className="essay-audio-transcript-lines">{transcript?.lines.map((line, index) => <article key={`${line.timestamp}-${index}`}><time>{line.timestamp || '--:--:--'}</time><strong>{line.speaker}</strong><p>{line.text}</p></article>)}</div>}</section> : null}
        <footer><p>转写质量：{result?.transcriptQuality || '关键数字和专有名词请回听原录音核验。'}</p><span>来源 {result?.sourceFiles?.length || artifacts.length} 个录音 · {transcriptOnly ? '未进行 AI 分析' : result?.indexed ? '已写入统一检索与情报库' : '报告不构成投资建议'}</span></footer>
        <div className="essay-audio-memo-actions">{!transcriptOnly ? <><button type="button" onClick={() => onDownload('docx')}><Download className="h-4 w-4" />Word</button><button type="button" onClick={() => onDownload('md')}><Download className="h-4 w-4" />Markdown</button><button type="button" onClick={() => onDownload('json')}><Download className="h-4 w-4" />结构化 JSON</button></> : null}<button type="button" className="is-primary" onClick={() => onDownload('zip')}><Archive className="h-4 w-4" />{transcriptOnly ? '下载逐字稿 ZIP' : '完整资料包'}</button></div>
      </article> : null}
    </Drawer>
  );
}

function EssayDetail({ selected, onClose }: { selected: EssayAnalysis | null; onClose: () => void }) {
  const images = selected?.note.images ?? [];
  const files = selected?.note.files ?? [];
  const audioFiles = files.filter(file => file.assetKind === 'audio');
  const ordinaryFiles = files.filter(file => file.assetKind !== 'audio');
  const analyzed = selected?.status === 'completed';
  return (
    <Drawer isOpen={Boolean(selected)} onClose={onClose} title={selected?.note.title || '纪要详情'}>
      {selected ? (
        <div className="essay-detail">
          <div className="essay-detail-meta">
            <Badge variant={analyzed ? sentimentVariant(selected.sentiment) : selected.status === 'failed' ? 'danger' : 'default'}>{analyzed ? (SENTIMENT_LABELS[selected.sentiment ?? ''] ?? '待判断') : (ANALYSIS_STATUS_LABELS[selected.status] ?? selected.status)}</Badge>
            <Badge>{analyzed ? (CATEGORY_LABELS[selected.primaryCategory ?? ''] ?? selected.primaryCategory ?? '未分类') : selected.status === 'media_only' ? '源文件' : '未分类'}</Badge>
            <span>{selected.note.groupName}</span>
            <span>{selected.note.authorName || '作者未标注'}</span>
            <span>{formatTime(selected.note.createdAt, true)}</span>
          </div>

          <section className="essay-original">
            <div className="essay-section-title"><FileText className="h-4 w-4" />原文</div>
            <p>{selected.note.content || '原文正文为空；如有图片或附件，请通过下方远端链接查看。'}</p>
          </section>

          {audioFiles.length ? <section className="essay-detail-section">
            <div className="essay-section-title"><Headphones className="h-4 w-4" />录音源文件</div>
            <p className="essay-help">录音默认只索引文件名和元数据；在“检索与获取 → 录音文件”勾选后，可按需提交后台转写并生成 AI 录音纪要。同步时不会预下载源音频。</p>
            <div className="essay-assets">{audioFiles.map((item, index) => (item.downloadUrl || item.viewUrl) ? <a key={item.fileId || index} href={item.downloadUrl || item.viewUrl}>
              <Headphones className="h-4 w-4" /><span>{item.name || `录音 ${index + 1}`}{formatDuration(item.durationSeconds ?? item.duration) ? ` · ${formatDuration(item.durationSeconds ?? item.duration)}` : ''}{formatAssetSize(item.size) ? ` · ${formatAssetSize(item.size)}` : ''}</span><Download className="h-3.5 w-3.5" />
            </a> : null)}</div>
          </section> : null}

          {images.length || ordinaryFiles.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><Archive className="h-4 w-4" />图片与普通文件</div>
              <p className="essay-help">同步时不下载到本机，仅在点击时打开知识星球远端地址。</p>
              <div className="essay-assets">
                {images.map((item, index) => item.viewUrl ? (
                  <a key={item.imageId || index} href={item.viewUrl} target="_blank" rel="noreferrer">
                    <Image className="h-4 w-4" />查看图片 {index + 1}<ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
                {ordinaryFiles.map((item, index) => (item.downloadUrl || item.viewUrl) ? (
                  <a key={item.fileId || index} href={item.downloadUrl || item.viewUrl}>
                    <FileText className="h-4 w-4" />{item.name || `附件 ${index + 1}`}<Download className="h-3.5 w-3.5" />
                  </a>
                ) : null)}
              </div>
            </section>
          ) : null}

          <section className="essay-detail-section">
            <div className="essay-section-title"><Brain className="h-4 w-4" />AI 研判</div>
            {selected.status === 'media_only' ? <p className="essay-summary">这是录音资料：系统不会自动分析；需要时可在录音检索页勾选源文件，后台转写并生成可下载的录音小作文。</p> : analyzed ? <>
              <div className="essay-detail-scoreline">
                <span>重要度 <strong>{selected.importanceScore ?? '—'}</strong></span>
                <span>信息增量 <strong>{selected.noveltyScore ?? '—'}</strong></span>
                <span>信源质量 <strong>{selected.sourceQuality || '未判断'}</strong></span>
              </div>
              <p className="essay-summary">{selected.summary || '尚未形成有效摘要。'}</p>
            </> : <p className="essay-summary">本条已进入本地小作文库，当前状态：{ANALYSIS_STATUS_LABELS[selected.status] ?? selected.status}。原文仍可正常检索与查看。</p>}
          </section>

          {selected.evidence?.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><ShieldAlert className="h-4 w-4" />观点与原文依据</div>
              <div className="essay-evidence-list">{selected.evidence.map((item) => (
                <div key={`${item.claim}-${item.evidence}`}>
                  <strong>{item.claim}</strong><Badge>{item.strength}</Badge>
                  <p>{item.evidence}</p>
                </div>
              ))}</div>
            </section>
          ) : null}

          {selected.status !== 'media_only' ? <div className="essay-detail-grid">
            <section><h4>催化剂</h4>{selected.catalysts.length ? selected.catalysts.map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
            <section className="is-risk"><h4>风险与证伪</h4>{[...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].length ? [...selected.risks, ...selected.contradictions, ...selected.falsificationConditions].map((item) => <p key={item}>{item}</p>) : <p>未识别</p>}</section>
          </div> : null}

          {selected.stockMentions.length ? (
            <section className="essay-detail-section">
              <div className="essay-section-title"><BarChart3 className="h-4 w-4" />提及标的</div>
              <div className="essay-stock-mentions">{selected.stockMentions.map((stock) => (
                <div key={`${stock.tsCode}-${stock.name}`}><strong>{stock.name || stock.tsCode}</strong><span>{stock.tsCode}</span><p>{stock.rationale}</p></div>
              ))}</div>
            </section>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

const EssayRadarPage = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const view = radarView(location.pathname);
  const [dashboard, setDashboard] = useState<EssayDashboard | null>(null);
  const [deepInsights, setDeepInsights] = useState<EssayDeepInsights | null>(null);
  const [insights, setInsights] = useState<EssayInsights | null>(null);
  const [status, setStatus] = useState<EssayStatus | null>(null);
  const [list, setList] = useState<EssayAnalysisList | null>(null);
  const [audioList, setAudioList] = useState<EssayAudioFileList | null>(null);
  const [reports, setReports] = useState<EssayDailyReportList | null>(null);
  const [cloud, setCloud] = useState<EssayWordCloud | null>(null);
  const [historicalBacklog, setHistoricalBacklog] = useState<EssayHistoricalBacklog | null>(null);
  const [libraryStatsLoading, setLibraryStatsLoading] = useState(false);
  const [selected, setSelected] = useState<EssayAnalysis | null>(null);
  const [feedMode, setFeedMode] = useState<'essays' | 'audio'>('essays');
  const [selectedEssays, setSelectedEssays] = useState<Set<string>>(() => new Set());
  const [selectedAudio, setSelectedAudio] = useState<Map<string, EssayAudioFile>>(() => new Map());
  const [query, setQuery] = useState(() => searchParams.get('query') || '');
  const [queryScope, setQueryScope] = useState<'title' | 'full'>('full');
  const deferredQuery = useDebouncedValue(query, 350);
  const [sentiment, setSentiment] = useState('');
  const [category, setCategory] = useState('');
  const [analysisStatus, setAnalysisStatus] = useState('');
  const [minImportance, setMinImportance] = useState(0);
  const [days, setDays] = useState(0);
  const [page, setPage] = useState(1);
  const [cloudPeriod, setCloudPeriod] = useState<'day' | 'week' | 'month'>('day');
  const [cloudKind, setCloudKind] = useState<'stocks' | 'tags' | 'themes'>('stocks');
  const [atlasHorizon, setAtlasHorizon] = useState<AtlasHorizon>('short');
  const [atlasStartDate, setAtlasStartDate] = useState('');
  const [atlasEndDate, setAtlasEndDate] = useState('');
  const [analysisBatchCount, setAnalysisBatchCount] = useState(100);
  const [analysisOrder, setAnalysisOrder] = useState<'newest' | 'oldest'>('newest');
  const [queueMessage, setQueueMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [atlasLoading, setAtlasLoading] = useState(false);
  const [atlasNotice, setAtlasNotice] = useState<string | null>(null);
  const [trendModuleState, setTrendModuleState] = useState({ trend: 'idle', cloud: 'idle', stocks: 'idle' } as Record<'trend' | 'cloud' | 'stocks', 'idle' | 'loading' | 'ready' | 'error'>);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedNotice, setFeedNotice] = useState<string | null>(null);
  const [exportingFeed, setExportingFeed] = useState(false);
  const [exportNotice, setExportNotice] = useState<string | null>(null);
  const [batchActionLoading, setBatchActionLoading] = useState(false);
  const [audioBatchTask, setAudioBatchTask] = useState<EssayAudioBatchTask | null>(null);
  const [activeAudioBatchTaskId, setActiveAudioBatchTaskId] = useState(
    () => window.localStorage.getItem(AUDIO_BATCH_TASK_STORAGE_KEY) ?? '',
  );
  const [audioDownloadProgress, setAudioDownloadProgress] = useState<EssayAudioDownloadProgress | null>(null);
  const [audioDownloadLoading, setAudioDownloadLoading] = useState(false);
  const [audioAnalysisCapability, setAudioAnalysisCapability] = useState<EssayAudioAnalysisCapability | null>(null);
  const [audioAnalysisTask, setAudioAnalysisTask] = useState<EssayAudioAnalysisTask | null>(null);
  const [audioAnalysisTasks, setAudioAnalysisTasks] = useState<EssayAudioAnalysisTask[]>([]);
  const [audioAnalysisLoading, setAudioAnalysisLoading] = useState(false);
  const [audioMemoTitle, setAudioMemoTitle] = useState('');
  const [audioMemoFocus, setAudioMemoFocus] = useState('');
  const [audioMemoHotwords, setAudioMemoHotwords] = useState('');
  const [audioMemoSpeakerCount, setAudioMemoSpeakerCount] = useState('');
  const [audioMemoOpen, setAudioMemoOpen] = useState(false);
  const [audioMemoInitialTab, setAudioMemoInitialTab] = useState<'memo' | 'transcript'>('memo');
  const [activeAudioAnalysisTaskId, setActiveAudioAnalysisTaskId] = useState(
    () => window.localStorage.getItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY) ?? '',
  );
  const [refreshKey, setRefreshKey] = useState(0);
  const [lastAutoRefreshAt, setLastAutoRefreshAt] = useState<string | null>(null);
  const feedPageCacheRef = useRef(new Map<string, EssayAnalysisList>());
  const audioListRef = useRef<EssayAudioFileList | null>(null);
  const feedInflightRef = useRef(new Map<string, Promise<EssayAnalysisList>>());
  const feedRequestVersionRef = useRef(0);
  const feedFilterSignatureRef = useRef('');
  const loadedViewsRef = useRef(new Set<RadarView>());
  const atlasRequestVersionRef = useRef(0);

  useEffect(() => {
    const viewLabel = VIEW_META.find((item) => item.view === view)?.label ?? '机构段子与录音';
    document.title = `${viewLabel} · 机构段子与录音 - 乐子乌超级价值`;
  }, [view]);

  useEffect(() => {
    if (!activeAudioBatchTaskId) return undefined;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      if (document.visibilityState === 'hidden') {
        timer = window.setTimeout(() => void poll(), 5000);
        return;
      }
      try {
        const next = await essayRadarApi.audioBatchTask(activeAudioBatchTaskId);
        if (cancelled) return;
        setAudioBatchTask(next);
        if (next.status === 'queued' || next.status === 'running') {
          timer = window.setTimeout(() => void poll(), 1000);
        }
      } catch {
        if (cancelled) return;
        window.localStorage.removeItem(AUDIO_BATCH_TASK_STORAGE_KEY);
        setActiveAudioBatchTaskId('');
        setAudioBatchTask(null);
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [activeAudioBatchTaskId]);

  useEffect(() => {
    if (view !== 'feed' || feedMode !== 'audio') return;
    void essayRadarApi.audioAnalysisCapability().then(setAudioAnalysisCapability).catch(() => undefined);
  }, [feedMode, view]);

  useEffect(() => {
    if (view !== 'feed' || feedMode !== 'audio') return undefined;
    let cancelled = false;
    const loadTasks = async () => {
      if (document.visibilityState === 'hidden') return;
      try {
        const next = await essayRadarApi.audioAnalysisTasks();
        if (!cancelled) setAudioAnalysisTasks(next.items);
      } catch { /* Task history is auxiliary; keep the current report visible. */ }
    };
    void loadTasks();
    const timer = window.setInterval(() => void loadTasks(), 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [feedMode, view]);

  useEffect(() => {
    if (!activeAudioAnalysisTaskId) return undefined;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      if (document.visibilityState === 'hidden') {
        timer = window.setTimeout(() => void poll(), 5000);
        return;
      }
      try {
        const next = await essayRadarApi.audioAnalysisTask(activeAudioAnalysisTaskId);
        if (cancelled) return;
        setAudioAnalysisTask(next);
        if (next.status === 'queued' || next.status === 'running') timer = window.setTimeout(() => void poll(), 1500);
      } catch {
        if (cancelled) return;
        window.localStorage.removeItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY);
        setActiveAudioAnalysisTaskId('');
      }
    };
    void poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [activeAudioAnalysisTaskId]);

  const loadView = useCallback(async (_requestVersion: number) => {
    void _requestVersion;
    if (view === 'feed') return;
    const initialLoad = !loadedViewsRef.current.has(view);
    if (initialLoad) {
      setLoading(true);
      setError(null);
    }
    let loaded = false;
    let partialError: string | null = null;
    try {
      if (view === 'overview') {
        const [nextInsights, nextStatus] = await Promise.allSettled([
          essayRadarApi.insights(30, 14), essayRadarApi.status(30),
        ]);
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextStatus.status === 'fulfilled') { setStatus(nextStatus.value); loaded = true; }
        if (nextInsights.status === 'rejected' && nextStatus.status === 'rejected') throw nextInsights.reason;
        if (nextInsights.status === 'rejected' || nextStatus.status === 'rejected') {
          partialError = '部分模块暂时不可用，已保留成功加载的真实数据。';
        }
      } else if (view === 'atlas') {
        const atlasVersion = ++atlasRequestVersionRef.current;
        setAtlasLoading(true);
        setAtlasNotice(null);
        const nextAtlas = await essayRadarApi.deepInsights({
            horizon: atlasHorizon,
            startDate: atlasHorizon === 'custom' ? atlasStartDate : undefined,
            endDate: atlasHorizon === 'custom' ? atlasEndDate : undefined,
          });
        if (atlasVersion !== atlasRequestVersionRef.current) return;
        setDeepInsights(nextAtlas);
        loaded = true;
      } else if (view === 'trends') {
        setTrendModuleState({ trend: 'loading', cloud: 'loading', stocks: 'loading' });
        const [nextDashboard, nextInsights, nextCloud] = await Promise.allSettled([
          essayRadarApi.dashboard(30), essayRadarApi.insights(30, 14), essayRadarApi.wordCloud(cloudPeriod, cloudKind),
        ]);
        if (nextDashboard.status === 'fulfilled') { setDashboard(nextDashboard.value); loaded = true; }
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextCloud.status === 'fulfilled') { setCloud(nextCloud.value); loaded = true; }
        setTrendModuleState({
          trend: nextInsights.status === 'fulfilled' ? 'ready' : 'error',
          cloud: nextCloud.status === 'fulfilled' ? 'ready' : 'error',
          stocks: nextDashboard.status === 'fulfilled' ? 'ready' : 'error',
        });
        const failures = [nextDashboard, nextInsights, nextCloud].filter(item => item.status === 'rejected');
        if (failures.length === 3) throw (failures[0] as PromiseRejectedResult).reason;
        if (failures.length) partialError = `部分模块暂时不可用（${failures.length}/3），其余数据仍可查看。`;
      } else if (view === 'reports') {
        const [nextReports, nextInsights] = await Promise.allSettled([
          essayRadarApi.dailyReports(), essayRadarApi.insights(30, 14),
        ]);
        if (nextReports.status === 'fulfilled') { setReports(nextReports.value); loaded = true; }
        if (nextInsights.status === 'fulfilled') { setInsights(nextInsights.value); loaded = true; }
        if (nextReports.status === 'rejected' && nextInsights.status === 'rejected') throw nextReports.reason;
        if (nextReports.status === 'rejected' || nextInsights.status === 'rejected') {
          partialError = '日报或研判摘要暂时不可用，已保留成功加载的模块。';
        }
      } else {
        const [nextStatus, nextBacklog] = await Promise.all([
          essayRadarApi.status(30), essayRadarApi.historicalBacklog(),
        ]);
        setStatus(nextStatus);
        setHistoricalBacklog(nextBacklog);
        loaded = true;
      }
      if (loaded) {
        loadedViewsRef.current.add(view);
        setLastAutoRefreshAt(new Date().toISOString());
        if (!partialError) setError(null);
      }
      if (partialError && initialLoad) setError(partialError);
    } catch (caught) {
      if (view === 'atlas') setAtlasNotice('长窗口仍在后台准备；已保留上一次结果，页面会自动重试。');
      if (initialLoad) setError(errorText(caught, '页面数据加载失败'));
    } finally {
      if (view === 'atlas') setAtlasLoading(false);
      if (initialLoad) setLoading(false);
    }
  }, [atlasEndDate, atlasHorizon, atlasStartDate, cloudKind, cloudPeriod, view]);

  const loadFeed = useCallback(async (refreshVersion: number) => {
    if (view !== 'feed') return;
    if (feedMode === 'audio') {
      const backgroundRefresh = Boolean(audioListRef.current);
      if (!backgroundRefresh) setLoading(true);
      try {
        const result = await essayRadarApi.audioFiles({
          days, query: deferredQuery, page, pageSize: 20,
        });
        audioListRef.current = result;
        setAudioList(result);
        loadedViewsRef.current.add('feed');
        setLastAutoRefreshAt(new Date().toISOString());
        setFeedNotice(null);
      } catch {
        setFeedNotice(backgroundRefresh
          ? '录音文件索引暂时没有响应，当前保留上一次结果并自动重试。'
          : '录音文件索引正在加载，请稍等，页面会自动继续。');
      } finally {
        if (!backgroundRefresh) setLoading(false);
      }
      return;
    }
    const baseFilters = {
      days, query: deferredQuery, queryScope, analysisStatus: analysisStatus || 'essay', sentiment, category, minImportance, pageSize: 20,
    };
    const filterSignature = `${JSON.stringify(baseFilters)}:page:${page}`;
    const backgroundRefresh = Boolean(list) && feedFilterSignatureRef.current === filterSignature;
    feedFilterSignatureRef.current = filterSignature;
    const queryKey = `${refreshVersion}:${JSON.stringify(baseFilters)}`;
    const pageKey = (targetPage: number) => `${queryKey}:page:${targetPage}`;
    const fetchPage = (targetPage: number, knownTotal?: number) => {
      const key = pageKey(targetPage);
      const cached = feedPageCacheRef.current.get(key);
      if (cached) return Promise.resolve(cached);
      const inflight = feedInflightRef.current.get(key);
      if (inflight) return inflight;
      const request = essayRadarApi.list({
        ...baseFilters,
        page: targetPage,
        knownTotal,
      }).then((result) => {
        if (feedPageCacheRef.current.size >= FEED_PAGE_CACHE_LIMIT) {
          const oldestKey = feedPageCacheRef.current.keys().next().value;
          if (oldestKey) feedPageCacheRef.current.delete(oldestKey);
        }
        feedPageCacheRef.current.set(key, result);
        return result;
      }).finally(() => {
        feedInflightRef.current.delete(key);
      });
      feedInflightRef.current.set(key, request);
      return request;
    };
    const prefetchNext = (result: EssayAnalysisList) => {
      const lastPage = Math.max(1, Math.ceil(result.total / 20));
      if (page < lastPage) void fetchPage(page + 1, result.total).catch(() => undefined);
    };

    const requestVersion = ++feedRequestVersionRef.current;
    if (!backgroundRefresh) setFeedNotice(null);
    const cached = feedPageCacheRef.current.get(pageKey(page));
    if (cached) {
      setList(cached);
      setLoading(false);
      prefetchNext(cached);
      return;
    }
    if (!backgroundRefresh) setLoading(true);
    try {
      const result = await fetchPage(page, feedPageCacheRef.current.get(pageKey(1))?.total);
      if (requestVersion !== feedRequestVersionRef.current) return;
      setList(result);
      loadedViewsRef.current.add('feed');
      setLastAutoRefreshAt(new Date().toISOString());
      setFeedNotice(null);
      prefetchNext(result);
    } catch {
      if (requestVersion !== feedRequestVersionRef.current) return;
      if (!backgroundRefresh) {
        setFeedNotice(list
          ? '检索服务刚才没有及时响应，当前仍保留上一次结果，并会自动重试。'
          : '本地知识库正在准备索引，请稍等片刻，页面会自动继续加载。');
      }
    } finally {
      if (!backgroundRefresh && requestVersion === feedRequestVersionRef.current) setLoading(false);
    }
  }, [analysisStatus, category, days, deferredQuery, feedMode, list, minImportance, page, queryScope, sentiment, view]);

  useEffect(() => { void loadView(refreshKey); }, [loadView, refreshKey]);
  useEffect(() => { void loadFeed(refreshKey); }, [loadFeed, refreshKey]);
  useEffect(() => {
    const requestAutomaticRefresh = () => {
      if (document.visibilityState === 'visible') setRefreshKey((value) => value + 1);
    };
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') requestAutomaticRefresh();
    };
    // The feed is incremental and cheap; atlas/trend/report views aggregate
    // thousands of records and should reuse their prepared result instead of
    // forcing a full recomputation every 15 seconds. Source ingestion remains
    // realtime in the backend worker.
    const refreshInterval = view === 'feed' ? 15_000 : view === 'overview' ? 30_000 : 60_000;
    const timer = window.setInterval(requestAutomaticRefresh, refreshInterval);
    window.addEventListener('focus', requestAutomaticRefresh);
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', requestAutomaticRefresh);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [view]);
  useEffect(() => {
    let active = true;
    const loadStatus = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const nextStatus = await essayRadarApi.status(30);
        if (active) setStatus(nextStatus);
      } catch { /* Automatic status polling keeps the last factual state. */ }
    };
    void loadStatus();
    const timer = window.setInterval(() => void loadStatus(), 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (view !== 'overview') return undefined;
    const timer = window.setTimeout(() => {
      void essayRadarApi.deepInsights({ horizon: 'short' }).then(setDeepInsights).catch(() => undefined);
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [view]);
  useEffect(() => {
    if (view !== 'feed') return undefined;
    let active = true;
    const loadBacklog = () => {
      if (document.visibilityState === 'hidden') return;
      const initialLoad = !loadedViewsRef.current.has('feed');
      if (initialLoad) setLibraryStatsLoading(true);
      void essayRadarApi.historicalBacklog()
        .then((result) => { if (active) setHistoricalBacklog(result); })
        .catch(() => { /* Keep search usable while knowledge-base statistics warm up. */ })
        .finally(() => { if (active && initialLoad) setLibraryStatsLoading(false); });
    };
    loadBacklog();
    const timer = window.setInterval(loadBacklog, 60_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [view]);

  const workerActive = Boolean(status?.worker.running && status?.mcpSync.running);

  const queueHistoricalAnalysis = async () => {
    const available = historicalBacklog?.unqueued ?? 0;
    const selected = Math.min(analysisBatchCount, available);
    if (!selected) return;
    const direction = analysisOrder === 'newest' ? '最近' : '最早';
    if (!window.confirm(`将本地库中${direction}的 ${selected} 篇未入队小作文交给 AI 分析，可能消耗模型额度。确认继续？`)) return;
    setActionLoading(true); setError(null); setQueueMessage(null);
    try {
      const result = await essayRadarApi.backfillCount(analysisBatchCount, analysisOrder);
      setHistoricalBacklog(result.backlog);
      setQueueMessage(`已加入 ${result.queue.selected.toLocaleString()} 篇，后台正在分析；未入队还剩 ${result.backlog.unqueued.toLocaleString()} 篇。`);
      setRefreshKey((value) => value + 1);
    } catch (caught) {
      const message = errorText(caught, '历史小作文加入分析队列失败');
      setError(message.includes('连接上游服务超时')
        ? '补分析入队请求等待超时。该步骤只写入本地 AI 队列，不代表 DeepSeek 不可用；请刷新本页确认队列数量后再决定是否重试。'
        : message);
    } finally {
      setActionLoading(false);
    }
  };

  const exportFeed = async () => {
    if (exportingFeed || query !== deferredQuery || !(list?.total ?? 0)) return;
    setExportingFeed(true);
    setExportNotice(null);
    try {
      const blob = await essayRadarApi.exportFeed({
        days,
        query: deferredQuery,
        queryScope,
        analysisStatus,
        sentiment,
        category,
        minImportance,
      });
      const timestamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
      downloadBlob(blob, `小作文检索结果_${timestamp}.xlsx`);
      setExportNotice(`已导出当前条件命中的 ${(list?.total ?? 0).toLocaleString()} 篇小作文。`);
    } catch (caught) {
      setExportNotice(errorText(caught, 'Excel 导出失败，请稍后重试。'));
    } finally {
      setExportingFeed(false);
    }
  };

  const toggleEssaySelection = (topicId: string) => {
    setSelectedEssays((current) => {
      const next = new Set(current);
      if (next.has(topicId)) next.delete(topicId); else next.add(topicId);
      return next;
    });
  };

  const toggleAudioSelection = (item: EssayAudioFile) => {
    setSelectedAudio((current) => {
      const next = new Map(current);
      if (next.has(item.assetId)) next.delete(item.assetId); else next.set(item.assetId, item);
      return next;
    });
  };

  const toggleCurrentPageSelection = () => {
    if (feedMode === 'audio') {
      const items = audioList?.items ?? [];
      const allSelected = items.length > 0 && items.every((item) => selectedAudio.has(item.assetId));
      setSelectedAudio((current) => {
        const next = new Map(current);
        items.forEach((item) => { if (allSelected) next.delete(item.assetId); else next.set(item.assetId, item); });
        return next;
      });
      return;
    }
    const items = list?.items ?? [];
    const allSelected = items.length > 0 && items.every((item) => selectedEssays.has(item.topicId));
    setSelectedEssays((current) => {
      const next = new Set(current);
      items.forEach((item) => { if (allSelected) next.delete(item.topicId); else next.add(item.topicId); });
      return next;
    });
  };

  const downloadSelected = async () => {
    const selectedCount = feedMode === 'audio' ? selectedAudio.size : selectedEssays.size;
    if (!selectedCount || batchActionLoading) return;
    setBatchActionLoading(true);
    setExportNotice(null);
    try {
      if (feedMode === 'audio') {
        const task = await essayRadarApi.startAudioBatchTask([...selectedAudio.values()].map((item) => ({
          topicId: item.topicId, fileId: item.fileId,
        })));
        setAudioBatchTask(task);
        setActiveAudioBatchTaskId(task.taskId);
        setAudioDownloadProgress(null);
        window.localStorage.setItem(AUDIO_BATCH_TASK_STORAGE_KEY, task.taskId);
        setExportNotice(`已提交 ${selectedCount} 个录音到后台打包，可以离开本页；返回后会继续显示进度。`);
      } else {
        const blob = await essayRadarApi.exportSelected([...selectedEssays]);
        downloadBlob(blob, `小作文已选原文_${selectedCount}篇_${new Date().toISOString().slice(0, 10)}.xlsx`);
        setExportNotice(`已下载 ${selectedCount} 篇小作文的完整原文和分析标签。`);
      }
    } catch (caught) {
      setExportNotice(errorText(caught, feedMode === 'audio' ? '录音批量下载失败，请稍后重试。' : '小作文批量下载失败，请稍后重试。'));
    } finally {
      setBatchActionLoading(false);
    }
  };

  const downloadCompletedAudioBatch = async () => {
    if (!audioBatchTask || audioBatchTask.status !== 'completed' || audioDownloadLoading) return;
    setAudioDownloadLoading(true);
    setAudioDownloadProgress({ loaded: 0 });
    setExportNotice(null);
    try {
      const blob = await essayRadarApi.downloadAudioBatchTask(audioBatchTask.taskId, setAudioDownloadProgress);
      setAudioDownloadProgress({ loaded: blob.size, total: blob.size, percent: 100 });
      downloadBlob(blob, audioBatchTask.downloadName || `知识星球录音_${audioBatchTask.taskId}.zip`);
      setExportNotice(`ZIP 下载完成，共 ${audioBatchTask.totalFiles} 个录音源文件。`);
    } catch (caught) {
      setExportNotice(errorText(caught, '录音 ZIP 下载失败，后台压缩包仍会保留 48 小时，可以稍后重试。'));
    } finally {
      setAudioDownloadLoading(false);
    }
  };

  const submitSelectedAudio = async (generateMemo: boolean) => {
    if (!selectedAudio.size || audioAnalysisLoading) return;
    setAudioAnalysisLoading(true);
    setExportNotice(null);
    try {
      const task = await essayRadarApi.startAudioAnalysisTask([...selectedAudio.values()].map((item) => ({
        topicId: item.topicId, fileId: item.fileId,
      })), {
        title: audioMemoTitle.trim() || undefined,
        focus: audioMemoFocus.trim() || undefined,
        hotwords: audioMemoHotwords.split(/[，,、\n]/).map((item) => item.trim()).filter(Boolean),
        speakerCount: audioMemoSpeakerCount ? Number(audioMemoSpeakerCount) : undefined,
        generateMemo,
      });
      setAudioAnalysisTask(task);
      setAudioAnalysisTasks((current) => [task, ...current.filter((item) => item.taskId !== task.taskId)]);
      setActiveAudioAnalysisTaskId(task.taskId);
      window.localStorage.setItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY, task.taskId);
      setExportNotice(generateMemo
        ? `已提交 ${selectedAudio.size} 个录音进行后台转写与 AI 纪要，离开页面后任务会继续。`
        : `已提交 ${selectedAudio.size} 个录音进行后台转写，不会调用 AI 分析；完成后可直接查看逐字稿。`);
    } catch (caught) {
      setExportNotice(errorText(caught, generateMemo ? '录音 AI 纪要任务提交失败，请检查上游服务配置。' : '录音转写任务提交失败，请检查语音转写服务配置。'));
    } finally {
      setAudioAnalysisLoading(false);
    }
  };

  const openAudioAnalysisTask = async (taskId: string) => {
    try {
      const task = await essayRadarApi.audioAnalysisTask(taskId);
      setAudioAnalysisTask(task);
      setActiveAudioAnalysisTaskId(task.taskId);
      window.localStorage.setItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY, task.taskId);
      if (task.status === 'completed') {
        setAudioMemoInitialTab(task.generateMemo === false || task.result?.transcriptOnly ? 'transcript' : 'memo');
        setAudioMemoOpen(true);
      }
    } catch (caught) {
      setExportNotice(errorText(caught, '任务详情暂时无法读取。'));
    }
  };

  const openAudioTranscript = async (item: EssayAudioFile) => {
    if (!item.transcriptTaskId) return;
    try {
      const task = await essayRadarApi.audioAnalysisTask(item.transcriptTaskId);
      setAudioAnalysisTask(task);
      setActiveAudioAnalysisTaskId(task.taskId);
      window.localStorage.setItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY, task.taskId);
      setAudioMemoInitialTab('transcript');
      setAudioMemoOpen(true);
    } catch (caught) {
      setExportNotice(errorText(caught, '逐字稿暂时无法读取，任务可能已超过保留时间。'));
    }
  };

  const retryAudioAnalysisTask = async (taskId: string) => {
    try {
      const task = await essayRadarApi.retryAudioAnalysisTask(taskId);
      setAudioAnalysisTask(task);
      setActiveAudioAnalysisTaskId(task.taskId);
      window.localStorage.setItem(AUDIO_ANALYSIS_TASK_STORAGE_KEY, task.taskId);
      setExportNotice('任务已恢复到后台队列，服务重启后也会继续执行。');
    } catch (caught) {
      setExportNotice(errorText(caught, '任务重试失败，请重新选择录音提交。'));
    }
  };

  const downloadAudioMemo = async (format: 'zip' | 'md' | 'docx' | 'json') => {
    if (!audioAnalysisTask || audioAnalysisTask.status !== 'completed') return;
    try {
      const blob = await essayRadarApi.downloadAudioAnalysis(audioAnalysisTask.taskId, format);
      const extension = format;
      const title = (audioAnalysisTask.result?.title || '录音纪要').replace(/[\\/:*?"<>|]/g, '_');
      downloadBlob(blob, `${title}.${extension}`);
    } catch (caught) {
      setExportNotice(errorText(caught, '录音纪要下载失败，任务结果仍会保留，可以稍后重试。'));
    }
  };

  const yesterday = insights?.yesterday;
  const currentFeedTotal = feedMode === 'audio' ? (audioList?.total ?? 0) : (list?.total ?? 0);
  const totalPages = Math.max(1, Math.ceil(currentFeedTotal / 20));
  const modelComparison = insights?.modelComparison;
  const libraryTotal = historicalBacklog?.totalNotes ?? 0;
  const libraryCompleted = historicalBacklog?.completed ?? 0;
  const libraryPending = historicalBacklog?.pending ?? 0;
  const libraryProcessing = historicalBacklog?.processing ?? 0;
  const libraryFailed = historicalBacklog?.failed ?? 0;
  const libraryUnqueued = historicalBacklog?.unqueued ?? 0;
  const libraryMediaOnly = historicalBacklog?.mediaOnly ?? 0;
  const librarySegments = [
    { key: 'completed', label: '已分析', value: libraryCompleted },
    { key: 'processing', label: '分析中', value: libraryProcessing },
    { key: 'pending', label: '排队中', value: libraryPending },
    { key: 'failed', label: '失败', value: libraryFailed },
    { key: 'media', label: '录音资料', value: libraryMediaOnly },
    { key: 'unqueued', label: '未入队', value: libraryUnqueued },
  ];
  const activityWindows = [
    { label: '近24小时', value: historicalBacklog?.notes24h ?? 0 },
    { label: '近7日', value: historicalBacklog?.notes7d ?? 0 },
    { label: '近30日', value: historicalBacklog?.notes30d ?? 0 },
  ];
  const activityMax = Math.max(...activityWindows.map((item) => item.value), 1);
  const trendMax = useMemo(() => Math.max(...(insights?.trend.map((item) => item.total) ?? [1]), 1), [insights?.trend]);
  const openFeedFor = (term: string) => {
    setQuery(term);
    setPage(1);
    navigate(`/essay-radar/feed?query=${encodeURIComponent(term)}`);
  };
  const changeAtlasPeriod = (nextHorizon: AtlasHorizon, nextStart?: string, nextEnd?: string) => {
    if (nextHorizon === 'custom' && (!nextStart || !nextEnd || nextStart > nextEnd)) return;
    setAtlasHorizon(nextHorizon);
    if (nextHorizon === 'custom') {
      setAtlasStartDate(nextStart || '');
      setAtlasEndDate(nextEnd || '');
    }
  };

  return (
    <AppPage className="essay-terminal max-w-none">
      <header className="essay-header">
        <div>
          <div className="essay-live-line"><span className={workerActive ? 'is-live' : ''} />知识星球自动增量库 · 最近入库 {formatTime(insights?.latestDataAt || status?.mcpSync.lastSyncAt || historicalBacklog?.latestSyncedAt, true)}</div>
          <h1>{view === 'atlas' ? '机构段子洞察图谱' : '机构段子与录音'}</h1>
          <p>{view === 'atlas' ? '按短中长期或自定义窗口，把主题提及与真实行情放在同一时间轴验证。' : '新增机构段子自动入库、自动分析，录音可检索获取；页面静默更新，不需要手动刷新。'}</p>
        </div>
        <div className="essay-auto-state" aria-label="自动更新状态"><strong>{workerActive ? '全自动运行中' : '自动恢复中'}</strong><span>MCP {status?.mcpSync.pollSeconds ?? 10} 秒 · 页面 15 秒</span><small>本页更新 {formatClock(lastAutoRefreshAt)}</small></div>
      </header>

      <RadarNavigation active={view} />
      {error ? <div className="essay-error">{error}</div> : null}
      {loading && view !== 'feed' ? <div className="essay-loading"><RefreshCw className="h-4 w-4 animate-spin" />正在读取当前页真实数据…</div> : null}

      {view === 'overview' ? (
        <div className="essay-view">
          <section className="essay-metric-grid">
            <Metric label="昨日新增" value={yesterday?.analyzedCount ?? '—'} note={yesterday?.date || '等待数据'} tone="signal" />
            <Metric label="高重要度" value={yesterday?.highImportanceCount ?? '—'} note="重要度 ≥ 80" />
            <Metric label="低置信" value={yesterday?.lowConfidenceCount ?? '—'} note="需要原文复核" tone="danger" />
            <Metric label="传闻" value={yesterday?.rumorCount ?? '—'} note="与事实分开展示" tone="danger" />
            <Metric label="证据覆盖" value={yesterday ? `${yesterday.evidenceCoveragePercent}%` : '—'} note="包含原文依据" />
          </section>

          <div className="essay-overview-grid">
            <section className="essay-panel essay-priority">
              <div className="essay-panel-head"><div><span>昨日新增 · 按信息增量排序</span><h2>优先核验</h2></div><Link to="/essay-radar/feed">进入检索与获取</Link></div>
              <div className="essay-signal-list">
                {insights?.highNoveltySignals.slice(0, 7).map((item) => <SignalRow key={item.topicId} item={item} onOpen={setSelected} />)}
                {!loading && !insights?.highNoveltySignals.length ? <EmptyState title="暂无高增量信号" description="等待新增纪要完成分析。" icon={<Sparkles className="h-7 w-7" />} /> : null}
              </div>
            </section>

            <aside className="essay-panel essay-watch-panel">
              <div className="essay-panel-head"><div><span>当前自选股</span><h2>相关信号</h2></div><Link to="/super-watchlist">超级看板</Link></div>
              <div className="essay-watch-list">{insights?.watchlist.map((stock) => (
                <article key={stock.symbol}>
                  <div><h3>{stock.name}</h3><span>{stock.symbol}</span></div>
                  <strong>{stock.monthMentions}</strong><small>近30日提及</small>
                  <div className="essay-watch-counts"><span>今日 {stock.dayMentions}</span><span>7日 {stock.weekMentions}</span></div>
                  <p>{stock.latestThesis || '尚未形成有效摘要，进入信息流查看原文。'}</p>
                  <button type="button" onClick={() => openFeedFor(stock.name)}>按该股票筛选</button>
                </article>
              ))}</div>
              <Link className="essay-wide-link" to="/essay-radar/trends">查看趋势与词频</Link>
            </aside>
          </div>
        </div>
      ) : null}

      {view === 'atlas' ? <>
        {atlasLoading && deepInsights ? <div className="essay-module-loading" role="status"><RefreshCw className="h-4 w-4 animate-spin" /><div><strong>正在计算 {atlasHorizon === 'short' ? '14日' : atlasHorizon === 'medium' ? '90日' : atlasHorizon === 'long' ? '180日' : '自定义'}窗口</strong><span>保留当前图谱，完成后整体切换，不展示半成品。</span></div></div> : null}
        {atlasNotice ? <div className="essay-module-notice">{atlasNotice}</div> : null}
        <DeepInsightsView
          data={deepInsights}
          horizon={atlasHorizon}
          startDate={atlasStartDate || deepInsights?.period.startDate || ''}
          endDate={atlasEndDate || deepInsights?.period.endDate || ''}
          onPeriodChange={changeAtlasPeriod}
          onFilter={openFeedFor}
        />
      </> : null}

      {view === 'feed' ? (
        <div className="essay-view">
          <section className="essay-library-board" aria-label="机构段子与录音知识库总览">
            <div className="essay-library-identity">
              <div><span>本地 SQLite 知识库</span><strong>{libraryStatsLoading && !historicalBacklog ? '读取中' : libraryTotal.toLocaleString()}</strong><small>条正文/录音/文件主题已入库</small></div>
              <div className="essay-library-timeline">
                <span>{formatDate(historicalBacklog?.earliestNoteAt)}</span>
                <i><b /></i>
                <span>{formatDate(historicalBacklog?.latestNoteAt)}</span>
              </div>
              <p>最近入库 {formatTime(historicalBacklog?.latestSyncedAt, true)} · {historicalBacklog?.groupCount ?? 0} 个知识星球</p>
            </div>
            <div className="essay-library-analysis">
              <div className="essay-library-analysis-head"><span>AI 分析覆盖</span><strong>{(historicalBacklog?.coveragePercent ?? 0).toFixed(1)}%</strong></div>
              <div className="essay-library-segments" aria-label="AI处理状态分布">
                {librarySegments.map((item) => <i key={item.key} className={`is-${item.key}`} style={{ width: `${libraryTotal ? (item.value / libraryTotal) * 100 : 0}%` }} />)}
              </div>
              <div className="essay-library-legend">{librarySegments.map((item) => <div key={item.key}><i className={`is-${item.key}`} /><span>{item.label}</span><strong>{item.value.toLocaleString()}</strong></div>)}</div>
            </div>
            <div className="essay-library-activity">
              <div className="essay-library-analysis-head"><span>原文新增</span><strong>{(historicalBacklog?.notes30d ?? 0).toLocaleString()}<small> / 30日</small></strong></div>
              <div>{activityWindows.map((item) => <div key={item.label}><span>{item.label}</span><i><b style={{ width: `${(item.value / activityMax) * 100}%` }} /></i><strong>{item.value.toLocaleString()}</strong></div>)}</div>
            </div>
          </section>
          <div className="essay-content-switch" role="tablist" aria-label="检索与获取内容类型">
            <button type="button" role="tab" aria-selected={feedMode === 'essays'} className={feedMode === 'essays' ? 'is-active' : ''} onClick={() => { setFeedMode('essays'); setPage(1); setFeedNotice(null); setExportNotice(null); }}><FileText className="h-4 w-4" /><span>小作文</span><small>原文与分析</small></button>
            <button type="button" role="tab" aria-selected={feedMode === 'audio'} className={feedMode === 'audio' ? 'is-active' : ''} onClick={() => { setFeedMode('audio'); setPage(1); setFeedNotice(null); setExportNotice(null); }}><Headphones className="h-4 w-4" /><span>录音文件</span><small>一个文件一行</small></button>
          </div>
          <section className="essay-filter-panel">
            <label className="essay-search"><Search className="h-4 w-4" /><input aria-label={feedMode === 'audio' ? '搜索录音文件' : '搜索小作文'} value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder={feedMode === 'audio' ? '严格搜索每个录音文件名' : queryScope === 'title' ? '仅在标题中检索关键词' : '检索标题、正文、作者、股票与 AI 标签'} /></label>
            {feedMode === 'essays' ? <select aria-label="关键词检索范围" value={queryScope} onChange={(event) => { setQueryScope(event.target.value as 'title' | 'full'); setPage(1); }}><option value="full">全文检索</option><option value="title">仅检索标题</option></select> : null}
            <select aria-label="时间范围" value={days} onChange={(event) => { setDays(Number(event.target.value)); setPage(1); }}><option value={0}>全部入库</option><option value={1}>今日</option><option value={7}>近7日</option><option value={30}>近30日</option><option value={365}>近1年</option><option value={730}>近2年</option></select>
            {feedMode === 'essays' ? <>
              <select aria-label="AI状态筛选" value={analysisStatus} onChange={(event) => { setAnalysisStatus(event.target.value); setPage(1); }}><option value="">全部小作文</option><option value="completed">已分析</option><option value="uncompleted">未完成分析</option><option value="not_queued">未入队</option><option value="pending">排队中</option><option value="processing">分析中</option><option value="failed">分析失败</option></select>
              <select aria-label="情绪筛选" value={sentiment} onChange={(event) => { setSentiment(event.target.value); setPage(1); }}><option value="">全部情绪</option>{Object.entries(SENTIMENT_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <select aria-label="类型筛选" value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">全部类型</option>{Object.entries(CATEGORY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <select aria-label="重要度筛选" value={minImportance} onChange={(event) => { setMinImportance(Number(event.target.value)); setPage(1); }}><option value={0}>全部重要度</option><option value={60}>≥ 60</option><option value={75}>≥ 75</option><option value={85}>≥ 85</option></select>
            </> : <div className="essay-audio-filter-note"><Headphones className="h-4 w-4" />严格按文件名召回，不会连带展示同帖其他录音</div>}
          </section>
          <div className="essay-feed-summary" aria-live="polite">
            <span>{query !== deferredQuery ? '等待输入完成…' : loading ? '正在检索整个本地库，当前结果继续保留…' : feedNotice || <>当前条件命中 <strong>{currentFeedTotal.toLocaleString()}</strong> {feedMode === 'audio' ? '个录音文件' : '篇小作文'} · {feedMode === 'essays' ? queryScope === 'title' ? '仅标题' : '全文' : '文件名'} · {days ? `近 ${days} 日` : '全部已入库'}</>}</span>
            <div className="essay-feed-summary-actions">
              <button type="button" onClick={() => { setQuery(''); setAnalysisStatus(''); setSentiment(''); setCategory(''); setMinImportance(0); setDays(0); setPage(1); setExportNotice(null); }}>清除筛选</button>
              {feedMode === 'essays' ? <button
                type="button"
                className="essay-feed-export"
                onClick={() => void exportFeed()}
                disabled={exportingFeed || query !== deferredQuery || loading || !(list?.total ?? 0)}
                aria-label="导出当前搜索结果 Excel"
              >
                {exportingFeed ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                {exportingFeed ? '正在生成' : '导出全部结果'}
              </button> : null}
            </div>
          </div>
          <div className="essay-batch-bar">
            <span>已选 <strong>{feedMode === 'audio' ? selectedAudio.size : selectedEssays.size}</strong> {feedMode === 'audio' ? '个录音' : '篇小作文'}</span>
            <div>
              <button type="button" onClick={toggleCurrentPageSelection} disabled={!currentFeedTotal}>全选/取消本页</button>
              <button type="button" onClick={() => feedMode === 'audio' ? setSelectedAudio(new Map()) : setSelectedEssays(new Set())} disabled={feedMode === 'audio' ? !selectedAudio.size : !selectedEssays.size}>清空已选</button>
              {feedMode === 'audio' ? <button type="button" className="is-transcribe" onClick={() => void submitSelectedAudio(false)} disabled={audioAnalysisLoading || !selectedAudio.size || !audioAnalysisCapability?.transcriptionConfigured || audioAnalysisTask?.status === 'queued' || audioAnalysisTask?.status === 'running'} title="只生成带时间戳逐字稿，不调用 AI 分析">
                {audioAnalysisLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <FileText className="h-3.5 w-3.5" />}
                {audioAnalysisLoading ? '正在提交' : '仅转写'}
              </button> : null}
              {feedMode === 'audio' ? <button type="button" className="is-ai" onClick={() => void submitSelectedAudio(true)} disabled={audioAnalysisLoading || !selectedAudio.size || !audioAnalysisCapability?.configured || audioAnalysisTask?.status === 'queued' || audioAnalysisTask?.status === 'running'} title={audioAnalysisCapability?.message}>
                {audioAnalysisLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Brain className="h-3.5 w-3.5" />}
                {audioAnalysisLoading ? '正在提交' : '转写并生成 AI 纪要'}
              </button> : null}
              <button type="button" className="is-primary" onClick={() => void downloadSelected()} disabled={batchActionLoading || (feedMode === 'audio' ? !selectedAudio.size || audioBatchTask?.status === 'queued' || audioBatchTask?.status === 'running' : !selectedEssays.size)}>
                {batchActionLoading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                {batchActionLoading ? '正在提交' : feedMode === 'audio' ? '提交后台打包' : '下载已选原文 Excel'}
              </button>
            </div>
          </div>
          {feedMode === 'audio' && audioAnalysisCapability ? <div className={`essay-audio-capability ${audioAnalysisCapability.transcriptionConfigured ? 'is-ready' : 'is-missing'}`}><Brain className="h-4 w-4" /><span>{audioAnalysisCapability.transcriptionConfigured ? `阿里云转写已就绪 · AI 纪要${audioAnalysisCapability.analysisConfigured ? '已就绪' : '未配置'} · 单次最多 ${audioAnalysisCapability.maxFiles} 个 · 单文件 ${audioAnalysisCapability.maxFileMb}MB` : audioAnalysisCapability.message}</span><small>“仅转写”不调用大模型；源音频转写后立即删除</small></div> : null}
          {feedMode === 'audio' ? <section className="essay-audio-brief-builder">
            <header><div><span>ANALYSIS BRIEF</span><h2>录音研究任务参数</h2></div><small>先定义关注问题和专业词，转写与纪要会更准确</small></header>
            <div><label><span>纪要标题（可选）</span><input value={audioMemoTitle} onChange={(event) => setAudioMemoTitle(event.target.value)} placeholder="例如：光模块产业链专家交流纪要" /></label><label><span>重点问题</span><input value={audioMemoFocus} onChange={(event) => setAudioMemoFocus(event.target.value)} placeholder="例如：2027年需求、产能瓶颈、利润弹性" /></label><label><span>金融 / 公司热词</span><input value={audioMemoHotwords} onChange={(event) => setAudioMemoHotwords(event.target.value)} placeholder="CPO，中际旭创，硅光，800G" /></label><label><span>预计说话人数</span><select value={audioMemoSpeakerCount} onChange={(event) => setAudioMemoSpeakerCount(event.target.value)}><option value="">自动识别</option><option value="2">2 人</option><option value="3">3 人</option><option value="4">4 人</option><option value="5">5 人</option><option value="6">6 人</option></select></label></div>
          </section> : null}
          {feedMode === 'audio' && audioAnalysisTask ? <section className={`essay-audio-task essay-audio-analysis-task is-${audioAnalysisTask.status}`} aria-live="polite">
            <div className="essay-audio-task-head"><div><span>{audioAnalysisTask.generateMemo === false ? '录音转写 → 带时间戳逐字稿' : '录音转写 → AI 纪要 → 录音小作文'}</span><strong>{audioAnalysisTask.message}</strong></div><b>{audioAnalysisTask.progress}%</b></div>
            <div className="essay-audio-task-track" role="progressbar" aria-label={audioAnalysisTask.generateMemo === false ? '录音转写进度' : '录音 AI 纪要生成进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={audioAnalysisTask.progress}><i style={{ width: `${audioAnalysisTask.progress}%` }} /></div>
            <div className="essay-audio-task-facts"><span>{audioAnalysisTask.phase === 'transcribing' ? '语音转写中' : audioAnalysisTask.phase === 'analyzing' ? 'AI 结构化分析中' : audioAnalysisTask.phase === 'completed' ? audioAnalysisTask.generateMemo === false ? '逐字稿已完成' : '报告已完成' : '后台任务'}</span><span>已处理 {audioAnalysisTask.completedFiles}/{audioAnalysisTask.totalFiles} 个</span>{audioAnalysisTask.currentFilename ? <span className="is-current">当前：{audioAnalysisTask.currentFilename}</span> : null}<span>结果保留 7 天</span></div>
            {audioAnalysisTask.status === 'completed' ? <div className="essay-audio-analysis-actions"><button type="button" className="is-primary" onClick={() => { setAudioMemoInitialTab(audioAnalysisTask.generateMemo === false ? 'transcript' : 'memo'); setAudioMemoOpen(true); }}><FileText className="h-4 w-4" />{audioAnalysisTask.generateMemo === false ? '页面内查看逐字稿' : '页面内查看纪要'}</button>{audioAnalysisTask.generateMemo !== false ? <button type="button" onClick={() => void downloadAudioMemo('docx')}><Download className="h-4 w-4" />下载 Word</button> : null}<button type="button" onClick={() => void downloadAudioMemo('zip')}><Archive className="h-4 w-4" />{audioAnalysisTask.generateMemo === false ? '下载逐字稿 ZIP' : '完整资料包'}</button></div> : null}
            {audioAnalysisTask.status === 'failed' ? <div className="essay-audio-task-retry"><p className="essay-audio-task-error">{audioAnalysisTask.error || '录音纪要生成失败，请核对上游配置后重试。'}</p><button type="button" onClick={() => void retryAudioAnalysisTask(audioAnalysisTask.taskId)}><RefreshCw className="h-4 w-4" />从后台重试</button></div> : null}
          </section> : null}
          {feedMode === 'audio' && audioAnalysisTasks.length ? <section className="essay-audio-task-history">
            <header><div><span>TASK LEDGER</span><h2>录音研究任务</h2></div><small>进行中与已完成任务均保留，可离开页面</small></header>
            <div>{audioAnalysisTasks.slice(0, 10).map((task) => <article key={task.taskId} className={`is-${task.status}`}><button type="button" onClick={() => void openAudioAnalysisTask(task.taskId)}><span>{task.status === 'completed' ? '已完成' : task.status === 'failed' ? '失败' : task.phase === 'resuming' ? '恢复中' : '执行中'}</span><strong>{task.title || `${task.generateMemo === false ? '录音转写' : '录音纪要'} · ${task.totalFiles} 个文件`}</strong><small>{formatTime(task.updatedAt, true)} · {task.progress}% · {task.message}</small></button><i><b style={{ width: `${task.progress}%` }} /></i>{task.status === 'failed' ? <button type="button" className="is-retry" onClick={() => void retryAudioAnalysisTask(task.taskId)}>重试</button> : null}</article>)}</div>
          </section> : null}
          {feedMode === 'audio' && audioBatchTask ? <section className={`essay-audio-task is-${audioBatchTask.status}`} aria-live="polite">
            <div className="essay-audio-task-head">
              <div><span>后台录音打包</span><strong>{audioBatchTask.message}</strong></div>
              <b>{audioBatchTask.progress}%</b>
            </div>
            <div className="essay-audio-task-track" role="progressbar" aria-label="录音后台下载与打包进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={audioBatchTask.progress}>
              <i style={{ width: `${audioBatchTask.progress}%` }} />
            </div>
            <div className="essay-audio-task-facts">
              <span>已完成 {audioBatchTask.completedFiles}/{audioBatchTask.totalFiles} 个</span>
              <span>{audioBatchTask.totalBytes ? `${formatAssetSize(audioBatchTask.downloadedBytes) || '0 B'} / ${formatAssetSize(audioBatchTask.totalBytes)}` : `已下载 ${formatAssetSize(audioBatchTask.downloadedBytes) || '0 B'}`}</span>
              {audioBatchTask.currentFilename ? <span className="is-current">当前：{audioBatchTask.currentFilename}</span> : null}
              <span>完成后保留 48 小时</span>
            </div>
            {audioBatchTask.status === 'completed' ? <div className="essay-audio-task-download">
              <button type="button" onClick={() => void downloadCompletedAudioBatch()} disabled={audioDownloadLoading}>
                {audioDownloadLoading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                {audioDownloadLoading ? `${audioDownloadProgress?.percent ?? '—'}% 正在传输` : `下载 ZIP · ${formatAssetSize(audioBatchTask.archiveBytes)}`}
              </button>
              {audioDownloadProgress ? <div role="progressbar" aria-label="录音 ZIP 下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={audioDownloadProgress.percent}><i style={{ width: `${audioDownloadProgress.percent ?? 20}%` }} /></div> : null}
            </div> : null}
            {audioBatchTask.status === 'failed' ? <p className="essay-audio-task-error">{audioBatchTask.error || '后台打包失败，请重新选择后提交。'}</p> : null}
          </section> : null}
          {exportNotice ? <div className="essay-export-notice" role="status">{exportNotice}</div> : null}
          <section className="essay-panel essay-feed-panel">
            {feedMode === 'essays' ? <div className="essay-feed-list">{list?.items.map((item) => <SignalRow key={item.topicId} item={item} onOpen={setSelected} selectable selected={selectedEssays.has(item.topicId)} onToggle={toggleEssaySelection} />)}</div> : <div className="essay-audio-list">{audioList?.items.map((item) => <AudioFileRow key={item.assetId} item={item} selected={selectedAudio.has(item.assetId)} onToggle={() => toggleAudioSelection(item)} onOpenTranscript={() => void openAudioTranscript(item)} />)}</div>}
            {!loading && !currentFeedTotal ? <EmptyState title={feedMode === 'audio' ? '没有匹配的录音文件' : '没有匹配的小作文'} description="调整关键词或时间范围。" icon={feedMode === 'audio' ? <Headphones className="h-7 w-7" /> : <Database className="h-7 w-7" />} /> : null}
            {currentFeedTotal ? <div className="essay-pagination"><span>第 {page} / {totalPages} 页</span><div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div></div> : null}
          </section>
        </div>
      ) : null}

      {view === 'trends' ? (
        <div className="essay-view">
          <div className="essay-trend-grid">
            <section className="essay-panel essay-chart-panel">
              <div className="essay-panel-head"><div><span>近14天完成分析记录</span><h2>提及量与平均重要度</h2></div><strong>峰值 {trendMax}</strong></div>
              {trendModuleState.trend === 'error' && !insights ? <div className="essay-module-empty">趋势聚合暂未完成，后台将在下一轮自动重试。</div> : <div className="essay-chart"><ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 900, height: 330 }}><ComposedChart data={insights?.trend ?? []}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.14)" /><XAxis dataKey="date" tickFormatter={(value) => String(value).slice(5)} tick={{ fontSize: 11 }} /><YAxis yAxisId="count" tick={{ fontSize: 11 }} /><YAxis yAxisId="score" orientation="right" domain={[0, 100]} tick={{ fontSize: 11 }} /><Tooltip /><Bar yAxisId="count" dataKey="total" fill="#22d3ee" opacity={0.62} /><Line yAxisId="score" type="monotone" dataKey="averageImportance" stroke="#c6ff4a" strokeWidth={2} dot={false} /></ComposedChart></ResponsiveContainer></div>}
            </section>
            <section className="essay-panel essay-cloud-panel">
              <div className="essay-panel-head"><div><span>{cloud?.startDate} 至 {cloud?.endDate} · {cloud?.sourceCount ?? 0} 篇</span><h2>提及变化</h2></div></div>
              <div className="essay-cloud-controls"><div>{(['day', 'week', 'month'] as const).map((value) => <button key={value} className={cloudPeriod === value ? 'is-active' : ''} onClick={() => setCloudPeriod(value)}>{value === 'day' ? '日' : value === 'week' ? '周' : '月'}</button>)}</div><div>{(['stocks', 'tags', 'themes'] as const).map((value) => <button key={value} className={cloudKind === value ? 'is-active' : ''} onClick={() => setCloudKind(value)}>{value === 'stocks' ? '股票' : value === 'tags' ? '标签' : '主题'}</button>)}</div></div>
              <div className="essay-cloud">{cloud?.items.slice(0, 24).map((item, index) => (
                <button key={item.name} style={{ fontSize: `${12 + Math.round((item.count / (cloud.items[0]?.count || 1)) * 18)}px`, opacity: Math.max(.5, 1 - index * .02) }} onClick={() => openFeedFor(item.name)}><span>{item.name}</span><small>{item.count}{item.change > 0 ? ' ↑' : item.change < 0 ? ' ↓' : ''}</small></button>
              ))}{trendModuleState.cloud === 'loading' && !cloud ? <div className="essay-module-empty">正在聚合词频…</div> : null}{trendModuleState.cloud === 'error' && !cloud ? <div className="essay-module-empty">词频模块正在自动重试。</div> : null}</div>
            </section>
          </div>

          <div className="essay-trend-grid essay-trend-grid-bottom">
            <section className="essay-panel">
              <div className="essay-panel-head"><div><span>自选股日 / 周 / 月口径</span><h2>关注股趋势</h2></div></div>
              <div className="essay-watch-table">{insights?.watchlist.map((stock) => (
                <button key={stock.symbol} onClick={() => openFeedFor(stock.name)}>
                  <div><strong>{stock.name}</strong><span>{stock.symbol}</span></div><span>{stock.dayMentions}</span><span>{stock.weekMentions}</span><span>{stock.monthMentions}</span><small>{stock.averageImportance.toFixed(1)}</small>
                </button>
              ))}<div className="essay-watch-table-head"><span>标的</span><span>日</span><span>周</span><span>月</span><span>重要度</span></div>{!insights?.watchlist.length ? <div className="essay-module-empty">当前用户自选股尚无可匹配语料。</div> : null}</div>
            </section>
            <section className="essay-panel">
              <div className="essay-panel-head"><div><span>近30天分析结果</span><h2>高频标的</h2></div></div>
              <div className="essay-stock-table">{dashboard?.topStocks.slice(0, 12).map((stock) => (
                <div key={stock.key}><div><strong>{stock.name || stock.tsCode}</strong><span>{stock.tsCode}</span></div><strong>{stock.mentionCount}</strong><span className="is-bull">{stock.bullish}</span><span className="is-bear">{stock.bearish}</span><small>{stock.averageImportance.toFixed(1)}</small></div>
              ))}{trendModuleState.stocks === 'loading' && !dashboard ? <div className="essay-module-empty">正在统计高频标的…</div> : null}{trendModuleState.stocks === 'error' && !dashboard ? <div className="essay-module-empty">高频标的模块正在自动重试。</div> : null}</div>
            </section>
          </div>
        </div>
      ) : null}

      {view === 'reports' ? (
        <div className="essay-view">
          <section className="essay-panel essay-report-summary">
            <div className="essay-panel-head"><div><span>{modelComparison?.reportDate || '最近报告日'}</span><h2>模型共识与分歧</h2></div><Badge variant={status?.dailyReportWorker.running ? 'success' : 'warning'}>{status?.dailyReportWorker.running ? `每日 ${status.dailyReportWorker.runHourShanghai}:00 自动生成` : '自动任务恢复中'}</Badge></div>
            <div className="essay-consensus-grid"><div><h3>共识</h3>{modelComparison?.consensus.length ? modelComparison.consensus.map((item) => <p key={item.text}>{item.text}<span>{item.modelCount} 个模型</span></p>) : <p>目前只有单模型报告，暂无跨模型共识。</p>}</div><div className="is-risk"><h3>分歧</h3>{modelComparison?.divergences.length ? modelComparison.divergences.map((item) => <p key={item.text}>{item.text}<span>{item.modelCount} 个模型</span></p>) : <p>暂无可比较的跨模型分歧。</p>}</div></div>
          </section>
          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>每个模型独立汇总前一日新增小作文</span><h2>历史日报</h2></div><strong>{reports?.total ?? 0} 份</strong></div>
            <div className="essay-report-list">{reports?.items.map((item, index) => (
              <details key={`${item.reportDate}-${item.model}`} open={index === 0}>
                <summary><div><strong>{item.reportDate}</strong><span>{item.model}</span></div><div><span>{item.sourceCount} 篇</span><Badge variant={item.status === 'completed' ? 'success' : item.status === 'failed' ? 'danger' : 'warning'}>{item.status}</Badge></div></summary>
                <DailyReportArticle item={item} />
              </details>
            ))}</div>
          </section>
        </div>
      ) : null}

      {view === 'system' ? (
        <div className="essay-view essay-system-grid">
          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>新增或正文变化后自动进入 AI 队列</span><h2>实时分析</h2></div><span className={`essay-status ${status?.worker.running ? 'is-running' : ''}`}>{status?.worker.running ? '自动分析中' : '自动恢复中'}</span></div>
            <div className="essay-progress"><div><span>近30天分析覆盖</span><strong>{status?.progress.coveragePercent ?? 0}%</strong></div><div><i style={{ width: `${status?.progress.coveragePercent ?? 0}%` }} /></div></div>
            <div className="essay-system-metrics"><Metric label="已完成" value={status?.progress.completed ?? 0} /><Metric label="待处理" value={status?.progress.pending ?? 0} /><Metric label="失败" value={status?.progress.failed ?? 0} tone="danger" /></div>
            <p className="essay-system-note">队列每 {status?.worker.pollSeconds ?? '—'} 秒自动检查；临时失败按指数退避重试，旧语料不会因服务重启被重新入队。</p>
          </section>

          <section className="essay-panel">
            <div className="essay-panel-head"><div><span>直接 MCP → SQLite · 附件仅保存链接</span><h2>知识星球增量同步</h2></div><span className={`essay-status ${status?.mcpSync.running ? 'is-running' : ''}`}>{status?.mcpSync.running ? '运行中' : '已停止'}</span></div>
            <p className="essay-system-note">每 {status?.mcpSync.pollSeconds ?? 10} 秒自动检查增量 · 最近成功 {formatTime(status?.mcpSync.lastSyncAt, true)} · 失败由同步看门狗自动唤醒，不需要人工操作。</p>
            <div className="essay-group-list">{status?.mcpSync.groups.map((group) => <div key={group.groupId}><strong>{group.groupName}</strong><Badge variant={group.lastStatus === 'success' ? 'success' : group.lastStatus === 'failed' ? 'danger' : 'default'}>{group.lastStatus}</Badge><span>累计入库 {group.totalSaved.toLocaleString()}</span><span>最新游标 {formatTime(group.lastTopicAt)}</span></div>)}</div>
          </section>

          <section className="essay-panel essay-history-panel">
            <div className="essay-panel-head"><div><span>只使用本地已入库、尚未创建 AI 任务的纪要</span><h2>历史小作文补分析</h2></div><Badge variant={historicalBacklog?.unqueued ? 'warning' : 'success'}>{historicalBacklog?.unqueued ? `${historicalBacklog.unqueued.toLocaleString()} 篇可选` : '已全部入队'}</Badge></div>
            <p className="essay-system-note">不重新抓取知识星球，不扩大所选数量；临时失败仍由后台按受控退避自动重试。</p>
            <div className="essay-system-metrics">
              <Metric label="未入队" value={(historicalBacklog?.unqueued ?? 0).toLocaleString()} note={`${formatTime(historicalBacklog?.earliestUnqueuedAt, true)} 至 ${formatTime(historicalBacklog?.latestUnqueuedAt, true)}`} />
              <Metric label="已完成" value={(historicalBacklog?.completed ?? 0).toLocaleString()} tone="signal" />
              <Metric label="排队 / 处理中" value={((historicalBacklog?.pending ?? 0) + (historicalBacklog?.processing ?? 0)).toLocaleString()} />
              <Metric label="失败" value={(historicalBacklog?.failed ?? 0).toLocaleString()} tone="danger" />
            </div>
            <div className="essay-history-controls">
              <select aria-label="历史补分析篇数" value={analysisBatchCount} onChange={(event) => setAnalysisBatchCount(Number(event.target.value))}><option value={50}>50篇</option><option value={100}>100篇</option><option value={500}>500篇</option><option value={1000}>1,000篇</option><option value={2000}>2,000篇</option><option value={5000}>5,000篇</option></select>
              <select aria-label="历史补分析顺序" value={analysisOrder} onChange={(event) => setAnalysisOrder(event.target.value as 'newest' | 'oldest')}><option value="newest">最近的优先</option><option value="oldest">最早的优先</option></select>
              <button disabled={actionLoading || !historicalBacklog?.unqueued} onClick={() => void queueHistoricalAnalysis()}>{actionLoading ? '正在加入队列' : `分析 ${Math.min(analysisBatchCount, historicalBacklog?.unqueued ?? analysisBatchCount).toLocaleString()} 篇`}</button>
            </div>
            {queueMessage ? <p className="essay-action-feedback">{queueMessage}</p> : null}
          </section>
        </div>
      ) : null}

      <EssayDetail selected={selected} onClose={() => setSelected(null)} />
      <AudioMemoDrawer task={audioMemoOpen ? audioAnalysisTask : null} initialTab={audioMemoInitialTab} onClose={() => setAudioMemoOpen(false)} onDownload={(format) => void downloadAudioMemo(format)} />
    </AppPage>
  );
};

export default EssayRadarPage;
