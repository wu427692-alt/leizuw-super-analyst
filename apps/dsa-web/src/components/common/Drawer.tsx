import type React from 'react';
import { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { useModalFocus } from './useModalFocus';

interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  width?: string;
  zIndex?: number;
  side?: 'left' | 'right';
  backdropClassName?: string;
}

/**
 * 详情阅读器。
 *
 * 手机 / 平板：从侧边滑出的整屏面板（触屏上更容易单手关闭）。
 * 桌面（≥1024px）：屏幕居中的弹框。侧栏式面板在宽屏上离内容太远、也压迫阅读区。
 * 两种形态共用同一套 DOM，只由 CSS 媒体查询切换，调用方不用关心。
 */
export const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  children,
  width = 'max-w-2xl',
  zIndex = 50,
  side = 'right',
  backdropClassName,
}) => {
  const { t } = useUiLanguage();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const generatedTitleId = useId();
  useModalFocus({ isOpen, dialogRef, initialFocusRef: closeButtonRef, zIndex, onClose });

  if (!isOpen) return null;

  const titleId = title ? generatedTitleId : undefined;

  const drawer = (
    <div className="app-drawer" style={{ zIndex }} role="presentation">
      <div
        className={cn('app-drawer__backdrop', backdropClassName)}
        onClick={onClose}
      />

      <div className={cn('app-drawer__frame', `is-${side}`, width)}>
        <div
          ref={dialogRef}
          role="dialog"
          tabIndex={-1}
          aria-modal="true"
          aria-labelledby={titleId}
          className="app-drawer__panel h-full min-h-0"
        >
          <div className="app-drawer__head">
            {title ? (
              <div className="min-w-0">
                <h2 id={titleId} className="app-drawer__title">{title}</h2>
              </div>
            ) : <div />}
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              className="app-drawer__close"
              aria-label={t('common.closeDrawer')}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="app-drawer__body">
            {children}
          </div>
        </div>
      </div>
    </div>
  );

  // 详情必须挂在 body 上：页面里存在 transform / overflow-hidden 的容器，
  // 直接渲染会让 position: fixed 相对那个面板定位，浮到看不见的地方。
  return createPortal(drawer, document.body);
};
