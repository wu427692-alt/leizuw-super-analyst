import { useEffect, useMemo, useRef, useState } from 'react';
import { getRealtimeQuotes, type RealtimeQuote } from '../api/realtimeQuotes';
import { getRealtimeIndices, type RealtimeIndexQuote } from '../api/realtimeQuotes';

const keyFor = (value: string) => value.trim().toUpperCase().split('.')[0].replace(/^(SH|SZ|BJ)/, '');

export function useRealtimeQuotes(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(keyFor).filter(Boolean))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeQuote>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    let active = true;
    const codes = signature ? signature.split(',') : [];
    if (!codes.length) { setQuotes(new Map()); return; }
    const poll = async () => {
      if (document.visibilityState === 'hidden') return;
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const rows = await getRealtimeQuotes(codes);
        if (active) { setQuotes(new Map(rows.map(row => [keyFor(row.stockCode), row]))); setError(null); }
      } catch (caught) {
        if (active) setError(caught instanceof Error ? caught.message : '秒级行情读取失败');
      } finally { inFlight.current = false; }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), Math.max(5_000, pollMs));
    const resume = () => { if (document.visibilityState === 'visible') void poll(); };
    document.addEventListener('visibilitychange', resume);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [pollMs, signature]);
  return { quotes, error, keyFor };
}

export function useRealtimeIndices(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(value => value.toUpperCase()))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeIndexQuote>>(new Map());
  useEffect(() => {
    let active = true; let running = false;
    const codes = signature ? signature.split(',') : [];
    if (!codes.length) return;
    const poll = async () => {
      if (document.visibilityState === 'hidden') return;
      if (running) return; running = true;
      try { const rows = await getRealtimeIndices(codes); if (active) setQuotes(new Map(rows.map(row => [row.code, row]))); }
      finally { running = false; }
    };
    void poll(); const timer = window.setInterval(() => void poll(), Math.max(5_000, pollMs));
    const resume = () => { if (document.visibilityState === 'visible') void poll(); };
    document.addEventListener('visibilitychange', resume);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [pollMs, signature]);
  return quotes;
}
