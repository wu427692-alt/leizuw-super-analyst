/** Standalone document navigation deliberately does not prefetch other SPAs. */
export function preloadRoute(): Promise<void> { return Promise.resolve(); }
export function preloadRouteData(): Promise<void> { return Promise.resolve(); }
