import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowRight, CircleStop, LoaderCircle, Play, RefreshCw } from 'lucide-react';
import { jevConvertibleTApi } from '../api/jevConvertibleT';
import { getParsedApiError, toApiErrorMessage } from '../api/error';
import { useUserAccess } from '../contexts/UserAccessContext';
import { ConvertibleTResultView } from '../components/jevConvertibleT/ConvertibleTResult';
import type { ConvertibleAvailability, ConvertibleCapabilities, ConvertibleFactor, ConvertibleTConfig, ConvertibleTask, TradingResearch } from '../types/jevConvertibleT';
import './JevBacktestPage.css';
import './JevConvertibleTPage.css';

const FACTORS: Array<{ key: ConvertibleFactor; label: string; note: string }> = [
  { key: 'ohlcv', label: '量价', note: '必需；保留真实事件价格与原始成交量' },
  { key: 'stock', label: '关联正股', note: '可转债对应正股；华懋转债关联华懋科技 603306' },
  { key: 'sector', label: '板块', note: '需明确代理指数或当时有效的板块成分' },
  { key: 'market', label: '大盘', note: 'A股参考沪深300；其他市场须有对应历史指数数据' },
  { key: 'orderbook', label: '盘口', note: '仅在历史买卖盘真实可用时启用' },
  { key: 'conversion_premium', label: '转股溢价', note: '需要历史时点有效的转股价' },
];
const TASK_STATUS = { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' };
const n = (value?: number | null, digits = 2) => value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const activeTask = (task: ConvertibleTask) => task.status === 'queued' || task.status === 'running';
const denied = (error: unknown) => [401, 403].includes(getParsedApiError(error).status ?? 0);
const availabilityKey = (config: ConvertibleTConfig) => JSON.stringify([config.symbol.trim(), config.datasetId, config.startDate, config.endDate, config.eventGranularity]);
const sourceStatus = (status: string) => ({ available: '可用', partial: '部分可用', unavailable: '不可用', unverified: '待核验' } as Record<string, string>)[status] ?? status;

function defaults(): ConvertibleTConfig {
  const day = new Date(); day.setDate(day.getDate() - 1);
  while (day.getDay() === 0 || day.getDay() === 6) day.setDate(day.getDate() - 1);
  const date = [day.getFullYear(), String(day.getMonth() + 1).padStart(2, '0'), String(day.getDate()).padStart(2, '0')].join('-');
  return { symbol: '113677', startDate: date, endDate: date, basePositionValue: 200000, initialCash: 200000,
    eventGranularity: 'tick', decisionIntervalSeconds: 60, lookbackEvents: 60,
    factors: ['ohlcv', 'stock', 'sector', 'market'], orderQuantity: 100, latencyMs: 1000, participationRate: .1,
    commissionBps: 1, minCommission: 0, slippageBps: 2, restoreBaseAtClose: true, confidenceThreshold: .65, maxCostUsd: .2, useModelLatency: true, orderTtlMs: 10000, otherFeeBps: 0 };
}

function ConvertibleWorkbench() {
  const [params, setParams] = useSearchParams(); const requestedRound = params.get('round');
  const publicSample = !!requestedRound; const requestedTask = requestedRound || params.get('task');
  const [config, setConfig] = useState<ConvertibleTConfig>(defaults);
  const [capabilities, setCapabilities] = useState<ConvertibleCapabilities | null>(null);
  const [availability, setAvailability] = useState<{ key: string; data: ConvertibleAvailability } | null>(null);
  const [tasks, setTasks] = useState<ConvertibleTask[]>([]); const [snapshot, setSnapshot] = useState<ConvertibleTask | null>(null);
  const [research, setResearch] = useState<TradingResearch | null>(null); const [researchError, setResearchError] = useState('');
  const [loading, setLoading] = useState(true); const [checking, setChecking] = useState(false); const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false); const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState(''); const [readError, setReadError] = useState(''); const [accessLost, setAccessLost] = useState(false);
  const [retry, setRetry] = useState(0);
  const resultAnchor = useRef<HTMLDivElement | null>(null);
  const controllers = useRef(new Set<AbortController>()); const invalidated = useRef(false); const dirty = useRef(false);
  const submitLock = useRef(false); const submission = useRef<{ key: string; id: string } | null>(null);
  const reader = useRef<AbortController | null>(null); const availabilityReader = useRef<AbortController | null>(null);
  const selected = snapshot?.taskId === requestedTask && (snapshot.visibility === 'public_sample') === publicSample ? snapshot : null;
  const instrument = capabilities?.instruments?.find(item => item.symbol === config.symbol && (!config.datasetId || item.datasetId === config.datasetId));
  const currency = instrument?.currency ?? (config.symbol === '113677' ? 'CNY' : '未确认');
  const currencyLabel = currency === 'USD' ? '美元 / USD' : currency === 'CNY' ? '人民币 / CNY' : currency;
  const rawUnit = instrument?.quantityUnit;
  const quantityUnit = rawUnit === 'shares' ? '股' : rawUnit === 'bonds' ? '张' : rawUnit ?? (config.symbol === '113677' ? '张' : '单位未确认');
  const lotSize = instrument?.lotSize ?? 10;
  const currentAvailability = availability?.key === availabilityKey(config) ? availability.data : null;
  const ready = !!capabilities?.configured && currentAvailability?.status === 'available'
    && currentAvailability.availableGranularities.includes(config.eventGranularity)
    && config.factors.every(factor => currentAvailability.factorAvailability[factor]?.available) && currency !== '未确认';
  const controller = useCallback(() => { const result = new AbortController(); controllers.current.add(result); return result; }, []);
  const loseAccess = useCallback(() => {
    invalidated.current = true; controllers.current.forEach(item => item.abort()); controllers.current.clear();
    setCapabilities(null); setAvailability(null); setTasks([]); setSnapshot(null); setResearch(null); setResearchError(''); setError(''); setReadError(''); setAccessLost(true);
    setLoading(false); setChecking(false); setSubmitting(false); setCancelling(false); setHistoryLoading(false);
  }, []);
  const mergeTask = useCallback((task: ConvertibleTask) => setTasks(previous => [task, ...previous.filter(item => item.taskId !== task.taskId)].sort((a, b) => b.createdAt.localeCompare(a.createdAt)).slice(0, 20)), []);

  useEffect(() => { const owned = controllers.current; return () => { invalidated.current = true; owned.forEach(item => item.abort()); owned.clear(); }; }, []);
  useEffect(() => {
    invalidated.current = false; const request = controller();
    void Promise.allSettled([jevConvertibleTApi.capabilities(request.signal), jevConvertibleTApi.tasks(request.signal), jevConvertibleTApi.research(request.signal)]).then(([capabilityRead, historyRead, researchRead]) => {
      if (request.signal.aborted || invalidated.current) return;
      if ([capabilityRead, historyRead, researchRead].some(item => item.status === 'rejected' && denied(item.reason))) { loseAccess(); return; }
      if (capabilityRead.status === 'fulfilled') {
        const cap = capabilityRead.value; setCapabilities(cap);
        const initial = { ...defaults(), ...cap.defaults };
        const choice = cap.instruments?.find(item => item.symbol === initial.symbol && (!initial.datasetId || item.datasetId === initial.datasetId));
        if (choice) { initial.datasetId = choice.datasetId; initial.accountMode = choice.accountMode ?? choice.simulationAccount ?? initial.accountMode; }
        if (!dirty.current) setConfig(initial);
        setAvailability({ key: availabilityKey(initial), data: cap.dataAvailability });
      }
      if (historyRead.status === 'fulfilled') setTasks(historyRead.value.items);
      if (researchRead.status === 'fulfilled') setResearch(researchRead.value);
      else setResearchError(toApiErrorMessage(researchRead.reason));
      setError([capabilityRead, historyRead].filter(item => item.status === 'rejected').map(item => toApiErrorMessage(item.reason)).join('；')); setLoading(false);
    }).finally(() => controllers.current.delete(request));
    document.title = 'Jev 秒级交易研究 - 乐子乌智能投研平台';
    return () => request.abort();
  }, [controller, loseAccess]);
  useEffect(() => {
    if (!requestedTask || accessLost) return;
    const owned = controllers.current; const request = controller(); reader.current = request;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try {
        const task = await (publicSample ? jevConvertibleTApi.researchRound : jevConvertibleTApi.task)(requestedTask, request.signal);
        if (request.signal.aborted || invalidated.current) return;
        if (task.taskId !== requestedTask || (publicSample && task.visibility !== 'public_sample')) throw new Error('返回的任务编号或公开属性与所选记录不一致。');
        setSnapshot(task); if (!publicSample) mergeTask(task); setReadError('');
        if (!publicSample && activeTask(task)) timer = setTimeout(() => void read(), 2500);
      } catch (failure) { if (!request.signal.aborted && !invalidated.current) { if (denied(failure)) loseAccess(); else setReadError(toApiErrorMessage(failure)); } }
    };
    void read(); return () => { request.abort(); owned.delete(request); if (timer) clearTimeout(timer); };
  }, [requestedTask, publicSample, retry, accessLost, controller, loseAccess, mergeTask]);

  const selectedTaskId = selected?.taskId;
  useEffect(() => {
    if (selectedTaskId) resultAnchor.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
  }, [selectedTaskId]);
  useEffect(() => {
    if (research?.status !== 'running' || accessLost) return;
    const owned = controllers.current; const request = controller();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const next = await jevConvertibleTApi.research(request.signal);
        if (request.signal.aborted || invalidated.current) return;
        setResearch(next); setResearchError('');
        if (next.status === 'running') timer = setTimeout(() => void read(), 7500);
      } catch (failure) {
        if (request.signal.aborted || invalidated.current) return;
        if (denied(failure)) { loseAccess(); return; }
        setResearchError(toApiErrorMessage(failure));
        timer = setTimeout(() => void read(), 7500);
      }
    };
    timer = setTimeout(() => void read(), 7500);
    return () => { clearTimeout(timer); request.abort(); owned.delete(request); };
  }, [research?.status, accessLost, controller, loseAccess]);

  const checkAvailability = async () => {
    if (accessLost || checking) return;
    availabilityReader.current?.abort(); const request = controller(); availabilityReader.current = request;
    const key = availabilityKey(config); setChecking(true); setError('');
    try { const data = await jevConvertibleTApi.availability(config, request.signal); if (!request.signal.aborted && !invalidated.current) setAvailability({ key, data }); }
    catch (failure) { if (!request.signal.aborted && !invalidated.current) { setAvailability(null); if (denied(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); } }
    finally { controllers.current.delete(request); if (!request.signal.aborted) setChecking(false); }
  };
  const refreshHistory = async () => {
    if (accessLost || historyLoading) return; const request = controller(); setHistoryLoading(true); setError('');
    try { const history = await jevConvertibleTApi.tasks(request.signal); if (!request.signal.aborted && !invalidated.current) setTasks(history.items); }
    catch (failure) { if (!request.signal.aborted && !invalidated.current) { if (denied(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); } }
    finally { controllers.current.delete(request); if (!request.signal.aborted) setHistoryLoading(false); }
  };
  const start = async (event: FormEvent) => {
    event.preventDefault(); if (!ready || submitLock.current || accessLost) return;
    if (config.startDate > config.endDate) { setError('开始日期不能晚于结束日期。'); return; }
    const evaluationStart = config.evaluationStart?.trim() || '';
    const evaluationEnd = config.evaluationEnd?.trim() || '';
    if (!!evaluationStart !== !!evaluationEnd) { setError('日内研究起止时间须同时填写，或同时留空。'); return; }
    if (evaluationStart && evaluationEnd) {
      const zonedIso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
      if (![evaluationStart, evaluationEnd].every(value => zonedIso.test(value) && Number.isFinite(Date.parse(value)))) { setError('日内研究时间须为含时区的 ISO8601，例如 2025-09-16T14:00:00-04:00。'); return; }
      if (Date.parse(evaluationEnd) <= Date.parse(evaluationStart)) { setError('日内研究结束时间须晚于开始时间。'); return; }
      if (evaluationStart.slice(0, 10) < config.startDate || evaluationEnd.slice(0, 10) > config.endDate || evaluationStart.slice(0, 10) > evaluationEnd.slice(0, 10)) { setError('日内研究起止时间须位于所选数据日期区间内。'); return; }
    }
    if (config.orderQuantity % lotSize !== 0) { setError(`每笔数量须为 ${lotSize} 的整数倍。`); return; }
    const key = JSON.stringify(config); if (submission.current?.key !== key) submission.current = { key, id: crypto.randomUUID() };
    submitLock.current = true; setSubmitting(true); setError(''); const request = controller();
    try {
      const task = await jevConvertibleTApi.start(config, submission.current.id, request.signal);
      if (request.signal.aborted || invalidated.current) return;
      submission.current = null; mergeTask(task); setSnapshot(task); setReadError('');
      const next = new URLSearchParams(params); next.delete('round'); next.set('task', task.taskId); setParams(next);
    } catch (failure) { if (!request.signal.aborted && !invalidated.current) { if (denied(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); } }
    finally { controllers.current.delete(request); submitLock.current = false; if (!request.signal.aborted) setSubmitting(false); }
  };
  const cancel = async () => {
    if (!selected || publicSample || !activeTask(selected) || cancelling || accessLost) return;
    const request = controller(); reader.current?.abort(); setCancelling(true); setError('');
    try { const task = await jevConvertibleTApi.cancel(selected.taskId, request.signal); if (!request.signal.aborted && !invalidated.current) { setSnapshot(current => current?.taskId === task.taskId ? task : current); mergeTask(task); } }
    catch (failure) { if (!request.signal.aborted && !invalidated.current) { if (denied(failure)) loseAccess(); else setError(toApiErrorMessage(failure)); } }
    finally { controllers.current.delete(request); if (!request.signal.aborted) { setCancelling(false); setRetry(value => value + 1); } }
  };
  const edit = <K extends keyof ConvertibleTConfig>(key: K, value: ConvertibleTConfig[K]) => { dirty.current = true; setConfig(current => ({ ...current, [key]: value })); };
  const numberField = (key: keyof ConvertibleTConfig, label: string, min: number, max: number, step: number | 'any' = 1) => <label>{label}<input name={key} type="number" required min={min} max={max} step={step} value={String(config[key])} onChange={event => edit(key, Number(event.target.value))} /></label>;
  const toggleFactor = (factor: ConvertibleFactor) => edit('factors', config.factors.includes(factor) ? config.factors.filter(item => item !== factor) : [...config.factors, factor]);

  if (accessLost) return <main className="jev-page"><h1>Jev 秒级交易研究</h1><div role="alert" className="jev-alert">访问权限已变化，当前账号的实验任务和结果已清除。</div></main>;
  return <main className="jev-page cbt-page">
    <header className="jev-page-heading"><div><h1>Jev 秒级交易研究</h1><p>{instrument?.name ?? capabilities?.instrument?.name ?? '华懋转债'} {config.symbol} · 底仓与现金独立记账 · 真实逐笔 / 秒级事件</p></div><span className="jev-input-label">历史事件 <ArrowRight size={14} /> 因子判断 <ArrowRight size={14} /> 成交模拟</span></header>
    {requestedTask ? <div ref={resultAnchor} className="cbt-selected-result">
    {requestedTask ? <section className="jev-panel"><div className="jev-section-heading"><div><h2>{selected ? `${selected.config.symbol} · ${TASK_STATUS[selected.status]}` : '正在读取实验任务'}</h2><p>{publicSample ? '公开研究样本' : '任务'} {requestedTask}</p></div>{selected && !publicSample && activeTask(selected) ? <button className="jev-secondary" disabled={cancelling} onClick={() => void cancel()}><CircleStop size={15} />{cancelling ? '正在取消' : '取消实验任务'}</button> : null}</div>{selected ? <><p aria-live="polite">{selected.message}</p>{publicSample ? <p className="jev-partial-note">公开历史样本，仅供回放查看；不会重跑模型，也不属于当前账号的私有任务。</p> : null}{activeTask(selected) ? <progress aria-label="做T回测进度" max={100} value={selected.progress} /> : null}<p className="cbt-usage">模型请求 {n(selected.usage.requests, 0)} · 缓存命中 {n(selected.usage.cached, 0)} · {selected.usage.costIsEstimate ? '未核验预算估值' : '费用'} ${n(selected.usage.costUsd, 4)}</p>{selected.status === 'failed' ? <div className="jev-alert" role="alert">实验失败：{selected.error || selected.message}</div> : null}</> : null}{readError ? <div className="jev-alert" role="alert">{readError}<button className="jev-text-button" onClick={() => setRetry(value => value + 1)}>重新读取任务</button></div> : null}</section> : null}
    {selected && (selected.result?.equity.length || selected.preview?.equity.length) ? <ConvertibleTResultView key={selected.taskId} task={selected} /> : <section className="jev-panel cbt-result-placeholder"><h2>底仓基准与增量做T收益</h2><div className="cbt-placeholder-columns"><div><strong>底仓持有基准</strong><p>持有初始底仓、现金不动，作为同一区间对照。</p></div><div><strong>增量做T收益</strong><p>策略权益相对底仓持有基准的差额，不等同于已落袋收益。</p></div><div><strong>账户总收益</strong><p>包括底仓涨跌和做T影响；有实际回测记录后再展示数值。</p></div></div></section>}
    </div> : null}
    <section className="jev-panel cbt-data-panel" aria-labelledby="cbt-data-heading">
      <div className="jev-section-heading"><div><h2 id="cbt-data-heading">数据可用性</h2><p>核验标的与所选因子的共同历史覆盖；不将分钟行情插值成逐笔或秒数据。</p></div><button type="button" className="jev-secondary" disabled={loading || checking} onClick={() => void checkAvailability()}><RefreshCw size={14} className={checking ? 'jev-spin' : ''} />{checking ? '正在核验' : '核验数据（免费）'}</button></div>
      <div className="cbt-data-status"><strong>{loading ? '正在读取实测状态' : currentAvailability?.status === 'available' ? '所选区间数据可用' : currentAvailability?.status === 'partial' ? '数据覆盖不完整，尚不可回测' : currentAvailability ? '所选区间尚无可用秒级数据' : '参数已更改，请重新核验数据'}</strong><p>{currentAvailability?.message ?? '核验结果与证券、日期和事件粒度绑定。'}</p>{currentAvailability?.checkedAt ? <small>核验时间：{new Date(currentAvailability.checkedAt).toLocaleString('zh-CN', { hour12: false })}</small> : null}</div>
      {currentAvailability?.sources?.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="各数据源实际覆盖"><table><thead><tr><th>数据源</th><th>实测状态</th><th>粒度 / 条数</th><th>实际历史覆盖</th><th>说明</th></tr></thead><tbody>{currentAvailability.sources.map(source => <tr key={source.key}><td>{source.label}</td><td>{sourceStatus(source.status)}</td><td>{source.frequency || '—'}{(source.count ?? source.rows) != null ? <small>{n(source.count ?? source.rows, 0)} 条</small> : null}</td><td>{source.coverageStart || '未确认'}<small>至 {source.coverageEnd || '未确认'}</small></td><td className="jev-reason">{source.reason || '—'}</td></tr>)}</tbody></table></div> : null}
    </section>
    <form id="cbt-form" className="jev-panel cbt-config-panel" onSubmit={event => void start(event)}>
      <div className="jev-section-heading"><div><h2>实验条件</h2><p>事件粒度决定行情精度；模型判断间隔单独控制，不等同于逐秒调用模型。</p></div></div>
      <fieldset disabled={loading || submitting} className="cbt-config-grid">
        <label>研究标的{capabilities?.instruments?.length ? <select value={config.symbol + '|' + (config.datasetId ?? '')} onChange={event => { const item = capabilities.instruments?.find(item => item.symbol + '|' + (item.datasetId ?? '') === event.target.value); if (!item) return; dirty.current = true; setConfig(current => ({ ...current, symbol: item.symbol, datasetId: item.datasetId, eventGranularity: item.granularity ?? item.availableGranularities?.[0] ?? current.eventGranularity, accountMode: item.accountMode ?? item.simulationAccount ?? (item.currency === 'USD' ? 'unlevered_margin' : 'cash_t0'), startDate: item.defaultStartDate ?? current.startDate, endDate: item.defaultEndDate ?? item.endDate ?? item.defaultStartDate ?? current.endDate, orderQuantity: Math.max(item.lotSize, Math.floor(current.orderQuantity / item.lotSize) * item.lotSize) })); }}>
          {capabilities.instruments.map(item => <option key={item.symbol + '|' + (item.datasetId ?? '')} value={item.symbol + '|' + (item.datasetId ?? '')}>{item.name} {item.symbol} · {item.currency}{item.status && item.status !== 'available' ? ' · 数据未就绪' : ''}</option>)}
        </select> : <input value={config.symbol} required onChange={event => edit('symbol', event.target.value)} />}</label>
        <label>开始日期<input type="date" required value={config.startDate} onChange={event => edit('startDate', event.target.value)} /></label>
        <label>结束日期<input type="date" required value={config.endDate} onChange={event => edit('endDate', event.target.value)} /></label>
        <label>日内研究开始（ISO8601，含时区）<input type="text" value={config.evaluationStart ?? ''} placeholder="2025-09-16T14:00:00-04:00" onChange={event => edit('evaluationStart', event.target.value)} /></label>
        <label>日内研究结束（ISO8601，含时区）<input type="text" value={config.evaluationEnd ?? ''} placeholder="2025-09-16T15:55:00-04:00" onChange={event => edit('evaluationEnd', event.target.value)} /></label>
        <p className="cbt-full-width jev-source-note">日内起止须成对填写；同时留空表示整个所选日期区间。按输入的时区原样提交，不转换为本机时间。载入历史实验时保留其研究窗口。</p>
        <label>真实事件粒度<select value={config.eventGranularity} onChange={event => edit('eventGranularity', event.target.value as ConvertibleTConfig['eventGranularity'])}><option value="tick">真实逐笔 / Tick</option><option value="1s">真实 1 秒</option></select></label>
        {numberField('basePositionValue', `初始底仓市值（${currencyLabel}）`, 0, 100000000, 100)}
        {numberField('initialCash', `初始可用现金（${currencyLabel}）`, 1, 100000000, 1)}
        {numberField('orderQuantity', `每笔数量（${quantityUnit}，${lotSize} 的整数倍）`, lotSize, 100000, lotSize)}
        {numberField('decisionIntervalSeconds', '模型判断间隔（秒）', 1, 3600)}
        {numberField('lookbackEvents', '输入窗口（事件数）', 20, 252)}
        {numberField('confidenceThreshold', '置信度阈值（0–1）', 0, 1, .01)}
        {numberField('latencyMs', '执行延迟（毫秒）', 0, 60000, 100)}
        {numberField('participationRate', '成交参与率（最高 25%）', .001, .25, .001)}
        {numberField('commissionBps', '佣金（bp）', 0, 100, .1)}
        {numberField('minCommission', `最低佣金（${currency}）`, 0, 1000, .1)}
        {numberField('slippageBps', '滑点（bp）', 0, 100, .1)}
        {numberField('maxCostUsd', '单次模型费用上限（美元）', .001, capabilities?.limits.maxCostUsd ?? .5, 'any')}
        {numberField('orderTtlMs', '委托有效期（毫秒）', 100, 300000, 100)}
        {numberField('otherFeeBps', '其他费用（bp）', 0, 100, .1)}
        <label className="jev-checkbox cbt-full-width"><input type="checkbox" checked={config.useModelLatency ?? true} onChange={event => edit('useModelLatency', event.target.checked)} />计入实测模型延迟（执行等待取其与配置延迟的较大值）</label>
        <label className="jev-checkbox cbt-full-width"><input type="checkbox" checked={config.restoreBaseAtClose} onChange={event => edit('restoreBaseAtClose', event.target.checked)} />收盘尝试恢复初始底仓数量（受实际可成交条件约束）</label>
      </fieldset>
      <div className="cbt-currency-notice"><strong>当前记账币种：{currencyLabel}</strong><p>底仓和现金均以该币种计价，不进行隐含汇率换算。{config.accountMode === 'unlevered_margin' ? '美股采用不借款、不做空的保证金账户假设；不模拟现金账户结算限制。' : '可转债使用现金 T+0 库存约束。'}</p></div>
      <div className="cbt-factor-heading"><h2>模型因子</h2><p>每个因子需有同一历史时点可用的数据；盘口和溢价仅在对应历史数据存在时可选。</p></div>
      <div className="cbt-factors">{FACTORS.map(factor => { const available = currentAvailability?.factorAvailability[factor.key]; const checked = config.factors.includes(factor.key); return <label key={factor.key} className="cbt-factor"><div><input type="checkbox" checked={checked} disabled={factor.key === 'ohlcv' || submitting || (!available?.available && !checked)} onChange={() => toggleFactor(factor.key)} /><strong>{factor.label}{factor.key === 'ohlcv' ? '（必需）' : ''}</strong><span>{available?.available ? '可用' : '待数据'}</span></div><p>{factor.note}</p>{available?.reason ? <small>{available.reason}</small> : null}</label>; })}</div>
      <div className="cbt-submit"><div><strong>底仓 {n(config.basePositionValue, 0)} {currency} + 现金 {n(config.initialCash, 0)} {currency}</strong><p>数据核验与历史查看免费。执行完整回测会调用付费 Jev，单次上限 ${n(config.maxCostUsd, 3)}。</p>{!ready ? <p>数据、所选因子和模型全部就绪后才能提交。</p> : null}</div><button type="submit" className="jev-primary" disabled={!ready || submitting}>{submitting ? <LoaderCircle size={16} className="jev-spin" /> : <Play size={16} />}{submitting ? '正在提交' : '开始秒级回测（付费）'}</button></div>
      {selected ? <button type="button" className="jev-text-button" onClick={() => { dirty.current = true; setConfig({ ...defaults(), ...selected.config }); }}>载入所选实验参数</button> : null}
      {error ? <div className="jev-alert" role="alert">{error}</div> : null}
    </form>
    <section className="jev-panel cbt-research" aria-labelledby="cbt-research-heading"><div className="jev-section-heading"><div><h2 id="cbt-research-heading">{research?.title || '多轮对照研究'}</h2><p>{research?.description || '已完成的实测研究将在此展示。'}</p></div><span>{research?.status === 'running' ? '研究进行中' : research?.status === 'completed' ? '研究已完成' : '尚未开始'}</span></div>
      {researchError ? <div className="jev-alert" role="alert">研究记录读取失败：{researchError}</div> : null}
      {research?.rounds.length ? <div className="jev-table-scroll" tabIndex={0} role="region" aria-label="实测研究轮次"><table><thead><tr><th>轮次 / 标的</th><th>因子 / 数据划分</th><th>历史区间</th><th>增量收益</th><th>总收益</th><th>最大回撤</th><th>成交数</th><th>模型调用 / 费用</th><th>延迟中位数</th><th>记录</th></tr></thead><tbody>{research.rounds.map(round => <tr key={round.id}><td>{round.name}<small>{round.symbol} · {round.currency}</small></td><td>{round.factors.map(factor => FACTORS.find(item => item.key === factor)?.label ?? factor).join('、')}<small>{round.split}</small></td><td>{round.period}</td><td>{n(round.incrementalTPnl)} {round.currency}</td><td>{n(round.totalPnl)} {round.currency}</td><td>{round.maxDrawdownPct == null ? '—' : `${n(round.maxDrawdownPct)}%`}</td><td>{n(round.tradeCount, 0)}</td><td>{n(round.modelRequests, 0)} 次<small>${n(round.modelCostUsd, 4)}</small></td><td>{n(round.medianLatencyMs, 0)} ms</td><td className="jev-reason">{Array.isArray(round.notes) ? round.notes.join('；') : round.notes || '—'}{round.taskId ? <button className="jev-text-button" onClick={() => { setSnapshot(null); setReadError(''); const next = new URLSearchParams(params); next.delete('task'); next.set('round', round.taskId!); setParams(next); if (requestedRound === round.taskId) setRetry(value => value + 1); }}>查看公开轮次</button> : null}</td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">{loading ? '正在读取研究记录…' : '尚无已完成的实测轮次。'}</p>}
      {research?.conclusion ? <p className="cbt-research-conclusion">{research.conclusion}</p> : null}{research?.limitations.length ? <ul className="jev-warning-list">{research.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul> : null}{research?.updatedAt ? <small>更新于 {research.updatedAt}</small> : null}
    </section>
    <section className="jev-panel"><div className="jev-section-heading"><div><h2>秒级研究任务</h2><p>当前账号最近 20 次任务</p></div><button className="jev-secondary" disabled={loading || historyLoading} onClick={() => void refreshHistory()}><RefreshCw size={14} />刷新实验历史</button></div>{tasks.length ? <div className="jev-table-scroll"><table><thead><tr><th>证券 / 任务</th><th>区间</th><th>事件 / 模型间隔</th><th>状态</th><th>操作</th></tr></thead><tbody>{tasks.map(task => <tr key={task.taskId}><td>{task.config.symbol}<small>{task.taskId.slice(0, 8)}</small></td><td>{task.config.startDate}<small>至 {task.config.endDate}</small></td><td>{task.config.eventGranularity} / {task.config.decisionIntervalSeconds} 秒</td><td>{TASK_STATUS[task.status]}</td><td><button className="jev-text-button" onClick={() => { setSnapshot(null); setReadError(''); const next = new URLSearchParams(params); next.delete('round'); next.set('task', task.taskId); setParams(next); if (requestedTask === task.taskId) setRetry(value => value + 1); }}>查看实验 {task.taskId.slice(0, 8)}</button></td></tr>)}</tbody></table></div> : <p className="jev-empty-inline">{loading ? '正在读取实验历史…' : '暂无秒级研究任务。'}</p>}</section>
  </main>;
}

export default function JevConvertibleTPage() {
  const access = useUserAccess();
  const key = JSON.stringify([access.user?.id, access.user?.status, access.authMethod, access.accessEnabled, access.loggedIn]);
  if (!access.loggedIn || access.isLoading) return <main className="jev-page"><p>正在确认秒级研究访问权限。</p></main>;
  return <ConvertibleWorkbench key={key} />;
}
