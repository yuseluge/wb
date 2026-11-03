/* 我的工作台 —— Service Worker
   目标：断网也能打开（用上次缓存的学习通数据），有网时静默更新。 */

const V = 'wb-v4';
const SHELL = ['./', './index.html', './manifest.webmanifest', './icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;

  // 采集数据和页面本身都用「网络优先」。
  // 页面必须是网络优先 —— 否则改完界面，用户看到的还是缓存里的旧版本。
  const isDoc = request.mode === 'navigate' || url.pathname.endsWith('.html');
  const isData = url.pathname.endsWith('activities.enc');

  if (isDoc || isData) {
    e.respondWith(
      fetch(request)
        .then((r) => {
          const cp = r.clone();
          caches.open(V).then((c) => c.put(request, cp));
          return r;
        })
        .catch(() => caches.match(request).then((hit) =>
          hit || (isDoc ? caches.match('./index.html') : undefined)))
    );
    return;
  }

  // 图标、manifest 这类几乎不变的资源走「缓存优先 + 后台更新」
  e.respondWith(
    caches.match(request).then((hit) => {
      const net = fetch(request)
        .then((r) => {
          if (r.ok) {
            const cp = r.clone();
            caches.open(V).then((c) => c.put(request, cp));
          }
          return r;
        })
        .catch(() => hit);
      return hit || net;
    })
  );
});
