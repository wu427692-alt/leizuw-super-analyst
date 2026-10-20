import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import source from '../../../../ai-news-nav.js?raw';

type BridgeWindow = Window & { __leziwuAiNewsNavBridge?: { disconnect: () => void } };
const bridgeWindow = window as BridgeWindow;
let frames: FrameRequestCallback[] = [];

function install() { new Function(source)(); }

async function flush() {
  // A DOM update schedules the next frame; the bridge's own idempotent update
  // can schedule one more. Bound the loop to expose self-observation failures.
  for (let step = 0; step < 5; step += 1) {
    await Promise.resolve();
    const pending = frames;
    frames = [];
    pending.forEach((callback) => callback(0));
  }
  expect(frames).toHaveLength(0);
}

function markup(label = '主导航', active = false, collapsed = false) {
  return `<nav aria-label="${label}">
    <a href="/app" class="nav-item border-transparent text-secondary-text hover:bg-neutral"><svg class="h-5 w-5 shrink-0 text-current"></svg><span class="truncate">首页</span></a>
    <a href="/essay-radar" class="nav-item ${active ? 'active is-active font-medium border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] text-[hsl(var(--primary))]' : 'border-transparent text-secondary-text hover:bg-neutral'}" ${active ? 'aria-current="page" data-active="true"' : ''}>
      <svg class="h-5 w-5 shrink-0 ${active ? 'text-[var(--nav-icon-active)]' : 'text-current'}"></svg>
      ${collapsed ? '' : '<span class="truncate">机构段子与录音</span>'}
    </a>
  </nav>`;
}

beforeEach(() => {
  frames = [];
  document.body.innerHTML = '';
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { frames.push(callback); return 1; });
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => { frames = []; });
});

afterEach(() => {
  bridgeWindow.__leziwuAiNewsNavBridge?.disconnect();
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('AI news frozen-SPA navigation bridge', () => {
  it('adds one same-origin native link and an independent newspaper icon', async () => {
    document.body.innerHTML = markup();
    install();
    await flush();
    const link = document.querySelector<HTMLAnchorElement>('a[href="/ai-news"]')!;
    expect(link).not.toBeNull();
    expect(link.getAttribute('href')).toBe('/ai-news');
    expect(link.textContent).toBe('AI 热点');
    expect(link.getAttribute('aria-label')).toBe('AI 热点');
    expect(link.querySelectorAll('svg path')).toHaveLength(3);
    expect(link.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(link.previousElementSibling).toHaveAttribute('href', '/essay-radar');
    const event = new Event('click', { bubbles: true, cancelable: true });
    const parentClick = vi.fn();
    link.parentElement!.addEventListener('click', parentClick);
    // An ancestor's delegated SPA handler is not called; default remains native.
    link.dispatchEvent(event);
    expect(parentClick).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it('deduplicates repeated script loads and observer refreshes', async () => {
    document.body.innerHTML = markup();
    install();
    await flush();
    const firstBridge = bridgeWindow.__leziwuAiNewsNavBridge;
    install();
    document.querySelector('nav')!.className = 'updated';
    await flush();
    expect(bridgeWindow.__leziwuAiNewsNavBridge).toBe(firstBridge);
    expect(document.querySelectorAll('a[href="/ai-news"]')).toHaveLength(1);
  });

  it('supports sidebar and mobile drawer but never adds to the mobile bottom bar', async () => {
    document.body.innerHTML = markup() + markup('Main navigation') + markup('手机主导航');
    install();
    await flush();
    expect(document.querySelectorAll('a[href="/ai-news"]')).toHaveLength(2);
    expect(document.querySelector('nav[aria-label="Main navigation"] a[href="/ai-news"]')).toHaveTextContent('AI News');
    expect(document.querySelector('nav[aria-label="手机主导航"] a[href="/ai-news"]')).toBeNull();
  });

  it('restores its link after React replaces or remounts the navigation', async () => {
    document.body.innerHTML = `<aside>${markup()}</aside>`;
    install();
    await flush();
    document.querySelector('aside')!.innerHTML = markup();
    await flush();
    expect(document.querySelectorAll('a[href="/ai-news"]')).toHaveLength(1);
    document.querySelector('a[href="/ai-news"]')!.remove();
    await flush();
    expect(document.querySelectorAll('a[href="/ai-news"]')).toHaveLength(1);
  });

  it('does not copy selected-state attributes or classes from an active essay link', async () => {
    document.body.innerHTML = markup('主导航', true);
    install();
    await flush();
    const link = document.querySelector('a[href="/ai-news"]')!;
    expect(link).not.toHaveAttribute('aria-current');
    expect(link).not.toHaveAttribute('data-active');
    expect(link.className).toBe('nav-item border-transparent text-secondary-text hover:bg-neutral');
    expect(link.className).not.toContain('active');
    expect(link.querySelector('svg')!.getAttribute('class')).not.toContain('nav-icon-active');
    expect(document.querySelector('a[href="/essay-radar"]')).toHaveAttribute('aria-current', 'page');
  });

  it('removes known active classes even if only the selected source link is mounted', async () => {
    document.body.innerHTML = markup('主导航', true);
    document.querySelector('a[href="/app"]')!.remove();
    install();
    await flush();
    const link = document.querySelector('a[href="/ai-news"]')!;
    expect(link.className).not.toMatch(/active|font-medium|--primary/);
    expect(link.querySelector('svg')!.getAttribute('class')).toContain('text-current');
  });

  it('tracks language changes and compact navigation without creating labels in a collapsed rail', async () => {
    document.body.innerHTML = markup('主导航', false, true);
    install();
    await flush();
    let link = document.querySelector('a[href="/ai-news"]')!;
    expect(link.querySelector('span')).toBeNull();
    document.querySelector('nav')!.setAttribute('aria-label', 'Main navigation');
    await flush();
    link = document.querySelector('a[href="/ai-news"]')!;
    expect(link).toHaveAttribute('aria-label', 'AI News');
    expect(link.querySelector('span')).toBeNull();
    const label = document.createElement('span');
    label.className = 'truncate';
    label.textContent = 'Institutional notes';
    document.querySelector('a[href="/essay-radar"]')!.appendChild(label);
    await flush();
    expect(link).toHaveTextContent('AI News');
  });

  it('leaves native future-SPA links untouched and removes its obsolete bridge link', async () => {
    document.body.innerHTML = markup();
    install();
    await flush();
    const native = document.createElement('a');
    native.href = '/ai-news';
    native.textContent = 'Native news route';
    document.querySelector('nav')!.appendChild(native);
    await flush();
    expect(document.querySelectorAll('a[href="/ai-news"]')).toHaveLength(1);
    expect(document.querySelector('a[href="/ai-news"]')).toBe(native);
    expect(native).not.toHaveAttribute('data-leziwu-ai-news-nav');
  });

  it('coalesces a mutation burst into one frame and does no network or storage work', async () => {
    const fetch = vi.spyOn(window, 'fetch');
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    document.body.innerHTML = markup();
    install();
    const nav = document.querySelector('nav')!;
    for (let index = 0; index < 10; index += 1) nav.className = `changed-${index}`;
    await Promise.resolve();
    expect(frames).toHaveLength(1);
    await flush();
    expect(fetch).not.toHaveBeenCalled();
    expect(storage).not.toHaveBeenCalled();
  });
});
