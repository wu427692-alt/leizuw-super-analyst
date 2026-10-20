import { useCallback, useEffect, useRef, useState } from 'react';

/** A concept panel owns one request identity: selection + window + filters. */
export function useConceptSnapshot<T>(key: string | null, fetchSnapshot: () => Promise<T>) {
  const [snapshot, setSnapshot] = useState<{ key: string; data: T | null; pending: boolean; error: string }>({
    key: '', data: null, pending: false, error: '',
  });
  const generation = useRef(0);
  const inflight = useRef<{ key: string; promise: Promise<void> } | null>(null);
  const reload = useCallback((): Promise<void> => {
    if (key === null) return Promise.resolve();
    if (inflight.current?.key === key) return inflight.current.promise;
    const ticket = ++generation.current;
    setSnapshot(previous => ({ key, data: previous.key === key ? previous.data : null, pending: true, error: '' }));
    const promise = Promise.resolve().then(fetchSnapshot).then(data => {
      if (generation.current === ticket) setSnapshot({ key, data, pending: false, error: '' });
    }).catch(() => {
      if (generation.current === ticket) setSnapshot(previous => ({
        key, data: previous.key === key ? previous.data : null, pending: false,
        error: '暂时未能读取数据，可以重试。',
      }));
    }).finally(() => {
      if (generation.current === ticket) inflight.current = null;
    });
    inflight.current = { key, promise };
    return promise;
  }, [key, fetchSnapshot]);

  useEffect(() => {
    let active = true;
    // Defer initialization, so StrictMode cleanup can retire an unused request.
    void Promise.resolve().then(() => { if (active) void reload(); });
    return () => { active = false; generation.current += 1; inflight.current = null; };
  }, [reload]);

  const matches = key !== null && snapshot.key === key;
  const data = matches ? snapshot.data : null;
  const pending = key !== null && (!matches || snapshot.pending);
  return { data, loading: pending && data === null, refreshing: pending, error: matches ? snapshot.error : '', reload };
}
