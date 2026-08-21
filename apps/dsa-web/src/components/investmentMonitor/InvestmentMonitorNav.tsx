import { BarChart3, Landmark, Radio, Rows3 } from 'lucide-react';
import { NavLink } from 'react-router-dom';

const items = [
  { to: '/investment-monitor', label: '全渠道情报', icon: Rows3, end: true },
  { to: '/investment-monitor/bi', label: '数据源 BI', icon: BarChart3 },
  { to: '/investment-monitor/feed', label: '实时流水', icon: Radio },
  { to: '/investment-monitor/dragon-tiger', label: '龙虎榜', icon: Landmark },
];

export function InvestmentMonitorNav() {
  return (
    <nav aria-label="投资情报台栏目" className="flex gap-1 overflow-x-auto border-y border-[#242A31] bg-[#0B0C0A] px-2 py-1.5 font-mono text-[#A7AFB8]">
      {items.map(({ to, label, icon: Icon, end }) => (
        <NavLink key={to} to={to} end={end} className={({ isActive }) => `inline-flex shrink-0 items-center gap-1.5 border px-2.5 py-1.5 text-[10px] font-semibold transition-colors ${isActive ? 'border-[#00E676] text-[#00E676]' : 'border-transparent hover:border-[#303740] hover:text-white'}`}>
          <Icon className="h-3.5 w-3.5" />{label}
        </NavLink>
      ))}
    </nav>
  );
}
