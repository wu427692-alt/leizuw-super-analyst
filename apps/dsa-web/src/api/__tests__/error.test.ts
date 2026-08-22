import { describe, expect, it } from 'vitest';
import { parseApiError } from '../error';

describe('parseApiError timeout classification', () => {
  it('does not claim a browser request timeout came from an upstream dependency', () => {
    const parsed = parseApiError(Object.assign(new Error('timeout of 30000ms exceeded'), {
      code: 'ECONNABORTED',
    }));

    expect(parsed.category).toBe('request_timeout');
    expect(parsed.title).toBe('请求响应超时');
    expect(parsed.message).not.toContain('外部依赖');
  });

  it('keeps explicit backend dependency timeouts classified as upstream failures', () => {
    const parsed = parseApiError({
      response: { status: 504, data: { detail: { message: 'upstream read timeout' } } },
    });

    expect(parsed.category).toBe('upstream_timeout');
    expect(parsed.title).toBe('连接上游服务超时');
  });
});
