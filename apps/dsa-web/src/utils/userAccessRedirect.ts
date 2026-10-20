/** Keep account callbacks inside the product and avoid login redirect loops. */
export function safeUserAccessRedirect(value: string | null | undefined, fallback = '/app'): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\')
    || [...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return fallback;
  try {
    const target = new URL(value, 'https://account-route.invalid');
    if (target.origin !== 'https://account-route.invalid' || target.pathname.replace(/\/+$/, '') === '/access') return fallback;
    return target.pathname + target.search + target.hash;
  } catch {
    return fallback;
  }
}
