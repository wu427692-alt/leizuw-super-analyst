import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, BarChart3, CheckCircle2, KeyRound, LockKeyhole, MessageSquareText, ShieldCheck, Smartphone, UserRoundPlus } from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { useUserAccess } from '../contexts/UserAccessContext';
import { safeUserAccessRedirect } from '../utils/userAccessRedirect';
import '../components/layout/GlassWorkspace.css';
import './UserAccessPage.css';

type Mode = 'login' | 'register' | 'sms';
function normalizePhone(value: string) { return value.replace(/[\s-]/g, '').replace(/^(?:\+86|0086)/, ''); }

const UserAccessPage: React.FC<{ documentNavigation?: boolean }> = ({ documentNavigation = false }) => {
  const access = useUserAccess();
  const [searchParams, setSearchParams] = useSearchParams();
  const policyReady = !access.isLoading && access.statusUnavailable !== true;
  const selfService = policyReady && access.registrationMode === 'self_service';
  const bindingRequested = searchParams.get('bind') === 'phone';
  const binding = bindingRequested && access.loggedIn && !!access.user?.id;
  const [chosenMode, setChosenMode] = useState<Mode | null>(null);
  const initialMode = bindingRequested ? 'login' : selfService ? 'sms' : searchParams.get('mode') === 'register' ? 'register' : 'login';
  const mode = access.statusUnavailable ? 'login' : chosenMode === 'register' && selfService ? 'sms' : chosenMode ?? initialMode;
  const smsMode = binding || mode === 'sms';
  const [name, setName] = useState(''); const [password, setPassword] = useState('');
  const [phone, setPhone] = useState(''); const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<{ id: string; phone: string; expiresAt: number; purpose: 'login' | 'bind' } | null>(null);
  const [retryAt, setRetryAt] = useState(0); const [clock, setClock] = useState(() => Date.now());
  const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const mutationLock = useRef(false);
  const [message, setMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false); const [bound, setBound] = useState(false);
  const [bindAfterLogin, setBindAfterLogin] = useState(bindingRequested);
  const retrySeconds = Math.max(0, Math.ceil((retryAt - clock) / 1000));
  const expired = !!challenge && clock >= challenge.expiresAt;
  const destination = safeUserAccessRedirect(searchParams.get('redirect'));
  const phoneAlreadyBound = binding && (bound || access.user?.phoneBound);
  const smsEnabled = access.smsLoginEnabled === true;

  useEffect(() => { document.title = `${binding ? '绑定手机号' : '登录'} · 乐子乌超级价值`; }, [binding]);
  useEffect(() => {
    if (!retryAt && !challenge) return;
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [retryAt, challenge]);

  const switchMode = (next: Mode) => { setChosenMode(next); setMessage(null); setNotice(null); setCode(''); setChallenge(null); };
  const retryStatus = async () => {
    if (mutationLock.current) return;
    mutationLock.current = true; setBusy(true);
    try { await access.refresh(); }
    finally { mutationLock.current = false; setBusy(false); }
  };
  const sendCode = async () => {
    if (mutationLock.current || retrySeconds || !smsEnabled) return;
    const normalized = normalizePhone(phone);
    if (!/^1[3-9]\d{9}$/.test(normalized)) { setMessage('请输入有效的中国大陆手机号'); return; }
    mutationLock.current = true; setSending(true); setMessage(null); setNotice(null);
    try {
      const purpose = binding ? 'bind' : 'login';
      const result = await access.sendSms(normalized, purpose);
      if (!result.success || !result.challenge) { setMessage(result.error?.message ?? '短信未发送，请稍后重试'); return; }
      const now = Date.now(); setClock(now);
      setChallenge({ id: result.challenge.challengeId, phone: normalized, purpose, expiresAt: now + result.challenge.expiresIn * 1000 });
      setRetryAt(now + result.challenge.retryAfter * 1000); setCode('');
      setNotice('验证码已发送，请查看手机短信。');
    } catch { setMessage('短信未发送，请稍后重试'); }
    finally { mutationLock.current = false; setSending(false); }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (mutationLock.current) return;
    if (smsMode && (!smsEnabled || !challenge || expired || challenge.phone !== normalizePhone(phone) || challenge.purpose !== (binding ? 'bind' : 'login') || !/^\d{6}$/.test(code))) {
      setMessage(!smsEnabled ? '短信服务暂不可用，已有账号可使用姓名和密码登录' : expired ? '验证码已过期，请重新获取' : '请先获取验证码，再输入短信中的 6 位数字'); return;
    }
    mutationLock.current = true; setBusy(true); setMessage(null); setNotice(null);
    try {
      if (smsMode && challenge) {
        const payload = { phone: challenge.phone, code, challengeId: challenge.id };
        const result = binding ? await access.bindPhone({ ...payload, currentPassword: password }) : await access.smsLogin({ ...payload, ...(name.trim() ? { name: name.trim() } : {}) });
        if (!result.success) setMessage(result.error?.message ?? '验证未完成，请稍后重试');
        else { setCode(''); setPassword(''); setChallenge(null); if (binding) setBound(true); }
      } else {
        if (selfService && mode === 'register') { setMessage('请使用手机号验证码注册'); return; }
        if (selfService && mode === 'login' && bindAfterLogin) {
          const params = new URLSearchParams(searchParams); params.set('bind', 'phone'); params.set('mode', 'login'); setSearchParams(params, { replace: true });
        }
        const result = mode === 'register' ? await access.register(name, password) : await access.login(name, password);
        if (result.success && result.pending) { setPending(true); setMessage('申请已经进入管理员审核队列。管理员批准后，请使用姓名和密码登录。'); }
        else if (!result.success) setMessage(result.error?.message ?? '请求未完成，请稍后重试');
        else setPassword('');
      }
    } finally { mutationLock.current = false; setBusy(false); }
  };

  return <main className="user-access-page">
    <section className="user-access-story">
      <Link to="/" reloadDocument={documentNavigation} className="user-access-brand"><span><BarChart3 aria-hidden="true" /></span><strong>乐子乌超级价值</strong></Link>
      <div className="user-access-copy"><p className="user-access-kicker">证据驱动的投资研究</p><h1>共享同一套事实，<br /><em>保留自己的研究路径。</em></h1><p>行情、公告、研报与机构语料持续更新；自选股、问股会话、调研与量化任务，按你的账号独立保存。</p>
        <div className="user-access-features">{policyReady && <article>{selfService ? <Smartphone /> : <ShieldCheck />}<div><strong>{selfService ? '手机号验证，即刻使用' : '管理员审批'}</strong><span>{selfService ? '未注册的手机号验证成功后自动创建账号，无需等待审核' : '注册后先审核，再开放数据和 AI 能力'}</span></div></article>}<article><LockKeyhole /><div><strong>个人数据独立保存</strong><span>账号之间隔离，换设备仍可继续自己的研究</span></div></article><article><KeyRound /><div><strong>原有账号继续使用</strong><span>{selfService ? '先用原密码登录，再绑定手机，原有数据不会迁移或丢失' : '完成账号登录后访问，共享网络不会自动放行'}</span></div></article></div>
      </div><small>机会发现 · 个股研究 · 信息检索 · 量化验证</small>
    </section>
    <section className="user-access-card" aria-busy={busy || sending || access.isLoading}>
      <div className="user-access-card-head"><span>{access.isLoading ? <ShieldCheck /> : pending || phoneAlreadyBound ? <CheckCircle2 /> : smsMode ? <Smartphone /> : mode === 'login' ? <KeyRound /> : <UserRoundPlus />}</span><div><p>{binding ? '保留当前账号与全部数据' : '用户访问'}</p><h2>{access.isLoading ? '正在准备登录' : phoneAlreadyBound ? '手机号已绑定' : binding ? '绑定手机号' : pending ? '等待访问批准' : mode === 'register' ? '提交访问申请' : '进入研究工作台'}</h2></div></div>
      {access.isLoading ? <p className="user-access-notice" role="status">正在读取登录方式…</p> : phoneAlreadyBound ? <div className="user-access-pending"><strong>{access.user?.name}，绑定成功</strong><p>{access.user?.phoneMasked ? `已绑定 ${access.user.phoneMasked}。` : ''}以后可使用手机号和短信验证码登录，原有自选股、会话和任务仍在当前账号中。</p><Link className="user-access-submit" to={destination} reloadDocument={documentNavigation}>进入工作台<ArrowRight /></Link></div> : pending ? <div className="user-access-pending"><CheckCircle2 /><strong>申请已提交</strong><p>{message}</p><button type="button" onClick={() => { setPending(false); setChosenMode('login'); setMessage(null); }}>返回登录</button></div> : <>
        {access.statusUnavailable && <div className="user-access-service-notice"><p role="status">暂时无法读取登录方式。已有账号可使用姓名和密码登录，或重试读取。</p><button className="user-access-existing" type="button" disabled={busy} onClick={() => void retryStatus()}>{busy ? '正在重试…' : '重试读取登录方式'}</button></div>}
        {!binding && policyReady && <nav className="user-access-tabs" aria-label="登录方式">{selfService ? <><button type="button" disabled={busy || sending} aria-pressed={mode === 'sms'} className={mode === 'sms' ? 'is-active' : ''} onClick={() => switchMode('sms')}>手机号登录 / 注册</button><button type="button" disabled={busy || sending} aria-pressed={mode === 'login'} className={mode === 'login' ? 'is-active' : ''} onClick={() => switchMode('login')}>原账号密码登录</button></> : <><button type="button" className={mode === 'login' ? 'is-active' : ''} onClick={() => switchMode('login')}>已有账号</button><button type="button" className={mode === 'register' ? 'is-active' : ''} onClick={() => switchMode('register')}>申请注册</button></>}</nav>}
        {binding && <p className="user-access-bind-context">正在为 <strong>{access.user?.name}</strong> 绑定手机号。这不是新注册，不会创建空白账号。</p>}
        {smsMode && !smsEnabled && <p className="user-access-service-notice" role="status">短信服务暂未就绪，暂不能发送验证码。已有账号仍可使用姓名和密码登录。</p>}
        {!binding && mode === 'sms' && <p className="user-access-form-intro">验证码验证成功后即可登录；未注册的手机号将自动注册，无需管理员审批。</p>}
        {!binding && selfService && mode === 'sms' && <button className="user-access-existing" type="button" onClick={() => switchMode('login')}><KeyRound size={15} />已有姓名密码账号？请先登录再绑定，保留原有数据</button>}
        <form onSubmit={submit}>
          {binding && <label><span>原账号密码</span><input aria-label="原账号密码" aria-describedby="binding-password-help" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} placeholder="再次验证原账号密码" minLength={6} maxLength={256} required disabled={busy} /><small id="binding-password-help">确认是你本人在绑定，不会更改原密码。</small></label>}
          {smsMode ? <><label><span>手机号</span><div className="user-access-phone"><span>+86</span><input aria-label="手机号" type="tel" inputMode="tel" autoComplete="tel-national" placeholder="中国大陆手机号" maxLength={16} required disabled={busy || sending || !smsEnabled} value={phone} onChange={event => { setPhone(event.target.value); setChallenge(null); setCode(''); setMessage(null); setNotice(null); }} /></div></label><label><span>短信验证码</span><div className="user-access-code"><input aria-label="短信验证码" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} placeholder="6 位数字" required disabled={busy || !smsEnabled} /><button type="button" disabled={busy || sending || retrySeconds > 0 || !smsEnabled} onClick={() => void sendCode()}>{sending ? '发送中…' : retrySeconds > 0 ? `${retrySeconds} 秒后重发` : challenge ? '重新获取' : '获取验证码'}</button></div></label>{!binding && <label><span>姓名 <small>首次使用可填写</small></span><input value={name} onChange={event => setName(event.target.value)} autoComplete="name" placeholder="可选，方便在平台中识别" minLength={2} maxLength={40} disabled={busy} /></label>}{expired && <p className="user-access-expired">验证码已过期，请重新获取。</p>}{notice && <p className="user-access-notice" role="status"><MessageSquareText size={15} />{notice}</p>}</> : <><label><span>姓名</span><input value={name} onChange={event => setName(event.target.value)} autoComplete="username" placeholder="输入姓名" minLength={2} maxLength={40} required disabled={busy} /></label><label><span>密码</span><input value={password} onChange={event => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} type="password" placeholder="至少 6 位" minLength={6} maxLength={256} required disabled={busy} /></label>{selfService && <label className="user-access-bind-option"><input type="checkbox" checked={bindAfterLogin} onChange={event => setBindAfterLogin(event.target.checked)} disabled={busy} /><span>登录后绑定手机号，保留这个账号的原有数据</span></label>}</>}
          {message && <p className="user-access-error" role="alert">{message}</p>}
          <button className="user-access-submit" type="submit" disabled={busy || sending || (smsMode && (!smsEnabled || !challenge || expired))}>{busy ? '正在处理…' : binding ? '验证并绑定手机号' : mode === 'sms' ? '验证并登录 / 注册' : mode === 'login' ? selfService && bindAfterLogin ? '登录并绑定手机号' : '进入工作台' : '提交管理员审核'}<ArrowRight /></button>
        </form>
        {binding ? <Link className="user-access-skip" to={destination} reloadDocument={documentNavigation}>暂不绑定，继续使用原账号</Link> : <p className="user-access-note">{access.statusUnavailable ? '已有账号可继续使用原姓名和密码登录。' : selfService ? '短信仅用于身份验证。验证码不会保存在浏览器中。' : '注册只需要姓名和密码。我们不会在前台展示 API 密钥或系统配置。'}</p>}
        <Link className="user-access-admin-link" to="/admin" reloadDocument={documentNavigation}><LockKeyhole />管理员入口</Link>
      </>}
    </section>
  </main>;
};
export default UserAccessPage;
