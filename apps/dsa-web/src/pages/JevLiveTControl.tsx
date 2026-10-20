import { useEffect, useRef, useState } from 'react';
import { jevLiveTApi } from '../api/jevLiveT';
import { getParsedApiError, toApiErrorMessage } from '../api/error';
import type { LiveTControl, LiveTControlPhase } from '../types/jevLiveT';

const phases: Record<LiveTControlPhase, string> = {
  stopped: '已关闭', starting: '正在开启', running: '运行中', market_closed: '休市暂停',
  stopping: '正在关闭', budget_exhausted: '累计预算已用尽', failed: '运行失败',
};
const money = (value?: number | null) => value == null || !Number.isFinite(value) ? '—' : `$${value.toFixed(4)}`;
const denied = (error: unknown) => [401, 403].includes(getParsedApiError(error).status ?? 0);

export default function JevLiveTControl({ selectedRunId, formatTime, onAccessLost, onChanged, onOpenRun }: {
  selectedRunId: string | null;
  formatTime: (value?: string | null) => string;
  onAccessLost: () => void;
  onChanged: (state: LiveTControl) => void;
  onOpenRun: (runId: string) => void;
}) {
  const [state, setState] = useState<LiveTControl | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [notice, setNotice] = useState('');
  const [retry, setRetry] = useState(0);
  const mounted = useRef(false);
  const busy = useRef(false);
  const generation = useRef(0);
  const readAbort = useRef<AbortController | null>(null);
  const writeAbort = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const callbacks = useRef({ onAccessLost, onChanged });
  callbacks.current = { onAccessLost, onChanged };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; readAbort.current?.abort(); writeAbort.current?.abort(); clearTimeout(timer.current); };
  }, []);
  useEffect(() => {
    let disposed = false;
    const read = async () => {
      if (disposed || busy.current) return;
      const current = ++generation.current;
      const controller = new AbortController(); readAbort.current = controller;
      let delay = 10000;
      try {
        const result = await jevLiveTApi.control(controller.signal);
        if (disposed || controller.signal.aborted || current !== generation.current) return;
        setState(result); setError(''); setUncertain(false);
        if (result.enabled || ['starting', 'stopping'].includes(result.phase)) delay = result.phase === 'market_closed' ? 10000 : 2000;
      } catch (failure) {
        if (disposed || controller.signal.aborted || current !== generation.current) return;
        if (denied(failure)) { setState(null); callbacks.current.onAccessLost(); return; }
        setError(`控制状态读取失败：${toApiErrorMessage(failure)}`); setUncertain(true);
      } finally {
        if (!disposed && !controller.signal.aborted && current === generation.current) { setLoading(false); timer.current = setTimeout(() => void read(), delay); }
      }
    };
    void read();
    return () => { disposed = true; readAbort.current?.abort(); clearTimeout(timer.current); };
  }, [retry]);

  const change = async (enabled: boolean) => {
    if (busy.current || !state?.canControl || uncertain) return;
    busy.current = true; generation.current += 1; clearTimeout(timer.current); readAbort.current?.abort();
    const controller = new AbortController(); writeAbort.current = controller;
    setPending(enabled); setError(''); setNotice('');
    try {
      const result = await jevLiveTApi.setControl(enabled, controller.signal);
      if (!mounted.current || controller.signal.aborted) return;
      setState(result); setUncertain(false);
      setNotice(enabled ? '开启请求已确认，状态以后台返回为准。' : '关闭请求已确认；账户和历史记录保留。');
      callbacks.current.onChanged(result);
    } catch (failure) {
      if (!mounted.current || controller.signal.aborted) return;
      if (denied(failure)) { setState(null); callbacks.current.onAccessLost(); return; }
      setError(`操作结果尚未确认，请核对最新状态：${toApiErrorMessage(failure)}`); setUncertain(true);
    } finally {
      busy.current = false;
      if (mounted.current && !controller.signal.aborted) { setPending(null); setRetry(value => value + 1); }
    }
  };
  const exhausted = state?.phase === 'budget_exhausted' || (state?.usage?.costUsd != null && state.config?.maxCostUsd != null && state.usage.costUsd >= state.config.maxCostUsd);
  const changing = pending !== null || state?.phase === 'stopping';
  const available = state?.canControl && Object.hasOwn(phases, state.phase);
  const otherRun = !!state?.runId && selectedRunId !== state.runId;
  return <section className="jev-panel live-t-control" aria-labelledby="live-control-heading" aria-busy={loading || pending !== null}>
    <div className="jev-section-heading"><div><h2 id="live-control-heading">持续纸上账户</h2><p>手动开启或关闭；页面不会自动开启。停止后保留账户，下次开启接续运行。</p></div><span className="jev-badge" role="status">{loading ? '正在读取控制状态' : state ? phases[state.phase] || '状态未知' : '控制状态未取得'}</span></div>
    {state?.message ? <p className="live-t-caption">{state.message}</p> : null}
    {state?.canControl ? <>
      <div className="live-t-control-facts"><div><span>{state.usage?.costIsEstimate ? '累计未核验预算估值' : '累计模型费用'} / 总预算（美元）</span><strong>{money(state.usage?.costUsd)} / {money(state.config?.maxCostUsd)}</strong></div><div><span>运行方式</span><strong>持续运行 · 不限轮数</strong></div><div><span>状态更新时间</span><strong>{formatTime(state.updatedAt)}</strong></div></div>
      <p className="live-t-caption">休市自动暂停，开市后继续；关闭不会清空持仓、账本或累计{state.usage?.costIsEstimate ? '预算估值' : '费用'}。达到总预算后停止模型调用，重新开启不会重置额度。不连接券商，不发送真实委托。</p>
      {state.config?.confidenceSizing ? <p className="live-t-caption">模型决定买卖方向，置信度用于确定数量；实际成交仍受 T+1 和流动性约束。</p> : null}
      <div className="live-t-control-actions">
        <button className={state.enabled ? 'jev-secondary' : 'jev-primary'} disabled={!available || changing || loading || uncertain || (!state.enabled && !!exhausted)} onClick={() => void change(!state.enabled)}>{pending === true ? '正在开启…' : pending === false || state.phase === 'stopping' ? '正在关闭…' : state.enabled ? '关闭持续纸上运行' : '开启持续纸上运行'}</button>
        {otherRun && state.runId ? <button className="jev-text-button" onClick={() => onOpenRun(state.runId!)}>查看当前持续账户</button> : null}
      </div>
      {selectedRunId && otherRun ? <p className="live-t-caption">开关只控制当前持续账户，下方所选历史记录不会被改写。</p> : null}
    </> : !loading && state ? <p className="live-t-caption">当前账号无此纸上账户控制权限。</p> : null}
    {notice ? <p className="live-t-caption" role="status">{notice}</p> : null}
    {error ? <div className="jev-alert" role="alert">{error}<button className="jev-text-button" disabled={pending !== null} onClick={() => { setLoading(true); setRetry(value => value + 1); }}>重新读取控制状态</button></div> : null}
  </section>;
}
