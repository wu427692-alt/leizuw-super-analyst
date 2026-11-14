import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Clock3, RefreshCw } from 'lucide-react';
import { usageApi, type UsageDashboard, type UsageModelBreakdown, type UsagePeriod } from '../api/usage';
import type { ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  AppPage,
  Card,
  EmptyState,
  MetricRow,
  PageHeader,
  PageTabs,
  ShareBar,
  BarMeter,
} from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { UiLanguage, UiTextKey, UiTextParams } from '../i18n/uiText';
import { cn } from '../utils/cn';

type Translate = (key: UiTextKey, params?: UiTextParams) => string;
type UsageTab = 'overview' | 'calls';

const PERIOD_OPTIONS: UsagePeriod[] = ['today', 'month', 'all'];

const PERIOD_LABEL_KEYS: Record<UsagePeriod, UiTextKey> = {
  today: 'usage.period.today',
  month: 'usage.period.month',
  all: 'usage.period.all',
};

const CALL_TYPE_LABEL_KEYS: Record<string, UiTextKey> = {
  analysis: 'usage.callType.analysis',
  agent: 'usage.callType.agent',
  market_review: 'usage.callType.marketReview',
};

function getLocale(language: UiLanguage): string {
  return language === 'en' ? 'en-US' : 'zh-CN';
}

function formatNumber(value: number | null | undefined, language: UiLanguage): string {
  return new Intl.NumberFormat(getLocale(language)).format(value ?? 0);
}

function formatDateTime(value: string, language: UiLanguage): string {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(getLocale(language), {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function getCallTypeLabel(callType: string, t: Translate): string {
  const key = CALL_TYPE_LABEL_KEYS[callType];
  return key ? t(key) : t('usage.callType.unknown', { type: callType || '-' });
}

function buildParsedError(error: unknown, t: Translate): ParsedApiError {
  if (error && typeof error === 'object' && 'parsedError' in error) {
    const parsedError = (error as { parsedError?: ParsedApiError }).parsedError;
    if (parsedError) {
      return parsedError;
    }
  }

  const message = error instanceof Error ? error.message : t('usage.error.message');
  return {
    title: t('usage.error.title'),
    message,
    rawMessage: message,
    category: 'http_error',
  };
}

const TokenUsagePage: React.FC = () => {
  const { language, t } = useUiLanguage();
  const [period, setPeriod] = useState<UsagePeriod>('month');
  const [tab, setTab] = useState<UsageTab>('overview');
  const [dashboard, setDashboard] = useState<UsageDashboard | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const requestSeqRef = useRef(0);

  const loadDashboard = useCallback(async () => {
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    setLoading(true);
    setError(null);
    try {
      const data = await usageApi.getDashboard({ period, limit: 50 });
      if (requestSeq !== requestSeqRef.current) {
        return;
      }
      setDashboard(data);
    } catch (err) {
      if (requestSeq !== requestSeqRef.current) {
        return;
      }
      setError(buildParsedError(err, t));
    } finally {
      if (requestSeq === requestSeqRef.current) {
        setLoading(false);
      }
    }
  }, [period, t]);

  useEffect(() => {
    void loadDashboard();
    return () => {
      requestSeqRef.current += 1;
    };
  }, [loadDashboard]);

  const modelMax = useMemo(
    () => Math.max(...(dashboard?.byModel.map((item) => item.totalTokens) ?? [0]), 1),
    [dashboard],
  );
  const callTypeSegments = useMemo(
    () => (dashboard?.byCallType ?? []).map((item) => ({
      key: item.callType,
      value: item.totalTokens,
      label: getCallTypeLabel(item.callType, t),
    })),
    [dashboard, t],
  );

  const renderModelRow = (model: UsageModelBreakdown) => (
    <div key={model.model} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_72px] items-center gap-[var(--gap-sm)] border-t border-[color:var(--line-1)] py-[var(--gap-xs)] first:border-t-0">
      <div className="min-w-0">
        <div className="truncate text-[length:var(--fs-sm)] font-medium text-[color:var(--text-1)]">{model.model}</div>
        <div className="text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{t('usage.calls', { count: formatNumber(model.calls, language) })}</div>
      </div>
      <BarMeter value={model.totalTokens} max={modelMax} log height={4} ariaLabel={`${model.model} 消耗 ${model.totalTokens} tokens`} />
      <div className="text-right">
        <div className="text-[length:var(--fs-num)] font-semibold tabular-nums text-[color:var(--text-1)]">{formatNumber(model.totalTokens, language)}</div>
        <div className="text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">{t('usage.maxSingleCall')} {formatNumber(model.maxTotalTokens, language)}</div>
      </div>
    </div>
  );

  return (
    <AppPage>
      <PageHeader
        eyebrow={t('usage.eyebrow')}
        title={t('usage.title')}
        description={t('usage.description')}
        actions={(
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-xl border border-border/70 bg-card/70 p-1">
              {PERIOD_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setPeriod(option)}
                  className={cn(
                    'rounded-lg px-3 py-1.5 text-sm transition-colors',
                    period === option
                      ? 'bg-cyan text-background shadow-soft-card'
                      : 'text-secondary-text hover:bg-hover hover:text-foreground'
                  )}
                >
                  {t(PERIOD_LABEL_KEYS[option])}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              onClick={() => void loadDashboard()}
              disabled={loading}
            >
              <RefreshCw className={cn('h-4 w-4', loading ? 'animate-spin' : '')} />
              {t('usage.refresh')}
            </button>
          </div>
        )}
      />

      <PageTabs
        ariaLabel={language === 'en' ? 'Usage views' : '用量视图切换'}
        value={tab}
        onChange={(key) => setTab(key as UsageTab)}
        items={[
          { key: 'overview', label: language === 'en' ? 'Overview' : '概览' },
          { key: 'calls', label: t('usage.recentCalls'), badge: dashboard?.recentCalls.length },
        ]}
      />

      {error ? <ApiErrorAlert error={error} actionLabel={t('common.retry')} onAction={() => void loadDashboard()} /> : null}

      {loading && !dashboard ? (
        <div className="flex flex-col gap-[var(--gap-sm)]">
          <div className="h-16 animate-pulse rounded-[var(--radius-card)] border border-[color:var(--line-1)] bg-[var(--bg-1)]" />
          <div className="h-40 animate-pulse rounded-[var(--radius-card)] border border-[color:var(--line-1)] bg-[var(--bg-1)]" />
        </div>
      ) : null}

      {dashboard ? (
        dashboard.totalCalls === 0 ? (
          <EmptyState title={t('usage.emptyTitle')} description={t('usage.emptyDescription')} />
        ) : tab === 'overview' ? (
          <>
            <MetricRow
              className="mb-[var(--gap-md)]"
              columns={4}
              items={[
                { label: t('usage.totalTokens'), value: formatNumber(dashboard.totalTokens, language), hint: t('usage.dateRange', { from: dashboard.fromDate, to: dashboard.toDate }) },
                { label: t('usage.totalCalls'), value: formatNumber(dashboard.totalCalls, language), hint: t('usage.totalCallsHint') },
                { label: t('usage.promptTokens'), value: formatNumber(dashboard.totalPromptTokens, language), hint: t('usage.promptTokensHint') },
                { label: t('usage.completionTokens'), value: formatNumber(dashboard.totalCompletionTokens, language), hint: t('usage.completionTokensHint') },
              ]}
            />

            <div className="mb-[var(--gap-md)] flex items-center gap-[var(--gap-sm)]">
              <span className="shrink-0 text-[length:var(--fs-caption)] text-[color:var(--text-3)]">{t('usage.callTypeTitle')}</span>
              <ShareBar className="max-w-[280px]" ariaLabel={t('usage.callTypeTitle')} segments={callTypeSegments} />
            </div>

            <div className="grid gap-[var(--gap-lg)] xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
              <Card title={t('usage.modelUsage')} subtitle={t('usage.breakdown')} padding="md">
                <p className="mb-[var(--gap-xs)] text-[length:var(--fs-caption)] text-[color:var(--text-3)]">{t('usage.modelUsageDescription')}</p>
                <div className="flex flex-col">{dashboard.byModel.map(renderModelRow)}</div>
              </Card>

              <Card title={t('usage.callTypeTitle')} subtitle={t('usage.breakdown')} padding="md">
                <div className="flex flex-col gap-[var(--gap-sm)]">
                  {dashboard.byCallType.map((item) => (
                    <div key={item.callType} className="border-t border-[color:var(--line-1)] pt-[var(--gap-xs)] first:border-t-0 first:pt-0">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[length:var(--fs-sm)] font-medium text-[color:var(--text-1)]">{getCallTypeLabel(item.callType, t)}</span>
                        <span className="text-[length:var(--fs-num)] font-semibold tabular-nums text-[color:var(--text-1)]">{formatNumber(item.totalTokens, language)}</span>
                      </div>
                      <p className="mt-1 text-[length:var(--fs-caption)] tabular-nums text-[color:var(--text-3)]">
                        {t('usage.callTypeDetail', {
                          calls: formatNumber(item.calls, language),
                          prompt: formatNumber(item.promptTokens, language),
                          completion: formatNumber(item.completionTokens, language),
                        })}
                      </p>
                    </div>
                  ))}
                </div>
              </Card>
            </div>
          </>
        ) : (
          <section>
            <div className="mb-[var(--gap-sm)] flex flex-wrap items-end justify-between gap-[var(--gap-sm)]">
              <div>
                <h2 className="text-[length:var(--fs-h2)] font-semibold text-[color:var(--text-1)]">{t('usage.recentCalls')}</h2>
                <p className="mt-1 text-[length:var(--fs-caption)] text-[color:var(--text-3)]">
                  {t('usage.recentCallsDescription')}
                  <span className="ml-2 tabular-nums">本页 {Math.min(dashboard.recentCalls.length, 25)} / {dashboard.recentCalls.length} 条</span>
                </p>
              </div>
              <Clock3 className="h-4 w-4 text-secondary-text" />
            </div>
            <div className="overflow-hidden rounded-[var(--radius-card)] border border-[color:var(--line-1)] bg-[var(--bg-1)]">
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-border/70 text-sm">
                  <thead className="bg-surface-2/70 text-left text-xs uppercase tracking-normal text-secondary-text">
                    <tr>
                      <th className="px-3 py-2 font-medium">{t('usage.table.time')}</th>
                      <th className="px-3 py-2 font-medium">{t('usage.table.type')}</th>
                      <th className="px-3 py-2 font-medium">{t('usage.table.model')}</th>
                      <th className="px-3 py-2 text-right font-medium">Prompt</th>
                      <th className="px-3 py-2 text-right font-medium">Completion</th>
                      <th className="px-3 py-2 text-right font-medium">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {dashboard.recentCalls.length ? dashboard.recentCalls.slice(0, 25).map((item) => (
                      <tr key={item.id} className="h-8 hover:bg-hover/60">
                        <td className="whitespace-nowrap px-3 py-1 tabular-nums text-secondary-text">{formatDateTime(item.calledAt, language)}</td>
                        <td className="whitespace-nowrap px-3 py-1 text-foreground">{getCallTypeLabel(item.callType, t)}</td>
                        <td className="min-w-56 px-3 py-1">
                          <div className="max-w-[18rem] truncate font-medium text-foreground">{item.model}</div>
                          {item.stockCode ? <div className="text-xs tabular-nums text-secondary-text">{item.stockCode}</div> : null}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1 text-right tabular-nums text-secondary-text">{formatNumber(item.promptTokens, language)}</td>
                        <td className="whitespace-nowrap px-3 py-1 text-right tabular-nums text-secondary-text">{formatNumber(item.completionTokens, language)}</td>
                        <td className="whitespace-nowrap px-3 py-1 text-right font-medium tabular-nums text-foreground">{formatNumber(item.totalTokens, language)}</td>
                      </tr>
                    )) : (
                      <tr>
                        <td colSpan={6} className="px-4 py-8 text-center text-secondary-text">{t('usage.noRecentCalls')}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </section>
        )
      ) : null}
    </AppPage>
  );
};

export default TokenUsagePage;
