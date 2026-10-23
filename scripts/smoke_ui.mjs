/**
 * Headless UI smoke test
 * ======================
 *   node scripts/smoke_ui.mjs
 *
 * Loads the real ui/index.html in headless Chrome and proves it actually boots
 * and works — not merely that the files parse. Implementation note: driving
 * Chrome's DevTools WebSocket protocol from Node proved unreliable here, so
 * instead this serves a small same-origin harness page (from memory — nothing is
 * written into ui/), which:
 *
 *   1. boots index.html inside an iframe,
 *   2. waits for the app's asynchronous init,
 *   3. runs assertions against the iframe's own window and DOM,
 *   4. writes a JSON report into its own <pre>.
 *
 * Chrome's --dump-dom then prints the harness DOM, which we parse. Load-time
 * errors are read back from the app's own persisted AgentLog, since the app
 * already installs window 'error' / 'unhandledrejection' handlers that record
 * them — so a clean report means the app really did boot without throwing.
 *
 * Also captures screenshots of the main pages.
 * Exits 0 (skipped) when Chrome is unavailable, so CI still runs on bare hosts.
 */

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import http from 'node:http';

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, '..');
const UI = path.join(ROOT, 'ui');
const SHOTS = path.join(ROOT, 'shots');

const CHROME_CANDIDATES = [
    process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
].filter(Boolean);

const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!chromePath) {
    console.log('⚠️  Chrome not found — skipping the headless UI smoke test.');
    process.exit(0);
}

const PORT = Number(process.env.SMOKE_PORT || 8123);
const WINDOW = process.env.SMOKE_WINDOW || '1440,1000';
const SUFFIX = process.env.SMOKE_SUFFIX || '';
const KEYS = process.env.SMOKE_PAGES ? process.env.SMOKE_PAGES.split(',') : ['dashboard', 'brand', 'utm', 'analytics', 'calendar', 'trends'];
const PROFILE = path.join(ROOT, '.smoke-chrome-profile');

/* ────────────────────────────────────────────────────────────────────────────
   Harness: same origin as the app, so every global is reachable.
   ──────────────────────────────────────────────────────────────────────────── */
const HARNESS = readFileSync(path.join(here, 'smoke_harness.html'), 'utf8');

/* ── static server: ui/ plus the in-memory harness ───────────────────────── */
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml' };
const TINY_GIF = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');

let openGate;
let gate = new Promise((resolve) => { openGate = resolve; });
const resetGate = () => { gate = new Promise((resolve) => { openGate = resolve; }); };
// Safety valve: never let a harness bug hang the whole run.
setInterval(() => openGate && openGate(), 45000).unref?.();

const server = http.createServer(async (req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
    if (rel === '/__smoke.html') {
        resetGate();
        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(HARNESS);
        return;
    }
    if (rel === '/__gate') {
        await gate;
        res.writeHead(200, { 'Content-Type': 'image/gif', 'Cache-Control': 'no-store' });
        res.end(TINY_GIF);
        return;
    }
    if (rel === '/__done') {
        if (openGate) openGate();
        res.writeHead(200, { 'Content-Type': 'text/plain' });
        res.end('ok');
        return;
    }
    const file = path.join(UI, rel === '/' ? 'index.html' : rel);
    if (!file.startsWith(UI) || !existsSync(file)) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(readFileSync(file));
});
await new Promise((resolve, reject) => { server.on('error', reject); server.listen(PORT, '127.0.0.1', resolve); });

mkdirSync(SHOTS, { recursive: true });
rmSync(PROFILE, { recursive: true, force: true });
mkdirSync(PROFILE, { recursive: true });

/**
 * Run headless Chrome until its artifact is ready, then kill it.
 *
 * Two Chrome 154 quirks drive this design:
 *   • plain `--headless` produces --dump-dom/--screenshot output but never exits
 *     on its own, so waiting for the process to close hangs forever;
 *   • `--virtual-time-budget` hangs outright in this build.
 * So we poll for the artifact and kill the process as soon as it exists.
 */
function runChrome(extraArgs, { timeoutMs = 60000, ready = null } = {}) {
    return new Promise((resolve) => {
        const child = spawn(chromePath, [
            '--headless', `--user-data-dir=${PROFILE}`,
            '--no-first-run', '--no-default-browser-check',
            '--disable-gpu', '--disable-background-networking', '--disable-sync',
            '--mute-audio', '--hide-scrollbars', `--window-size=${WINDOW}`,
            // Sandboxed/CI hosts (and the DSH file sandbox) cannot create
            // Chrome's crashpad dirs or its own sandbox — keep it simple.
            '--no-sandbox', '--disable-breakpad', '--disable-crash-reporter',
            '--disable-dev-shm-usage', `--crash-dumps-dir=${PROFILE}`,
            ...extraArgs,
        ], { stdio: ['ignore', 'pipe', 'pipe'] });

        let out = '', err = '', settled = false;
        const finish = (timedOut) => {
            if (settled) return;
            settled = true;
            clearInterval(poll);
            clearTimeout(deadline);
            try { child.kill('SIGKILL'); } catch {}
            resolve({ out, err, timedOut });
        };

        child.stdout.on('data', (c) => {
            out += c;
            if (ready && ready({ out, err })) finish(false);
        });
        child.stderr.on('data', (c) => { err += c; });
        child.on('close', () => finish(false));

        const poll = setInterval(() => { if (ready && ready({ out, err })) finish(false); }, 250);
        const deadline = setTimeout(() => finish(true), timeoutMs);
    });
}

/* ────────────────────────────────────────────────────────────────────────────
   Real-viewport capture over the DevTools protocol.

   Chrome refuses to make a headless window narrower than 500 CSS px on macOS
   (measured: --window-size=390 renders at 500), which silently crops every
   "mobile" --screenshot. Driving Emulation.setDeviceMetricsOverride over CDP is
   the only way to render a true 390 px viewport. Node 22+ ships a WebSocket
   global, so no dependency is needed.
   ──────────────────────────────────────────────────────────────────────────── */
function cdpLaunch() {
    return new Promise((resolve, reject) => {
        const profile = PROFILE + '-cdp';
        const child = spawn(chromePath, [
            '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
            '--no-first-run', '--no-default-browser-check',
            '--disable-gpu', '--disable-background-networking', '--disable-sync',
            '--mute-audio', '--hide-scrollbars',
            '--no-sandbox', '--disable-breakpad', '--disable-crash-reporter',
            '--disable-dev-shm-usage', `--crash-dumps-dir=${profile}`,
            'about:blank',
        ], { stdio: ['ignore', 'pipe', 'pipe'] });
        let buf = '';
        let done = false;
        const timer = setTimeout(() => { if (!done) { done = true; reject(new Error('timed out waiting for the DevTools endpoint')); } }, 20000);
        const onErr = (chunk) => {
            buf += chunk;
            const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
            if (m && !done) { done = true; clearTimeout(timer); child.stderr.off('data', onErr); resolve({ child, wsUrl: m[1] }); }
        };
        child.stderr.on('data', onErr);
        child.on('error', (e) => { if (!done) { done = true; clearTimeout(timer); reject(e); } });
        child.on('exit', (code) => { if (!done && code != null) { done = true; clearTimeout(timer); reject(new Error(`Chrome exited early (${code}): ${buf.slice(0, 300)}`)); } });
    });
}

function connectWs(wsUrl) {
    return new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        const timer = setTimeout(() => reject(new Error('DevTools WebSocket connect timed out')), 10000);
        ws.addEventListener('open', () => { clearTimeout(timer); resolve(ws); });
        ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('DevTools WebSocket error')); });
    });
}

class CDP {
    constructor(ws) {
        this.ws = ws; this.seq = 0; this.pending = new Map(); this.listeners = [];
        ws.addEventListener('message', (ev) => {
            let msg; try { msg = JSON.parse(ev.data); } catch { return; }
            if (msg.id && this.pending.has(msg.id)) {
                const p = this.pending.get(msg.id); this.pending.delete(msg.id);
                msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result);
            } else if (msg.method) {
                for (const l of this.listeners.slice()) l(msg);
            }
        });
    }
    send(method, params = {}, sessionId) {
        const id = ++this.seq;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            this.ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
        });
    }
    once(method, sessionId, timeoutMs = 20000) {
        return new Promise((resolve, reject) => {
            const t = setTimeout(() => { off(); reject(new Error('timeout waiting for ' + method)); }, timeoutMs);
            const fn = (msg) => { if (msg.method === method && (!sessionId || msg.sessionId === sessionId)) { clearTimeout(t); off(); resolve(msg.params); } };
            const off = () => { this.listeners = this.listeners.filter((l) => l !== fn); };
            this.listeners.push(fn);
        });
    }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function cdpEval(cdp, sessionId, expression) {
    const r = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, sessionId);
    return r && r.result ? r.result.value : undefined;
}

/** Load index.html directly at the emulated viewport and wait for the app to boot. */
async function cdpOpen(cdp, base, width, height) {
    const { targetId } = await cdp.send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await cdp.send('Target.attachToTarget', { targetId, flatten: true });
    await cdp.send('Page.enable', {}, sessionId);
    await cdp.send('Runtime.enable', {}, sessionId);
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 820 }, sessionId);
    const loaded = cdp.once('Page.loadEventFired', sessionId).catch(() => {});
    await cdp.send('Page.navigate', { url: `${base}/index.html` }, sessionId);
    await loaded;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
        const ready = await cdpEval(cdp, sessionId, 'typeof switchPage === "function" && !!document.getElementById("page-dashboard")').catch(() => false);
        if (ready) break;
        await sleep(200);
    }
    await sleep(1600);   // init(), relay probe and persisted state settle
    return { targetId, sessionId };
}

async function cdpCapturePage(cdp, base, page, width, height) {
    const { targetId, sessionId } = await cdpOpen(cdp, base, width, height);
    try {
        await cdpEval(cdp, sessionId, `(() => {
            try {
                const n = document.getElementById('notifPanel'); if (n) n.classList.remove('open');
                const t = document.getElementById('toastContainer'); if (t) t.innerHTML = '';
                switchPage(${JSON.stringify(page)});
            } catch (e) {}
            return true;
        })()`);
        await sleep(500);
        const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, sessionId);
        return Buffer.from(shot.data, 'base64');
    } finally {
        await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
    }
}

async function cdpOverflowAudit(cdp, base, width, height) {
    const { targetId, sessionId } = await cdpOpen(cdp, base, width, height);
    try {
        const pages = await cdpEval(cdp, sessionId, '[...document.querySelectorAll(".nav-item")].map(n => n.dataset.page)') || [];
        const lines = [];
        let worst = 0;
        for (const pg of pages) {
            await cdpEval(cdp, sessionId, `try{switchPage(${JSON.stringify(pg)})}catch(e){}`);
            await sleep(70);
            const res = await cdpEval(cdp, sessionId, `(() => {
                const de = document.documentElement, vw = de.clientWidth, ov = de.scrollWidth - vw;
                const bad = [...document.querySelectorAll('#page-' + ${JSON.stringify(pg)} + ' *')]
                    .map(el => ({ el, r: el.getBoundingClientRect() }))
                    .filter(o => o.r.right > vw + 1)
                    .slice(0, 5)
                    .map(o => o.el.tagName.toLowerCase() + (o.el.id ? '#' + o.el.id : '') +
                        (typeof o.el.className === 'string' && o.el.className.trim() ? '.' + o.el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '') +
                        '@' + Math.round(o.r.right));
                return { ov, vw, bad };
            })()`);
            if (res) { worst = Math.max(worst, res.ov); lines.push(`${pg}: ov=${res.ov} vw=${res.vw}${res.bad.length ? ' :: ' + res.bad.join(' ') : ''}`); }
        }
        return { worst, lines };
    } finally {
        await cdp.send('Target.closeTarget', { targetId }).catch(() => {});
    }
}

let exitCode = 0;
const cleanup = () => {
    try { server.close(); } catch {}
    try { rmSync(PROFILE, { recursive: true, force: true }); } catch {}
    try { rmSync(PROFILE + '-cdp', { recursive: true, force: true }); } catch {}
};
process.on('exit', cleanup);

try {
    console.log(`\n🔎 Headless smoke test — ${chromePath}`);
    const url = `http://127.0.0.1:${PORT}/__smoke.html`;
    const { out, err, timedOut } = await runChrome(['--dump-dom', url], {
        timeoutMs: 70000,
        ready: ({ out: o }) => o.includes('SMOKE_JSON:'),
    });

    const marker = out.match(/SMOKE_JSON:(\[.*?\])<\/pre>/s) || out.match(/SMOKE_JSON:(\[[\s\S]*\])/);
    if (!marker) {
        console.error('✘ could not read the harness report');
        if (timedOut) console.error('  Chrome timed out.');
        console.error((out || err).slice(0, 1600));
        exitCode = 1;
    } else {
        const results = JSON.parse(marker[1]);
        let passed = 0, failed = 0;
        for (const r of results) {
            if (r.ok) { passed++; console.log(`  ✔ ${r.name}`); }
            else { failed++; console.error(`  ✘ ${r.name}${r.detail ? `\n      ${r.detail}` : ''}`); }
        }

        const base = `http://127.0.0.1:${PORT}`;
        const [winW, winH] = (process.env.SMOKE_WINDOW || WINDOW).split(',').map(Number);
        let cdp = null, chromeCdp = null;
        try {
            const launched = await cdpLaunch();
            chromeCdp = launched.child;
            cdp = new CDP(await connectWs(launched.wsUrl));

            if (process.env.SMOKE_PROBE) {
                const audit = await cdpOverflowAudit(cdp, base, winW, winH);
                console.log(`\n📐 Overflow audit @ ${winW}x${winH}`);
                for (const line of audit.lines) console.log('   ' + line);
                if (audit.worst > 1) { console.error(`   ✘ horizontal overflow up to ${audit.worst}px`); failed++; }
                else console.log('   ✔ no horizontal overflow on any page');
            } else {
                console.log(`\n📸 Screenshots @ ${winW}x${winH}`);
                for (const page of KEYS) {
                    const file = path.join(SHOTS, `smoke-${page}${SUFFIX}.png`);
                    rmSync(file, { force: true });
                    try {
                        const png = await cdpCapturePage(cdp, base, page, winW, winH);
                        if (png && png.length > 1500) {
                            writeFileSync(file, png);
                            console.log(`  ✔ shots/smoke-${page}${SUFFIX}.png (${Math.round(png.length / 1024)} KB)`);
                        } else { throw new Error('empty capture'); }
                    } catch (e) { console.error(`  ✘ ${page} screenshot failed: ${e.message}`); failed++; }
                }
            }
        } catch (e) {
            console.error(`  ✘ CDP capture unavailable: ${e.message}`);
            failed++;
        } finally {
            try { cdp && cdp.ws.close(); } catch {}
            try { chromeCdp && chromeCdp.kill('SIGKILL'); } catch {}
        }

        console.log(`\n${passed} passed, ${failed} failed\n`);
        exitCode = failed ? 1 : 0;
    }
} catch (err) {
    console.error(`\n💥 Smoke test crashed: ${err.message}\n`);
    exitCode = 1;
} finally {
    cleanup();
}

process.exit(exitCode);
