import { StrictMode, useState, type ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { ConfirmDialog } from '../ConfirmDialog';
import { Drawer } from '../Drawer';

function provide(children: ReactNode) {
  return <UiLanguageProvider>{children}</UiLanguageProvider>;
}

function ModalPair({ drawerZ = 50, showDrawer = true, hideTrigger = false,
  cancelDisabled = false, confirmDisabled = false, title = '确认更改',
  onDrawerClose = () => {}, onCancel = () => {}, onConfirm = () => {} }: {
  drawerZ?: number; showDrawer?: boolean; hideTrigger?: boolean;
  cancelDisabled?: boolean; confirmDisabled?: boolean; title?: string;
  onDrawerClose?: () => void; onCancel?: () => void; onConfirm?: () => void;
}) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  return <>
    <button onClick={() => setDrawerOpen(true)}>打开资料</button>
    <button>页面其他操作</button>
    <Drawer isOpen={drawerOpen && showDrawer} title="资料窗口" zIndex={drawerZ}
      onClose={() => { onDrawerClose(); setDrawerOpen(false); }}>
      <button hidden={hideTrigger} onClick={() => setConfirmOpen(true)}>请求更改</button>
      <input aria-label="资料检索" />
    </Drawer>
    <ConfirmDialog isOpen={confirmOpen} title={title} message="更改此条记录吗？"
      cancelText="取消更改" confirmText="确定更改"
      cancelDisabled={cancelDisabled} confirmDisabled={confirmDisabled}
      onCancel={() => { onCancel(); setConfirmOpen(false); }}
      onConfirm={() => { onConfirm(); setConfirmOpen(false); }} />
  </>;
}

function open(name: string) {
  const trigger = screen.getByRole('button', { name });
  trigger.focus(); // fireEvent does not emulate native pointer focus.
  fireEvent.click(trigger);
  return trigger;
}

function drawerClose() {
  return within(screen.getByRole('dialog', { name: '资料窗口' }))
    .getByRole('button', { name: /^(关闭抽屉|Close drawer)$/ });
}

function escape() {
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
}

describe('ConfirmDialog shared modal focus', () => {
  afterEach(() => {
    cleanup();
    document.querySelectorAll('[data-external-confirm-trigger]').forEach(node => node.remove());
    document.body.style.overflow = '';
    vi.restoreAllMocks();
  });

  it('focuses the top confirmation, contains Tab and restores both trigger levels in order', () => {
    const onCancel = vi.fn();
    const onDrawerClose = vi.fn();
    render(provide(<ModalPair onCancel={onCancel} onDrawerClose={onDrawerClose} />));
    const rootTrigger = open('打开资料');
    const nestedTrigger = open('请求更改');
    const cancel = screen.getByRole('button', { name: '取消更改' });
    const confirm = screen.getByRole('button', { name: '确定更改' });
    expect(cancel).toHaveFocus();
    fireEvent.keyDown(cancel, { key: 'Tab', shiftKey: true });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(confirm, { key: 'Tab' });
    expect(cancel).toHaveFocus();
    screen.getByRole('textbox', { name: '资料检索' }).focus();
    expect(cancel).toHaveFocus();
    screen.getByRole('button', { name: '页面其他操作' }).focus();
    expect(cancel).toHaveFocus();
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(nestedTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    escape();
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(rootTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('');
  });

  it('selects the enabled confirmation when the preferred cancel button is disabled', () => {
    const onCancel = vi.fn();
    const onDrawerClose = vi.fn();
    render(provide(<ModalPair cancelDisabled onCancel={onCancel} onDrawerClose={onDrawerClose} />));
    open('打开资料');
    open('请求更改');
    const confirm = screen.getByRole('button', { name: '确定更改' });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(confirm, { key: 'Tab' });
    expect(confirm).toHaveFocus();
    fireEvent.keyDown(confirm, { key: 'Tab', shiftKey: true });
    expect(confirm).toHaveFocus();
    escape();
    expect(onCancel).not.toHaveBeenCalled();
    expect(onDrawerClose).not.toHaveBeenCalled();
  });

  it('uses the confirmation container when busy and never dismisses it or the underlying Drawer', () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const onDrawerClose = vi.fn();
    render(provide(<ModalPair cancelDisabled confirmDisabled
      onCancel={onCancel} onConfirm={onConfirm} onDrawerClose={onDrawerClose} />));
    open('打开资料');
    open('请求更改');
    const dialog = screen.getByRole('alertdialog', { name: '确认更改' });
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(dialog).toHaveFocus();
    screen.getByRole('button', { name: '页面其他操作' }).focus();
    expect(dialog).toHaveFocus();
    escape();
    fireEvent.click(dialog.parentElement!);
    fireEvent.click(screen.getByRole('button', { name: '取消更改' }));
    fireEvent.click(screen.getByRole('button', { name: '确定更改' }));
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });

  it('honors updated busy state and callback without resetting the original return target', () => {
    const oldCancel = vi.fn();
    const latestCancel = vi.fn();
    const view = render(provide(<ModalPair onCancel={oldCancel} />));
    const rootTrigger = open('打开资料');
    const nestedTrigger = open('请求更改');
    view.rerender(provide(<ModalPair cancelDisabled confirmDisabled onCancel={latestCancel} />));
    expect(screen.getByRole('alertdialog')).toHaveFocus();
    escape();
    expect(latestCancel).not.toHaveBeenCalled();
    view.rerender(provide(<ModalPair title="确认最新更改" onCancel={latestCancel} />));
    const cancel = screen.getByRole('button', { name: '取消更改' });
    cancel.focus();
    view.rerender(provide(<ModalPair title="确认最新更改" onCancel={latestCancel} />));
    expect(cancel).toHaveFocus();
    escape();
    expect(oldCancel).not.toHaveBeenCalled();
    expect(latestCancel).toHaveBeenCalledTimes(1);
    expect(nestedTrigger).toHaveFocus();
    escape();
    expect(rootTrigger).toHaveFocus();
  });

  it.each([false, true])('recovers focus when only confirmDisabled changes with stable callbacks (body focus: %s)', (blurToBody) => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const renderConfirm = (confirmDisabled: boolean) => provide(<ConfirmDialog isOpen title="正在保存"
      message="请等待操作结束" cancelText="取消更改" confirmText="确定更改"
      cancelDisabled confirmDisabled={confirmDisabled} onCancel={onCancel} onConfirm={onConfirm} />);
    const view = render(renderConfirm(false));
    const confirm = screen.getByRole('button', { name: '确定更改' });
    expect(confirm).toHaveFocus();
    if (blurToBody) {
      confirm.blur();
      expect(document.body).toHaveFocus();
    }
    view.rerender(renderConfirm(true));
    expect(screen.getByRole('alertdialog')).toHaveFocus();
    escape();
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('preserves default layers and switches top focus by DOM order when the Drawer is lowered to the same layer', () => {
    const onDrawerClose = vi.fn();
    const onCancel = vi.fn();
    const view = render(provide(<ModalPair drawerZ={100} onDrawerClose={onDrawerClose} onCancel={onCancel} />));
    const rootTrigger = open('打开资料');
    const nestedTrigger = open('请求更改');
    expect(nestedTrigger).toHaveFocus();
    view.rerender(provide(<ModalPair drawerZ={50} onDrawerClose={onDrawerClose} onCancel={onCancel} />));
    expect(screen.getByRole('button', { name: '取消更改' })).toHaveFocus();
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(nestedTrigger).toHaveFocus();
    escape();
    expect(rootTrigger).toHaveFocus();
  });

  it('restores the nested trigger after a confirmed action, not only after Escape', () => {
    const onConfirm = vi.fn();
    render(provide(<ModalPair onConfirm={onConfirm} />));
    open('打开资料');
    const nestedTrigger = open('请求更改');
    fireEvent.click(screen.getByRole('button', { name: '确定更改' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(nestedTrigger).toHaveFocus();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('falls back to the surviving Drawer when the nested trigger becomes hidden', () => {
    const view = render(provide(<ModalPair />));
    const rootTrigger = open('打开资料');
    open('请求更改');
    view.rerender(provide(<ModalPair hideTrigger />));
    escape();
    expect(drawerClose()).toHaveFocus();
    escape();
    expect(rootTrigger).toHaveFocus();
  });

  it('rebases the confirmation return target when its underlying Drawer is removed first', () => {
    const view = render(provide(<ModalPair />));
    const rootTrigger = open('打开资料');
    open('请求更改');
    view.rerender(provide(<ModalPair showDrawer={false} />));
    expect(screen.getByRole('button', { name: '取消更改' })).toHaveFocus();
    escape();
    expect(rootTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('');
  });

  it('keeps one stack and restores the exact prior scroll state after StrictMode replay', () => {
    const onDrawerClose = vi.fn();
    const onCancel = vi.fn();
    document.body.style.overflow = 'clip';
    const view = render(<StrictMode>{provide(<ModalPair onDrawerClose={onDrawerClose} onCancel={onCancel} />)}</StrictMode>);
    const rootTrigger = open('打开资料');
    const nestedTrigger = open('请求更改');
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(nestedTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    escape();
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(rootTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('clip');
    view.unmount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(document.body.style.overflow).toBe('clip');
  });

  it('cleans up an initially open confirmation after StrictMode registration replay', () => {
    const trigger = document.createElement('button');
    trigger.dataset.externalConfirmTrigger = 'true';
    document.body.append(trigger);
    trigger.focus();
    document.body.style.overflow = 'clip';
    const onCancel = vi.fn();
    const view = render(<StrictMode>{provide(<ConfirmDialog isOpen title="严格模式确认"
      message="确认继续吗？" cancelText="取消更改" onCancel={onCancel} onConfirm={vi.fn()} />)}</StrictMode>);
    expect(screen.getByRole('button', { name: '取消更改' })).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('clip');
    escape();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('assigns distinct accessible names and descriptions to simultaneous confirmations', () => {
    render(provide(<>
      <ConfirmDialog isOpen title="确认一" message="第一条说明" onCancel={vi.fn()} onConfirm={vi.fn()} />
      <ConfirmDialog isOpen title="确认二" message="第二条说明" onCancel={vi.fn()} onConfirm={vi.fn()} />
    </>));
    const first = screen.getByRole('alertdialog', { name: '确认一' });
    const second = screen.getByRole('alertdialog', { name: '确认二' });
    expect(first).toHaveAccessibleDescription('第一条说明');
    expect(second).toHaveAccessibleDescription('第二条说明');
    expect(first.getAttribute('aria-labelledby')).not.toBe(second.getAttribute('aria-labelledby'));
    expect(first.getAttribute('aria-describedby')).not.toBe(second.getAttribute('aria-describedby'));
  });
});
