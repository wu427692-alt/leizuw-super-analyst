import type React from 'react';
import { AudioLines, BookOpen, CandlestickChart, ClipboardCheck, DatabaseZap, FlaskConical, House, MessageSquareQuote, MessagesSquare, Network, Newspaper, Orbit, Search, Star } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';

/** 全站唯一的模块清单：桌面侧栏、手机「更多」与路由标题都从这里取。 */
export type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
  group: 'primary' | 'tools';
};

export const NAV_ITEMS: NavItem[] = [
  { key: 'workbench', labelKey: 'layout.nav.workbench', to: '/home', icon: House, exact: true, group: 'primary' },
  { key: 'meeting', labelKey: 'layout.nav.meeting', to: '/meeting', icon: MessagesSquare, exact: true, group: 'primary' },
  { key: 'home', labelKey: 'layout.nav.home', to: '/app', icon: CandlestickChart, exact: true, group: 'primary' },
  { key: 'concept-themes', labelKey: 'layout.nav.conceptThemes', to: '/concept-themes', icon: Orbit, group: 'primary' },
  { key: 'super-watchlist', labelKey: 'layout.nav.superWatchlist', to: '/super-watchlist', icon: Star, group: 'primary' },
  { key: 'industry-research', labelKey: 'layout.nav.industryResearch', to: '/industry-research', icon: Network, group: 'primary' },
  { key: 'tasks', labelKey: 'layout.nav.tasks', to: '/tasks', icon: ClipboardCheck, group: 'primary' },
  { key: 'ai-news', labelKey: 'layout.nav.aiNews', to: '/ai-news', icon: Newspaper, group: 'tools' },
  { key: 'essay-radar', labelKey: 'layout.nav.essayRadar', to: '/essay-radar', icon: AudioLines, group: 'tools' },
  { key: 'essay-quant', labelKey: 'layout.nav.essayQuant', to: '/essay-quant', icon: FlaskConical, group: 'tools' },
  { key: 'data-acquisition', labelKey: 'layout.nav.dataAcquisition', to: '/data-acquisition', icon: DatabaseZap, group: 'tools' },
  { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion', group: 'tools' },
  { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search, group: 'tools' },
  { key: 'guide', labelKey: 'layout.nav.guide', to: '/guide', icon: BookOpen, group: 'tools' },
];
