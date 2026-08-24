const COMPACT_DATE = /^(\d{4})[-/]?(\d{2})[-/]?(\d{2})/;

export function quoteDateKey(value?: string | null): string | null {
  if (!value) return null;
  const match = String(value).trim().match(COMPACT_DATE);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return shanghaiDateKey(parsed);
}

export function shanghaiDateKey(date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function isCurrentShanghaiQuote(value?: string | null, now = new Date()): boolean {
  return quoteDateKey(value) === shanghaiDateKey(now);
}

export type MarketQuoteSession = {
  dateKey: string | null;
  label: string;
  canShowChange: boolean;
  isCurrentDay: boolean;
  isClose: boolean;
};

function explicitClock(value?: string | null): string | null {
  if (!value) return null;
  const match = String(value).trim().match(/[T\s](\d{2}):(\d{2})(?::\d{2})?/);
  return match ? `${match[1]}:${match[2]}` : null;
}

/**
 * Prefer a candidate quote when it belongs to a newer trading day.  On the
 * same trading day a stale candidate must not overwrite an official/current
 * value, but a newer-day partial snapshot is still more truthful than showing
 * yesterday's close as though it were today's market.
 */
export function shouldPreferQuote(
  candidateAt?: string | null,
  currentAt?: string | null,
  candidateIsStale = false,
): boolean {
  const candidateDate = quoteDateKey(candidateAt);
  const currentDate = quoteDateKey(currentAt);
  if (!candidateDate) return false;
  if (!currentDate) return true;
  if (candidateDate !== currentDate) return candidateDate > currentDate;
  return !candidateIsStale;
}

function shanghaiClock(now: Date): { weekday: number; minutes: number } {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const weekdays: Record<string, number> = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 0 };
  return {
    weekday: weekdays[values.weekday] ?? 0,
    minutes: Number(values.hour) * 60 + Number(values.minute),
  };
}

/**
 * Give every quote an explicit, auditable session label.
 *
 * Daily APIs are always close data. A same-day snapshot is "实时" only during
 * the A-share trading session; after 15:00 it becomes that date's close. A
 * previous trading day is still valid close data, so its daily change may be
 * displayed as long as the date itself is present.
 */
export function marketQuoteSession(
  value?: string | null,
  source?: string | null,
  now = new Date(),
): MarketQuoteSession {
  const dateKey = quoteDateKey(value);
  if (!dateKey) {
    return {
      dateKey: null,
      label: '交易日未标注',
      canShowChange: false,
      isCurrentDay: false,
      isClose: false,
    };
  }

  const isCurrentDay = dateKey === shanghaiDateKey(now);
  const { weekday, minutes } = shanghaiClock(now);
  const isTradingDay = weekday >= 1 && weekday <= 5;
  const isIntradayWindow = isTradingDay && minutes >= 9 * 60 + 15 && minutes < 15 * 60;
  const normalizedSource = String(source || '').toLowerCase();
  const dailyCloseSource = normalizedSource.includes('index_daily')
    || normalizedSource.includes('index_global')
    || normalizedSource.endsWith('.daily');
  const clock = explicitClock(value);
  const clockMinutes = clock ? Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5)) : null;
  const snapshotReachedClose = clockMinutes != null && clockMinutes >= 15 * 60;
  const partialSnapshot = !dailyCloseSource && clockMinutes != null && !snapshotReachedClose;
  const isClose = dailyCloseSource || snapshotReachedClose || (!isCurrentDay && !partialSnapshot);
  const label = partialSnapshot && !isIntradayWindow
    ? `${dateKey} ${clock} 最新`
    : `${dateKey} ${isClose ? '收盘' : '实时'}`;

  return {
    dateKey,
    label,
    canShowChange: true,
    isCurrentDay,
    isClose,
  };
}
