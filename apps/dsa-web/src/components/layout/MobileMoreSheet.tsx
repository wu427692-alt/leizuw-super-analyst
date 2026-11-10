import type React from 'react';
import { Languages, LockKeyhole, Moon, Sun } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTheme } from 'next-themes';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { BottomSheet } from '../mobile/BottomSheet';
import { NAV_ITEMS } from './navItems';

type MobileMoreSheetProps = {
  isOpen: boolean;
  onClose: () => void;
  /** 已经在底部标签栏里的入口，不在这里重复 */
  excludePaths: string[];
  documentNavigation?: boolean;
};

/** 「更多」：全部模块宫格 + 外观与语言。与侧栏共用 NAV_ITEMS，避免出现第二份模块清单。 */
export const MobileMoreSheet: React.FC<MobileMoreSheetProps> = ({ isOpen, onClose, excludePaths, documentNavigation = false }) => {
  const { t, language, setLanguage } = useUiLanguage();
  const { resolvedTheme, setTheme } = useTheme();
  const { pathname } = useLocation();
  const items = NAV_ITEMS.filter((item) => !excludePaths.includes(item.to));
  const groups = [
    { title: '研究', items: items.filter((item) => item.group === 'primary') },
    { title: '工具', items: items.filter((item) => item.group === 'tools') },
  ].filter((group) => group.items.length);
  const isActive = (to: string, exact?: boolean) => (exact ? pathname === to : pathname === to || pathname.startsWith(`${to}/`));

  return (
    <BottomSheet isOpen={isOpen} onClose={onClose} title="全部模块">
      <div className="mobile-more">
        {groups.map((group) => (
          <section key={group.title} className="mobile-more__section">
            <h3>{group.title}</h3>
            <div className="mobile-more__grid">
              {group.items.map(({ key, to, labelKey, icon: Icon, exact }) => (
                <NavLink
                  key={key}
                  to={to}
                  reloadDocument={documentNavigation}
                  className={cn('mobile-more__item', isActive(to, exact) && 'is-active')}
                  onClick={onClose}
                >
                  <span><Icon aria-hidden="true" /></span>
                  {t(labelKey)}
                </NavLink>
              ))}
              {group.title === '工具' ? (
                <NavLink to="/admin" reloadDocument={documentNavigation} className={cn('mobile-more__item', isActive('/admin') && 'is-active')} onClick={onClose}>
                  <span><LockKeyhole aria-hidden="true" /></span>
                  {t('layout.nav.admin')}
                </NavLink>
              ) : null}
            </div>
          </section>
        ))}
        <section className="mobile-more__section">
          <h3>偏好</h3>
          <div className="mobile-more__prefs">
            <button type="button" onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}>
              {resolvedTheme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
              {resolvedTheme === 'dark' ? '浅色外观' : '深色外观'}
            </button>
            <button type="button" onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>
              <Languages aria-hidden="true" />
              {language === 'zh' ? 'English' : '中文'}
            </button>
          </div>
        </section>
      </div>
    </BottomSheet>
  );
};
