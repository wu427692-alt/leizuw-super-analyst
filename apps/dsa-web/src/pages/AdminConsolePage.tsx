import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  ExternalLink,
  KeyRound,
  RefreshCw,
  UserCheck,
  UserX,
} from 'lucide-react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import apiClient from '../api';
import { BarMeter, MetricRow, PageTabs, ShareBar } from '../components/common';

type HealthState = 'online' | 'degraded' | 'unknown';
type Probe = {
  key: string;
  name: string;
  hint: string;
  endpoint: string;
  state: HealthState;
  detail: string;
  latencyMs?: number | null;
};
type ManagedUser = { id: number; name: string; status: string; createdAt?: string; approvedAt?: string; registrationIp?: string; lastLoginAt?: string; lastLoginIp?: string; trustedIpCount?: number };
type AccessTab = 'queue' | 'boundary';

const probes: Omit<Probe, 'state' | 'detail'>[] = [
  { key: 'market', name: '行情数据库', hint: '分钟行情与历史 K 线', endpoint: '/api/v1/stocks/market-data/status' },
  { key: 'intelligence', name: '投资情报', hint: '公告、新闻、天眼查与扩展源', endpoint: '/api/v1/investment-monitor/status' },
  { key: 'essay', name: '知识星球', hint: '增量同步与 AI 结构化', endpoint: '/api/v1/essay-radar/status' },
  { key: 'scheduler', name: '任务调度器', hint: '后台采集与分析调度', endpoint: '/api/v1/system/scheduler/status' },
];

const sections = [
  { key: '/admin', label: '概览' },
  { key: '/admin/data-sources', label: '数据源' },
  { key: '/admin/sync', label: '同步任务' },
  { key: '/admin/access', label: '用户与访问' },
];

const sectionCopy: Record<string, { eyebrow: string; title: string; description: string }> = {
  '/admin': { eyebrow: 'CONTROL PLANE', title: '运行概览', description: '一眼看清服务在线情况、待审申请与常用管理入口。' },
  '/admin/data-sources': { eyebrow: 'DATA FABRIC', title: '数据源与渠道', description: '逐条核对数据链路；密钥与参数在 API 与模型中维护。' },
  '/admin/sync': { eyebrow: 'SYNC ORCHESTRATION', title: '同步任务', description: '查看后台调度是否在跑；普通用户不能启动或停止任务。' },
  '/admin/access': { eyebrow: 'ACCESS CONTROL', title: '用户与访问', description: '审核访问申请，核对账号状态与权限边界。' },
};

const STATE_LABEL: Record<HealthState, string> = { online: '在线', degraded: '待核验', unknown: '检测中' };

function describePayload(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '接口已响应';
  const value = payload as Record<string, unknown>;
  const candidates = [value.status, value.state, value.message, value.last_success_at, value.updated_at];
  const found = candidates.find((item) => typeof item === 'string' && item.trim());
  return found ? String(found) : '接口已响应';
}

const AdminConsolePage: React.FC = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const section = sections.find((item) => item.key === location.pathname)?.key ?? '/admin';
  const copy = sectionCopy[section] ?? sectionCopy['/admin'];
  const isAccessView = section === '/admin/access';
  const [items, setItems] = useState<Probe[]>(probes.map((probe) => ({ ...probe, state: 'unknown', detail: '等待检测', latencyMs: null })));
  const [refreshing, setRefreshing] = useState(false);
  const [checkedAt, setCheckedAt] = useState<string>('尚未检测');
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [userActionId, setUserActionId] = useState<number | null>(null);
  const [accessTab, setAccessTab] = useState<AccessTab>('queue');

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const response = await apiClient.get<{ users: ManagedUser[] }>('/api/v1/user-auth/admin/users');
      setUsers(response.data.users ?? []);
    } finally {
      setUsersLoading(false);
    }
  }, []);

  const setUserStatus = async (userId: number, action: 'approve' | 'reject' | 'disable') => {
    setUserActionId(userId);
    try {
      await apiClient.post(`/api/v1/user-auth/admin/users/${userId}/${action}`);
      await loadUsers();
    } finally {
      setUserActionId(null);
    }
  };

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const startedAt = Date.now();
    const results = await Promise.allSettled(probes.map((probe) => apiClient.get(probe.endpoint)));
    setItems(probes.map((probe, index) => {
      const result = results[index];
      if (result?.status === 'fulfilled') {
        return { ...probe, state: 'online', detail: describePayload(result.value.data), latencyMs: Date.now() - startedAt };
      }
      return { ...probe, state: 'degraded', detail: '当前探测未完成', latencyMs: null };
    }));
    setCheckedAt(new Date().toLocaleTimeString('zh-CN', { hour12: false }));
    setRefreshing(false);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  const onlineCount = useMemo(() => items.filter((item) => item.state === 'online').length, [items]);
  const degradedCount = useMemo(() => items.filter((item) => item.state !== 'online').length, [items]);
  const pendingCount = useMemo(() => users.filter((user) => user.status === 'pending').length, [users]);
  const approvedCount = useMemo(() => users.filter((user) => user.status === 'approved').length, [users]);
  const disabledCount = useMemo(() => users.filter((user) => user.status === 'disabled').length, [users]);
  const rejectedCount = useMemo(() => users.filter((user) => user.status === 'rejected').length, [users]);
  const latencyMax = Math.max(1, ...items.map((item) => item.latencyMs ?? 0));
  const schedulerProbe = items.find((item) => item.key === 'scheduler');

  const renderProbeRows = (withEndpoint: boolean) => (
    <div className="admin-service-table" role="table">
      {items.map((item) => (
        <div className="admin-service-row" role="row" key={item.key}>
          <span className={`admin-state-icon is-${item.state}`}>{item.state === 'online' ? <CheckCircle2 /> : item.state === 'degraded' ? <CircleAlert /> : <Clock3 />}</span>
          <div><strong>{item.name}</strong><small>{item.hint}</small></div>
          {withEndpoint ? <code>{item.endpoint}</code> : <span className="text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{item.latencyMs == null ? '—' : `${item.latencyMs}ms`}</span>}
          <span className={`admin-state-text is-${item.state}`}>{STATE_LABEL[item.state]}</span>
          <em>{item.detail}</em>
        </div>
      ))}
    </div>
  );

  const statusShare = (
    <div className="mb-[var(--gap-md)] flex items-center gap-[var(--gap-sm)]">
      <span className="shrink-0 text-[length:var(--fs-caption)] text-[color:var(--text-3)]">服务状态构成</span>
      <ShareBar
        className="max-w-[220px]"
        ariaLabel="服务状态构成"
        segments={[
          { key: 'online', value: onlineCount, label: '在线' },
          { key: 'degraded', value: degradedCount, color: 'var(--warning)', label: '待核验' },
        ]}
      />
      <span className="shrink-0 text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">在线 {onlineCount} / {items.length}</span>
    </div>
  );

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

      <PageTabs
        ariaLabel="管理后台视图切换"
        value={section}
        onChange={(key) => navigate(key)}
        items={sections.map((item) => ({ key: item.key, label: item.label }))}
      />

      {section === '/admin' ? (
        <>
          <MetricRow
            className="mb-[var(--gap-md)]"
            columns={4}
            items={[
              { label: '在线服务', value: `${onlineCount}/${items.length}`, hint: `最后检测 ${checkedAt}` },
              { label: '待审申请', value: pendingCount, hint: '姓名 + 密码注册' },
              { label: '已批准账号', value: approvedCount, hint: `共 ${users.length} 个账号` },
              { label: '管理边界', value: 'PROTECTED', hint: '设置与操作需管理员会话' },
            ]}
          />
          {statusShare}
          <section className="admin-panel admin-service-panel">
            <div className="admin-panel-title">
              <div><p>LIVE PROBES</p><h2>服务状态</h2></div>
              <span className="flex items-center gap-[var(--gap-sm)]">
                {degradedCount === 0 ? '全部探测正常' : '存在待核验链路'}
                <Link to="/admin/data-sources" className="underline underline-offset-4">完整链路</Link>
              </span>
            </div>
            <div className="flex flex-col px-[var(--gap-md)]">
              {items.map((item) => (
                <div key={item.key} className="flex items-center gap-[var(--gap-sm)] border-t border-[color:var(--line-1)] py-[var(--gap-xs)] first:border-t-0">
                  <span className={`admin-state-icon is-${item.state}`}>{item.state === 'online' ? <CheckCircle2 /> : item.state === 'degraded' ? <CircleAlert /> : <Clock3 />}</span>
                  <span className="min-w-0 flex-1 truncate text-[length:var(--fs-sm)] text-[color:var(--text-1)]">{item.name}</span>
                  <span className="truncate text-[length:var(--fs-caption)] text-[color:var(--text-3)]">{item.hint}</span>
                  <span className={`admin-state-text is-${item.state}`}>{STATE_LABEL[item.state]}</span>
                  <span className="w-14 shrink-0 text-right text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{item.latencyMs == null ? '—' : `${item.latencyMs}ms`}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="admin-panel admin-quick-panel">
            <p className="admin-panel-kicker">CONFIGURATION</p><h2>管理入口</h2>
            <Link to="/admin/api-models"><KeyRound /><span><strong>API 与模型</strong><small>DeepSeek、Tushare、天眼查及模型路由</small></span></Link>
            <Link to="/admin/usage"><Activity /><span><strong>用量审计</strong><small>模型调用、Token 与最近任务记录</small></span></Link>
            <Link to="/admin/settings"><Database /><span><strong>系统设置</strong><small>数据库、通知、网络和运行参数</small></span></Link>
            <Link to="/admin/access"><UserCheck /><span><strong>用户与访问</strong><small>{pendingCount} 个申请等待处理</small></span></Link>
          </section>
        </>
      ) : null}

      {section === '/admin/data-sources' ? (
        <>
          <MetricRow
            className="mb-[var(--gap-md)]"
            columns={4}
            items={[
              { label: '在线服务', value: onlineCount, hint: `共 ${items.length} 个探针` },
              { label: '待核验', value: degradedCount, trend: degradedCount > 0 ? 'down' : 'flat' },
              { label: '探测次数', value: items.length, hint: `最后检测 ${checkedAt}` },
              { label: '管理边界', value: 'SERVER', hint: '浏览器不返回 API 明文' },
            ]}
          />
          {statusShare}
          <section className="admin-panel admin-service-panel">
            <div className="admin-panel-title"><div><p>LIVE PROBES</p><h2>服务与数据链路</h2></div><span>{degradedCount === 0 ? '全部探测正常' : '存在待核验链路'}</span></div>
            {renderProbeRows(true)}
          </section>
          <section className="admin-panel admin-quick-panel">
            <p className="admin-panel-kicker">PROBE LATENCY</p><h2>本轮探测耗时</h2>
            <div className="flex flex-col gap-[var(--gap-xs)]">
              {items.map((item) => (
                <div key={item.key} className="flex items-center gap-[var(--gap-sm)]">
                  <span className="w-28 shrink-0 truncate text-[length:var(--fs-sm)] text-[color:var(--text-2)]">{item.name}</span>
                  <BarMeter value={item.latencyMs ?? 0} max={latencyMax} height={4} tone="neutral" ariaLabel={`${item.name} 探测耗时 ${item.latencyMs ?? 0} 毫秒`} />
                  <span className="w-16 shrink-0 text-right text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{item.latencyMs == null ? '—' : `${item.latencyMs}ms`}</span>
                </div>
              ))}
            </div>
            <p className="mt-[var(--gap-sm)] text-[length:var(--fs-caption)] text-[color:var(--text-3)]">密钥与参数在「API 与模型」「系统设置」中维护，这里只读探测结果。</p>
          </section>
        </>
      ) : null}

      {section === '/admin/sync' ? (
        <>
          <MetricRow
            className="mb-[var(--gap-md)]"
            columns={3}
            items={[
              { label: '调度器', value: schedulerProbe ? STATE_LABEL[schedulerProbe.state] : null, hint: schedulerProbe?.detail },
              { label: '探测耗时', value: schedulerProbe?.latencyMs == null ? null : `${schedulerProbe.latencyMs}ms`, hint: `最后检测 ${checkedAt}` },
              { label: '在线服务', value: `${onlineCount}/${items.length}`, hint: '全部探针' },
            ]}
          />
          <section className="admin-panel admin-service-panel">
            <div className="admin-panel-title"><div><p>SCHEDULER</p><h2>任务调度器</h2></div><span>{schedulerProbe ? STATE_LABEL[schedulerProbe.state] : '检测中'}</span></div>
            <div className="admin-service-table" role="table">
              {schedulerProbe ? (
                <div className="admin-service-row" role="row">
                  <span className={`admin-state-icon is-${schedulerProbe.state}`}>{schedulerProbe.state === 'online' ? <CheckCircle2 /> : schedulerProbe.state === 'degraded' ? <CircleAlert /> : <Clock3 />}</span>
                  <div><strong>{schedulerProbe.name}</strong><small>{schedulerProbe.hint}</small></div>
                  <code>{schedulerProbe.endpoint}</code>
                  <span className={`admin-state-text is-${schedulerProbe.state}`}>{STATE_LABEL[schedulerProbe.state]}</span>
                  <em>{schedulerProbe.detail}</em>
                </div>
              ) : null}
            </div>
          </section>
          <section className="admin-panel admin-boundary-panel">
            <p className="admin-panel-kicker">BOUNDARY</p><h2>同步任务边界</h2>
            <div><span className="is-admin">ADMIN</span><p><strong>查看状态</strong><small>调度、预计算与游标状态在本页只读展示。</small></p></div>
            <div><span className="is-public">USER</span><p><strong>不能启停</strong><small>普通用户无法开始、停止或修改后台任务。</small></p></div>
            <Link to="/admin/data-sources">查看数据链路探测 <ExternalLink size={14} /></Link>
          </section>
        </>
      ) : null}

      {isAccessView ? (
        <>
          <MetricRow
            className="mb-[var(--gap-md)]"
            columns={4}
            items={[
              { label: '待审申请', value: pendingCount, hint: '姓名 + 密码注册' },
              { label: '已批准', value: approvedCount },
              { label: '已停用', value: disabledCount, trend: disabledCount > 0 ? 'down' : 'flat' },
              { label: '账号总数', value: users.length },
            ]}
          />
          <div className="mb-[var(--gap-md)] flex items-center gap-[var(--gap-sm)]">
            <span className="shrink-0 text-[length:var(--fs-caption)] text-[color:var(--text-3)]">账号状态构成</span>
            <ShareBar
              className="max-w-[260px]"
              ariaLabel="账号状态构成"
              segments={[
                { key: 'pending', value: pendingCount, color: 'var(--warning)', label: '待审核' },
                { key: 'approved', value: approvedCount, label: '已批准' },
                { key: 'disabled', value: disabledCount, color: 'var(--text-4)', label: '已停用' },
                { key: 'rejected', value: rejectedCount, color: 'var(--danger)', label: '已拒绝' },
              ]}
            />
            <span className="shrink-0 text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">待审核 {pendingCount} / 已批准 {approvedCount} / 停用 {disabledCount} / 拒绝 {rejectedCount}</span>
          </div>

          <PageTabs
            ariaLabel="用户与访问分支"
            value={accessTab}
            onChange={(key) => setAccessTab(key as AccessTab)}
            items={[
              { key: 'queue', label: '审批队列', badge: pendingCount },
              { key: 'boundary', label: '权限边界' },
            ]}
          />

          {accessTab === 'queue' ? (
            <section className="admin-panel admin-users-panel">
              <div className="admin-panel-title"><div><p>USER APPROVAL QUEUE</p><h2>注册申请与账号</h2></div><button type="button" onClick={() => void loadUsers()} disabled={usersLoading}><RefreshCw size={14} className={usersLoading ? 'animate-spin' : ''} />刷新</button></div>
              <div className="admin-user-table">
                <div className="admin-user-row is-head"><span>用户</span><span>状态</span><span>注册网络</span><span>网络记录</span><span>最近登录</span><span>操作</span></div>
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
          ) : (
            <section className="admin-access-grid">
              <article className="admin-panel admin-security-card">
                <p className="admin-panel-kicker">ACCESS MATRIX</p><h2>访问权限矩阵</h2>
                <div className="admin-access-row"><span>行情、公告、新闻、小作文</span><b className="is-public">批准用户</b></div>
                <div className="admin-access-row"><span>自选股、问股与个人任务</span><b className="is-public">账号隔离</b></div>
                <div className="admin-access-row"><span>API 密钥、模型和系统参数</span><b className="is-admin">仅管理员</b></div>
                <div className="admin-access-row"><span>用户审批、同步启停、用量审计</span><b className="is-admin">仅管理员</b></div>
              </article>
              <article className="admin-panel admin-security-card">
                <p className="admin-panel-kicker">SESSION SECURITY</p><h2>账号会话强制验证</h2>
                <p>批准账号不会自动登录。每个浏览器必须使用姓名和密码建立有效会话；网络信息仅做脱敏审计，不再作为身份凭证，共享网络无法绕过注册与登录。</p>
                <Link to="/admin/api-models">检查 API 与模型 <KeyRound size={15} /></Link>
              </article>
            </section>
          )}
        </>
      ) : null}

      <p className="mt-[var(--gap-md)] flex items-center gap-[var(--gap-xs)] text-[length:var(--fs-caption)] text-[color:var(--text-3)]">
        <Bot size={14} />管理后台只读展示运行状态；密钥与配置在各自页面维护。
      </p>
    </div>
  );
};

export default AdminConsolePage;
