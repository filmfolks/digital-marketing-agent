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
            '--mute-audio', '--hide-scrollbars', '--window-size=1440,1000',
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

let exitCode = 0;
const cleanup = () => {
    try { server.close(); } catch {}
    try { rmSync(PROFILE, { recursive: true, force: true }); } catch {}
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

        console.log('\n📸 Screenshots');
        for (const page of ['dashboard', 'brand', 'utm', 'analytics', 'calendar', 'trends']) {
            const file = path.join(SHOTS, `smoke-${page}.png`);
            rmSync(file, { force: true });
            // Wait for the PNG to exist AND stop growing — Chrome writes it in
            // stages and a partial file makes a corrupt preview.
            let lastSize = -1, stable = 0;
            const shot = await runChrome(['--screenshot=' + file, `${url}?page=${page}`], {
                timeoutMs: 60000,
                ready: () => {
                    if (!existsSync(file)) return false;
                    const size = readFileSync(file).length;
                    stable = size > 1500 && size === lastSize ? stable + 1 : 0;
                    lastSize = size;
                    return stable >= 2;
                },
            });
            if (existsSync(file) && readFileSync(file).length > 1500) {
                console.log(`  ✔ shots/smoke-${page}.png (${Math.round(readFileSync(file).length / 1024)} KB)`);
            } else {
                console.error(`  ✘ ${page} screenshot failed${shot.timedOut ? ' (timeout)' : ''}`);
                failed++;
            }
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
