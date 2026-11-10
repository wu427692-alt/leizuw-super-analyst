import type React from 'react';
import { useState } from 'react';
import { CandlestickChart, Ellipsis, House, MessagesSquare, Star } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';
import { preloadRouteData } from '../../utils/routeDataPreload';
import { MobileMoreSheet } from './MobileMoreSheet';

/* 手机端唯一的一套导航：团队 · 行情 · 自选 · 总览 · 更多。
   当前页面不属于前 4 项时，「更多」高亮，保证用户始终知道自己在哪里。 */
const PRIMARY_ITEMS = [
  { to: '/meeting', labelKey: 'layout.nav.meeting' as const, shortLabel: '团队', icon: MessagesSquare, exact: true },
  { to: '/app', labelKey: 'layout.nav.home' as const, shortLabel: '行情', icon: CandlestickChart, exact: true },
  { to: '/super-watchlist', labelKey: 'layout.nav.superWatchlist' as const, shortLabel: '自选', icon: Star },
  { to: '/home', labelKey: 'layout.nav.workbench' as const, shortLabel: '总览', icon: House, exact: true },
];

const isPrimaryPath = (pathname: string) => PRIMARY_ITEMS.some(({ to, exact }) => (
  exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`)
));

export const MobileBottomNav: React.FC<{ documentNavigation?: boolean }> = ({ documentNavigation = false }) => {
  const { t } = useUiLanguage();
  const { pathname } = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = !isPrimaryPath(pathname);
  const warmNavigation = (to: string) => {
    if (documentNavigation) return;
    void Promise.allSettled([preloadRoute(to), preloadRouteData(to)]);
  };

  return (
    <>
      <nav className="mobile-bottom-nav lg:hidden" aria-label="手机主导航">
        {PRIMARY_ITEMS.map(({ to, labelKey, shortLabel, icon: Icon, exact }) => (
          <NavLink
            key={to}
            to={to}
            reloadDocument={documentNavigation}
            end={exact}
            onTouchStart={() => warmNavigation(to)}
            onFocus={() => warmNavigation(to)}
            className={({ isActive }) => cn('mobile-bottom-nav__item', isActive && 'is-active')}
            aria-label={t(labelKey)}
          >
            <Icon aria-hidden="true" />
            <span aria-hidden="true">{shortLabel}</span>
          </NavLink>
        ))}
        <button
          type="button"
          className={cn('mobile-bottom-nav__item', moreActive && 'is-active')}
          aria-label="全部模块"
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen(true)}
        >
          <Ellipsis aria-hidden="true" />
          <span aria-hidden="true">更多</span>
        </button>
      </nav>
      <MobileMoreSheet
        isOpen={moreOpen}
        onClose={() => setMoreOpen(false)}
        excludePaths={PRIMARY_ITEMS.map(({ to }) => to)}
        documentNavigation={documentNavigation}
      />
    </>
  );
};
