import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));

import apiClient from '../index';
import { essayQuantApi } from '../essayQuant';

describe('essayQuantApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('normalizes numeric suffix fields used by the dashboard', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        summary: { first_mention_30d_count: 2 },
        first_mentions_30d: [{ topic_id: 'one' }],
        trend_signals: [{ symbol: '000001.SZ', momentum_20d: 3.5 }],
      },
    });

    const result = await essayQuantApi.dashboard();

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/essay-quant/institution-dashboard');
    expect(result.summary.firstMention30dCount).toBe(2);
    expect(result.firstMentions30d[0].topicId).toBe('one');
    expect(result.trendSignals[0].momentum20d).toBe(3.5);
  });

  it('reads background institution-ranking precompute status', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { running: true, computing: false, last_result: { ranked_group_count: 8 } } });
    const result = await essayQuantApi.precomputeStatus();
    expect(result.running).toBe(true);
    expect(result.lastResult?.rankedGroupCount).toBe(8);
  });
});
