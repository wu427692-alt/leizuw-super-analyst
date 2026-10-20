import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

const script = readFileSync(resolve(process.cwd(), 'public/assets/account-access-navigation.js'), 'utf8');
function fixture(pathname = '/app', root: { querySelector: (selector: string) => unknown } | null = null) {
  const tasks: Array<() => void> = [];
  const callbacks: Record<string, () => void> = {};
  const location = { pathname, search: '?bind=phone', hash: '', replace: vi.fn() };
  const history = { pushState: vi.fn(), replaceState: vi.fn() };
  const window = { location, history, addEventListener: (event: string, callback: () => void) => { callbacks[event] = callback; } };
  const disconnect = vi.fn();
  const observe = vi.fn();
  const document = { readyState: 'complete', getElementById: () => root };
  class Observer { observe = observe; disconnect = disconnect; }
  runInNewContext(script, { window, document, MutationObserver: Observer, queueMicrotask: (callback: () => void) => tasks.push(callback) });
  return { window, tasks, callbacks, disconnect, observe };
}
describe('shared account document navigation', () => {
  it('moves only the access route from old SPA navigation to a real document', () => {
    const { window, tasks } = fixture();
    window.location.pathname = '/access';
    window.history.pushState();
    tasks.forEach(task => task());
    expect(window.location.replace).toHaveBeenCalledExactlyOnceWith('/access?bind=phone');
  });
  it('does not reload a document that is already the access entry', () => {
    const { window, tasks } = fixture('/access');
    window.history.replaceState();
    tasks.forEach(task => task());
    expect(window.location.replace).not.toHaveBeenCalled();
  });
  it('does not intercept research, admin or news routes', () => {
    const { window, tasks } = fixture();
    for (const route of ['/super-watchlist', '/admin', '/ai-news']) {
      window.location.pathname = route;
      window.history.pushState();
      tasks.splice(0).forEach(task => task());
    }
    expect(window.location.replace).not.toHaveBeenCalled();
  });
  it('handles back navigation once without modifying the history payload', () => {
    const { window, callbacks } = fixture();
    window.location.pathname = '/access';
    callbacks.popstate(); callbacks.popstate();
    expect(window.location.replace).toHaveBeenCalledTimes(1);
  });
  it('neutralizes only the frozen landing registration copy and stops observing after leaving home', () => {
    const copy = { textContent: '姓名与密码即可提交注册申请，审核通过后使用个人研究空间。' };
    const label = { nodeType: 3, nodeValue: '注册申请' };
    const root = { querySelector: (selector: string) => selector === '.landing-final p' ? copy : { firstChild: label } };
    const { window, tasks, observe, disconnect } = fixture('/', root);
    expect(copy.textContent).toBe('登录或注册，开启你的个人研究空间。');
    expect(label.nodeValue).toBe('注册账号');
    expect(observe).toHaveBeenCalledTimes(1);
    window.location.pathname = '/ai-news';
    window.history.pushState();
    tasks.forEach(task => task());
    expect(disconnect).toHaveBeenCalledTimes(1);
  });
  it('does not replace unrelated landing text', () => {
    const copy = { textContent: 'A custom introduction.' };
    const root = { querySelector: (selector: string) => selector === '.landing-final p' ? copy : null };
    fixture('/', root);
    expect(copy.textContent).toBe('A custom introduction.');
  });
});
