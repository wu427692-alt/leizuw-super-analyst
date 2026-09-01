import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Activity, ArrowRight, Binary, Boxes, BrainCircuit, ChevronRight, CircleDot,
  Clock3, Database, Download, GitBranch, Layers3, Link2, Network, RefreshCw, Scale, Search, ShieldCheck,
  Sparkles, Star, Target, TrendingUp, X,
} from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';
import { AppPage } from '../components/common';
import { EmptyState } from '../components/common/EmptyState';
import { conceptThemesApi, type ConceptClusterDetail, type ConceptLeaders, type ConceptLifecycle, type ConceptMembershipChanges, type ConceptOverview, type ConceptRotation, type ConceptStock, type ConceptTheme, type InstitutionThemeRadar, type StockThemeLens, type ThemeDetail, type WatchlistThemeMap } from '../api/conceptThemes';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { usePageActivationRefresh } from '../hooks/usePageActivationRefresh';
import './ConceptThemesPage.css';
import './ConceptThemesCompare.css';

const SOURCE_COLOR: Record<string, string> = {
  ths: '#5AA7FF', dc_board: '#7C5CFC', dc_theme: '#00D4B8', kpl: '#FFB547', tdx: '#F06C9B', sw: '#98A2B3',
};
const SOURCE_LABEL: Record<string, string> = {
  ths: '同花顺', dc_board: '东财板块', dc_theme: '东财题材', kpl: '开盘啦', tdx: '通达信', sw: '申万',
};
const TYPE_LABEL: Record<string, string> = {
  concept: '概念', theme: '题材', industry: '行业', region: '地域', style: '风格', feature: '特色', broad: '宽基',
};

const number = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat('zh-CN').format(value);
const metric = (value?: number | null, digits = 2) => value == null ? '—' : Number(value).toFixed(digits);
const signed = (value?: number | null, suffix = '%') => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}${suffix}`;
const growthMultiple = (value?: number | null) => value == null ? 'NEW' : `${Math.max(0, 1 + value / 100).toFixed(1)}×`;

type ConceptSection = 'overview' | 'market' | 'catalog' | 'stocks' | 'decision';

const CONCEPT_SECTIONS: Array<{ key: ConceptSection; index: string; label: string; purpose: string }> = [
  { key: 'overview', index: '01', label: '研究总览', purpose: '数据边界与研究入口' },
  { key: 'market', index: '02', label: '题材轮动', purpose: '价格、语料与成分变化' },
  { key: 'catalog', index: '03', label: '题材库', purpose: '分层检索与成分归因' },
  { key: 'stocks', index: '04', label: '个股映射', purpose: 'Beta、Alpha 与自选暴露' },
  { key: 'decision', index: '05', label: '交易验证', purpose: '证据门槛、计划与复盘' },
];

function ConceptWorkflowRail({ active, onSelect }: { active: ConceptSection; onSelect: (section: ConceptSection) => void }) {
  const steps: Array<{ section: ConceptSection; label: string; note: string }> = [
    { section: 'overview', label: '数据证据', note: '确认来源与截止日' },
    { section: 'market', label: '题材强弱', note: '识别扩张或退潮' },
    { section: 'catalog', label: '成分归因', note: '核验股票与权重' },
    { section: 'stocks', label: '个股暴露', note: '拆分 Beta / Alpha' },
    { section: 'decision', label: '计划验证', note: '形成规则并复盘' },
  ];
  return <nav className="concept-workflow-rail" aria-label="概念题材研究闭环">
    <span>RESEARCH LOOP</span>
    {steps.map((step, index) => <button type="button" key={step.section} className={active === step.section ? 'is-active' : ''} onClick={() => onSelect(step.section)}>
      <i>{String(index + 1).padStart(2, '0')}</i><strong>{step.label}</strong><small>{step.note}</small>{index < steps.length - 1 ? <ArrowRight /> : null}
    </button>)}
  </nav>;
}

function ConceptOverviewStart({ overview, onSelect }: { overview: ConceptOverview | null; onSelect: (section: ConceptSection) => void }) {
  const warnings = overview?.summary.quality.warnings ?? [];
  return <section className="concept-start-grid" aria-label="概念题材研究入口">
    <header><div><span>START WITH A QUESTION</span><h2>先确定研究问题，再进入对应工作台</h2></div><p>每页只加载当前任务需要的数据。结论必须能回到供应商目录、成分关系和同一截止日归因。</p></header>
    <div>
      <button type="button" onClick={() => onSelect('market')}><Activity /><span><strong>今天哪些题材正在扩张？</strong><small>比较多源价格、机构语料和成分变化</small></span><ArrowRight /></button>
      <button type="button" onClick={() => onSelect('catalog')}><Layers3 /><span><strong>一个题材到底包含谁？</strong><small>进入分层题材库，核验来源、成分与权重</small></span><ArrowRight /></button>
      <button type="button" onClick={() => onSelect('stocks')}><Target /><span><strong>一只股票跟什么题材有关？</strong><small>拆分共识主线、题材 Beta 与独特 Alpha</small></span><ArrowRight /></button>
      <button type="button" onClick={() => onSelect('decision')}><ShieldCheck /><span><strong>研究结论如何变成可验证计划？</strong><small>设置数据、主题、个股和风险四道门槛</small></span><ArrowRight /></button>
    </div>
    <footer data-tone={warnings.length ? 'attention' : 'ready'}><ShieldCheck /><strong>{warnings.length ? `${warnings.length} 项数据边界需要注意` : '当前数据边界已对齐'}</strong><p>{warnings.join(' · ') || `目录 ${overview?.summary.quality.catalogDate || '—'} · 归因 ${overview?.summary.quality.exposureDate || '—'}；仍需结合原始证据核验。`}</p></footer>
  </section>;
}

function TradeDecisionWorkbench({ overview, leaders, lifecycle, watchlistMap, onOpen }: {
  overview: ConceptOverview | null;
  leaders: ConceptLeaders | null;
  lifecycle: ConceptLifecycle | null;
  watchlistMap: WatchlistThemeMap | null;
  onOpen: (tsCode: string) => void;
}) {
  const lifecycleByTheme = new Map<string, ConceptLifecycle['items'][number]>();
  lifecycle?.items.forEach(item => {
    item.marketThemes.forEach(name => lifecycleByTheme.set(name, item));
    lifecycleByTheme.set(item.cluster, item);
  });
  const candidates = (leaders?.items ?? []).slice(0, 8).map(item => {
    const exposure = item.primaryThemes[0];
    const life = exposure ? lifecycleByTheme.get(exposure.canonicalName) : undefined;
    const sourceReady = (exposure?.sourceCount ?? 0) >= 2;
    const attributionReady = exposure?.confidence !== 'insufficient' && exposure?.beta != null;
    const alphaPositive = (exposure?.residualReturn ?? 0) > 0;
    const themeConfirming = life?.stage === '共识扩张' || life?.stage === '价格驱动';
    const passed = [sourceReady, attributionReady, alphaPositive, themeConfirming].filter(Boolean).length;
    return { item, exposure, life, passed, state: passed >= 4 ? '进入验证' : passed >= 2 ? '继续跟踪' : '补充证据' };
  });
  const topLifecycle = lifecycle?.items[0];
  const watchlistCoverage = watchlistMap?.stockCount ? watchlistMap.concentration.coveredStockCount / watchlistMap.stockCount * 100 : 0;
  return <section className="concept-decision-workbench">
    <header><div><span>RESEARCH → TEST → REVIEW</span><h2>交易研究验证台</h2><p>这里不直接给买卖指令，只把真实数据转成可执行、可证伪、可复盘的研究计划。</p></div><b>{leaders?.asOfDate || overview?.summary.quality.exposureDate || '等待归因'}</b></header>
    <div className="concept-decision-gates">
      <article><i>01</i><Database /><strong>数据门</strong><b>{overview?.summary.quality.warnings.length ? '有边界' : '已对齐'}</b><p>目录 {overview?.summary.quality.catalogDate || '—'} · 扫描 {metric(overview?.summary.scanCoveragePct, 0)}%</p></article>
      <article><i>02</i><Activity /><strong>题材门</strong><b>{topLifecycle?.stage || '待交叉'}</b><p>{topLifecycle ? `${topLifecycle.cluster} · 5日 ${signed(topLifecycle.marketMomentum5D)}` : '等待价格与语料形成真实交集'}</p></article>
      <article><i>03</i><Target /><strong>个股门</strong><b>{leaders?.totalCandidates || 0} 个候选</b><p>同一归因日比较来源、Beta、Alpha 和专属性</p></article>
      <article><i>04</i><ShieldCheck /><strong>风险门</strong><b>{metric(watchlistCoverage, 0)}% 覆盖</b><p>自选集中度 {watchlistMap?.concentration.level || '待读取'} · 结果需样本外验证</p></article>
    </div>
    <section className="concept-decision-candidates">
      <header><strong>候选验证队列</strong><small>四项检查：多源确认、归因可用、Alpha 为正、题材阶段确认</small></header>
      <div>{candidates.map(({ item, exposure, life, passed, state }) => <article key={item.tsCode} data-state={state}>
        <header><span><strong>{item.name}</strong><code>{item.tsCode}</code></span><b>{state} · {passed}/4</b></header>
        <h3>{exposure?.canonicalName || '待补主线题材'}</h3>
        <dl><div><dt>来源</dt><dd>{exposure?.sourceCount || 0} 方</dd></div><div><dt>Beta</dt><dd>{metric(exposure?.beta)}</dd></div><div><dt>Alpha</dt><dd data-tone={(exposure?.residualReturn ?? 0) >= 0 ? 'up' : 'down'}>{signed(exposure?.residualReturn)}</dd></div><div><dt>阶段</dt><dd>{life?.stage || '未交叉'}</dd></div></dl>
        <p>{state === '进入验证' ? '证据条件齐备，可进入量化回测和交易规则验证。' : state === '继续跟踪' ? '已有部分证据，等待题材或个股归因进一步确认。' : '当前证据不足，不应把单一标签当成交易理由。'}</p>
        <footer><button type="button" onClick={() => onOpen(item.tsCode)}>打开证据透镜</button><a href={`/essay-quant?symbol=${encodeURIComponent(item.tsCode)}&theme=${encodeURIComponent(exposure?.canonicalName || '')}`}>创建量化验证 <ArrowRight /></a></footer>
      </article>)}</div>
      {!candidates.length ? <div className="concept-decision-empty"><EmptyState title="候选正在准备" description="归因任务完成后，这里只展示满足真实来源和同一截止日要求的股票。" /><a href="/essay-quant?source=concept-themes&strategy=theme-event">先创建题材事件验证模板 <ArrowRight /></a><small>可先定义入场、持有期、止损与样本外窗口；候选形成后再绑定标的，不需要停在空页面等待。</small></div> : null}
    </section>
    <footer><ShieldCheck /><p><strong>闭环规则：</strong>先保存数据截止日与入选条件，再在量化回测中验证收益、回撤、成交约束和样本外表现，最后回到此页复盘失效原因。研究计划不构成投资建议。</p></footer>
  </section>;
}

function exportThemeComparison(left: ThemeDetail, right: ThemeDetail, horizonDays: number) {
  const leftByCode = new Map(left.stocks.map(item => [item.tsCode, item]));
  const rightByCode = new Map(right.stocks.map(item => [item.tsCode, item]));
  const codes = [...new Set([...leftByCode.keys(), ...rightByCode.keys()])].sort();
  const quote = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  const columns = ['股票代码', '股票名称', `${left.theme.canonicalName}成分`, `${left.theme.canonicalName}权重`, `${left.theme.canonicalName}来源数`, `${left.theme.canonicalName}Beta`, `${left.theme.canonicalName}${horizonDays}日Alpha`, `${right.theme.canonicalName}成分`, `${right.theme.canonicalName}权重`, `${right.theme.canonicalName}来源数`, `${right.theme.canonicalName}Beta`, `${right.theme.canonicalName}${horizonDays}日Alpha`, '关系'];
  const rows = codes.map(code => {
    const a = leftByCode.get(code); const b = rightByCode.get(code);
    return [code, a?.name || b?.name || '', a ? '是' : '否', a?.weightScore, a?.sourceCount, a?.beta, a?.residualReturn, b ? '是' : '否', b?.weightScore, b?.sourceCount, b?.beta, b?.residualReturn, a && b ? '共同成分' : a ? `仅${left.theme.canonicalName}` : `仅${right.theme.canonicalName}`];
  });
  const csv = `\ufeff${[columns, ...rows].map(row => row.map(quote).join(',')).join('\r\n')}`;
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url;
  anchor.download = `${left.theme.canonicalName.replaceAll('/', '-')}_对比_${right.theme.canonicalName.replaceAll('/', '-')}_${horizonDays}日.csv`;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

function ScoreRing({ value, label }: { value: number; label: string }) {
  const safe = Math.max(0, Math.min(100, value || 0));
  return <div className="concept-score-ring" style={{ '--score-angle': `${safe * 3.6}deg` } as CSSProperties}>
    <strong>{safe.toFixed(0)}</strong><span>{label}</span>
  </div>;
}

function RotationSparkline({ points }: { points: ConceptRotation['items'][number]['points'] }) {
  const values = points.map(item => item.pctChange).filter((value): value is number => typeof value === 'number');
  if (values.length < 2) return <span className="concept-rotation-new">NEW</span>;
  const low = Math.min(...values); const high = Math.max(...values); const spread = Math.max(0.1, high - low);
  const path = values.map((value, index) => `${index ? 'L' : 'M'}${(index / Math.max(1, values.length - 1) * 72).toFixed(1)},${(23 - (value - low) / spread * 20).toFixed(1)}`).join(' ');
  return <svg className="concept-rotation-spark" viewBox="0 0 72 26" role="img" aria-label={`${values.length}日题材涨跌轨迹`}><path d={path} /></svg>;
}

function ThemeCard({ item, active, onClick }: { item: ConceptTheme; active: boolean; onClick: () => void }) {
  return <button type="button" className={`concept-theme-card ${active ? 'is-active' : ''}`} onClick={onClick}>
    <div className="concept-theme-card__top">
      <span className="concept-source-dot" style={{ background: SOURCE_COLOR[item.source] ?? '#98A2B3' }} />
      <span>{item.sourceLabel}</span><code>{item.sourceCode}</code>
    </div>
    <h3>{item.canonicalName}</h3>
    {item.name !== item.canonicalName ? <p>源名称 · {item.name}</p> : <p>{item.family} → {item.cluster}</p>}
    <div className="concept-theme-card__metrics">
      <span><b>{number(item.constituentCount)}</b>成分</span>
      <span><b>{metric(item.heatScore, 0)}</b>热度</span>
      <span data-tone={(item.pctChange ?? 0) >= 0 ? 'up' : 'down'}><b>{signed(item.pctChange)}</b>当日</span>
    </div>
    <div className="concept-theme-card__foot"><span>{TYPE_LABEL[item.themeType] ?? item.themeType} · L{item.level} · {item.canonicalSourceCount || 1}方/{item.canonicalNodeCount || 1}节点</span><ChevronRight /></div>
  </button>;
}

function SourceRail({ themes }: { themes: ConceptTheme[] }) {
  return <div className="concept-source-rail">
    {themes.map((item, index) => <div className="concept-source-node" key={`${item.source}-${item.sourceCode}`}>
      <span style={{ background: SOURCE_COLOR[item.source] ?? '#98A2B3' }}>{index + 1}</span>
      <div><strong>{item.sourceLabel}</strong><small>{item.name} · {item.sourceCode}</small></div>
      <em>{number(item.constituentCount)}股</em>
    </div>)}
  </div>;
}

function InstitutionCorpusPulse({ value }: { value: ThemeDetail['institutionCorpus'] }) {
  const total = Math.max(1, value.total || 0);
  return <section className="concept-corpus-pulse">
    <header><div><span><BrainCircuit /> AI STRUCTURED CORPUS</span><strong>机构语料共识</strong><small>近 {value.windowDays || 90} 日 · 只统计主题字段明确命中的已分析机构段子</small></div><ScoreRing value={(value.score + 100) / 2} label={value.score > 12 ? '偏多' : value.score < -12 ? '偏空' : '中性'} /></header>
    <div className="concept-corpus-pulse__meter"><i data-tone="bullish" style={{ width: `${value.bullish / total * 100}%` }} /><i data-tone="neutral" style={{ width: `${value.neutral / total * 100}%` }} /><i data-tone="bearish" style={{ width: `${value.bearish / total * 100}%` }} /></div>
    <dl><div><dt>明确看多</dt><dd>{value.bullish}</dd></div><div><dt>中性跟踪</dt><dd>{value.neutral}</dd></div><div><dt>明确看空</dt><dd>{value.bearish}</dd></div><div><dt>近14日新增</dt><dd>{value.recent14D}<small>{value.volumeChangePct == null ? ' · 无前窗' : ` · ${signed(value.volumeChangePct)}`}</small></dd></div></dl>
    {value.items.length ? <div className="concept-corpus-pulse__items">{value.items.slice(0, 4).map(item => <a href={item.url} key={item.topicId}><span data-tone={item.sentiment}>{item.sentiment === 'bullish' ? '看多' : item.sentiment === 'bearish' ? '看空' : '中性'}</span><strong>{item.title}</strong><small>{item.summary || `${item.model} 已完成结构化研判`}</small></a>)}</div> : <p>当前题材暂无满足“已分析 + 明确主题命中”的机构语料，不用通用行业文字凑共识。</p>}
    <footer>{value.truncated ? '高频题材仅取最近 600 篇作为当前样本 · ' : ''}{value.method}</footer>
  </section>;
}

function ThemeAffinityMap({ value, onTheme }: { value: ThemeDetail['relatedThemes']; onTheme: (name: string) => void }) {
  if (!value?.items.length) return null;
  const maxJaccard = Math.max(...value.items.map(item => item.jaccardPct), 1);
  return <section className="concept-affinity-map">
    <header><div><span><Network /> CONSTITUENT AFFINITY</span><strong>题材成分关系图</strong><small>寻找共同股票，也识别只是名字相近但成分不同的题材</small></div><b>{value.targetTotalStocks} 股基准</b></header>
    <div>{value.items.slice(0, 10).map(item => <button type="button" key={item.canonicalName} onClick={() => onTheme(item.canonicalName)}>
      <span><strong>{item.canonicalName}</strong><small>{item.relationType} · {item.family} · {item.cluster}</small></span><b>{metric(item.jaccardPct, 1)}%</b><i style={{ width: `${Math.max(4, item.jaccardPct / maxJaccard * 100)}%` }} /><p>{item.sharedStocks} 只共同 · 当前题材仍有 {item.targetExclusiveStocks} 只独占 · 对方共 {item.otherTotalStocks} 股</p><ChevronRight />
    </button>)}</div>
    <footer>{value.method}</footer>
  </section>;
}

function ConsensusMatrix({ stocks }: { stocks: ConceptStock[] }) {
  const sources = ['ths', 'dc_board', 'dc_theme', 'kpl', 'tdx', 'sw'];
  const rows = [...stocks].sort((left, right) => right.sourceCount - left.sourceCount || right.weightScore - left.weightScore).slice(0, 12);
  if (!rows.length) return null;
  return <section className="concept-consensus-matrix">
    <header><div><strong>核心成分 × 六源共识矩阵</strong><small>亮点表示该来源明确将股票纳入当前规范题材</small></div><span>TOP {rows.length}</span></header>
    <div className="concept-matrix-grid" style={{ '--matrix-columns': sources.length } as CSSProperties}>
      <b>股票</b>{sources.map(source => <b key={source}>{SOURCE_LABEL[source] ?? source}</b>)}
      {rows.map(stock => <div className="concept-matrix-row" key={stock.tsCode}>
        <span><strong>{stock.name}</strong><small>{stock.tsCode}</small></span>
        {sources.map(source => <i key={source} className={stock.sources.includes(source) ? 'is-hit' : ''} aria-label={`${stock.name} · ${SOURCE_LABEL[source]} · ${stock.sources.includes(source) ? '已纳入' : '未纳入'}`} />)}
      </div>)}
    </div>
  </section>;
}

function BetaAlphaMap({ stocks, horizonDays }: { stocks: ConceptStock[]; horizonDays: number }) {
  const points = stocks.filter(item => typeof item.beta === 'number' && typeof item.residualReturn === 'number')
    .sort((left, right) => right.weightScore - left.weightScore).slice(0, 160)
    .map(item => ({ name: item.name, code: item.tsCode, beta: item.beta, alpha: item.residualReturn, weight: Math.max(16, item.weightScore) }));
  if (points.length < 3) return null;
  return <section className="concept-beta-alpha-map">
    <header><div><strong>Beta / Alpha 四象限</strong><small>横轴题材敏感度 · 纵轴 {horizonDays} 日窗口 Alpha · 气泡为题材权重</small></div><span>{points.length} 只已归因</span></header>
    <div className="concept-quadrant-labels"><i>独立强势</i><i>高弹性领涨</i><i>低相关/弱势</i><i>高弹性落后</i></div>
    <ResponsiveContainer width="100%" height={245}>
      <ScatterChart margin={{ top: 12, right: 16, bottom: 8, left: 4 }}>
        <CartesianGrid stroke="rgba(90,135,190,.12)" />
        <XAxis type="number" dataKey="beta" name="题材 Beta" tick={{ fill: '#607a9a', fontSize: 8 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
        <YAxis type="number" dataKey="alpha" name={`${horizonDays}日 Alpha`} unit="%" tick={{ fill: '#607a9a', fontSize: 8 }} axisLine={false} tickLine={false} width={36} domain={['auto', 'auto']} />
        <ZAxis type="number" dataKey="weight" range={[24, 150]} />
        <ReferenceLine x={1} stroke="rgba(90,167,255,.55)" strokeDasharray="4 4" />
        <ReferenceLine y={0} stroke="rgba(49,215,197,.45)" strokeDasharray="4 4" />
        <Tooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ background: '#071326', border: '1px solid #29476c', fontSize: 10 }} formatter={(value, name) => [typeof value === 'number' ? value.toFixed(2) : value, name]} />
        <Scatter name="成分股" data={points} fill="#4e8cff" fillOpacity={0.66} stroke="#83bdff" />
      </ScatterChart>
    </ResponsiveContainer>
  </section>;
}

function ThemeHistoryPulse({ value }: { value: ThemeDetail['history'] }) {
  if (!value?.points.length) return <section className="concept-theme-history is-empty"><Activity /><div><strong>题材历史正在自动补齐</strong><p>没有真实日快照时不绘制走势，也不会用当前值倒推过去。</p></div></section>;
  return <section className="concept-theme-history">
    <header><div><span><TrendingUp /> 20D CONSENSUS TAPE</span><strong>多源题材走势</strong><small>{value.availableDates} 个真实交易日 · 截止 {value.latestDate}</small></div><b data-tone={(value.cumulativeReturn ?? 0) >= 0 ? 'up' : 'down'}>{signed(value.cumulativeReturn)}</b></header>
    <ResponsiveContainer width="100%" height={175}>
      <AreaChart data={value.points} margin={{ top: 8, right: 10, bottom: 0, left: -18 }}>
        <defs><linearGradient id="concept-history-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4e8cff" stopOpacity={0.36} /><stop offset="100%" stopColor="#4e8cff" stopOpacity={0.02} /></linearGradient></defs>
        <CartesianGrid stroke="rgba(90,135,190,.10)" vertical={false} />
        <XAxis dataKey="date" tickFormatter={date => String(date).slice(5)} interval="preserveStartEnd" tick={{ fill: '#6f86a5', fontSize: 9 }} axisLine={false} tickLine={false} />
        <YAxis tickFormatter={value => `${value}%`} tick={{ fill: '#6f86a5', fontSize: 8 }} axisLine={false} tickLine={false} width={42} domain={['auto', 'auto']} />
        <Tooltip contentStyle={{ background: '#071326', border: '1px solid #29476c', fontSize: 10 }} labelFormatter={date => `${date} · 多源中位`} formatter={(metricValue, name) => [typeof metricValue === 'number' ? `${metricValue.toFixed(2)}%` : metricValue, name]} />
        <ReferenceLine y={0} stroke="rgba(130,170,220,.42)" strokeDasharray="4 4" />
        <Area type="monotone" dataKey="cumulativeReturn" name="复合累计" stroke="#5aa7ff" strokeWidth={2} fill="url(#concept-history-fill)" dot={false} activeDot={{ r: 3 }} />
      </AreaChart>
    </ResponsiveContainer>
    <footer><div>{value.points.slice(-5).map(point => <span key={point.date} data-tone={point.pctChange >= 0 ? 'up' : 'down'}><b>{point.date.slice(5)}</b><em>{signed(point.pctChange)}</em><small>{point.sourceCount}方</small></span>)}</div><p>{value.method}</p></footer>
  </section>;
}

function ThemeResearchQueue({ stocks, onOpen }: { stocks: ConceptStock[]; onOpen: (tsCode: string) => void }) {
  const take = (values: ConceptStock[]) => values.slice(0, 6);
  const groups = [
    { key: 'consensus', title: '多源核心', note: '来源≥2且权重靠前', values: take([...stocks].filter(item => item.sourceCount >= 2).sort((a, b) => b.weightScore - a.weightScore)) },
    { key: 'elastic', title: '高 Beta 弹性', note: 'Beta≥1且样本有效', values: take([...stocks].filter(item => (item.beta ?? -99) >= 1 && item.confidence !== 'insufficient').sort((a, b) => (b.beta ?? 0) - (a.beta ?? 0))) },
    { key: 'alpha', title: '独立正 Alpha', note: 'Beta<0.8且窗口Alpha>0', values: take([...stocks].filter(item => (item.beta ?? 99) < .8 && (item.residualReturn ?? 0) > 0).sort((a, b) => (b.residualReturn ?? 0) - (a.residualReturn ?? 0))) },
    { key: 'divergence', title: '负 Alpha 背离', note: 'Beta≥0.8但窗口Alpha<0', values: take([...stocks].filter(item => (item.beta ?? -99) >= .8 && (item.residualReturn ?? 0) < 0).sort((a, b) => (a.residualReturn ?? 0) - (b.residualReturn ?? 0))) },
  ];
  return <section className="concept-research-queue"><header><div><strong>题材研究优先队列</strong><small>由来源共识与归因结果生成，用于决定先核验谁，不构成买卖信号</small></div><Target /></header><div>{groups.map(group => <article key={group.key} data-kind={group.key}><h3>{group.title}</h3><p>{group.note}</p>{group.values.map(item => <button type="button" key={item.tsCode} aria-label={`${item.name}：${item.betaInterpretation}`} onClick={() => onOpen(item.tsCode)}><span>{item.name}<small>{item.tsCode}</small></span><b>{group.key === 'consensus' ? `${item.sourceCount}源 · W${metric(item.weightScore, 0)}` : group.key === 'elastic' ? `β ${metric(item.beta)}` : signed(item.residualReturn)}</b></button>)}{!group.values.length ? <em>当前无满足条件的成分</em> : null}</article>)}</div></section>;
}

function MarketConsensusRadar({ value, mode, onMode, onOpen }: {
  value: ConceptLeaders | null;
  mode: ConceptLeaders['mode'];
  onMode: (mode: ConceptLeaders['mode']) => void;
  onOpen: (tsCode: string) => void;
}) {
  const modes: Array<[ConceptLeaders['mode'], string]> = [['consensus', '独立主线共识'], ['beta', '高弹性'], ['alpha', '独立强势'], ['specificity', '独特题材']];
  return <section className="concept-market-radar" id="concept-stock-radar">
    <header><div><span><Target /> CROSS-THEME STOCK RADAR</span><h2>跨题材股票雷达</h2><p>不是按单个概念涨跌排名，而是在同一归因截止日比较股票的多源题材覆盖、Beta 与独特 Alpha。</p></div><div>{modes.map(([key, label]) => <button type="button" className={mode === key ? 'is-active' : ''} onClick={() => onMode(key)} key={key}>{label}</button>)}</div></header>
    <div className="concept-market-radar__grid">
      {value?.items.slice(0, 12).map((item, index) => { const focus = mode === 'alpha' ? item.alphaFocus : mode === 'beta' ? item.betaFocus : mode === 'specificity' ? item.specificityFocus : item.primaryThemes[0]; return <button type="button" key={item.tsCode} onClick={() => onOpen(item.tsCode)}>
        <i>{String(index + 1).padStart(2, '0')}</i><div><strong>{item.name}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><code>{item.tsCode}</code></div>
        <b>{mode === 'alpha' ? signed(focus?.residualReturn) : mode === 'beta' ? `β ${metric(focus?.beta)}` : mode === 'specificity' ? metric(focus?.specificityScore, 0) : `${item.independentClusterCount}主线`}</b>
        <p>{focus?.canonicalName || item.primaryThemes.map(theme => theme.canonicalName).slice(0, 2).join(' · ') || '待补充归因'}</p>
        <span>W{metric(item.averageWeight, 0)} · {item.consensusThemeCount}标签 · 重叠{metric(item.themeOverlapRate, 0)}% · 雷达 {metric(item.radarScore, 0)}</span><ChevronRight />
      </button>; })}
      {!value?.items.length ? <p className="concept-market-radar__empty">跨题材归因正在随成分扫描自动扩展；不会用单源题材凑榜。</p> : null}
    </div>
    <footer>{value?.asOfDate || '最新交易日'} · {value?.totalCandidates || 0} 个合格候选 · {value?.method || '仅展示同一截止日、满足共识与回归质量条件的真实结果。'}</footer>
  </section>;
}

function InstitutionDiscoveryRadar({ value, onSelect, onOpen }: {
  value: InstitutionThemeRadar | null;
  onSelect: (item: InstitutionThemeRadar['items'][number]) => void;
  onOpen: (tsCode: string) => void;
}) {
  const statusLabel = {
    provider_consensus: '多源已确认', provider_single: '单源待交叉', corpus_candidate: '语料新候选',
  } as const;
  return <section className="concept-institution-radar" id="concept-corpus-radar">
    <header><div><span><BrainCircuit /> LOCAL CORPUS DISCOVERY</span><h2>机构语料新兴题材雷达</h2><p>从本地机构段子提取正在加速的主题与明确提及股票；AI 候选不会自动升级为市场共识。</p></div><div><strong>{number(value?.totalCandidates)}</strong><small>{value?.windowDays || 30} 日有效候选</small></div></header>
    <div className="concept-institution-radar__track">
      {value?.items.slice(0, 8).map(item => {
        const totalTone = Math.max(1, item.sentiment.bullish + item.sentiment.neutral + item.sentiment.bearish);
        return <article key={item.canonicalName} data-status={item.status}>
          <div className="concept-institution-radar__title"><button type="button" onClick={() => onSelect(item)}><strong>{item.canonicalName}</strong><ChevronRight /></button><span>{statusLabel[item.status]}</span></div>
          <div className="concept-institution-radar__metrics"><b>{item.noteCount}<small>篇语料</small></b><b>{item.recent7D}<small>近7日</small></b><b data-tone={(item.accelerationPct ?? 0) >= 0 ? 'up' : 'down'}>{item.accelerationPct == null ? 'NEW' : signed(item.accelerationPct)}<small>提及加速度</small></b><b>{metric(item.discoveryScore, 0)}<small>发现分</small></b></div>
          <div className="concept-institution-radar__tone" aria-label={`看多 ${item.sentiment.bullish} · 中性 ${item.sentiment.neutral} · 看空 ${item.sentiment.bearish}`}><i data-tone="bullish" style={{ width: `${item.sentiment.bullish / totalTone * 100}%` }} /><i data-tone="neutral" style={{ width: `${item.sentiment.neutral / totalTone * 100}%` }} /><i data-tone="bearish" style={{ width: `${item.sentiment.bearish / totalTone * 100}%` }} /></div>
          <div className="concept-institution-radar__stocks">{item.stocks.slice(0, 4).map(stock => <button type="button" key={`${item.canonicalName}-${stock.tsCode || stock.name}`} onClick={() => stock.tsCode ? onOpen(stock.tsCode) : undefined} disabled={!stock.tsCode}><span>{stock.name}</span><b>{stock.mentions}</b></button>)}{!item.stocks.length ? <em>暂无明确股票提及</em> : null}</div>
          <footer>{item.providerCount ? `${item.providerCount} 个独立供应商已有同名题材` : '尚无供应商同名题材，需人工核验'} · {item.latestAt?.slice(0, 10) || '近期'}</footer>
        </article>;
      })}
      {!value?.items.length ? <p>机构语料候选正在从已完成 AI 结构化结果中聚合；无证据时不生成虚假题材。</p> : null}
    </div>
    <footer>{value?.method || '机构语料只负责发现线索，供应商目录负责定义市场共识，两套口径不会混算。'}</footer>
  </section>;
}

function ThemeLifecycleBoard({ value, onTheme }: { value: ConceptLifecycle | null; onTheme: (name: string) => void }) {
  const stageNote: Record<ConceptLifecycle['items'][number]['stage'], string> = {
    共识扩张: '语料与价格同向', 语料先行: '讨论先于价格确认', 价格驱动: '价格强于语料', 分歧退潮: '讨论仍热但价格转弱', 交叉观察: '已有交集待确认',
  };
  return <section className="concept-lifecycle" id="concept-lifecycle">
    <header><div><span><GitBranch /> MARKET × CORPUS LIFECYCLE</span><h2>题材生命周期交叉台</h2><p>把供应商题材轮动与机构段子 AI 结构化主题放到同一语义簇比较；只呈现真实交集。</p></div><div><strong>{value?.total || 0}</strong><small>个可交叉主题簇</small></div></header>
    <div className="concept-lifecycle__grid">
      {value?.items.map(item => <button type="button" data-stage={item.stage} key={`${item.family}-${item.cluster}`} onClick={() => onTheme(item.marketThemes[0] || item.cluster)}>
        <header><span>{item.stage}</span><b>{metric(item.score, 0)}</b></header>
        <h3>{item.cluster}</h3><p>{stageNote[item.stage]}</p>
        <dl><div><dt>5日市场动量</dt><dd data-tone={item.marketMomentum5D >= 0 ? 'up' : 'down'}>{signed(item.marketMomentum5D)}</dd></div><div><dt>语料相对前窗</dt><dd>{growthMultiple(item.corpusAccelerationPct)}</dd></div><div><dt>近期语料</dt><dd>{item.corpusRecent7D}/{item.corpusNotes}</dd></div><div><dt>市场来源</dt><dd>{item.marketSourceCount} 方</dd></div></dl>
        <section><span>市场</span><p>{item.marketThemes.slice(0, 3).join(' · ')}</p><span>语料</span><p>{item.corpusThemes.slice(0, 3).join(' · ')}</p></section>
        <footer>{item.interpretation}<ChevronRight /></footer>
      </button>)}
      {!value?.items.length ? <p className="concept-lifecycle__empty">市场题材与机构语料暂未形成可核验交集；候选仍保留在下方机构语料雷达，不强行归类。</p> : null}
    </div>
    <footer>{value?.marketDate || '最新交易日'} × {value?.corpusAsOfAt?.slice(0, 10) || '最新语料'} · {value?.method || '交叉阶段为描述性规则，不是交易信号。'}</footer>
  </section>;
}

function InstitutionCandidatePanel({ item, onClose, onTheme, onOpen }: {
  item: InstitutionThemeRadar['items'][number];
  onClose: () => void;
  onTheme: (name: string) => void;
  onOpen: (tsCode: string) => void;
}) {
  const statusLabel = item.status === 'provider_consensus' ? '多源市场确认' : item.status === 'provider_single' ? '单源待交叉' : '机构语料候选';
  return <aside className="concept-candidate-panel">
    <header><div><span><Sparkles /> CORPUS EVIDENCE LENS</span><h2>{item.canonicalName}</h2><p>{statusLabel} · 最近证据 {item.latestAt?.slice(0, 10) || '—'}</p></div><button type="button" onClick={onClose} aria-label="关闭机构题材证据"><X /></button></header>
    <section className="concept-candidate-panel__summary"><div><strong>{item.noteCount}</strong><span>30日语料</span></div><div><strong>{item.recent7D}</strong><span>近7日</span></div><div><strong>{item.accelerationPct == null ? 'NEW' : signed(item.accelerationPct)}</strong><span>提及加速度</span></div><div><strong>{metric(item.discoveryScore, 0)}</strong><span>发现分</span></div></section>
    <section className="concept-candidate-panel__boundary"><ShieldCheck /><div><strong>{item.providerCount ? `${item.providerCount} 个独立供应商已确认` : '尚未进入供应商市场共识'}</strong><p>{item.providerCount ? `源目录：${item.providerSources.map(source => SOURCE_LABEL[source] || source).join(' · ')}` : '当前只说明机构语料集中讨论，不能据此新增题材成分或解释股价。'}</p></div></section>
    <section className="concept-candidate-panel__stocks"><header><strong>明确提及股票</strong><small>同名股票已与题材成分库统一身份</small></header><div>{item.stocks.map(stock => <button type="button" disabled={!stock.tsCode} onClick={() => stock.tsCode ? onOpen(stock.tsCode) : undefined} key={`${stock.tsCode}-${stock.name}`}><span><strong>{stock.name}</strong><small>{stock.tsCode || '非A股/待映射'}</small></span><b>{stock.mentions} 次</b><ChevronRight /></button>)}</div></section>
    <section className="concept-candidate-panel__evidence"><header><strong>最近原始证据</strong><small>点击回到机构段子原文</small></header>{item.samples.map(sample => <a href={sample.url} key={sample.topicId}><span>{sample.date.slice(0, 10)}</span><strong>{sample.title || '未命名机构段子'}</strong><ChevronRight /></a>)}</section>
    {item.providerCount ? <button type="button" className="concept-candidate-panel__action" onClick={() => { onTheme(item.canonicalName); onClose(); }}>进入市场题材与成分归因 <ArrowRight /></button> : <p className="concept-candidate-panel__note">继续等待供应商目录交叉确认；候选证据不会自动进入题材权重、Beta 或 Alpha。</p>}
  </aside>;
}

function WatchlistExposureMap({ value, onOpen, onCluster, onExport }: {
  value: WatchlistThemeMap | null;
  onOpen: (tsCode: string) => void;
  onCluster: (family: string, cluster: string) => void;
  onExport: () => void;
}) {
  if (!value?.stockCount) return null;
  return <section className="concept-watchlist-map" id="concept-watchlist">
    <header><div><span><Star /> PERSONAL EXPOSURE MAP</span><h2>我的自选题材暴露</h2><p>按当前登录用户隔离，只统计多源确认的业务主线；相近题材先去重再看集中度。</p></div><div className="concept-watchlist-map__actions"><b>{value.stockCount} 只自选 · {value.asOfDate || '最新归因'}</b><button type="button" onClick={onExport}><Download />导出数据</button></div></header>
    <div className="concept-watchlist-map__summary">
      <div data-level={value.concentration.level}><span>共同题材集中度</span><strong>{value.concentration.level}</strong><small>{value.concentration.topCluster || '暂无共同主线'} · 覆盖 {metric(value.concentration.topCoveragePct, 0)}%</small></div>
      <div><span>多源归因覆盖</span><strong>{value.concentration.coveredStockCount}/{value.stockCount}</strong><small>未达两源门槛不会凑数</small></div>
      <div><span>共同题材</span><strong>{value.concentration.sharedClusterCount}</strong><small>至少覆盖两只自选股</small></div>
      <div><span>每股独立主线</span><strong>{metric(value.concentration.averageClusterCount, 1)}</strong><small>按语义簇去重后的均值</small></div>
      <p>{value.concentration.interpretation}</p>
    </div>
    <div className="concept-watchlist-map__body">
      <div className="concept-watchlist-map__themes">{value.themes.slice(0, 8).map(item => <button type="button" key={`${item.family}-${item.cluster}`} onClick={() => onCluster(item.family, item.cluster)}><span><strong>{item.cluster}</strong><small>{item.family}</small></span><b>{item.stockCount}/{value.stockCount}</b><p>平均权重 {metric(item.averageWeight, 0)} · {item.stocks.map(stock => stock.name).join('、')}</p><i style={{ width: `${item.stockCount / value.stockCount * 100}%` }} /></button>)}</div>
      <div className="concept-watchlist-map__stocks">{value.stocks.map(stock => <button type="button" onClick={() => onOpen(stock.tsCode)} key={stock.tsCode}><div><strong>{stock.name}</strong><code>{stock.tsCode}</code></div><b>{stock.independentClusterCount} 主线</b><p>{stock.themes.slice(0, 3).map(theme => theme.cluster).join(' · ') || '等待多源题材归因'}</p><span>{stock.rawThemeCount} 标签 · 重叠 {metric(stock.overlapRate, 0)}% · {stock.asOfDate || '待归因'}</span><ChevronRight /></button>)}</div>
    </div>
    <footer>{value.method}</footer>
  </section>;
}

function MembershipChangeLedger({ value, onOpen, onTheme }: {
  value: ConceptMembershipChanges | null;
  onOpen: (tsCode: string) => void;
  onTheme: (name: string) => void;
}) {
  return <section className="concept-change-ledger" id="concept-changes">
    <header><div><span><Activity /> PROVIDER MEMBERSHIP LEDGER</span><h2>题材成分变化账本</h2><p>跟踪供应商后续新增与退出；首次建库成分不会伪装成市场变化。</p></div><div><b>{number(value?.added)}</b><small>新增归属</small><b>{number(value?.removed)}</b><small>退出归属</small></div></header>
    <div className="concept-change-ledger__track">
      {value?.items.slice(0, 12).map(item => <article data-state={item.state} key={`${item.state}-${item.tsCode}-${item.canonicalName}`}>
        <header><span>{item.state === 'added' ? '新增归属' : '退出归属'}</span><time>{item.eventAt.slice(5, 16).replace('T', ' ')}</time></header>
        <button type="button" onClick={() => onOpen(item.tsCode)}><strong>{item.name}</strong><code>{item.tsCode}</code><ChevronRight /></button>
        <button type="button" onClick={() => onTheme(item.canonicalName)}><b>{item.canonicalName}</b><small>{item.cluster}</small></button>
        <p>{item.sourceCount} 个独立供应商 · {item.sources.map(source => SOURCE_LABEL[source] || source).join(' / ')}</p>
      </article>)}
      {!value?.items.length ? <div className="concept-change-ledger__empty"><ShieldCheck /><strong>近 {value?.windowDays || 7} 日暂无可确认的后续成分变化</strong><p>系统已忽略 {number(value?.baselineIgnored)} 条首次建库基线，不会为了填满页面生成假事件。</p></div> : null}
    </div>
    <footer>{value?.method || '供应商变更账本自动刷新；无事实时保持空白。'}</footer>
  </section>;
}

function ClusterAggregate({ value, onOpen }: { value: ConceptClusterDetail; onOpen: (tsCode: string) => void }) {
  return <section className="concept-cluster-aggregate">
    <header><div><span>LEVEL 2 AGGREGATE</span><h3>{value.cluster}</h3><p>{value.themeNodes} 个原始节点 · {value.canonicalThemes} 个规范题材 · {value.totalStocks} 只去重股票 · {value.sourceCount} 个独立提供方</p></div><b>{value.asOfDate || '最新快照'}</b></header>
    <div>{value.items.slice(0, 12).map(item => <button type="button" onClick={() => onOpen(item.tsCode)} key={item.tsCode}>
      <span><strong>{item.name}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><code>{item.tsCode}</code></span><b>{metric(item.clusterScore, 0)}</b><small>{item.themeCount} 题材 · {item.sourceCount} 方</small><p>{item.dominantExposure?.canonicalName || item.themes.slice(0, 2).join(' · ')}</p>
    </button>)}</div>
    <footer>{value.method}</footer>
  </section>;
}

function StockRow({ item, selected, onClick }: { item: ConceptStock; selected: boolean; onClick: () => void }) {
  return <button type="button" className={`concept-stock-row ${selected ? 'is-active' : ''}`} onClick={onClick}>
    <span className="concept-stock-name"><strong>{item.name || item.tsCode}{item.inWatchlist ? <Star aria-label="我的自选" /> : null}</strong><small>{item.tsCode}</small></span>
    <span><strong>{item.weightScore ? item.weightScore.toFixed(1) : '待算'}</strong><small>题材权重</small></span>
    <span aria-label={item.betaInterpretation}><strong>{metric(item.beta)}</strong><small>题材 Beta · {item.confidence === 'high' ? '高' : item.confidence === 'medium' ? '中' : '低'}置信</small></span>
    <span data-tone={(item.residualReturn ?? 0) >= 0 ? 'up' : 'down'}><strong>{signed(item.residualReturn)}</strong><small>窗口 Alpha</small></span>
    <span><strong>{item.sourceCount}</strong><small>独立提供方</small></span>
    <span className="concept-stock-reason">{item.reasons?.[0] || '结构化来源已确认，尚无文字入选原因'}</span>
    <ChevronRight />
  </button>;
}

function StockLensPanel({ value, onClose, onExport }: { value: StockThemeLens; onClose: () => void; onExport: () => void }) {
  const chart = value.primaryThemes.map(item => ({
    name: item.canonicalName.length > 9 ? `${item.canonicalName.slice(0, 9)}…` : item.canonicalName,
    weight: item.weightScore,
    beta: item.beta ?? 0,
  }));
  const driverCategories = Object.entries(value.uniqueDriverSummary?.categories || {}).slice(0, 5);
  return <aside className="concept-stock-lens">
    <div className="concept-stock-lens__head">
      <div><span>STOCK EXPOSURE LENS</span><h2>{value.name || value.tsCode}</h2><p>{value.tsCode} · 截止 {value.asOfDate || '最新入库交易日'}</p></div>
      <div className="concept-stock-lens__head-actions"><button type="button" onClick={onExport}><Download />导出画像</button><button type="button" onClick={onClose} aria-label="关闭股票题材透镜"><X /></button></div>
    </div>
    <div className="concept-lens-summary">
      <div><strong>{value.summary.themeCount}</strong><span>归属题材</span></div>
      <div><strong>{value.summary.sourceCount}</strong><span>独立提供方</span></div>
      <div><strong>{value.summary.consensusCount}</strong><span>多源共识</span></div>
      <div><strong>{value.summary.independentClusterCount}</strong><span>独立题材主线</span></div>
      <div><strong>{metric(value.summary.themeOverlapRate, 0)}%</strong><span>相近标签重叠</span></div>
      <div><strong>{value.summary.alphaPositiveCount}</strong><span>正向窗口Alpha</span></div>
      <div><strong>{value.summary.stableBetaCount}</strong><span>跨周期稳定 Beta</span></div>
      <div><strong>{value.summary.persistentAlphaCount}</strong><span>至少双窗正 Alpha</span></div>
    </div>
    <section className="concept-overlap-audit">
      <header><div><strong>题材重叠审计</strong><small>{value.overlapAudit?.method}</small></div><span>{value.overlapAudit?.dominantCluster || '待归类'} · {metric(value.overlapAudit?.dominantClusterShare, 0)}%</span></header>
      <div>{value.overlapAudit?.clusters.slice(0, 6).map(item => <article key={`${item.family}-${item.cluster}`}>
        <span><strong>{item.cluster}</strong><small>{item.themeCount} 个相近标签</small></span><b>{metric(item.weightShare, 0)}%</b><p>{item.themes.slice(0, 4).join(' · ')}</p>
      </article>)}</div>
    </section>
    <div className="concept-lens-chart">
      <ResponsiveContainer width="100%" height={230}>
        <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 24 }}>
          <CartesianGrid stroke="rgba(130,150,190,.13)" horizontal={false} />
          <XAxis type="number" domain={[0, 100]} tick={{ fill: '#7e91ad', fontSize: 10 }} axisLine={false} tickLine={false} />
          <YAxis dataKey="name" type="category" width={102} tick={{ fill: '#c8d7eb', fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: 'rgba(82,127,255,.08)' }} contentStyle={{ background: '#081327', border: '1px solid #29446b', fontSize: 11 }} />
          <Bar dataKey="weight" name="题材权重" fill="#4e8cff" radius={[0, 5, 5, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
    <div className="concept-lens-list">
      {value.primaryThemes.map(item => {
        const rawCiLow = item.components?.betaCiLow;
        const rawCiHigh = item.components?.betaCiHigh;
        const ciLow = typeof rawCiLow === 'number' ? rawCiLow : Number.NaN;
        const ciHigh = typeof rawCiHigh === 'number' ? rawCiHigh : Number.NaN;
        const hasInterval = Number.isFinite(ciLow) && Number.isFinite(ciHigh) && (item.observations ?? 0) >= 20;
        return <article key={item.canonicalName}>
        <div><h3>{item.canonicalName}</h3><span>{item.sources.map(source => SOURCE_LABEL[source] ?? source).join(' · ')}</span></div>
        <ScoreRing value={item.weightScore} label="权重" />
        <dl><div><dt>题材 Beta</dt><dd>{metric(item.beta)}</dd></div><div><dt>{value.horizonDays}日 Alpha</dt><dd>{signed(item.residualReturn)}</dd></div><div><dt>拟合度 R²</dt><dd>{metric(item.rSquared)}</dd></div></dl>
        <div className="concept-horizon-profile">
          <header><strong>跨周期归因</strong><span data-tone={item.betaStability}>{item.betaStability === 'stable' ? '稳定' : item.betaStability === 'shifting' ? '阶段切换' : '样本不足'}</span></header>
          <div>{([20, 60, 120] as const).map(window => { const point = item.horizonProfile?.find(value => value.horizonDays === window); return <span key={window}><b>{window}日</b><small>β {metric(point?.beta)}</small><small>α {signed(point?.residualReturn)}</small><em>{point ? `${point.observations}样本 · ${point.confidence === 'high' ? '高' : point.confidence === 'medium' ? '中' : '低'}置信` : '待计算'}</em></span>; })}</div>
        </div>
        <div className="concept-weight-decomp" aria-label="题材权重分项">
          <span><i style={{ width: `${Number(item.components?.consensus) || 0}%` }} /><b>{metric(Number(item.components?.consensus) || 0, 0)}</b><small>来源共识 · 36%</small></span>
          <span aria-label={`供应商理由 ${metric(Number(item.components?.providerReasonScore) || 0, 0)} · 本地机构语料 ${metric(Number(item.components?.localCorpusScore) || 0, 0)}`}><i style={{ width: `${Number(item.components?.relevance) || 0}%` }} /><b>{metric(Number(item.components?.relevance) || 0, 0)}</b><small>业务证据 · 29%{Number(item.components?.localCorpusEvidenceCount) > 0 ? ` · 机构${Number(item.components?.localCorpusEvidenceCount)}篇` : ''}</small></span>
          <span><i style={{ width: `${Number(item.components?.market) || 0}%` }} /><b>{metric(Number(item.components?.market) || 0, 0)}</b><small>市场热度 · 20%</small></span>
          <span><i style={{ width: `${Number(item.components?.specificity) || 0}%` }} /><b>{metric(Number(item.components?.specificity) || 0, 0)}</b><small>题材专属性 · 15%</small></span>
        </div>
        <small className="concept-beta-audit">{hasInterval ? `Beta 95%区间 ${ciLow.toFixed(2)} ~ ${ciHigh.toFixed(2)}` : 'Beta区间待足够样本'} · {item.observations ?? 0}个交易日样本</small>
        <p>{item.betaInterpretation || '样本不足，暂不解释 Beta。'} · {item.reasons?.[0] || '该题材由结构化成分表确认，暂无文字证据。'}</p>
        {item.evidence?.find(entry => typeof entry === 'object' && entry.kind === 'institution_corpus') ? <p className="concept-corpus-proof">本地语料交叉证据 · {(() => { const entry = item.evidence?.find(value => typeof value === 'object' && value.kind === 'institution_corpus'); return typeof entry === 'object' ? `${entry.title || ''} ${entry.summary || ''}`.trim() : ''; })()}</p> : null}
      </article>;})}
      {!value.primaryThemes.length ? <p className="concept-muted concept-no-consensus">当前没有达到两个独立来源的主线题材；下方单源线索不会冒充市场共识。</p> : null}
    </div>
    <section className="concept-unique-themes">
      <header><GitBranch /><div><h3>非共识题材线索</h3><p>单一来源且专属性较高，只作为待核验 Alpha 方向，不计作市场共识</p></div></header>
      {value.uniqueThemes?.map(item => <article key={item.canonicalName}>
        <div><strong>{item.canonicalName}</strong><span>{SOURCE_LABEL[item.sources[0]] ?? item.sources[0] ?? '单一来源'}</span></div>
        <b>{metric(item.specificityScore, 0)}</b>
        <p>{item.reasons?.[0] || '来源成分表确认，尚缺少文字入选理由。'}</p>
      </article>)}
      {!value.uniqueThemes?.length ? <p className="concept-muted">当前没有同时满足“单源 + 高专属性 + 已归因”的题材。</p> : null}
    </section>
    <section className="concept-alpha-evidence">
      <header><Sparkles /><div><h3>公司独特 Alpha 线索</h3><p>与题材共振分开呈现，仅列本地事实库可回溯证据</p></div></header>
      {value.uniqueDrivers.length ? <div className="concept-driver-summary">
        <div>{driverCategories.map(([name, count]) => <span key={name}><b>{name}</b><em>{count}</em></span>)}</div>
        <p><i data-tone="positive">正向措辞 {value.uniqueDriverSummary?.directions.positive || 0}</i><i data-tone="neutral">中性 {value.uniqueDriverSummary?.directions.neutral || 0}</i><i data-tone="negative">风险措辞 {value.uniqueDriverSummary?.directions.negative || 0}</i></p>
        <small>{value.uniqueDriverSummary?.method}</small>
      </div> : null}
      {value.uniqueDrivers.slice(0, 8).map((item, index) => <a href={item.url || '#'} key={`${item.kind}-${item.title}-${index}`}>
        <span>{item.category} · {item.source}</span><em data-tone={item.direction}>{item.direction === 'positive' ? '正向措辞' : item.direction === 'negative' ? '风险措辞' : '中性事实'}</em><strong>{item.title}</strong><p>{item.summary}</p>
      </a>)}
      {!value.uniqueDrivers.length ? <p className="concept-muted">近半年尚未检出可单独归因的公司事件。</p> : null}
    </section>
  </aside>;
}

export default function ConceptThemesPage() {
  const initialParams = typeof window === 'undefined' ? null : new URLSearchParams(window.location.search);
  const initialThemeValue = Number(initialParams?.get('theme') || 0);
  const initialThemeId = Number.isInteger(initialThemeValue) && initialThemeValue > 0 ? initialThemeValue : null;
  const initialWindowValue = Number(initialParams?.get('window') || 60);
  const initialHorizon = ([20, 60, 120].includes(initialWindowValue) ? initialWindowValue : 60) as 20 | 60 | 120;
  const initialSectionValue = initialParams?.get('section') as ConceptSection | null;
  const initialSection = initialThemeId != null ? 'catalog' : CONCEPT_SECTIONS.some(item => item.key === initialSectionValue) ? initialSectionValue! : 'overview';
  const directThemeRef = useRef(initialThemeId != null);
  const [activeSection, setActiveSection] = useState<ConceptSection>(initialSection);
  const [overview, setOverview] = useState<ConceptOverview | null>(null);
  const [rotation, setRotation] = useState<ConceptRotation | null>(null);
  const [leaders, setLeaders] = useState<ConceptLeaders | null>(null);
  const [institutionRadar, setInstitutionRadar] = useState<InstitutionThemeRadar | null>(null);
  const [lifecycle, setLifecycle] = useState<ConceptLifecycle | null>(null);
  const [institutionCandidate, setInstitutionCandidate] = useState<InstitutionThemeRadar['items'][number] | null>(null);
  const [watchlistMap, setWatchlistMap] = useState<WatchlistThemeMap | null>(null);
  const [membershipChanges, setMembershipChanges] = useState<ConceptMembershipChanges | null>(null);
  const [leaderMode, setLeaderMode] = useState<ConceptLeaders['mode']>('consensus');
  const [clusterDetail, setClusterDetail] = useState<ConceptClusterDetail | null>(null);
  const [detail, setDetail] = useState<ThemeDetail | null>(null);
  const [stockLens, setStockLens] = useState<StockThemeLens | null>(null);
  const [compareThemes, setCompareThemes] = useState<ThemeDetail[]>([]);
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebouncedValue(query, 320);
  const [themeType, setThemeType] = useState('');
  const [source, setSource] = useState('');
  const [family, setFamily] = useState('');
  const [cluster, setCluster] = useState('');
  const [minSources, setMinSources] = useState(1);
  const [readiness, setReadiness] = useState<'all' | 'membered' | 'attributed' | 'researchable'>('all');
  const [catalogView, setCatalogView] = useState<'canonical' | 'source'>('canonical');
  const [sortBy, setSortBy] = useState<'heat' | 'name' | 'size' | 'change'>('heat');
  const [page, setPage] = useState(1);
  const [horizonDays, setHorizonDays] = useState<20 | 60 | 120>(initialHorizon);
  const [selectedTheme, setSelectedTheme] = useState<number | null>(initialThemeId);
  const [stockQuery, setStockQuery] = useState('');
  const [stockSort, setStockSort] = useState<'weight' | 'beta' | 'alpha' | 'name'>('weight');
  const [watchlistOnly, setWatchlistOnly] = useState(false);
  const [stockPage, setStockPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [status, setStatus] = useState('正在读取共享题材图谱…');
  const requestSequence = useRef(0);

  const loadOverview = useCallback(async () => {
    const sequence = ++requestSequence.current;
    try {
      const responsivePageSize = typeof window !== 'undefined' && window.innerWidth <= 720 ? 10 : 24;
      const value = await conceptThemesApi.overview({ query: debouncedQuery, themeType, source, family, cluster, minSources, readiness, sortBy, view: catalogView, page, pageSize: responsivePageSize });
      if (sequence !== requestSequence.current) return;
      setOverview(value);
      setStatus(`${value.summary.marketDate || '最新交易日'} · ${number(value.summary.themes)} 个源题材 · ${number(value.summary.memberships)} 条成分关系`);
      if (activeSection === 'catalog') setSelectedTheme(current => (directThemeRef.current && current != null) || value.items.some(item => item.id === current) ? current : value.items.find(item => item.canonicalSourceCount >= 3 && item.constituentCount > 0)?.id ?? value.items.find(item => item.canonicalSourceCount >= 2 && item.constituentCount > 0)?.id ?? value.items[0]?.id ?? null);
    } catch (error) {
      if (sequence !== requestSequence.current) return;
      setStatus(error instanceof Error ? error.message : '题材库暂时不可用，系统会静默重试');
    } finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [activeSection, catalogView, cluster, debouncedQuery, family, minSources, page, readiness, sortBy, source, themeType]);
  const loadRotation = useCallback(async () => {
    try { setRotation(await conceptThemesApi.rotation(20, 18)); }
    catch { /* keep the last successful rotation snapshot while the upstream refreshes */ }
  }, []);
  const loadLeaders = useCallback(async () => {
    try { setLeaders(await conceptThemesApi.leaders(horizonDays, leaderMode, 24)); }
    catch { /* keep the last successful market radar while attribution catches up */ }
  }, [horizonDays, leaderMode]);
  const loadInstitutionRadar = useCallback(async () => {
    try { setInstitutionRadar(await conceptThemesApi.institutionRadar(30, 16)); }
    catch { /* keep the last successful corpus discovery snapshot while analysis catches up */ }
  }, []);
  const loadLifecycle = useCallback(async () => {
    try { setLifecycle(await conceptThemesApi.lifecycle(30, 12)); }
    catch { /* keep the last successful lifecycle cross-check */ }
  }, []);
  const loadWatchlistMap = useCallback(async () => {
    try { setWatchlistMap(await conceptThemesApi.watchlistMap(horizonDays)); }
    catch { /* a user without a watchlist should not block the market workspace */ }
  }, [horizonDays]);
  const loadMembershipChanges = useCallback(async () => {
    try { setMembershipChanges(await conceptThemesApi.membershipChanges(7, 24)); }
    catch { /* keep the last successful provider change ledger */ }
  }, []);
  const loadCluster = useCallback(async () => {
    if (!family || !cluster) return;
    try { setClusterDetail(await conceptThemesApi.cluster(family, cluster, horizonDays)); }
    catch { /* keep the last successful cluster aggregate during a transient refresh */ }
  }, [cluster, family, horizonDays]);

  useEffect(() => { document.title = '概念题材查看 - 乐子乌超级价值'; }, []);
  useEffect(() => {
    const tasks: Array<Promise<unknown>> = [];
    if (activeSection === 'market') tasks.push(loadRotation(), loadInstitutionRadar(), loadLifecycle(), loadMembershipChanges());
    if (activeSection === 'stocks') tasks.push(loadLeaders(), loadWatchlistMap());
    if (activeSection === 'decision') tasks.push(loadRotation(), loadLeaders(), loadLifecycle(), loadWatchlistMap());
    if (tasks.length) void Promise.allSettled(tasks);
  }, [activeSection, loadCluster, loadInstitutionRadar, loadLeaders, loadLifecycle, loadMembershipChanges, loadRotation, loadWatchlistMap]);
  useEffect(() => { if (activeSection === 'catalog') { setClusterDetail(null); void loadCluster(); } }, [activeSection, loadCluster]);
  useEffect(() => { setPage(1); }, [catalogView, cluster, debouncedQuery, family, minSources, readiness, sortBy, source, themeType]);
  useEffect(() => { setLoading(true); void loadOverview(); }, [loadOverview]);
  const refreshActivePage = useCallback(async () => {
    const tasks: Array<Promise<unknown>> = [loadOverview()];
    if (activeSection === 'market') tasks.push(loadRotation(), loadInstitutionRadar(), loadLifecycle(), loadMembershipChanges());
    if (activeSection === 'stocks') tasks.push(loadLeaders(), loadWatchlistMap());
    if (activeSection === 'decision') tasks.push(loadRotation(), loadLeaders(), loadLifecycle(), loadWatchlistMap());
    if (activeSection === 'catalog') tasks.push(loadCluster());
    await Promise.allSettled(tasks);
  }, [activeSection, loadCluster, loadInstitutionRadar, loadLeaders, loadLifecycle, loadMembershipChanges, loadOverview, loadRotation, loadWatchlistMap]);
  usePageActivationRefresh(refreshActivePage, { intervalMs: 60_000, minIntervalMs: 8_000, runOnMount: false });

  useEffect(() => {
    if (activeSection !== 'catalog' || selectedTheme == null) { if (activeSection !== 'catalog') setDetail(null); return; }
    let active = true;
    setDetailLoading(true);
    setDetail(null);
    conceptThemesApi.theme(selectedTheme, true, horizonDays).then(value => { if (active) setDetail(value); }).catch(error => {
      if (active) setStatus(error instanceof Error ? error.message : '题材成分读取失败');
    }).finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [activeSection, horizonDays, selectedTheme]);

  const families = useMemo(() => Object.entries(overview?.summary.families ?? {}).sort((a, b) => b[1] - a[1]), [overview]);
  const clusters = useMemo(() => Object.entries(family ? overview?.summary.clusterFamilies?.[family] ?? {} : {}).sort((a, b) => b[1] - a[1]), [family, overview]);
  const visibleThemes = overview?.items ?? [];
  const filteredStocks = useMemo(() => {
    const term = stockQuery.trim().toLowerCase();
    const values = (detail?.stocks ?? []).filter(item => (!watchlistOnly || item.inWatchlist) && (!term || item.name.toLowerCase().includes(term) || item.tsCode.toLowerCase().includes(term)));
    values.sort((left, right) => stockSort === 'name' ? left.name.localeCompare(right.name, 'zh-CN')
      : stockSort === 'beta' ? (right.beta ?? -999) - (left.beta ?? -999)
        : stockSort === 'alpha' ? (right.residualReturn ?? -999) - (left.residualReturn ?? -999)
          : right.weightScore - left.weightScore);
    return values;
  }, [detail, stockQuery, stockSort, watchlistOnly]);
  const stockPageSize = 24;
  const visibleStocks = filteredStocks.slice((stockPage - 1) * stockPageSize, stockPage * stockPageSize);
  const consensusDistribution = detail?.consensusDistribution ?? {
    strong: detail?.stocks.filter(item => item.sourceCount >= 3).length ?? 0,
    confirmed: detail?.stocks.filter(item => item.sourceCount === 2).length ?? 0,
    singleSource: detail?.stocks.filter(item => item.sourceCount === 1).length ?? 0,
  };
  const comparison = useMemo(() => {
    if (compareThemes.length !== 2) return null;
    const [left, right] = compareThemes;
    const leftCodes = new Set(left.stocks.map(item => item.tsCode));
    const rightCodes = new Set(right.stocks.map(item => item.tsCode));
    const shared = left.stocks.filter(item => rightCodes.has(item.tsCode));
    const union = new Set([...leftCodes, ...rightCodes]).size;
    const exclusive = (source: ThemeDetail, otherCodes: Set<string>) => source.stocks
      .filter(item => !otherCodes.has(item.tsCode)).sort((a, b) => b.weightScore - a.weightScore).slice(0, 8);
    return {
      left, right, shared,
      similarity: union ? shared.length / union * 100 : 0,
      leftExclusive: exclusive(left, rightCodes), rightExclusive: exclusive(right, leftCodes),
    };
  }, [compareThemes]);

  useEffect(() => { setStockPage(1); setStockQuery(''); setWatchlistOnly(false); }, [selectedTheme]);
  useEffect(() => { setStockPage(1); }, [stockQuery, stockSort]);

  const openStock = async (tsCode: string) => {
    setDetailLoading(true);
    try { setStockLens(await conceptThemesApi.stock(tsCode, true, horizonDays)); }
    catch (error) { setStatus(error instanceof Error ? error.message : '股票题材透镜读取失败'); }
    finally { setDetailLoading(false); }
  };
  const copyThemeLink = async () => {
    if (!detail) return;
    const url = new URL(window.location.href);
    url.searchParams.set('theme', String(detail.theme.id));
    url.searchParams.set('window', String(horizonDays));
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(url.toString());
    } catch {
      const input = document.createElement('textarea');
      input.value = url.toString();
      input.setAttribute('readonly', '');
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.select();
      const copied = document.execCommand('copy');
      input.remove();
      if (!copied) {
        setStatus('浏览器未授权复制；研究链接已写入当前地址，可直接复制地址栏');
        window.history.replaceState({}, '', url);
        return;
      }
    }
    setStatus(`已复制 ${detail.theme.canonicalName} 的可复现研究链接`);
  };
  const toggleCompare = (value: ThemeDetail) => setCompareThemes(current => {
    const exists = current.some(item => item.theme.canonicalName === value.theme.canonicalName);
    if (exists) return current.filter(item => item.theme.canonicalName !== value.theme.canonicalName);
    return current.length >= 2 ? [current[1], value] : [...current, value];
  });
  const selectSection = useCallback((next: ConceptSection) => {
    setActiveSection(next);
    const url = new URL(window.location.href);
    url.searchParams.set('section', next);
    if (next !== 'catalog') url.searchParams.delete('theme');
    window.history.replaceState({}, '', url);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);
  const openThemeInCatalog = useCallback((name: string) => {
    setQuery(name); setPage(1); setFamily(''); setCluster(''); selectSection('catalog');
  }, [selectSection]);

  return <AppPage className="concept-page max-w-[1840px]">
    <div className="concept-shell">
      <header className="concept-hero">
        <div className="concept-hero__copy"><span><Network /> CONCEPT CONSENSUS GRAPH</span><h1>概念题材查看</h1><p>把分散的市场题材、行业层级和成分股归属，变成有来源、有权重、有 Beta/Alpha 解释的共识地图。</p></div>
        <div className="concept-hero__status"><i className={loading || detailLoading ? 'is-loading' : ''} /><span>{status}</span><b><RefreshCw />自动更新</b></div>
        <div className="concept-orbit" aria-hidden="true"><span /><span /><span /><b /></div>
      </header>

      <nav className="concept-page-nav" aria-label="概念题材工作台分页">
        {CONCEPT_SECTIONS.map(item => <button type="button" key={item.key} className={activeSection === item.key ? 'is-active' : ''} onClick={() => selectSection(item.key)}>
          <i>{item.index}</i><span><strong>{item.label}</strong><small>{item.purpose}</small></span>
        </button>)}
      </nav>
      <ConceptWorkflowRail active={activeSection} onSelect={selectSection} />

      {activeSection === 'overview' ? <>
        <section className="concept-kpis">
          <div><Database /><span>源题材节点</span><strong>{number(overview?.summary.themes)}</strong><small>语义分层 {metric(overview?.summary.semanticCoveragePct, 1)}% · 六套原始口径保留</small></div>
          <div><GitBranch /><span>成分关系</span><strong>{number(overview?.summary.memberships)}</strong><small>已扫描 {number(overview?.summary.attemptedThemes)} 个节点 · {metric(overview?.summary.scanCoveragePct, 1)}%</small></div>
          <div><Binary /><span>归因结果</span><strong>{number(overview?.summary.exposures)}</strong><small>{number(overview?.summary.exposureThemes)}题材 / {number(overview?.summary.exposureStocks)}股票 · 可研究 {number(overview?.summary.researchableThemes)}</small></div>
          <div><ShieldCheck /><span>最新交易日</span><strong>{overview?.summary.marketDate || '—'}</strong><small>{overview?.sync?.stage || '共享题材库'}</small></div>
        </section>
        <section className="concept-quality-strip" data-tone={overview?.summary.quality.warnings.length ? 'attention' : 'ready'} aria-label="题材数据质量与截止时间">
          <div><span>目录交易日</span><strong>{overview?.summary.quality.catalogDate || '—'}</strong></div>
          <div><span>独立目录就绪</span><strong>{overview ? `${overview.summary.quality.freshCatalogs}/${overview.summary.quality.totalCatalogs}` : '—'}</strong></div>
          <div><span>成分扫描</span><strong>{metric(overview?.summary.scanCoveragePct, 1)}%</strong></div>
          <div><span>归因截止</span><strong>{overview?.summary.quality.exposureDate || '—'}</strong></div>
          <p>{overview?.summary.quality.warnings.join(' · ') || '目录、成分与归因截止时间一致；仍需结合原始证据核验。'}</p>
        </section>
        <ConceptOverviewStart overview={overview} onSelect={selectSection} />
        <section className="concept-cadence" aria-label="概念题材数据更新节奏">
          <header><Clock3 /><div><strong>数据更新节奏</strong><small>按数据本身的生成频率更新，不把页面轮询冒充上游实时数据</small></div></header>
          <div><span>供应商题材目录</span><strong>每日交易日同步</strong><small>保留六套原始口径与各自交易日</small></div>
          <div><span>全市场成分扫描</span><strong>每 5 分钟续跑</strong><small>断点续扫 · 单轮 120 个源节点</small></div>
          <div><span>我的自选校验</span><strong>每 6 小时复核</strong><small>优先补齐新增自选的题材和归因</small></div>
          <div><span>当前页面</span><strong>切回即刷新</strong><small>停留期间每 60 秒静默读取共享库</small></div>
        </section>
      </> : null}

      {activeSection === 'catalog' ? <>
      <section className="concept-toolbar">
        <label className="concept-search"><Search /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索题材、行业、股票代码或源代码" /></label>
        <select value={themeType} onChange={event => setThemeType(event.target.value)} aria-label="题材类型"><option value="">全部层级</option><option value="theme">市场题材</option><option value="concept">概念</option><option value="industry">行业</option><option value="region">地域</option><option value="style">风格</option></select>
        <select value={source} onChange={event => setSource(event.target.value)} aria-label="数据来源"><option value="">全部来源</option>{overview?.methodology.sources.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</select>
        <select value={minSources} onChange={event => setMinSources(Number(event.target.value))} aria-label="共识门槛"><option value={1}>全部共识层级</option><option value={2}>至少 2 个来源</option><option value={3}>至少 3 个来源</option><option value={4}>至少 4 个来源</option></select>
        <select value={readiness} onChange={event => setReadiness(event.target.value as typeof readiness)} aria-label="数据成熟度"><option value="all">全部数据成熟度</option><option value="membered">已有成分股</option><option value="attributed">已完成归因</option><option value="researchable">可研究 · 多源且已归因</option></select>
        <select value={catalogView} onChange={event => setCatalogView(event.target.value as typeof catalogView)} aria-label="题材展示口径"><option value="canonical">规范题材 · 去重</option><option value="source">全部源节点</option></select>
        <select value={sortBy} onChange={event => setSortBy(event.target.value as typeof sortBy)} aria-label="排序"><option value="heat">市场热度</option><option value="change">当日涨跌</option><option value="size">成分规模</option><option value="name">名称</option></select>
      </section>
      {debouncedQuery && overview?.stockMatches?.length ? <section className="concept-stock-direct" aria-label="股票题材画像直达">
        <span><Target /> 股票画像直达</span>
        <div>{overview.stockMatches.map(item => <button type="button" key={item.tsCode} onClick={() => void openStock(item.tsCode)}>
          <strong>{item.name}</strong><code>{item.tsCode}</code><small>{item.themeCount} 个题材 · {item.sourceCount} 个来源</small><ChevronRight />
        </button>)}</div>
      </section> : null}
      </> : null}

      {activeSection === 'market' ? <>
      <section className="concept-rotation-board" id="concept-rotation">
        <header><div><span><Activity /> THEME ROTATION</span><h2>多源题材轮动</h2></div><p>{rotation?.latestDate || overview?.summary.marketDate || '最新交易日'} · {rotation?.availableDates || 0} 个历史交易日 · 日涨跌取多来源中位数</p></header>
        <div className="concept-rotation-track">
          {rotation?.items.slice(0, 12).map(item => <button type="button" key={item.canonicalName} onClick={() => openThemeInCatalog(item.canonicalName)}>
            <div><strong>{item.canonicalName}</strong><span>{item.cluster}</span></div>
            <RotationSparkline points={item.points} />
            <b data-tone={(item.pctChange || 0) >= 0 ? 'up' : 'down'}>{signed(item.pctChange)}</b>
            <small>轮动 {metric(item.rotationScore, 0)} · {item.sourceCount}源 · 5日 {signed(item.momentum5d)}</small>
          </button>)}
          {!rotation?.items.length ? <p>题材历史快照正在自动建立；当前目录与成分股仍可正常检索。</p> : null}
        </div>
        <footer>{rotation?.method || '每日保留各来源原始快照后再聚合；数据不足时不外推历史。'}</footer>
      </section>

      <ThemeLifecycleBoard value={lifecycle} onTheme={openThemeInCatalog} />

      <InstitutionDiscoveryRadar value={institutionRadar} onSelect={item => { setStockLens(null); setInstitutionCandidate(item); }} onOpen={tsCode => void openStock(tsCode)} />

      <MembershipChangeLedger value={membershipChanges} onOpen={tsCode => void openStock(tsCode)} onTheme={openThemeInCatalog} />
      </> : null}

      {activeSection === 'stocks' ? <>
        <WatchlistExposureMap value={watchlistMap} onOpen={tsCode => void openStock(tsCode)} onCluster={(nextFamily, nextCluster) => { setFamily(nextFamily); setCluster(nextCluster); selectSection('catalog'); }} onExport={() => void conceptThemesApi.exportWatchlistMap(horizonDays)} />
        <MarketConsensusRadar value={leaders} mode={leaderMode} onMode={setLeaderMode} onOpen={tsCode => void openStock(tsCode)} />
      </> : null}

      {activeSection === 'catalog' ? <>
      <div className="concept-workspace" id="concept-universe">
        <aside className="concept-families">
          <div className="concept-panel-title"><Layers3 /><div><strong>题材分层</strong><small>规范族群 → 原始题材</small></div></div>
          <button type="button" className={!family ? 'is-active' : ''} onClick={() => { setFamily(''); setCluster(''); }}><span>全部题材宇宙</span><b>{number(overview?.summary.themes)}</b></button>
          {families.map(([name, count], index) => <button type="button" className={family === name ? 'is-active' : ''} onClick={() => { setFamily(name); setCluster(''); }} key={name}>
            <i>{String(index + 1).padStart(2, '0')}</i><span>{name}</span><b>{count}</b>
          </button>)}
          <section className="concept-source-legend"><h3>六源目录 · 更新与覆盖透明</h3>{overview?.methodology.sources.map(item => { const health = overview.summary.sourceHealth?.[item.key]; return <div key={item.key}><span style={{ background: SOURCE_COLOR[item.key] }} /><b>{item.name}<small>{health?.marketDate || '待更新'} · {metric(health?.scanCoveragePct, 0)}%已扫描</small></b><em data-tone={health?.status === 'fresh' ? 'fresh' : 'lagging'}>{number(overview?.summary.sources[item.key])} 节点 · W{Math.round(item.reliability * 100)}</em></div>; })}</section>
        </aside>

        <main className="concept-universe">
          <div className="concept-section-head"><div><span>THEME UNIVERSE</span><h2>{family || '全市场题材宇宙'}</h2></div><p>当前召回 {number(overview?.total)} 个{catalogView === 'canonical' ? '规范题材' : '源节点'} · 点击进入成分与归因</p></div>
          {clusters.length ? <div className="concept-clusters"><button type="button" className={!cluster ? 'is-active' : ''} onClick={() => setCluster('')}>全部二级主题</button>{clusters.map(([name, count]) => <button type="button" className={cluster === name ? 'is-active' : ''} onClick={() => setCluster(name)} key={name}>{name}<b>{count}</b></button>)}</div> : null}
          {clusterDetail ? <ClusterAggregate value={clusterDetail} onOpen={tsCode => void openStock(tsCode)} /> : null}
          <div className="concept-grid">
            {visibleThemes.map(item => <ThemeCard key={item.id} item={item} active={selectedTheme === item.id} onClick={() => { directThemeRef.current = false; setSelectedTheme(item.id); }} />)}
          </div>
          {overview && overview.total > overview.pageSize ? <nav className="concept-pagination" aria-label="题材分页"><button type="button" disabled={page <= 1 || loading} onClick={() => setPage(value => Math.max(1, value - 1))}>上一页</button><span>第 {page} / {Math.max(1, Math.ceil(overview.total / overview.pageSize))} 页</span><button type="button" disabled={page >= Math.ceil(overview.total / overview.pageSize) || loading} onClick={() => setPage(value => value + 1)}>下一页</button></nav> : null}
          {!loading && !visibleThemes.length ? <EmptyState title="没有命中题材" description="调整关键词或层级筛选；后台目录会按最新交易日自动更新。" /> : null}
        </main>

        <aside className="concept-detail">
          {detail ? <>
            <header className="concept-detail__head"><span>{detail.theme.family}</span><h2>{detail.theme.canonicalName}</h2><p>{detail.totalStocks} 只股票 · {detail.consensusStocks} 只获得多源确认 · {detail.attributionReady} 只已完成归因</p><div className="concept-detail-actions"><div className="concept-horizon" aria-label="归因窗口">{([20, 60, 120] as const).map(days => <button type="button" className={horizonDays === days ? 'is-active' : ''} onClick={() => setHorizonDays(days)} key={days}>{days}日</button>)}</div><button type="button" className={compareThemes.some(item => item.theme.canonicalName === detail.theme.canonicalName) ? 'is-active' : ''} onClick={() => toggleCompare(detail)}><Scale />{compareThemes.some(item => item.theme.canonicalName === detail.theme.canonicalName) ? '移出对照' : '加入对照'}</button><button type="button" onClick={() => void copyThemeLink()}><Link2 />复制研究链接</button><button type="button" onClick={() => void conceptThemesApi.exportTheme(detail.theme.id, horizonDays)}><Download />导出 CSV</button></div></header>
            <section className="concept-consensus-meter"><ScoreRing value={detail.totalStocks ? detail.consensusStocks / detail.totalStocks * 100 : 0} label="多源率" /><div><strong>市场共识不是 AI 猜测</strong><p>来源成分表独立保留；相同规范题材下合并投票，文字入选原因单独进入业务相关性评分。</p></div></section>
            <ThemeHistoryPulse value={detail.history} />
            {detail.institutionCorpus ? <InstitutionCorpusPulse value={detail.institutionCorpus} /> : null}
            {detail.relatedThemes ? <ThemeAffinityMap value={detail.relatedThemes} onTheme={setQuery} /> : null}
            <section className="concept-consensus-spectrum"><div><span>强共识 · 3+源</span><b>{consensusDistribution.strong}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.strong / detail.totalStocks * 100 : 0}%` }} /></div><div><span>交叉确认 · 2源</span><b>{consensusDistribution.confirmed}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.confirmed / detail.totalStocks * 100 : 0}%` }} /></div><div><span>单源待核验</span><b>{consensusDistribution.singleSource}</b><i style={{ width: `${detail.totalStocks ? consensusDistribution.singleSource / detail.totalStocks * 100 : 0}%` }} /></div></section>
            <SourceRail themes={detail.sourceNodes} />
            <ConsensusMatrix stocks={detail.stocks} />
            <BetaAlphaMap stocks={detail.stocks} horizonDays={horizonDays} />
            <ThemeResearchQueue stocks={detail.stocks} onOpen={tsCode => void openStock(tsCode)} />
            <div className="concept-stock-table-head"><span>成分股 / 权重 / Beta / {horizonDays}日 Alpha</span><span>点击展开股票题材透镜</span></div>
            <div className="concept-stock-tools"><label><Search /><input value={stockQuery} onChange={event => setStockQuery(event.target.value)} placeholder="搜索成分股名称或代码" /></label><button type="button" className={watchlistOnly ? 'is-active' : ''} disabled={!detail.watchlistStocks?.length} onClick={() => setWatchlistOnly(value => !value)}><Star />我的自选 {detail.watchlistStocks?.length || 0}</button><select value={stockSort} onChange={event => setStockSort(event.target.value as typeof stockSort)} aria-label="成分股排序"><option value="weight">题材权重</option><option value="beta">Beta弹性</option><option value="alpha">Alpha排序</option><option value="name">名称</option></select></div>
            <div className="concept-stock-table">{visibleStocks.map(item => <StockRow key={item.tsCode} item={item} selected={stockLens?.tsCode === item.tsCode} onClick={() => void openStock(item.tsCode)} />)}</div>
            {filteredStocks.length > stockPageSize ? <nav className="concept-pagination concept-stock-pages" aria-label="成分股分页"><button type="button" disabled={stockPage <= 1} onClick={() => setStockPage(value => Math.max(1, value - 1))}>上一页</button><span>{stockPage} / {Math.ceil(filteredStocks.length / stockPageSize)} · {filteredStocks.length}股</span><button type="button" disabled={stockPage >= Math.ceil(filteredStocks.length / stockPageSize)} onClick={() => setStockPage(value => value + 1)}>下一页</button></nav> : null}
          </> : <div className="concept-detail-empty"><CircleDot className={detailLoading ? 'is-loading' : ''} /><h2>{detailLoading ? '正在组装题材共识' : '选择一个题材'}</h2><p>{detailLoading ? '系统正在对齐多源成分股并计算当前窗口 Beta/Alpha，其他区域可继续使用。' : '这里将展开各来源节点、成分股共识、可解释权重和收益归因。'}</p></div>}
        </aside>
      </div>

      <section className="concept-compare">
        <header><div><span><Scale /> THEME COMPARISON</span><h2>题材对照台</h2></div><div className="concept-compare-actions"><p>{comparison ? `${comparison.left.theme.canonicalName} × ${comparison.right.theme.canonicalName}` : `已选 ${compareThemes.length}/2 · 在右侧题材详情点击“加入对照”`}</p>{comparison ? <button type="button" onClick={() => exportThemeComparison(comparison.left, comparison.right, horizonDays)}><Download />导出对照 CSV</button> : null}</div></header>
        {comparison ? <div className="concept-compare-grid">
          {[comparison.left, comparison.right].map((item, index) => { const lastPoint = item.history?.points.at(-1); return <article key={item.theme.canonicalName}><span>{index ? 'B' : 'A'} · {item.theme.family}</span><h3>{item.theme.canonicalName}</h3><dl><div><dt>全部成分</dt><dd>{item.totalStocks}</dd></div><div><dt>多源确认</dt><dd>{item.consensusStocks}</dd></div><div><dt>归因完成</dt><dd>{item.attributionReady}</dd></div><div><dt>来源节点</dt><dd>{item.sourceNodes.length}</dd></div><div><dt>最新日涨跌</dt><dd data-tone={(lastPoint?.pctChange ?? 0) >= 0 ? 'up' : 'down'}>{signed(lastPoint?.pctChange)}</dd></div><div><dt>{item.history?.availableDates || 0}日累计</dt><dd data-tone={(item.history?.cumulativeReturn ?? 0) >= 0 ? 'up' : 'down'}>{signed(item.history?.cumulativeReturn)}</dd></div><div><dt>多源确认率</dt><dd>{item.totalStocks ? metric(item.consensusStocks / item.totalStocks * 100, 1) : '—'}%</dd></div><div><dt>归因覆盖率</dt><dd>{item.totalStocks ? metric(item.attributionReady / item.totalStocks * 100, 1) : '—'}%</dd></div></dl><p>独占高权重 · {(index ? comparison.rightExclusive : comparison.leftExclusive).map(stock => stock.name).join('、') || '暂无'}</p></article>; })}
          <aside><strong>{comparison.similarity.toFixed(1)}%</strong><span>成分 Jaccard 相似度</span><b>{comparison.shared.length} 只共同成分</b><p>{comparison.shared.slice(0, 12).map(item => item.name).join('、') || '没有共同成分'}</p></aside>
        </div> : <div className="concept-compare-empty"><Scale /><p>对照不是只比涨跌：系统会比较成分重合、市场共识、归因覆盖和双方独占的高权重股票。</p></div>}
      </section>
      </> : null}

      {activeSection === 'decision' ? <TradeDecisionWorkbench overview={overview} leaders={leaders} lifecycle={lifecycle} watchlistMap={watchlistMap} onOpen={tsCode => void openStock(tsCode)} /> : null}

      {activeSection === 'overview' ? <section className="concept-method">
        <header><BrainCircuit /><div><span>ATTRIBUTION METHOD</span><h2>Beta 与 Alpha 如何被计算</h2></div></header>
        <div className="concept-method__flow"><div><Boxes /><strong>来源共识</strong><p>同花顺、东财、开盘啦、通达信、申万独立投票</p></div><ArrowRight /><div><Target /><strong>题材权重</strong><p>36%共识 + 29%业务证据 + 20%热度 + 15%专属性</p></div><ArrowRight /><div><TrendingUp /><strong>题材 Beta</strong><p>剔除个股自身后的题材组合，同时控制沪深300</p></div><ArrowRight /><div><Activity /><strong>独特 Alpha</strong><p>统计窗口残差与公司独有事实证据分层展示</p></div></div>
        <p className="concept-method__formula">{overview?.methodology.betaFormula}</p>
        <small>{overview?.methodology.licenseNote}</small>
      </section> : null}
    </div>
      {stockLens ? <StockLensPanel value={stockLens} onClose={() => setStockLens(null)} onExport={() => void conceptThemesApi.exportStock(stockLens.tsCode, horizonDays)} /> : null}
      {institutionCandidate ? <InstitutionCandidatePanel item={institutionCandidate} onClose={() => setInstitutionCandidate(null)} onTheme={openThemeInCatalog} onOpen={tsCode => { setInstitutionCandidate(null); void openStock(tsCode); }} /> : null}
  </AppPage>;
}
