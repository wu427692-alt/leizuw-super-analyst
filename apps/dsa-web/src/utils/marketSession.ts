const SHANGHAI_CLOCK = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit',
  hour12: false, hourCycle: 'h23',
});

/**
 * Cheap client-side guard for continuous A-share polling.
 *
 * Activation refreshes still run outside this window, so users always receive
 * the latest stored close when they enter or return to a page. The small edge
 * buffers also capture opening and closing snapshots.
 */
export function isAshareLiveWindow(now = new Date()): boolean {
  const parts = Object.fromEntries(
    SHANGHAI_CLOCK.formatToParts(now).map(part => [part.type, part.value]),
  );
  if (!['Mon', 'Tue', 'Wed', 'Thu', 'Fri'].includes(parts.weekday ?? '')) return false;
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return false;
  const clockMinutes = hour * 60 + minute;
  return (clockMinutes >= 9 * 60 + 25 && clockMinutes <= 11 * 60 + 35)
    || (clockMinutes >= 12 * 60 + 55 && clockMinutes <= 15 * 60 + 5);
}
