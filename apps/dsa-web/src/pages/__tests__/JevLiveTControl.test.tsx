import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jevLiveTApi } from '../../api/jevLiveT';
import type { LiveTControl } from '../../types/jevLiveT';
import JevLiveTControl from '../JevLiveTControl';

vi.mock('../../api/jevLiveT', () => ({ jevLiveTApi: { control: vi.fn(), setControl: vi.fn() } }));
const stopped: LiveTControl = { canControl: true, enabled: false, phase: 'stopped', runId: 'continuous-one', config: { symbol: '603306.SH', maxCostUsd: 10, confidenceSizing: true, durationSeconds: null, maxModelRequests: null }, usage: { costUsd: .008075718, costIsEstimate: true }, message: '账户保留', updatedAt: '2026-09-21T05:00:00Z' };
const running: LiveTControl = { ...stopped, enabled: true, phase: 'running' };
const onChanged = vi.fn(); const onAccessLost = vi.fn(); const onOpenRun = vi.fn();
const props = { selectedRunId: null, formatTime: () => '2026-09-21 13:00:00', onChanged, onAccessLost, onOpenRun };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(jevLiveTApi.control).mockResolvedValue(stopped); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('manual continuous paper control', () => {
  it('only reads on mount and separates cumulative cost from the fixed overall budget', async () => {
    render(<JevLiveTControl {...props} />);
    expect(screen.getByText('正在读取控制状态')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '开启持续纸上运行' })).toBeEnabled();
    expect(screen.getByText('累计未核验预算估值 / 总预算（美元）').parentElement).toHaveTextContent('$0.0081 / $10.0000');
    expect(screen.getByText('持续运行 · 不限轮数')).toBeInTheDocument();
    expect(screen.getByText(/重新开启不会重置额度/)).toBeInTheDocument();
    expect(jevLiveTApi.setControl).not.toHaveBeenCalled();
  });
  it('submits one explicit start even on double click and navigates using the confirmed run', async () => {
    let resolve!: (value: LiveTControl) => void;
    vi.mocked(jevLiveTApi.setControl).mockImplementation(() => new Promise(done => { resolve = done; }));
    render(<JevLiveTControl {...props} selectedRunId="historical-other" />);
    const start = await screen.findByRole('button', { name: '开启持续纸上运行' });
    fireEvent.click(start); fireEvent.click(start);
    expect(jevLiveTApi.setControl).toHaveBeenCalledTimes(1);
    expect(jevLiveTApi.setControl).toHaveBeenCalledWith(true, expect.any(AbortSignal));
    expect(screen.getByRole('button', { name: '正在开启…' })).toBeDisabled();
    vi.mocked(jevLiveTApi.control).mockResolvedValue(running);
    await act(async () => resolve(running));
    expect(onChanged).toHaveBeenCalledWith(running);
    expect(await screen.findByRole('button', { name: '关闭持续纸上运行' })).toBeEnabled();
  });
  it('stops and resumes the same retained account without sending reset or financial parameters', async () => {
    vi.mocked(jevLiveTApi.control).mockResolvedValue(running);
    vi.mocked(jevLiveTApi.setControl).mockResolvedValueOnce(stopped).mockResolvedValueOnce(running);
    render(<JevLiveTControl {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '关闭持续纸上运行' }));
    vi.mocked(jevLiveTApi.control).mockResolvedValue(stopped);
    fireEvent.click(await screen.findByRole('button', { name: '开启持续纸上运行' }));
    vi.mocked(jevLiveTApi.control).mockResolvedValue(running);
    await screen.findByRole('button', { name: '关闭持续纸上运行' });
    expect(vi.mocked(jevLiveTApi.setControl).mock.calls.map(call => call[0])).toEqual([false, true]);
    expect(onChanged.mock.calls.map(call => call[0].runId)).toEqual(['continuous-one', 'continuous-one']);
  });
  it('does not retry a failed mutation automatically and requires a fresh state before another action', async () => {
    vi.mocked(jevLiveTApi.setControl).mockRejectedValue(new Error('network unavailable'));
    render(<JevLiveTControl {...props} />);
    const start = await screen.findByRole('button', { name: '开启持续纸上运行' });
    vi.mocked(jevLiveTApi.control).mockImplementation(() => new Promise(() => {}));
    fireEvent.click(start);
    expect(await screen.findByRole('alert')).toHaveTextContent('操作结果尚未确认');
    expect(screen.getByRole('button', { name: '开启持续纸上运行' })).toBeDisabled();
    expect(jevLiveTApi.setControl).toHaveBeenCalledTimes(1);
  });
  it('ignores an older in-flight status read after a confirmed manual change', async () => {
    vi.useFakeTimers(); let staleRead!: (value: LiveTControl) => void;
    vi.mocked(jevLiveTApi.control).mockResolvedValueOnce(stopped).mockImplementationOnce(() => new Promise(done => { staleRead = done; })).mockResolvedValue(running);
    vi.mocked(jevLiveTApi.setControl).mockResolvedValue(running);
    await act(async () => { render(<JevLiveTControl {...props} />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开启持续纸上运行' })); });
    await act(async () => staleRead(stopped));
    expect(screen.getByRole('button', { name: '关闭持续纸上运行' })).toBeEnabled();
  });
  it('hides controls and account cost when the owner has no control permission', async () => {
    vi.mocked(jevLiveTApi.control).mockResolvedValue({ ...stopped, canControl: false, config: null, usage: null, message: '此账号无权限' });
    render(<JevLiveTControl {...props} />);
    await screen.findByText('此账号无权限');
    expect(screen.queryByRole('button', { name: /开启|关闭/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/总预算/)).not.toBeInTheDocument();
    expect(jevLiveTApi.setControl).not.toHaveBeenCalled();
  });
  it('shows market closure as a pause while keeping manual stop available and linking the current account', async () => {
    vi.mocked(jevLiveTApi.control).mockResolvedValue({ ...running, phase: 'market_closed' });
    render(<JevLiveTControl {...props} selectedRunId="old-run" />);
    await screen.findByText('休市暂停');
    expect(screen.getByRole('button', { name: '关闭持续纸上运行' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '查看当前持续账户' }));
    expect(onOpenRun).toHaveBeenCalledWith('continuous-one');
    expect(screen.getByText(/下方所选历史记录不会被改写/)).toBeInTheDocument();
    expect(jevLiveTApi.setControl).not.toHaveBeenCalled();
  });
  it('cannot restart an exhausted cumulative budget', async () => {
    vi.mocked(jevLiveTApi.control).mockResolvedValue({ ...stopped, phase: 'budget_exhausted', usage: { costUsd: 10 } });
    render(<JevLiveTControl {...props} />);
    await screen.findByText('累计预算已用尽');
    expect(screen.getByRole('button', { name: '开启持续纸上运行' })).toBeDisabled();
  });
  it('clears control state and reports lost authorization on a polling 401', async () => {
    vi.useFakeTimers();
    vi.mocked(jevLiveTApi.control).mockResolvedValueOnce(running).mockRejectedValue({ response: { status: 401 } });
    await act(async () => { render(<JevLiveTControl {...props} />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(onAccessLost).toHaveBeenCalledOnce();
    expect(screen.queryByText('$0.0081 / $10.0000')).not.toBeInTheDocument();
  });
  it('aborts a pending write and prevents callbacks after the identity-scoped panel unmounts', async () => {
    let resolve!: (value: LiveTControl) => void;
    vi.mocked(jevLiveTApi.setControl).mockImplementation(() => new Promise(done => { resolve = done; }));
    const page = render(<JevLiveTControl {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '开启持续纸上运行' }));
    const signal = vi.mocked(jevLiveTApi.setControl).mock.calls[0][1];
    page.unmount(); expect(signal?.aborted).toBe(true);
    await act(async () => resolve(running));
    expect(onChanged).not.toHaveBeenCalled();
  });
  it('recovers a failed initial status read through the explicit read-only retry', async () => {
    vi.mocked(jevLiveTApi.control).mockRejectedValueOnce(new Error('temporary failure')).mockResolvedValue(stopped);
    render(<JevLiveTControl {...props} />);
    fireEvent.click(await screen.findByRole('button', { name: '重新读取控制状态' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '开启持续纸上运行' })).toBeEnabled());
    expect(jevLiveTApi.setControl).not.toHaveBeenCalled();
  });
});
