import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ResearchReportLibrary from '../ResearchReportLibrary';
import type { ResearchReportItem, ResearchReportSearchResult } from '../../../types/dataAcquisition';

const { search, exportSelected, status, facets } = vi.hoisted(() => ({ search: vi.fn(), exportSelected: vi.fn(), status: vi.fn(), facets: vi.fn() }));
vi.mock('../../../api/dataAcquisition', () => ({ dataAcquisitionApi: { searchResearchReports: search, exportSelectedResearchReports: exportSelected, researchReportStatus: status, researchReportFacets: facets } }));

const report = (id: number): ResearchReportItem => ({ id, title: `低空研究${id}`, tradeDate: '2026-08-20', abstract: '研究摘要', broker: '测试券商', tags: ['深度研究'], pdfUrl: `https://example.com/${id}.pdf` });
const response = (ids = [1, 2], page = 1, total = 32): ResearchReportSearchResult => ({ items: ids.map(report), total, page, pageSize: 30, source: 'local_sqlite' });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

beforeEach(() => {
  vi.clearAllMocks();
  search.mockImplementation((_filters, page) => Promise.resolve(page === 2 ? response([31, 32], 2) : response()));
  status.mockResolvedValue({ status: 'idle', total: 32, pdfCount: 32, progress: 100 });
  facets.mockResolvedValue({ brokers: [], reportTypes: [], industries: [], companies: [], tags: [] });
  exportSelected.mockResolvedValue(new Blob(['xlsx']));
  URL.createObjectURL = vi.fn().mockReturnValue('blob:test');
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
});
const ready = async () => { render(<ResearchReportLibrary />); await screen.findByLabelText('选择研报 低空研究1'); };

describe('研报跨页筛选与精确导出', () => {
  it('retains selected reports across pages and exports only selected IDs', async () => {
    await ready();
    fireEvent.click(screen.getByLabelText('选择研报 低空研究1'));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByLabelText('选择研报 低空研究31');
    fireEvent.click(screen.getByLabelText('选择研报 低空研究31'));
    fireEvent.click(screen.getByRole('button', { name: '上一页' }));
    expect(await screen.findByLabelText('选择研报 低空研究1')).toBeChecked();
    expect(screen.getByLabelText('选择研报 低空研究2')).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '导出已选 2 篇 Excel' }));
    await waitFor(() => expect(exportSelected).toHaveBeenCalledWith([1, 31]));
    expect(await screen.findByText(/已生成 2 篇研报的 Excel/)).toBeVisible();
  });

  it('applies only submitted filters, retains basket across searches, and can remove off-page selections', async () => {
    await ready();
    fireEvent.click(screen.getByLabelText('选择研报 低空研究1'));
    fireEvent.change(screen.getByLabelText('标题关键词'), { target: { value: '机器人' } });
    expect(search).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/条件已修改/)).toBeVisible();
    search.mockResolvedValueOnce(response([51], 1, 1));
    fireEvent.submit(screen.getByRole('button', { name: '点击搜索' }).closest('form')!);
    await screen.findByLabelText('选择研报 低空研究51');
    expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ titleQuery: '机器人' }), 1, 30);
    expect(screen.getByRole('button', { name: '导出已选 1 篇 Excel' })).toBeEnabled();
    fireEvent.click(screen.getByText('检查已选资料（1 篇）'));
    fireEvent.click(screen.getByLabelText('移除已选研报 低空研究1'));
    expect(screen.getByRole('button', { name: '导出已选 0 篇 Excel' })).toBeDisabled();
  });

  it('selects and deselects only the current page without dropping other pages', async () => {
    await ready();
    fireEvent.click(screen.getByRole('button', { name: '全选/取消本页' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByLabelText('选择研报 低空研究31');
    fireEvent.click(screen.getByRole('button', { name: '全选/取消本页' }));
    expect(screen.getByRole('button', { name: '导出已选 4 篇 Excel' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '全选/取消本页' }));
    expect(screen.getByRole('button', { name: '导出已选 2 篇 Excel' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '清空已选' }));
    expect(screen.getByRole('button', { name: '导出已选 0 篇 Excel' })).toBeDisabled();
  });

  it('keeps the last page and selection on a temporary error then retries the failed page', async () => {
    await ready();
    fireEvent.click(screen.getByLabelText('选择研报 低空研究1'));
    search.mockRejectedValueOnce(new Error('网络暂时中断'));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(await screen.findByText(/仍展示上次成功结果/)).toBeVisible();
    expect(screen.getByLabelText('选择研报 低空研究1')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await screen.findByLabelText('选择研报 低空研究31');
    expect(search).toHaveBeenLastCalledWith(expect.anything(), 2, 30);
    expect(screen.getByRole('button', { name: '导出已选 1 篇 Excel' })).toBeEnabled();
  });

  it('does not confuse an initial request failure with no matching reports', async () => {
    search.mockRejectedValueOnce(new Error('暂时不可用'));
    render(<ResearchReportLibrary />);
    expect(await screen.findByText(/尚未取得检索结果，不代表没有研报/)).toBeVisible();
    expect(screen.queryByText(/当前人工条件没有命中/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
    await screen.findByLabelText('选择研报 低空研究1');
  });

  it('retains the export snapshot and selection on failure without resetting search', async () => {
    await ready();
    fireEvent.click(screen.getByLabelText('选择研报 低空研究1'));
    exportSelected.mockRejectedValueOnce(new Error('导出连接中断'));
    fireEvent.click(screen.getByRole('button', { name: '导出已选 1 篇 Excel' }));
    expect(await screen.findByText(/已选资料仍保留，可重新导出/)).toBeVisible();
    expect(search).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '导出已选 1 篇 Excel' }));
    await waitFor(() => expect(exportSelected).toHaveBeenCalledTimes(2));
    expect(exportSelected).toHaveBeenLastCalledWith([1]);
  });

  it('rejects reversed dates before a request and quick date/reset buttons do not submit', async () => {
    await ready();
    fireEvent.click(screen.getByRole('button', { name: '近1月' }));
    fireEvent.click(screen.getByRole('button', { name: '重置条件' }));
    expect(search).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2030-01-02' } });
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2030-01-01' } });
    fireEvent.click(screen.getByRole('button', { name: '点击搜索' }));
    expect(screen.getByText(/开始日期不能晚于结束日期/)).toBeVisible();
    expect(search).toHaveBeenCalledTimes(1);
  });

  it('ignores a delayed StrictMode initial request after the newer result succeeds', async () => {
    const old = deferred<ResearchReportSearchResult>();
    search.mockReturnValueOnce(old.promise).mockResolvedValueOnce(response([11], 1, 1));
    render(<StrictMode><ResearchReportLibrary /></StrictMode>);
    await screen.findByLabelText('选择研报 低空研究11');
    await act(async () => old.resolve(response([99], 1, 1)));
    expect(screen.queryByLabelText('选择研报 低空研究99')).not.toBeInTheDocument();
    expect(screen.getByLabelText('选择研报 低空研究11')).toBeVisible();
  });

  it('renders a successful empty search honestly and keeps the selected basket usable', async () => {
    await ready();
    fireEvent.click(screen.getByLabelText('选择研报 低空研究1'));
    search.mockResolvedValueOnce(response([], 1, 0));
    fireEvent.change(screen.getByLabelText('标题关键词'), { target: { value: '不匹配' } });
    fireEvent.click(screen.getByRole('button', { name: '点击搜索' }));
    expect(await screen.findByText(/当前人工条件没有命中/)).toBeVisible();
    const region = screen.getByRole('region', { name: '研报检索结果与已选资料' });
    expect(within(region).getByRole('button', { name: '导出已选 1 篇 Excel' })).toBeEnabled();
  });

  it('checks completed library status every minute but pauses while hidden and refreshes on return', async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    try {
      status.mockResolvedValue({ status: 'completed', total: 32, pdfCount: 32, progress: 100 });
      render(<ResearchReportLibrary />);
      await act(async () => {});
      expect(status).toHaveBeenCalledTimes(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(status).toHaveBeenCalledTimes(2);
      visibility.mockReturnValue('hidden');
      await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
      expect(status).toHaveBeenCalledTimes(2);
      visibility.mockReturnValue('visible');
      await act(async () => { fireEvent(document, new Event('visibilitychange')); });
      expect(status).toHaveBeenCalledTimes(3);
      expect(search).toHaveBeenCalledTimes(1);
    } finally { visibility.mockRestore(); vi.useRealTimers(); }
  });

  it('polls a running sync every three seconds without resubmitting search', async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    try {
      status.mockResolvedValue({ status: 'running', total: 32, pdfCount: 32, progress: 40 });
      render(<ResearchReportLibrary />);
      await act(async () => {});
      await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
      expect(status).toHaveBeenCalledTimes(3);
      expect(search).toHaveBeenCalledTimes(1);
    } finally { visibility.mockRestore(); vi.useRealTimers(); }
  });
});
