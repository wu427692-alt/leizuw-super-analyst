import { beforeEach, describe, expect, it, vi } from 'vitest';
const { get, remove } = vi.hoisted(() => ({ get: vi.fn(), remove: vi.fn() }));
vi.mock('../index', () => ({ default: { get, delete: remove } }));
import { agentApi } from '../agent';
beforeEach(() => vi.resetAllMocks());
describe('chat session HTTP boundaries', () => {
  it('encodes session handles and preserves the message array contract', async () => {
    const messages = [{ id: 'one', role: 'assistant', content: 'answer' }];
    get.mockResolvedValue({ data: { session_id: 'a/b?c', messages } });
    expect(await agentApi.getChatSessionMessages('a/b?c')).toEqual(messages);
    expect(get).toHaveBeenCalledWith('/api/v1/agent/chat/sessions/a%2Fb%3Fc');
    await agentApi.deleteChatSession('a/b?c');
    expect(remove).toHaveBeenCalledWith('/api/v1/agent/chat/sessions/a%2Fb%3Fc');
  });
  it('rejects mismatched history rather than displaying another conversation', async () => {
    get.mockResolvedValue({ data: { session_id: 'other', messages: [] } });
    await expect(agentApi.getChatSessionMessages('mine')).rejects.toThrow('不匹配');
  });
  it('keeps older responses without a session field compatible', async () => {
    get.mockResolvedValue({ data: { messages: [] } });
    expect(await agentApi.getChatSessionMessages('mine')).toEqual([]);
  });
});
