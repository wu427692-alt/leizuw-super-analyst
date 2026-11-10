import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, MoreHorizontal } from 'lucide-react';
import { cn } from '../../utils/cn';

export type MobileAppBarAction = {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>;
  onClick: () => void;
  /** 次要动作收进「更多」菜单 */
  overflow?: boolean;
};

type MobileAppBarProps = {
  title: string;
  /** 大标题（首屏展示，向下滚动后收起为导航栏标题） */
  largeTitle?: string;
  /** 没有返回时传 undefined;传函数即在左侧显示返回箭头 */
  onBack?: () => void;
  backLabel?: string;
  actions?: MobileAppBarAction[];
  /** 页面内容滚动容器，用于把大标题与滚动联动；不传则监听 window */
  scrollTargetRef?: React.RefObject<HTMLElement | null>;
  className?: string;
};

/**
 * 手机端应用栏。
 * 首屏显示大标题，向下滚动后收起为单行标题（iOS Large Title 模式）。
 * 栏本身 sticky、固定 44px；大标题在文档流里，随内容滚到栏下面后由栏内小标题接替。
 */
export const MobileAppBar: React.FC<MobileAppBarProps> = ({
  title,
  largeTitle,
  onBack,
  backLabel = '返回',
  actions = [],
  scrollTargetRef,
  className,
}) => {
  const [scrolled, setScrolled] = useState(false);
  const collapsed = !largeTitle || scrolled;
  const [menuOpen, setMenuOpen] = useState(false);
  const barRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const target: HTMLElement | Window = scrollTargetRef?.current ?? window;
    const read = () => {
      const offset = target instanceof Window ? window.scrollY : (target as HTMLElement).scrollTop;
      setScrolled(offset > 32);
    };
    read();
    target.addEventListener('scroll', read, { passive: true });
    return () => target.removeEventListener('scroll', read);
  }, [largeTitle, scrollTargetRef]);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointer = (event: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    return () => document.removeEventListener('mousedown', onPointer);
  }, [menuOpen]);

  const inlineActions = actions.filter((action) => !action.overflow);
  const overflowActions = actions.filter((action) => action.overflow);

  return (
    <>
    <header ref={barRef} className={cn('mobile-app-bar', collapsed && 'is-collapsed', className)}>
      <div className="mobile-app-bar__row">
        {onBack ? (
          <button type="button" className="mobile-app-bar__back" onClick={onBack} aria-label={backLabel}>
            <ChevronLeft aria-hidden="true" />
          </button>
        ) : null}
        <p className="mobile-app-bar__title">{title}</p>
        <div className="mobile-app-bar__actions">
          {inlineActions.map(({ key, label, icon: Icon, onClick }) => (
            <button key={key} type="button" className="mobile-app-bar__icon" onClick={onClick} aria-label={label}>
              <Icon aria-hidden="true" />
            </button>
          ))}
          {overflowActions.length ? (
            <div className="mobile-app-bar__menu-wrap">
              <button
                type="button"
                className="mobile-app-bar__icon"
                aria-label="更多操作"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                onClick={() => setMenuOpen((open) => !open)}
              >
                <MoreHorizontal aria-hidden="true" />
              </button>
              {menuOpen ? (
                <div className="mobile-app-bar__menu" role="menu">
                  {overflowActions.map(({ key, label, icon: Icon, onClick }) => (
                    <button
                      key={key}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        onClick();
                      }}
                    >
                      <Icon aria-hidden="true" />
                      <span>{label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </header>
    {largeTitle ? <h1 className="mobile-app-bar__large-title">{largeTitle}</h1> : null}
    </>
  );
};
