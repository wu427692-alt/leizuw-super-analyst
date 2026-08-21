import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  KeyRound,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  UserCheck,
  UserX,
} from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import apiClient from '../api';

type HealthState = 'online' | 'degraded' | 'unknown';
type Probe = { key: string; name: string; hint: string; endpoint: string; state: HealthState; detail: string };
type ManagedUser = { id: number; name: string; status: string; createdAt?: string; approvedAt?: string; registrationIp?: string; lastLoginAt?: string; lastLoginIp?: string; trustedIpCount?: number };

const probes: Omit<Probe, 'state' | 'detail'>[] = [
  { key: 'market', name: '行情数据库', hint: '分钟行情与历史 K 线', endpoint: '/api/v1/stocks/market-data/status' },
  { key: 'intelligence', name: '投资情报', hint: '公告、新闻、天眼查与扩展源', endpoint: '/api/v1/investment-monitor/status' },
  { key: 'essay', name: '知识星球', hint: '增量同步与 AI 结构化', endpoint: '/api/v1/essay-radar/status' },
  { key: 'scheduler', name: '任务调度器', hint: '后台采集与分析调度', endpoint: '/api/v1/system/scheduler/status' },
];

const sectionCopy: Record<string, { eyebrow: string; title: string; description: string }> = {
  '/admin': { eyebrow: 'CONTROL PLANE', title: '运行概览', description: '在这里管理密钥、同步、用户审批与系统运行。' },
  '/admin/data-sources': { eyebrow: 'DATA FABRIC', title: '数据源与渠道', description: '查看各类数据链路是否在线，具体密钥与参数在 API 与模型中维护。' },
  '/admin/sync': { eyebrow: 'SYNC ORCHESTRATION', title: '同步任务', description: '监测自动同步与预计算状态；公开用户无法启动、停止或修改后台任务。' },
  '/admin/access': { eyebrow: 'ACCESS CONTROL', title: '用户与访问', description: '审核访问申请，并查看账号、可信网络与最近登录状态。' },
};

function describePayload(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '接口已响应';
  const value = payload as Record<string, unknown>;
  const candidates = [value.status, value.state, value.message, value.last_success_at, value.updated_at];
  const found = candidates.find((item) => typeof item === 'string' && item.trim());
  return found ? String(found) : '接口已响应';
}

const AdminConsolePage: React.FC = () => {
  const location = useLocation();
  const copy = sectionCopy[location.pathname] ?? sectionCopy['/admin'];
  const isAccessView = location.pathname === '/admin/access';
  const [items, setItems] = useState<Probe[]>(probes.map((probe) => ({ ...probe, state: 'unknown', detail: '等待检测' })));
  const [refreshing, setRefreshing] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string>('尚未检测');
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [userActionId, setUserActionId] = useState<number | null>(null);

  const loadUsers = async () => {
    setUsersLoading(true);
    try {
      const response = await apiClient.get<{ users: ManagedUser[] }>('/api/v1/user-auth/admin/users');
      setUsers(response.data.users ?? []);
    } finally {
      setUsersLoading(false);
    }
  };

  const setUserStatus = async (userId: number, action: 'approve' | 'reject' | 'disable') => {
    setUserActionId(userId);
    try {
      await apiClient.post(`/api/v1/user-auth/admin/users/${userId}/${action}`);
      await loadUsers();
    } finally {
      setUserActionId(null);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    const results = await Promise.allSettled(probes.map((probe) => apiClient.get(probe.endpoint)));
    setItems(probes.map((probe, index) => {
      const result = results[index];
      if (result?.status === 'fulfilled') {
        return { ...probe, state: 'online', detail: describePayload(result.value.data) };
      }
      return { ...probe, state: 'degraded', detail: '当前探测未完成' };
    }));
    setCheckedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
    setRefreshing(false);
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (isAccessView) void loadUsers();
  }, [isAccessView]);

  const onlineCount = useMemo(() => items.filter((item) => item.state === 'online').length, [items]);
  const pendingCount = useMemo(() => users.filter((user) => user.status === 'pending').length, [users]);

  return (
    <div className="admin-console-page">
      <header className="admin-topbar">
        <div><span className="admin-live-dot" />前台用户 <strong>审批后访问</strong></div>
        <div className="admin-top-actions"><span>管理员会话已验证</span><Link to="/" target="_blank">打开前台 <ExternalLink size={14} /></Link></div>
      </header>

      <section className="admin-page-head">
        <div><p>{copy.eyebrow}</p><h1>{copy.title}</h1><span>{copy.description}</span></div>
        <button type="button" onClick={() => void refresh()} disabled={refreshing}>
          <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} />重新探测
        </button>
      </section>

      <section className="admin-metrics" aria-label="管理后台状态摘要">
        <article><Activity /><div><span>在线服务</span><strong>{onlineCount}/{items.length}</strong><small>最后检测 {checkedAt}</small></div></article>
        <article><RadioTower /><div><span>待审申请</span><strong>{pendingCount}</strong><small>姓名 + 密码注册</small></div></article>
        <article><ShieldCheck /><div><span>管理边界</span><strong>PROTECTED</strong><small>设置与操作需管理员会话</small></div></article>
        <article><Bot /><div><span>密钥交付</span><strong>SERVER</strong><small>浏览器不返回 API 明文</small></div></article>
      </section>

      {isAccessView ? (
        <>
          <section className="admin-panel admin-users-panel">
            <div className="admin-panel-title"><div><p>USER APPROVAL QUEUE</p><h2>注册申请与账号</h2></div><button type="button" onClick={() => void loadUsers()} disabled={usersLoading}><RefreshCw size={14} className={usersLoading ? 'animate-spin' : ''} />刷新</button></div>
            <div className="admin-user-table">
              <div className="admin-user-row is-head"><span>用户</span><span>状态</span><span>注册网络</span><span>可信 IP</span><span>最近登录</span><span>操作</span></div>
              {users.map((user) => (
                <div className="admin-user-row" key={user.id}>
                  <span><strong>{user.name}</strong><small>#{user.id} · {user.createdAt ? new Date(user.createdAt).toLocaleString('zh-CN') : '—'}</small></span>
                  <span><b className={`user-status is-${user.status}`}>{user.status === 'pending' ? '待审核' : user.status === 'approved' ? '已批准' : user.status === 'disabled' ? '已停用' : '已拒绝'}</b></span>
                  <span><code>{user.registrationIp || '—'}</code></span>
                  <span>{user.trustedIpCount ?? 0} 个</span>
                  <span><small>{user.lastLoginAt ? new Date(user.lastLoginAt).toLocaleString('zh-CN') : '尚未登录'}<br />{user.lastLoginIp || ''}</small></span>
                  <span className="admin-user-actions">
                    {user.status !== 'approved' && <button type="button" disabled={userActionId === user.id} onClick={() => void setUserStatus(user.id, 'approve')}><UserCheck />批准</button>}
                    {user.status === 'approved' ? <button type="button" className="is-danger" disabled={userActionId === user.id} onClick={() => void setUserStatus(user.id, 'disable')}><UserX />停用</button> : <button type="button" className="is-danger" disabled={userActionId === user.id} onClick={() => void setUserStatus(user.id, 'reject')}><UserX />拒绝</button>}
                  </span>
                </div>
              ))}
              {!usersLoading && users.length === 0 && <div className="admin-users-empty">暂无注册申请</div>}
            </div>
          </section>
          <section className="admin-access-grid">
            <article className="admin-panel admin-security-card">
              <p className="admin-panel-kicker">ACCESS MATRIX</p><h2>访问权限矩阵</h2>
              <div className="admin-access-row"><span>行情、公告、新闻、小作文</span><b className="is-public">批准用户</b></div>
              <div className="admin-access-row"><span>自选股、问股、持仓与个人任务</span><b className="is-public">账号隔离</b></div>
              <div className="admin-access-row"><span>API 密钥、模型和系统参数</span><b className="is-admin">仅管理员</b></div>
              <div className="admin-access-row"><span>用户审批、同步启停、用量审计</span><b className="is-admin">仅管理员</b></div>
            </article>
            <article className="admin-panel admin-security-card">
              <p className="admin-panel-kicker">TRUSTED NETWORK</p><h2>可信 IP 自动识别</h2>
              <p>批准时自动信任注册来源网络；密码登录成功会绑定当前网络。IP 只保存不可逆哈希和脱敏展示，同一 IP 有多个账号时不会自动选择。</p>
              <Link to="/admin/api-models">检查 API 与模型 <KeyRound size={15} /></Link>
            </article>
          </section>
        </>
      ) : (
        <>
          <section className="admin-panel admin-service-panel">
            <div className="admin-panel-title"><div><p>LIVE PROBES</p><h2>服务与数据链路</h2></div><span>{onlineCount === items.length ? '全部探测正常' : '存在待核验链路'}</span></div>
            <div className="admin-service-table" role="table">
              {items.map((item) => (
                <div className="admin-service-row" role="row" key={item.key}>
                  <span className={`admin-state-icon is-${item.state}`}>{item.state === 'online' ? <CheckCircle2 /> : item.state === 'degraded' ? <CircleAlert /> : <Clock3 />}</span>
                  <div><strong>{item.name}</strong><small>{item.hint}</small></div>
                  <code>{item.endpoint}</code>
                  <span className={`admin-state-text is-${item.state}`}>{item.state === 'online' ? '在线' : item.state === 'degraded' ? '待核验' : '检测中'}</span>
                  <em>{item.detail}</em>
                </div>
              ))}
            </div>
          </section>

          <section className="admin-lower-grid">
            <article className="admin-panel admin-quick-panel">
              <p className="admin-panel-kicker">CONFIGURATION</p><h2>管理入口</h2>
              <Link to="/admin/api-models"><KeyRound /><span><strong>API 与模型</strong><small>DeepSeek、Tushare、天眼查及模型路由</small></span></Link>
              <Link to="/admin/usage"><Activity /><span><strong>用量审计</strong><small>模型调用、Token 与最近任务记录</small></span></Link>
              <Link to="/admin/settings"><Database /><span><strong>系统设置</strong><small>数据库、通知、网络和运行参数</small></span></Link>
            </article>
            <article className="admin-panel admin-boundary-panel">
              <p className="admin-panel-kicker">PUBLIC / ADMIN</p><h2>权限边界</h2>
              <div><span className="is-public">USER</span><p><strong>批准用户访问</strong><small>查看共享数据；自选股、问股、持仓与任务按账号隔离</small></p></div>
              <div><span className="is-admin">ADMIN</span><p><strong>控制面登录</strong><small>密钥、同步控制、用量、配置与访问策略</small></p></div>
              <Link to="/admin/access">查看完整权限矩阵</Link>
            </article>
          </section>
        </>
      )}
    </div>
  );
};

export default AdminConsolePage;
