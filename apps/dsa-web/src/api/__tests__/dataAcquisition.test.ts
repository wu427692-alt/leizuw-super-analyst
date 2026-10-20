import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn(), head: vi.fn() } }));

import apiClient from '../index';
import { dataAcquisitionApi } from '../dataAcquisition';
import type { AcquisitionDownloadProgress, AcquisitionPlan } from '../../types/dataAcquisition';
import { clearRequestCache } from '../requestCache';

const plan: AcquisitionPlan = {
  title: '测试取数', objective: '获取行情', model: 'test', generatedAt: '2026-08-24T00:00:00Z',
  outputFormats: ['json', 'zip'], caveats: [], includeFiles: false,
  scope: { symbols: ['603306.SH'], companyNames: ['华懋科技'], keywords: [], startDate: '', endDate: '', marketWide: false },
  tasks: [{ id: 'market', source: 'tushare', resource: 'daily', label: '日线', reason: '', params: {}, fields: [] }],
};

describe('dataAcquisitionApi', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  beforeEach(() => {
    vi.clearAllMocks(); clearRequestCache();
    // Existing legacy-response cases explicitly exercise HEAD-not-supported.
    vi.mocked(apiClient.head).mockRejectedValue({ response: { status: 405 } });
  });

  it('reads directory synchronization metadata without changing its timestamps or refreshing it', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: {
      sources: [], planner: { available: true, model: 'test', max_tasks: 12, max_rows_per_dataset: 100 },
      stock_directory: { source: 'Tushare stock_basic', last_success_at: null, refreshing: true, stale: true, entry_count: 5500 },
    } });
    const result = await dataAcquisitionApi.capabilities();
    expect(result.stockDirectory).toEqual({ source: 'Tushare stock_basic', lastSuccessAt: null, refreshing: true, stale: true, entryCount: 5500 });
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it('reads server identity checks while preserving upstream params and query keys', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      title: '目标校验', objective: '获取公告', model: 'test', generated_at: 'now', output_formats: ['zip'], caveats: [],
      scope: { symbols: ['688828.SH'], company_names: ['国仪量子'], entity_queries: ['company_alias'], entity_selections: { company_alias: '688828.SH' } },
      tasks: [{ ...plan.tasks[0], params: { ts_code: '688828.SH', keyword_mode: 'all', filters: { company_name: '国仪量子' } } }],
      preflight: { status: 'ready', can_execute: true, entities: [{ query: 'company_alias', status: 'resolved', name: '国仪量子', symbol: '688828.SH', source: 'stock_master', candidates: [] }],
        checks: [{ task_id: 'market', status: 'ready', message: '代码已核验' }], blockers: [] },
    } });

    const result = await dataAcquisitionApi.plan('获取国仪公告');

    expect(result.preflight?.canExecute).toBe(true);
    expect(result.preflight?.checks[0].taskId).toBe('market');
    expect(result.scope.entityQueries).toEqual(['company_alias']);
    expect(result.scope.entitySelections).toEqual({ company_alias: '688828.SH' });
    expect(result.tasks[0].params).toEqual({ ts_code: '688828.SH', keyword_mode: 'all', filters: { company_name: '国仪量子' } });
  });

  it('revalidates candidate selections without resubmitting AI or trusting client preflight', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      ...plan, output_formats: ['zip'], generated_at: 'now',
      scope: { symbols: ['603306.SH'], company_names: ['华懋科技'] },
      preflight: { status: 'ready', can_execute: true, entities: [], checks: [], blockers: [] },
    } });
    const selected: AcquisitionPlan = { ...plan,
      scope: { ...plan.scope, entityQueries: ['company_alias'], entitySelections: { company_alias: '603306.SH' } },
      preflight: { status: 'needs_confirmation', canExecute: false, entities: [], checks: [], blockers: ['等待确认'] },
      tasks: [{ ...plan.tasks[0], params: { ts_code: '603306.SH', nested: { start_date: '2026-01-01' } } }],
    };

    const result = await dataAcquisitionApi.validatePlan('获取华懋公告', selected);

    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/data-acquisition/validate-plan', expect.objectContaining({
      request: '获取华懋公告', plan: expect.objectContaining({
        scope: expect.objectContaining({ entity_queries: ['company_alias'], entity_selections: { company_alias: '603306.SH' } }),
        tasks: [expect.objectContaining({ params: { ts_code: '603306.SH', nested: { start_date: '2026-01-01' } } })],
      }),
    }));
    const sent = vi.mocked(apiClient.post).mock.calls[0][1] as { plan: Record<string, unknown> };
    expect(sent.plan).not.toHaveProperty('preflight');
    expect(result.preflight?.canExecute).toBe(true);
  });

  it('submits acquisition as a background task instead of a long-held request', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: {
      task_id: 'acq-one', status: 'queued', progress: 0, phase: 'queued', message: '已排队',
      completed_tasks: 0, total_tasks: 1, tasks: [], created_at: 'now', updated_at: 'now',
    } });

    const result = await dataAcquisitionApi.runAsync('获取行情', plan);

    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/data-acquisition/run-async', expect.objectContaining({ request: '获取行情' }));
    expect(result.taskId).toBe('acq-one');
    expect(result.totalTasks).toBe(1);
  });

  it('reports real bytes, requests a range, and only reaches 100 after the file is validated', async () => {
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      config?.onDownloadProgress?.({ loaded: 5, total: 7 } as never);
      return { data: new Blob(['package']), status: 200, headers: { 'content-length': '7' } };
    });
    const updates: Array<{ loaded: number; total?: number; percent?: number }> = [];

    await dataAcquisitionApi.download('job-one', (progress) => updates.push(progress));

    expect(updates[0]).toMatchObject({ loaded: 5, total: undefined, percent: undefined });
    expect(updates.at(-1)).toMatchObject({ loaded: 7, total: 7, percent: 100 });
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/data-acquisition/jobs/job-one/download', expect.objectContaining({ responseType: 'blob', __dsaRetryCount: 2, headers: expect.objectContaining({ Range: 'bytes=0-4194303', 'X-DSA-Route-Load': 'background' }) }));
  });

  it('resumes only the failed chunk, with at most three concurrent chunks and no duplicate completed bytes', async () => {
    const size = 4 * 1024 * 1024, total = size * 3 + 2;
    const counts = new Map<number, number>();
    let active = 0, maxActive = 0;
    const updates: Array<{ loaded: number; total?: number; percent?: number }> = [];
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const headers = config?.headers as Record<string, string>;
      const [start, requestedEnd] = /bytes=(\d+)-(\d+)/.exec(headers.Range)!.slice(1).map(Number);
      counts.set(start, (counts.get(start) ?? 0) + 1);
      if (start > 0) expect(headers['If-Range']).toBe('"fixed-zip"');
      active += 1; maxActive = Math.max(active, maxActive);
      await new Promise(resolve => setTimeout(resolve, 10));
      active -= 1;
      if (start === size && counts.get(start) === 1) throw { code: 'ERR_NETWORK' };
      const end = Math.min(requestedEnd, total - 1), length = end - start + 1;
      config?.onDownloadProgress?.({ loaded: length, total: length } as never);
      return { status: 206, data: new Blob([new Uint8Array(length).fill(Math.floor(start / size))]), headers: {
        'content-range': `bytes ${start}-${end}/${total}`, 'content-length': String(length), etag: '"fixed-zip"',
      } };
    });
    const result = await dataAcquisitionApi.download('job-resume', update => updates.push(update));
    expect(result.size).toBe(total);
    expect(counts).toEqual(new Map([[0, 1], [size, 2], [size * 2, 1], [size * 3, 1]]));
    expect(maxActive).toBe(3);
    expect(updates.filter(update => update.percent === 100)).toHaveLength(1);
    expect(updates.at(-1)).toMatchObject({ loaded: total, total, percent: 100 });
    expect(updates.every(update => update.loaded <= total)).toBe(true);
  });

  it('uses HEAD to start six chunks including chunk zero together, then assembles exact bytes in offset order', async () => {
    const size = 4 * 1024 * 1024, total = size * 6 + 3;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: {
      'content-length': String(total), 'accept-ranges': 'bytes', etag: '"head-fixed"', 'content-encoding': 'identity',
    } });
    const waiting = new Map<number, () => void>();
    let active = 0, maxActive = 0;
    const updates: Array<{ loaded: number; percent?: number }> = [];
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const headers = config?.headers as Record<string, string>;
      const [start, end] = /bytes=(\d+)-(\d+)/.exec(headers.Range)!.slice(1).map(Number);
      expect(headers['If-Range']).toBe('"head-fixed"');
      active += 1; maxActive = Math.max(active, maxActive);
      const length = end - start + 1;
      config?.onDownloadProgress?.({ loaded: length } as never);
      if (start < size * 6) await new Promise<void>(resolve => waiting.set(start, resolve));
      active -= 1;
      return { status: 206, data: new Blob([new Uint8Array(length).fill(Math.floor(start / size))]), headers: {
        'content-range': `bytes ${start}-${end}/${total}`, 'content-length': String(length), etag: '"head-fixed"',
      } };
    });
    const downloading = dataAcquisitionApi.download('job-parallel', update => updates.push(update));
    await vi.waitFor(() => expect(waiting.size).toBe(6));
    expect([...waiting.keys()]).toEqual([0, size, size * 2, size * 3, size * 4, size * 5]);
    expect(updates.every(update => update.percent !== 100)).toBe(true);
    // Finish out of order: the assembled archive must retain offset order.
    [...waiting.values()].reverse().forEach(resolve => resolve());
    const result = await downloading;
    const bytes = await new Promise<Uint8Array>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(result);
    });
    expect(bytes.length).toBe(total);
    expect(bytes.every((value, offset) => value === Math.floor(offset / size))).toBe(true);
    expect(maxActive).toBe(6);
    expect(apiClient.head).toHaveBeenCalledWith('/api/v1/data-acquisition/jobs/job-parallel/download', expect.objectContaining({ timeout: 12000, __dsaRetryCount: 2 }));
    expect(updates.filter(update => update.percent === 100)).toHaveLength(1);
    expect(updates.at(-1)).toMatchObject({ loaded: total, total, percent: 100 });
  });

  it('falls back to serial first-range negotiation when HEAD lacks a strong validator', async () => {
    const size = 4 * 1024 * 1024, total = size + 2;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes' } });
    let finishFirst: (() => void) | undefined;
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const headers = config?.headers as Record<string, string>;
      const [start, requestedEnd] = /bytes=(\d+)-(\d+)/.exec(headers.Range)!.slice(1).map(Number);
      if (start === 0) {
        expect(headers['If-Range']).toBeUndefined();
        await new Promise<void>(resolve => { finishFirst = resolve; });
      } else expect(headers['If-Range']).toBe('"legacy-fixed"');
      const end = Math.min(requestedEnd, total - 1), length = end - start + 1;
      return { status: 206, data: new Blob([new Uint8Array(length)]), headers: {
        'content-range': `bytes ${start}-${end}/${total}`, 'content-length': String(length), etag: '"legacy-fixed"',
      } };
    });
    const downloading = dataAcquisitionApi.download('job-legacy');
    await vi.waitFor(() => expect(finishFirst).toBeTypeOf('function'));
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    finishFirst!();
    expect((await downloading).size).toBe(total);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it.each([405, 501])('retains legacy full-file download when HEAD is unsupported (%s)', async status => {
    vi.mocked(apiClient.head).mockRejectedValue({ response: { status } });
    vi.mocked(apiClient.get).mockResolvedValue({ status: 200, data: new Blob(['package']), headers: { 'content-length': '7' } });
    expect((await dataAcquisitionApi.download('job-old-server')).size).toBe(7);
    expect(apiClient.head).toHaveBeenCalledTimes(1);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('bounds the optional HEAD timeout and safely negotiates the first range after a timeout', async () => {
    vi.mocked(apiClient.head).mockRejectedValue({ code: 'ECONNABORTED' });
    vi.mocked(apiClient.get).mockResolvedValue({ status: 206, data: new Blob(['package']), headers: {
      'content-range': 'bytes 0-6/7', 'content-length': '7', etag: '"fixed"',
    } });
    expect((await dataAcquisitionApi.download('job-head-timeout')).size).toBe(7);
    expect(apiClient.head).toHaveBeenCalledTimes(1);
    expect(apiClient.head).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ timeout: 12000, __dsaRetryCount: 2 }));
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403, 404])('stops at protected HEAD status %s without sending any GET', async status => {
    vi.mocked(apiClient.head).mockRejectedValue({ response: { status } });
    const updates: Array<{ percent?: number }> = [];
    await expect(dataAcquisitionApi.download('job-private', update => updates.push(update))).rejects.toMatchObject({ response: { status } });
    expect(apiClient.head).toHaveBeenCalledTimes(1);
    expect(apiClient.get).not.toHaveBeenCalled();
    expect(updates.some(update => update.percent === 100)).toBe(false);
  });

  it.each(['changed-etag', 'changed-length', 'ignored-range'])('fails closed when a chunk conflicts with HEAD metadata: %s', async mode => {
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': '7', 'accept-ranges': 'bytes', etag: '"head-fixed"' } });
    vi.mocked(apiClient.get).mockResolvedValue({ status: mode === 'ignored-range' ? 200 : 206, data: new Blob(['package']), headers: {
      'content-range': `bytes 0-6/${mode === 'changed-length' ? 8 : 7}`, 'content-length': '7', etag: mode === 'changed-etag' ? '"new-file"' : '"head-fixed"',
    } });
    const updates: Array<{ percent?: number }> = [];
    await expect(dataAcquisitionApi.download('job-changing', update => updates.push(update))).rejects.toThrow(/断点|版本/);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(updates.some(update => update.percent === 100)).toBe(false);
  });

  it.each([{ 'content-type': 'text/html' }, { 'content-encoding': 'gzip' }])('does not fall back from explicitly unsafe HEAD metadata %o', async extra => {
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: {
      'content-length': '7', 'accept-ranges': 'bytes', etag: '"head-fixed"', ...extra,
    } });
    await expect(dataAcquisitionApi.download('job-unsafe-head')).rejects.toThrow('元数据');
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it('retries only a failed first chunk in HEAD-parallel mode and preserves completed siblings', async () => {
    const size = 4 * 1024 * 1024, total = size * 2 + 1;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes', etag: '"head-fixed"' } });
    const counts = new Map<number, number>();
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const [start, end] = /bytes=(\d+)-(\d+)/.exec((config?.headers as Record<string, string>).Range)!.slice(1).map(Number);
      counts.set(start, (counts.get(start) ?? 0) + 1);
      if (start === 0 && counts.get(start)! < 3) throw { code: 'ERR_NETWORK' };
      const length = end - start + 1;
      return { status: 206, data: new Blob([new Uint8Array(length)]), headers: {
        'content-range': `bytes ${start}-${end}/${total}`, 'content-length': String(length), etag: '"head-fixed"',
      } };
    });
    expect((await dataAcquisitionApi.download('job-retry-zero')).size).toBe(total);
    expect(counts).toEqual(new Map([[0, 3], [size, 1], [size * 2, 1]]));
  });

  it('cancels all six active chunks and never reports completion after cancellation', async () => {
    const total = 4 * 1024 * 1024 * 8;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes', etag: '"head-fixed"' } });
    const signals: AbortSignal[] = [];
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((_resolve, reject) => {
      const signal = config?.signal as AbortSignal;
      signals.push(signal);
      signal.addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
    }));
    const controller = new AbortController(), updates: Array<{ percent?: number }> = [];
    const downloading = dataAcquisitionApi.download('job-cancel-parallel', update => updates.push(update), controller.signal);
    const rejection = expect(downloading).rejects.toMatchObject({ name: 'AbortError' });
    await vi.waitFor(() => expect(signals).toHaveLength(6));
    controller.abort();
    await rejection;
    expect(signals.every(signal => signal.aborted)).toBe(true);
    expect(apiClient.get).toHaveBeenCalledTimes(6);
    expect(updates.some(update => update.percent === 100)).toBe(false);
  });

  it('cancels a pending HEAD probe without starting any chunks', async () => {
    vi.mocked(apiClient.head).mockImplementation((_url, config) => new Promise((_resolve, reject) => {
      (config?.signal as AbortSignal).addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
    }));
    const controller = new AbortController();
    const downloading = dataAcquisitionApi.download('job-cancel-head', undefined, controller.signal);
    const rejection = expect(downloading).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await rejection;
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404, 416])('does not retry protected or invalid-range response %s', async status => {
    vi.mocked(apiClient.get).mockRejectedValue({ response: { status } });
    await expect(dataAcquisitionApi.download('job-one')).rejects.toMatchObject({ response: { status } });
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('limits transient retries and never reports failed data as completed', async () => {
    vi.mocked(apiClient.get).mockRejectedValue({ code: 'ERR_NETWORK' });
    const updates: Array<{ percent?: number }> = [];
    await expect(dataAcquisitionApi.download('job-one', update => updates.push(update))).rejects.toMatchObject({ code: 'ERR_NETWORK' });
    expect(apiClient.get).toHaveBeenCalledTimes(3);
    expect(updates.some(update => update.percent === 100)).toBe(false);
  });

  it('honors cancellation before a request and during a retry delay', async () => {
    const alreadyAborted = new AbortController(); alreadyAborted.abort();
    await expect(dataAcquisitionApi.download('job-one', undefined, alreadyAborted.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(apiClient.get).not.toHaveBeenCalled();
    const controller = new AbortController();
    vi.mocked(apiClient.get).mockImplementation(async () => { setTimeout(() => controller.abort(), 0); throw { code: 'ERR_NETWORK' }; });
    await expect(dataAcquisitionApi.download('job-one', undefined, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it.each(['bytes 1-6/7', 'bytes 0-5/7', 'bytes 0-6/*'])('rejects an inconsistent content range %s without retry', async range => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 206, data: new Blob(['package']), headers: { 'content-range': range, 'content-length': '7', etag: '"fixed"' } });
    await expect(dataAcquisitionApi.download('job-one')).rejects.toThrow(/范围|分块/);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it.each(['W/"weak"', '', 'not-quoted'])('rejects an unsafe range validator %s', async etag => {
    vi.mocked(apiClient.get).mockResolvedValue({ status: 206, data: new Blob(['package']), headers: { 'content-range': 'bytes 0-6/7', 'content-length': '7', etag } });
    await expect(dataAcquisitionApi.download('job-one')).rejects.toThrow('可靠');
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it.each(['changed-etag', 'ignored-range'])('will not concatenate a changed archive or later whole response: %s', async mode => {
    const size = 4 * 1024 * 1024;
    vi.mocked(apiClient.get).mockImplementation(async (_url, config) => {
      const start = Number(/bytes=(\d+)/.exec((config?.headers as Record<string, string>).Range)![1]);
      return start === 0
        ? { status: 206, data: new Blob([new Uint8Array(size)]), headers: { 'content-range': `bytes 0-${size - 1}/${size + 1}`, 'content-length': String(size), etag: '"original"' } }
        : { status: mode === 'ignored-range' ? 200 : 206, data: new Blob(['x']), headers: { 'content-range': `bytes ${size}-${size}/${size + 1}`, 'content-length': '1', etag: '"changed"' } };
    });
    const updates: Array<{ percent?: number }> = [];
    await expect(dataAcquisitionApi.download('job-one', update => updates.push(update))).rejects.toThrow(/断点|版本/);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(updates.some(update => update.percent === 100)).toBe(false);
  });

  it('allows continuously progressing chunks beyond 120 seconds with bounded total and idle timeouts', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': '100', 'accept-ranges': 'bytes', etag: '"slow"' } });
    let progress: ((event: never) => void) | undefined, finish: (() => void) | undefined;
    let requestSignal: AbortSignal | undefined;
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((resolve, reject) => {
      expect(config?.timeout).toBe(600000);
      requestSignal = config?.signal as AbortSignal;
      progress = config?.onDownloadProgress;
      requestSignal.addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
      finish = () => resolve({ status: 206, data: new Blob([new Uint8Array(100)]), headers: { 'content-length': '100', 'content-range': 'bytes 0-99/100', etag: '"slow"' } });
    }));
    const updates: AcquisitionDownloadProgress[] = [];
    const downloading = dataAcquisitionApi.download('slow', update => updates.push(update));
    await vi.advanceTimersByTimeAsync(0);
    for (let index = 1; index <= 7; index += 1) {
      await vi.advanceTimersByTimeAsync(20000);
      progress?.({ loaded: index * 10 } as never);
      expect(requestSignal?.aborted).toBe(false);
    }
    expect(updates.some(update => update.percent === 100)).toBe(false);
    finish?.();
    expect((await downloading).size).toBe(100);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(updates.at(-1)).toMatchObject({ percent: 100, phase: 'completed', retryCount: 0 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('aborts an idle attempt after 30 seconds, retries only that chunk, and clears timers', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    let progress: ((event: never) => void) | undefined;
    vi.mocked(apiClient.get).mockImplementationOnce((_url, config) => new Promise((_resolve, reject) => {
      progress = config?.onDownloadProgress;
      (config?.signal as AbortSignal).addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
    })).mockResolvedValue({ status: 200, data: new Blob(['package']), headers: { 'content-length': '7' } });
    const updates: AcquisitionDownloadProgress[] = [];
    const downloading = dataAcquisitionApi.download('idle', update => updates.push(update));
    await vi.advanceTimersByTimeAsync(10000); progress?.({ loaded: 0 } as never);
    await vi.advanceTimersByTimeAsync(10000); progress?.({ loaded: 0 } as never);
    await vi.advanceTimersByTimeAsync(9999);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(400);
    expect((await downloading).size).toBe(7);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(updates.at(-1)).toMatchObject({ retryCount: 1, percent: 100 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('reduces new attempt permits after failure, keeps healthy requests, and gradually recovers below six', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    const size = 4 * 1024 * 1024, total = size * 12;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes', etag: '"adaptive"' } });
    const pending = new Map<number, { resolve: () => void; reject: (error: unknown) => void; signal: AbortSignal }>();
    const counts = new Map<number, number>();
    let open = 0, maxOpen = 0;
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((resolve, reject) => {
      const [start, end] = /bytes=(\d+)-(\d+)/.exec((config?.headers as Record<string, string>).Range)!.slice(1).map(Number);
      counts.set(start, (counts.get(start) ?? 0) + 1);
      open += 1; maxOpen = Math.max(maxOpen, open);
      const done = () => { open -= 1; pending.delete(start); };
      pending.set(start, { signal: config?.signal as AbortSignal, reject: error => { done(); reject(error); }, resolve: () => {
        done(); resolve({ status: 206, data: new Blob([new Uint8Array(end - start + 1)]), headers: { 'content-length': String(end - start + 1), 'content-range': `bytes ${start}-${end}/${total}`, etag: '"adaptive"' } });
      } });
    }));
    const updates: AcquisitionDownloadProgress[] = [];
    const downloading = dataAcquisitionApi.download('adaptive', update => updates.push(update));
    await vi.advanceTimersByTimeAsync(0);
    expect(pending.size).toBe(6);
    pending.get(0)!.reject({ code: 'ERR_NETWORK' });
    await vi.advanceTimersByTimeAsync(400);
    expect(apiClient.get).toHaveBeenCalledTimes(6);
    expect([...pending.values()].every(value => !value.signal.aborted)).toBe(true);
    // One completion leaves four requests above the reduced cap of three.
    pending.get(size)!.resolve(); await vi.advanceTimersByTimeAsync(0);
    expect(apiClient.get).toHaveBeenCalledTimes(6);
    // Two healthy chunks recover one permit, never cancelling siblings.
    pending.get(size * 2)!.resolve(); await vi.advanceTimersByTimeAsync(0);
    expect(pending.size).toBe(4);
    for (let turn = 0; turn < 12 && pending.size; turn += 1) {
      [...pending.values()].forEach(value => value.resolve());
      await vi.advanceTimersByTimeAsync(0);
    }
    expect((await downloading).size).toBe(total);
    expect(maxOpen).toBe(6);
    expect(counts.get(0)).toBe(2);
    expect([...counts.entries()].filter(([start]) => start !== 0).every(([, count]) => count === 1)).toBe(true);
    expect(updates.at(-1)).toMatchObject({ retryCount: 1, activeConnections: 0, percent: 100 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([429, 503])('honors bounded Retry-After for %s without multiplying interceptor retries', async status => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status, headers: { 'retry-after': '2' } } })
      .mockResolvedValue({ status: 200, data: new Blob(['package']), headers: { 'content-length': '7' } });
    const downloading = dataAcquisitionApi.download('limited');
    await vi.advanceTimersByTimeAsync(1999);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect((await downloading).size).toBe(7);
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(vi.mocked(apiClient.get).mock.calls.every(([, config]) => (config as { __dsaRetryCount?: number }).__dsaRetryCount === 2)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('reports finite effective speed and ETA using RAM-only numeric statistics', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    const localSet = vi.spyOn(Storage.prototype, 'setItem');
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': '100', 'accept-ranges': 'bytes', etag: '"metrics"' } });
    let progress: ((event: never) => void) | undefined, finish: (() => void) | undefined;
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise(resolve => {
      progress = config?.onDownloadProgress;
      finish = () => resolve({ status: 206, data: new Blob([new Uint8Array(100)]), headers: { 'content-length': '100', 'content-range': 'bytes 0-99/100', etag: '"metrics"' } });
    }));
    const updates: AcquisitionDownloadProgress[] = [];
    const downloading = dataAcquisitionApi.download('private-job-name', update => updates.push(update));
    await vi.advanceTimersByTimeAsync(1000); progress?.({ loaded: 10 } as never);
    expect(updates.at(-1)).toMatchObject({ loaded: 10, bytesPerSecond: 10, estimatedSecondsRemaining: 9, activeConnections: 1 });
    progress?.({ loaded: Number.NaN } as never);
    await vi.advanceTimersByTimeAsync(6000);
    expect(updates.at(-1)).toMatchObject({ loaded: 10, bytesPerSecond: 0 });
    expect(updates.at(-1)?.estimatedSecondsRemaining).toBeUndefined();
    expect(updates.every(update => Object.entries(update).every(([key, value]) => typeof value !== 'number' || Number.isFinite(value) && !key.includes('job')))).toBe(true);
    expect(JSON.stringify(updates)).not.toContain('private-job-name');
    expect(localSet).not.toHaveBeenCalled();
    finish?.(); await downloading;
    localSet.mockRestore();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('caps excessive Retry-After at 30 seconds', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    vi.mocked(apiClient.get).mockRejectedValueOnce({ response: { status: 503, headers: { 'retry-after': '3600' } } })
      .mockResolvedValue({ status: 200, data: new Blob(['package']), headers: { 'content-length': '7' } });
    const downloading = dataAcquisitionApi.download('bounded-cooldown');
    await vi.advanceTimersByTimeAsync(29999);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect((await downloading).size).toBe(7);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps genuinely progressing legacy whole-file responses alive beyond the requested range length', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    const length = 6 * 1024 * 1024;
    let progress: ((event: never) => void) | undefined, finish: (() => void) | undefined;
    let requestSignal: AbortSignal | undefined;
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((resolve, reject) => {
      progress = config?.onDownloadProgress; requestSignal = config?.signal as AbortSignal;
      requestSignal.addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
      finish = () => resolve({ status: 200, data: new Blob([new Uint8Array(length)]), headers: { 'content-length': String(length) } });
    }));
    const downloading = dataAcquisitionApi.download('legacy-large');
    await vi.advanceTimersByTimeAsync(0);
    progress?.({ loaded: 4 * 1024 * 1024 } as never);
    await vi.advanceTimersByTimeAsync(20000); progress?.({ loaded: 5 * 1024 * 1024 } as never);
    await vi.advanceTimersByTimeAsync(20000); progress?.({ loaded: length } as never);
    expect(requestSignal?.aborted).toBe(false);
    finish?.();
    expect((await downloading).size).toBe(length);
    expect(apiClient.get).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('cancels degraded permit waiters and active siblings without starting new requests', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    const total = 4 * 1024 * 1024 * 9;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes', etag: '"waiting"' } });
    let fail: (() => void) | undefined;
    const signals: AbortSignal[] = [];
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((_resolve, reject) => {
      const signal = config?.signal as AbortSignal; signals.push(signal);
      if (!fail) fail = () => reject({ code: 'ERR_NETWORK' });
      signal.addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
    }));
    const controller = new AbortController();
    const downloading = dataAcquisitionApi.download('queued-abort', undefined, controller.signal);
    const rejection = expect(downloading).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    fail?.(); await vi.advanceTimersByTimeAsync(400);
    expect(signals).toHaveLength(6);
    controller.abort(); await rejection;
    expect(signals.slice(1).every(signal => signal.aborted)).toBe(true);
    expect(apiClient.get).toHaveBeenCalledTimes(6);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('uses at most two connections on explicit save-data networks and cancels queued waiters', async () => {
    vi.useFakeTimers(); vi.setSystemTime(0);
    vi.stubGlobal('navigator', { connection: { saveData: true, effectiveType: '4g' } });
    const total = 4 * 1024 * 1024 * 8;
    vi.mocked(apiClient.head).mockResolvedValue({ status: 200, headers: { 'content-length': String(total), 'accept-ranges': 'bytes', etag: '"save-data"' } });
    const signals: AbortSignal[] = [];
    vi.mocked(apiClient.get).mockImplementation((_url, config) => new Promise((_resolve, reject) => {
      const signal = config?.signal as AbortSignal; signals.push(signal);
      signal.addEventListener('abort', () => reject({ code: 'ERR_CANCELED' }), { once: true });
    }));
    const controller = new AbortController();
    const downloading = dataAcquisitionApi.download('save-data', undefined, controller.signal);
    const rejection = expect(downloading).rejects.toMatchObject({ name: 'AbortError' });
    await vi.advanceTimersByTimeAsync(0);
    expect(signals).toHaveLength(2);
    controller.abort(); await rejection;
    expect(signals.every(signal => signal.aborted)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('searches the local report library with explicit manual filters', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 30, source: 'local_sqlite' } });

    await dataAcquisitionApi.searchResearchReports({
      titleQuery: '低空经济', contentQuery: '无人机', broker: '测试证券', company: '', tsCode: '',
      reportType: '行业研报', industry: '航空装备', author: '', tag: '深度研究',
      startDate: '2024-08-24', endDate: '2026-08-24', hasPdf: true, sort: 'latest',
    });

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/data-acquisition/research-reports/search', expect.objectContaining({
      params: expect.objectContaining({ title_query: '低空经济', content_query: '无人机', broker: '测试证券', tag: '深度研究', has_pdf: true }),
    }));
  });

  it('exports only manually selected report ids', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: new Blob(['xlsx']) });

    await dataAcquisitionApi.exportSelectedResearchReports([3, 9]);

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/data-acquisition/research-reports/export-selected',
      { ids: [3, 9] },
      expect.objectContaining({ responseType: 'blob' }),
    );
  });
});
