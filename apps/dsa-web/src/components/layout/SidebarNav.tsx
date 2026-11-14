import React, { useEffect, useState } from 'react';
import { BarChart3, LockKeyhole } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { ALPHASIFT_CONFIG_CHANGED_EVENT, SYSTEM_CONFIG_CHANGED_EVENT, alphasiftApi } from '../../api/alphasift';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';
import { preloadRouteData } from '../../utils/routeDataPreload';
import { StatusDot } from '../common/StatusDot';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
  documentNavigation?: boolean;
};

import { NAV_ITEMS } from './navItems';

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default', documentNavigation = false }) => {
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showAlphaSiftNav, setShowAlphaSiftNav] = useState(false);

  useEffect(() => {
    if (documentNavigation) return;
    let active = true;

    const refreshAlphaSiftStatus = async () => {
      try {
        const status = await alphasiftApi.getStatus();
        if (active) {
          setShowAlphaSiftNav(status.enabled);
        }
      } catch {
        if (active) {
          setShowAlphaSiftNav(false);
        }
      }
    };

    void refreshAlphaSiftStatus();
    window.addEventListener(ALPHASIFT_CONFIG_CHANGED_EVENT, refreshAlphaSiftStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshAlphaSiftStatus);

    return () => {
      active = false;
      window.removeEventListener(ALPHASIFT_CONFIG_CHANGED_EVENT, refreshAlphaSiftStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshAlphaSiftStatus);
    };
  }, [documentNavigation]);

  const navItems = showAlphaSiftNav ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.key !== 'screening');
  const isRail = variant === 'rail';
  // 唯一的导航项样式：左对齐、固定 18px 图标列、40px 行高；选中态只有一种（底色 + 左侧品牌竖条）。
  const itemBaseClass = cn(
    'nav-item group relative box-border flex h-10 w-full min-w-0 max-w-full items-center justify-start rounded-[var(--radius-control)] text-[length:var(--fs-body)] leading-[var(--lh-body)] font-normal text-text-2 transition-colors duration-[var(--dur-fast)]',
    collapsed ? 'justify-center px-0' : 'gap-2 px-3'
  );
  const itemInteractiveClass = cn(itemBaseClass, 'hover:bg-bg-3 hover:text-text-1');
  const itemActiveClass = 'is-active bg-bg-3 font-medium text-text-1';
  const itemIconClass = 'h-[18px] w-[18px] shrink-0';
  const itemLabelClass = 'min-w-0 truncate text-left';
  const warmNavigation = (to: string) => {
    if (documentNavigation) return;
    void Promise.allSettled([preloadRoute(to), preloadRouteData(to)]);
  };

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      <div
        className={cn('mb-4 flex items-center gap-2 px-3 pt-1', collapsed ? 'justify-center px-0' : '')}
      >
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--radius-control)] bg-brand text-[color:var(--brand-on)]">
          <BarChart3 className="h-4 w-4" aria-hidden="true" />
        </div>
        {!collapsed ? (
          <p className="min-w-0 truncate text-[length:var(--fs-body)] font-semibold leading-[var(--lh-body)] text-text-1">乐子乌智能投研</p>
        ) : null}
      </div>

      <nav
        className="flex min-h-0 w-full min-w-0 flex-1 flex-col gap-1 overflow-y-auto"
        aria-label={t('layout.mainNav')}
      >
        {navItems.map(({ key, labelKey, to, icon: Icon, exact, badge, group }, index) => {
          const label = t(labelKey);
          return (
          <React.Fragment key={key}>
          {index === 0 || navItems[index - 1]?.group !== group ? <div className={cn('nav-group-title px-3 pb-2 text-[length:var(--fs-caption)] font-medium leading-[var(--lh-caption)] text-text-3', index === 0 ? 'pt-0' : 'pt-6', collapsed && 'sr-only')}>{t(group === 'primary' ? 'layout.nav.primaryGroup' : 'layout.nav.toolsGroup')}</div> : null}
          <NavLink
            to={to}
            reloadDocument={documentNavigation || to === '/ai-news'}
            end={exact}
            onClick={onNavigate}
            onMouseEnter={() => warmNavigation(to)}
            onFocus={() => warmNavigation(to)}
            onTouchStart={() => warmNavigation(to)}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                itemInteractiveClass,
                isActive ? itemActiveClass : ''
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon aria-hidden="true" className={cn(itemIconClass, isActive ? 'text-brand' : 'text-current')} />
                {!collapsed ? <span className={itemLabelClass}>{label}</span> : null}
                {badge === 'completion' && completionBadge ? (
                  <StatusDot
                    tone="info"
                    data-testid="chat-completion-badge"
                    className={cn(
                      'absolute right-3 border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]',
                      collapsed ? 'right-2 top-2' : ''
                    )}
                    aria-label={t('layout.newChatMessage')}
                  />
                ) : null}
              </>
            )}
          </NavLink>
          </React.Fragment>
        );
        })}

      </nav>

      <div className="mt-2 flex shrink-0 flex-col gap-1 border-t border-line-1 pt-2">
        <ThemeToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
        <UiLanguageToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />

      <NavLink
          to="/admin"
          reloadDocument={documentNavigation}
          onClick={onNavigate}
          onMouseEnter={() => warmNavigation('/admin')}
          onFocus={() => warmNavigation('/admin')}
          onTouchStart={() => warmNavigation('/admin')}
          className={cn(
            itemInteractiveClass,
            'shrink-0'
          )}
        >
          <LockKeyhole aria-hidden="true" className={itemIconClass} />
          {/* 原来写死中文「管理员」：切到 English 后整条侧栏只有这一项还是中文。 */}
          {!collapsed ? <span className={itemLabelClass}>{t('layout.nav.admin')}</span> : null}
      </NavLink>
      </div>
    </div>
  );
};
