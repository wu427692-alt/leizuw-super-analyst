import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  EssayQuantCatalog, EssayQuantDashboard, EssayQuantPlan,
  EssayQuantMethod, EssayQuantRule, EssayQuantRunHistory, EssayQuantTaskList, QuantMetric,
} from '../types/essayQuant';
import './EssayQuantPage.css';
import './EssayQuantTasks.css';

const DEFAULT_RULE: EssayQuantRule = {
  name: 'AI 已分析小作文事件策略', sourceQuery: '', signalDirection: 'bullish', lookbackDays: 365,
  holdingPeriods: [5, 10, 20], firstMentionOnly: false, firstMentionWindowDays: 180,
  minImportance: 60, minConfidence: 0.5, benchmarkCode: '000300.SH', portfolioSize: 10,
  enabled: true, strategyType: 'essay_event', rawNotePolicy: 'exclude', dedupeWindowDays: 3,
  transactionCostBps: 12, validationMethod: 'walk_forward',
};

const NAV = [
  ['overview', '任务中心', Activity], ['builder', '新建任务', SlidersHorizontal],
  ['natural-language', 'AI 建任务', Bot], ['results', '结果研判', BarChart3],
  ['data', '数据与方法', Database], ['history', '任务档案', History],
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

type QuantSection = typeof NAV[number][0];
type QuantModule = 'dashboard' | 'catalog' | 'history' | 'rules' | 'tasks';
const QUANT_SECTIONS = new Set<QuantSection>(NAV.map(([key]) => key));

const SECTION_MODULES: Record<QuantSection, QuantModule[]> = {
  overview: ['dashboard', 'catalog', 'history', 'tasks'],
  builder: ['dashboard', 'catalog', 'rules', 'tasks'],
  'natural-language': ['catalog', 'tasks'],
  results: ['dashboard', 'tasks'],
  data: ['dashboard', 'catalog', 'tasks'],
  history: ['history', 'tasks'],
};

const MODULE_LABELS: Record<QuantModule, string> = {
  dashboard: '最近研究结果', catalog: '数据资产目录', history: '任务档案', rules: '研究规则', tasks: '研究任务',
};

const n = (value?: number | null, digits = 1) => value == null ? '—' : value.toFixed(digits);
const pct = (value?: number | null, digits = 1) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`;
const compact = (value?: number | null) => value == null ? '—' : new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const tone = (value?: number | null) => value == null ? 'quant-muted' : value >= 0 ? 'quant-positive' : 'quant-negative';

const buildMethodRule = (method: EssayQuantMethod): EssayQuantRule => ({
  ...DEFAULT_RULE,
  ...method.template,
  name: method.template.name || method.name,
  strategyType: method.template.strategyType || (method.key === 'event_study' ? 'essay_event' : method.key),
});

function SectionTitle({ icon: Icon, title, note, aside }: { icon: typeof Activity; title: string; note?: string; aside?: React.ReactNode }) {
  return <div className="quant-section-head"><div className="quant-section-title"><Icon aria-hidden="true" /><div><h2>{title}</h2>{note ? <p>{note}</p> : null}</div></div>{aside}</div>;
}
function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={ok ? 'quant-status is-ok' : 'quant-status'}>{ok ? <CheckCircle2 /> : <LoaderCircle />}{children}</span>;
}
function LoadingScreen({ progress }: { progress: number }) {
  return <div className="quant-loading" role="status"><RadioTower /><h1>正在装载量化研究工作台</h1><p>读取研究方法、真实数据资产、最近运行和后台状态</p><div><span style={{ width: `${progress}%` }} /></div><strong>{progress}%</strong></div>;
}

function StableChart({ children }: { children: React.ReactElement }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const measure = () => {
      const next = { width: Math.floor(host.clientWidth), height: Math.floor(host.clientHeight) };
      if (next.width > 0 && next.height > 0) {
        setSize(current => current.width === next.width && current.height === next.height ? current : next);
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, []);
  return <div ref={hostRef} className="quant-chart-host">{size.width > 0 && size.height > 0 ? <ResponsiveContainer width={size.width} height={size.height}>{children}</ResponsiveContainer> : null}</div>;
}

export default function EssayQuantPage() {
  const location = useLocation(); const navigate = useNavigate();
  const searchParams = new URLSearchParams(location.search);
  const requestedSection = searchParams.get('section') || location.pathname.split('/')[2] || 'overview';
  const active: QuantSection = QUANT_SECTIONS.has(requestedSection as QuantSection) ? requestedSection as QuantSection : 'overview';
  const requestedMethodKey = searchParams.get('method');
  const [data, setData] = useState<EssayQuantDashboard | null>(null);
  const [catalog, setCatalog] = useState<EssayQuantCatalog | null>(null);
  const [history, setHistory] = useState<EssayQuantRunHistory>({ items: [], total: 0 });
  const [taskList, setTaskList] = useState<EssayQuantTaskList>({ items: [], total: 0 });
  const [rules, setRules] = useState<EssayQuantRule[]>([]);
  const [rule, setRule] = useState<EssayQuantRule>(DEFAULT_RULE);
  const [loading, setLoading] = useState(true); const [progress, setProgress] = useState(12);
  const [running, setRunning] = useState(false); const [message, setMessage] = useState('');
  const [messageScope, setMessageScope] = useState<QuantSection | null>(null);
  const [failedModules, setFailedModules] = useState<QuantModule[]>([]);
  const initiallyLoaded = useRef(false);
  const [prompt, setPrompt] = useState(EXAMPLES[0]); const [plan, setPlan] = useState<EssayQuantPlan | null>(null);

  const load = useCallback(async () => {
    if (!initiallyLoaded.current) setLoading(true);
    setProgress(18); setMessage(''); setMessageScope(null); setFailedModules([]);
    const tasks: Record<QuantModule, () => Promise<void>> = {
      dashboard: async () => { const value = await essayQuantApi.dashboard(); setData(value); setRule({ ...DEFAULT_RULE, ...value.rule }); },
      catalog: async () => { setCatalog(await essayQuantApi.catalog()); },
      history: async () => { setHistory(await essayQuantApi.runs()); },
      tasks: async () => { setTaskList(await essayQuantApi.tasks()); },
      rules: async () => { const value = await essayQuantApi.rules(); setRules(value.items); },
    };
    const modules = SECTION_MODULES[active];
    const timer = window.setInterval(() => setProgress(current => Math.min(88, current + 7)), 140);
    const settled = await Promise.allSettled(modules.map(key => tasks[key]()));
    window.clearInterval(timer); setProgress(100);
    setFailedModules(modules.filter((_, index) => settled[index]?.status === 'rejected'));
    initiallyLoaded.current = true;
    window.setTimeout(() => setLoading(false), 160);
  }, [active]);
  const refreshLive = useCallback(async () => {
    const [taskJob, dashboardJob, historyJob] = await Promise.allSettled([
      essayQuantApi.tasks(),
      active === 'overview' ? essayQuantApi.dashboard() : Promise.resolve(null),
      active === 'history' || active === 'overview' ? essayQuantApi.runs() : Promise.resolve(null),
    ] as const);
    if (taskJob.status === 'fulfilled') setTaskList(taskJob.value);
    if (dashboardJob.status === 'fulfilled' && dashboardJob.value) setData(dashboardJob.value);
    if (historyJob.status === 'fulfilled' && historyJob.value) setHistory(historyJob.value);
  }, [active]);
  useEffect(() => {
    void load(); document.title = '量化回测与数据利用 - DSA';
  }, [load]);
  useEffect(() => {
    const hasActiveTasks = taskList.items.some(item => item.status === 'queued' || item.status === 'running');
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshLive();
    }, hasActiveTasks ? 3_000 : 30_000);
    const onVisibility = () => { if (document.visibilityState === 'visible') void refreshLive(); };
    document.addEventListener('visibilitychange', onVisibility);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisibility); };
  }, [refreshLive, taskList.items]);
  useEffect(() => {
    if (!catalog || !requestedMethodKey) return;
    const method = catalog.methods.find(item => item.key === requestedMethodKey);
    if (method) setRule(buildMethodRule(method));
  }, [catalog, requestedMethodKey]);

  const run = async (target = rule) => {
    setRunning(true); setMessageScope(active); setMessage('正在提交个人后台研究任务…');
    try {
      const task = await essayQuantApi.startTask(target);
      setTaskList(current => ({
        items: [task, ...current.items.filter(item => item.taskId !== task.taskId)],
        total: current.total + 1,
      }));
      setMessage(`后台任务 ${task.taskId.slice(0, 8)} 已启动。现在可以切换到其他页面，完成后到“运行历史”查看结果。`);
    }
    catch (error) { setMessage(error instanceof Error ? error.message : '后台任务提交失败'); }
    finally { setRunning(false); }
  };
  const save = async () => {
    setRunning(true); setMessageScope(active);
    try { const saved = await essayQuantApi.saveRule(rule); setRule(saved); setRules(current => [saved, ...current.filter(item => item.id !== saved.id)]); setMessage('研究规则已保存'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '保存失败'); }
    finally { setRunning(false); }
  };
  const generatePlan = async () => {
    setRunning(true); setMessageScope(active); setPlan(null); setMessage('DeepSeek 正在把需求拆成信号、样本、成本和验证任务…');
    try { const result = await essayQuantApi.plan(prompt); setPlan(result); setRule({ ...DEFAULT_RULE, ...result.rule }); setMessage('研究方案已生成，请检查假设和安全边界后再执行。'); }
    catch (error) { setMessage(error instanceof Error ? error.message : '研究方案生成失败'); }
    finally { setRunning(false); }
  };
  const executePlan = async () => {
    if (!plan) return; setRunning(true); setMessageScope(active); setMessage('正在把已确认方案提交到个人后台队列…');
    try {
      const task = await essayQuantApi.startTask(plan.rule);
      setTaskList(current => ({
        items: [task, ...current.items.filter(item => item.taskId !== task.taskId)],
        total: current.total + 1,
      }));
      setMessage(`自然语言研究任务 ${task.taskId.slice(0, 8)} 已在后台运行，可以离开本页。`);
    }
    catch (error) { setMessage(error instanceof Error ? error.message : '方案任务提交失败'); }
    finally { setRunning(false); }
  };
  const openRunResult = async (runId: number) => {
    setRunning(true); setMessageScope(active); setMessage(`正在读取运行 #${runId} 的不可变结果快照…`);
    try {
      const result = await essayQuantApi.runResult(runId);
      setData(result); setRule({ ...DEFAULT_RULE, ...result.rule });
      setMessageScope('results'); navigate('/essay-quant?section=results');
      setMessage(`已打开运行 #${runId}，结果仅属于当前用户。`);
    } catch (error) { setMessage(error instanceof Error ? error.message : '结果读取失败'); }
    finally { setRunning(false); }
  };
  const primary = Math.max(...(rule.holdingPeriods.length ? rule.holdingPeriods : [20]));
  const metric = (period: number, excess = false): QuantMetric | undefined => (excess ? data?.summary.excessMetrics : data?.summary.metrics)?.find(item => item.period === period);
  const portfolioChart = useMemo(() => { let peak = 0; return (data?.portfolio.curve ?? []).map(item => { const value = (item.value - 1) * 100; peak = Math.max(peak, item.value); return { ...item, value, drawdown: peak ? (item.value / peak - 1) * 100 : 0 }; }); }, [data]);
  const freshness = data?.dataQuality.priceFreshnessRatio ?? 0;
  const latestHistoryCutoff = history.items[0]?.priceCutoff;
  const visibleFailures = failedModules;
  const loadNotice = visibleFailures.length
    ? `${visibleFailures.map(key => MODULE_LABELS[key]).join('、')}暂时未更新；页面已保留可用数据，并会在后台继续重试。`
    : '';
  const activeMethod = catalog?.methods.find(item => (
    item.template.strategyType || (item.key === 'event_study' ? 'essay_event' : item.key)
  ) === rule.strategyType) ?? null;
  const activeTaskCount = taskList.items.filter(item => item.status === 'queued' || item.status === 'running').length;

  if (loading) return <AppPage className="aurora-workbench max-w-[1720px] px-3 py-3"><LoadingScreen progress={progress} /></AppPage>;
  return <AppPage className="aurora-workbench max-w-[1760px] px-2 pb-8 pt-2 md:px-3"><div className="essay-quant-terminal">
    <header className="quant-header"><div><h1>量化研究任务中心</h1><p>从交易假设出发，建立可后台运行、可复现、能解释到交易规则的个人研究任务。</p></div><div className="quant-header-actions">{activeTaskCount ? <Status ok={false}>运行中 {activeTaskCount}</Status> : <Status ok>暂无运行任务</Status>}{active === 'history' ? <Status ok={Boolean(latestHistoryCutoff)}>行情截止 {latestHistoryCutoff || '检测中'}</Status> : <Status ok={freshness >= 98}>行情 {data?.dataQuality.priceTargetDate || '检测中'} · {freshness.toFixed(1)}%</Status>}<span className="quant-auto-sync"><RadioTower />自动同步</span></div></header>
    <nav className="quant-tabs" aria-label="量化研究栏目">{NAV.map(([key, label, Icon]) => <button key={key} className={active === key ? 'is-active' : ''} onClick={() => navigate(key === 'overview' ? '/essay-quant' : `/essay-quant?section=${key}`)}><Icon />{label}</button>)}</nav>
    {message && messageScope === active ? <div className="quant-message" aria-live="polite">{running ? <LoaderCircle className="is-spinning" /> : <Activity />}{message}</div> : null}
    {loadNotice ? <div className="quant-message" aria-live="polite"><Activity />{loadNotice}</div> : null}
    {data && freshness < 98 ? <div className="quant-message is-warning"><TriangleAlert />当前仅 {data.dataQuality.currentPriceSymbolCount ?? 0}/{data.dataQuality.resolvedSymbolCount ?? 0} 个量化标的覆盖目标交易日，后台正在自动补齐；历史结果仍可查看。</div> : null}
    {active === 'overview' && <Overview catalog={catalog} history={history} tasks={taskList} onNew={() => navigate('/essay-quant?section=builder')} onOpenResult={(runId) => void openRunResult(runId)} onMethod={(method) => { setRule(buildMethodRule(method)); navigate(`/essay-quant?section=builder&method=${encodeURIComponent(method.key)}`); }} onNatural={() => navigate('/essay-quant?section=natural-language')} />}
    {active === 'builder' && <Builder method={activeMethod} rule={rule} rules={rules} running={running} setRule={setRule} onRun={() => void run()} onSave={() => void save()} />}
    {active === 'natural-language' && <NaturalLanguage prompt={prompt} setPrompt={setPrompt} plan={plan} running={running} onGenerate={() => void generatePlan()} onExecute={() => void executePlan()} />}
    {active === 'results' && <Results data={data} primary={primary} metric={metric} portfolioChart={portfolioChart} onBuilder={() => navigate(`/essay-quant?section=builder${activeMethod ? `&method=${encodeURIComponent(activeMethod.key)}` : ''}`)} />}
    {active === 'data' && <DataAssets catalog={catalog} data={data} />}
    {active === 'history' && <RunHistory history={history} tasks={taskList} onOpenResult={(runId) => void openRunResult(runId)} />}
    <footer className="quant-footer"><ShieldCheck /> 历史结果不构成投资建议；研究结论必须结合样本外检验、成交约束和数据截止时间解释。</footer>
  </div></AppPage>;
}

function Overview({ catalog, history, tasks, onNew, onOpenResult, onMethod, onNatural }: {
  catalog: EssayQuantCatalog | null; history: EssayQuantRunHistory; tasks: EssayQuantTaskList;
  onNew: () => void; onOpenResult: (runId: number) => void;
  onMethod: (method: EssayQuantMethod) => void; onNatural: () => void;
}) {
  const activeTasks = tasks.items.filter(item => item.status === 'queued' || item.status === 'running');
  return <main className="quant-page quant-task-center">
    <section className="quant-task-launch">
      <button className="quant-primary" onClick={onNew}><Play />新建研究任务</button>
      <button className="quant-hypothesis-launch" onClick={onNatural}><Bot /><span>描述你想验证的交易假设…</span><ChevronRight /></button>
    </section>
    <section className="quant-pipeline" aria-label="量化研究任务流程">{PIPELINE.map(([title, note, Icon], index) => <div key={title} className="quant-pipeline-step"><span>{index + 1}</span><Icon /><div><strong>{title}</strong><p>{note}</p></div>{index < PIPELINE.length - 1 ? <ChevronRight className="quant-pipe-arrow" /> : null}</div>)}</section>
    <section className="quant-panel quant-task-panel"><SectionTitle icon={Activity} title="正在运行" note="任务在服务器后台执行，离开本页不会中断。" aside={<span className="mono">{activeTasks.length} ACTIVE</span>} />
      {activeTasks.length ? <div className="quant-active-task-list">{activeTasks.map(task => <article key={task.taskId}><div><strong>{task.name}</strong><small>{task.message}</small></div><span>{task.strategyType}</span><div className="quant-task-progress"><i style={{ width: `${task.progress}%` }} /><b>{task.progress}%</b></div><time>{task.startedAt ? `开始 ${new Date(task.startedAt).toLocaleString('zh-CN')}` : '等待执行器'}</time></article>)}</div> : <div className="quant-inline-empty"><CheckCircle2 /><span>当前没有运行中的任务。新任务会在这里显示真实进度。</span></div>}
    </section>
    <section className="quant-panel quant-completed-panel"><SectionTitle icon={History} title="最近完成" note="这里只展示用户主动创建的研究任务；系统维护与机构基线不会出现在这里。" aside={<span className="mono">{history.total} TASKS</span>} />
      {history.items.length ? <table className="quant-completed-table"><thead><tr><th>研究任务</th><th>数据截止</th><th>事件 / 到期</th><th>样本外结果</th><th>风险判断</th><th>完成时间</th><th>操作</th></tr></thead><tbody>{history.items.slice(0, 8).map(row => <tr key={row.id}><td><strong>{row.name}</strong><small>{row.strategyType}</small></td><td className="mono">{row.priceCutoff || '—'}</td><td>{row.eventCount} / {row.matureEventCount}</td><td className={tone(row.outOfSampleExcess)}>{pct(row.outOfSampleExcess, 2)}</td><td><span className={`quant-verdict-tag ${row.verdict === '可进入模拟观察' ? 'is-positive' : row.verdict === '暂不采用' || row.verdict === '风险偏高' ? 'is-negative' : ''}`}>{row.verdict || '等待判断'}</span></td><td>{row.createdAt ? new Date(row.createdAt).toLocaleString('zh-CN') : '—'}</td><td><button className="quant-result-link" onClick={() => onOpenResult(row.id)}>查看结果<ChevronRight /></button></td></tr>)}</tbody></table> : <EmptyState title="还没有完成的研究任务" description="从模板或自然语言创建任务，结果会自动保存为不可变快照。" />}
    </section>
    <section className="quant-panel quant-template-panel"><SectionTitle icon={Layers3} title="从模板创建" note="每个模板都有不同的数据、样本构造与专属输出。" /><div className="quant-template-rail">{(catalog?.methods ?? []).map((method, index) => { const Icon = [Target, Blocks, Sparkles, RadioTower, CircleDollarSign][index] ?? FlaskConical; return <button key={method.key} onClick={() => onMethod(method)}><Icon /><span><strong>{method.name}</strong><small>{method.purpose}</small></span><ChevronRight /></button>; })}</div></section>
    <section className="quant-panel quant-availability"><SectionTitle icon={Database} title="数据可用性" note="任务将固定记录各数据源的截止时间。" /><div>{(catalog?.assets ?? []).slice(0, 7).map(item => <article key={item.key}><span className={item.status === 'ready' ? 'is-ready' : ''} /><div><strong>{item.name}</strong><small>{item.status === 'ready' ? `${compact(item.count)} 条 · 截至 ${item.latestAt?.slice(0, 16) || '未知'}` : '当前不可用'}</small></div></article>)}</div></section>
  </main>;
}

function Builder({ method, rule, rules, running, setRule, onRun, onSave }: { method: EssayQuantMethod | null; rule: EssayQuantRule; rules: EssayQuantRule[]; running: boolean; setRule: (rule: EssayQuantRule) => void; onRun: () => void; onSave: () => void }) {
  const update = <K extends keyof EssayQuantRule>(key: K, value: EssayQuantRule[K]) => setRule({ ...rule, [key]: value });
  return <main className="quant-page quant-builder-page"><section className="quant-panel"><SectionTitle icon={SlidersHorizontal} title="策略工坊" note="按信号、样本、交易和验证四步配置；右侧随时显示完整研究定义。" aside={<div className="quant-action-row"><select aria-label="载入保存规则" value={rule.id ?? ''} onChange={event => setRule(rules.find(item => item.id === Number(event.target.value)) ?? DEFAULT_RULE)}><option value="">新建规则</option>{rules.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button onClick={onSave} disabled={running}><Save />保存</button><button className="quant-primary" onClick={onRun} disabled={running}><Play />{running ? '执行中' : '运行研究'}</button></div>} />
    {method ? <section className="quant-method-context"><div><span>当前方法</span><h2>{method.name}</h2><p>{method.purpose}</p></div><dl><div><dt>真实数据</dt><dd>{method.usedData.join(' · ')}</dd></div><div><dt>执行逻辑</dt><dd>{method.engine}</dd></div><div><dt>专属输出</dt><dd>{method.output}</dd></div></dl></section> : null}
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

function MethodResultFocus({ data }: { data: EssayQuantDashboard }) {
  const analysis = data.methodAnalysis;
  if (!analysis) return null;
  const strategy = analysis.strategyType;
  return <section className="quant-panel quant-method-result"><SectionTitle icon={Target} title={`${analysis.name} · 专属结果`} note={analysis.selectionRule} aside={<span className="quant-method-count">{analysis.selectedEventCount} / {analysis.sourceEventCount} 入选</span>} />
    <div className="quant-method-diagnostics">{analysis.diagnostics.map(item => <article key={item.label}><small>{item.label}</small><strong>{item.value ?? '—'}</strong><p>{item.note}</p></article>)}</div>
    {strategy === 'essay_event' ? <div className="quant-method-output"><h3>多事件窗验证</h3>{data.summary.excessMetrics.map(item => <p key={item.period}><span>{item.period}日</span><strong className={tone(item.averageReturn)}>{pct(item.averageReturn, 2)}</strong><small>{item.sampleCount}个到期样本 · 胜率 {pct(item.winRate)}</small></p>)}</div> : null}
    {strategy === 'multi_factor' ? <div className="quant-method-output"><h3>因子高低组差</h3>{data.factorAnalysis.map(item => <p key={item.factor}><span>{item.label}</span><strong className={tone(item.highLowSpread)}>{pct(item.highLowSpread, 2)}</strong><small>高分组减低分组</small></p>)}</div> : null}
    {strategy === 'hybrid_intelligence' ? <div className="quant-method-output"><h3>共振信号</h3>{data.events.slice(0, 8).map(item => <p key={`${item.topicId}-${item.symbol}`}><span>{item.stockName}</span><strong>{item.stance === 'bearish' ? '看空 × 下行' : '看多 × 上行'}</strong><small>事前MA5 {n(item.preEventMa5, 2)} / MA20 {n(item.preEventMa20, 2)}</small></p>)}</div> : null}
    {strategy === 'institution_track' ? <div className="quant-method-output"><h3>机构校正排名</h3>{data.researchGroupRankings.slice(0, 8).map(item => <p key={item.researchGroup}><span>{item.researchGroup}</span><strong>{pct(item.adjustedWinRate)}</strong><small>{item.matureCount}个到期样本 · 超额 {pct(item.averageExcessReturn, 2)}</small></p>)}</div> : null}
    {strategy === 'portfolio' ? <div className="quant-method-output"><h3>实际组合候选</h3>{data.portfolio.components.map(item => <p key={item.symbol}><span>{item.stockName}</span><strong>{item.weight.toFixed(1)}%</strong><small>{item.symbol} · {item.trigger}</small></p>)}</div> : null}
  </section>;
}

function Results({ data, primary, metric, portfolioChart, onBuilder }: { data: EssayQuantDashboard | null; primary: number; metric: (period: number, excess?: boolean) => QuantMetric | undefined; portfolioChart: Array<Record<string, string | number>>; onBuilder: () => void }) {
  if (!data) return <main className="quant-page"><section className="quant-panel"><EmptyState title="还没有回测结果" description="先在策略工坊或自然语言工作台运行一个研究任务。" /><button className="quant-primary centered" onClick={onBuilder}>进入策略工坊</button></section></main>;
  const ci = data.robustness?.confidenceInterval95 ?? [null, null];
  const validation = data.robustness?.validation;
  const oos = validation?.testAverageExcessReturn;
  const ciCrossesZero = ci[0] == null || ci[1] == null || (ci[0] <= 0 && ci[1] >= 0);
  const verdict = data.summary.matureEventCount < 30 ? '样本不足，仅作探索'
    : oos == null ? '等待样本外检验'
      : oos <= 0 ? '暂不形成交易规则'
        : ciCrossesZero ? '仅可观察，暂不形成交易规则'
          : (data.portfolio.maxDrawdown ?? 0) <= -20 ? '收益为正，但风险偏高'
            : '可进入模拟观察';
  const verdictTone = verdict === '可进入模拟观察' ? 'is-positive' : verdict.includes('暂不') || verdict.includes('风险') ? 'is-negative' : '';
  const reasons = [
    `到期样本 ${data.summary.matureEventCount} 个`,
    `样本外超额 ${pct(oos, 2)}`,
    `95% 置信区间 ${n(ci[0], 2)}% ~ ${n(ci[1], 2)}%${ciCrossesZero ? '，穿过零轴' : ''}`,
    `最大回撤 ${pct(data.portfolio.maxDrawdown, 2)}`,
  ];
  return <main className="quant-page quant-results-page">
    <section className={`quant-decision-banner ${verdictTone}`}><div><span>研究结论</span><h2>{verdict}</h2><p>{data.rule.name}</p></div><ul>{reasons.map(item => <li key={item}>{item}</li>)}</ul><aside><small>数据截止</small><strong>{data.dataQuality.priceCutoff || '—'}</strong><small>不可变快照</small><strong className="mono">#{data.runId ?? '—'} · {data.snapshotHash || '读取中'}</strong></aside></section>
    <section className="quant-evidence-questions" aria-label="研究证据链"><div><span>01</span><strong>研究了什么</strong><p>{data.rule.sourceQuery || '全部机构'}的{data.rule.signalDirection === 'bullish' ? '看多' : data.rule.signalDirection === 'bearish' ? '看空' : '全部'}观点，观察事件后 {data.rule.holdingPeriods.join(' / ')} 日。</p></div><div><span>02</span><strong>用了哪些数据</strong><p>AI结构化小作文、股票映射、Tushare日线、{data.dataQuality.benchmark}与交易成本。</p></div><div><span>03</span><strong>跑出了什么</strong><p>主观察窗平均超额 {pct(data.robustness?.averageExcessReturn, 2)}，样本外 {pct(oos, 2)}。</p></div><div><span>04</span><strong>如何用于交易</strong><p>{verdict === '可进入模拟观察' ? '仅进入模拟盘观察，先验证执行偏差。' : '当前证据不足，不应直接转成实盘订单。'}</p></div></section>
    <section className="quant-panel quant-task-definition"><SectionTitle icon={ClipboardCheck} title="任务定义" note="结果必须和原始假设、样本口径及交易约束一起阅读。" /><dl><div><dt>研究假设</dt><dd>{data.rule.sourceQuery || '机构观点'}对未来 {primary} 个交易日存在可重复的信息增量</dd></div><div><dt>信号</dt><dd>{data.rule.signalDirection} · 重要度 ≥ {data.rule.minImportance} · 置信度 ≥ {data.rule.minConfidence}</dd></div><div><dt>股票范围</dt><dd>语料中可解析且具备行情的 A 股</dd></div><div><dt>入场 / 出场</dt><dd>{data.dataQuality.entryRule} / {data.dataQuality.exitRule}</dd></div><div><dt>基准 / 成本</dt><dd>{data.dataQuality.benchmark} / 双边合计 {data.rule.transactionCostBps}bp</dd></div><div><dt>去重</dt><dd>同股、同机构、同方向 {data.rule.dedupeWindowDays} 日内只计一次</dd></div></dl></section>
    <section className="quant-kpis quant-explained-kpis"><div><small>样本外超额</small><strong className={tone(oos)}>{pct(oos, 2)}</strong><p>未参与规则选择的后段样本，相对基准多赚或少赚多少。</p></div><div><small>95% 置信区间</small><strong>{n(ci[0], 2)}% ~ {n(ci[1], 2)}%</strong><p>{ciCrossesZero ? '区间穿过零，暂不能排除结果来自随机波动。' : '区间未穿过零，但仍需继续滚动验证。'}</p></div><div><small>最大回撤</small><strong className="quant-negative">{pct(data.portfolio.maxDrawdown, 2)}</strong><p>组合净值从历史高点到低点的最大跌幅。</p></div><div><small>正收益率</small><strong>{pct(data.robustness?.positiveRate)}</strong><p>到期事件中取得正收益的比例，不等于盈利概率。</p></div><div><small>盈亏比</small><strong>{n(data.robustness?.payoffRatio, 2)}</strong><p>平均盈利与平均亏损绝对值之比，需要结合胜率判断。</p></div><div><small>有效样本</small><strong>{data.summary.matureEventCount}</strong><p>{data.summary.eventCount} 个事件中已走完整个持有期的样本。</p></div></section>
    <MethodResultFocus data={data} />
    <div className="quant-result-grid"><section className="quant-panel span-2"><SectionTitle icon={LineChartIcon} title="组合收益与回撤" note="收益曲线与水下回撤共享时间轴；只展示真实运行结果。" /><div className="quant-chart tall"><StableChart><ComposedChart data={portfolioChart}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="date" tickFormatter={value => String(value).slice(5)} /><YAxis yAxisId="left" tickFormatter={value => `${value}%`} /><YAxis yAxisId="right" orientation="right" tickFormatter={value => `${value}%`} /><Tooltip formatter={(value) => `${n(Number(value), 2)}%`} /><Legend/><ReferenceLine yAxisId="left" y={0} stroke="var(--quant-muted)"/><Line yAxisId="left" type="monotone" dataKey="value" name="组合收益" stroke="var(--quant-lime)" strokeWidth={2} dot={false}/><Bar yAxisId="right" dataKey="drawdown" name="回撤" fill="var(--quant-risk)" opacity={.45}/></ComposedChart></StableChart></div></section>
      <section className="quant-panel"><SectionTitle icon={Archive} title="收益分布与置信区间" note={`主观察窗 ${primary} 日，样本 ${data.robustness?.sampleCount ?? 0}`} /><div className="quant-chart"><StableChart><BarChart data={data.robustness?.distribution ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="midpoint" tickFormatter={value => `${n(Number(value), 0)}%`} /><YAxis /><Tooltip labelFormatter={value => `${n(Number(value), 2)}%`} /><Bar dataKey="count" name="样本数" fill="var(--quant-cyan)" /></BarChart></StableChart></div><div className="quant-ci">95% 自助法区间 <strong>{n(ci[0], 2)}% ~ {n(ci[1], 2)}%</strong></div></section>
      <section className="quant-panel"><SectionTitle icon={Activity} title="事件后路径" note="每个点只使用已经走满该交易日的事件。" /><div className="quant-chart"><StableChart><LineChart data={data.eventCurve}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="day"/><YAxis tickFormatter={value => `${value}%`}/><Tooltip formatter={value => `${n(Number(value), 2)}%`}/><Legend/><ReferenceLine y={0} stroke="var(--quant-muted)"/><Line dataKey="strategy" name="事件策略" stroke="var(--quant-lime)" dot={false}/><Line dataKey="benchmark" name="市场基准" stroke="var(--quant-cyan)" dot={false}/></LineChart></StableChart></div></section>
      <section className="quant-panel"><SectionTitle icon={SlidersHorizontal} title="成本敏感性" note="观察结论是否依赖过低交易成本假设。" /><div className="quant-chart"><StableChart><BarChart data={data.robustness?.sensitivity ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="label"/><YAxis tickFormatter={value => `${value}%`}/><Tooltip formatter={value => `${n(Number(value), 2)}%`}/><ReferenceLine y={0} stroke="var(--quant-muted)"/><Bar dataKey="averageExcessReturn" name="平均超额收益">{(data.robustness?.sensitivity ?? []).map(row => <Cell key={row.label} fill={row.averageExcessReturn >= 0 ? 'var(--quant-lime)' : 'var(--quant-risk)'} />)}</Bar></BarChart></StableChart></div></section>
      <section className="quant-panel span-2"><SectionTitle icon={History} title="时间队列稳定性" note="按事件月份展示样本量、平均超额和胜率；用于识别只在单一行情阶段有效的策略。" /><div className="quant-chart"><StableChart><ComposedChart data={data.robustness?.cohorts ?? []}><CartesianGrid stroke="var(--quant-grid)" vertical={false}/><XAxis dataKey="period"/><YAxis yAxisId="left" tickFormatter={value => `${value}%`}/><YAxis yAxisId="right" orientation="right"/><Tooltip/><Legend/><ReferenceLine yAxisId="left" y={0} stroke="var(--quant-muted)"/><Bar yAxisId="right" dataKey="sampleCount" name="样本数" fill="var(--quant-panel-3)"/><Line yAxisId="left" dataKey="averageExcessReturn" name="平均超额" stroke="var(--quant-lime)"/><Line yAxisId="left" dataKey="winRate" name="胜率" stroke="var(--quant-cyan)"/></ComposedChart></StableChart></div></section>
      {data.robustness.validation ? <section className="quant-panel span-2"><SectionTitle icon={ShieldCheck} title="时间顺序样本外验证" note={`前70%训练观察 / 后30%样本外验证 · 切分日 ${data.robustness.validation.splitDate || '—'}`} /><div className="quant-validation"><div><small>训练观察</small><strong className={tone(data.robustness.validation.trainAverageExcessReturn)}>{pct(data.robustness.validation.trainAverageExcessReturn, 2)}</strong><span>{data.robustness.validation.trainSampleCount} 样本</span></div><ChevronRight/><div><small>样本外</small><strong className={tone(data.robustness.validation.testAverageExcessReturn)}>{pct(data.robustness.validation.testAverageExcessReturn, 2)}</strong><span>{data.robustness.validation.testSampleCount} 样本</span></div><div className="quant-folds">{data.robustness.validation.walkForwardFolds.map(fold => <p key={fold.fold}><b>F{fold.fold}</b><span>{fold.startAt.slice(5)}~{fold.endAt.slice(5)}</span><strong className={tone(fold.averageExcessReturn)}>{pct(fold.averageExcessReturn, 2)}</strong></p>)}</div></div></section> : null}
      <section className="quant-panel span-2"><SectionTitle icon={Blocks} title="非结构化因子分层" note="把重要度、置信度、信息增量和观点强度按样本三等分，观察高分组是否稳定优于低分组。" /><div className="quant-factor-grid">{data.factorAnalysis.map(factor => <article key={factor.factor}><header><strong>{factor.label}</strong><span className={tone(factor.highLowSpread)}>高-低 {pct(factor.highLowSpread, 2)}</span></header><div>{factor.buckets.map(bucket => <p key={bucket.bucket}><b>{bucket.bucket}</b><span className={tone(bucket.averageExcessReturn)}>{pct(bucket.averageExcessReturn, 2)}</span><small>{bucket.sampleCount} 样本 · 胜率 {pct(bucket.winRate)}</small></p>)}</div></article>)}</div></section>
      <section className="quant-panel span-2 quant-trade-translation"><SectionTitle icon={CircleDollarSign} title="交易转译" note="把研究证据翻译成可验证规则；这里不会生成或发送订单。" aside={<span className={`quant-verdict-tag ${verdictTone}`}>{verdict}</span>} /><div><article><span>1</span><strong>信号触发</strong><p>{data.rule.sourceQuery || '任意机构'}发布{data.rule.signalDirection === 'bullish' ? '看多' : data.rule.signalDirection === 'bearish' ? '看空' : '明确方向'}观点，重要度 ≥ {data.rule.minImportance}，置信度 ≥ {data.rule.minConfidence}。</p></article><article><span>2</span><strong>模拟入场</strong><p>{data.dataQuality.entryRule}；若涨跌停、停牌或缺失成交数据则跳过。</p></article><article><span>3</span><strong>模拟退出</strong><p>持有 {primary} 个交易日后退出，同时保留 {data.rule.holdingPeriods.join(' / ')} 日观察窗比较。</p></article><article><span>4</span><strong>仓位上限</strong><p>仅用于研究模拟：单股不超过组合的 {(100 / Math.max(1, data.rule.portfolioSize)).toFixed(1)}%，不代表实盘建议。</p></article><article><span>5</span><strong>停止采用</strong><p>样本外超额转负、置信区间持续穿零、成本后优势消失或数据新鲜度不足时停止使用。</p></article><article><span>6</span><strong>下一步验证</strong><p>{verdict === '可进入模拟观察' ? '进入模拟盘并持续比较模拟成交与回测成交偏差。' : '扩大时间窗口并等待更多到期样本，不进入实盘。'}</p></article></div></section>
      <section className="quant-panel"><SectionTitle icon={Target} title="多持有期对照" note="同时比较绝对收益与相对基准超额。" /><table><thead><tr><th>持有期</th><th>样本</th><th>胜率</th><th>平均</th><th>平均超额</th></tr></thead><tbody>{data.rule.holdingPeriods.map(period => <tr key={period}><td>{period}日</td><td>{metric(period)?.sampleCount ?? 0}</td><td>{pct(metric(period)?.winRate)}</td><td className={tone(metric(period)?.averageReturn)}>{pct(metric(period)?.averageReturn, 2)}</td><td className={tone(metric(period, true)?.averageReturn)}>{pct(metric(period, true)?.averageReturn, 2)}</td></tr>)}</tbody></table></section>
      <section className="quant-panel"><SectionTitle icon={RadioTower} title="机构胜率（收缩后）" note="至少 3 个到期样本进入主榜，小样本仅观察。" /><table><thead><tr><th>研究组</th><th>样本</th><th>校正胜率</th><th>超额</th></tr></thead><tbody>{data.researchGroupRankings.slice(0, 8).map(row => <tr key={row.researchGroup}><td>{row.researchGroup}</td><td>{row.matureCount}</td><td>{pct(row.adjustedWinRate)}</td><td className={tone(row.averageExcessReturn)}>{pct(row.averageExcessReturn, 2)}</td></tr>)}</tbody></table></section></div></main>;
}

function DataAssets({ catalog, data }: { catalog: EssayQuantCatalog | null; data: EssayQuantDashboard | null }) {
  const funnel = [['扫描语料', data?.dataQuality.notesScanned ?? 0], ['AI 已分析', data?.dataQuality.analyzedNoteCount ?? 0], ['解析到股票', data?.dataQuality.resolvedNoteCount ?? 0], ['重复事件过滤', data?.dataQuality.duplicateEventCount ?? 0], ['具备行情股票', data?.dataQuality.pricedSymbolCount ?? 0], ['到期有效事件', data?.summary.matureEventCount ?? 0]] as const;
  return <main className="quant-page quant-data-page"><section className="quant-panel"><SectionTitle icon={Database} title="数据资产地图" note="不是接口宣传清单：数量、最近时间和状态来自本地库实时查询。" /><table className="quant-data-table"><thead><tr><th>数据资产</th><th>本地记录</th><th>最近数据</th><th>研究用途</th><th>可用状态</th></tr></thead><tbody>{(catalog?.assets ?? []).map(item => <tr key={item.key}><td><Database />{item.name}</td><td className="mono">{item.count.toLocaleString()}</td><td className="mono">{item.latestAt || '—'}</td><td>{item.usage}</td><td><Status ok={item.status === 'ready'}>{item.status === 'ready' ? '可直接使用' : item.status === 'empty' ? '库为空' : '尚未建库'}</Status></td></tr>)}</tbody></table></section>
    <section className="quant-panel"><SectionTitle icon={FileSearch} title="本次样本漏斗" note="把“为什么最后只有这些样本”完整展示出来。" /><div className="quant-funnel">{funnel.map(([label, value], index) => <div key={label}><span>{index + 1}</span><p>{label}</p><strong>{Number(value).toLocaleString()}</strong>{index < funnel.length - 1 ? <ChevronRight /> : null}</div>)}</div><div className="quant-quality-notes"><div><strong>语料策略</strong><p>{data?.rule.rawNotePolicy === 'include' ? '纳入未分析语料（探索模式）' : '仅 AI 已分析且通过阈值的语料'}</p></div><div><strong>行情口径</strong><p>{data?.dataQuality.priceBasis ?? '等待回测'}</p></div><div><strong>基准与成本</strong><p>{data?.rule.benchmarkCode ?? '—'} · {data?.rule.transactionCostBps ?? 0}bp</p></div><div><strong>数据截止</strong><p>{data?.dataQuality.priceCutoff ?? '—'}</p></div></div></section></main>;
}

function RunHistory({ history, tasks, onOpenResult }: { history: EssayQuantRunHistory; tasks: EssayQuantTaskList; onOpenResult: (runId: number) => void }) {
  const statusLabel = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败' } as const;
  return <main className="quant-page quant-history-page">
    <section className="quant-panel"><SectionTitle icon={RadioTower} title="我的后台任务" note="任务由服务器独立执行；切换页面、关闭当前标签页都不会中断。每位用户只能查看自己的任务和结果。" aside={<span className="mono">{tasks.total} TASKS</span>} />
      {tasks.items.length ? <div className="quant-task-list">{tasks.items.map(task => <article key={task.taskId} className={`quant-task-card is-${task.status}`}>
        <header><div><strong>{task.name}</strong><small>{task.strategyType} · {task.taskId.slice(0, 8)}</small></div><span>{statusLabel[task.status]}</span></header>
        <p>{task.error || task.message}</p>
        <div className="quant-task-progress"><i style={{ width: `${task.progress}%` }} /></div>
        <footer><time>{task.completedAt || task.startedAt || task.createdAt ? new Date(task.completedAt || task.startedAt || task.createdAt || '').toLocaleString('zh-CN') : '等待调度'}</time>{task.status === 'completed' && task.resultRunId ? <button className="quant-primary" onClick={() => onOpenResult(task.resultRunId!)}>查看结果 <ChevronRight /></button> : <span>{task.progress}%</span>}</footer>
      </article>)}</div> : <EmptyState title="暂无后台任务" description="从策略工坊或自然语言回测提交任务后，会在这里持续显示进度。" />}
    </section>
    <section className="quant-panel"><SectionTitle icon={History} title="已完成任务" note="每项任务保存规则、数据截止与不可变结果快照；系统预计算不会出现在此处。" aside={<span className="mono">{history.total} TASKS</span>} />{history.items.length ? <table className="quant-history-table"><thead><tr><th>任务</th><th>研究名称</th><th>事件 / 到期</th><th>样本外超额</th><th>风险判断</th><th>行情截止</th><th>完成时间</th><th>结果</th></tr></thead><tbody>{history.items.map(row => <tr key={row.id}><td className="mono">#{row.id}</td><td><strong>{row.name}</strong><small>{row.strategyType}</small></td><td>{row.eventCount} / {row.matureEventCount}</td><td className={tone(row.outOfSampleExcess)}>{pct(row.outOfSampleExcess, 2)}</td><td><span className="quant-verdict-tag">{row.verdict || '等待判断'}</span></td><td className="mono">{row.priceCutoff || '—'}</td><td>{row.createdAt ? new Date(row.createdAt).toLocaleString('zh-CN') : '—'}</td><td><button className="quant-result-link" onClick={() => onOpenResult(row.id)}>查看</button></td></tr>)}</tbody></table> : <EmptyState title="暂无已完成任务" description="研究任务完成后，结果会自动保存到这里。" />}</section>
  </main>;
}
