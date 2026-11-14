import { beforeEach, describe, expect, it } from 'vitest';
import {
  findResearchSession,
  readResearchSessions,
  RESEARCH_SESSION_LIMIT,
  saveResearchSession,
  type ResearchSessionRecord,
} from '../researchSessionStore';

const record = (sessionId: string, topic = '测试议题'): ResearchSessionRecord => ({
  sessionId,
  topic,
  stock: '300750',
  stockName: '宁德时代',
  cycle: '中线',
  at: '2026-11-13T10:00:00.000Z',
});

beforeEach(() => {
  localStorage.clear();
});

describe('researchSessionStore', () => {
  it('keeps the newest research first and dedupes the same session', () => {
    saveResearchSession('user:1', record('a', '议题 A'));
    saveResearchSession('user:1', record('b', '议题 B'));
    saveResearchSession('user:1', record('a', '议题 A 改'));

    const sessions = readResearchSessions('user:1');
    expect(sessions.map((item) => item.topic)).toEqual(['议题 A 改', '议题 B']);
  });

  it('isolates records per owner and caps the history size', () => {
    for (let index = 0; index < RESEARCH_SESSION_LIMIT + 2; index += 1) {
      saveResearchSession('user:1', record(`s${index}`, `议题 ${index}`));
    }
    saveResearchSession('user:2', record('other', '另一个人的研究'));

    expect(readResearchSessions('user:1')).toHaveLength(RESEARCH_SESSION_LIMIT);
    expect(readResearchSessions('user:1').some((item) => item.topic === '另一个人的研究')).toBe(false);
    expect(readResearchSessions('user:2').map((item) => item.topic)).toEqual(['另一个人的研究']);
  });

  it('finds the research behind a chat session id', () => {
    saveResearchSession('user:1', record('research-1', '白酒还能追吗'));
    expect(findResearchSession('user:1', 'research-1')?.topic).toBe('白酒还能追吗');
    expect(findResearchSession('user:1', 'plain-chat')).toBeNull();
    expect(findResearchSession('user:1', '')).toBeNull();
  });

  it('treats corrupt storage as empty instead of throwing', () => {
    localStorage.setItem('dsa:research-sessions:v1:user:1', '{not json');
    expect(readResearchSessions('user:1')).toEqual([]);
    localStorage.setItem('dsa:research-sessions:v1:user:1', JSON.stringify([{ sessionId: 1 }]));
    expect(readResearchSessions('user:1')).toEqual([]);
  });
});
