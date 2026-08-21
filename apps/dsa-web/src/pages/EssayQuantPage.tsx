import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  Activity, Archive, BarChart3, Blocks, Bot, Braces, CheckCircle2, ChevronRight,
  CircleDollarSign, ClipboardCheck, Database, FileSearch, FlaskConical, History,
  Layers3, LineChart as LineChartIcon, LoaderCircle, Play, RadioTower, Save,
  ShieldCheck, SlidersHorizontal, Sparkles, Target, TriangleAlert,
} from 'lucide-react';
import {
  Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { essayQuantApi } from '../api/essayQuant';
import { AppPage, EmptyState } from '../components/common';
import type {
  EssayQuantCatalog, EssayQuantDashboard, EssayQuantPlan, EssayQuantPrecomputeStatus,
  EssayQuantRule, EssayQuantRunHistory, QuantMetric,
} from '../types/essayQuant';
import './EssayQuantPage.css';

const DEFAULT_RULE: EssayQuantRule = {
  name: 'AI 已分析小作文事件策略', sourceQuery: '', signalDirection: 'bullish', lookbackDays: 365,
  holdingPeriods: [5, 10, 20], firstMentionOnly: false, firstMentionWindowDays: 180,
  minImportance: 60, minConfidence: 0.5, benchmarkCode: '000300.SH', portfolioSize: 10,
  enabled: true, strategyType: 'essay_event', rawNotePolicy: 'exclude', dedupeWindowDays: 3,
  transactionCostBps: 12, validationMethod: 'walk_forward',
};

const NAV = [
  ['overview', '研究总览', Activity], ['builder', '策略工坊', SlidersHorizontal],
  ['natural-language', '自然语言回测', Bot], ['results', '结果实验室', BarChart3],
  ['data', '数据资产', Database], ['history', '运行历史', History],
] as const;
const PIPELINE = [
  ['语料与事实源', '连接小作文、研报、公告、行情和财务', Database],
  ['因子构建', '把方向、热度、财务和行情转为可验证信号', Blocks],
  ['样本与交易约束', '去重、时间切分、流动性与成本', ClipboardCheck],
  ['回测执行', '事件窗、组合净值、交易明细', Play],
  ['稳健性与归因', '置信区间、敏感性与分组稳定性', LineChartIcon],
] as const;
const EXAMPLES = [
  '研究近30日首次被两家以上机构提及的股票，持有20日，相对沪深300计算超额收益。',
  '只看中信电子组近一年重要度70以上的看多观点，去重后测试5日、10日和20日表现。',
  '检验高热度小作文是否反而跑输，排除未做AI分析的历史原文，加入20bp交易成本。',
];

const n = (value?: number | null, digits = 1) => value == null ? '—' : value.toFixed(digits);
const pct = (value?: number | null, digits = 1) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
const compact = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const tone = (value?: number | null) => value == null ? 'quant-muted' : value >= 0 ? 'quant-positive' : 'quant-negative';

function SectionTitle({ icon: Icon, title, note, aside }: { icon: typeof Activity; title: string; note?: string; aside?: React.ReactNode }) {
  return <div className="quant-section-head"><div className="quant-section-title"><Icon aria-hidden="true" /><div><h2>{title}</h2>{note ? <p>{note}</p> : null}</div></div>{aside}</div>;
}
function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={ok ? 'quant-status is-ok' : 'quant-status'}>{ok ? <CheckCircle2 /> : <LoaderCircle />}{children}</span>;
}
function LoadingScreen({ progress }: { progress: number }) {
  return <div className="quant-loading" role="status"><RadioTower /><h1>正在装载量化研究工作台</h1><p>读取研究方法、真实数据资产、最近运行和后台状态</p><div><span style={{ width: `${progress}%` }} /></div><strong>{progress}%</strong></div>;
}

export default function EssayQuantPage() {
  const location = useLocation(); const navigate = useNavigate();
  const active = (new URLSearchParams(location.search).get('section') || location.pathname.split('/')[2] || 'overview') as typeof NAV[number][0];
  const [data, setData] = useState<EssayQuantDashboard | null>(null);
  const [catalog, setCatalog] = useState<EssayQuantCatalog | null>(null);
  const [history, setHistory] = useState<EssayQuantRunHistory>({ items: [], total: 0 });
  const [rules, setRules] = useState<EssayQuantRule[]>([]);
  const [rule, setRule] = useState<EssayQuantRule>(DEFAULT_RULE);
  const [precompute, setPrecompute] = useState<EssayQuantPrecomputeStatus | null>(null);
  const [loading, setLoading] = useState(true); const [progress, setProgress] = useState(12);
  const [running, setRunning] = useState(false); const [message, setMessage] = useState('');
  const [prompt, setPrompt] = useState(EXAMPLES[0]); const [plan, setPlan] = useState<EssayQuantPlan | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setProgress(18); setMessage('');
    const jobs = [essayQuantApi.dashboard(), essayQuantApi.catalog(), essayQuantApi.runs(), essayQuantApi.rules(), essayQuantApi.precomputeStatus()] as const;
    const timer = window.setInterval(() => setProgress(current => Math.min(88, current + 7)), 140);
    const settled = await Promise.allSettled(jobs); window.clearInterval(timer); setProgress(100);
    const [dashboardJob, catalogJob, historyJob, rulesJob, precomputeJob] = settled;
    if (dashboardJob.status === 'fulfilled') { setData(dashboardJob.value); setRule({ ...DEFAULT_RULE, ...dashboardJob.value.rule }); }
    if (catalogJob.status === 'fulfilled') setCatalog(catalogJob.value);
    if (historyJob.status === 'fulfilled') setHistory(historyJob.value);
    if (rulesJob.status === 'fulfilled') setRules(rulesJob.value.items);
    if (precomputeJob.status === 'fulfilled') setPrecompute(precomputeJob.value);
    const failed = settled.filter(item => item.status === 'rejected').length;
    if (failed) setMessage(`${failed} 个后台模块尚未就绪，页面已保留其余真实数据；可稍后重试。`);
    window.setTimeout(() => setLoading(false), 160);
  }, []);
  useEffect(() => { void load(); document.title = '量化回测与数据利用 - DSA'; }, [load]);

  const run = async (target = rule) => {
    setRunning(true); setMessage('正在补齐行情、聚类事件并执行稳健性检验…');
    try { const result = await essayQuantApi.run(target); setData(result); setRule({ ...DEFAULT_RULE, ...result.rule }); setHistory(await essayQuantApi.runs()); navigate('/essay-quant?section=results'); setMessage(`研究完成：${result.summary.matureEventCount} 个到期有效样本，结果已保存。`); }
    catch (error) { setMessage(error instanceof Error ? error.message : '回测执行失败'); }
    finally { setRunning(false); }
  };
  const save = async () => {
    setRunning(true);
    try { const saved = await essayQuantApi.saveRule(rule); setRule(saved); setRules(current => [saved, ...current.filter(item => item.id !== saved.id)]); setMessage('研究规则已保存'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setRunning(false); }
  };
  const generatePlan = async () => {
    setRunning(true); setPlan(null); setMessage('DeepSeek 正在把需求拆成信号、样本、成本和验证任务…');
    try { const result = await essayQuantApi.plan(prompt); setPlan(result); setRule({ ...DEFAULT_RULE, ...result.rule }); setMessage('研究方案已生成，请检查假设和安全边界后再执行。'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '研究方案生成失败'); }
    finally { setRunning(false); }
  };
  const executePlan = async () => {
    if (!plan) return; setRunning(true); setMessage('已确认方案，正在安全研究引擎中执行…');
    try { const result = await essayQuantApi.executePlan(plan.rule); setData(result); setHistory(await essayQuantApi.runs()); navigate('/essay-quant?section=results'); setMessage('自然语言研究任务执行完成。'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '方案执行失败'); }
    finally { setRunning(false); }
  };
  const primary = Math.max(...(rule.holdingPeriods.length ? rule.holdingPeriods : [20]));
  const metric = (period: number, excess = false): QuantMetric | undefined => (excess ? data?.summary.excessMetrics : data?.summary.metrics)?.find(item => item.period === period);
  const portfolioChart = useMemo(() => { let peak = 0; return (data?.portfolio.curve ?? []).map(item => { const value = (item.value - 1) * 100; peak = Math.max(peak, item.value); return { ...item, value, drawdown: peak ? (item.value / peak - 1) * 100 : 0 }; }); }, [data]);

  if (loading) return <AppPage className="aurora-workbench max-w-[1720px] px-3 py-3"><LoadingScreen progress={progress} /></AppPage>;
  return <AppPage className="aurora-workbench max-w-[1760px] px-2 pb-8 pt-2 md:px-3"><div className="essay-quant-terminal">
    <header className="quant-header"><div><span className="quant-eyebrow"><FlaskConical /> QUANT RESEARCH OS</span><h1>量化回测与数据利用</h1><p>把非结构化情报、行情、基本面和资金数据变成可复现、可解释、可质疑的研究结果。</p></div><div className="quant-header-actions"><Status ok={!precompute?.dirty && !precompute?.computing}>{precompute?.computing ? '机构基线计算中' : precompute?.dirty ? '等待增量重算' : '机构基线已就绪'}</Status><button onClick={() => void load()}><RadioTower />刷新数据</button></div></header>
    <nav className="quant-tabs" aria-label="量化研究栏目">{NAV.map(([key, label, Icon]) => <button key={key} className={active === key ? 'is-active' : ''} onClick={() => navigate(key === 'overview' ? '/essay-quant' : `/essay-quant?section=${key}`)}><Icon />{label}</button>)}</nav>
    {message ? <div className="quant-message" aria-live="polite">{running ? <LoaderCircle className="is-spinning" /> : <Activity />}{message}</div> : null}
    {active === 'overview' && <Overview catalog={catalog} data={data} onMethod={(key) => { setRule(current => ({ ...current, strategyType: key === 'event_study' ? 'essay_event' : key })); navigate('/essay-quant?section=builder'); }} onNatural={() => navigate('/essay-quant?section=natural-language')} />}
    {active === 'builder' && <Builder rule={rule} rules={rules} running={running} setRule={setRule} onRun={() => void run()} onSave={() => void save()} />}
    {active === 'natural-language' && <NaturalLanguage prompt={prompt} setPrompt={setPrompt} plan={plan} running={running} onGenerate={() => void generatePlan()} onExecute={() => void executePlan()} />}
    {active === 'results' && <Results data={data} primary={primary} metric={metric} portfolioChart={portfolioChart} onBuilder={() => navigate('/essay-quant?section=builder')} />}
    {active === 'data' && <DataAssets catalog={catalog} data={data} />}
    {active === 'history' && <RunHistory history={history} />}
    <footer className="quant-footer"><ShieldCheck /> 历史结果不构成投资建议；研究结论必须结合样本外检验、成交约束和数据截止时间解释。</footer>
  </div></AppPage>;
}

function Overview({ catalog, data, onMethod, onNatural }: { catalog: EssayQuantCatalog | null; data: EssayQuantDashboard | null; onMethod: (key: string) => void; onNatural: () => void }) {
  return <main className="quant-page"><section className="quant-pipeline">{PIPELINE.map(([title, note, Icon], index) => <div key={title} className="quant-pipeline-step"><span>{index + 1}</span><Icon /><div><strong>{title}</strong><p>{note}</p><Status ok={index < 2}>{index < 2 ? '数据已连接' : index === 2 ? '等待配置' : '等待运行'}</Status></div>{index < PIPELINE.length - 1 ? <ChevronRight className="quant-pipe-arrow" /> : null}</div>)}</section>
    <div className="quant-overview-grid"><section className="quant-panel quant-methods"><SectionTitle icon={Layers3} title="研究方法" note="每个入口都写清楚目的、使用的数据和输出，不再让你猜按钮用途。" /><div className="quant-method-grid">{(catalog?.methods ?? []).map((method, index) => { const Icon = [Target, Blocks, Sparkles, RadioTower, CircleDollarSign][index] ?? FlaskConical; return <article key={method.key}><Icon /><h3>{method.name}</h3><small>目的</small><p>{method.purpose}</p><small>输出</small><p>{method.output}</p><button onClick={() => onMethod(method.key)}>新建研究<ChevronRight /></button></article>; })}</div></section>
      <section className="quant-panel quant-natural-card"><SectionTitle icon={Bot} title="自然语言回测" note="描述研究想法，模型负责拆任务；执行仍走安全白名单引擎。" /><blockquote>“筛选近30日首次被机构提及、盈利预期上调的股票，持有20日并计算超额收益。”</blockquote><ol><li>生成结构化假设与规则</li><li>显示受约束可复现代码</li><li>确认后执行并保存快照</li></ol><button className="quant-primary" onClick={onNatural}><Sparkles />打开自然语言工作台</button></section>
      <section className="quant-panel quant-assets-preview"><SectionTitle icon={Database} title="数据资产实况" note="以下数量和时间直接读取本地数据库。" aside={<Status ok>{catalog?.assets.filter(item => item.status === 'ready').length ?? 0} 个可用</Status>} /><table><thead><tr><th>数据集</th><th>本地数据</th><th>最近更新</th><th>可用于</th><th>状态</th></tr></thead><tbody>{(catalog?.assets ?? []).map(item => <tr key={item.key}><td>{item.name}</td><td className="mono">{compact(item.count)}</td><td className="mono">{item.latestAt?.slice(0, 16) || '—'}</td><td>{item.usage}</td><td><Status ok={item.status === 'ready'}>{item.status === 'ready' ? '可用' : item.status === 'empty' ? '暂无数据' : '待接入'}</Status></td></tr>)}</tbody></table></section>
      <section className="quant-panel quant-last-run"><SectionTitle icon={BarChart3} title="最近一次研究" note={data?.generatedAt ? `运行于 ${new Date(data.generatedAt).toLocaleString('zh-CN')}` : '等待运行'} />{data ? <div className="quant-mini-results"><div><small>有效事件</small><strong>{data.summary.eventCount}</strong><span>到期 {data.summary.matureEventCount}</span></div><div><small>平均超额</small><strong className={tone(data.robustness?.averageExcessReturn)}>{pct(data.robustness?.averageExcessReturn, 2)}</strong><span>95% CI {n(data.robustness?.confidenceInterval95?.[0], 2)} ~ {n(data.robustness?.confidenceInterval95?.[1], 2)}</span></div><div><small>重复信号过滤</small><strong>{data.dataQuality.duplicateEventCount ?? 0}</strong><span>{data.rule.dedupeWindowDays} 日聚类窗口</span></div><div><small>交易成本</small><strong>{data.rule.transactionCostBps}bp</strong><span>已从个股收益扣除</span></div></div> : <EmptyState title="等待首次运行" description="从研究方法或自然语言回测开始。" />}</section>
    </div></main>;
}

function Builder({ rule, rules, running, setRule, onRun, onSave }: { rule: EssayQuantRule; rules: EssayQuantRule[]; running: boolean; setRule: (rule: EssayQuantRule) => void; onRun: () => void; onSave: () => void }) {
  const update = <K extends keyof EssayQuantRule>(key: K, value: EssayQuantRule[K]) => setRule({ ...rule, [key]: value });
  return <main className="quant-page quant-builder-page"><section className="quant-panel"><SectionTitle icon={SlidersHorizontal} title="策略工坊" note="按信号、样本、交易和验证四步配置；右侧随时显示完整研究定义。" aside={<div className="quant-action-row"><select aria-label="载入保存规则" value={rule.id ?? ''} onChange={event => setRule(rules.find(item => item.id === Number(event.target.value)) ?? DEFAULT_RULE)}><option value="">新建规则</option>{rules.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button onClick={onSave} disabled={running}><Save />保存</button><button className="quant-primary" onClick={onRun} disabled={running}><Play />{running ? '执行中' : '运行研究'}</button></div>} />
    <div className="quant-builder-grid"><div className="quant-form-stack"><fieldset><legend><span>1</span>定义信号</legend><label>研究名称<input value={rule.name} onChange={event => update('name', event.target.value)} /></label><div className="quant-form-row"><label>机构 / 来源关键词<input value={rule.sourceQuery} onChange={event => update('sourceQuery', event.target.value)} placeholder="空白 = 全部来源" /></label><label>观点方向<select value={rule.signalDirection} onChange={event => update('signalDirection', event.target.value as EssayQuantRule['signalDirection'])}><option value="bullish">看多</option><option value="bearish">看空</option><option value="all">全部</option></select></label></div><label className="quant-toggle"><input type="checkbox" checked={rule.rawNotePolicy === 'include'} onChange={event => update('rawNotePolicy', event.target.checked ? 'include' : 'exclude')} /><span><strong>纳入未做 AI 分析的原始语料</strong><small>仅适合探索。默认关闭，避免关键词展开把事件样本放大。</small></span></label></fieldset>
      <fieldset><legend><span>2</span>样本筛选</legend><div className="quant-form-row three"><label>回看周期<select value={rule.lookbackDays} onChange={event => update('lookbackDays', Number(event.target.value))}><option value={90}>90日</option><option value={180}>180日</option><option value={365}>1年</option><option value={730}>2年</option></select></label><label>最低重要度<input type="number" min="0" max="100" value={rule.minImportance} onChange={event => update('minImportance', Number(event.target.value))} /></label><label>最低置信度<input type="number" min="0" max="1" step="0.1" value={rule.minConfidence} onChange={event => update('minConfidence', Number(event.target.value))} /></label></div><div className="quant-form-row"><label>重复信号聚类窗口<input type="number" min="0" max="30" value={rule.dedupeWindowDays} onChange={event => update('dedupeWindowDays', Number(event.target.value))} /><small>同股、同机构、同方向在窗口内只计一次</small></label><label className="quant-toggle compact"><input type="checkbox" checked={rule.firstMentionOnly} onChange={event => update('firstMentionOnly', event.target.checked)} /><span><strong>只看首次提及</strong><small>{rule.firstMentionWindowDays} 日内无同股观点</small></span></label></div></fieldset>
      <fieldset><legend><span>3</span>交易约束</legend><div className="quant-form-row three"><label>持有期<div className="quant-checks">{[5, 10, 20, 30, 60].map(period => <button type="button" key={period} className={rule.holdingPeriods.includes(period) ? 'is-active' : ''} onClick={() => update('holdingPeriods', rule.holdingPeriods.includes(period) ? rule.holdingPeriods.filter(item => item !== period) : [...rule.holdingPeriods, period].sort((a, b) => a - b).slice(0, 3))}>{period}日</button>)}</div></label><label>交易成本（bp）<input type="number" min="0" max="200" value={rule.transactionCostBps} onChange={event => update('transactionCostBps', Number(event.target.value))} /></label><label>组合股票数<input type="number" min="2" max="30" value={rule.portfolioSize} onChange={event => update('portfolioSize', Number(event.target.value))} /></label></div></fieldset>
      <fieldset><legend><span>4</span>验证方式</legend><div className="quant-form-row"><label>市场基准<select value={rule.benchmarkCode} onChange={event => update('benchmarkCode', event.target.value)}><option value="000300.SH">沪深300</option><option value="000905.SH">中证500</option><option value="000852.SH">中证1000</option><option value="000001.SH">上证指数</option></select></label><label>验证方法<select value={rule.validationMethod} onChange={event => update('validationMethod', event.target.value as EssayQuantRule['validationMethod'])}><option value="walk_forward">滚动样本外验证</option><option value="time_split">固定时间切分</option><option value="none">仅全样本探索</option></select></label></div></fieldset></div>
      <aside className="quant-definition"><h3><Braces />研究定义</h3><dl><dt>语料</dt><dd>{rule.rawNotePolicy === 'exclude' ? '仅 AI 已分析有效观点' : 'AI观点 + 原始语料探索层'}</dd><dt>信号</dt><dd>{rule.sourceQuery || '全部机构'} · {rule.signalDirection}</dd><dt>入场 / 出场</dt><dd>事件后首个交易日开盘 / 第 N 日收盘</dd><dt>去重</dt><dd>同股同机构 {rule.dedupeWindowDays} 日聚类</dd><dt>成本</dt><dd>{rule.transactionCostBps}bp</dd><dt>持有期</dt><dd>{rule.holdingPeriods.join('、')} 个交易日</dd><dt>评价</dt><dd>收益、超额、置信区间、月度队列、成本敏感性</dd></dl>{rule.rawNotePolicy === 'include' ? <div className="quant-warning"><TriangleAlert />原始语料未经过观点方向和置信度完整校验，会单独标记为探索证据。</div> : null}</aside></div></section></main>;
}

function NaturalLanguage({ prompt, setPrompt, plan, running, onGenerate, onExecute }: { prompt: string; setPrompt: (value: string) => void; plan: EssayQuantPlan | null; running: boolean; onGenerate: () => void; onExecute: () => void }) {
  return <main className="quant-page quant-nl-page"><section className="quant-panel"><SectionTitle icon={Bot} title="自然语言回测" note="DeepSeek 负责理解需求和生成方案；服务器只执行经过字段校验的白名单任务。" /><div className="quant-nl-grid"><div className="quant-prompt"><label>输入你的研究需求<textarea value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="请说明信号、股票范围、持有期、基准和想验证的问题…" /></label><div className="quant-examples"><span>示例</span>{EXAMPLES.map(item => <button key={item} onClick={() => setPrompt(item)}>{item}</button>)}</div><button className="quant-primary quant-generate" disabled={running || prompt.trim().length < 8} onClick={onGenerate}>{running ? <LoaderCircle className="is-spinning" /> : <Sparkles />}{running ? '正在生成研究方案' : '生成研究方案'}</button></div><aside className="quant-safety"><ShieldCheck /><h3>安全执行边界</h3><p>模型不会直接获得执行器。它只能填写受控研究字段，代码由服务器模板生成。</p><strong>允许</strong><ul><li>读取聚合后的研究数据</li><li>调用量化事件研究引擎</li><li>保存不可变结果快照</li></ul><strong>阻止</strong><ul><li>exec / eval 与任意 Shell</li><li>任意 SQL、文件、密钥和下单</li><li>未授权网络访问</li></ul></aside></div></section>
  {plan ? <section className="quant-panel quant-plan"><SectionTitle icon={ClipboardCheck} title={plan.plan.title} note={plan.plan.hypothesis} aside={<button className="quant-primary" onClick={onExecute} disabled={running}><Play />确认并执行</button>} /><div className="quant-plan-grid"><div><h3>结构化研究任务</h3><dl><dt>股票范围</dt><dd>{plan.plan.universe}</dd><dt>数据源</dt><dd>{plan.plan.signalSources.join(' / ')}</dd><dt>持有期</dt><dd>{plan.rule.holdingPeriods.join('、')} 日</dd><dt>成本 / 去重</dt><dd>{plan.rule.transactionCostBps}bp / {plan.rule.dedupeWindowDays} 日</dd><dt>验证</dt><dd>{plan.rule.validationMethod}</dd></dl><h4>研究假设</h4><ul>{plan.plan.assumptions.length ? plan.plan.assumptions.map(item => <li key={item}>{item}</li>) : <li>模型未补充额外假设</li>}</ul>{plan.plan.unsupportedRequests.length ? <div className="quant-warning"><TriangleAlert /><div><strong>暂不支持</strong>{plan.plan.unsupportedRequests.map(item => <p key={item}>{item}</p>)}</div></div> : null}</div><div className="quant-code"><div><Braces />可复现模板代码 <span>待你确认后执行</span></div><pre>{plan.code}</pre></div></div></section> : null}</main>;
}

function Results({ data, primary, metric, portfolioChart, onBuilder }: { data: EssayQuantDashboard | null; primary: number; metric: (period: number, excess?: boolean) => QuantMetric | undefined; portfolioChart: Array<Record<string, string | number>>; onBuilder: () => void }) {
  if (!data) return <main className="quant-page"><section className="quant-panel"><EmptyState title="还没有回测结果" description="先在策略工坊或自然语言工作台运行一个研究任务。" /><button className="quant-primary centered" onClick={onBuilder}>进入策略工坊</button></section></main>;
  const ci = data.robustness?.confidenceInterval95 ?? [null, null];
  return <main className="quant-page quant-results-page"><section className="quant-kpis"><div><small>有效事件 / 到期</small><strong>{data.summary.eventCount} <span>/ {data.summary.matureEventCount}</span></strong><p>AI语料口径：{data.rule.rawNotePolicy === 'exclude' ? '严格' : '探索'}</p></div><div><small>{primary}日平均超额</small><strong className={tone(data.robustness?.averageExcessReturn)}>{pct(data.robustness?.averageExcessReturn, 2)}</strong><p>95% CI {n(ci[0], 2)}% ~ {n(ci[1], 2)}%</p></div><div><small>t 值 / 盈亏比</small><strong>{n(data.robustness?.tStat, 2)} <span>/ {n(data.robustness?.payoffRatio, 2)}</span></strong><p>正收益率 {pct(data.robustness?.positiveRate)}</p></div><div><small>最大回撤</small><strong className="quant-negative">{pct(data.portfolio.maxDrawdown, 2)}</strong><p>组合年化 {pct(data.portfolio.annualizedReturn, 2)}</p></div><div><small>样本质量</small><strong>{data.dataQuality.duplicateEventCount ?? 0}</strong><p>重复信号已过滤 · 成本 {data.rule.transactionCostBps}bp</p></div></section>
    <div className="quant-result-grid"><section className="quant-panel span-2"><SectionTitle icon={LineChartIcon} title="组合收益与回撤" note="收益曲线与水下回撤共享时间轴；只展示真实运行结果。" /><div className="quant-chart tall"><ResponsiveContainer><ComposedChart data={portfolioChart}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="date" tickFormatter={value => String(value).slice(5)} /><YAxis yAxisId="left" tickFormatter={value => `${value}%`} /><YAxis yAxisId="right" orientation="right" tickFormatter={value => `${value}%`} /><Tooltip formatter={(value) => `${n(Number(value), 2)}%`} /><Legend/><ReferenceLine yAxisId="left" y={0} stroke="var(--quant-muted)"/><Line yAxisId="left" type="monotone" dataKey="value" name="组合收益" stroke="var(--quant-lime)" strokeWidth={2} dot={false}/><Bar yAxisId="right" dataKey="drawdown" name="回撤" fill="var(--quant-risk)" opacity={.45}/></ComposedChart></ResponsiveContainer></div></section>
      <section className="quant-panel"><SectionTitle icon={Archive} title="收益分布与置信区间" note={`主观察窗 ${primary} 日，样本 ${data.robustness?.sampleCount ?? 0}`} /><div className="quant-chart"><ResponsiveContainer><BarChart data={data.robustness?.distribution ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="midpoint" tickFormatter={value => `${n(Number(value), 0)}%`} /><YAxis /><Tooltip labelFormatter={value => `${n(Number(value), 2)}%`} /><Bar dataKey="count" name="样本数" fill="var(--quant-cyan)" /></BarChart></ResponsiveContainer></div><div className="quant-ci">95% 自助法区间 <strong>{n(ci[0], 2)}% ~ {n(ci[1], 2)}%</strong></div></section>
      <section className="quant-panel"><SectionTitle icon={Activity} title="事件后路径" note="每个点只使用已经走满该交易日的事件。" /><div className="quant-chart"><ResponsiveContainer><LineChart data={data.eventCurve}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="day"/><YAxis tickFormatter={value => `${value}%`}/><Tooltip formatter={value => `${n(Number(value), 2)}%`}/><Legend/><ReferenceLine y={0} stroke="var(--quant-muted)"/><Line dataKey="strategy" name="事件策略" stroke="var(--quant-lime)" dot={false}/><Line dataKey="benchmark" name="市场基准" stroke="var(--quant-cyan)" dot={false}/></LineChart></ResponsiveContainer></div></section>
      <section className="quant-panel"><SectionTitle icon={SlidersHorizontal} title="成本敏感性" note="观察结论是否依赖过低交易成本假设。" /><div className="quant-chart"><ResponsiveContainer><BarChart data={data.robustness?.sensitivity ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="label"/><YAxis tickFormatter={value => `${value}%`}/><Tooltip formatter={value => `${n(Number(value), 2)}%`}/><ReferenceLine y={0} stroke="var(--quant-muted)"/><Bar dataKey="averageExcessReturn" name="平均超额收益">{(data.robustness?.sensitivity ?? []).map(row => <Cell key={row.label} fill={row.averageExcessReturn >= 0 ? 'var(--quant-lime)' : 'var(--quant-risk)'} />)}</Bar></BarChart></ResponsiveContainer></div></section>
      <section className="quant-panel span-2"><SectionTitle icon={History} title="时间队列稳定性" note="按事件月份展示样本量、平均超额和胜率；用于识别只在单一行情阶段有效的策略。" /><div className="quant-chart"><ResponsiveContainer><ComposedChart data={data.robustness?.cohorts ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="period"/><YAxis yAxisId="left" tickFormatter={value => `${value}%`}/><YAxis yAxisId="right" orientation="right"/><Tooltip/><Legend/><ReferenceLine yAxisId="left" y={0} stroke="var(--quant-muted)"/><Bar yAxisId="right" dataKey="sampleCount" name="样本数" fill="var(--quant-panel-3)"/><Line yAxisId="left" dataKey="averageExcessReturn" name="平均超额" stroke="var(--quant-lime)"/><Line yAxisId="left" dataKey="winRate" name="胜率" stroke="var(--quant-cyan)"/></ComposedChart></ResponsiveContainer></div></section>
      {data.robustness.validation ? <section className="quant-panel span-2"><SectionTitle icon={ShieldCheck} title="时间顺序样本外验证" note={`前70%训练观察 / 后30%样本外验证 · 切分日 ${data.robustness.validation.splitDate || '—'}`} /><div className="quant-validation"><div><small>训练观察</small><strong className={tone(data.robustness.validation.trainAverageExcessReturn)}>{pct(data.robustness.validation.trainAverageExcessReturn, 2)}</strong><span>{data.robustness.validation.trainSampleCount} 样本</span></div><ChevronRight/><div><small>样本外</small><strong className={tone(data.robustness.validation.testAverageExcessReturn)}>{pct(data.robustness.validation.testAverageExcessReturn, 2)}</strong><span>{data.robustness.validation.testSampleCount} 样本</span></div><div className="quant-folds">{data.robustness.validation.walkForwardFolds.map(fold => <p key={fold.fold}><b>F{fold.fold}</b><span>{fold.startAt.slice(5)}~{fold.endAt.slice(5)}</span><strong className={tone(fold.averageExcessReturn)}>{pct(fold.averageExcessReturn, 2)}</strong></p>)}</div></div></section> : null}
      <section className="quant-panel span-2"><SectionTitle icon={Blocks} title="非结构化因子分层" note="把重要度、置信度、信息增量和观点强度按样本三等分，观察高分组是否稳定优于低分组。" /><div className="quant-factor-grid">{data.factorAnalysis.map(factor => <article key={factor.factor}><header><strong>{factor.label}</strong><span className={tone(factor.highLowSpread)}>高-低 {pct(factor.highLowSpread, 2)}</span></header><div>{factor.buckets.map(bucket => <p key={bucket.bucket}><b>{bucket.bucket}</b><span className={tone(bucket.averageExcessReturn)}>{pct(bucket.averageExcessReturn, 2)}</span><small>{bucket.sampleCount} 样本 · 胜率 {pct(bucket.winRate)}</small></p>)}</div></article>)}</div></section>
      <section className="quant-panel"><SectionTitle icon={Target} title="多持有期对照" note="同时比较绝对收益与相对基准超额。" /><table><thead><tr><th>持有期</th><th>样本</th><th>胜率</th><th>平均</th><th>平均超额</th></tr></thead><tbody>{data.rule.holdingPeriods.map(period => <tr key={period}><td>{period}日</td><td>{metric(period)?.sampleCount ?? 0}</td><td>{pct(metric(period)?.winRate)}</td><td className={tone(metric(period)?.averageReturn)}>{pct(metric(period)?.averageReturn, 2)}</td><td className={tone(metric(period, true)?.averageReturn)}>{pct(metric(period, true)?.averageReturn, 2)}</td></tr>)}</tbody></table></section>
      <section className="quant-panel"><SectionTitle icon={RadioTower} title="机构胜率（收缩后）" note="至少 3 个到期样本进入主榜，小样本仅观察。" /><table><thead><tr><th>研究组</th><th>样本</th><th>校正胜率</th><th>超额</th></tr></thead><tbody>{data.researchGroupRankings.slice(0, 8).map(row => <tr key={row.researchGroup}><td>{row.researchGroup}</td><td>{row.matureCount}</td><td>{pct(row.adjustedWinRate)}</td><td className={tone(row.averageExcessReturn)}>{pct(row.averageExcessReturn, 2)}</td></tr>)}</tbody></table></section></div></main>;
}

function DataAssets({ catalog, data }: { catalog: EssayQuantCatalog | null; data: EssayQuantDashboard | null }) {
  const funnel = [['扫描语料', data?.dataQuality.notesScanned ?? 0], ['AI 已分析', data?.dataQuality.analyzedNoteCount ?? 0], ['解析到股票', data?.dataQuality.resolvedNoteCount ?? 0], ['重复事件过滤', data?.dataQuality.duplicateEventCount ?? 0], ['具备行情股票', data?.dataQuality.pricedSymbolCount ?? 0], ['到期有效事件', data?.summary.matureEventCount ?? 0]] as const;
  return <main className="quant-page quant-data-page"><section className="quant-panel"><SectionTitle icon={Database} title="数据资产地图" note="不是接口宣传清单：数量、最近时间和状态来自本地库实时查询。" /><table className="quant-data-table"><thead><tr><th>数据资产</th><th>本地记录</th><th>最近数据</th><th>研究用途</th><th>可用状态</th></tr></thead><tbody>{(catalog?.assets ?? []).map(item => <tr key={item.key}><td><Database />{item.name}</td><td className="mono">{item.count.toLocaleString()}</td><td className="mono">{item.latestAt || '—'}</td><td>{item.usage}</td><td><Status ok={item.status === 'ready'}>{item.status === 'ready' ? '可直接使用' : item.status === 'empty' ? '库为空' : '尚未建库'}</Status></td></tr>)}</tbody></table></section>
    <section className="quant-panel"><SectionTitle icon={FileSearch} title="本次样本漏斗" note="把“为什么最后只有这些样本”完整展示出来。" /><div className="quant-funnel">{funnel.map(([label, value], index) => <div key={label}><span>{index + 1}</span><p>{label}</p><strong>{Number(value).toLocaleString()}</strong>{index < funnel.length - 1 ? <ChevronRight /> : null}</div>)}</div><div className="quant-quality-notes"><div><strong>语料策略</strong><p>{data?.rule.rawNotePolicy === 'include' ? '纳入未分析语料（探索模式）' : '仅 AI 已分析且通过阈值的语料'}</p></div><div><strong>行情口径</strong><p>{data?.dataQuality.priceBasis ?? '等待回测'}</p></div><div><strong>基准与成本</strong><p>{data?.rule.benchmarkCode ?? '—'} · {data?.rule.transactionCostBps ?? 0}bp</p></div><div><strong>数据截止</strong><p>{data?.dataQuality.priceCutoff ?? '—'}</p></div></div></section></main>;
}

function RunHistory({ history }: { history: EssayQuantRunHistory }) {
  return <main className="quant-page"><section className="quant-panel"><SectionTitle icon={History} title="运行历史" note="每次研究保存规则、数据哈希、截止时间和结果快照，便于复现与比较。" aside={<span className="mono">{history.total} RUNS</span>} />{history.items.length ? <table className="quant-history-table"><thead><tr><th>运行</th><th>研究名称</th><th>事件 / 到期</th><th>主期平均超额</th><th>95% CI</th><th>行情截止</th><th>运行时间</th></tr></thead><tbody>{history.items.map(row => <tr key={row.id}><td className="mono">#{row.id}</td><td><strong>{row.name}</strong><small>{row.strategyType}</small></td><td>{row.eventCount} / {row.matureEventCount}</td><td className={tone(row.primaryAverageExcess)}>{pct(row.primaryAverageExcess, 2)}</td><td>{row.confidenceInterval ? `${n(row.confidenceInterval[0], 2)} ~ ${n(row.confidenceInterval[1], 2)}` : '—'}</td><td className="mono">{row.priceCutoff || '—'}</td><td>{row.createdAt ? new Date(row.createdAt).toLocaleString('zh-CN') : '—'}</td></tr>)}</tbody></table> : <EmptyState title="暂无运行历史" description="完成一次策略研究后，结果会保存到这里。" />}</section></main>;
}
