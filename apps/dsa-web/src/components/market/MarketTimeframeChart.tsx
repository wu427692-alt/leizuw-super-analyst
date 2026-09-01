import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Area, Bar, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { RefreshCw } from 'lucide-react';
import { getMarketSeries } from '../../api/marketSeries';
import type { MarketBar, MarketPeriod, MarketRange, MarketSeries } from '../../types/marketSeries';
import { adaptivePercentDomain, resolveIntradayBasePrice, tooltipChangePercent } from './marketChartDomain';
import { usePageActivationRefresh } from '../../hooks/usePageActivationRefresh';
import { isAshareLiveWindow } from '../../utils/marketSession';

const PERIODS: Array<{ value: MarketPeriod; label: string }> = [
  { value: 'intraday', label: '分时' }, { value: 'daily', label: '日K' },
  { value: 'weekly', label: '周K' }, { value: 'monthly', label: '月K' },
  { value: 'yearly', label: '年K' },
];
const RANGES: Record<MarketPeriod, Array<{ value: MarketRange; label: string }>> = {
  intraday: [{ value: '1d', label: '当日' }, { value: '5d', label: '5日' }],
  daily: [{ value: '1m', label: '1月' }, { value: '3m', label: '3月' }, { value: '6m', label: '6月' }, { value: '1y', label: '1年' }, { value: '2y', label: '2年' }, { value: '5y', label: '5年' }],
  weekly: [{ value: '6m', label: '6月' }, { value: '1y', label: '1年' }, { value: '2y', label: '2年' }, { value: '5y', label: '5年' }, { value: '10y', label: '10年' }],
  monthly: [{ value: '1y', label: '1年' }, { value: '3y', label: '3年' }, { value: '5y', label: '5年' }, { value: '10y', label: '10年' }, { value: 'max', label: '全部' }],
  yearly: [{ value: '5y', label: '5年' }, { value: '10y', label: '10年' }, { value: 'max', label: '全部' }],
};
const DEFAULT_RANGE: Record<MarketPeriod, MarketRange> = { intraday: '1d', daily: '6m', weekly: '2y', monthly: '5y', yearly: 'max' };
const UP = '#EF4444';
const DOWN = '#16A06A';
const FLAT = '#7B8494';

type ChartRow = MarketBar & {
  label: string;
  priceRange: [number, number];
  stageChangePercent?: number;
  stageHighPercent?: number;
  stageLowPercent?: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
};

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function compactNumber(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function axisLabel(value: string, period: MarketPeriod, range: MarketRange) {
  if (period === 'intraday') return range === '5d' ? `${value.slice(5, 10)} ${value.slice(11, 16)}` : value.slice(11, 16);
  if (period === 'yearly') return value.slice(0, 4);
  return value.slice(5, 10);
}

function addMovingAverages(rows: ChartRow[]) {
  const windows = [5, 10, 20] as const;
  const sums = new Map<number, number>();
  return rows.map((row, index) => {
    const next = { ...row };
    for (const size of windows) {
      const close = Number(row.close ?? 0);
      const sum = (sums.get(size) ?? 0) + close - (index >= size ? Number(rows[index - size].close ?? 0) : 0);
      sums.set(size, sum);
      if (index >= size - 1) next[`ma${size}`] = sum / size;
    }
    return next;
  });
}

type CandleProps = { x?: number; y?: number; width?: number; height?: number; payload?: ChartRow };

function Candle({ x = 0, y = 0, width = 0, height = 0, payload }: CandleProps) {
  if (!payload || payload.open == null || payload.close == null || payload.high == null || payload.low == null) return null;
  const span = payload.high - payload.low;
  const toY = (value: number) => span === 0 ? y + height / 2 : y + ((payload.high! - value) / span) * height;
  const openY = toY(payload.open);
  const closeY = toY(payload.close);
  const color = payload.close >= payload.open ? UP : DOWN;
  const bodyWidth = Math.max(2, width * .62);
  const bodyY = Math.min(openY, closeY);
  const bodyHeight = Math.max(1.2, Math.abs(openY - closeY));
  const center = x + width / 2;
  return <g>
    <line x1={center} x2={center} y1={y} y2={y + Math.max(height, 1)} stroke={color} strokeWidth={1} />
    <rect x={center - bodyWidth / 2} y={bodyY} width={bodyWidth} height={bodyHeight} fill={payload.close >= payload.open ? color : 'hsl(var(--card))'} stroke={color} strokeWidth={1} />
  </g>;
}

function PriceTooltip({ active, payload, basePrice, intraday, intradayRange }: { active?: boolean; payload?: Array<{ payload?: ChartRow }>; basePrice?: number | null; intraday?: boolean; intradayRange?: MarketRange }) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const pct = tooltipChangePercent(row, basePrice, Boolean(intraday));
  const tone = (pct ?? 0) > 0 ? UP : (pct ?? 0) < 0 ? DOWN : FLAT;
  return <div className="border border-border bg-card/95 px-2.5 py-2 text-[10px] shadow-lg backdrop-blur">
    <p className="mb-1 font-mono text-secondary-text">{row.date.replace('T', ' ')}</p>
    {intraday ? <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 tabular-nums">
      <span style={{ color: tone }}>时点价格 {number(row.close)}</span>
      <span style={{ color: tone }}>{intradayRange === '5d' ? '较区间首点' : '较昨收'} {pct == null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`}</span>
      <span>本分钟量 {compactNumber(row.volume)}</span><span>本分钟额 {compactNumber(row.amount)}</span>
      {row.cumulativeVolume != null ? <span>累计量 {compactNumber(row.cumulativeVolume)}</span> : null}
      {row.cumulativeAmount != null ? <span>累计额 {compactNumber(row.cumulativeAmount)}</span> : null}
    </div> : <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 tabular-nums">
      <span>开 {number(row.open)}</span><span>高 {number(row.high)}</span>
      <span>低 {number(row.low)}</span><span style={{ color: tone }}>收 {number(row.close)}</span>
      <span>成交量 {compactNumber(row.volume)}</span><span>成交额 {compactNumber(row.amount)}</span>
      <span style={{ color: tone }}>涨跌幅 {pct == null ? '—' : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`}</span>
    </div>}
  </div>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className="min-w-0 border-r border-border/55 px-2 last:border-r-0">
    <span className="text-[9px] text-secondary-text">{label}</span>
    <p className="truncate font-mono text-[11px] font-semibold tabular-nums" style={tone ? { color: tone } : undefined}>{value}</p>
  </div>;
}

export function MarketTimeframeChart({
  symbol, compact = false, className = '', assetType = 'stock', initialPeriod = 'daily', initialRange,
}: { symbol: string; compact?: boolean; className?: string; assetType?: 'stock' | 'index'; initialPeriod?: MarketPeriod; initialRange?: MarketRange }) {
  const [period, setPeriod] = useState<MarketPeriod>(initialPeriod);
  const [range, setRange] = useState<MarketRange>(initialRange ?? DEFAULT_RANGE[initialPeriod]);
  const [series, setSeries] = useState<MarketSeries | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [recoveryAttempt, setRecoveryAttempt] = useState(0);
  const requestVersion = useRef(0);
  const requestInFlight = useRef(false);
  const mounted = useRef(true);

  const load = useCallback(async (force = false, silent = false) => {
    if (silent && requestInFlight.current) return;
    const version = ++requestVersion.current;
    requestInFlight.current = true;
    if (!silent) { setLoading(true); setError(''); }
    try {
      const next = await getMarketSeries(symbol, period, range, force, assetType);
      if (mounted.current && version === requestVersion.current) {
        setSeries(next);
        setError('');
        setRecoveryAttempt(0);
      }
    } catch (caught) {
      if (mounted.current && version === requestVersion.current && !silent) {
        setError(caught instanceof Error ? caught.message : '行情加载失败');
      }
    } finally {
      if (version === requestVersion.current) {
        requestInFlight.current = false;
        if (mounted.current && !silent) setLoading(false);
      }
    }
  }, [assetType, period, range, symbol]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestVersion.current += 1;
      requestInFlight.current = false;
    };
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!error) return undefined;
    const delay = Math.min(10_000, 1_200 * (2 ** Math.min(recoveryAttempt, 3)));
    const timer = window.setTimeout(() => {
      void load(false, true).finally(() => {
        if (mounted.current) setRecoveryAttempt(attempt => attempt + 1);
      });
    }, delay);
    return () => window.clearTimeout(timer);
  }, [error, load, recoveryAttempt]);
  const refreshVisibleChart = useCallback(() => load(false, true), [load]);
  // The chart stores minute bars. Refresh on activation and every 15 seconds
  // while visible; focus/visibility events are coalesced by the shared hook.
  usePageActivationRefresh(refreshVisibleChart, {
    enabled: period === 'intraday', intervalMs: 15_000,
    intervalGuard: isAshareLiveWindow, minIntervalMs: 2_000, runOnMount: false,
  });

  const stageBase = useMemo(() => {
    if (period !== 'intraday') return null;
    return resolveIntradayBasePrice(series?.preClose, series?.data ?? [], range === '5d' ? '5d' : '1d');
  }, [period, range, series]);
  const chart = useMemo(() => addMovingAverages((series?.data ?? []).map((row) => {
    const percent = (value: number | null | undefined) => value != null && stageBase
      ? ((value - stageBase) / stageBase) * 100
      : undefined;
    return {
      ...row,
      label: axisLabel(row.date, period, range),
      priceRange: [Number(row.low ?? row.close ?? 0), Number(row.high ?? row.close ?? 0)] as [number, number],
      stageChangePercent: percent(row.close),
      stageHighPercent: percent(row.high ?? row.close),
      stageLowPercent: percent(row.low ?? row.close),
    };
  })), [period, range, series, stageBase]);
  const latest = chart.at(-1);
  const comparison = period === 'intraday' ? stageBase : chart.at(-2)?.close;
  const change = latest?.close != null && comparison != null ? latest.close - comparison : null;
  const changePct = change != null && comparison ? change / comparison * 100 : latest?.changePercent;
  const changeTone = (changePct ?? 0) > 0 ? UP : (changePct ?? 0) < 0 ? DOWN : FLAT;
  const sessionOpen = period === 'intraday' ? chart.find((row) => row.open != null)?.open : latest?.open;
  const sessionHigh = period === 'intraday' && chart.length ? Math.max(...chart.map((row) => Number(row.high ?? row.close ?? -Infinity))) : latest?.high;
  const sessionLow = period === 'intraday' && chart.length ? Math.min(...chart.map((row) => Number(row.low ?? row.close ?? Infinity))) : latest?.low;
  const latestIntervalVolume = latest?.volume;
  const sessionAmount = period === 'intraday' && chart.length
    ? Math.max(...chart.map((row) => Number(row.cumulativeAmount ?? row.amount ?? 0)))
    : (latest?.amount ?? latest?.volume);
  const priceDomain = useMemo<[number, number]>(() => {
    if (!chart.length) return [0, 1];
    const low = Math.min(...chart.map((row) => Number(row.low ?? row.close ?? Infinity)));
    const high = Math.max(...chart.map((row) => Number(row.high ?? row.close ?? -Infinity)));
    const padding = Math.max((high - low) * .06, high * .001);
    return [low - padding, high + padding];
  }, [chart]);
  const intradayPercentDomain = useMemo(() => adaptivePercentDomain(chart), [chart]);
  const zeroInIntradayDomain = intradayPercentDomain[0] <= 0 && intradayPercentDomain[1] >= 0;
  const latestIsRealtime = period === 'intraday' && /T\d{2}:\d{2}:\d{2}$/.test(latest?.date ?? '');

  const selectPeriod = (next: MarketPeriod) => startTransition(() => { setPeriod(next); setRange(DEFAULT_RANGE[next]); });
  const priceHeight = compact ? 205 : 320;
  const volumeHeight = compact ? 72 : 100;
  const chartMargin = { top: 8, right: compact ? 8 : 16, bottom: 0, left: 0 };

  return <section className={`market-timeframe-chart overflow-hidden border border-border/70 bg-background/25 ${className}`}>
    <div className="market-chart-toolbar flex flex-wrap items-center justify-between gap-2 border-b border-border/70 px-2 py-1.5">
      <div className="market-chart-periods flex shrink-0 items-center">
        {PERIODS.map((item) => <button key={item.value} onClick={() => selectPeriod(item.value)} className={`h-7 whitespace-nowrap border px-3 text-[10px] font-semibold ${period === item.value ? 'border-cyan/70 bg-cyan/15 text-cyan' : 'border-transparent text-secondary-text hover:border-border'}`}>{item.label}</button>)}
      </div>
      <div className="market-chart-ranges flex shrink-0 items-center gap-1">
        {RANGES[period].map((item) => <button key={item.value} onClick={() => setRange(item.value)} className={`h-6 whitespace-nowrap px-2 text-[9px] ${range === item.value ? 'border-b-2 border-cyan font-bold text-cyan' : 'text-secondary-text'}`}>{item.label}</button>)}
        <button aria-label="刷新行情" onClick={() => void load(true)} className="ml-1 p-1 text-secondary-text hover:text-cyan"><RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} /></button>
      </div>
    </div>

    <div className="grid grid-cols-4 border-b border-border/55 sm:grid-cols-8">
      <Metric label="最新" value={number(latest?.close)} tone={changeTone} />
      <Metric label="涨跌" value={change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(2)}`} tone={changeTone} />
      <Metric label="涨幅" value={changePct == null ? '—' : `${changePct > 0 ? '+' : ''}${changePct.toFixed(2)}%`} tone={changeTone} />
      <Metric label={period === 'intraday' && range === '5d' ? '区间基准' : '昨收'} value={number(period === 'intraday' ? stageBase : comparison)} />
      <Metric label="开盘" value={number(sessionOpen)} />
      <Metric label="最高" value={number(sessionHigh)} tone={UP} />
      <Metric label="最低" value={number(sessionLow)} tone={DOWN} />
      <Metric label={period === 'intraday' ? '本分钟量' : '成交'} value={compactNumber(period === 'intraday' ? latestIntervalVolume : sessionAmount)} />
    </div>

    <div className="flex flex-wrap items-center justify-between gap-1 border-b border-border/45 px-3 py-1 text-[9px] text-secondary-text">
      <span>{series?.storedCount ?? 0} 个{period === 'intraday' ? '分钟点' : '数据点'}{latestIsRealtime ? ' · 当前分钟实时' : ''} · SQLite · {series?.source ?? '本地缓存'}</span>
      <div className="flex items-center gap-3 font-mono"><span className="text-[#E4B64A]">MA5</span><span className="text-[#A987FF]">MA10</span><span className="text-cyan">MA20</span><span>{series?.latestAt?.replace('T', ' ') ?? '暂无时间'}</span></div>
    </div>

    {error && chart.length ? <div role="status" className="border-b border-warning/20 bg-warning/5 px-3 py-1.5 text-[9px] text-secondary-text">行情更新暂时延迟，当前仍展示最近一次成功数据，系统正在自动重试。</div> : null}
    {chart.length ? <>
      <div style={{ height: priceHeight }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: compact ? 520 : 900, height: priceHeight }}>
          <ComposedChart data={chart} margin={chartMargin} syncId={`market-${symbol}-${period}`}>
            <CartesianGrid stroke="hsl(var(--border))" strokeOpacity={0.55} vertical={false} />
            <XAxis dataKey="label" hide />
            <YAxis
              tick={{ fontSize: 9, fill: 'hsl(var(--secondary-text))' }}
              width={period === 'intraday' ? 58 : 52}
              domain={period === 'intraday' ? intradayPercentDomain : priceDomain}
              tickFormatter={(value) => period === 'intraday' ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%` : Number(value).toFixed(2)}
            />
            <Tooltip content={<PriceTooltip basePrice={period === 'intraday' ? stageBase : null} intraday={period === 'intraday'} intradayRange={range} />} cursor={{ stroke: 'hsl(var(--secondary-text))', strokeDasharray: '3 3' }} />
            {period === 'intraday' ? <>
              {zeroInIntradayDomain ? <ReferenceLine y={0} ifOverflow="hidden" stroke="hsl(var(--secondary-text))" strokeDasharray="4 4" strokeOpacity={.75} /> : null}
              <Area type="monotone" dataKey="stageChangePercent" stroke="#2F80ED" fill="#2F80ED" fillOpacity={.10} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
            </> : <>
              <Bar dataKey="priceRange" shape={(props: CandleProps) => <Candle {...props} />} isAnimationActive={false} />
              <Line dataKey="ma5" stroke="#E4B64A" strokeWidth={1} dot={false} connectNulls isAnimationActive={false} />
              <Line dataKey="ma10" stroke="#A987FF" strokeWidth={1} dot={false} connectNulls isAnimationActive={false} />
              <Line dataKey="ma20" stroke="hsl(var(--primary))" strokeWidth={1} dot={false} connectNulls isAnimationActive={false} />
            </>}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="border-t border-border/45" style={{ height: volumeHeight }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: compact ? 520 : 900, height: volumeHeight }}>
          <ComposedChart data={chart} margin={{ ...chartMargin, top: 4 }} syncId={`market-${symbol}-${period}`}>
            <CartesianGrid stroke="hsl(var(--border))" strokeOpacity={0.35} vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 9, fill: 'hsl(var(--secondary-text))' }}
              interval="preserveStartEnd"
              minTickGap={range === '5d' ? 70 : compact ? 48 : 36}
              tickMargin={7}
              height={range === '5d' ? 32 : 24}
            />
            <YAxis hide domain={[0, 'auto']} />
            <Bar dataKey="volume" isAnimationActive={false}>
              {chart.map((row) => <Cell key={row.date} fill={(row.close ?? 0) >= (row.open ?? row.close ?? 0) ? UP : DOWN} fillOpacity={.65} />)}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </> : <div role="status" className={`flex items-center justify-center text-secondary-text ${compact ? 'h-[277px] text-[10px]' : 'h-[420px] text-[11px]'}`}>{loading ? '正在读取本地行情数据库…' : error ? '行情连接正在恢复，页面会自动重试…' : '所选周期暂无行情'}</div>}
  </section>;
}
