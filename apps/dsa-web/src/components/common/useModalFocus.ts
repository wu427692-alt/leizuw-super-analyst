import { useLayoutEffect, useRef, type RefObject } from 'react';

type ModalSession = {
  dialog: HTMLElement;
  initialFocus: () => HTMLElement | null;
  trigger: HTMLElement | null;
  zIndex: number;
  close: () => void;
};

const activeModals: ModalSession[] = [];
let previousBodyOverflow = '';
const FOCUSABLE_SELECTOR = [
  'a[href]', 'button', 'input', 'textarea', 'select', 'summary', '[tabindex]',
  'audio[controls]', 'video[controls]',
].join(',');

// Participating modals portal to body. Match their numeric layer, then DOM paint
// order; opening a lower layer must not steal focus from the visible top modal.
function topModal() {
  return activeModals.reduce<ModalSession | undefined>((top, entry) => (
    !top || entry.zIndex > top.zIndex || (entry.zIndex === top.zIndex
      && Boolean(top.dialog.compareDocumentPosition(entry.dialog) & Node.DOCUMENT_POSITION_FOLLOWING)) ? entry : top
  ), undefined);
}

function availableFocusTarget(element: HTMLElement) {
  if (!element.isConnected || element.matches(':disabled') || element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (node instanceof HTMLDetailsElement && !node.open) {
      const summary = node.querySelector(':scope > summary');
      if (!summary?.contains(element)) return false;
    }
  }
  return true;
}

function focusableElements(dialog: HTMLElement) {
  return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    .filter(element => element.tabIndex >= 0 && availableFocusTarget(element))
    .sort((first, second) => (first.tabIndex || Infinity) - (second.tabIndex || Infinity));
}

function focusModal(entry: ModalSession) {
  const preferred = entry.initialFocus();
  const target = preferred && availableFocusTarget(preferred)
    ? preferred : focusableElements(entry.dialog)[0] ?? entry.dialog;
  target.focus({ preventScroll: true });
}

function handleModalKeyDown(event: KeyboardEvent) {
  const top = topModal();
  if (!top || event.defaultPrevented) return;
  if (event.key === 'Escape') {
    // A busy confirmation still consumes Escape, without dismissing itself or
    // falling through to a detail reader underneath it.
    event.preventDefault();
    event.stopPropagation();
    top.close();
    return;
  }
  if (event.key !== 'Tab' || event.ctrlKey || event.metaKey || event.altKey) return;
  const elements = focusableElements(top.dialog);
  const first = elements[0];
  const last = elements[elements.length - 1];
  if (!first) {
    event.preventDefault();
    top.dialog.focus({ preventScroll: true });
    return;
  }
  const active = document.activeElement;
  if (!elements.includes(active as HTMLElement) || (event.shiftKey ? active === first : active === last)) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus({ preventScroll: true });
  }
}

function handleModalFocus(event: FocusEvent) {
  const top = topModal();
  if (top && event.target instanceof Node && !top.dialog.contains(event.target)) focusModal(top);
}

function registerModal(entry: ModalSession) {
  if (!activeModals.length) {
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', handleModalKeyDown);
    document.addEventListener('focusin', handleModalFocus);
  }
  activeModals.push(entry);
  if (topModal() === entry) focusModal(entry);

  return () => {
    const wasTop = topModal() === entry;
    const index = activeModals.indexOf(entry);
    if (index < 0) return;
    activeModals.splice(index, 1);
    // Rebase nested return targets if an underlying modal is removed first.
    for (const remaining of activeModals) {
      if (remaining.trigger && entry.dialog.contains(remaining.trigger)) remaining.trigger = entry.trigger;
    }
    if (!activeModals.length) {
      document.removeEventListener('keydown', handleModalKeyDown);
      document.removeEventListener('focusin', handleModalFocus);
      document.body.style.overflow = previousBodyOverflow;
    }
    if (!wasTop) return;
    const remaining = topModal();
    if (entry.trigger && availableFocusTarget(entry.trigger)
      && (!remaining || remaining.dialog.contains(entry.trigger))) {
      entry.trigger.focus({ preventScroll: true });
      if (document.activeElement === entry.trigger) return;
    }
    if (remaining) focusModal(remaining);
  };
}

/** Shared focus/scroll ownership for body-portaled Drawer and ConfirmDialog. */
export function useModalFocus({ isOpen, dialogRef, initialFocusRef, zIndex, onClose, canDismiss = true }: {
  isOpen: boolean;
  dialogRef: RefObject<HTMLElement | null>;
  initialFocusRef: RefObject<HTMLElement | null>;
  zIndex: number;
  onClose: () => void;
  canDismiss?: boolean;
}) {
  const closeRef = useRef({ onClose, canDismiss });
  const zIndexRef = useRef(zIndex);
  const sessionRef = useRef<ModalSession | null>(null);

  useLayoutEffect(() => {
    closeRef.current = { onClose, canDismiss };
    const session = sessionRef.current;
    const active = document.activeElement;
    // Controls may become disabled or be removed independently of callbacks.
    // Preserve a valid internal focus, but recover if the browser drops it to body.
    if (session && topModal() === session && (!(active instanceof HTMLElement)
      || !session.dialog.contains(active) || !availableFocusTarget(active))) focusModal(session);
  });
  useLayoutEffect(() => {
    zIndexRef.current = zIndex;
    const previousTop = topModal();
    if (sessionRef.current) sessionRef.current.zIndex = zIndex;
    const nextTop = topModal();
    if (nextTop && nextTop !== previousTop) focusModal(nextTop);
  }, [zIndex]);
  useLayoutEffect(() => {
    if (!isOpen || !dialogRef.current) return;
    const session: ModalSession = {
      dialog: dialogRef.current,
      initialFocus: () => initialFocusRef.current,
      trigger: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      zIndex: zIndexRef.current,
      close: () => { if (closeRef.current.canDismiss) closeRef.current.onClose(); },
    };
    sessionRef.current = session;
    const unregister = registerModal(session);
    return () => { unregister(); sessionRef.current = null; };
  }, [isOpen, dialogRef, initialFocusRef]);
}
