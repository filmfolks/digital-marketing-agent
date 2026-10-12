/**
 * Nexus service worker
 * =====================
 * Goals: installable PWA, usable offline, and NEVER a source of stale or leaked
 * data. The first version was cache-first for everything, which meant:
 *
 *   • Bug 1 — `index.html` came from cache forever, so after any update the user
 *     kept running an old UI with no way to refresh out of it.
 *   • Bug 2 — it cached every GET response, including cross-origin API traffic
 *     (AI provider responses, Telegram, relay health). Authenticated responses
 *     must never be written into the Cache Storage API.
 *   • Bug 3 — `cached || fetch(...)` could poison the cache with an opaque or
 *     redirected response that later fails to decode.
 *
 * Strategy now:
 *   navigation / HTML  → network-first, cache fallback when offline
 *   same-origin assets → stale-while-revalidate (fast, self-healing)
 *   cross-origin       → pass through untouched (never cached)
 *   non-GET            → pass through (publishing, AI calls, relay)
 */

const VERSION = 'v8';
const CACHE = `nexus-ui-${VERSION}`;
const PRECACHE = [
    './',
    './index.html',
    './style.css',
    './script.js',
    './ai-client.js',
    './nexus-tasks.js',
    './client-config.js',
    './favicon.svg',
    './manifest.json',
    './icon-192.png',
    './icon-512.png',
    './icon-maskable-512.png',
    './apple-touch-icon.png',
];

// Hosts whose responses must never be captured by the cache.
const NEVER_CACHE_HOSTS = [
    'localhost',
    '127.0.0.1',
    'api.telegram.org',
    'api.openai.com',
    'api.anthropic.com',
    'api.x.ai',
    'api.groq.com',
    'api.deepseek.com',
    'openrouter.ai',
    'generativelanguage.googleapis.com',
    'dashscope.aliyuncs.com',
    'api.canva.com',
    'graph.facebook.com',
    'api.linkedin.com',
    'api.twitter.com',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE)
            // addAll is all-or-nothing; add() each so one 404 cannot block install.
            .then((cache) => Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => {}))))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

/** Let the page trigger an immediate update after a deploy. */
self.addEventListener('message', (event) => {
    if (event.data === 'SKIP_WAITING') self.skipWaiting();
});

function isCacheableSameOrigin(url) {
    if (url.origin !== self.location.origin) return false;
    if (NEVER_CACHE_HOSTS.includes(url.hostname)) return false;
    // When the PWA is served by the relay (/app/ on a LAN IP), the relay's API
    // (/health, /queue, /shots/…) shares the origin. Only cache files inside
    // this worker's scope, never API responses.
    const scope = (self.registration && self.registration.scope) || self.location.href.replace(/[^/]*$/, '');
    if (!url.href.startsWith(scope)) return false;
    return true;
}

async function networkFirst(request) {
    const cache = await caches.open(CACHE);
    try {
        const fresh = await fetch(request);
        if (fresh && fresh.ok && fresh.type === 'basic') {
            cache.put(request, fresh.clone()).catch(() => {});
        }
        return fresh;
    } catch (err) {
        const cached = await cache.match(request);
        if (cached) return cached;
        const shell = await cache.match('./index.html');
        if (shell) return shell;
        throw err;
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(request);
    const network = fetch(request).then((res) => {
        if (res && res.ok && res.type === 'basic') {
            cache.put(request, res.clone()).catch(() => {});
        }
        return res;
    }).catch(() => null);
    return cached || (await network) || Response.error();
}

self.addEventListener('fetch', (event) => {
    const { request } = event;

    // Never touch anything that changes state or carries credentials.
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    if (url.hostname && NEVER_CACHE_HOSTS.includes(url.hostname)) return;
    if (request.headers.has('Authorization') || request.headers.has('X-Nexus-Token')) return;

    // HTML / navigations: freshness matters more than speed.
    if (request.mode === 'navigate' || (request.headers.get('Accept') || '').includes('text/html')) {
        event.respondWith(networkFirst(request));
        return;
    }

    if (!isCacheableSameOrigin(url)) return;   // leave cross-origin alone
    event.respondWith(staleWhileRevalidate(request));
});
