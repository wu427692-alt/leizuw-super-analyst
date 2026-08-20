import { useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink, Play, Save, ShieldCheck } from 'lucide-react';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { essayQuantApi } from '../api/essayQuant';
import { AppPage, EmptyState } from '../components/common';
import type { EssayQuantDashboard, EssayQuantRule, QuantMetric } from '../types/essayQuant';

const DEFAULT_RULE: EssayQuantRule = {
  name: '小作文多头事件策略', sourceQuery: '', signalDirection: 'bullish', lookbackDays: 365,
  holdingPeriods: [5, 10, 20], firstMentionOnly: false, firstMentionWindowDays: 180,
  minImportance: 60, minConfidence: 0.5, benchmarkCode: '000300.SH', portfolioSize: 10, enabled: true,
};

const n = (value?: number | null, digits = 1) => value == null ? '—' : value.toFixed(digits);
const pct = (value?: number | null, digits = 1) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
const tone = (value?: number | null) => value == null ? 'text-[#62666D]' : value >= 0 ? 'text-[#027A48]' : 'text-[#B42318]';

function MetricCell({ label, value, note, valueClass = '' }: { label: string; value: string | number; note: string; valueClass?: string }) {
  return <div className="border-r border-[#D8DADF] bg-white px-5 py-4 last:border-r-0"><p className="text-xs font-semibold text-[#62666D]">{label}</p><p className={`mt-2 font-mono text-3xl font-bold tracking-[-0.04em] ${valueClass}`}>{value}</p><p className="mt-1 text-[10px] text-[#7B7F87]">{note}</p></div>;
}

function SourceLink({ url }: { url: string }) {
  return <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-semibold text-[#155EEF] hover:underline">原文<ExternalLink className="h-3 w-3" /></a>;
}

export default function EssayQuantPage() {
  const [data, setData] = useState<EssayQuantDashboard | null>(null);
  const [rules, setRules] = useState<EssayQuantRule[]>([]);
  const [rule, setRule] = useState<EssayQuantRule>(DEFAULT_RULE);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    setLoading(true); setMessage('');
    try {
      const [dashboard, saved] = await Promise.all([essayQuantApi.dashboard(), essayQuantApi.rules()]);
      setData(dashboard); setRules(saved.items);
      if (saved.items.length) setRule(saved.items[0]); else if (dashboard.rule) setRule({ ...DEFAULT_RULE, ...dashboard.rule });
    } catch (error) { setMessage(error instanceof Error ? error.message : '量化数据加载失败'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); document.title = '量化回测与数据利用 - DSA'; }, [load]);

  const save = async () => {
    setRunning(true); setMessage('');
    try { const draft = !rule.id && rule.sourceQuery ? { ...rule, name: `${rule.sourceQuery}跟踪策略` } : rule; const saved = await essayQuantApi.saveRule(draft); setRule(saved); setRules(current => [saved, ...current.filter(item => item.id !== saved.id)]); setMessage('规则已保存'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '规则保存失败'); }
    finally { setRunning(false); }
  };
  const run = async () => {
    setRunning(true); setMessage('正在补取命中股票的 Tushare 日线与复权因子…');
    try { const result = await essayQuantApi.run(rule); setData(result); setMessage(`回测完成：${result.summary.matureEventCount} 个到期样本`); }
    catch (error) { setMessage(error instanceof Error ? error.message : '回测失败'); }
    finally { setRunning(false); }
  };

  const metric = (period: number): QuantMetric | undefined => data?.summary.metrics.find(item => item.period === period);
  const excessMetric = (period: number): QuantMetric | undefined => data?.summary.excessMetrics.find(item => item.period === period);
  const primaryPeriod = Math.max(...(rule.holdingPeriods.length ? rule.holdingPeriods : [20]));
  const portfolioChart = useMemo(() => data?.portfolio.curve.map(item => ({ ...item, value: (item.value - 1) * 100 })) ?? [], [data]);

  return <AppPage className="max-w-[1720px] px-3 pb-8 pt-3 md:px-4 lg:px-5">
    <div className="overflow-hidden border border-[#C9CCD2] bg-[#F7F7F8] text-[#17181A] shadow-[0_18px_50px_rgba(17,24,39,0.08)]">
      <header className="flex flex-col justify-between gap-4 border-b border-[#17181A] bg-white px-5 py-5 lg:flex-row lg:items-center">
        <div><h1 className="text-3xl font-bold tracking-[-0.04em]">量化回测与数据利用</h1><p className="mt-2 text-sm text-[#62666D]">把小作文观点变成可复算的事件策略；未走满持有期的样本不计入胜率。</p></div>
        <div className="flex flex-wrap gap-2"><select aria-label="已保存规则" value={rule.id ?? ''} onChange={e => { const selected = rules.find(item => item.id === Number(e.target.value)); setRule(selected ?? { ...DEFAULT_RULE }); }} className="h-10 border border-[#C9CCD2] bg-white px-3 text-xs"><option value="">新建规则</option>{rules.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="inline-flex h-10 items-center gap-2 border border-[#C9CCD2] bg-white px-4 text-xs font-semibold" disabled={running} onClick={() => void save()}><Save className="h-4 w-4" />保存规则</button><button className="inline-flex h-10 items-center gap-2 bg-[#155EEF] px-5 text-xs font-semibold text-white disabled:opacity-50" disabled={running || !rule.holdingPeriods.length} onClick={() => void run()}><Play className="h-4 w-4" />{running ? '运行中…' : '运行回测'}</button></div>
      </header>

      <section className="grid gap-px border-b border-[#C9CCD2] bg-[#D8DADF] xl:grid-cols-[1.35fr_.75fr_.65fr_1.25fr_.65fr_.75fr]">
        <label className="bg-white p-3"><span className="text-[10px] font-semibold text-[#62666D]">研究组 / 券商关键词</span><input value={rule.sourceQuery} onChange={e => setRule({ ...rule, sourceQuery: e.target.value })} placeholder="如：中信电子组" className="mt-1 h-9 w-full border border-[#C9CCD2] px-3 text-xs outline-none focus:border-[#155EEF]" /></label>
        <label className="bg-white p-3"><span className="text-[10px] font-semibold text-[#62666D]">信号方向</span><select value={rule.signalDirection} onChange={e => setRule({ ...rule, signalDirection: e.target.value as EssayQuantRule['signalDirection'] })} className="mt-1 h-9 w-full border border-[#C9CCD2] bg-white px-2 text-xs"><option value="bullish">看多</option><option value="bearish">看空</option><option value="all">全部</option></select></label>
        <label className="bg-white p-3"><span className="text-[10px] font-semibold text-[#62666D]">回看周期</span><select value={rule.lookbackDays} onChange={e => setRule({ ...rule, lookbackDays: Number(e.target.value) })} className="mt-1 h-9 w-full border border-[#C9CCD2] bg-white px-2 text-xs"><option value={90}>近90日</option><option value={180}>近180日</option><option value={365}>近1年</option><option value={730}>近2年</option></select></label>
        <fieldset className="bg-white p-3"><legend className="text-[10px] font-semibold text-[#62666D]">持有期</legend><div className="mt-3 flex gap-4">{[5, 10, 20].map(period => <label key={period} className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={rule.holdingPeriods.includes(period)} onChange={e => setRule({ ...rule, holdingPeriods: e.target.checked ? [...rule.holdingPeriods, period].sort((a,b)=>a-b) : rule.holdingPeriods.filter(item => item !== period) })} />{period}日</label>)}</div></fieldset>
        <label className="bg-white p-3"><span className="text-[10px] font-semibold text-[#62666D]">首次提及</span><span className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={rule.firstMentionOnly} onChange={e => setRule({ ...rule, firstMentionOnly: e.target.checked })} />只看首次</span></label>
        <label className="bg-white p-3"><span className="text-[10px] font-semibold text-[#62666D]">组合数量</span><select value={rule.portfolioSize} onChange={e => setRule({ ...rule, portfolioSize: Number(e.target.value) })} className="mt-1 h-9 w-full border border-[#C9CCD2] bg-white px-2 text-xs">{[5,10,15,20].map(size => <option key={size} value={size}>前{size}只</option>)}</select></label>
      </section>

      {message ? <div className="border-b border-[#C9CCD2] bg-[#EEF4FF] px-5 py-2 text-xs text-[#155EEF]">{message}</div> : null}
      <section className="grid border-b border-[#C9CCD2] sm:grid-cols-2 xl:grid-cols-4">
        <MetricCell label="样本数（事件）" value={data?.summary.eventCount ?? '—'} note={`到期样本 ${data?.summary.matureEventCount ?? 0}`} />
        <MetricCell label="5日胜率" value={pct(metric(5)?.winRate)} note={`样本量 ${metric(5)?.sampleCount ?? 0}`} valueClass={tone(metric(5)?.averageReturn)} />
        <MetricCell label={`${primaryPeriod}日平均超额收益`} value={pct(excessMetric(primaryPeriod)?.averageReturn, 2)} note={`相对 ${data?.dataQuality.benchmark ?? rule.benchmarkCode}`} valueClass={tone(excessMetric(primaryPeriod)?.averageReturn)} />
        <MetricCell label="覆盖股票（去重）" value={data?.summary.coveredStockCount ?? '—'} note={`近30日首次提及 ${data?.summary.firstMention30dCount ?? 0}`} />
      </section>

      <section className="grid border-b border-[#C9CCD2] xl:grid-cols-[1.55fr_1fr]">
        <div className="min-w-0 bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><h2 className="text-lg font-bold">事件发生后平均累计收益</h2><p className="mt-1 text-xs text-[#62666D]">横轴为事件后交易日；每个点只使用已具备该日行情的样本。</p><div className="mt-4 h-72 min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={288} initialDimension={{ width: 700, height: 288 }}><LineChart data={data?.eventCurve ?? []}><CartesianGrid stroke="#E5E7EB" vertical={false}/><XAxis dataKey="day" tick={{fontSize:10}}/><YAxis tick={{fontSize:10}} tickFormatter={v=>`${v}%`}/><Tooltip formatter={(v)=>`${n(Number(v),2)}%`}/><Legend/><Line type="monotone" dataKey="strategy" name="事件策略" stroke="#155EEF" strokeWidth={2} dot={false}/><Line type="monotone" dataKey="benchmark" name="沪深300" stroke="#7B7F87" strokeWidth={1.5} dot={false}/></LineChart></ResponsiveContainer></div></div>
        <div className="bg-[#EEF2F7] p-5"><div className="flex items-center justify-between"><h2 className="text-lg font-bold">策略定义与数据质量</h2><ShieldCheck className="h-5 w-5 text-[#155EEF]" /></div><dl className="mt-5 grid grid-cols-[88px_1fr] gap-x-3 gap-y-3 text-xs"><dt className="text-[#62666D]">事件定义</dt><dd>{rule.sourceQuery || '全部研究组'} · {rule.signalDirection === 'bullish' ? '看多观点' : rule.signalDirection === 'bearish' ? '看空观点' : '全部观点'}</dd><dt className="text-[#62666D]">买入价格</dt><dd>{data?.dataQuality.entryRule ?? '事件后首个交易日开盘'}</dd><dt className="text-[#62666D]">卖出价格</dt><dd>{data?.dataQuality.exitRule ?? '第N个交易日收盘'}</dd><dt className="text-[#62666D]">价格口径</dt><dd>{data?.dataQuality.priceBasis ?? '—'}</dd><dt className="text-[#62666D]">数据截止</dt><dd>{data?.dataQuality.priceCutoff ?? '—'}</dd><dt className="text-[#62666D]">样本约束</dt><dd>{data?.dataQuality.survivorshipNote ?? '—'}</dd><dt className="text-[#62666D]">排名校正</dt><dd>{data?.dataQuality.rankingNote ?? '—'}</dd></dl>{data?.dataQuality.warnings.length ? <ul className="mt-5 border-l-2 border-[#B54708] pl-3 text-xs text-[#B54708]">{data.dataQuality.warnings.map(item=><li key={item}>{item}</li>)}</ul> : null}</div>
      </section>

      <section className="grid border-b border-[#C9CCD2] xl:grid-cols-[1fr_1.15fr_.9fr]">
        <div className="overflow-hidden bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><h2 className="text-lg font-bold">券商 / 研究组胜率排行</h2><p className="mt-1 text-xs text-[#62666D]">按{primaryPeriod}日到期样本；至少3个样本进入主排名，原始胜率仍展示。</p><div className="mt-4 overflow-auto"><table className="w-full text-left text-xs"><thead className="border-y border-[#D8DADF] text-[#62666D]"><tr><th className="py-2">研究组</th><th>样本</th><th>原胜率</th><th>收缩胜率</th><th>超额</th></tr></thead><tbody>{data?.researchGroupRankings.slice(0,10).map(row=><tr key={row.researchGroup} className="border-b border-[#E5E7EB]"><td className="max-w-[150px] truncate py-2 font-semibold">{row.researchGroup}{row.rankEligible === false ? <span className="ml-1 font-normal text-[#B54708]">样本不足</span> : null}</td><td>{row.matureCount}</td><td>{pct(row.winRate)}</td><td>{pct(row.adjustedWinRate)}</td><td className={tone(row.averageExcessReturn)}>{pct(row.averageExcessReturn,2)}</td></tr>)}</tbody></table></div></div>
        <div className="overflow-hidden bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><h2 className="text-lg font-bold">近30日首次提及</h2><p className="mt-1 text-xs text-[#62666D]">此前 {rule.firstMentionWindowDays} 日无同股观点才计为首次。</p><div className="mt-4 overflow-auto"><table className="w-full text-left text-xs"><thead className="border-y border-[#D8DADF] text-[#62666D]"><tr><th className="py-2">日期 / 股票</th><th>研究组</th><th>观点</th><th>原文</th></tr></thead><tbody>{data?.firstMentions30d.slice(0,10).map(row=><tr key={`${row.topicId}-${row.symbol}`} className="border-b border-[#E5E7EB]"><td className="py-2"><p className="font-semibold">{row.stockName}</p><p className="font-mono text-[10px] text-[#62666D]">{row.eventAt.slice(5,10)} · {row.symbol}</p></td><td className="max-w-[120px] truncate">{row.researchGroup}</td><td className="max-w-[160px] truncate">{row.summary}</td><td><SourceLink url={row.url}/></td></tr>)}</tbody></table>{!loading&&!data?.firstMentions30d.length?<EmptyState title="近30日无首次提及" description="可扩大回看周期或降低重要度门槛。"/>:null}</div></div>
        <div className="min-w-0 bg-white p-5"><h2 className="text-lg font-bold">吹票强度 × 后续走势</h2><p className="mt-1 text-xs text-[#62666D]">强度由观点词、重要度和信息增量构成，不代表事实。</p><div className="mt-4 h-64 min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={256} initialDimension={{width:380,height:256}}><BarChart data={data?.hypeAnalysis ?? []}><CartesianGrid stroke="#E5E7EB" vertical={false}/><XAxis dataKey="level" tick={{fontSize:10}}/><YAxis tick={{fontSize:10}} tickFormatter={v=>`${v}%`}/><Tooltip/><Legend/><Bar dataKey="averageReturn" name={`${primaryPeriod}日平均收益`} fill="#155EEF"/><Bar dataKey="winRate" name="胜率" fill="#98A2B3"/></BarChart></ResponsiveContainer></div></div>
      </section>

      <section className="grid xl:grid-cols-[1fr_1.35fr]">
        <div className="overflow-hidden bg-white p-5 xl:border-r xl:border-[#C9CCD2]"><h2 className="text-lg font-bold">趋势买点信号</h2><p className="mt-1 text-xs text-[#62666D]">首次提及、观点热度、MA5/MA20 与20日动量联合打分。</p><div className="mt-4 overflow-auto"><table className="w-full text-left text-xs"><thead className="border-y border-[#D8DADF] text-[#62666D]"><tr><th className="py-2">股票</th><th>研究组</th><th>强度</th><th>20日动量</th><th>原文</th></tr></thead><tbody>{data?.trendSignals.slice(0,12).map(row=><tr key={row.symbol} className="border-b border-[#E5E7EB]"><td className="py-2"><p className="font-semibold">{row.stockName}</p><p className="font-mono text-[10px] text-[#62666D]">{row.symbol}</p></td><td className="max-w-[120px] truncate">{row.researchGroup}</td><td className="font-mono">{row.signalStrength}/5</td><td className={tone(row.momentum20d)}>{pct(row.momentum20d,2)}</td><td><SourceLink url={row.url}/></td></tr>)}</tbody></table></div></div>
        <div className="grid min-w-0 bg-[#F7F7F8] lg:grid-cols-[.8fr_1.2fr]"><div className="p-5 lg:border-r lg:border-[#C9CCD2]"><h2 className="text-lg font-bold">排名靠前规则的等权组合</h2><p className="mt-1 text-xs text-[#62666D]">仅为研究样本，不自动下单。</p><div className="mt-5 grid grid-cols-3 gap-3"><div><p className="text-[10px] text-[#62666D]">年化收益</p><p className={`mt-1 font-mono text-xl font-bold ${tone(data?.portfolio.annualizedReturn)}`}>{pct(data?.portfolio.annualizedReturn,2)}</p></div><div><p className="text-[10px] text-[#62666D]">最大回撤</p><p className="mt-1 font-mono text-xl font-bold text-[#B42318]">{pct(data?.portfolio.maxDrawdown,2)}</p></div><div><p className="text-[10px] text-[#62666D]">日胜率</p><p className="mt-1 font-mono text-xl font-bold">{pct(data?.portfolio.winRate)}</p></div></div><div className="mt-5 divide-y divide-[#D8DADF] border-y border-[#D8DADF]">{data?.portfolio.components.map(item=><div key={item.symbol} className="flex items-center justify-between py-2 text-xs"><div><p className="font-semibold">{item.stockName}</p><p className="font-mono text-[10px] text-[#62666D]">{item.symbol} · {item.researchGroup}</p></div><span className="font-mono">{n(item.weight,1)}%</span></div>)}</div></div><div className="min-w-0 p-5"><h3 className="text-sm font-bold">组合净值走势</h3><div className="mt-4 h-64 min-w-0"><ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={256} initialDimension={{width:560,height:256}}><LineChart data={portfolioChart}><CartesianGrid stroke="#E5E7EB" vertical={false}/><XAxis dataKey="date" tick={{fontSize:9}} tickFormatter={v=>String(v).slice(5)}/><YAxis tick={{fontSize:10}} tickFormatter={v=>`${n(v,0)}%`}/><Tooltip formatter={v=>`${n(Number(v),2)}%`}/><Line type="monotone" dataKey="value" name="等权组合" stroke="#155EEF" strokeWidth={2} dot={false}/></LineChart></ResponsiveContainer></div></div></div>
      </section>
      <footer className="border-t border-[#C9CCD2] bg-white px-5 py-3 text-[10px] text-[#62666D]">免责声明：本页是对历史公开观点和行情的事件研究，不构成投资建议。停牌、涨跌停无法成交、交易成本和复权覆盖会影响真实结果。</footer>
    </div>
  </AppPage>;
}
