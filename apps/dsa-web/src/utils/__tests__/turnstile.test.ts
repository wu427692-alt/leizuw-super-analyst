import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TurnstileApi } from '../turnstile';

let loadTurnstile: typeof import('../turnstile').loadTurnstile;
const api = (): TurnstileApi => ({ render: vi.fn(), remove: vi.fn() });
beforeEach(async () => {
  vi.resetModules();
  delete window.turnstile;
  document.querySelectorAll('#leziwu-turnstile-script').forEach(script => script.remove());
  ({ loadTurnstile } = await import('../turnstile'));
});
afterEach(() => { vi.useRealTimers(); delete window.turnstile; });

describe('Turnstile script lifecycle', () => {
  it('loads the official script once for concurrent widgets and resolves after the API is available', async () => {
    const first = loadTurnstile(); const second = loadTurnstile();
    expect(first).toBe(second);
    const scripts = document.querySelectorAll<HTMLScriptElement>('#leziwu-turnstile-script');
    expect(scripts.length).toBe(1);
    expect(scripts[0].src).toBe('https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit');
    expect(scripts[0].async).toBe(true); expect(scripts[0].defer).toBe(true);
    const loaded = api(); window.turnstile = loaded;
    scripts[0].dispatchEvent(new Event('load'));
    await expect(first).resolves.toBe(loaded); await expect(second).resolves.toBe(loaded);
    await expect(loadTurnstile()).resolves.toBe(loaded);
  });

  it('removes a failed script so a user-initiated retry can actually load again', async () => {
    const failed = loadTurnstile();
    const rejection = expect(failed).rejects.toThrow('script load failed');
    const original = document.querySelector('#leziwu-turnstile-script')!;
    original.dispatchEvent(new Event('error')); await rejection;
    expect(document.querySelector('#leziwu-turnstile-script')).toBeNull();
    const retried = loadTurnstile();
    const replacement = document.querySelector('#leziwu-turnstile-script')!;
    expect(replacement).not.toBe(original);
    const loaded = api(); window.turnstile = loaded;
    replacement.dispatchEvent(new Event('load'));
    await expect(retried).resolves.toBe(loaded);
  });

  it('stops showing an infinite loading state when the script is silently blocked', async () => {
    vi.useFakeTimers();
    const failed = loadTurnstile();
    const rejection = expect(failed).rejects.toThrow('script load timeout');
    await vi.advanceTimersByTimeAsync(15000); await rejection;
    expect(document.querySelector('#leziwu-turnstile-script')).toBeNull();
  });

  it('does not treat a script load without the expected SDK as successful verification', async () => {
    const failed = loadTurnstile();
    const rejection = expect(failed).rejects.toThrow('API unavailable');
    document.querySelector('#leziwu-turnstile-script')!.dispatchEvent(new Event('load'));
    await rejection;
    expect(document.querySelector('#leziwu-turnstile-script')).toBeNull();
  });
});
