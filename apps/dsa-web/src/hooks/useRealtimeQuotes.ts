import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getRealtimeQuotes, type RealtimeQuote } from '../api/realtimeQuotes';
import { getRealtimeIndices, type RealtimeIndexQuote } from '../api/realtimeQuotes';
import { usePageActivationRefresh } from './usePageActivationRefresh';

const keyFor = (value: string) => value.trim().toUpperCase().split('.')[0].replace(/^(SH|SZ|BJ)/, '');

export function useRealtimeQuotes(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(keyFor).filter(Boolean))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeQuote>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { if (!signature) setQuotes(new Map()); }, [signature]);
  const poll = useCallback(async () => {
    const codes = signature ? signature.split(',') : [];
    if (!codes.length || inFlight.current) return;
    inFlight.current = true;
    try {
      const rows = await getRealtimeQuotes(codes);
      if (mounted.current) { setQuotes(new Map(rows.map(row => [keyFor(row.stockCode), row]))); setError(null); }
    } catch (caught) {
      if (mounted.current) setError(caught instanceof Error ? caught.message : '秒级行情读取失败');
    } finally { inFlight.current = false; }
  }, [signature]);
  usePageActivationRefresh(poll, {
    enabled: Boolean(signature), intervalMs: Math.max(5_000, pollMs), minIntervalMs: 1_500,
  });
  return { quotes, error, keyFor };
}

export function useRealtimeIndices(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(value => value.toUpperCase()))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeIndexQuote>>(new Map());
  const running = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const poll = useCallback(async () => {
    const codes = signature ? signature.split(',') : [];
    if (!codes.length || running.current) return;
    running.current = true;
    try {
      const rows = await getRealtimeIndices(codes);
      if (mounted.current) setQuotes(new Map(rows.map(row => [row.code, row])));
    } finally { running.current = false; }
  }, [signature]);
  usePageActivationRefresh(poll, {
    enabled: Boolean(signature), intervalMs: Math.max(5_000, pollMs), minIntervalMs: 1_500,
  });
  return quotes;
}
