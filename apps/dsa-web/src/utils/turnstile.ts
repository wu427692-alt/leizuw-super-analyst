export type TurnstileOptions = {
  sitekey: string;
  size: 'flexible' | 'compact';
  theme: 'auto';
  language: string;
  appearance: 'always';
  retry: 'never';
  'refresh-expired': 'manual';
  'refresh-timeout': 'manual';
  'response-field': false;
  callback: (token: string) => void;
  'expired-callback': () => void;
  'timeout-callback': () => void;
  'error-callback': (code: string) => boolean;
};

export type TurnstileApi = {
  render: (element: HTMLElement, options: TurnstileOptions) => string;
  remove: (widgetId: string) => void;
};

declare global { interface Window { turnstile?: TurnstileApi } }

const SCRIPT_ID = 'leziwu-turnstile-script';
let pending: Promise<TurnstileApi> | undefined;

/** Load only Cloudflare's official script; failed loads may be explicitly retried. */
export function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (pending) return pending;
  const script = document.createElement('script');
  script.id = SCRIPT_ID;
  script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
  script.async = true;
  script.defer = true;
  pending = new Promise<TurnstileApi>((resolve, reject) => {
    const finish = (error?: Error) => {
      window.clearTimeout(timer);
      script.removeEventListener('load', onLoad);
      script.removeEventListener('error', onError);
      if (error) reject(error);
      else if (window.turnstile) resolve(window.turnstile);
      else reject(new Error('Turnstile API unavailable'));
    };
    const onLoad = () => finish();
    const onError = () => finish(new Error('Turnstile script load failed'));
    const timer = window.setTimeout(() => finish(new Error('Turnstile script load timeout')), 15000);
    script.addEventListener('load', onLoad);
    script.addEventListener('error', onError);
    document.head.appendChild(script);
  }).catch(error => {
    script.remove();
    pending = undefined;
    throw error;
  });
  return pending;
}
