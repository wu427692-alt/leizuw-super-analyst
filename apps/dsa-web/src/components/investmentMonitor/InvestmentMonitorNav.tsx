import { Activity, Building2, LayoutDashboard, Radar, Sparkles, Star } from 'lucide-react';
import { NavLink } from 'react-router-dom';

const items = [
  { to: '/investment-monitor', label: '情报总览', icon: LayoutDashboard, end: true },
  { to: '/investment-monitor/feed', label: '事实流水', icon: Radar },
  { to: '/super-watchlist', label: '超级关注股', icon: Star },
  { to: '/investment-monitor/market', label: '市场结构', icon: Activity },
  { to: '/investment-monitor/company', label: '公司与机构', icon: Building2 },
  { to: '/investment-monitor/analysis', label: '综合研判', icon: Sparkles },
];

export function InvestmentMonitorNav() {
  return (
    <nav aria-label="投资情报台栏目" className="flex gap-1 overflow-x-auto border-y border-[#D8DADF] bg-white px-2 py-1.5 text-[#17181A]">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => `inline-flex shrink-0 items-center gap-1.5 px-2.5 py-1.5 text-[10px] font-semibold transition-colors ${isActive ? 'bg-[#155EEF] text-white' : 'hover:bg-[#EEF2F7]'}`}>
          <Icon className="h-3.5 w-3.5" />{label}
        </NavLink>
      ))}
    </nav>
  );
}
