import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, ArrowRight, Database, RefreshCw, ShieldCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { investmentMonitorApi } from '../../api/investmentMonitor';
import type { StockWorkspace } from '../../types/investmentMonitor';
import { cn } from '../../utils/cn';

const requestCache = new Map<string, { at: number; value: StockWorkspace }>();
const CACHE_MS = 20_000;

function number(value: number | null | undefined, digits = 2): string {
  return value == null || Number.isNaN(value) ? '—' : value.toFixed(digits);
}

function percent(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

type Props = {
  symbol?: string;
  title?: string;
  allowInput?: boolean;
  className?: string;
};

export function UnifiedStockContextPanel({
  symbol = '', title = '全渠道决策底稿', allowInput = false, className,
}: Props) {
  const [draft, setDraft] = useState(symbol);
  const [activeSymbol, setActiveSymbol] = useState(normalizeSymbol(symbol));
  const [data, setData] = useState<StockWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [quietError, setQuietError] = useState('');
  const requestId = useRef(0);

  useEffect(() => {
    const next = normalizeSymbol(symbol);
    setDraft(next);
    setActiveSymbol(next);
  }, [symbol]);

  useEffect(() => {
    if (!activeSymbol) {
      setData(null);
      return;
    }
    const id = requestId.current + 1;
    requestId.current = id;
    const cached = requestCache.get(activeSymbol);
    if (cached && Date.now() - cached.at < CACHE_MS) {
      setData(cached.value);
      setQuietError('');
      setLoading(false);
      return;
    }
    setLoading(true);
    setQuietError('');
    investmentMonitorApi.stockWorkspace(activeSymbol)
      .then((result) => {
        if (requestId.current !== id) return;
        requestCache.set(activeSymbol, { at: Date.now(), value: result });
        setData(result);
      })
      .catch(() => {
        if (requestId.current !== id) return;
        setQuietError('共享底稿暂未就绪，当前功能仍可继续使用。');
      })
      .finally(() => {
        if (requestId.current === id) setLoading(false);
      });
  }, [activeSymbol]);

  const stock = data?.stock;
  const available = useMemo(() => stock?.coverage.filter((item) => item.available) ?? [], [stock]);
  const fresh = available.filter((item) => item.freshnessStatus === 'fresh').length;
  const change = stock?.market.changePct;

  const refresh = async () => {
    if (!activeSymbol || loading) return;
    setLoading(true);
    setQuietError('');
    try {
      const result = await investmentMonitorApi.stockWorkspace(activeSymbol, 365, true);
      requestCache.set(activeSymbol, { at: Date.now(), value: result });
      setData(result);
    } catch {
      setQuietError('刷新未完成，继续展示最近一次可用底稿。');
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className={cn('overflow-hidden rounded-2xl border border-cyan-400/15 bg-[linear-gradient(120deg,rgba(8,21,36,.94),rgba(14,17,31,.96))] shadow-[0_18px_60px_rgba(0,0,0,.18)]', className)}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/8 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-xl border border-cyan-300/20 bg-cyan-400/10 text-cyan-300"><Database className="h-4 w-4" /></span>
          <div>
            <div className="flex items-center gap-2"><h2 className="text-sm font-semibold text-foreground">{title}</h2><span className="rounded-full bg-emerald-400/10 px-2 py-0.5 text-[10px] text-emerald-300">本地共享 · 只读事实</span></div>
            <p className="mt-0.5 text-[11px] text-muted-text">行情、Tushare、公告、研报、小作文、天眼查与股评统一口径</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {allowInput ? (
            <form className="flex items-center gap-2" onSubmit={(event) => { event.preventDefault(); setActiveSymbol(normalizeSymbol(draft)); }}>
              <input value={draft} onChange={(event) => setDraft(event.target.value)} className="input-surface h-9 w-36 rounded-lg border bg-transparent px-3 text-xs" placeholder="股票代码" />
              <button className="btn-secondary !h-9 !px-3 !text-xs" type="submit">读取</button>
            </form>
          ) : null}
          {activeSymbol ? <button type="button" className="btn-secondary !h-9 !px-3" onClick={() => void refresh()} disabled={loading} aria-label="刷新共享底稿"><RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} /></button> : null}
        </div>
      </div>

      {!activeSymbol ? <div className="px-4 py-5 text-sm text-secondary-text">输入个股代码后，加载该股的全渠道事实底稿；不会触发外部下载。</div> : null}
      {activeSymbol && loading && !data ? <div className="h-28 animate-pulse bg-[linear-gradient(90deg,transparent,rgba(255,255,255,.035),transparent)]" aria-label="正在准备全渠道底稿" /> : null}
      {quietError && !data ? <div className="px-4 py-5 text-xs text-amber-200/80">{quietError}</div> : null}
      {stock ? (
        <div className="grid gap-0 xl:grid-cols-[1.1fr_1.35fr_.85fr]">
          <div className="border-b border-white/8 p-4 xl:border-b-0 xl:border-r">
            <div className="flex items-baseline gap-2"><strong className="text-lg text-foreground">{stock.name || stock.symbol}</strong><span className="text-xs text-muted-text">{stock.symbol}</span></div>
            <div className="mt-3 flex items-end gap-3"><span className="font-mono text-3xl font-semibold tracking-tight text-foreground">{number(stock.market.price)}</span><span className={cn('pb-1 text-sm font-semibold', (change ?? 0) >= 0 ? 'text-red-400' : 'text-emerald-400')}>{percent(change)}</span></div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]"><span className="text-muted-text">PE TTM<br/><b className="text-secondary-text">{number(stock.valuation.peTtm, 1)}</b></span><span className="text-muted-text">ROE<br/><b className="text-secondary-text">{percent(stock.fundamentals.roe)}</b></span><span className="text-muted-text">净利同比<br/><b className="text-secondary-text">{percent(stock.fundamentals.netProfitYoy)}</b></span></div>
          </div>

          <div className="border-b border-white/8 p-4 xl:border-b-0 xl:border-r">
            <div className="mb-3 flex items-center justify-between text-xs"><span className="font-medium text-secondary-text">数据覆盖</span><span className="text-muted-text">{available.length}/{stock.coverage.length} 类 · {fresh} 类新鲜</span></div>
            <div className="grid grid-cols-3 gap-2">
              {stock.coverage.map((item) => (
                <div key={item.name} className="rounded-lg border border-white/7 bg-white/[.025] px-2.5 py-2">
                  <div className="flex items-center justify-between gap-1"><span className="truncate text-[11px] text-secondary-text">{item.name}</span><i className={cn('h-1.5 w-1.5 rounded-full', item.freshnessStatus === 'fresh' ? 'bg-emerald-400' : item.available ? 'bg-amber-400' : 'bg-slate-600')} /></div>
                  <b className="mt-1 block font-mono text-sm text-foreground">{item.count}</b>
                </div>
              ))}
            </div>
          </div>

          <div className="p-4">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-xl bg-white/[.035] p-3"><Activity className="mb-2 h-4 w-4 text-cyan-300"/><b className="text-xl text-foreground">{stock.evidence.eventCount}</b><p className="text-[10px] text-muted-text">去重决策证据</p></div>
              <div className="rounded-xl bg-white/[.035] p-3"><ShieldCheck className="mb-2 h-4 w-4 text-emerald-300"/><b className="text-xl text-foreground">{stock.evidence.factualCount}</b><p className="text-[10px] text-muted-text">事实/已报告</p></div>
            </div>
            <div className="mt-3 grid gap-1.5 text-[11px] text-secondary-text"><span>机构 {stock.institution.researchCount} · 公告 {stock.company.announcementCount}</span><span>小作文 {stock.alternative.essayCount} · 股评 {stock.stockComments.count}</span></div>
            <Link to={`/super-watchlist?symbol=${encodeURIComponent(stock.symbol)}`} className="mt-3 inline-flex items-center gap-1 text-xs text-cyan-300 hover:text-cyan-200">打开完整证据工作台 <ArrowRight className="h-3 w-3" /></Link>
            {quietError ? <p className="mt-2 text-[10px] text-amber-200/70">{quietError}</p> : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}
