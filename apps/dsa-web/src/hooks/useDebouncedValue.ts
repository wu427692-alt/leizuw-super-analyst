import { useEffect, useState } from 'react';

/**
 * Delay expensive server-side filtering until the user pauses typing.
 * React's useDeferredValue changes render priority; it does not debounce I/O.
 */
export function useDebouncedValue<T>(value: T, delayMs = 320): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debouncedValue;
}
