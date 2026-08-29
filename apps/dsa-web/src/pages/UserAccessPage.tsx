import type React from 'react';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, CheckCircle2, KeyRound, LockKeyhole, RadioTower, ShieldCheck, UserRoundPlus } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { useUserAccess } from '../contexts/UserAccessContext';
import './UserAccessPage.css';

type Mode = 'login' | 'register';

const UserAccessPage: React.FC = () => {
  const { login, register } = useUserAccess();
  const [searchParams] = useSearchParams();
  const requestedMode = searchParams.get('mode');
  const [mode, setMode] = useState<Mode>(requestedMode === 'register' ? 'register' : 'login');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setMessage(null);
    const result = mode === 'register' ? await register(name, password) : await login(name, password);
    setBusy(false);
    if (result.success && result.pending) {
      setPending(true);
      setMessage('申请已经进入管理员审核队列。管理员批准后，请使用姓名和密码登录。');
      return;
    }
    if (!result.success) setMessage(result.error?.message ?? '请求未完成，请稍后重试');
  };

  return (
    <main className="user-access-page">
      <div className="user-access-grid" aria-hidden="true" />
      <section className="user-access-story">
        <Link to="/" className="user-access-brand"><span>乐子乌超级价值</span> FINANCIAL INTELLIGENCE</Link>
        <div className="user-access-copy">
          <p className="user-access-kicker"><RadioTower /> PRIVATE DATA WORKSPACE</p>
          <h1>一套共享事实库，<br /><em>每个人拥有独立工作台。</em></h1>
          <p>行情、公告、研报和知识星球由后台统一更新；自选股、问股会话与个人任务只属于当前账号。</p>
          <div className="user-access-features">
            <article><ShieldCheck /><div><strong>管理员审批</strong><span>注册后先审核，再开放数据和 AI 能力</span></div></article>
            <article><LockKeyhole /><div><strong>用户数据隔离</strong><span>服务端按账号分区，不依赖浏览器自报身份</span></div></article>
            <article><KeyRound /><div><strong>会话登录保护</strong><span>必须完成账号登录，共享网络不会自动放行</span></div></article>
          </div>
        </div>
        <small>MARKET DATA · INTELLIGENCE · AI RESEARCH</small>
      </section>

      <section className="user-access-card">
        <div className="user-access-card-head">
          <span>{pending ? <CheckCircle2 /> : mode === 'login' ? <KeyRound /> : <UserRoundPlus />}</span>
          <div><p>ACCESS GATEWAY</p><h2>{pending ? '等待访问批准' : mode === 'login' ? '进入情报台' : '提交访问申请'}</h2></div>
        </div>

        {pending ? (
          <div className="user-access-pending">
            <div className="user-access-pulse"><span /></div>
            <strong>申请已提交</strong>
            <p>{message}</p>
            <button type="button" onClick={() => { setPending(false); setMode('login'); setMessage(null); }}>返回登录</button>
          </div>
        ) : (
          <>
            <div className="user-access-tabs" role="tablist">
              <button type="button" className={mode === 'login' ? 'is-active' : ''} onClick={() => { setMode('login'); setMessage(null); }}>已有账号</button>
              <button type="button" className={mode === 'register' ? 'is-active' : ''} onClick={() => { setMode('register'); setMessage(null); }}>申请注册</button>
            </div>
            <form onSubmit={submit}>
              <label><span>姓名</span><input value={name} onChange={(event) => setName(event.target.value)} autoComplete="username" placeholder="输入姓名" minLength={2} maxLength={40} required /></label>
              <label><span>密码</span><input value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} type="password" placeholder="至少 6 位" minLength={6} maxLength={256} required /></label>
              {message && <p className="user-access-error" role="alert">{message}</p>}
              <button className="user-access-submit" type="submit" disabled={busy}>
                {busy ? '正在处理…' : mode === 'login' ? '进入工作台' : '提交管理员审核'} <ArrowRight />
              </button>
            </form>
            <p className="user-access-note">注册只需要姓名和密码。我们不会在前台展示 API 密钥或系统配置。</p>
            <Link className="user-access-admin-link" to="/admin"><LockKeyhole />管理员入口</Link>
          </>
        )}
      </section>
    </main>
  );
};

export default UserAccessPage;
