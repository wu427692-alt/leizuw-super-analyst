/*
 * 乐子乌智能投研平台 Service Worker
 *
 * 目标：装到主屏后能像应用一样启动，弱网下不至于白屏。
 * - 导航请求：网络优先，失败时回退到缓存的应用外壳（保证不会拿到旧版本 JS）。
 * - 静态资源：带 hash 的文件缓存优先；未带 hash 的（图标、manifest）网络优先。
 * - 接口（/api/、/meeting-embed 等）：一律不缓存，直接放行。
 */

const VERSION = 'leziwu-shell-v1';
const SHELL_CACHE = `${VERSION}-shell`;
const ASSET_CACHE = `${VERSION}-assets`;
const SHELL_URLS = ['/', '/index.html', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_URLS).catch(() => undefined))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => !key.startsWith(VERSION))
          .map((key) => caches.delete(key)),
      ))
      .then(() => self.clients.claim()),
  );
});

const isAssetRequest = (url) => /\/assets\/[^/]+\.(js|css|woff2?|png|jpg|jpeg|svg|webp|json)$/i.test(url.pathname);
const isApiRequest = (url) => url.pathname.startsWith('/api/') || url.pathname.startsWith('/meeting-embed');

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isApiRequest(url)) return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        const cache = await caches.open(SHELL_CACHE);
        cache.put('/index.html', response.clone()).catch(() => undefined);
        return response;
      } catch {
        const cached = await caches.match('/index.html');
        return cached ?? Response.error();
      }
    })());
    return;
  }

  if (!isAssetRequest(url)) return;

  event.respondWith((async () => {
    const cached = await caches.match(request);
    if (cached) return cached;
    try {
      const response = await fetch(request);
      if (response.ok) {
        const cache = await caches.open(ASSET_CACHE);
        cache.put(request, response.clone()).catch(() => undefined);
      }
      return response;
    } catch {
      return Response.error();
    }
  })());
});
