import apiClient from './index';
import { toCamelCase } from './utils';
import { cachedQuery, invalidateCachedQueries } from './requestCache';
import type { AxiosRequestConfig } from 'axios';
import type {
  AcquisitionCapabilities,
  AcquisitionDownloadProgress,
  AcquisitionJob,
  AcquisitionPlan,
  AcquisitionPreflight,
  AcquisitionRunTask,
  ResearchReportFacets,
  ResearchReportLibraryStatus,
  ResearchReportSearchFilters,
  ResearchReportSearchResult,
} from '../types/dataAcquisition';

type RawPlan = Omit<AcquisitionPlan, 'outputFormats' | 'generatedAt' | 'scope' | 'preflight'> & {
  output_formats?: string[];
  generated_at?: string;
  include_files?: boolean;
  scope?: { symbols?: string[]; company_names?: string[]; keywords?: string[]; start_date?: string; end_date?: string; market_wide?: boolean;
    entity_queries?: string[]; entity_selections?: Record<string, string> };
  preflight?: unknown;
};

function readPlan(raw: RawPlan): AcquisitionPlan {
  return {
    ...raw,
    tasks: (raw.tasks ?? []).map((task) => ({ ...task, params: task.params ?? {} })),
    outputFormats: raw.output_formats ?? [],
    generatedAt: raw.generated_at ?? '',
    includeFiles: raw.include_files ?? false,
    preflight: raw.preflight ? toCamelCase<AcquisitionPreflight>(raw.preflight) : undefined,
    scope: {
      symbols: raw.scope?.symbols ?? [], companyNames: raw.scope?.company_names ?? [],
      keywords: raw.scope?.keywords ?? [], startDate: raw.scope?.start_date ?? '',
      endDate: raw.scope?.end_date ?? '', marketWide: raw.scope?.market_wide ?? false,
      entityQueries: raw.scope?.entity_queries,
      // Keys are user-provided entity queries, not API field names.
      entitySelections: raw.scope?.entity_selections,
    },
  };
}

function writePlan(plan: AcquisitionPlan) {
  return {
    title: plan.title, objective: plan.objective, model: plan.model, caveats: plan.caveats,
    generated_at: plan.generatedAt, output_formats: plan.outputFormats, include_files: plan.includeFiles ?? false,
    scope: { symbols: plan.scope.symbols, company_names: plan.scope.companyNames, keywords: plan.scope.keywords,
      start_date: plan.scope.startDate, end_date: plan.scope.endDate, market_wide: plan.scope.marketWide,
      entity_queries: plan.scope.entityQueries, entity_selections: plan.scope.entitySelections },
    // Preflight is a server-owned validation result, never trusted on submission.
    // params is an opaque upstream contract and must never be camel-cased.
    tasks: plan.tasks.map((task) => ({ ...task, params: task.params })),
  };
}

// ACQUISITION_RESUME_BEGIN: kept self-contained for the narrowly pinned production API overlay.
async function acqDownloadPackage(
  client: typeof apiClient,
  jobId: string,
  onProgress?: (progress: AcquisitionDownloadProgress) => void,
  signal?: AbortSignal,
): Promise<Blob> {
  const chunkSize = 4 * 1024 * 1024;
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
  const maxParallel = connection?.saveData || /^(slow-)?2g$/.test(connection?.effectiveType ?? '') ? 2 : 6;
  const controller = new AbortController();
  const abortError = () => new DOMException('下载已取消', 'AbortError');
  const checkAbort = () => { if (controller.signal.aborted) throw abortError(); };
  const cancel = () => controller.abort();
  if (signal?.aborted) throw abortError();
  signal?.addEventListener('abort', cancel, { once: true });
  let confirmed = 0;
  let total: number | undefined;
  let etag = '';
  let mime = 'application/zip';
  let metadataPinned = false;
  let permits = maxParallel;
  let openRequests = 0;
  let healthyChunks = 0;
  let retryCount = 0;
  let notBefore = 0;
  let phase: AcquisitionDownloadProgress['phase'] = 'probing';
  const permitWaiters = new Set<() => void>();
  const active = new Map<number, number>();
  const parts = new Map<number, Blob>();
  const samples = [{ at: Date.now(), loaded: 0 }];
  const report = (complete = false) => {
    const loaded = confirmed + [...active.values()].reduce((sum, value) => sum + value, 0);
    const now = Date.now();
    // Count useful aggregate bytes, not retransmitted wire bytes. Failed attempts
    // can roll progress back; a fresh window avoids negative or inflated rates.
    if (loaded < samples[samples.length - 1].loaded) samples.splice(0, samples.length, { at: now, loaded });
    else if (now > samples[samples.length - 1].at) samples.push({ at: now, loaded });
    else samples[samples.length - 1].loaded = loaded;
    while (samples.length > 2 && samples[1].at <= now - 5000) samples.shift();
    const elapsed = now - samples[0].at;
    const bytesPerSecond = elapsed > 0 ? Math.max(0, (loaded - samples[0].loaded) * 1000 / elapsed) : 0;
    const remaining = total === undefined || bytesPerSecond <= 0 ? undefined : Math.max(0, (total - loaded) / bytesPerSecond);
    onProgress?.({ loaded, total, percent: total ? complete ? 100 : Math.min(99, Math.floor(loaded / total * 100)) : undefined,
      bytesPerSecond, estimatedSecondsRemaining: complete ? 0 : remaining,
      activeConnections: openRequests, retryCount, phase: complete ? 'completed' : phase });
  };
  const wait = (ms: number) => new Promise<void>((resolve, reject) => {
    checkAbort();
    const aborted = () => { window.clearTimeout(timer); reject(abortError()); };
    const timer = window.setTimeout(() => { controller.signal.removeEventListener('abort', aborted); resolve(); }, ms);
    controller.signal.addEventListener('abort', aborted, { once: true });
  });
  const readHeader = (headers: unknown, name: string): string => {
    const values = headers as { get?: (key: string) => unknown; [key: string]: unknown } | undefined;
    return String(values?.get?.(name) ?? values?.[name.toLowerCase()] ?? values?.[name] ?? '');
  };
  const wakePermits = () => { [...permitWaiters].forEach(wake => wake()); };
  const acquirePermit = async () => {
    while (true) {
      checkAbort();
      if (openRequests < permits && Date.now() >= notBefore) { openRequests += 1; return; }
      await new Promise<void>((resolve, reject) => {
        let timer: number | undefined;
        const cleanup = () => { permitWaiters.delete(wake); controller.signal.removeEventListener('abort', aborted); window.clearTimeout(timer); };
        const wake = () => { cleanup(); resolve(); };
        const aborted = () => { cleanup(); reject(abortError()); };
        permitWaiters.add(wake);
        controller.signal.addEventListener('abort', aborted, { once: true });
        if (Date.now() < notBefore) timer = window.setTimeout(wake, notBefore - Date.now());
        if (controller.signal.aborted) aborted();
      });
    }
  };
  const retryable = (error: unknown) => {
    const value = error as { code?: string; name?: string; response?: { status?: number } };
    if (controller.signal.aborted || value?.code === 'ERR_CANCELED' || value?.name === 'AbortError') return false;
    const status = value?.response?.status;
    return status != null ? [408, 425, 429, 500, 502, 503, 504, 524].includes(status)
      : ['ERR_NETWORK', 'ECONNABORTED', 'ETIMEDOUT', 'ECONNRESET'].includes(value?.code ?? '');
  };
  const probeMetadata = async () => {
    try {
      const config: AxiosRequestConfig & { __dsaRetryCount: number } = {
        timeout: 12000, signal: controller.signal, __dsaRetryCount: 2,
        headers: { 'X-DSA-Route-Load': 'background' },
      };
      const response = await client.head(`/api/v1/data-acquisition/jobs/${encodeURIComponent(jobId)}/download`, config);
      checkAbort();
      if (response.status === 405 || response.status === 501) return;
      if (response.status !== 200) throw Object.assign(new Error('下载元数据请求失败，已停止'), { response: { status: response.status } });
      const encoding = readHeader(response.headers, 'content-encoding').trim().toLowerCase();
      const contentType = readHeader(response.headers, 'content-type');
      if (encoding && encoding !== 'identity' || /json|html/i.test(contentType)) {
        throw new Error('下载接口的文件元数据不可靠，已停止，未标记完成');
      }
      const length = readHeader(response.headers, 'content-length');
      const size = Number(length);
      const tag = readHeader(response.headers, 'etag');
      if (!/^\d+$/.test(length) || !Number.isSafeInteger(size) || size <= 0
        || !/^"[^"\r\n]+"$/.test(tag) || readHeader(response.headers, 'accept-ranges').trim().toLowerCase() !== 'bytes') return;
      total = size;
      etag = tag;
      metadataPinned = true;
    } catch (error) {
      checkAbort();
      const value = error as { code?: string; response?: { status?: number } };
      const status = value?.response?.status;
      // Old servers may lack HEAD, and an optional probe must not block the
      // existing first-range negotiation on a transient network/timeout failure.
      // Authentication and other real HTTP failures must never fall back to GET.
      if (status === 405 || status === 501 || status == null && retryable(error)) return;
      throw error;
    }
  };
  const requestChunk = async (start: number) => {
    const end = Math.min(start + chunkSize - 1, total === undefined ? Number.MAX_SAFE_INTEGER : total - 1);
    for (let attempt = 0; ; attempt += 1) {
      checkAbort();
      try {
        await acquirePermit();
        const attemptController = new AbortController();
        const cancelAttempt = () => attemptController.abort();
        controller.signal.addEventListener('abort', cancelAttempt, { once: true });
        if (controller.signal.aborted) attemptController.abort();
        let idleTimer: number | undefined;
        let idleTimedOut = false;
        let lastLoaded = 0;
        const resetIdle = () => {
          window.clearTimeout(idleTimer);
          idleTimer = window.setTimeout(() => { idleTimedOut = true; attemptController.abort(); }, 30000);
        };
        active.set(start, 0);
        if (attempt > 0) retryCount += 1;
        phase = 'downloading';
        resetIdle();
        try {
          const config: AxiosRequestConfig & { __dsaRetryCount: number } = {
            // The total stays bounded, but continuously arriving slow data must
            // not be mistaken for a stalled connection at the former 120s limit.
            responseType: 'blob', timeout: 600000, signal: attemptController.signal,
            // This bounded downloader owns its retries; do not multiply them by the generic interceptor.
            __dsaRetryCount: 2,
            headers: { 'X-DSA-Route-Load': 'background', Range: `bytes=${start}-${end}`, ...(etag ? { 'If-Range': etag } : {}) },
            onDownloadProgress: event => {
              if (controller.signal.aborted || attemptController.signal.aborted) return;
              const received = Number.isFinite(event.loaded) ? Math.max(lastLoaded, event.loaded) : lastLoaded;
              if (received > lastLoaded) { lastLoaded = received; resetIdle(); }
              // An old server can ignore the first Range and send the whole file.
              // Its genuine continuing bytes must also reset the idle watchdog.
              active.set(start, total === undefined ? received : Math.min(received, end - start + 1));
              report();
            },
          };
          const response = await client.get(`/api/v1/data-acquisition/jobs/${encodeURIComponent(jobId)}/download`, config);
          checkAbort();
          const blob = response.data as Blob;
          if (!(blob instanceof Blob)) throw new Error('下载响应不是文件，已停止，未标记完成');
          const contentType = readHeader(response.headers, 'content-type') || blob.type;
          if (/json|html/i.test(contentType)) throw new Error('下载接口未返回 ZIP 文件，请检查登录和任务结果');
          if (response.status === 200 && start === 0 && !metadataPinned) {
            const length = readHeader(response.headers, 'content-length');
            if (length && (!/^\d+$/.test(length) || Number(length) !== blob.size)) throw new Error('整包下载长度不完整，未标记完成');
            if (!blob.size) throw new Error('下载文件为空，未标记完成');
            total = blob.size;
            active.set(start, blob.size);
            return { blob, whole: true };
          }
          if (response.status !== 206) throw new Error('服务器未按断点返回文件，请重新下载，避免混合不同数据包');
          const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(readHeader(response.headers, 'content-range'));
          const tag = readHeader(response.headers, 'etag');
          const length = readHeader(response.headers, 'content-length');
          if (!range || !/^"[^"\r\n]+"$/.test(tag)) throw new Error('服务器未提供可靠的文件范围或版本标识，已停止续传');
          const [from, to, size] = range.slice(1).map(Number);
          if (![from, to, size].every(Number.isSafeInteger) || size <= 0 || from !== start || to !== Math.min(end, size - 1)
            || to < from || blob.size !== to - from + 1 || !/^\d+$/.test(length) || Number(length) !== blob.size
            || total !== undefined && size !== total || etag && tag !== etag) {
            throw new Error('下载分块长度或数据包版本不一致，已停止，未标记完成');
          }
          total = size;
          etag = tag;
          mime = blob.type || mime;
          active.set(start, blob.size);
          healthyChunks += 1;
          if (healthyChunks >= 2 && permits < maxParallel) { permits += 1; healthyChunks = 0; }
          return { blob, whole: false };
        } catch (error) {
          if (idleTimedOut && !controller.signal.aborted) throw Object.assign(new Error('下载连接30秒无数据进展，将有限重试'), { code: 'ETIMEDOUT' });
          throw error;
        } finally {
          window.clearTimeout(idleTimer);
          controller.signal.removeEventListener('abort', cancelAttempt);
          openRequests -= 1;
          wakePermits();
        }
      } catch (error) {
        active.delete(start);
        checkAbort();
        report();
        if (attempt >= 2 || !retryable(error)) throw error;
        permits = Math.max(1, Math.floor(permits / 2));
        healthyChunks = 0;
        phase = 'retrying';
        const value = error as { response?: { status?: number; headers?: unknown } };
        const retryAfter = [429, 503].includes(value.response?.status ?? 0) ? readHeader(value.response?.headers, 'retry-after').trim() : '';
        const delay = /^\d+(?:\.\d+)?$/.test(retryAfter) ? Number(retryAfter) * 1000 : Date.parse(retryAfter) - Date.now();
        const backoff = Math.max((attempt + 1) * 400, Number.isFinite(delay) ? Math.max(0, Math.min(30000, delay)) : 0);
        notBefore = Math.max(notBefore, Date.now() + backoff);
        await wait(backoff);
      }
    }
  };
  const progressTimer = window.setInterval(() => report(), 1000);
  try {
    await probeMetadata();
    let whole = false;
    if (!metadataPinned) {
      const first = await requestChunk(0);
      parts.set(0, first.blob);
      active.delete(0);
      confirmed = first.blob.size;
      whole = first.whole;
      report();
    }
    if (!whole && total !== undefined) {
      let next = confirmed;
      const worker = async () => {
        while (next < total!) {
          checkAbort();
          const start = next;
          next += chunkSize;
          const result = await requestChunk(start);
          parts.set(start, result.blob);
          active.delete(start);
          confirmed += result.blob.size;
          report();
        }
      };
      const workers = Array.from({ length: Math.min(maxParallel, Math.ceil((total - confirmed) / chunkSize)) }, worker);
      try { await Promise.all(workers); } catch (error) {
        controller.abort();
        await Promise.allSettled(workers);
        throw error;
      }
    }
    if (confirmed !== total) throw new Error('下载尚未完整完成，请重试');
    phase = 'assembling';
    const blob = parts.size === 1 ? parts.get(0)! : new Blob([...parts].sort(([a], [b]) => a - b).map(([, part]) => part), { type: mime });
    report(true);
    return blob;
  } finally {
    window.clearInterval(progressTimer);
    controller.abort();
    signal?.removeEventListener('abort', cancel);
    active.clear();
    parts.clear();
  }
}
// ACQUISITION_RESUME_END

export const dataAcquisitionApi = {
  capabilities: async (): Promise<AcquisitionCapabilities> => {
    return cachedQuery('acquisition:capabilities', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/capabilities');
      return toCamelCase<AcquisitionCapabilities>(response.data);
    }, { freshMs: 5 * 60_000, staleMs: 24 * 60 * 60_000 });
  },
  plan: async (request: string): Promise<AcquisitionPlan> => {
    const response = await apiClient.post('/api/v1/data-acquisition/plan', { request }, { timeout: 150000 });
    return readPlan(response.data as RawPlan);
  },
  validatePlan: async (request: string, plan: AcquisitionPlan): Promise<AcquisitionPlan> => {
    const response = await apiClient.post('/api/v1/data-acquisition/validate-plan', { request, plan: writePlan(plan) });
    return readPlan(response.data as RawPlan);
  },
  runAsync: async (request: string, plan: AcquisitionPlan): Promise<AcquisitionRunTask> => {
    const response = await apiClient.post('/api/v1/data-acquisition/run-async', { request, plan: writePlan(plan) });
    invalidateCachedQueries('acquisition:tasks');
    return toCamelCase<AcquisitionRunTask>(response.data);
  },
  task: async (taskId: string): Promise<AcquisitionRunTask> => {
    const response = await apiClient.get(`/api/v1/data-acquisition/tasks/${encodeURIComponent(taskId)}`);
    const task = toCamelCase<AcquisitionRunTask>(response.data);
    if (task.result) task.result.plan = readPlan((response.data as { result: { plan: RawPlan } }).result.plan);
    return task;
  },
  tasks: async (): Promise<{ items: AcquisitionRunTask[]; total: number }> => {
    return cachedQuery('acquisition:tasks', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/tasks', { params: { limit: 50 } });
      return toCamelCase(response.data);
    // Keep in-flight deduplication, but deliver refreshed task state to pages
    // that intentionally stop polling once their current list is finished.
    }, { freshMs: 1_000, staleMs: 0 });
  },
  jobs: async (): Promise<{ items: AcquisitionJob[]; total: number }> => {
    return cachedQuery('acquisition:jobs', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/jobs');
      return toCamelCase(response.data);
    }, { freshMs: 3_000, staleMs: 15_000 });
  },
  download: async (
    jobId: string,
    onProgress?: (progress: AcquisitionDownloadProgress) => void,
    signal?: AbortSignal,
  ): Promise<Blob> => acqDownloadPackage(apiClient, jobId, onProgress, signal),
  researchReportStatus: async (): Promise<ResearchReportLibraryStatus> => {
    return cachedQuery('acquisition:research-status', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/status');
      return toCamelCase(response.data);
    }, { freshMs: 5_000, staleMs: 30_000 });
  },
  syncResearchReports: async (years = 2): Promise<ResearchReportLibraryStatus> => {
    const response = await apiClient.post('/api/v1/data-acquisition/research-reports/sync', undefined, { params: { years } });
    invalidateCachedQueries('acquisition:research-');
    return toCamelCase(response.data);
  },
  researchReportFacets: async (): Promise<ResearchReportFacets> => {
    return cachedQuery('acquisition:research-facets', async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/facets');
      return toCamelCase(response.data);
    }, { freshMs: 10 * 60_000, staleMs: 24 * 60 * 60_000 });
  },
  searchResearchReports: async (
    filters: ResearchReportSearchFilters,
    page = 1,
    pageSize = 30,
  ): Promise<ResearchReportSearchResult> => {
    const key = `acquisition:research-search:${JSON.stringify({ filters, page, pageSize })}`;
    return cachedQuery(key, async () => {
      const response = await apiClient.get('/api/v1/data-acquisition/research-reports/search', {
        params: {
          title_query: filters.titleQuery, content_query: filters.contentQuery,
          broker: filters.broker, company: filters.company, ts_code: filters.tsCode,
          report_type: filters.reportType, industry: filters.industry, author: filters.author,
          tag: filters.tag, start_date: filters.startDate, end_date: filters.endDate,
          has_pdf: filters.hasPdf, sort: filters.sort, page, page_size: pageSize,
        },
      });
      return toCamelCase(response.data);
    }, { freshMs: 30_000, staleMs: 5 * 60_000 });
  },
  exportSelectedResearchReports: async (ids: number[]): Promise<Blob> => {
    const response = await apiClient.post('/api/v1/data-acquisition/research-reports/export-selected', { ids }, {
      responseType: 'blob', timeout: 120000,
    });
    return response.data as Blob;
  },
};
