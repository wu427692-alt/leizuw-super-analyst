import React, { useEffect, useState } from 'react';
import { BarChart3, DatabaseZap, FlaskConical, Home, LockKeyhole, MessageSquareQuote, Network, Radar, RadioTower, Search, ShieldCheck, Star } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { ALPHASIFT_CONFIG_CHANGED_EVENT, SYSTEM_CONFIG_CHANGED_EVENT, alphasiftApi } from '../../api/alphasift';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';
import { StatusDot } from '../common/StatusDot';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
};

const NAV_ITEMS: NavItem[] = [
  { key: 'home', labelKey: 'layout.nav.home', to: '/app', icon: Home, exact: true },
  { key: 'research-center', labelKey: 'layout.nav.researchCenter', to: '/research-center', icon: ShieldCheck },
  { key: 'industry-research', labelKey: 'layout.nav.industryResearch', to: '/industry-research', icon: Network },
  { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
  { key: 'essay-radar', labelKey: 'layout.nav.essayRadar', to: '/essay-radar', icon: Radar },
  { key: 'essay-quant', labelKey: 'layout.nav.essayQuant', to: '/essay-quant', icon: FlaskConical },
  { key: 'investment-monitor', labelKey: 'layout.nav.investmentMonitor', to: '/investment-monitor', icon: RadioTower },
  { key: 'super-watchlist', labelKey: 'layout.nav.superWatchlist', to: '/super-watchlist', icon: Star },
  { key: 'data-acquisition', labelKey: 'layout.nav.dataAcquisition', to: '/data-acquisition', icon: DatabaseZap },
  { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default' }) => {
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showAlphaSiftNav, setShowAlphaSiftNav] = useState(false);

  useEffect(() => {
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
  }, []);

  const navItems = showAlphaSiftNav ? NAV_ITEMS : NAV_ITEMS.filter((item) => item.key !== 'screening');
  const isRail = variant === 'rail';
  const itemBaseClass = cn(
    'group relative box-border flex h-[var(--nav-item-height)] w-full min-w-0 max-w-full items-center overflow-hidden rounded-md border border-transparent text-sm leading-none text-secondary-text transition-all',
    isRail
      ? 'justify-center gap-2.5 px-1.5'
      : collapsed
        ? 'justify-center px-0'
        : 'gap-3 px-[var(--nav-item-padding-x)]'
  );
  const itemInteractiveClass = cn(
    itemBaseClass,
    'hover:bg-[var(--nav-hover-bg)] hover:text-foreground'
  );
  const itemActiveClass = 'border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] font-medium text-[hsl(var(--primary))]';
  const itemIconClass = cn(isRail ? 'h-[18px] w-[18px]' : 'h-5 w-5', 'shrink-0');
  const itemLabelClass = cn('truncate', isRail ? 'text-center' : '');

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col">
      <div
        className={cn(
          'flex items-center',
          isRail ? 'mb-5 justify-center gap-2 pt-1' : 'mb-4 gap-2 px-1',
          collapsed || isRail ? 'justify-center' : ''
        )}
      >
        <div
          className={cn(
            'flex items-center justify-center bg-[var(--research-blue)] text-white',
            isRail ? 'h-9 w-9 rounded-md' : 'h-10 w-10 rounded-md'
          )}
        >
          <BarChart3 className={cn(isRail ? 'h-[19px] w-[19px]' : 'h-5 w-5')} />
        </div>
        {!collapsed ? (
          <p className={cn('min-w-0 font-semibold leading-tight text-foreground', isRail ? 'max-w-[130px] text-[0.75rem]' : 'text-sm')}>乐子乌超级价值</p>
        ) : null}
      </div>

      <nav
        className="flex min-h-0 w-full min-w-0 flex-1 flex-col gap-1.5 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        aria-label={t('layout.mainNav')}
      >
        {navItems.map(({ key, labelKey, to, icon: Icon, exact, badge }) => {
          const label = t(labelKey);
          return (
          <NavLink
            key={key}
            to={to}
            end={exact}
            onClick={onNavigate}
            onMouseEnter={() => void preloadRoute(to).catch(() => undefined)}
            onFocus={() => void preloadRoute(to).catch(() => undefined)}
            onTouchStart={() => void preloadRoute(to).catch(() => undefined)}
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
                <Icon className={cn(itemIconClass, isActive ? 'text-[var(--nav-icon-active)]' : 'text-current')} />
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
        );
        })}

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
      </nav>

      <NavLink
          to="/admin"
          onClick={onNavigate}
          onMouseEnter={() => void preloadRoute('/admin').catch(() => undefined)}
          onFocus={() => void preloadRoute('/admin').catch(() => undefined)}
          onTouchStart={() => void preloadRoute('/admin').catch(() => undefined)}
          className={cn(
            itemInteractiveClass,
            'shrink-0',
            isRail ? 'mt-1.5' : 'mt-5'
          )}
        >
          <LockKeyhole className={itemIconClass} />
          {!collapsed ? <span className={itemLabelClass}>管理员</span> : null}
      </NavLink>
    </div>
  );
};
