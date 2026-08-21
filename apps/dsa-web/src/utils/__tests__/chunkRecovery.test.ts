import { describe, expect, it } from 'vitest';
import { cacheBustedPageUrl, chunkRetryKey, isChunkLoadError } from '../chunkRecovery';

describe('chunk recovery', () => {
  it('recognizes dynamic import failures but not component render errors', () => {
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(true);
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
  });

  it('uses a build-scoped retry marker', () => {
    expect(chunkRetryKey('essay-radar', 'build-20260820')).toBe(
      'dsa:chunk-retry:essay-radar:build-20260820',
    );
  });

  it('preserves route state while forcing a fresh index request', () => {
    expect(cacheBustedPageUrl('http://127.0.0.1:8000/essay-radar/system?tab=data#queue', 'build-new'))
      .toBe('/essay-radar/system?tab=data&__dsa_reload=build-new#queue');
  });
});
