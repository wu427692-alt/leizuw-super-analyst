import { useCallback, useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { dataAcquisitionApi } from '../api/dataAcquisition';
import type { AcquisitionRunTask } from '../types/dataAcquisition';

type Snapshot = { id: string; task: AcquisitionRunTask | null; notice: string; unavailable: boolean };

/** Read an exact background task, retaining a verified snapshot during transient failures. */
export function useAcquisitionTask(taskId: string, onSnapshot: (task: AcquisitionRunTask) => void) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ id: '', task: null, notice: '', unavailable: false });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!taskId) return;
    let cancelled = false, inFlight = false, finished = false, failures = 0;
    let timer = 0;
    const isVisible = () => document.visibilityState !== 'hidden';
    const poll = async () => {
      if (cancelled || inFlight || finished || !isVisible()) return;
      window.clearTimeout(timer);
      inFlight = true;
      try {
        const task = await dataAcquisitionApi.task(taskId);
        if (cancelled) return;
        if (task.taskId !== taskId) throw new Error('Task response identity mismatch');
        failures = 0;
        setSnapshot({ id: taskId, task, notice: '', unavailable: false });
        onSnapshot(task);
        finished = task.status === 'completed' || task.status === 'failed';
      } catch (error) {
        if (cancelled) return;
        const status = isAxiosError(error) ? error.response?.status : undefined;
        finished = status === 401 || status === 403 || status === 404;
        failures += 1;
        setSnapshot(current => ({
          id: taskId,
          task: finished ? null : current.id === taskId ? current.task : null,
          notice: finished ? '该任务不存在、已过期或当前账号无权查看，请选择其他任务。'
            : '任务状态暂时未更新，保留最近进度并自动重试，不会重复提交任务。',
          unavailable: finished,
        }));
      } finally {
        inFlight = false;
        if (!cancelled && !finished && isVisible()) {
          timer = window.setTimeout(() => void poll(), Math.min(30_000, failures ? 5_000 * 2 ** (failures - 1) : 3_000));
        }
      }
    };
    const resume = () => {
      window.clearTimeout(timer);
      if (isVisible()) void poll();
    };
    void poll();
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', resume);
    };
  }, [taskId, onSnapshot, attempt]);
  const current = snapshot.id === taskId ? snapshot : { id: taskId, task: null, notice: '', unavailable: false };
  return { ...current, retry, loading: Boolean(taskId && !current.task && !current.unavailable) };
}
