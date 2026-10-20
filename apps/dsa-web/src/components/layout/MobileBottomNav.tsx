import type React from 'react';
import { ClipboardCheck, MessagesSquare, Network, Orbit, Star } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';
import { preloadRouteData } from '../../utils/routeDataPreload';

const PRIMARY_ITEMS = [
  { to: '/meeting', labelKey: 'layout.nav.meeting' as const, shortLabel: '会议', icon: MessagesSquare, exact: true },
  { to: '/concept-themes', labelKey: 'layout.nav.conceptThemes' as const, shortLabel: '题材', icon: Orbit },
  { to: '/super-watchlist', labelKey: 'layout.nav.superWatchlist' as const, shortLabel: '自选', icon: Star },
  { to: '/industry-research', labelKey: 'layout.nav.industryResearch' as const, shortLabel: '研究', icon: Network },
  { to: '/tasks', labelKey: 'layout.nav.tasks' as const, shortLabel: '任务', icon: ClipboardCheck },
];

export const MobileBottomNav: React.FC<{ documentNavigation?: boolean }> = ({ documentNavigation = false }) => {
  const { t } = useUiLanguage();
  const warmNavigation = (to: string) => {
    if (documentNavigation) return;
    void Promise.allSettled([preloadRoute(to), preloadRouteData(to)]);
  };

  return (
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
    </nav>
  );
};
