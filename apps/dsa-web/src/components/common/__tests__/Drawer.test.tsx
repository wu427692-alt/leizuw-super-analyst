import { StrictMode, useState, type ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { Drawer } from '../Drawer';

function provide(children: ReactNode) {
  return <UiLanguageProvider>{children}</UiLanguageProvider>;
}

function Example({ children, title = '资料窗口', onClosed }: {
  children?: ReactNode; title?: string; onClosed?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <>
    <button onClick={() => setOpen(true)}>打开资料</button>
    <button>页面其他操作</button>
    <Drawer isOpen={open} title={title} onClose={() => { onClosed?.(); setOpen(false); }}>
      {children}
    </Drawer>
  </>;
}

function Stacked({ firstZ = 50, secondZ = 50, showFirst = true, hideSecondTrigger = false,
  onFirstClose = () => {}, onSecondClose = () => {} }: {
  firstZ?: number; secondZ?: number; showFirst?: boolean; hideSecondTrigger?: boolean;
  onFirstClose?: () => void; onSecondClose?: () => void;
}) {
  const [first, setFirst] = useState(false);
  const [second, setSecond] = useState(false);
  return <>
    <button onClick={() => setFirst(true)}>打开第一层</button>
    <Drawer isOpen={first && showFirst} title="第一层" zIndex={firstZ}
      onClose={() => { onFirstClose(); setFirst(false); }}>
      <button hidden={hideSecondTrigger} onClick={() => setSecond(true)}>打开第二层</button>
      <a href="https://example.test/first">第一层原文</a>
    </Drawer>
    <Drawer isOpen={second} title="第二层" zIndex={secondZ}
      onClose={() => { onSecondClose(); setSecond(false); }}>
      <a href="https://example.test/second">第二层原文</a>
    </Drawer>
  </>;
}

function open(name: string) {
  const trigger = screen.getByRole('button', { name });
  trigger.focus(); // fireEvent.click deliberately does not emulate browser focus.
  fireEvent.click(trigger);
  return trigger;
}

function closeButton(name: string) {
  return within(screen.getByRole('dialog', { name })).getByRole('button', { name: /^(关闭抽屉|Close drawer)$/ });
}

describe('Drawer', () => {
  afterEach(() => {
    cleanup();
    document.querySelectorAll('[data-external-trigger]').forEach(node => node.remove());
    document.body.style.overflow = '';
    vi.restoreAllMocks();
  });

  it('portals the fixed panel to document.body instead of a transformed page container', () => {
    const { container } = render(
      <UiLanguageProvider>
        <div data-testid="page-container" style={{ transform: 'translateZ(0)', overflow: 'hidden' }}>
          <Drawer isOpen onClose={vi.fn()} title="原文窗口">
            <p>完整原文内容</p>
          </Drawer>
        </div>
      </UiLanguageProvider>,
    );

    const dialog = screen.getByRole('dialog', { name: '原文窗口' });
    expect(dialog).toHaveTextContent('完整原文内容');
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
    expect(document.body).toContainElement(dialog);
    expect(dialog).toHaveClass('h-full', 'min-h-0');
  });

  it('focuses close on open, closes with Escape and returns focus to its trigger', () => {
    const onClosed = vi.fn();
    render(provide(<Example onClosed={onClosed}><p>资料内容</p></Example>));
    const trigger = open('打开资料');
    expect(closeButton('资料窗口')).toHaveFocus();
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(onClosed).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('cycles Tab and Shift+Tab within the current visible enabled controls', () => {
    render(provide(<Example>
      <input aria-label="资料检索" />
      <a href="https://example.test/report.pdf">原始 PDF</a>
      <button disabled>禁用操作</button>
      <button hidden>隐藏操作</button>
      <button style={{ display: 'none' }}>样式隐藏操作</button>
      <div inert><button>背景操作</button></div>
      <fieldset disabled><button>禁用字段操作</button></fieldset>
      <details><summary>辅助字段</summary><button>折叠内容操作</button></details>
    </Example>));
    open('打开资料');
    const first = closeButton('资料窗口');
    const last = screen.getByText('辅助字段');
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(first).toHaveFocus();
    screen.getByRole('button', { name: '页面其他操作' }).focus();
    expect(first).toHaveFocus();
  });

  it('keeps focus on the only control instead of allowing Tab into the page', () => {
    render(provide(<Example><p>没有其他可交互控件</p></Example>));
    open('打开资料');
    const close = closeButton('资料窗口');
    fireEvent.keyDown(close, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(close).toHaveFocus();
  });

  it('uses the dialog fallback when no enabled tab stop remains', () => {
    render(provide(<Example><p>资料内容</p></Example>));
    open('打开资料');
    const close = closeButton('资料窗口');
    close.setAttribute('disabled', '');
    fireEvent.keyDown(close, { key: 'Tab' });
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('queries newly loaded controls without resetting focus when title or callback changes', () => {
    const previous = vi.fn();
    const latest = vi.fn();
    const view = render(provide(<Example onClosed={previous}><input aria-label="资料检索" /></Example>));
    const trigger = open('打开资料');
    const input = screen.getByRole('textbox', { name: '资料检索' });
    input.focus();
    view.rerender(provide(<Example title="完整资料窗口" onClosed={latest}>
      <input aria-label="资料检索" /><a href="https://example.test/new.pdf">新取得的 PDF</a>
    </Example>));
    expect(input).toHaveFocus();
    const last = screen.getByRole('link', { name: '新取得的 PDF' });
    last.focus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(closeButton('完整资料窗口')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(previous).not.toHaveBeenCalled();
    expect(latest).toHaveBeenCalledTimes(1);
    expect(trigger).toHaveFocus();
  });

  it('closes only the top same-layer Drawer and restores the nested trigger, then the page trigger', () => {
    const onFirstClose = vi.fn();
    const onSecondClose = vi.fn();
    render(provide(<Stacked onFirstClose={onFirstClose} onSecondClose={onSecondClose} />));
    const rootTrigger = open('打开第一层');
    const nestedTrigger = open('打开第二层');
    expect(closeButton('第二层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onSecondClose).toHaveBeenCalledTimes(1);
    expect(onFirstClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: '第二层' })).not.toBeInTheDocument();
    expect(nestedTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onFirstClose).toHaveBeenCalledTimes(1);
    expect(rootTrigger).toHaveFocus();
  });

  it('keeps a visually higher Drawer on top even when a lower z-index Drawer opens later', () => {
    const onFirstClose = vi.fn();
    const onSecondClose = vi.fn();
    render(provide(<Stacked firstZ={100} secondZ={50} onFirstClose={onFirstClose} onSecondClose={onSecondClose} />));
    const rootTrigger = open('打开第一层');
    const nestedTrigger = open('打开第二层');
    expect(nestedTrigger).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onFirstClose).toHaveBeenCalledTimes(1);
    expect(onSecondClose).not.toHaveBeenCalled();
    expect(closeButton('第二层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onSecondClose).toHaveBeenCalledTimes(1);
    expect(rootTrigger).toHaveFocus();
  });

  it('preserves trigger history when a lower Drawer changes z-index while another remains above it', () => {
    const view = render(provide(<Stacked firstZ={50} secondZ={80} />));
    const rootTrigger = open('打开第一层');
    const nestedTrigger = open('打开第二层');
    view.rerender(provide(<Stacked firstZ={60} secondZ={80} />));
    expect(closeButton('第二层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(nestedTrigger).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(rootTrigger).toHaveFocus();
  });

  it('uses DOM paint order when a z-index change brings two Drawers onto the same layer', () => {
    const onFirstClose = vi.fn();
    const onSecondClose = vi.fn();
    const view = render(provide(<Stacked firstZ={100} secondZ={50} onFirstClose={onFirstClose} onSecondClose={onSecondClose} />));
    open('打开第一层');
    open('打开第二层');
    view.rerender(provide(<Stacked firstZ={50} secondZ={50} onFirstClose={onFirstClose} onSecondClose={onSecondClose} />));
    expect(closeButton('第二层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onSecondClose).toHaveBeenCalledTimes(1);
    expect(onFirstClose).not.toHaveBeenCalled();
  });

  it('returns to the surviving Drawer when its former trigger becomes hidden', () => {
    const view = render(provide(<Stacked />));
    const rootTrigger = open('打开第一层');
    open('打开第二层');
    view.rerender(provide(<Stacked hideSecondTrigger />));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(closeButton('第一层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(rootTrigger).toHaveFocus();
  });

  it('does not return into a removed underlying Drawer', () => {
    const view = render(provide(<Stacked />));
    const rootTrigger = open('打开第一层');
    open('打开第二层');
    view.rerender(provide(<Stacked showFirst={false} />));
    expect(closeButton('第二层')).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(rootTrigger).toHaveFocus();
  });

  it('assigns a unique accessible title ID to every simultaneous Drawer on the same side', () => {
    render(provide(<><Drawer isOpen title="左侧资料" onClose={vi.fn()}><p>一</p></Drawer>
      <Drawer isOpen title="右侧资料" onClose={vi.fn()}><p>二</p></Drawer></>));
    const first = screen.getByRole('dialog', { name: '左侧资料' });
    const second = screen.getByRole('dialog', { name: '右侧资料' });
    const firstId = first.getAttribute('aria-labelledby');
    const secondId = second.getAttribute('aria-labelledby');
    expect(firstId).toBeTruthy();
    expect(secondId).toBeTruthy();
    expect(firstId).not.toBe(secondId);
    expect(document.getElementById(firstId!)).toHaveTextContent('左侧资料');
    expect(document.getElementById(secondId!)).toHaveTextContent('右侧资料');
  });

  it('does not leave duplicate listeners or overwrite prior scroll state after StrictMode replay', () => {
    const trigger = document.createElement('button');
    trigger.dataset.externalTrigger = 'true';
    trigger.textContent = '外部触发按钮';
    document.body.append(trigger);
    trigger.focus();
    document.body.style.overflow = 'clip';
    const close = vi.fn();
    const view = render(<StrictMode>{provide(<Drawer isOpen title="严格模式资料" onClose={close}><p>内容</p></Drawer>)}</StrictMode>);
    expect(closeButton('严格模式资料')).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('clip');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it('supports nested Drawers in StrictMode without trapping focus after both close', () => {
    render(<StrictMode>{provide(<Stacked />)}</StrictMode>);
    const trigger = open('打开第一层');
    open('打开第二层');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('button', { name: '打开第二层' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(trigger).toHaveFocus();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe('');
  });
});
