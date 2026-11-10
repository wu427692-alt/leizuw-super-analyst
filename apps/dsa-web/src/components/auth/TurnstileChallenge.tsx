import { useEffect, useRef, useState } from 'react';
import { loadTurnstile } from '../../utils/turnstile';
import type { TurnstileApi } from '../../utils/turnstile';

type State = 'loading' | 'waiting' | 'verified' | 'expired' | 'failed';

/** Visible verification, including actionable failures instead of an empty slot. */
export default function TurnstileChallenge({ siteKey, onTokenChange }: {
  siteKey: string;
  onTokenChange: (token: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<State>('loading');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let api: TurnstileApi | undefined;
    let widgetId: string | undefined;
    onTokenChange(null);
    loadTurnstile().then(loaded => {
      if (!active || !container.current) return;
      api = loaded;
      const width = container.current.getBoundingClientRect().width;
      setState('waiting');
      widgetId = loaded.render(container.current, {
        sitekey: siteKey,
        size: width > 0 && width < 300 ? 'compact' : 'flexible',
        theme: 'auto', language: 'zh-CN', appearance: 'always',
        retry: 'never', 'refresh-expired': 'manual', 'refresh-timeout': 'manual', 'response-field': false,
        callback: token => { if (active) { onTokenChange(token); setState('verified'); } },
        'expired-callback': () => { if (active) { onTokenChange(null); setState('expired'); } },
        'timeout-callback': () => { if (active) { onTokenChange(null); setState('expired'); } },
        'error-callback': () => { if (active) { onTokenChange(null); setState('failed'); } return true; },
      });
    }).catch(() => { if (active) { onTokenChange(null); setState('failed'); } });
    return () => {
      active = false;
      onTokenChange(null);
      if (widgetId !== undefined && api) api.remove(widgetId);
    };
  }, [siteKey, onTokenChange, attempt]);

  const retry = () => { onTokenChange(null); setState('loading'); setAttempt(value => value + 1); };
  return <section className="user-access-captcha" aria-label="人机验证">
    <span>安全验证</span>
    <div ref={container} className="user-access-captcha-widget" />
    <p role={state === 'failed' || state === 'expired' ? 'alert' : 'status'}>
      {state === 'loading' ? '正在加载人机验证…' : state === 'verified' ? '人机验证已通过。' : state === 'expired' ? '人机验证已过期，请重新验证。' : state === 'failed' ? '人机验证未能加载或未通过，请重新加载。已有账号可改用密码登录。' : '请完成上方人机验证，再继续提交。'}
    </p>
    {state !== 'loading' && state !== 'verified' && <button type="button" onClick={retry}>重新加载人机验证</button>}
  </section>;
}
