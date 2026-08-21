export type RouteLoadToken = {
  sessionId: number;
  requestId: number;
};

export type RouteLoadSnapshot = {
  sessionId: number;
  routeKey: string;
  active: boolean;
  started: number;
  completed: number;
  pending: number;
  startedAt: number;
  lastActivityAt: number;
};

const listeners = new Set<() => void>();
let nextSessionId = 0;
let nextRequestId = 0;
let acceptingUntil = 0;
let snapshot: RouteLoadSnapshot = {
  sessionId: 0,
  routeKey: '',
  active: false,
  started: 0,
  completed: 0,
  pending: 0,
  startedAt: 0,
  lastActivityAt: 0,
};

function publish(next: RouteLoadSnapshot) {
  snapshot = next;
  listeners.forEach((listener) => listener());
}

export function beginRouteLoad(routeKey: string): number {
  const now = Date.now();
  const sessionId = ++nextSessionId;
  // React effects dispatch immediately after the route mounts. Admit that
  // first batch only; delayed polling and secondary panels must never keep the
  // whole route behind the loading gate.
  acceptingUntil = now + 750;
  publish({
    sessionId,
    routeKey,
    active: true,
    started: 0,
    completed: 0,
    pending: 0,
    startedAt: now,
    lastActivityAt: now,
  });
  return sessionId;
}

export function finishRouteLoad(sessionId: number) {
  if (!snapshot.active || snapshot.sessionId !== sessionId) return;
  publish({ ...snapshot, active: false, lastActivityAt: Date.now() });
}

export function startRouteRequest(): RouteLoadToken | null {
  if (!snapshot.active) return null;
  const now = Date.now();
  const insideInitialWindow = now <= acceptingUntil;
  const chainedToInitialLoad = snapshot.pending > 0 && now - snapshot.startedAt <= 3_000;
  if (!insideInitialWindow && !chainedToInitialLoad) return null;
  const token = { sessionId: snapshot.sessionId, requestId: ++nextRequestId };
  publish({
    ...snapshot,
    started: snapshot.started + 1,
    pending: snapshot.pending + 1,
    lastActivityAt: now,
  });
  return token;
}

export function finishRouteRequest(token?: RouteLoadToken | null) {
  if (!token || token.sessionId !== snapshot.sessionId || !snapshot.active) return;
  publish({
    ...snapshot,
    completed: snapshot.completed + 1,
    pending: Math.max(0, snapshot.pending - 1),
    lastActivityAt: Date.now(),
  });
}

export function getRouteLoadSnapshot(): RouteLoadSnapshot {
  return snapshot;
}

export function subscribeRouteLoad(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function resetRouteLoadTrackerForTests() {
  nextSessionId = 0;
  nextRequestId = 0;
  acceptingUntil = 0;
  snapshot = {
    sessionId: 0,
    routeKey: '',
    active: false,
    started: 0,
    completed: 0,
    pending: 0,
    startedAt: 0,
    lastActivityAt: 0,
  };
  listeners.clear();
}
