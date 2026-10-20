import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowDownToLine, ArrowRight, CircleStop, LoaderCircle, Play, RefreshCw } from 'lucide-react';
import { jevBacktestApi } from '../api/jevBacktest';
import { getParsedApiError, toApiErrorMessage } from '../api/error';
import { useUserAccess } from '../contexts/UserAccessContext';
import type { JevBacktestConfig, JevBacktestTask, JevCapabilities, JevTaskStatus } from '../types/jevBacktest';
import { JevReplay } from '../components/jevBacktest/JevReplay';
import './JevBacktestPage.css';

const JevConvertibleTPage = lazy(() => import('./JevConvertibleTPage'));
const JevLiveTPage = lazy(() => import('./JevLiveTPage'));

const STATUS: Record<JevTaskStatus, string> = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' };
const isActive = (task: JevBacktestTask) => task.status === 'queued' || task.status === 'running';
const number = (value: number | null | undefined, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const usd = (value: number | null | undefined) => value == null ? '—' : `$${number(value, 4)}`;
const errorIsAccess = (error: unknown) => [401, 403].includes(getParsedApiError(error).status ?? 0);

function defaultConfig(): JevBacktestConfig {
  const end = new Date(); const start = new Date(end); start.setDate(start.getDate() - 4);
  return {
    dataSource: 'sina',
    symbol: '', startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10),
    timeframe: '1min', positionPct: 1, settlementMode: 't1', stopLossPct: 0, takeProfitPct: 0, lookbackBars: 60, initialCash: 100000, confidenceThreshold: 0.65, commissionBps: 3,
    minCommission: 5, sellTaxBps: 5, slippageBps: 5, maxHoldingBars: 60,
    decisionInterval: 1, refreshPrices: true, maxCostUsd: 0.2,
  };
}

function TaskBadge({ status }: { status: JevTaskStatus }) {
  return <span className={`jev-badge jev-badge-${status}`}>{STATUS[status] ?? status}</span>;
}

function JevWorkbench({ allowed }: { allowed: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams(); const requestedTask = searchParams.get('task');
  const [capabilities, setCapabilities] = useState<JevCapabilities | null>(null);
  const [config, setConfig] = useState<JevBacktestConfig>(defaultConfig);
  const [tasks, setTasks] = useState<JevBacktestTask[]>([]);
  const [snapshot, setSnapshot] = useState<JevBacktestTask | null>(null);
  const [error, setError] = useState(''); const [readError, setReadError] = useState('');
  const [accessLost, setAccessLost] = useState(false);
  const [loading, setLoading] = useState(true); const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false); const [retry, setRetry] = useState(0);
  const [historyLoading, setHistoryLoading] = useState(false);
  const controllers = useRef(new Set<AbortController>()); const invalidated = useRef(false);
  const submitLock = useRef(false); const dirty = useRef(false);
  const submission = useRef<{ signature: string; id: string } | null>(null);
  const taskRequest = useRef<AbortController | null>(null);
  const selected = snapshot?.taskId === requestedTask && !accessLost ? snapshot : null;

  const makeController = useCallback(() => { const controller = new AbortController(); controllers.current.add(controller); return controller; }, []);
  const loseAccess = useCallback(() => {
    invalidated.current = true;
    controllers.current.forEach(controller => controller.abort()); controllers.current.clear();
    setCapabilities(null); setTasks([]); setSnapshot(null); setAccessLost(true); setError(''); setReadError('');
    setLoading(false); setHistoryLoading(false); setSubmitting(false); setCancelling(false);
  }, []);
  const mergeTask = useCallback((task: JevBacktestTask) => {
    setTasks(current => [task, ...current.filter(item => item.taskId !== task.taskId)].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 20));
  }, []);

  useEffect(() => {
    const ownedControllers = controllers.current;
    return () => { invalidated.current = true; ownedControllers.forEach(controller => controller.abort()); ownedControllers.clear(); };
  }, []);

  useEffect(() => {
    if (!allowed) return;
    // StrictMode replays the mount effect; each replay receives fresh requests.
    invalidated.current = false;
    const controller = makeController();
    void Promise.allSettled([jevBacktestApi.capabilities(controller.signal), jevBacktestApi.tasks(controller.signal)]).then(([capabilityRead, historyRead]) => {
      if (controller.signal.aborted || invalidated.current) return;
      if ([capabilityRead, historyRead].some(item => item.status === 'rejected' && errorIsAccess(item.reason))) { loseAccess(); return; }
      if (capabilityRead.status === 'fulfilled') {
        setCapabilities(capabilityRead.value);
        if (!dirty.current) setConfig(current => ({ ...current, ...capabilityRead.value.defaults }));
      }
      if (historyRead.status === 'fulfilled') setTasks(historyRead.value.items);
      const errors = [capabilityRead, historyRead].filter(item => item.status === 'rejected').map(item => toApiErrorMessage(item.reason));
      setError(errors.join('；')); setLoading(false);
    }).finally(() => controllers.current.delete(controller));
    document.title = 'Jev 交易回测 - 乐子乌超级价值';
    return () => controller.abort();
  }, [allowed, makeController, loseAccess]);

  useEffect(() => {
    if (!allowed || accessLost || !requestedTask) return;
    const trackedControllers = controllers.current;
    const controller = makeController(); taskRequest.current = controller;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const task = await jevBacktestApi.task(requestedTask, controller.signal);
        if (controller.signal.aborted || invalidated.current) return;
        if (task.taskId !== requestedTask) throw new Error('返回的任务与所选记录不一致，请重新读取。');
        setSnapshot(task); mergeTask(task); setReadError('');
        if (isActive(task)) timer = setTimeout(() => void read(), 2500);
      } catch (readFailure) {
        if (controller.signal.aborted || invalidated.current) return;
        if (errorIsAccess(readFailure)) { loseAccess(); return; }
        setReadError(toApiErrorMessage(readFailure));
      }
    };
    void read();
    return () => { controller.abort(); trackedControllers.delete(controller); if (timer) clearTimeout(timer); };
  }, [requestedTask, retry, allowed, accessLost, makeController, mergeTask, loseAccess]);

  const selectTask = (task: JevBacktestTask) => {
    setSnapshot(null); setReadError('');
    const next = new URLSearchParams(searchParams); next.set('task', task.taskId); setSearchParams(next);
    if (requestedTask === task.taskId) setRetry(current => current + 1);
  };
  const refreshHistory = async () => {
    if (historyLoading || accessLost) return;
    const controller = makeController(); setHistoryLoading(true); setError('');
    try {
      const history = await jevBacktestApi.tasks(controller.signal);
      if (!controller.signal.aborted && !invalidated.current) setTasks(history.items);
    } catch (failure) {
      if (!controller.signal.aborted && !invalidated.current) { if (errorIsAccess(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); }
    } finally { controllers.current.delete(controller); if (!controller.signal.aborted) setHistoryLoading(false); }
  };

  const start = async (event: FormEvent) => {
    event.preventDefault();
    if (submitLock.current || !capabilities?.configured || accessLost || !allowed) return;
    if (!config.symbol.trim()) { setError('请输入股票代码。'); return; }
    if (config.startDate > config.endDate) { setError('开始日期不能晚于结束日期。'); return; }
    if (!(config.maxCostUsd > 0 && config.maxCostUsd <= capabilities.limits.maxCostUsd)) { setError(`单次费用上限应大于 $0，且不超过 ${usd(capabilities.limits.maxCostUsd)}。`); return; }
    const signature = JSON.stringify(config);
    if (submission.current?.signature !== signature) submission.current = { signature, id: crypto.randomUUID() };
    submitLock.current = true; setSubmitting(true); setError('');
    const controller = makeController();
    try {
      const task = await jevBacktestApi.start(config, submission.current.id, controller.signal);
      if (controller.signal.aborted || invalidated.current) return;
      submission.current = null; mergeTask(task); setSnapshot(task); setReadError('');
      const next = new URLSearchParams(searchParams); next.set('task', task.taskId); setSearchParams(next);
    } catch (failure) {
      if (!controller.signal.aborted && !invalidated.current) { if (errorIsAccess(failure)) loseAccess(); else setError(`${toApiErrorMessage(failure)} 若提交结果不明，先刷新任务历史；相同参数重试会复用请求编号。`); }
    } finally { controllers.current.delete(controller); submitLock.current = false; if (!controller.signal.aborted) setSubmitting(false); }
  };

  const cancel = async () => {
    if (!selected || !isActive(selected) || cancelling || accessLost) return;
    const controller = makeController(); setCancelling(true); setError(''); taskRequest.current?.abort();
    try {
      const task = await jevBacktestApi.cancel(selected.taskId, controller.signal);
      if (controller.signal.aborted || invalidated.current) return;
      setSnapshot(current => current?.taskId === task.taskId ? task : current); mergeTask(task);
    } catch (failure) {
      if (!controller.signal.aborted && !invalidated.current) { if (errorIsAccess(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); }
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted) { setCancelling(false); setRetry(current => current + 1); }
    }
  };
  const edit = <K extends keyof JevBacktestConfig>(key: K, value: JevBacktestConfig[K]) => { dirty.current = true; setConfig(current => ({ ...current, [key]: value })); };
  const numericField = (key: keyof JevBacktestConfig, label: string, min: number, max: number, step = 1) => <label>{label}<input type="number" name={key} min={min} max={max} step={step} required value={String(config[key])} onChange={event => edit(key, Number(event.target.value))} /></label>;

  if (!allowed || accessLost) return <main className="jev-page"><header className="jev-page-heading"><h1>Jev 交易回测</h1></header><div className="jev-alert" role="alert">访问权限已变化，当前账号的任务和结果已清除。请重新登录或刷新页面确认权限。</div></main>;

  return <main className="jev-page">
    <header className="jev-page-heading"><div><h1>Jev 交易回测</h1><p>加速回放分钟行情，跟随 Jev 的买卖决策查看利润变化。</p></div><span className="jev-input-label">OHLCV <ArrowRight size={14} /> BUY / SELL / HOLD</span></header>
    <div className="jev-workspace">
      <aside className="jev-parameters">
        <form id="jev-backtest-form" onSubmit={event => void start(event)}>
          <div className="jev-section-heading"><h2>回测参数</h2><span>分钟行情 · 历史模拟</span></div>
          <fieldset disabled={submitting || loading}>
            <label>分钟数据源<select value={config.dataSource} onChange={event => edit('dataSource', event.target.value as JevBacktestConfig['dataSource'])}><option value="sina">新浪公开分钟（近期历史）</option><option value="tushare">Tushare分钟（账户权限）</option></select></label>
            <p className="jev-source-note">{config.dataSource === 'sina' ? '新浪仅覆盖近期历史，超出覆盖区间会明确报错。' : 'Tushare 分钟数据需要当前账户具备相应接口权限。'}保留来源原始成交量，模型只比较相对量。</p>
            <label>股票代码<input name="symbol" value={config.symbol} onChange={event => edit('symbol', event.target.value)} placeholder="输入股票代码" autoComplete="off" maxLength={24} required /></label>
            <label>行情周期<select value={config.timeframe} onChange={event => edit('timeframe', event.target.value as JevBacktestConfig['timeframe'])}>{(capabilities?.timeframes ?? ['1min', '5min', '15min']).map(value => <option key={value} value={value}>{value.replace('min', ' 分钟')}</option>)}</select></label>
            <div className="jev-field-pair"><label>开始日期<input type="date" required value={config.startDate} onChange={event => edit('startDate', event.target.value)} /></label><label>结束日期<input type="date" required value={config.endDate} onChange={event => edit('endDate', event.target.value)} /></label></div>
            {numericField('initialCash', '初始资金（元）', 1, 100000000, 1)}
            <label>目标仓位（%）<input type="number" min="10" max="100" step="1" required value={Math.round(config.positionPct * 100)} onChange={event => edit('positionPct', Number(event.target.value) / 100)} /></label>
            <div className="jev-field-pair">{numericField('lookbackBars', '输入窗口（K线根数）', 20, 252)}{numericField('decisionInterval', '决策间隔（K线根数）', 1, 252)}</div>
            <div className="jev-field-pair">{numericField('confidenceThreshold', '置信度阈值（0–1）', 0, 1, 0.01)}{numericField('maxHoldingBars', '最长持仓（K线根数）', 1, 2000)}</div>
            <label>交易制度<select value={config.settlementMode} onChange={event => edit('settlementMode', event.target.value as JevBacktestConfig['settlementMode'])}><option value="t1">A 股 T+1（按交易日期）</option><option value="research_t0">研究 T+0（不代表 A 股可执行）</option></select></label>
            <div className="jev-field-pair">{numericField('stopLossPct', '止损（%，0 关闭）', 0, 100, 0.1)}{numericField('takeProfitPct', '止盈（%，0 关闭）', 0, 100, 0.1)}</div>
            <details className="jev-cost-settings"><summary>交易成本设置</summary><div className="jev-field-pair">{numericField('commissionBps', '佣金（bp）', 0, 100, 0.1)}{numericField('minCommission', '最低佣金（元）', 0, 100, 0.1)}{numericField('sellTaxBps', '卖出税（bp）', 0, 100, 0.1)}{numericField('slippageBps', '滑点（bp）', 0, 100, 0.1)}</div><p>1 bp = 0.01%。成本同时作用于 Jev 与基准。</p></details>
            {numericField('maxCostUsd', '本次模型费用上限（美元）', 0.001, capabilities?.limits.maxCostUsd ?? 0.5, 0.001)}
            <label className="jev-checkbox"><input type="checkbox" checked={config.refreshPrices} onChange={event => edit('refreshPrices', event.target.checked)} />获取最新历史行情</label>
          </fieldset>
          <p className="jev-parameter-note">{capabilities ? `每次最多评估 ${capabilities.limits.maxEvaluationBars} 根K线。` : '正在读取服务限制。'}仅向 Jev 输入量价。调整仓位、费用、阈值和交易制度可复用相同窗口的缓存信号；改变周期或输入窗口需重新生成。</p>
          {selected ? <button type="button" className="jev-secondary" disabled={submitting} onClick={() => { dirty.current = true; setConfig(current => ({ ...current, ...selected.config, dataSource: selected.config.dataSource ?? 'tushare', refreshPrices: false })); }}>载入所选任务参数，修改后重跑</button> : null}
        </form>
      </aside>
      <div className="jev-main-column">
        <section className="jev-panel jev-run-panel">
          <div className="jev-section-heading"><div><h2>运行状态</h2><p>{loading ? '正在检查模型配置与任务历史…' : capabilities?.configured ? `${capabilities.provider} · ${capabilities.model}` : 'Jev 尚未配置，当前不能开始回测。'}</p></div><span className={`jev-availability ${capabilities?.configured ? 'is-ready' : ''}`}>{loading ? '检查中' : capabilities?.configured ? '模型已配置' : '未就绪'}</span></div>
          {capabilities?.message ? <p>{capabilities.message}</p> : null}
          <div className="jev-run-controls"><div><span>本次模型费用上限</span><strong>{usd(config.maxCostUsd)}</strong><p>生成信号将调用付费 Jev。回放与查看历史免费；相同行情窗口的信号可缓存复用。</p></div><button className="jev-primary" type="submit" form="jev-backtest-form" disabled={loading || submitting || !capabilities?.configured}>{submitting ? <LoaderCircle className="jev-spin" size={17} /> : <Play size={17} />}{submitting ? '正在提交' : '生成信号并回测'}</button></div>
          {capabilities?.dataSources?.length ? <p className="jev-sources">行情来源：{capabilities.dataSources.join(' / ')}</p> : null}
          {error ? <div role="alert" className="jev-alert">{error}</div> : null}
        </section>
        {requestedTask ? <section className="jev-panel jev-task-panel" aria-label="当前任务状态">
          <div className="jev-section-heading"><div><h2>{selected ? `${selected.config.symbol} · ${STATUS[selected.status]}` : '正在读取所选任务'}</h2><p className="jev-task-id">任务 {requestedTask}</p></div>{selected ? <TaskBadge status={selected.status} /> : null}</div>
          <div aria-live="polite">{selected ? <><p>{selected.message}</p>{isActive(selected) ? <progress value={selected.progress} max={100} aria-label="回测进度" /> : null}<div className="jev-task-usage"><span>模型请求 {number(selected.usage.requests, 0)}</span><span>缓存命中 {number(selected.usage.cached, 0)}</span><span>{selected.usage.costIsEstimate ? '未核验预算估值' : '模型费用'} {usd(selected.usage.costUsd)}</span></div></> : null}</div>
          {selected?.status === 'failed' ? <div role="alert" className="jev-alert">回测失败：{selected.error || selected.message || '任务未完成，未生成有效收益结果。'}</div> : null}
          {selected?.status === 'cancelled' ? <p className="jev-empty-inline">任务已取消，{selected.usage.costIsEstimate ? '未核验预算估值' : '已发生的模型费用'}保留记录。</p> : null}
          {readError ? <div role="alert" className="jev-alert">状态读取失败：{readError}<button className="jev-text-button" onClick={() => { setReadError(''); setRetry(current => current + 1); }}>重新读取状态</button></div> : null}
          {selected && isActive(selected) ? <button className="jev-secondary" onClick={() => void cancel()} disabled={cancelling}><CircleStop size={15} />{cancelling ? '正在请求取消' : '取消任务'}</button> : null}
        </section> : null}
        {selected && ((selected.status === 'completed' && selected.result) || selected.preview?.equity.length) ? <JevReplay key={selected.taskId} task={selected} /> : <section className="jev-panel jev-empty-results"><ArrowDownToLine size={25} /><h2>{selected?.status === 'failed' ? '本次没有有效回测结果' : selected?.status === 'completed' ? '任务未返回结果，请重新读取状态' : '回测结果将在此展示'}</h2><p>任务运行时显示已处理行情及账户变化。完成后可播放、暂停、调速或拖动进度，也可修改条件复用信号回测。</p></section>}
      </div>
    </div>
    <section className="jev-panel jev-history" aria-labelledby="jev-history-heading">
      <div className="jev-section-heading"><div><h2 id="jev-history-heading">任务历史</h2><p>当前账号最近 20 次任务 · 查看记录不会重新运行回测</p></div><button className="jev-secondary" onClick={() => void refreshHistory()} disabled={historyLoading || loading}><RefreshCw size={14} className={historyLoading ? 'jev-spin' : ''} />{historyLoading ? '正在刷新' : '刷新历史'}</button></div>
      {tasks.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="任务历史，可横向滚动"><table><thead><tr><th>股票 / 任务</th><th>回测区间</th><th>数据源</th><th>状态</th><th>创建时间</th><th>模型费用 / 预算估值</th><th>操作</th></tr></thead><tbody>{tasks.map(task => <tr key={task.taskId} className={requestedTask === task.taskId ? 'is-selected' : undefined}><td>{task.config.symbol}<small>{task.taskId.slice(0, 8)}</small></td><td>{task.config.startDate}<small>至 {task.config.endDate}</small></td><td>{task.config.dataSource === 'sina' ? '新浪公开分钟' : 'Tushare 分钟'}</td><td><TaskBadge status={task.status} /></td><td>{new Date(task.createdAt).toLocaleString('zh-CN', { hour12: false })}</td><td>{usd(task.usage.costUsd)}{task.usage.costIsEstimate ? <small>未核验预算估值</small> : null}</td><td><button className="jev-text-button" onClick={() => selectTask(task)} aria-label={`查看 ${task.config.symbol} 任务 ${task.taskId.slice(0, 8)}`}>查看{isActive(task) ? '进度' : '结果'}<ArrowRight size={14} /></button></td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">{loading ? '正在读取任务历史…' : '暂无回测任务。'}</p>}
    </section>
  </main>;
}

export default function JevBacktestPage() {
  const access = useUserAccess();
  const [params, setParams] = useSearchParams();
  const eventLab = params.get('lab') === 'convertible-t';
  const liveLab = params.get('lab') === 'live-t';
  const switchLab = (lab?: string) => { const next = new URLSearchParams(params); next.delete('task'); next.delete('round'); next.delete('run'); if (lab) next.set('lab', lab); else next.delete('lab'); setParams(next); };
  // The keyed subtree resets synchronously on owner or permission change;
  // a prior owner's snapshot never renders for the new identity.
  const scope = JSON.stringify([access.user?.id, access.user?.status, access.authMethod, access.accessEnabled, access.loggedIn]);
  return <><nav className="jev-lab-tabs" aria-label="Jev研究类型"><button type="button" aria-pressed={!eventLab && !liveLab} onClick={() => switchLab()}>分钟行情回测</button><button type="button" aria-pressed={eventLab} onClick={() => switchLab('convertible-t')}>秒级交易研究</button><button type="button" aria-pressed={liveLab} onClick={() => switchLab('live-t')}>实时纸上做T</button></nav>{liveLab ? <Suspense fallback={<main className="jev-page">正在加载纸上试跑…</main>}><JevLiveTPage key={scope} /></Suspense> : eventLab ? <Suspense fallback={<main className="jev-page">正在加载秒级研究…</main>}><JevConvertibleTPage key={scope} /></Suspense> : <JevWorkbench key={scope} allowed={access.loggedIn && !access.isLoading} />}</>;
}
