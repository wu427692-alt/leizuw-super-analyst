import type React from 'react';
import { AudioLines, Download, Home, Menu, Star } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { preloadRoute } from '../../utils/routePreload';

type MobileBottomNavProps = {
  onOpenMore: () => void;
};

const PRIMARY_ITEMS = [
  { to: '/app', labelKey: 'layout.nav.home' as const, shortLabel: '首页', icon: Home, exact: true },
  { to: '/super-watchlist', labelKey: 'layout.nav.superWatchlist' as const, shortLabel: '自选', icon: Star },
  { to: '/essay-radar', labelKey: 'layout.nav.essayRadar' as const, shortLabel: '机构段子', icon: AudioLines },
  { to: '/data-acquisition', labelKey: 'layout.nav.dataAcquisition' as const, shortLabel: '数据下载', icon: Download },
];

export const MobileBottomNav: React.FC<MobileBottomNavProps> = ({ onOpenMore }) => {
  const { t } = useUiLanguage();
  const location = useLocation();
  const moreActive = !PRIMARY_ITEMS.some((item) => (
    item.exact ? location.pathname === item.to : location.pathname.startsWith(item.to)
  ));

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
      <button
        type="button"
        onClick={onOpenMore}
        className={cn('mobile-bottom-nav__item', moreActive && 'is-active')}
        aria-label={t('layout.navMenu')}
      >
        <Menu aria-hidden="true" />
        <span>更多</span>
      </button>
    </nav>
  );
};
