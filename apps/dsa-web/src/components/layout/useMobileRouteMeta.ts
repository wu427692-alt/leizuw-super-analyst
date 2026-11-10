import { useLocation, useSearchParams } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';

type RouteMeta = {
  /** 收起后应用栏上的标题 */
  title: string;
  /** 首屏大标题；与 title 相同时不显示大标题 */
  largeTitle?: string;
  /** 二级页面：一定显示返回箭头，即使浏览器历史里没有上一页 */
  nested?: boolean;
};

const ROUTE_TITLES: Array<{ match: (path: string) => boolean; titleKey: UiTextKey }> = [
  { match: (path) => path === '/home', titleKey: 'layout.nav.workbench' },
  { match: (path) => path === '/app', titleKey: 'layout.nav.home' },
  { match: (path) => path.startsWith('/super-watchlist'), titleKey: 'layout.nav.superWatchlist' },
  { match: (path) => path.startsWith('/meeting'), titleKey: 'layout.nav.meeting' },
  { match: (path) => path.startsWith('/concept-themes'), titleKey: 'layout.nav.conceptThemes' },
  { match: (path) => path.startsWith('/industry-research'), titleKey: 'layout.nav.industryResearch' },
  { match: (path) => path.startsWith('/tasks'), titleKey: 'layout.nav.tasks' },
  { match: (path) => path.startsWith('/ai-news'), titleKey: 'layout.nav.aiNews' },
  { match: (path) => path.startsWith('/essay-radar'), titleKey: 'layout.nav.essayRadar' },
  { match: (path) => path.startsWith('/essay-quant'), titleKey: 'layout.nav.essayQuant' },
  { match: (path) => path.startsWith('/data-acquisition'), titleKey: 'layout.nav.dataAcquisition' },
  { match: (path) => path.startsWith('/chat'), titleKey: 'layout.nav.chat' },
  { match: (path) => path.startsWith('/screening'), titleKey: 'layout.nav.screening' },
  { match: (path) => path.startsWith('/guide'), titleKey: 'layout.nav.guide' },
  { match: (path) => path.startsWith('/admin'), titleKey: 'layout.nav.admin' },
];

/**
 * 手机应用栏的标题来源：与侧栏导航用同一套文案，避免出现第二套命名。
 * 大标题只在根页面显示，子页面直接显示单行标题。
 */
export function useMobileRouteMeta(): RouteMeta {
  const { pathname } = useLocation();
  const [searchParams] = useSearchParams();
  const { t } = useUiLanguage();
  const entry = ROUTE_TITLES.find(({ match }) => match(pathname));
  const title = entry ? t(entry.titleKey) : t('layout.appFallbackTitle');

  // 带 ?symbol= / ?project= 时是二级页面：标题换成具体对象，且一定显示返回。
  const isStockDetail = pathname.startsWith('/super-watchlist') && Boolean(searchParams.get('symbol'));
  const isResearchDetail = pathname.startsWith('/industry-research') && Boolean(searchParams.get('project'));
  if (isStockDetail || isResearchDetail) {
    return {
      title: isStockDetail ? '个股详情' : '研究报告',
      largeTitle: undefined,
      nested: true,
    };
  }

  const isRootLevel = !pathname.includes('/', 1);
  return { title, largeTitle: isRootLevel ? title : undefined, nested: !isRootLevel };
}
