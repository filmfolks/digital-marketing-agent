/**
 * Front-end tests for ui/nexus-tasks.js (pure helpers; no browser).
 *   node tests/test_tasks_ui.mjs
 */
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
await import(path.join(here, '..', 'ui', 'nexus-tasks.js'));
const T = globalThis.NexusTasksUI.__internals;

let passed = 0, failed = 0;
function test(name, fn) {
    try { fn(); passed++; console.log(`  ✔ ${name}`); }
    catch (e) { failed++; console.error(`  ✘ ${name}\n      ${e.message}`); }
}
console.log('\nNexus tasks UI');

test('month grid is Monday-first and covers the month', () => {
    const w = T.monthGrid(2026, 9);          // October 2026
    assert.equal(w[0][0], '2026-09-28');     // Monday before Thu 1 Oct
    assert.ok(w.flat().includes('2026-10-31'));
    w.forEach(week => assert.equal(week.length, 7));
});
test('week of a Sunday starts on the Monday before', () => {
    assert.deepEqual(T.weekOf('2026-10-18').slice(0, 1), ['2026-10-12']);
});
test('groupByDay uses the stored (user-tz) day, skips cancelled, sorts by time', () => {
    const g = T.groupByDay([
        { id: 'b', scheduled_at: '2026-10-20T19:00:00+05:30', status: 'approved' },
        { id: 'a', scheduled_at: '2026-10-20T09:00:00+05:30', status: 'pending_approval' },
        { id: 'c', scheduled_at: '2026-10-21T00:30:00+05:30', status: 'cancelled' },
        { id: 'd', scheduled_at: null, status: 'draft' }]);
    assert.deepEqual(Object.keys(g), ['2026-10-20']);
    assert.deepEqual(g['2026-10-20'].map(t => t.id), ['a', 'b']);
});
test('drag to another day keeps time and offset', () => {
    assert.equal(T.moveToDay('2026-10-20T19:00:00+05:30', '2026-10-23'), '2026-10-23T19:00:00+05:30');
    assert.equal(T.moveToDay(null, '2026-10-23'), '2026-10-23T10:00');
});
test('SSE parser handles split chunks', () => {
    let r = T.parseSSE('event: task\ndata: {"task":{"id":"t1"}}\n\nevent: te');
    assert.deepEqual(r.events, [['task', { task: { id: 't1' } }]]);
    r = T.parseSSE(r.rest + 'xt\ndata: {"text":"hi"}\n\n');
    assert.deepEqual(r.events, [['text', { text: 'hi' }]]);
});
test('brain mapping from Settings', () => {
    assert.deepEqual(T.brainFromSettings({ provider: 'local', localType: 'ollama', localUrl: 'http://localhost:11434/v1/', localModel: 'hermes3:8b' }),
        { provider: 'ollama', base_url: 'http://localhost:11434/v1', model: 'hermes3:8b' });
    assert.equal(T.brainFromSettings({ provider: 'cloud', cloudProvider: 'anthropic' }).provider, 'claude');
    assert.equal(T.brainFromSettings({ provider: 'antigravity' }).provider, 'antigravity');
    assert.equal(T.brainFromSettings({ provider: 'local' }, 'sho').provider, 'sho');
    assert.equal(T.brainFromSettings({ provider: 'builtin' }).provider, 'lmstudio');
    assert.equal(T.brainFromSettings({}, 'openrouter', () => ({ apiKey: 'k' })).api_key, 'k');
});
test('task card escapes model text and shows the right buttons', () => {
    const html = T.taskCardHtml({ id: 't1', title: '<img src=x onerror=alert(1)>', status: 'pending_approval',
        platforms: ['instagram'], content: 'hi', scheduled_at: '2026-10-20T19:00:00+05:30' });
    assert.ok(!html.includes('<img'));
    assert.ok(html.includes('data-act="approve"'));
    assert.ok(html.includes('value="2026-10-20T19:00"'));
    const done = T.taskCardHtml({ id: 't2', title: 'x', status: 'done', platforms: [] });
    assert.ok(!done.includes('data-act="approve"') && !done.includes('data-act="edit"'));
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
