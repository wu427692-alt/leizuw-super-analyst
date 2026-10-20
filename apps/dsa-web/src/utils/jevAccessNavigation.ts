/** Only the independently served Jev document may receive this auth handoff. */
export function getJevReturnTarget(search: string): string {
  const target = new URLSearchParams(search).get('redirect') ?? '';
  const path = target.split(/[?#]/, 1)[0];
  if ((path !== '/jev-backtest' && path !== '/jev-backtest/') || [...target].some(char => char === '\\' || char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return '/jev-backtest';
  return target;
}

export const jevDocumentNavigation = {
  replace(target: string): void {
    window.location.replace(target);
  },
};
