import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  beginRouteLoad,
  finishRouteLoad,
  finishRouteRequest,
  getRouteLoadSnapshot,
  resetRouteLoadTrackerForTests,
  startRouteRequest,
} from '../routeLoadTracker';

describe('routeLoadTracker', () => {
  beforeEach(() => {
    vi.useRealTimers();
    resetRouteLoadTrackerForTests();
  });

  it('counts parallel initial requests and ignores stale completions from the previous route', () => {
    const firstSession = beginRouteLoad('/');
    const first = startRouteRequest();
    const second = startRouteRequest();
    expect(getRouteLoadSnapshot()).toMatchObject({ sessionId: firstSession, started: 2, pending: 2 });

    finishRouteRequest(first);
    expect(getRouteLoadSnapshot()).toMatchObject({ completed: 1, pending: 1 });

    const nextSession = beginRouteLoad('/essay-radar');
    finishRouteRequest(second);
    expect(getRouteLoadSnapshot()).toMatchObject({ sessionId: nextSession, completed: 0, pending: 0 });
  });

  it('stops accepting requests after the route becomes visible', () => {
    const session = beginRouteLoad('/super-watchlist');
    const token = startRouteRequest();
    finishRouteRequest(token);
    finishRouteLoad(session);

    expect(startRouteRequest()).toBeNull();
    expect(getRouteLoadSnapshot().active).toBe(false);
  });
});
