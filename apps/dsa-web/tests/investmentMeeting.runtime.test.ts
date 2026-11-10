import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import html from '../public/home.html?raw';

type Question = { id: string; question: string; status: string; options: { id: string; label: string; description?: string; count?: number }[]; allow_custom?: boolean; context?: unknown; selected?: string; draft?: string; error?: string; awaiting_ack?: boolean; answer?: { option_id?: string; text?: string } };
type MeetingSession = { id: string; title: string; status: string; messages: { role: string; content: string; tasks?: { task_id: string; [key: string]: unknown }[] }[]; clarifications?: Question[] };
type MeetingClient = {
  ready: Promise<boolean>;
  refreshOwner: () => Promise<boolean>;
  createSession: (title: string) => MeetingSession;
  switchSession: (id: string) => void;
  send: () => Promise<void>;
  startManagedTask: () => Promise<void>;
  registerDownloadTask: (task: { task_id: string; poll_path?: string; [key: string]: unknown }) => void;
  sessionDisplayStatus: (session: unknown) => string;
  refreshEssayExports: () => Promise<void>;
  exportLedgerHtml: () => string;
  dispose: () => void;
  snapshot: () => { sessions: MeetingSession[]; activeId: string; owner: string | null; managedCount: number; downloads: Record<string, { status: string; progress: number | null; url?: string; retrieved_count?: number; checkpoint?: unknown; [key: string]: unknown }> };
};

let clients: MeetingClient[] = [];
let userId = 1;
let loggedIn = true;
const exportListPath = '/api/v1/financial-data/essays/package/tasks';
let exportListFetch: (() => Promise<Response>) | null = null;
const status = () => ({ accessEnabled: true, loggedIn, authMethod: 'password', user: loggedIn ? { id: userId, status: 'approved' } : null });
const response = (data: unknown, code = 200) => ({ ok: code < 400, status: code, json: async () => data });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const streamResponse = (events: unknown[]) => {
  let sent = false;
  return { ok: true, status: 200, body: { getReader: () => ({ read: async () => {
    if (sent) return { done: true };
    sent = true;
    return { done: false, value: new TextEncoder().encode(events.map(event => JSON.stringify(event)).join('\n') + '\n') };
  } }) } } as Response;
};
const questionFixture = (overrides: Partial<Question> = {}): Question => ({
  id: 'question-cat-l', question: '请选择宁德时代小作文的范围', status: 'pending', allow_custom: true,
  options: [
    { id: 'focused', label: '主旨聚焦宁德时代（整篇围绕公司）', description: '全文候选246篇，不是聚焦数；确认后AI逐篇阅读全文筛选，聚焦篇数尚未确定。' },
    { id: 'full', label: '全部提及宁德时代', description: '包含正文、标题和标签提及', count: 246 },
    { id: 'title', label: '仅标题包含宁德时代', description: '仅做标题关键词匹配，不代表文章主旨。', count: 37 },
  ], context: { keyword: '宁德时代', date_start: '2026-10-20', date_end: '2026-10-21', total: 246 },
  ...overrides,
});
const selectAnswer = (optionId: string, text = '') => {
  const radio = document.querySelector(`.clarification-card input[value="${optionId}"]`) as HTMLInputElement;
  radio.checked = true; radio.dispatchEvent(new Event('change', { bubbles: true }));
  const textarea = document.querySelector('.clarification-custom textarea') as HTMLTextAreaElement;
  if (textarea) { textarea.value = text; textarea.dispatchEvent(new Event('input', { bubbles: true })); }
};
const exportFixture = (overrides: Record<string, unknown> = {}) => ({
  task_id: 'essay-export-test-uuid', poll_path: '/api/v1/financial-data/essays/package/tasks/essay-export-test-uuid',
  status: 'collecting', phase: 'collecting', progress: null, matched_total: 24001, requested_limit: null,
  target_total: 24001, retrieved_count: 20001, packaged_count: 0, bytes_written: 0, bytes_total: null,
  checkpoint: { last_id: 20001 }, resumable: true, message: '正在读取原文', ...overrides,
});

async function install() {
  const delegate = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation((url, init) => String(url) === exportListPath
    ? exportListFetch ? exportListFetch() : Promise.resolve(response({ items: [], total: 0 }) as Response)
    : String(url) === '/api/v1/data-acquisition/tasks'
      ? Promise.resolve(response({items:[],total:0}) as Response) : delegate(url, init));
  const template = document.createElement('template');
  template.innerHTML = html;
  document.body.innerHTML = template.content.querySelector('body')?.innerHTML || html;
  const scripts = [...template.content.querySelectorAll('script:not([src])')].map(s => s.textContent).join('\n');
  const client = new Function(`${scripts}\nreturn {
    ready:meetingReady, refreshOwner, createSession, switchSession, send, startManagedTask, registerDownloadTask,
    sessionDisplayStatus, refreshEssayExports, exportLedgerHtml,
    dispose:disposeMeeting,
    snapshot:()=>({sessions,activeId,owner:ownerScope,managedCount:managedTasks.length,downloads})
  };`)() as MeetingClient;
  clients.push(client);
  await client.ready;
  return client;
}

beforeEach(() => {
  vi.useFakeTimers();
  userId = 1; loggedIn = true;
  exportListFetch = null;
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  vi.stubGlobal('CSS', { escape: (value: string) => value });
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === '/api/v1/user-auth/status') return response(status());
    throw new Error(`Unexpected request: ${url}`);
  }));
});
afterEach(() => {
  clients.forEach(client => client.dispose()); clients = [];
  vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('investment meeting standalone client contracts', () => {
  it('does not import unowned legacy histories and isolates records by the authenticated account', async () => {
    localStorage.setItem('leziwu.sessions.v1', JSON.stringify([{ id: 'legacy', title: 'PRIVATE LEGACY', messages: [] }]));
    localStorage.setItem('leziwu.downloads', JSON.stringify({ legacy: { task_id: 'legacy', url: '/private-file' } }));
    const client = await install();
    expect(document.body.textContent).not.toContain('PRIVATE LEGACY');
    client.createSession('ONLY USER ONE');
    expect(localStorage.getItem('leziwu.sessions.v2:user:1')).toContain('ONLY USER ONE');
    (document.getElementById('input') as HTMLTextAreaElement).value = 'PRIVATE UNSENT DRAFT';
    userId = 2;
    await client.refreshOwner();
    expect(client.snapshot().owner).toBe('user:2');
    expect(document.body.textContent).not.toContain('ONLY USER ONE');
    expect((document.getElementById('input') as HTMLTextAreaElement).value).toBe('');
    expect(client.snapshot().downloads).toEqual({});
    userId = 1;
    await client.refreshOwner();
    expect(document.body.textContent).toContain('ONLY USER ONE');
  });

  it('does not load private local records while identity is unavailable', async () => {
    localStorage.setItem('leziwu.sessions.v2:user:1', JSON.stringify([{ id: 'private', title: 'DO NOT DISPLAY', messages: [] }]));
    vi.mocked(fetch).mockRejectedValue(new Error('offline'));
    const client = await install();
    expect(client.snapshot().sessions).toEqual([]);
    expect(document.body.textContent).not.toContain('DO NOT DISPLAY');
    expect(document.body.textContent).toContain('暂时无法确认登录身份');
    expect(document.getElementById('send')).toBeDisabled();
  });

  it('routes a streamed result to its original session after switching to another session', async () => {
    let finish!: (value: unknown) => void;
    const pending = new Promise(resolve => { finish = resolve; });
    vi.mocked(fetch).mockImplementation(async (url) => url === '/api/v1/user-auth/status'
      ? response(status()) as Response : pending as Promise<Response>);
    const client = await install();
    const first = client.snapshot().activeId;
    const second = client.createSession('SECOND SESSION').id;
    client.switchSession(first);
    (document.getElementById('input') as HTMLTextAreaElement).value = 'Question for first session';
    const sending = client.send();
    await flush();
    client.switchSession(second);
    let sent = false;
    finish({ ok: true, status: 200, body: { getReader: () => ({ read: async () => {
      if (sent) return { done: true };
      sent = true;
      return { done: false, value: new TextEncoder().encode(JSON.stringify({ type: 'done', payload: { reply: 'FIRST SESSION ANSWER' } }) + '\n') };
    } }) } });
    await sending;
    expect(client.snapshot().sessions.find(s => s.id === first)?.messages.at(-1)?.content).toBe('FIRST SESSION ANSWER');
    expect(client.snapshot().sessions.find(s => s.id === second)?.messages).toEqual([]);
    expect(document.getElementById('messages')?.textContent).not.toContain('FIRST SESSION ANSWER');
    client.switchSession(first);
    expect(document.getElementById('messages')?.textContent).toContain('FIRST SESSION ANSWER');
    expect(document.getElementById('liveRow')).toBeNull();
  });

  it('ignores old stream callbacks when the authenticated owner changes', async () => {
    let finish!: (value: unknown) => void;
    const pending = new Promise(resolve => { finish = resolve; });
    vi.mocked(fetch).mockImplementation(async (url) => url === '/api/v1/user-auth/status'
      ? response(status()) as Response : pending as Promise<Response>);
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = 'Private question';
    const sending = client.send(); await flush();
    userId = 2;
    await client.refreshOwner();
    finish({ ok: true, status: 200, body: { getReader: () => { throw new Error('Must not read another owner response'); } } });
    await sending;
    expect(client.snapshot().owner).toBe('user:2');
    expect(document.getElementById('messages')?.textContent).not.toContain('Private question');
    expect(client.snapshot().sessions.every(s => s.messages.length === 0)).toBe(true);
  });

  it('uses one polling loop per task despite repeated stream/card registration', async () => {
    vi.mocked(fetch).mockImplementation(async (url) => response(url === '/api/v1/user-auth/status'
      ? status() : { ok: true, status: 200, data: { status: 'processing', progress: 25 } }) as Response);
    const client = await install();
    const task = { task_id: 'one-task', poll_path: '/api/v1/data-acquisition/tasks/one-task' };
    client.registerDownloadTask(task); client.registerDownloadTask(task); client.registerDownloadTask(task);
    await flush();
    const taskRequests = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'));
    expect(taskRequests()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(taskRequests()).toHaveLength(2);
    expect(client.snapshot().downloads['one-task'].progress).toBe(25);
    client.dispose();
    await vi.advanceTimersByTimeAsync(9000);
    expect(taskRequests()).toHaveLength(2);
  });

  it('stops polling expired credentials and preserves cleanup for inaccessible acquisition tasks', async () => {
    vi.mocked(fetch).mockImplementation(async (url) => response(url === '/api/v1/user-auth/status'
      ? status() : { ok: false, status: 401, data: { detail: 'Unauthorized' } }) as Response);
    const client = await install();
    client.registerDownloadTask({ task_id: 'expired-session' }); await flush();
    expect(client.snapshot().downloads['expired-session'].status).toBe('failed');
    const requests = vi.mocked(fetch).mock.calls.length;
    await vi.advanceTimersByTimeAsync(9000);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(requests);
    vi.mocked(fetch).mockResolvedValue(response({ ok: true, status: 200, data: { error: 'data_acquisition_error', message: '无权访问' } }) as Response);
    client.registerDownloadTask({ task_id: 'inaccessible' }); await flush();
    expect(client.snapshot().downloads['inaccessible']).toBeUndefined();
  });

  it('marks reloaded synchronous requests as unconfirmed instead of claiming background recovery', async () => {
    localStorage.setItem('leziwu.sessions.v2:user:1', JSON.stringify([{
      id: 'interrupted-task', title: 'Interrupted research', status: 'running', history: [], messages: [],
    }]));
    const client = await install();
    expect(client.snapshot().sessions[0].status).toBe('interrupted');
    expect(client.snapshot().managedCount).toBe(0);
    expect(document.getElementById('messages')?.textContent).toContain('不会自动重发');
    expect(document.getElementById('messages')?.textContent).toContain('任务页核对');
  });

  it('records HTTP failures in their session instead of leaving a task permanently running', async () => {
    vi.mocked(fetch).mockImplementation(async (url) => (url === '/api/v1/user-auth/status'
      ? response(status()) : response({ detail: { message: 'quota limit' } }, 429)) as Response);
    const client = await install();
    (document.getElementById('taskInput') as HTMLInputElement).value = 'Managed request';
    await client.startManagedTask();
    const task = client.snapshot().sessions.find(s => s.title === 'Managed request');
    expect(task?.status).toBe('failed');
    expect(task?.messages.at(-1)?.content).toContain('quota limit');
    expect(document.getElementById('messages')?.textContent).toContain('quota limit');
  });

  it('renders a streamed scope question with true counts above 20 and waits instead of completing', async () => {
    const clarification = questionFixture();
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response
      : streamResponse([{ type: 'clarification', clarification }, { type: 'done', payload: { reply: '关键词召回已完成，请选择范围。', status: 'waiting_input', clarification } }]));
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '给我下载宁德时代这两天的小作文';
    await client.send();
    expect(document.querySelectorAll('.clarification-card')).toHaveLength(1);
    expect(document.querySelector('.clarification-card')?.textContent).toContain('246 条');
    expect(document.querySelector('.clarification-context')?.textContent).toContain('2026-10-20 → 2026-10-21');
    expect(client.snapshot().sessions[0].status).toBe('waiting_input');
    expect(document.querySelector('[data-clarification-submit]')).not.toBeDisabled();
    expect(document.querySelector('.session-item')?.textContent).toContain('等待选择');
    const options = document.querySelectorAll('.clarification-option');
    expect(options).toHaveLength(3);
    expect(options[0].textContent).toContain('主旨聚焦');
    expect(options[0].querySelector('.clarification-count')).toBeNull();
    expect(options[0].textContent).toContain('候选246篇，不是聚焦数');
    expect(options[2].textContent).toContain('仅标题');
  });

  it('submits the selected scope and custom text once, without converting it to a new unstructured request', async () => {
    let complete!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { complete = resolve; });
    let calls = 0;
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      calls++;
      if (calls === 1) return streamResponse([{ type: 'done', payload: { reply: '请选择范围', status: 'waiting_input', clarification: questionFixture() } }]);
      return pending;
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代的小作文'; await client.send();
    selectAnswer('full', '只下载原文，不做 AI 分析');
    const submit = document.querySelector('[data-clarification-submit]') as HTMLButtonElement;
    submit.click(); submit.click(); await flush();
    const continuations = vi.mocked(fetch).mock.calls.filter(([, init]) => String(init?.body).includes('clarification_answer'));
    expect(continuations).toHaveLength(1);
    expect(JSON.parse(String(continuations[0][1]?.body))).toMatchObject({ clarification_answer: { question_id: 'question-cat-l', option_id: 'full', text: '只下载原文，不做 AI 分析' } });
    expect(document.querySelector('[data-clarification-submit]')).toBeDisabled();
    complete(streamResponse([{ type: 'done', payload: { reply: '已提交全部匹配原文的后台导出任务。' } }]));
    await flush();
    expect(client.snapshot().sessions[0].clarifications?.[0].status).toBe('answered');
    expect(document.querySelector('[data-clarification-submit]')).toBeNull();
    expect(document.querySelector('.clarification-answer')?.textContent).toContain('全部提及宁德时代');
  });

  it('restores an owner-scoped pending question and saved selection after reload, without automatically answering it', async () => {
    localStorage.setItem('leziwu.sessions.v2:user:1', JSON.stringify([{
      id: 'saved', title: 'Saved scope', sessionId: 'saved', workspace: 'ws-saved', status: 'waiting_input', messages: [], history: [],
      clarifications: [questionFixture({ selected: 'full', draft: '需要 Excel' })],
    }]));
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { status: 'waiting_input', clarification: questionFixture() }) as Response);
    const client = await install(); await flush();
    expect(client.snapshot().sessions[0].status).toBe('waiting_input');
    expect(document.querySelector('.clarification-card input[value="full"]')).toBeChecked();
    expect((document.querySelector('.clarification-custom textarea') as HTMLTextAreaElement).value).toBe('需要 Excel');
    expect(document.querySelector('[data-clarification-submit]')).not.toBeDisabled();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes('/clarifications/question-cat-l?workspace_id=ws-saved'))).toBe(true);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('retains the question and selection when validation rejects an answer, with a page-inline error', async () => {
    let requests = 0;
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      requests++;
      return requests === 1 ? streamResponse([{ type: 'done', payload: { reply: '请选择', status: 'waiting_input', clarification: questionFixture() } }]) : response({ detail: '选项已更新，请重新选择' }, 400) as Response;
    });
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send(); selectAnswer('focused', '最近两天');
    (document.querySelector('[data-clarification-submit]') as HTMLButtonElement).click(); await flush();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('选项已更新');
    expect(document.querySelector('.clarification-card input[value="focused"]')).toBeChecked();
    expect((document.querySelector('.clarification-custom textarea') as HTMLTextAreaElement).value).toBe('最近两天');
    expect(document.querySelector('[data-clarification-submit]')).not.toBeDisabled();
    expect(alert).not.toHaveBeenCalled();
    expect(client.snapshot().sessions[0].status).toBe('waiting_input');
  });

  it('does not answer until a real option or custom text is supplied', async () => {
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : streamResponse([{ type: 'done', payload: { status: 'waiting_input', clarification: questionFixture() } }]));
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send();
    const count = vi.mocked(fetch).mock.calls.length;
    (document.querySelector('[data-clarification-submit]') as HTMLButtonElement).click(); await flush();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('请先选择一项');
    expect(vi.mocked(fetch).mock.calls).toHaveLength(count);
  });

  it('restores multiple clarification cards and safely escapes their displayed labels', async () => {
    const clarification = questionFixture({ question: '<img src=x onerror=alert(1)>', options: [{ id: 'safe', label: '<script>bad()</script>', count: 21 }] });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : streamResponse([{ type: 'done', payload: { status: 'waiting_input', clarifications: [clarification, questionFixture({ id: 'second', question: '请选择导出格式' })] } }]));
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '研究与下载'; await client.send();
    expect(document.querySelectorAll('.clarification-card')).toHaveLength(2);
    expect(document.querySelector('.clarification-card img')).toBeNull();
    expect(document.querySelector('.clarification-card script')).toBeNull();
    expect(document.querySelector('.clarification-card')?.textContent).toContain('<script>bad()</script>');
  });

  it('keeps uncertain answers disabled until a read-only status check succeeds, and never retries the POST automatically', async () => {
    let submissions = 0;
    let readWorks = false;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).includes('/clarifications/')) {
        if (!readWorks) throw new Error('offline');
        return response({ status: 'waiting_input', clarification: questionFixture() }) as Response;
      }
      submissions++;
      if (submissions === 1) return streamResponse([{ type: 'done', payload: { status: 'waiting_input', clarification: questionFixture() } }]);
      expect(init?.method).toBe('POST'); throw new Error('connection lost');
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send(); selectAnswer('focused');
    (document.querySelector('[data-clarification-submit]') as HTMLButtonElement).click(); await flush();
    expect(submissions).toBe(2);
    expect(document.querySelector('[data-clarification-submit]')).toBeDisabled();
    expect(document.querySelector('[data-clarification-refresh]')).not.toBeDisabled();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('不会自动重发');
    await vi.advanceTimersByTimeAsync(15000);
    expect(submissions).toBe(2);
    readWorks = true;
    (document.querySelector('[data-clarification-refresh]') as HTMLButtonElement).click(); await flush();
    expect(document.querySelector('[data-clarification-submit]')).not.toBeDisabled();
    expect(document.querySelector('.clarification-card input[value="focused"]')).toBeChecked();
    expect(submissions).toBe(2);
  });

  it('reconciles a duplicate-answer conflict with server state instead of repeating execution', async () => {
    let posts = 0;
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).includes('/clarifications/')) return response({ status: 'answered', clarification: questionFixture({ status: 'answered', answer: { option_id: 'full', text: '' } }) }) as Response;
      posts++;
      return posts === 1 ? streamResponse([{ type: 'done', payload: { status: 'waiting_input', clarification: questionFixture() } }]) : response({ detail: '已提交' }, 409) as Response;
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send(); selectAnswer('full');
    (document.querySelector('[data-clarification-submit]') as HTMLButtonElement).click(); await flush();
    expect(posts).toBe(2);
    expect(document.querySelector('[data-clarification-submit]')).toBeNull();
    expect(document.querySelector('.clarification-state')?.textContent).toBe('已确认');
    expect(client.snapshot().sessions[0].status).toBe('done');
  });

  it('does not display another account pending questions after identity changes', async () => {
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : streamResponse([{ type: 'done', payload: { status: 'waiting_input', clarification: questionFixture() } }]));
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send(); selectAnswer('full', '私人补充');
    userId = 2; await client.refreshOwner();
    expect(document.querySelector('.clarification-card')).toBeNull();
    expect(document.body.textContent).not.toContain('私人补充');
    expect(client.snapshot().sessions.every(session => !session.clarifications?.length)).toBe(true);
  });

  it('recovers a completed answer and its file without resubmitting, and does not duplicate the result on another restore', async () => {
    localStorage.setItem('leziwu.sessions.v2:user:1', JSON.stringify([{
      id: 'recover-result', title: 'Interrupted answer', sessionId: 'recover-result', workspace: 'ws-saved', status: 'waiting_input', messages: [], history: [],
      clarifications: [questionFixture({ awaiting_ack: true })],
    }]));
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {
      status: 'answered', clarification: questionFixture({ status: 'answered', answer: { option_id: 'full', text: '' } }),
      result: { reply: '已完整导出 246 条原文。', status: 'completed', widgets: [{ type: 'file', filename: '宁德时代-246条.zip', url: '/api/v1/files/recovered.zip', size: 1024 }] },
    }) as Response);
    const client = await install(); await flush();
    expect(document.querySelector('a[href="/api/v1/files/recovered.zip"]')).not.toBeNull();
    expect(client.snapshot().sessions[0].messages.filter(message => message.content === '已完整导出 246 条原文。')).toHaveLength(1);
    client.dispose(); clients = clients.filter(item => item !== client);
    const restored = await install(); await flush();
    expect(restored.snapshot().sessions[0].messages.filter(message => message.content === '已完整导出 246 条原文。')).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it.each([['partial', '部分完成'], ['no_results', '未命中'], ['failed', '失败']])('keeps %s outcomes distinct from full completion', async (outcome, label) => {
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : streamResponse([{ type: 'done', payload: { status: outcome, reply: label } }]));
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '导出小作文'; await client.send();
    expect(client.snapshot().sessions[0].status).toBe(outcome);
    expect(document.querySelector('.session-item')?.textContent).toContain(label);
  });

  it('clears processing indicators after a result and keeps them cleared when switching back to the finished session', async () => {
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : streamResponse([{ type: 'done', payload: { status: 'completed', agent: 'research', reply: '已完整导出 237 条。', widgets: [{ type: 'file', filename: '原文.zip', url: '/files/ready.zip' }] } }]));
    const client = await install();
    const first = client.snapshot().activeId;
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载宁德时代'; await client.send();
    expect(document.getElementById('statusText')?.textContent).toBe('研究员已完成');
    expect(document.getElementById('boardMeta')?.textContent).toContain('无待处理步骤');
    client.createSession('Second'); client.switchSession(first);
    expect(document.getElementById('statusText')?.textContent).toBe('研究员已完成');
    expect(document.getElementById('boardMeta')?.textContent).not.toContain('正在处理');
    expect(document.querySelector('.seat.active')).toBeNull();
  });

  it('shows real export counts beyond 10000 without inventing an indexing percentage or total limit', async () => {
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: exportFixture() }) as Response);
    const client = await install();
    client.registerDownloadTask(exportFixture()); await flush();
    const dock = document.getElementById('dockItems');
    expect(dock?.textContent).toContain('不设篇数上限');
    expect(dock?.textContent).toContain('24,001');
    expect(dock?.textContent).toContain('20,001');
    expect(dock?.textContent).not.toContain('%');
    expect(dock?.querySelector('[role="progressbar"]')).not.toHaveAttribute('aria-valuenow');
    expect(dock?.querySelector('.indeterminate')).not.toBeNull();
    expect(client.snapshot().downloads['essay-export-test-uuid'].progress).toBeNull();
    expect(dock?.textContent).toContain('断点已保存');
  });

  it('shows a user-specified count separately from total matches and real packaged progress', async () => {
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: exportFixture({ requested_limit: 20, target_total: 20, retrieved_count: 20, packaged_count: 7, progress: 35, phase: 'packaging', status: 'packaging', bytes_written: 2048, bytes_total: 4096 }) }) as Response);
    const client = await install(); client.registerDownloadTask(exportFixture()); await flush();
    const dock = document.getElementById('dockItems');
    expect(dock?.textContent).toContain('按你指定的 20 篇');
    expect(dock?.textContent).toContain('匹配 24,001 篇');
    expect(dock?.textContent).toContain('本次目标 20 篇');
    expect(dock?.textContent).toContain('已整理 7 篇');
    expect(dock?.textContent).toContain('35%');
    expect(dock?.textContent).toContain('2 KB / 4 KB');
    expect(dock?.querySelector('[role="progressbar"]')).toHaveAttribute('aria-valuenow', '35');
  });

  it.each(['collecting', 'failed', 'no_results', 'completed'])('never exposes a partial or absent result as a complete download in %s', async taskState => {
    const result = taskState === 'no_results' ? null : { download_url: '/private/partial.zip', filename: 'partial.zip', complete: false };
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: exportFixture({ status: taskState, result }) }) as Response);
    const client = await install(); client.registerDownloadTask(exportFixture()); await flush();
    expect(document.querySelector('a[href="/private/partial.zip"]')).toBeNull();
    expect(client.snapshot().downloads['essay-export-test-uuid'].url).toBe('');
    if (taskState === 'no_results') {
      expect(document.getElementById('dockItems')?.textContent).toContain('未命中');
      const count = vi.mocked(fetch).mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(vi.mocked(fetch).mock.calls).toHaveLength(count);
    }
  });

  it('restores failed resumable exports and verifies complete files via GET, never a create request', async () => {
    localStorage.setItem('leziwu.downloads.v2:user:1', JSON.stringify({ 'essay-export-test-uuid': exportFixture({ status: 'failed', url: '/stale.zip', error: '服务中断' }) }));
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: exportFixture({ status: 'failed', error: '等待续跑' }) }) as Response);
    const client = await install(); await flush();
    expect(client.snapshot().downloads['essay-export-test-uuid'].retrieved_count).toBe(20001);
    expect(document.querySelector('[data-export-resume]')).not.toBeNull();
    expect(document.querySelector('a[href="/stale.zip"]')).toBeNull();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
    (document.getElementById('dockClear') as HTMLButtonElement).click();
    expect(client.snapshot().downloads['essay-export-test-uuid']).toBeDefined();
  });

  it('rechecks a cached completed download against the current account before displaying its file', async () => {
    localStorage.setItem('leziwu.downloads.v2:user:1', JSON.stringify({ 'essay-export-test-uuid': exportFixture({ status: 'completed', url: '/stale.zip', result: { complete: true } }) }));
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : pending);
    const client = await install();
    expect(document.querySelector('a[href="/stale.zip"]')).toBeNull();
    finish(response({ data: exportFixture({ status: 'completed', progress: 100, result: { complete: true, download_url: '/verified.zip', filename: '全部原文.zip' } }) }) as Response);
    await flush();
    expect(document.querySelector('a[href="/verified.zip"]')).not.toBeNull();
    expect(client.snapshot().downloads['essay-export-test-uuid'].url).toBe('/verified.zip');
  });

  it('verifies a newly registered completed export too and never trusts a streamed download URL alone', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : pending);
    const client = await install();
    client.registerDownloadTask(exportFixture({ status: 'completed', url: '/not-verified.zip' }));
    expect(document.querySelector('a[href="/not-verified.zip"]')).toBeNull();
    finish(response({ data: exportFixture({ status: 'no_results', result: null }) }) as Response); await flush();
    expect(client.snapshot().downloads['essay-export-test-uuid'].status).toBe('no_results');
    expect(document.querySelector('.dock-dl')).toBeNull();
  });

  it('resumes the same export id once despite repeated clicks and keeps one polling loop', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    let resumed = false;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (init?.method === 'POST') { resumed = true; return pending; }
      return response({ data: exportFixture({ status: resumed ? 'collecting' : 'failed' }) }) as Response;
    });
    const client = await install(); client.registerDownloadTask(exportFixture()); await flush();
    const button = document.querySelector('[data-export-resume]') as HTMLButtonElement;
    button.click(); button.click(); await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === 'POST')?.[0]).toBe('/api/v1/financial-data/essays/package/tasks/essay-export-test-uuid/resume');
    finish(response({ task_id: 'essay-export-test-uuid' }) as Response); await flush();
    const polls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task')).length;
    expect(polls()).toBe(2);
    await vi.advanceTimersByTimeAsync(3000);
    expect(polls()).toBe(3);
    expect(client.snapshot().downloads['essay-export-test-uuid'].retrieved_count).toBe(20001);
  });

  it('reconciles an uncertain resume response with GET and never retries the POST automatically', async () => {
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (init?.method === 'POST') throw new Error('connection lost after submit');
      return response({ data: exportFixture({ status: 'failed' }) }) as Response;
    });
    const client = await install(); client.registerDownloadTask(exportFixture()); await flush();
    (document.querySelector('[data-export-resume]') as HTMLButtonElement).click(); await flush();
    await vi.advanceTimersByTimeAsync(20000);
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(2);
  });

  it('ignores a resume callback from the previous account and does not leak its checkpoint or start another poll', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    vi.mocked(fetch).mockImplementation(async (url, init) => url === '/api/v1/user-auth/status' ? response(status()) as Response
      : init?.method === 'POST' ? pending : response({ data: exportFixture({ status: 'failed' }) }) as Response);
    const client = await install(); client.registerDownloadTask(exportFixture()); await flush();
    (document.querySelector('[data-export-resume]') as HTMLButtonElement).click(); await flush();
    userId = 2; await client.refreshOwner();
    const count = vi.mocked(fetch).mock.calls.length;
    finish(response({ status: 'queued' }) as Response); await flush();
    expect(client.snapshot().downloads).toEqual({});
    expect(document.getElementById('dockItems')?.textContent).toBe('');
    expect(vi.mocked(fetch).mock.calls).toHaveLength(count);
  });

  it('marks a submitted background export as running until its verified file completes without blocking another question', async () => {
    let done = false;
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).startsWith('/api/v1/command/task')) return response({ data: exportFixture(done ? { status: 'completed', progress: 100, result: { complete: true, download_url: '/all.zip' } } : {}) }) as Response;
      return streamResponse([{ type: 'done', payload: { status: 'submitted', reply: '已提交后台导出，可继续提问。', tasks: [exportFixture({ status: 'queued' })] } }]);
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '下载全部宁德时代小作文';
    await client.send(); await flush();
    expect(document.querySelector('.session-item')?.textContent).toContain('后台执行中');
    expect(document.getElementById('statusText')?.textContent).toBe('后台执行中');
    expect(document.getElementById('send')).not.toBeDisabled();
    expect(document.querySelector('.task-card')?.textContent).toContain('20,001');
    expect(document.querySelector('.task-card [role="progressbar"]')).not.toHaveAttribute('aria-valuenow');
    done = true;
    await vi.advanceTimersByTimeAsync(3000);
    expect(document.querySelector('.session-item')?.textContent).toContain('已完成');
    expect(document.querySelector('.task-card a[href="/all.zip"]')).not.toBeNull();
    (document.getElementById('dockClear') as HTMLButtonElement).click();
    expect(document.getElementById('downloadDock')).not.toBeVisible();
    expect(document.querySelector('.session-item')?.textContent).toContain('已完成');
    expect(document.querySelector('.task-card a[href="/all.zip"]')).not.toBeNull();
  });

  it('opens a task-center export deep link with one GET and no model or create POST', async () => {
    window.history.replaceState({}, '', '/meeting-embed?export_task=essay-export-test-uuid');
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: exportFixture({ status: 'failed' }) }) as Response);
    const client = await install(); await flush();
    expect(document.querySelector('.task-card[data-task="essay-export-test-uuid"]')).not.toBeNull();
    expect(document.querySelector('.session-item.active')?.textContent).toContain('待续跑');
    expect(document.querySelector('.task-card')?.textContent).toContain('断点已保存');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
    expect(client.snapshot().downloads['essay-export-test-uuid']).toBeDefined();
  });

  it('restores durable exports without browser history and exposes them in both standalone drawers', async () => {
    const task = exportFixture({ status: 'failed', error: '等待恢复', filters: { query: '宁德时代' } });
    exportListFetch = async () => response({ items: [task], total: 1 }) as Response;
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status'
      ? status() : String(url).startsWith('/api/v1/financial-data/workspace-files')
        ? { items: [] } : { data: task }) as Response);
    const client = await install(); await flush();
    expect(client.snapshot().downloads[task.task_id].retrieved_count).toBe(20001);
    (document.getElementById('taskBtn') as HTMLButtonElement).click(); await flush();
    expect(document.getElementById('taskList')?.textContent).toContain('宁德时代');
    expect(document.getElementById('taskList')?.textContent).toContain('24,001');
    expect(document.querySelector('#taskList [data-export-resume]')).not.toBeNull();
    (document.getElementById('taskClose') as HTMLButtonElement).click();
    (document.getElementById('filesBtn') as HTMLButtonElement).click(); await flush();
    expect(document.getElementById('wsFiles')).toBeVisible();
    expect(document.getElementById('wsFilesList')?.textContent).toContain('宁德时代');
    expect(document.querySelector('#wsFilesList [data-export-view]')).not.toBeNull();
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('coalesces overlapping restore requests and retains one polling loop after opening drawers repeatedly', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    exportListFetch = () => pending;
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status'
      ? status() : String(url).startsWith('/api/v1/financial-data/workspace-files')
        ? { items: [] } : { data: exportFixture() }) as Response);
    await install();
    (document.getElementById('taskBtn') as HTMLButtonElement).click();
    (document.getElementById('filesBtn') as HTMLButtonElement).click(); await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === exportListPath)).toHaveLength(1);
    finish(response({ items: [exportFixture()], total: 1 }) as Response); await flush();
    const polls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'));
    expect(polls()).toHaveLength(1);
    (document.getElementById('taskBtn') as HTMLButtonElement).click(); await flush();
    (document.getElementById('filesBtn') as HTMLButtonElement).click(); await flush();
    expect(polls()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(polls()).toHaveLength(2);
  });

  it('ignores a late server ledger response after switching accounts and does not import private task names', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    exportListFetch = () => userId === 1 ? pending : Promise.resolve(response({ items: [], total: 0 }) as Response);
    const client = await install();
    userId = 2; await client.refreshOwner(); await flush();
    finish(response({ items: [exportFixture({ title: 'PRIVATE USER ONE EXPORT' })], total: 1 }) as Response); await flush();
    expect(client.snapshot().owner).toBe('user:2');
    expect(client.snapshot().downloads).toEqual({});
    expect(document.body.textContent).not.toContain('PRIVATE USER ONE EXPORT');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(0);
  });

  it('keeps saved resumable failures when the task list is empty or temporarily unavailable', async () => {
    localStorage.setItem('leziwu.downloads.v2:user:1', JSON.stringify({ 'essay-export-test-uuid': exportFixture({ status: 'failed' }) }));
    exportListFetch = async () => response({ detail: 'upstream busy' }, 503) as Response;
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status'
      ? status() : { data: exportFixture({ status: 'failed' }) }) as Response);
    const client = await install(); await flush();
    (document.getElementById('taskBtn') as HTMLButtonElement).click(); await flush();
    expect(client.snapshot().downloads['essay-export-test-uuid'].retrieved_count).toBe(20001);
    expect(document.getElementById('taskList')?.textContent).toContain('已保留已有任务与断点');
    expect(document.querySelector('#taskList [data-export-resume]')).not.toBeNull();
    exportListFetch = async () => response({ items: [], total: 0 }) as Response;
    (document.getElementById('taskBtn') as HTMLButtonElement).click(); await flush();
    expect(client.snapshot().downloads['essay-export-test-uuid']).toBeDefined();
    expect(document.getElementById('taskList')?.textContent).not.toContain('暂无小作文后台导出');
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('opens a restored result from the file drawer without the new SPA route or a create request', async () => {
    const path = exportFixture().poll_path;
    const task = exportFixture({ status: 'completed', progress: 100, retrieved_count: 24001, packaged_count: 24001,
      result: { complete: true, download_url: path + '/download', filename: '宁德时代_全部原文.zip', count: 24001 } });
    exportListFetch = async () => response({ items: [task], total: 1 }) as Response;
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status'
      ? status() : String(url).startsWith('/api/v1/financial-data/workspace-files')
        ? { items: [] } : { data: task }) as Response);
    await install(); await flush();
    (document.getElementById('filesBtn') as HTMLButtonElement).click(); await flush();
    expect(document.querySelector(`#wsFilesList a[href="${path}/download"]`)).not.toBeNull();
    (document.querySelector('#wsFilesList [data-export-view]') as HTMLButtonElement).click(); await flush();
    expect(document.querySelector(`.task-card[data-task="${task.task_id}"]`)).not.toBeNull();
    expect(document.getElementById('wsFiles')).not.toBeVisible();
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('does not replace a newly completed ledger snapshot with an older in-flight progress response', async () => {
    let finish!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    const earlier = exportFixture({ updated_at: '2026-10-22T01:00:00Z' });
    exportListFetch = async () => response({ items: [earlier], total: 1 }) as Response;
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : pending);
    const client = await install(); await flush();
    const path = earlier.poll_path;
    const completed = exportFixture({ status: 'completed', progress: 100, updated_at: '2026-10-22T01:01:00Z',
      result: { complete: true, download_url: path + '/download', filename: '已完成.zip' } });
    exportListFetch = async () => response({ items: [completed], total: 1 }) as Response;
    (document.getElementById('taskBtn') as HTMLButtonElement).click(); await flush();
    expect(client.snapshot().downloads[earlier.task_id].status).toBe('completed');
    finish(response({ data: earlier }) as Response); await flush();
    expect(client.snapshot().downloads[earlier.task_id].status).toBe('completed');
    expect(document.querySelector(`#taskList a[href="${path}/download"]`)).not.toBeNull();
  });

  it('keeps subject screening in progress and separates candidates from unknown focused totals and final limits', async () => {
    const task = exportFixture({ status: 'screening', phase: 'screening', filters: { query_scope: 'focused', query: '宁德时代' },
      progress: null, matched_total: null, target_total: null, requested_limit: 3, candidate_total: 37,
      screened_count: 12, focused_count: 5, mentioned_count: 7, irrelevant_count: 0, uncertain_count: 0,
      focus_screening_complete: false, retrieved_count: 37,
      focus_review_path: exportFixture().poll_path + '/focus-review' });
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).startsWith('/api/v1/command/task')) return response({ data: task }) as Response;
      return streamResponse([{ type: 'done', payload: { status: 'submitted', reply: '已提交后台全文主旨筛选', tasks: [task] } }]);
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '整篇围绕宁德时代的，最多3篇';
    await client.send(); await flush();
    const card = document.querySelector('.task-card');
    expect(card?.textContent).toContain('正在阅读全文判定主旨');
    expect(card?.textContent).toContain('检索候选 37 条');
    expect(card?.textContent).toContain('已判定 12 条');
    expect(card?.textContent).toContain('已确认聚焦 5 篇');
    expect(card?.textContent).toContain('聚焦总数 待筛选完成');
    expect(card?.textContent).toContain('本次目标 筛选后确定');
    expect(card?.textContent).toContain('全部候选筛选后最多导出 3 篇');
    expect(card?.textContent).toContain('不生成新的小作文研判或转写');
    expect(card?.textContent).not.toContain('聚焦总数 37');
    expect(document.querySelector('.session-item')?.textContent).toContain('后台执行中');
    expect(document.getElementById('statusText')?.textContent).toBe('后台执行中');
    expect(card?.querySelector('[role="progressbar"]')).not.toHaveAttribute('aria-valuenow');
    expect(card?.querySelector('a[download]')).toBeNull();
    expect(card?.querySelector('a[href$="/focus-review"]')).toHaveTextContent('查看主旨筛选依据');
    expect(card?.querySelector('a[href$="/focus-review"]')).toHaveAttribute('target', '_blank');
    expect(document.getElementById('send')).not.toBeDisabled();
  });

  it('updates semantic counts from polling and only exposes the post-screening complete export', async () => {
    let completed = false;
    const task = exportFixture({ status: 'screening', phase: 'screening', filters: { query_scope: 'focused' },
      matched_total: null, target_total: null, requested_limit: 3, candidate_total: 37, screened_count: 12,
      focused_count: 5, mentioned_count: 7, irrelevant_count: 0, uncertain_count: 0, focus_screening_complete: false });
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status()
      : { data: completed ? { ...task, status: 'completed', progress: 100, screened_count: 37, focused_count: 7,
          mentioned_count: 29, irrelevant_count: 1, focus_screening_complete: true, matched_total: 7, target_total: 3,
          retrieved_count: 3, packaged_count: 3, result: { complete: true, filename: '聚焦原文3篇.zip', download_url: '/focus-3.zip' } } : task }) as Response);
    const client = await install();
    client.registerDownloadTask({ task_id: task.task_id, poll_path: task.poll_path }); await flush();
    expect(document.getElementById('dockItems')?.textContent).toContain('检索候选 37 条');
    expect(document.getElementById('dockItems')?.textContent).toContain('待筛选完成');
    completed = true; await vi.advanceTimersByTimeAsync(3000);
    expect(document.getElementById('dockItems')?.textContent).toContain('已判定 37 条');
    expect(document.getElementById('dockItems')?.textContent).toContain('聚焦总数 7 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('本次目标 3 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('所有候选已判定');
    expect(document.querySelector('a[href="/focus-3.zip"]')).not.toBeNull();
  });

  it('shows uncertain subject decisions as resumable rather than silently excluding them', async () => {
    const task = exportFixture({ status: 'failed', phase: 'screening', filters: { query_scope: 'focused' },
      matched_total: null, target_total: null, candidate_total: 37, screened_count: 35, focused_count: 5,
      mentioned_count: 29, irrelevant_count: 1, uncertain_count: 2, focus_screening_complete: false,
      error: '2篇主旨尚未确认，保留断点，继续时只重试未确认的原文。', resumable: true });
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: task }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(document.getElementById('dockItems')?.textContent).toContain('待核验 2 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('聚焦总数 待筛选完成');
    expect(document.querySelector('[data-export-resume]')).not.toBeNull();
    expect(document.querySelector('.dock-dl')).toBeNull();
    expect(client.snapshot().downloads[task.task_id].status).toBe('failed');
  });

  it('revalidates an existing failed export id and restarts one poll loop after the server has resumed it elsewhere', async () => {
    let resumed = false;
    const task = exportFixture({ status: 'failed', phase: 'screening', filters: { query_scope: 'focused' },
      candidate_total: 25, focused_count: 16, mentioned_count: 5, uncertain_count: 4,
      matched_total: null, target_total: null, focus_screening_complete: false });
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status()
      : { data: resumed ? { ...task, status: 'screening', mentioned_count: 6, uncertain_count: 3 } : task }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(client.snapshot().downloads[task.task_id].status).toBe('failed');
    resumed = true;
    client.registerDownloadTask(task); client.registerDownloadTask(task); await flush();
    const polls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'));
    expect(polls()).toHaveLength(2);
    expect(client.snapshot().downloads[task.task_id].status).toBe('screening');
    expect(document.getElementById('dockItems')?.textContent).toContain('仅提及 6 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('待核验 3 篇');
    await vi.advanceTimersByTimeAsync(3000);
    expect(polls()).toHaveLength(3);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('reconciles the exact old task used by a generic Agent resume call instead of leaving its failed counts stale', async () => {
    let resumed = false;
    let requests = 0;
    const task = exportFixture({ status: 'failed', phase: 'screening', filters: { query_scope: 'focused' },
      candidate_total: 25, focused_count: 16, mentioned_count: 5, uncertain_count: 4,
      matched_total: null, target_total: null, focus_screening_complete: false });
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).startsWith('/api/v1/command/task')) return response({ data: resumed
        ? { ...task, mentioned_count: 6, uncertain_count: 3 } : task }) as Response;
      if (++requests === 1) {
        return streamResponse([{ type: 'done', payload: { status: 'failed', reply: '已保留断点', tasks: [task] } }]);
      }
      return streamResponse([{ type: 'done', payload: { status: 'failed', reply: '16篇聚焦、6篇仅提及、3篇待核验。',
        steps: [{ name: 'invoke_api', ok: true, args: { method: 'POST', path: task.poll_path + '/resume' } }] } }]);
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '提交主旨聚焦导出';
    // The first task state is the pre-retry failure, as emitted by the server.
    await client.send(); await flush();
    expect(document.querySelector('.task-card')?.textContent).toContain('待核验 4 篇');
    resumed = true;
    (document.getElementById('input') as HTMLTextAreaElement).value = '继续原任务的待核验记录';
    await client.send(); await flush();
    expect(document.querySelector('.task-card')?.textContent).toContain('仅提及 6 篇');
    expect(document.querySelector('.task-card')?.textContent).toContain('待核验 3 篇');
    expect(document.querySelector('.task-card')?.textContent).not.toContain('待核验 4 篇');
    expect(client.snapshot().downloads[task.task_id].status).toBe('failed');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/resume'))).toHaveLength(0);
  });

  it('does not recheck historical failed tasks for ordinary replies and bounds focus checks to the visible dock', async () => {
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response
      : String(url).startsWith('/api/v1/command/task') ? response({ data: exportFixture({ status: 'failed',
          task_id: new URL(String(url), 'http://fixture').searchParams.get('task_id') }) }) as Response
      : streamResponse([{ type: 'done', payload: { status: 'completed', reply: '普通回答',
          steps: [{ name: 'web_search', ok: true, args: { query: '市场新闻' } }] } }]));
    const client = await install();
    for (let index = 0; index < 8; index++) client.registerDownloadTask(exportFixture({
      task_id: `essay-export-failed-${index}`, poll_path: `/api/v1/financial-data/essays/package/tasks/essay-export-failed-${index}` }));
    await flush();
    const polls = () => vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'));
    expect(polls()).toHaveLength(8);
    (document.getElementById('input') as HTMLTextAreaElement).value = '普通市场新闻问题';
    await client.send(); await flush();
    expect(polls()).toHaveLength(8);
    window.dispatchEvent(new Event('focus')); await flush();
    expect(polls()).toHaveLength(12);
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url) === exportListPath)).toHaveLength(1);
  });

  it('reconciles session task progress on a get_task_status event even when the final reply contains no task snapshot', async () => {
    let requests = 0, retried = false;
    const task = exportFixture({ status: 'failed', phase: 'screening', filters: { query_scope: 'focused' },
      candidate_total: 25, focused_count: 16, mentioned_count: 5, uncertain_count: 4,
      matched_total: null, target_total: null, focus_screening_complete: false });
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).startsWith('/api/v1/command/task')) return response({ data: retried
        ? { ...task, mentioned_count: 6, uncertain_count: 3 } : task }) as Response;
      return ++requests === 1 ? streamResponse([{ type: 'done', payload: { status: 'failed', tasks: [task], reply: '保留断点' } }])
        : streamResponse([{ type: 'tool_result', name: 'get_task_status', ok: true },
            { type: 'done', payload: { reply: '现为16篇聚焦，6篇提及，3篇待核验。', status: 'failed' } }]);
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '提交任务'; await client.send(); await flush();
    retried = true;
    (document.getElementById('input') as HTMLTextAreaElement).value = '核对这个任务状态'; await client.send(); await flush();
    expect(document.querySelector('.task-card')?.textContent).toContain('仅提及 6 篇');
    expect(document.querySelector('.task-card')?.textContent).toContain('待核验 3 篇');
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(2);
  });

  it('keeps attachment-only source records separate from uncertain, mentioned, or semantically read articles', async () => {
    const task = exportFixture({ status: 'packaging', phase: 'packaging', filters: { query_scope: 'focused' },
      candidate_total: 25, screened_count: 25, focused_count: 16, mentioned_count: 8, irrelevant_count: 0,
      uncertain_count: 0, source_only_count: 1, matched_total: 16, target_total: 16, retrieved_count: 25, focus_screening_complete: true });
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: task }) as Response);
    const client = await install(); client.registerDownloadTask({ task_id: task.task_id, poll_path: task.poll_path }); await flush();
    expect(client.snapshot().downloads[task.task_id].source_only_count).toBe(1);
    expect(document.getElementById('dockItems')?.textContent).toContain('仅源文件 1 条（附件正文未读取，未纳入文字聚焦包）');
    expect(document.getElementById('dockItems')?.textContent).toContain('检索候选 25 条');
    expect(document.getElementById('dockItems')?.textContent).toContain('已判定 25 条');
    expect(document.getElementById('dockItems')?.textContent).toContain('已读取候选 25 条');
    expect(document.getElementById('dockItems')?.textContent).not.toContain('已读取 25 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('待核验 0 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('仅提及 8 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('聚焦总数 16 篇');
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it('preserves all 25 candidate reads after the completed focused package narrows retrieval to 15 selected articles', async () => {
    const task = exportFixture({ status: 'completed', phase: 'completed', filters: { query_scope: 'focused' },
      candidate_total: 25, candidate_retrieved_count: 25, screened_count: 25, focused_count: 15,
      mentioned_count: 9, irrelevant_count: 0, uncertain_count: 0, source_only_count: 1,
      matched_total: 15, target_total: 15, retrieved_count: 15, packaged_count: 15, focus_screening_complete: true,
      result: { complete: true, filename: '公司主旨原文15篇.zip', download_url: exportFixture().poll_path + '/download' } });
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : { data: task }) as Response);
    const client = await install(); client.registerDownloadTask({ task_id: task.task_id, poll_path: task.poll_path }); await flush();
    expect(client.snapshot().downloads[task.task_id].candidate_retrieved_count).toBe(25);
    expect(document.getElementById('dockItems')?.textContent).toContain('已读取候选 25 条');
    expect(document.getElementById('dockItems')?.textContent).not.toContain('已读取候选 15 条');
    expect(document.getElementById('dockItems')?.textContent).toContain('聚焦总数 15 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('已整理 15 篇');
    expect(document.getElementById('dockItems')?.textContent).toContain('仅源文件 1 条（附件正文未读取，未纳入文字聚焦包）');
    expect(document.querySelector('a[href$="/download"]')).not.toBeNull();
  });

  it('keeps one document task with real counts and never activates a file link before complete verification', async () => {
    let complete = false;
    const task = { task_id:'acq-document-104-files', document_export:true,
      poll_path:'/api/v1/data-acquisition/tasks/acq-document-104-files', job_id:'documents-frozen-104',
      title:'宁德时代 · 全部公告', status:'running', progress:45,
      matched_total:104, total_files:104, completed_files:46, downloaded_bytes:234567,
      checkpoint:{files:46}, resumable:true,
      result:{complete:false, filename:'宁德时代公告.zip', download_url:'/api/v1/data-acquisition/jobs/documents-frozen-104/download'} };
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {
      data:complete ? {...task,status:'completed',progress:100,completed_files:104,result:{...task.result,complete:true}} : task,
    }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(Object.keys(client.snapshot().downloads)).toHaveLength(1);
    expect(document.getElementById('dockItems')?.textContent).toContain('一个需求 · 一个后台任务 · 最终一个资料包');
    expect(document.getElementById('dockItems')?.textContent).toContain('104');
    expect(document.getElementById('dockItems')?.textContent).not.toContain('研报包');
    expect(document.querySelector('a[download]')).toBeNull();
    complete = true; await vi.advanceTimersByTimeAsync(3000); await flush();
    // The dock and task ledger can link to the same artifact; never two jobs.
    expect(new Set([...document.querySelectorAll('a[download]')].map(link => link.getAttribute('href')))).toEqual(new Set([task.result.download_url]));
    expect(document.querySelector('a[download]')?.getAttribute('href')).toBe(task.result.download_url);
    expect(vi.mocked(fetch).mock.calls.every(([,init]) => init?.method !== 'POST')).toBe(true);
  });

  it('resumes the same document task only on click and refuses mismatched ready paths', async () => {
    let resumed = false;
    const task = {task_id:'acq-document-resume-id',document_export:true,
      poll_path:'/api/v1/data-acquisition/tasks/acq-document-resume-id',job_id:'fixed-doc-job',
      status:'failed',progress:75,resumable:true,completed_files:70,total_files:104,
      checkpoint:{files:70},result:{complete:true,download_url:'/wrong/private.zip'}};
    vi.mocked(fetch).mockImplementation(async (url,init) => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url) === task.poll_path+'/resume') {
        expect(init?.method).toBe('POST'); resumed = true; return response({...task,status:'queued'},202) as Response;
      }
      return response({data:resumed ? {...task,status:'running',result:null}:task}) as Response;
    });
    const client=await install(); client.registerDownloadTask(task); await flush();
    expect(document.querySelector('a[download]')).toBeNull();
    expect(vi.mocked(fetch).mock.calls.filter(([,init]) => init?.method === 'POST')).toHaveLength(0);
    (document.querySelector('[data-export-resume]') as HTMLButtonElement).click(); await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/resume'))).toHaveLength(1);
    expect(Object.keys(client.snapshot().downloads)).toEqual([task.task_id]);
    expect(client.snapshot().downloads[task.task_id].completed_files).toBe(70);
    expect(client.snapshot().downloads[task.task_id].status).toBe('running');
  });

  it('restores resumable document failures without trusting a saved download URL', async () => {
    const task = {task_id:'acq-document-saved-id',document_export:true,
      poll_path:'/api/v1/data-acquisition/tasks/acq-document-saved-id',status:'failed',resumable:true,
      checkpoint:{files:101},completed_files:101,total_files:104,url:'/old-unverified.zip'};
    localStorage.setItem('leziwu.downloads.v2:user:1',JSON.stringify({[task.task_id]:task}));
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {data:task}) as Response);
    const client = await install(); await flush();
    expect(document.querySelector('a[download]')).toBeNull();
    // Restoration is owner-scoped and may not POST a new job or resume by itself.
    expect(vi.mocked(fetch).mock.calls.every(([,init]) => init?.method !== 'POST')).toBe(true);
    expect(client.snapshot().downloads[task.task_id]?.checkpoint).toEqual({files:101});
  });

  it('discovers owned document jobs in the task ledger on a fresh device and derives session status from verified results', async () => {
    const task = {task_id:'acq-document-ledger-id',document_export:true,job_id:'acq-document-ledger-id',
      poll_path:'/api/v1/data-acquisition/tasks/acq-document-ledger-id',status:'completed',progress:100,
      title:'宁德时代 · 公告',total_files:104,completed_files:104,
      result:{complete:true,filename:'宁德时代_104份公告.zip',download_url:'/api/v1/data-acquisition/jobs/acq-document-ledger-id/download'}};
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {data:task}) as Response);
    const client = await install(); await flush();
    const delegate = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((url,init) => String(url) === '/api/v1/data-acquisition/tasks'
      ? Promise.resolve(response({items:[task],total:1}) as Response) : delegate(url,init));
    await client.refreshEssayExports(); await flush();
    expect(Object.keys(client.snapshot().downloads)).toEqual([task.task_id]);
    expect(client.exportLedgerHtml()).toContain('宁德时代_104份公告.zip');
    expect(client.exportLedgerHtml()).toContain('下载 ZIP');
    const session = {status:'submitted',messages:[{tasks:[task]}]};
    expect(client.sessionDisplayStatus(session)).toBe('done');
    Object.assign(client.snapshot().downloads[task.task_id], {status:'failed',resumable:true,url:''});
    expect(client.sessionDisplayStatus(session)).toBe('resumable');
    expect(vi.mocked(fetch).mock.calls.every(([,init]) => init?.method !== 'POST')).toBe(true);
  });

  it.each([
    { kind: 'industry project_id', id: 'industry-project-runtime', path: '/api/v1/industry-research/projects/industry-project-runtime',
      url: '/industry-research?project=industry-project-runtime',
      data: { project_id: 'industry-project-runtime', topic: '华懋科技公司调研', report: { long_form_report: '报告正文' } } },
    { kind: 'quant result_run_id', id: 'quant-task-runtime', path: '/api/v1/essay-quant/tasks/quant-task-runtime',
      url: '/essay-quant?section=results&run=73',
      data: { task_id: 'quant-task-runtime', name: '机构首次提及回测', result_run_id: 73 } },
    { kind: 'audio transcription', id: 'audio-analysis-runtime', path: '/api/v1/financial-data/research-notes/audio-analysis/tasks/audio-analysis-runtime',
      url: '/essay-radar/feed?tab=audio&task=audio-analysis-runtime',
      data: { task_id: 'audio-analysis-runtime', generate_memo: false, result: { title: '华懋科技录音逐字稿', transcript_only: true },
        download_urls: { zip: '/api/v1/financial-data/research-notes/audio-analysis/tasks/audio-analysis-runtime/download?format=zip' } } },
  ])('opens a verified $kind task result through the retained product page, not a fake ZIP', async fixture => {
    let verify!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { verify = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response
      : String(url).startsWith('/api/v1/command/task') ? pending : response({}) as Response);
    const client = await install();
    const receipt = { task_id: fixture.id, poll_path: fixture.path, title: fixture.kind,
      status: 'completed', result_url: fixture.url };
    client.registerDownloadTask(receipt); await flush();
    // A streamed/saved receipt is a hint, not server readback verification.
    expect(document.querySelector('#dockItems a')).toBeNull();
    expect(client.snapshot().downloads[fixture.id].verified).toBe(false);
    verify(response({ ok: true, data: { ...fixture.data, status: 'completed', progress: 100 }, task: receipt }) as Response);
    await flush();
    const link = document.querySelector('#dockItems a') as HTMLAnchorElement;
    expect(link?.getAttribute('href')).toBe(fixture.url);
    expect(link?.textContent).toBe('查看结果');
    expect(link?.hasAttribute('download')).toBe(false);
    expect(document.querySelector('a[download]')).toBeNull();
    expect(client.snapshot().downloads[fixture.id].status).toBe('completed');
    const polls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'));
    expect(new URL(String(polls[0][0]), 'https://fixture').searchParams.get('poll_path')).toBe(fixture.path);
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
  });

  it('settles a streamed limited research report as partial and stops polling rather than staying submitted', async () => {
    const task = { task_id: 'industry-limited-runtime', poll_path: '/api/v1/industry-research/projects/industry-limited-runtime',
      title: '华懋科技受限报告', status: 'queued', result_url: '/industry-research?project=industry-limited-runtime' };
    vi.mocked(fetch).mockImplementation(async url => {
      if (url === '/api/v1/user-auth/status') return response(status()) as Response;
      if (String(url).startsWith('/api/v1/command/task')) return response({ ok: true,
        data: { project_id: task.task_id, status: 'limited', progress: 100, message: '报告已生成但官方证据不足', report: { quality_assurance: { status: 'limited' } } },
        task: { ...task, status: 'limited' } }) as Response;
      return streamResponse([{ type: 'task', task }, { type: 'done', payload: { reply: '已提交公司调研', status: 'submitted', tasks: [task] } }]);
    });
    const client = await install();
    (document.getElementById('input') as HTMLTextAreaElement).value = '研究华懋科技并保留证据不足事项';
    await client.send(); await flush();
    const session = client.snapshot().sessions[0];
    expect(client.snapshot().downloads[task.task_id].status).toBe('limited');
    expect(client.sessionDisplayStatus(session)).toBe('partial');
    expect(document.querySelector('.session-item')?.textContent).not.toContain('后台执行');
    expect(document.querySelector('#dockItems')?.textContent).toContain('受限');
    expect(document.querySelector('#dockItems')?.textContent).not.toContain('查看结果');
    expect(document.querySelector('a[download]')).toBeNull();
    const count = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task')).length;
    await vi.advanceTimersByTimeAsync(15000); await flush();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(count);
  });

  it('shows a verified failed task diagnosis without claiming a completed result or download', async () => {
    const task = { task_id: 'industry-failed-runtime', poll_path: '/api/v1/industry-research/projects/industry-failed-runtime',
      status: 'failed', result_url: '/industry-research?project=industry-failed-runtime', title: '待检查课题' };
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {
      ok: true, data: { project_id: task.task_id, status: 'failed', progress: 100, error: '上游材料不足，未生成完整报告' }, task,
    }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(document.querySelector('#dockItems')?.textContent).toContain('查看任务');
    expect(document.querySelector('#dockItems')?.textContent).not.toContain('查看结果');
    expect(document.querySelector('a[download]')).toBeNull();
    expect(client.sessionDisplayStatus({ status: 'submitted', messages: [{ tasks: [task] }] })).toBe('failed');
  });

  it('rejects a result URL attached to another task receipt even when that URL looks valid', async () => {
    const task = { task_id: 'quant-bound-task', poll_path: '/api/v1/essay-quant/tasks/quant-bound-task', status: 'queued' };
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : {
      ok: true, data: { task_id: task.task_id, status: 'completed', progress: 100, result_run_id: 73 },
      task: { task_id: 'quant-different-task', poll_path: '/api/v1/essay-quant/tasks/quant-different-task', result_url: '/essay-quant?section=results&run=73' },
    }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(client.snapshot().downloads[task.task_id].result_url).toBe('');
    expect(document.querySelector('#dockItems a')).toBeNull();
    expect(document.querySelector('a[download]')).toBeNull();
  });

  it('restores an owned generic task by GET, discards its saved result URL, and verifies the same task before linking', async () => {
    const task = { task_id: 'quant-saved-runtime', poll_path: '/api/v1/essay-quant/tasks/quant-saved-runtime',
      status: 'completed', progress: 100, result_url: '/essay-quant?section=results&run=999', verified: true };
    localStorage.setItem('leziwu.downloads.v2:user:1', JSON.stringify({ [task.task_id]: task }));
    let verify!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { verify = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : pending);
    const client = await install(); await flush();
    expect(client.snapshot().downloads[task.task_id].result_url).toBe('');
    expect(document.querySelector('#dockItems a')).toBeNull();
    verify(response({ ok: true, data: { task_id: task.task_id, status: 'completed', progress: 100, result_run_id: 73 },
      task: { ...task, result_url: '/essay-quant?section=results&run=73' } }) as Response);
    await flush();
    expect(document.querySelector('#dockItems a')?.getAttribute('href')).toBe('/essay-quant?section=results&run=73');
    expect(document.body.textContent).not.toContain('run=999');
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it('syncs a polled generic result into its original session without displaying it in another open session', async () => {
    const task = { task_id: 'industry-original-session', poll_path: '/api/v1/industry-research/projects/industry-original-session',
      title: '原会话公司研究', status: 'queued', result_url: '/industry-research?project=industry-original-session' };
    let verify!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { verify = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response
      : String(url).startsWith('/api/v1/command/task') ? pending
        : streamResponse([{ type: 'done', payload: { reply: '后台调研已提交', status: 'submitted', tasks: [task] } }]));
    const client = await install();
    const original = client.snapshot().activeId;
    (document.getElementById('input') as HTMLTextAreaElement).value = '开始公司调研';
    await client.send(); await flush();
    const second = client.createSession('另一个会话').id; client.switchSession(second);
    verify(response({ ok: true, data: { project_id: task.task_id, status: 'completed', progress: 100, report: { one_sentence: '研究完成' } },
      task: { ...task, status: 'completed' } }) as Response);
    await flush();
    const previous = client.snapshot().sessions.find(session => session.id === original)!;
    expect(previous.messages.at(-1)?.tasks?.[0].status).toBe('completed');
    expect(client.sessionDisplayStatus(previous)).toBe('done');
    expect(client.snapshot().sessions.find(session => session.id === second)?.messages).toEqual([]);
    expect(document.getElementById('messages')?.textContent).not.toContain('原会话公司研究');
    client.switchSession(original);
    expect(document.querySelector('.task-card a')?.getAttribute('href')).toBe(task.result_url);
  });

  it('drops a late generic-task readback after the authenticated owner changes', async () => {
    const task = { task_id: 'industry-private-owner-one', poll_path: '/api/v1/industry-research/projects/industry-private-owner-one', status: 'queued' };
    let verify!: (value: Response) => void;
    const pending = new Promise<Response>(resolve => { verify = resolve; });
    vi.mocked(fetch).mockImplementation(async url => url === '/api/v1/user-auth/status' ? response(status()) as Response : pending);
    const client = await install(); client.registerDownloadTask(task); await flush();
    userId = 2; await client.refreshOwner();
    verify(response({ ok: true, data: { project_id: task.task_id, status: 'completed', progress: 100, topic: 'PRIVATE COMPANY REPORT' },
      task: { ...task, result_url: '/industry-research?project=industry-private-owner-one' } }) as Response);
    await flush();
    expect(client.snapshot().owner).toBe('user:2');
    expect(client.snapshot().downloads).toEqual({});
    expect(document.querySelector('#dockItems a')).toBeNull();
    expect(document.body.textContent).not.toContain('PRIVATE COMPANY REPORT');
    expect(localStorage.getItem('leziwu.downloads.v2:user:2')).not.toContain('industry-private-owner-one');
  });

  it.each([401, 403])('removes a previously verified generic result entrance when readback becomes %s', async code => {
    let rejected = false;
    const task = { task_id: 'industry-expired-access', poll_path: '/api/v1/industry-research/projects/industry-expired-access',
      status: 'completed', result_url: '/industry-research?project=industry-expired-access' };
    vi.mocked(fetch).mockImplementation(async url => response(url === '/api/v1/user-auth/status' ? status() : rejected
      ? { ok: false, status: code, data: { detail: '当前账号无权读取' } }
      : { ok: true, data: { project_id: task.task_id, status: 'completed', progress: 100 }, task }) as Response);
    const client = await install(); client.registerDownloadTask(task); await flush();
    expect(document.querySelector('#dockItems a')?.getAttribute('href')).toBe(task.result_url);
    rejected = true; client.registerDownloadTask(task); await flush();
    expect(document.querySelector('#dockItems a')).toBeNull();
    expect(document.querySelector('a[download]')).toBeNull();
    const polls = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task')).length;
    await vi.advanceTimersByTimeAsync(9000);
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith('/api/v1/command/task'))).toHaveLength(polls);
  });
});
