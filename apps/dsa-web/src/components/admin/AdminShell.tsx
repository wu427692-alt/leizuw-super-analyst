import type React from 'react';
import {
  Activity,
  Braces,
  Database,
  Gauge,
  KeyRound,
  LogOut,
  RefreshCw,
  Settings2,
  ShieldCheck,
  UsersRound,
} from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { cn } from '../../utils/cn';
import '../../pages/AdminConsolePage.css';

type AdminShellProps = { children?: React.ReactNode };

const items = [
  { to: '/admin', label: '运行概览', icon: Activity, end: true },
  { to: '/admin/api-models', label: 'API 与模型', icon: KeyRound },
  { to: '/admin/data-sources', label: '数据源', icon: Database },
  { to: '/admin/sync', label: '同步任务', icon: RefreshCw },
  { to: '/admin/access', label: '用户与访问', icon: UsersRound },
  { to: '/admin/usage', label: '用量审计', icon: Gauge },
  { to: '/admin/settings', label: '系统设置', icon: Settings2 },
];

export const AdminShell: React.FC<AdminShellProps> = ({ children }) => {
  const { logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/', { replace: true });
  };

  return (
    <div className="admin-shell min-h-screen text-slate-100">
      <aside className="admin-sidebar">
        <NavLink to="/admin" className="admin-brand" aria-label="乐子乌超级价值管理后台">
          <span className="admin-brand-mark"><Braces size={18} /></span>
          <span><strong>乐子乌超级价值</strong><small>ADMIN CONSOLE</small></span>
        </NavLink>

        <nav className="admin-nav" aria-label="管理员导航">
          {items.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => cn('admin-nav-item', isActive && 'is-active')}
            >
              <Icon size={17} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="admin-sidebar-foot">
          <NavLink to="/" className="admin-public-link"><ShieldCheck size={16} />返回公开前台</NavLink>
          <button type="button" onClick={() => void handleLogout()} className="admin-logout">
            <LogOut size={16} />退出管理员
          </button>
        </div>
      </aside>

      <main className="admin-main">{children ?? <Outlet />}</main>
    </div>
  );
};
