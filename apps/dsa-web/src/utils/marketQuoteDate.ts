const COMPACT_DATE = /^(\d{4})[-/]?(\d{2})[-/]?(\d{2})/;

export function quoteDateKey(value?: string | null): string | null {
  if (!value) return null;
  // Offset-bearing quote timestamps must be compared in the exchange zone,
  // not by the date printed in UTC (which may still be yesterday).
  if (/[T\s]\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.trim())) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return shanghaiDateKey(parsed);
  }
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
  if (/[T\s]\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.trim())) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) {
      const { minutes } = shanghaiClock(parsed);
      return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
    }
  }
  const match = String(value).trim().match(/[T\s](\d{2}):(\d{2})(?::\d{2})?/);
  return match ? `${match[1]}:${match[2]}` : null;
}

function quoteInstant(value: string, dateKey: string): number | null {
  const text = value.trim();
  // A date-only Tushare row is a completed daily close, not midnight. A
  // same-day 14:00 snapshot must never replace that official 15:00 record.
  if (/^\d{4}[-/]?\d{2}[-/]?\d{2}$/.test(text)) return Date.parse(`${dateKey}T15:00:00+08:00`);
  if (!/[T\s]\d{2}:\d{2}/.test(text)) return null;
  const normalized = text.replaceAll('/', '-').replace(/\s+/, 'T');
  const timestamp = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}+08:00`);
  return Number.isFinite(timestamp) ? timestamp : null;
}

/** Actual quote time in the exchange timezone, without inventing freshness or a close. */
export function formatMarketQuoteTimestamp(value?: string | null): string | null {
  const dateKey = quoteDateKey(value);
  if (!dateKey || !value) return null;
  const instant = quoteInstant(value, dateKey);
  if (instant == null || shanghaiDateKey(new Date(instant)) !== dateKey) return null;
  const clock = explicitClock(value);
  return clock ? `${dateKey} ${clock}` : dateKey;
}

/**
 * Prefer a candidate quote when it belongs to a newer trading day.  On the
 * same trading day compare actual instants and protect official daily closes.
 * Polling staleness never outranks an explicitly newer timestamp. In
 * particular a completed daily close is marked stale by the quote API but
 * must replace an earlier intraday snapshot. Staleness only breaks a tie.
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
  const candidateInstant = quoteInstant(candidateAt!, candidateDate);
  const currentInstant = quoteInstant(currentAt!, currentDate);
  if (candidateInstant == null) return false;
  return currentInstant == null || candidateInstant > currentInstant
    || (candidateInstant === currentInstant && !candidateIsStale);
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
