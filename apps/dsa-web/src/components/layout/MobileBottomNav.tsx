import type React from 'react';
import { ClipboardCheck, Home, Network, Orbit, Star } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';

const PRIMARY_ITEMS = [
  { to: '/app', labelKey: 'layout.nav.home' as const, shortLabel: '首页', icon: Home, exact: true },
  { to: '/concept-themes', labelKey: 'layout.nav.conceptThemes' as const, shortLabel: '机会', icon: Orbit },
  { to: '/super-watchlist', labelKey: 'layout.nav.superWatchlist' as const, shortLabel: '个股', icon: Star },
  { to: '/industry-research', labelKey: 'layout.nav.industryResearch' as const, shortLabel: '研究', icon: Network },
  { to: '/tasks', labelKey: 'layout.nav.tasks' as const, shortLabel: '任务', icon: ClipboardCheck },
];

export const MobileBottomNav: React.FC = () => {
  const { t } = useUiLanguage();

  return (
    <nav className="mobile-bottom-nav lg:hidden" aria-label="手机主导航">
      {PRIMARY_ITEMS.map(({ to, labelKey, shortLabel, icon: Icon, exact }) => (
        <NavLink
          key={to}
          to={to}
          end={exact}
          onTouchStart={() => void preloadRoute(to).catch(() => undefined)}
          onFocus={() => void preloadRoute(to).catch(() => undefined)}
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
