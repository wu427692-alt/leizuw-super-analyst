import { StrictMode, type ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { investmentMonitorApi } from '../../../api/investmentMonitor';
import { toCamelCase } from '../../../api/utils';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import type { MonitorEvent } from '../../../types/investmentMonitor';
import { SourceRecordDrawer, type FinancialRecordSection } from '../SourceRecordDrawer';

vi.mock('../../../api/investmentMonitor', () => ({ investmentMonitorApi: { event: vi.fn() } }));

function source(id = 17, overrides: Partial<MonitorEvent> = {}): MonitorEvent {
  return {
    id, title: `样例资料 ${id}`, sourceKey: 'tushare.research_pdf', sourceName: '样例资料来源',
    sourceType: 'test', externalId: `fixture-${id}`, eventType: 'research_report_pdf',
    perspective: 'institution', symbols: ['300308.SZ'], sentiment: 'neutral',
    importanceScore: 50, confidenceScore: 0.8, tags: [], actors: [],
    metrics: {}, summary: `列表摘要 ${id}`, eventAt: '2026-09-10T08:15:00Z', ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise; reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function financialRecord(id = 17): MonitorEvent {
  return source(id, { eventType: 'fundamental_snapshot', summary: '程序生成摘要，不作为财报正文', metrics: toCamelCase({
    indicator: { end_date: '20241231', roe: 0, private_config: 'PRIVATE_SENTINEL' },
    income: { end_date: '20250630', revenue: 0, n_income_attr_p: -120000 },
    cashflow: { n_cashflow_act: -1500 },
    balance_sheet: { end_date: '20250331', total_assets: 42000 },
    forecast: { end_date: '20250930', type: '预减', summary: '已保存业绩预告' },
    raw: 'RAW_SENTINEL', config: { api_key: 'KEY_SENTINEL' },
  }) });
}

function provide(children: ReactNode) {
  return <UiLanguageProvider>{children}</UiLanguageProvider>;
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe('SourceRecordDrawer: local saved records with the real shared Drawer', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(source());
  });
  afterEach(() => {
    cleanup(); vi.useRealTimers(); vi.restoreAllMocks();
  });

  it('shows the seed immediately as an excerpt, then reads details once without calling it full text', async () => {
    const pending = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValue(pending.promise);
    const { container, rerender } = render(provide(<SourceRecordDrawer event={source()} onClose={vi.fn()} />));
    const dialog = screen.getByRole('dialog', { name: '样例资料 17' });
    expect(dialog).toHaveTextContent('列表摘要 17');
    expect(dialog).toHaveTextContent('先显示列表中已有的内容');
    expect(dialog).toHaveTextContent('不是完整 PDF 正文');
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
    expect(document.body).toContainElement(dialog);
    expect(document.body.style.overflow).toBe('hidden');
    rerender(provide(<SourceRecordDrawer event={{ ...source() }} onClose={vi.fn()} />));
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
    expect(investmentMonitorApi.event).toHaveBeenCalledWith(17);
    await act(async () => pending.resolve(source(17, { summary: '已保存详细摘要' })));
    expect(dialog).toHaveTextContent('已保存详细摘要');
    expect(dialog).not.toHaveTextContent('列表摘要 17');
    expect(dialog).toHaveTextContent('不是完整 PDF 正文');
    expect(screen.queryByText(/正在读取已入库资料/)).not.toBeInTheDocument();
  });

  it('keeps only the seed on failure, hides the internal error, and retries locally on request', async () => {
    vi.mocked(investmentMonitorApi.event).mockRejectedValueOnce(new Error('INTERNAL_DATABASE_PASSWORD_SENTINEL'));
    render(provide(<SourceRecordDrawer event={source()} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog')).toHaveTextContent('列表摘要 17');
    expect(screen.getByRole('status')).toHaveTextContent('本次详情读取未完成，已保留列表摘录');
    expect(document.body).not.toHaveTextContent('INTERNAL_DATABASE_PASSWORD_SENTINEL');
    const pending = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole('button', { name: '重试读取资料' }));
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('dialog')).toHaveTextContent('正在读取已入库资料');
    expect(screen.queryByRole('button', { name: '重试读取资料' })).not.toBeInTheDocument();
    await act(async () => pending.resolve(source(17, { summary: '重试成功后的摘要' })));
    expect(screen.getByRole('dialog')).toHaveTextContent('重试成功后的摘要');
    expect(screen.queryByText(/本次详情读取未完成/)).not.toBeInTheDocument();
  });

  it('rejects a successful HTTP response containing the wrong record ID', async () => {
    vi.mocked(investmentMonitorApi.event).mockResolvedValueOnce(source(99, {
      title: '错 ID 的资料', summary: '不允许出现的其他正文', url: 'https://example.test/other.pdf',
    }));
    render(provide(<SourceRecordDrawer event={source()} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog', { name: '样例资料 17' })).toHaveTextContent('列表摘要 17');
    expect(screen.getByRole('status')).toHaveTextContent('本次详情读取未完成');
    expect(document.body).not.toHaveTextContent('错 ID 的资料');
    expect(document.body).not.toHaveTextContent('不允许出现的其他正文');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('does not show a mismatched seed when an explicit event ID is requested', async () => {
    const pending = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValue(pending.promise);
    render(provide(<SourceRecordDrawer event={source(99)} eventId={17} onClose={vi.fn()} />));
    expect(screen.getByRole('dialog', { name: '资料详情' })).toHaveTextContent('尚未取得内容');
    expect(document.body).not.toHaveTextContent('列表摘要 99');
    expect(investmentMonitorApi.event).toHaveBeenCalledWith(17);
    await act(async () => pending.resolve(source()));
    expect(screen.getByRole('dialog', { name: '样例资料 17' })).toHaveTextContent('列表摘要 17');
  });

  it('keeps an explicit failure and retry when a deep link fails without a seed', async () => {
    vi.mocked(investmentMonitorApi.event).mockRejectedValueOnce({ response: { status: 404 } });
    render(provide(<SourceRecordDrawer eventId={17} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog', { name: '资料详情' })).toHaveTextContent('本次详情读取未完成，请重试');
    expect(screen.getByRole('button', { name: '重试读取资料' })).toBeEnabled();
    expect(screen.queryByText(/正在读取已入库资料/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('switches ID immediately and ignores the old response even when it arrives before the new detail', async () => {
    const old = deferred<MonitorEvent>();
    const current = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    const view = render(provide(<SourceRecordDrawer event={source()} onClose={vi.fn()} />));
    view.rerender(provide(<SourceRecordDrawer event={source(18)} onClose={vi.fn()} />));
    expect(screen.getByRole('dialog', { name: '样例资料 18' })).toHaveTextContent('列表摘要 18');
    expect(document.body).not.toHaveTextContent('列表摘要 17');
    await act(async () => old.resolve(source(17, { title: '迟到旧标题', summary: '迟到旧正文' })));
    expect(document.body).not.toHaveTextContent('迟到旧正文');
    expect(screen.getByRole('dialog', { name: '样例资料 18' })).toHaveTextContent('正在读取已入库资料');
    await act(async () => current.resolve(source(18, { summary: '当前资料详细摘要' })));
    expect(screen.getByRole('dialog')).toHaveTextContent('当前资料详细摘要');
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(2);
  });

  it('ignores an old permission failure after another ID has loaded successfully', async () => {
    const old = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValueOnce(old.promise).mockResolvedValueOnce(source(18));
    const view = render(provide(<SourceRecordDrawer eventId={17} onClose={vi.fn()} />));
    view.rerender(provide(<SourceRecordDrawer eventId={18} onClose={vi.fn()} />));
    await flush();
    await act(async () => old.reject({ response: { status: 403 } }));
    expect(screen.getByRole('dialog', { name: '样例资料 18' })).toHaveTextContent('列表摘要 18');
    expect(screen.queryByText(/当前账号无法读取/)).not.toBeInTheDocument();
  });

  it('closes through the real Drawer and cannot fill a reopened record from the abandoned request', async () => {
    const old = deferred<MonitorEvent>();
    const reopened = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValueOnce(old.promise).mockReturnValueOnce(reopened.promise);
    const close = vi.fn();
    const view = render(provide(<SourceRecordDrawer event={source()} onClose={close} />));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalledTimes(1);
    view.rerender(provide(null)); // The owner handles onClose by removing its selection.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe('');
    view.rerender(provide(<SourceRecordDrawer event={source()} onClose={close} />));
    await act(async () => old.resolve(source(17, { summary: '关闭前请求的迟到正文' })));
    expect(document.body).not.toHaveTextContent('关闭前请求的迟到正文');
    expect(screen.getByRole('dialog')).toHaveTextContent('正在读取已入库资料');
    await act(async () => reopened.resolve(source(17, { summary: '重新打开取得的摘要' })));
    expect(screen.getByRole('dialog')).toHaveTextContent('重新打开取得的摘要');
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(2);
  });

  it('shares one logical in-flight GET across StrictMode effect replay and does not poll a slow read', async () => {
    vi.useFakeTimers();
    const pending = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValue(pending.promise);
    const view = render(<StrictMode>{provide(<SourceRecordDrawer event={source()} onClose={vi.fn()} />)}</StrictMode>);
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog')).toHaveTextContent('正在读取已入库资料');
    await act(async () => pending.resolve(source(17, { summary: '慢请求正常返回' })));
    expect(screen.getByRole('dialog')).toHaveTextContent('慢请求正常返回');
    view.unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it.each([401, 403])('clears all seed content and links on %s and waits for manual recovery', async status => {
    vi.useFakeTimers();
    const seed = source(17, { url: 'https://example.test/private.pdf', metrics: { orgName: '种子机构' } });
    vi.mocked(investmentMonitorApi.event).mockRejectedValueOnce({
      response: { status, data: { message: 'PRIVATE_SERVER_MESSAGE_SENTINEL' } },
    });
    render(provide(<SourceRecordDrawer event={seed} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog', { name: '资料详情' })).toHaveTextContent('当前账号无法读取该资料');
    expect(document.body).not.toHaveTextContent('列表摘要 17');
    expect(document.body).not.toHaveTextContent('样例资料来源');
    expect(document.body).not.toHaveTextContent('种子机构');
    expect(document.body).not.toHaveTextContent('PRIVATE_SERVER_MESSAGE_SENTINEL');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTime(60_000));
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '重试读取资料' }));
    await flush();
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('dialog', { name: '样例资料 17' })).toHaveTextContent('列表摘要 17');
    expect(screen.queryByText(/当前账号无法读取/)).not.toBeInTheDocument();
  });

  it.each([
    'javascript:alert(1)', 'data:text/html,unsafe', 'file:///private/report.pdf',
    '//example.test/report.pdf', '/api/private/report.pdf', 'https://name:password@example.test/report.pdf',
    'not-a-url',
  ])('does not expose an unsafe or unsupported source link: %s', async url => {
    const item = source(17, { url });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    await flush();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveTextContent('此条未提供可用的原文链接');
  });

  it('keeps a valid PDF link separate from the saved excerpt and uses an isolated new tab', async () => {
    const item = source(17, { url: 'https://example.test/report.PDF?download=1#page=2' });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    const link = screen.getByRole('link', { name: '打开原始 PDF' });
    expect(link).toHaveAttribute('href', item.url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link.getAttribute('rel')?.split(/\s+/)).toContain('noopener');
    expect(link.getAttribute('rel')?.split(/\s+/)).toContain('noreferrer');
    expect(screen.getByRole('dialog')).toHaveTextContent('不是完整 PDF 正文');
  });

  it('renders only documented metadata and prediction columns, never arbitrary nested payload fields', async () => {
    const item = Object.assign(source(17, {
      eventType: 'institution_forecast', summary: '普通保存摘要', metrics: {
        orgName: '测试证券', reportSymbol: '300308.SZ', reportType: '公司研究', author: '测试作者',
        abstr: '来源提供的研报摘要', targetPriceMin: 0,
        forecasts: [{ quarter: '2027', eps: 0, pe: 25, np: 'UNSELECTED_FORECAST_SENTINEL', raw: 'NESTED_FORECAST_SENTINEL' }],
        rawPayload: 'RAW_CAMEL_SENTINEL', raw_payload: 'RAW_SNAKE_SENTINEL',
        config: { apiKey: 'CONFIG_SECRET_SENTINEL' }, arbitrary: 'ARBITRARY_METRIC_SENTINEL',
        records: [{ body: 'RECORDS_DUMP_SENTINEL' }],
        _evidence: { secret: 'EVIDENCE_DUMP_SENTINEL' },
      },
    }), { raw_payload: 'TOP_LEVEL_RAW_SENTINEL' });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('测试证券');
    expect(dialog).toHaveTextContent('300308.SZ');
    expect(dialog).toHaveTextContent('公司研究');
    expect(dialog).toHaveTextContent('来源提供的研报摘要');
    expect(dialog).not.toHaveTextContent('普通保存摘要');
    expect(dialog).not.toHaveTextContent('SENTINEL');
    fireEvent.click(screen.getByText('研报预测记录 · 已保存 1 条'));
    const table = within(dialog).getByRole('table');
    expect(within(table).getAllByRole('columnheader').map(node => node.textContent)).toEqual(['预测期', 'EPS（元）', 'PE（倍）']);
    expect(within(table).getAllByRole('cell').map(node => node.textContent)).toEqual(['2027', '0', '25']);
    expect(dialog.querySelector('pre')).not.toBeInTheDocument();
  });

  it('renders saved forecast profit, revenue and unscaled ROE through the real snake-to-camel contract', async () => {
    const item = source(17, { eventType: 'institution_forecast', metrics: toCamelCase({
      forecasts: [
        { quarter: '2027Q4', eps: 0, pe: 12, np: -12500.5, op_rt: 0, roe: 0.125, raw: 'RAW_SENTINEL', config: 'CONFIG_SENTINEL' },
        { quarter: '2028Q4', eps: 2, pe: 10, np: 0, op_rt: 250000, roe: -4.2 },
        { quarter: '2029Q4', eps: 3, pe: 9, np: null, op_rt: 'invalid_SENTINEL', roe: 'Infinity' },
      ],
    }) });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    fireEvent.click(screen.getByText('研报预测记录 · 已保存 3 条'));
    const dialog = screen.getByRole('dialog');
    const table = within(dialog).getByRole('table');
    expect(within(table).getAllByRole('columnheader').map(node => node.textContent)).toEqual([
      '预测期', 'EPS（元）', 'PE（倍）', '净利润（万元）', '营业收入（万元）', 'ROE（源值）',
    ]);
    expect(within(table).getAllByRole('cell').map(node => node.textContent)).toEqual([
      '2027Q4', '0', '12', '-12500.5', '0', '0.125',
      '2028Q4', '2', '10', '0', '250000', '-4.2',
      '2029Q4', '3', '9', '—', '—', '—',
    ]);
    expect(dialog).toHaveTextContent('净利润和营业收入保留源单位万元');
    expect(dialog).toHaveTextContent('不推断百分比缩放');
    expect(dialog).toHaveTextContent('预测并非已实现业绩');
    expect(dialog).not.toHaveTextContent('SENTINEL');
    expect(dialog).not.toHaveTextContent('Infinity');
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
  });

  it.each(['research_report_pdf', 'institution_forecast', 'institution_survey', 'broker_recommendation'])(
    'keeps the shared research classification and documented fields for %s', async eventType => {
      const item = source(17, { eventType, metrics: { orgName: '研究机构', tsCode: '300308.SZ',
        arbitrary: 'ARBITRARY_METRIC_SENTINEL' } });
      vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
      render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
      await flush();
      const dialog = screen.getByRole('dialog');
      expect(within(dialog).getByRole('heading', { name: '研报摘要与记录' })).toBeVisible();
      expect(dialog).toHaveTextContent('研究机构');
      expect(dialog).toHaveTextContent('300308.SZ');
      expect(dialog).toHaveTextContent('不是完整 PDF 正文');
      expect(dialog).not.toHaveTextContent('ARBITRARY_METRIC_SENTINEL');
    },
  );

  it('paginates saved forecast rows without dropping the tail or making another data request', async () => {
    const item = source(17, { eventType: 'institution_forecast', metrics: {
      forecasts: Array.from({ length: 42 }, (_, index) => ({ quarter: `预测样例-${index + 1}`, eps: index, pe: 20, np: index * 100, opRt: index * 500, roe: index / 100 })),
    } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    fireEvent.click(screen.getByText('研报预测记录 · 已保存 42 条'));
    expect(screen.getAllByRole('row')).toHaveLength(41);
    expect(screen.getByText('预测样例-40')).toBeVisible();
    expect(screen.queryByText('预测样例-42')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '研报预测记录下一页' }));
    expect(screen.getAllByRole('row')).toHaveLength(3);
    expect(screen.getByText('预测样例-42')).toBeVisible();
    expect(within(screen.getAllByRole('row').at(-1)!).getAllByRole('cell').map(node => node.textContent))
      .toEqual(['预测样例-42', '41', '20', '4100', '20500', '0.41']);
    expect(screen.getByText('第 2 / 2 页')).toBeVisible();
    expect(screen.getByRole('button', { name: '研报预测记录下一页' })).toBeDisabled();
    expect(screen.getByRole('region', { name: '研报预测记录明细，可横向滚动' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: '研报预测记录上一页' }));
    expect(screen.getByText('预测样例-1')).toBeVisible();
    expect(screen.getByRole('region', { name: '研报预测记录明细，可横向滚动' })).toHaveFocus();
    expect(investmentMonitorApi.event).toHaveBeenCalledTimes(1);
  });

  it('keeps useful financial fields grouped by their own period without dumping payloads', async () => {
    const item = source(17, { eventType: 'fundamental_snapshot', summary: '', metrics: {
      indicator: { endDate: '20260630', roe: 0, privateConfig: 'PRIVATE_SENTINEL' },
      income: { endDate: '20251231', revenue: 12000 },
      cashflow: { nCashflowAct: -1500 },
      raw: 'RAW_SENTINEL', config: { apiKey: 'KEY_SENTINEL' },
    } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    const indicator = within(dialog).getByRole('heading', { name: '财务指标' }).closest('section')!;
    const income = within(dialog).getByRole('heading', { name: '利润表' }).closest('section')!;
    const cashflow = within(dialog).getByRole('heading', { name: '现金流量表' }).closest('section')!;
    expect(indicator).toHaveTextContent('20260630');
    expect(indicator).toHaveTextContent('净资产收益率（%）0');
    expect(income).toHaveTextContent('20251231');
    expect(income).toHaveTextContent('12000');
    expect(cashflow).toHaveTextContent('报告期未注明');
    expect(cashflow).toHaveTextContent('-1500');
    expect(cashflow).not.toHaveTextContent('20260630');
    expect(cashflow).not.toHaveTextContent('20251231');
    expect(dialog).toHaveTextContent('以下为数据源记录，按各自日期和单位展示');
    expect(dialog).not.toHaveTextContent('SENTINEL');
  });

  it.each([
    { section: 'income' as const, title: '利润表', date: '20250630', value: '-120000' },
    { section: 'cashflow' as const, title: '现金流量表', date: '未注明', value: '-1500' },
    { section: 'indicator' as const, title: '财务指标', date: '20241231', value: '净资产收益率（%）0' },
  ])('prioritizes the $title context while every other saved table remains expandable without another GET', async ({ section, title, date, value }) => {
    const item = financialRecord();
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer eventId={item.id} financialSection={section} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog', { name: item.title });
    const current = within(dialog).getByRole('region', { name: `当前核对${title}` });
    expect(within(current).getByRole('heading', { name: `当前核对：${title}` })).toBeVisible();
    expect(current).toHaveTextContent(date);
    expect(current).toHaveTextContent(value);
    expect(dialog.querySelector('.source-record-fields')?.querySelector('h3')).toHaveTextContent(`当前核对：${title}`);
    if (section === 'cashflow') expect(current).not.toHaveTextContent(/20241231|20250630|20250331/);
    const other = dialog.querySelector<HTMLDetailsElement>('.source-record-other-fields')!;
    expect(other.open).toBe(false);
    expect(other).toHaveTextContent('其他已保存字段（4 组）');
    for (const heading of other.querySelectorAll('h3')) expect(heading).not.toBeVisible();
    fireEvent.click(within(other).getByText('其他已保存字段（4 组）'));
    expect(other.open).toBe(true);
    for (const heading of other.querySelectorAll('h3')) expect(heading).toBeVisible();
    expect(dialog.querySelectorAll('.source-record-fields')).toHaveLength(5);
    expect(dialog).toHaveTextContent('已保存业绩预告');
    expect(dialog).not.toHaveTextContent(/PRIVATE_SENTINEL|RAW_SENTINEL|KEY_SENTINEL|程序生成摘要/);
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(item.id);
  });

  it.each(['income', 'cashflow', 'indicator'] as const)('reports a genuinely missing $section table instead of substituting another saved table', async section => {
    const item = financialRecord();
    delete item.metrics[section];
    const title = { income: '利润表', cashflow: '现金流量表', indicator: '财务指标' }[section];
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer eventId={item.id} financialSection={section} onClose={vi.fn()} />));
    expect(screen.queryByText(`此条记录未保存可展示的${title}字段，不使用其他表替代。`)).not.toBeInTheDocument();
    await flush();
    const current = screen.getByRole('region', { name: `当前核对${title}` });
    expect(current).toHaveTextContent(`此条记录未保存可展示的${title}字段，不使用其他表替代。`);
    expect(current.querySelector('dl')).toBeNull();
    expect(current).not.toHaveTextContent(/20241231|20250630|20250331|-120000|-1500/);
    expect(screen.getByRole('dialog').querySelectorAll('.source-record-other-fields .source-record-fields')).toHaveLength(4);
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(item.id);
  });

  it('keeps the selected table actual disclosure date, nominal date and raw revision marker visible together', async () => {
    const item = financialRecord();
    item.metrics = toCamelCase({ income: {
      end_date: '20260630', ann_date: '20260825', f_ann_date: '20260820', report_type: '4', update_flag: 0, revenue: 0,
    } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer eventId={item.id} financialSection="income" onClose={vi.fn()} />));
    await flush();
    const current = screen.getByRole('region', { name: '当前核对利润表' });
    for (const [label, value] of [['披露日', '20260825'], ['实际公告日', '20260820'], ['报表类型代码', '4'], ['更新标记（源值）', '0'], ['营业收入', '0']]) {
      expect(within(current).getByText(label, { exact: true }).closest('div')?.querySelector('dd')).toHaveTextContent(value);
    }
    expect(current).not.toHaveTextContent('最新');
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(item.id);
  });

  it.each(['loaded', 'pending'])('resets the same record table context and disclosure state during a %s read without refetching', async state => {
    const item = financialRecord();
    const pending = deferred<MonitorEvent>();
    vi.mocked(investmentMonitorApi.event).mockReturnValue(pending.promise);
    const view = render(provide(<SourceRecordDrawer event={item} financialSection="income" onClose={vi.fn()} />));
    if (state === 'loaded') await act(async () => pending.resolve(item));
    const other = screen.getByText('其他已保存字段（4 组）').closest('details')!;
    fireEvent.click(within(other).getByText('其他已保存字段（4 组）'));
    expect(other.open).toBe(true);
    view.rerender(provide(<SourceRecordDrawer event={item} financialSection="cashflow" onClose={vi.fn()} />));
    const current = screen.getByRole('region', { name: '当前核对现金流量表' });
    expect(current).toHaveFocus();
    expect(current).toHaveTextContent('-1500');
    expect(current).not.toHaveTextContent('-120000');
    expect(screen.queryByRole('region', { name: '当前核对利润表' })).not.toBeInTheDocument();
    expect(screen.getByText('其他已保存字段（4 组）').closest('details')!.open).toBe(false);
    if (state === 'pending') await act(async () => pending.resolve(item));
    expect(screen.getByRole('region', { name: '当前核对现金流量表' })).toHaveTextContent('-1500');
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(item.id);
  });

  it.each(['balanceSheet', 'constructor', 'INCOME'])('ignores unsupported financial context %s and retains the ordinary source reader', async value => {
    const item = financialRecord();
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} financialSection={value as FinancialRecordSection} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    expect(dialog.querySelector('.source-record-financial-context')).toBeNull();
    for (const title of ['财务指标', '利润表', '资产负债表', '现金流量表', '业绩预告']) {
      expect(within(dialog).getByRole('heading', { name: title })).toBeVisible();
    }
    expect(investmentMonitorApi.event).toHaveBeenCalledExactlyOnceWith(item.id);
  });

  it.each(['20260909', null])('renders saved valuation fields with date %s and suppresses generated market prose', async valuationDate => {
    const item = source(17, { eventType: 'market_snapshot', summary: '行情生成摘要_SENTINEL', metrics: toCamelCase({
      trade_date: '20260910', valuation_trade_date: valuationDate,
      valuation_pe_ttm: 0, valuation_pb: -1.5, valuation_total_mv: 123.45,
      total_mv: 999, raw: { valuation_pe_ttm: 888 },
    }) });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    const valuation = within(dialog).getByRole('heading', { name: '估值记录' }).closest('section')!;
    expect(within(dialog).getByRole('heading', { name: '来源字段记录' })).toBeVisible();
    expect(valuation).toHaveTextContent(`估值日期${valuationDate ?? '未注明'}`);
    expect(valuation).toHaveTextContent('滚动市盈率（PE TTM）0 倍');
    expect(valuation).toHaveTextContent('市净率（PB）-1.5 倍');
    expect(valuation).toHaveTextContent('总市值1234500 元');
    expect(valuation).not.toHaveTextContent('20260910');
    expect(dialog).not.toHaveTextContent('SENTINEL');
    expect(valuation).not.toHaveTextContent('999');
    expect(valuation).not.toHaveTextContent('888');
    expect(dialog).not.toHaveTextContent('未提供可用的原文链接');
  });

  it.each(['capital_chip_snapshot', 'earnings_express'])('uses source fields rather than obsolete generated prose for %s', async eventType => {
    const metrics = eventType === 'capital_chip_snapshot'
      ? { _evidence: { contentNature: 'derived_summary' }, moneyflow: { tradeDate: '20260910', netMfAmount: 0 } }
      : { endDate: '20260630', nIncome: 2500, dilutedRoe: 4.5 };
    const item = source(17, { eventType, summary: '旧生成摘要_SENTINEL', metrics });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: '来源字段记录' })).toBeVisible();
    expect(dialog).not.toHaveTextContent('旧生成摘要_SENTINEL');
    expect(dialog).toHaveTextContent(eventType === 'capital_chip_snapshot' ? '资金净流入0' : '摊薄净资产收益率（%）4.5');
  });

  it.each(['market_snapshot', 'capital_chip_snapshot', 'earnings_express'])('does not restore an obsolete compact summary while %s fields load or fail', async eventType => {
    const pending = deferred<MonitorEvent>();
    const compact = source(17, { eventType, summary: '旧错误口径_SENTINEL', metrics: {
      _evidence: { contentNature: eventType === 'capital_chip_snapshot' ? 'derived_summary' : 'source_record' },
    } });
    vi.mocked(investmentMonitorApi.event).mockReturnValueOnce(pending.promise);
    render(provide(<SourceRecordDrawer event={compact} onClose={vi.fn()} />));
    expect(screen.getByRole('heading', { name: '来源字段记录' })).toBeVisible();
    expect(screen.getByText('正在读取来源字段…')).toBeVisible();
    expect(document.body).not.toHaveTextContent('旧错误口径_SENTINEL');
    await act(async () => pending.reject(new Error('offline')));
    expect(screen.getByRole('status')).toHaveTextContent('已保留来源信息');
    expect(screen.getByText('来源字段尚未取得，可重试读取。')).toBeVisible();
    expect(document.body).not.toHaveTextContent('旧错误口径_SENTINEL');
    vi.mocked(investmentMonitorApi.event).mockResolvedValueOnce(compact);
    fireEvent.click(screen.getByRole('button', { name: '重试读取资料' }));
    await flush();
    expect(screen.getByText('此条未保存可展示的来源字段。')).toBeVisible();
    expect(document.body).not.toHaveTextContent('旧错误口径_SENTINEL');
  });

  it.each(['market_snapshot', 'fundamental_snapshot', 'capital_chip_snapshot', 'ownership_snapshot', 'technical_factor', 'earnings_express'])(
    'recognizes historical generated %s records without content-nature metadata', async eventType => {
      const item = source(17, { eventType, summary: '旧错误摘要_SENTINEL', metrics: {} });
      vi.mocked(investmentMonitorApi.event).mockResolvedValueOnce(item);
      render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
      expect(document.body).not.toHaveTextContent('旧错误摘要_SENTINEL');
      await flush();
      expect(screen.getByRole('heading', { name: '来源字段记录' })).toBeVisible();
      expect(screen.getByText('此条未保存可展示的来源字段。')).toBeVisible();
      expect(document.body).not.toHaveTextContent('旧错误摘要_SENTINEL');
    },
  );

  it('presents a field-only shareholder record without redundant missing-article warnings', async () => {
    const item = source(17, { eventType: 'top_shareholders', summary: '  ', metrics: {
      period: '20260630', holders: [{ holderName: '样例股东', holdAmount: 100, holdRatio: 0 }],
    } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValueOnce(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('heading', { name: '来源字段记录' })).toBeVisible();
    expect(screen.getByText('十大股东 · 已保存 1 条')).toBeVisible();
    expect(document.body).not.toHaveTextContent('没有正文摘录');
    expect(document.body).not.toHaveTextContent('未提供可用的原文链接');
  });

  it('retains actual saved prose for records that also have structured fields', async () => {
    const item = source(17, { eventType: 'top_shareholders', summary: '已保存的股东说明', metrics: {
      period: '20260630', holders: [{ holderName: '样例股东', holdAmount: 100 }],
    } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValueOnce(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('heading', { name: '已保存摘录' })).toBeVisible();
    expect(screen.getByText('已保存的股东说明')).toBeVisible();
  });

  it('describes an empty report honestly without inventing article text or calling it numerical data', async () => {
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(source(17, { summary: '', metrics: {} }));
    render(provide(<SourceRecordDrawer eventId={17} onClose={vi.fn()} />));
    await flush();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('这条记录没有保存正文摘录');
    expect(dialog).toHaveTextContent('此条未提供可用的原文链接，不将摘要当作全文');
    expect(dialog).not.toHaveTextContent('数值型记录');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it('shows only the supplied disclosure date for date-precision announcements', async () => {
    const item = source(17, { eventType: 'company_announcement', eventAt: '2026-09-09T16:00:00Z',
      metrics: { timePrecision: 'date', disclosureDate: '2026-09-10', name: '样例公司', categoryNames: ['年度报告'] } });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog').querySelector('time')?.textContent).toBe('2026-09-10');
    expect(screen.getByRole('dialog')).toHaveTextContent('年度报告');
    expect(screen.getByRole('dialog')).not.toHaveTextContent('00:00');
  });

  it('does not manufacture clock precision when the stored event itself is only a date', async () => {
    const item = source(17, { eventAt: '2026-09-10' });
    vi.mocked(investmentMonitorApi.event).mockResolvedValue(item);
    render(provide(<SourceRecordDrawer event={item} onClose={vi.fn()} />));
    await flush();
    expect(screen.getByRole('dialog').querySelector('time')?.textContent).toBe('2026-09-10');
  });

  it.each([undefined, 0, -1, 1.5, NaN])('does not fetch or open a Drawer for invalid ID %s', eventId => {
    render(provide(<SourceRecordDrawer eventId={eventId} onClose={vi.fn()} />));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(investmentMonitorApi.event).not.toHaveBeenCalled();
  });
});
