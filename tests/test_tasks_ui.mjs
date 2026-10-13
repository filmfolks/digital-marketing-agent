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
    assert.equal(T.moveToDay(null, '2026-10-23'), '2026-10-23T10:00:00+05:30');
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

test('datetime-local value becomes tz-aware ISO in the stored offset', () => {
    assert.equal(T.fromLocalInput('2026-10-13T08:45', '2026-10-20T17:00:00+05:30'), '2026-10-13T08:45:00+05:30');
    assert.equal(T.fromLocalInput('2026-10-13T08:45', null), '2026-10-13T08:45:00+05:30');
    assert.equal(T.fromLocalInput('', 'x'), '');
});
test('edit sends only changed fields (rescheduling an approved task)', () => {
    const orig = { title: 'A', content: 'B', poster_idea: '', platforms: ['x'], scheduled_at: '2026-10-20T17:00:00+05:30' };
    assert.deepEqual(T.changedFields(orig, { title: 'A', content: 'B', poster_idea: '', platforms: ['x'],
        scheduled_at: '2026-10-13T08:45:00+05:30' }), { scheduled_at: '2026-10-13T08:45:00+05:30' });
    assert.deepEqual(T.changedFields(orig, { title: 'A', platforms: ['x'], scheduled_at: '2026-10-20T17:00:00+05:30' }), {});
});
test('drag to another day keeps time and offset', () => {
    assert.equal(T.moveToDay('2026-10-20T17:00:00+05:30', '2026-10-13'), '2026-10-13T17:00:00+05:30');
});
test('task card shows the image preview and a Generate/Regenerate image button', () => {
    const none = T.taskCardHtml({ id: 't3', title: 'x', status: 'approved', platforms: ['instagram'], media: [] });
    assert.ok(none.includes('data-act="image"') && none.includes('Generate image') && none.includes('nt-media-missing'));
    const withImg = T.taskCardHtml({ id: 't4', title: 'x', status: 'pending_approval', platforms: ['instagram'],
        media: ['/a/data/brands/b/media/t4-instagram.png"onerror="x'], media_source: 'card' });
    assert.ok(withImg.includes('Regenerate image') && withImg.includes('<img src="') && withImg.includes('brand card'));
    assert.ok(!withImg.includes('"onerror'), 'media path is escaped');
    assert.ok(T.mediaSrc('/x/y.png').includes('/activity/media?path=%2Fx%2Fy.png'));
    assert.equal(T.mediaSrc('https://cdn/x.png'), 'https://cdn/x.png');
    const done = T.taskCardHtml({ id: 't5', title: 'x', status: 'done', platforms: ['instagram'], media: ['/p.png'] });
    assert.ok(!done.includes('data-act="image"'));
    assert.deepEqual(T.imageEdits({ headline: ' Hi ', keywords: 'a, b,', bg: '', provider: 'card' }),
        { headline: 'Hi', keywords: ['a', 'b'], provider: 'card' });
});

test('approved status label indicates waiting for posted', () => {
    assert.equal(T.STATUS_LABEL.approved, 'Approved · waiting for posted');
    const card = T.taskCardHtml({ id: 't6', title: 'Scheduled post', status: 'approved', platforms: ['x'], scheduled_at: '2026-10-25T10:00:00+05:30' });
    assert.ok(card.includes('Approved · waiting for posted'));
});

test('NexusTaskTimer has robust API, signature generator, and storage helpers', () => {
    const timer = T.NexusTaskTimer;
    assert.ok(timer && typeof timer.tick === 'function');
    assert.ok(typeof timer.start === 'function' && typeof timer.stop === 'function');
    const sig = timer.taskSignature({ id: 'task-1', status: 'failed', error: 'locator timeout', scheduled_at: '2026-10-20T10:00:00+05:30' });
    assert.equal(sig, 'task-1:failed:locator timeout:2026-10-20T10:00:00+05:30');
});

test('showUnpostedPopup renders popup modal with reason and action buttons', () => {
    // Set up a mock DOM document
    const createdElements = [];
    globalThis.document = {
        getElementById: id => (id === 'ntUnpostedPopup' ? null : null),
        createElement: tag => {
            const classes = new Set();
            const el = {
                tagName: tag,
                innerHTML: '',
                className: '',
                id: '',
                style: {},
                classList: {
                    add: c => classes.add(c),
                    remove: c => classes.delete(c),
                    contains: c => classes.has(c)
                }
            };
            createdElements.push(el);
            return el;
        },
        body: { appendChild: el => {} }
    };
    T.showUnpostedPopup([
        { id: 't-fail', title: 'Launch teaser', status: 'failed', platforms: ['x'], error: 'locator timeout', scheduled_at: '2026-10-13T09:00:00+05:30' },
        { id: 't-overdue', title: 'Daily tip', status: 'approved', platforms: ['instagram'], scheduled_at: '2026-10-13T08:00:00+05:30' }
    ]);
    const popup = createdElements.find(e => e.id === 'ntUnpostedPopup');
    assert.ok(popup, 'popup modal element was created');
    assert.ok(popup.innerHTML.includes('Unposted / Overdue Tasks'));
    assert.ok(popup.innerHTML.includes('Launch teaser'));
    assert.ok(popup.innerHTML.includes('locator timeout'));
    assert.ok(popup.innerHTML.includes('Post Now / Kickstart'));
    assert.ok(popup.innerHTML.includes('Reschedule'));
    assert.ok(popup.innerHTML.includes('Dismiss'));
    delete globalThis.document;
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
