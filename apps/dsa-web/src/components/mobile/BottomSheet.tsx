import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useModalFocus } from '../common/useModalFocus';

type BottomSheetProps = {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  /** 副标题或说明，最多一行 */
  description?: string;
  children: React.ReactNode;
  /** 主要动作区（固定在底部） */
  footer?: React.ReactNode;
  /** 初始高度档位：short 约 40dvh，large 约 92dvh */
  detent?: 'short' | 'large';
};

/**
 * 手机端底部弹层。遵循移动端弹层契约：
 * 一个把手、一个固定标题栏、一个滚动主体、可选固定动作区；下滑或点遮罩关闭。
 */
export const BottomSheet: React.FC<BottomSheetProps> = ({
  isOpen,
  onClose,
  title,
  description,
  children,
  footer,
  detent = 'large',
}) => {
  const sheetRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [dragOffset, setDragOffset] = useState(0);
  const dragStart = useRef<number | null>(null);
  useModalFocus({ isOpen, dialogRef: sheetRef, initialFocusRef: closeRef, zIndex: 90, onClose });

  useEffect(() => {
    if (!isOpen) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handlePointerDown = (event: React.PointerEvent) => {
    dragStart.current = event.clientY;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  };
  const handlePointerMove = (event: React.PointerEvent) => {
    if (dragStart.current === null) return;
    setDragOffset(Math.max(0, event.clientY - dragStart.current));
  };
  const handlePointerUp = () => {
    if (dragStart.current === null) return;
    const shouldClose = dragOffset > 96;
    dragStart.current = null;
    setDragOffset(0);
    if (shouldClose) onClose();
  };

  return createPortal(
    <div className="mobile-sheet-root" role="presentation">
      <div className="mobile-sheet-scrim" onClick={onClose} />
      <div
        ref={sheetRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn('mobile-sheet', `is-${detent}`)}
        style={dragOffset ? { transform: `translateY(${dragOffset}px)`, transition: 'none' } : undefined}
      >
        <div
          className="mobile-sheet__handle-area"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
        >
          <span className="mobile-sheet__handle" aria-hidden="true" />
        </div>
        <div className="mobile-sheet__header">
          <div className="mobile-sheet__title-block">
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button ref={closeRef} type="button" className="mobile-sheet__close" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </div>
        <div className="mobile-sheet__body">{children}</div>
        {footer ? <div className="mobile-sheet__footer">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
};
