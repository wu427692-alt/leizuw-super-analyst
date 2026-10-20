import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getRealtimeQuotes, type RealtimeQuote } from '../api/realtimeQuotes';
import { getRealtimeIndices, type RealtimeIndexQuote } from '../api/realtimeQuotes';
import { usePageActivationRefresh } from './usePageActivationRefresh';
import { isAshareLiveWindow } from '../utils/marketSession';

const keyFor = (value: string) => value.trim().toUpperCase().split('.')[0].replace(/^(SH|SZ|BJ)/, '');

export function useRealtimeQuotes(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(keyFor).filter(Boolean))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeQuote>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(new Set<string>());
  const activeSignature = useRef(signature);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const poll = useCallback(async () => {
    const codes = signature ? signature.split(',') : [];
    if (!codes.length || inFlight.current.has(signature) || document.visibilityState === 'hidden') return;
    inFlight.current.add(signature);
    try {
      const rows = await getRealtimeQuotes(codes);
      if (mounted.current && activeSignature.current === signature) { setQuotes(new Map(rows.map(row => [keyFor(row.stockCode), row]))); setError(null); }
    } catch (caught) {
      if (mounted.current && activeSignature.current === signature) setError(caught instanceof Error ? caught.message : '行情读取失败');
    } finally { inFlight.current.delete(signature); }
  }, [signature]);
  useEffect(() => {
    activeSignature.current = signature;
    // A new watchlist is a user activation even outside the trading window.
    // Different batches may overlap; a superseded batch cannot overwrite it.
    if (signature) void poll();
  }, [signature, poll]);
  usePageActivationRefresh(poll, {
    enabled: Boolean(signature), intervalMs: Math.max(5_000, pollMs),
    intervalGuard: isAshareLiveWindow, minIntervalMs: 1_500, runOnMount: false,
  });
  const visibleQuotes = useMemo(() => {
    const codes = new Set(signature.split(',').filter(Boolean));
    return new Map([...quotes].filter(([code]) => codes.has(code)));
  }, [quotes, signature]);
  return { quotes: visibleQuotes, error: signature ? error : null, keyFor };
}

export function useRealtimeIndices(symbols: string[], pollMs = 15_000) {
  const signature = useMemo(() => Array.from(new Set(symbols.map(value => value.toUpperCase()))).sort().join(','), [symbols]);
  const [quotes, setQuotes] = useState<Map<string, RealtimeIndexQuote>>(new Map());
  const running = useRef(new Set<string>());
  const activeSignature = useRef(signature);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const poll = useCallback(async () => {
    const codes = signature ? signature.split(',') : [];
    if (!codes.length || running.current.has(signature) || document.visibilityState === 'hidden') return;
    running.current.add(signature);
    try {
      const rows = await getRealtimeIndices(codes);
      if (mounted.current && activeSignature.current === signature) setQuotes(new Map(rows.map(row => [row.code, row])));
    } finally { running.current.delete(signature); }
  }, [signature]);
  useEffect(() => {
    activeSignature.current = signature;
    if (signature) void poll().catch(() => undefined);
  }, [signature, poll]);
  usePageActivationRefresh(poll, {
    enabled: Boolean(signature), intervalMs: Math.max(5_000, pollMs), minIntervalMs: 1_500, runOnMount: false,
  });
  return useMemo(() => {
    const codes = new Set(signature.split(',').filter(Boolean));
    return new Map([...quotes].filter(([code]) => codes.has(code)));
  }, [quotes, signature]);
}
