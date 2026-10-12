/**
 * Front-end unit tests (no browser needed).
 *
 *   node tests/test_ui.mjs
 *
 * Covers the pure logic that the UI depends on: the markdown renderer, the
 * provider registry (no silent provider substitution) and the brand guardrails.
 */

import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const uiDir = path.join(here, '..', 'ui');

/* ── minimal browser globals ─────────────────────────────────────────────── */
const store = new Map();
globalThis.window = globalThis;
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
};
globalThis.performance = globalThis.performance ?? { now: () => Date.now() };
globalThis.location = { origin: 'null', pathname: '/index.html', search: '' };
globalThis.sessionStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

await import(path.join(uiDir, 'ai-client.js'));

const { renderMarkdown, NexusAI, NexusProviders, __nexusInternals } = globalThis;
const { applyGuardrails, tokenBudgetFor } = __nexusInternals;

let passed = 0;
let failed = 0;
async function test(name, fn) {
    try {
        await fn();
        passed++;
        console.log(`  ✔ ${name}`);
    } catch (err) {
        failed++;
        console.error(`  ✘ ${name}\n      ${err.message}`);
    }
}

/* ══════════════════════════════════════════════════════════════════════════
   MARKDOWN RENDERER
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nMarkdown renderer');

await test('escapes HTML so model output cannot inject markup', () => {
    const html = renderMarkdown('<script>alert(1)</script>');
    assert.ok(!html.includes('<script>'), html);
    assert.ok(html.includes('&lt;script&gt;'), html);
});

await test('renders a multi-line list as ONE list (previously one <ul> per item)', () => {
    const html = renderMarkdown('- one\n- two\n- three');
    assert.equal((html.match(/<ul>/g) || []).length, 1, html);
    assert.equal((html.match(/<li>/g) || []).length, 3, html);
});

await test('keeps ordered lists ordered and preserves the start number', () => {
    const html = renderMarkdown('3. three\n4. four');
    assert.ok(html.includes('<ol start="3">'), html);
    assert.equal((html.match(/<ol/g) || []).length, 1, html);
});

await test('separates a list from the following paragraph', () => {
    const html = renderMarkdown('- item\n\nplain paragraph');
    assert.ok(html.includes('</ul>'), html);
    assert.ok(html.includes('<p>plain paragraph</p>'), html);
});

await test('bold and italic do not swallow list bullets', () => {
    const html = renderMarkdown('* bullet with **bold** text');
    assert.ok(html.includes('<li>'), html);
    assert.ok(html.includes('<strong>bold</strong>'), html);
    assert.ok(!html.includes('<em> bullet'), html);
});

await test('headings render at the right level', () => {
    const html = renderMarkdown('# One\n## Two\n### Three');
    assert.ok(html.includes('<h1>One</h1>'), html);
    assert.ok(html.includes('<h2>Two</h2>'), html);
    assert.ok(html.includes('<h3>Three</h3>'), html);
});

await test('fenced code keeps its content verbatim and is not parsed as markup', () => {
    const html = renderMarkdown('```\n# not a heading\n- not a list\n```');
    assert.ok(html.includes('<pre><code>'), html);
    assert.ok(html.includes('# not a heading'), html);
    assert.ok(!html.includes('<h1>'), html);
    assert.ok(!html.includes('<li>'), html);
});

await test('blockquotes and horizontal rules render', () => {
    assert.ok(renderMarkdown('> quoted').includes('<blockquote>quoted</blockquote>'));
    assert.ok(renderMarkdown('---').includes('<hr'));
});

await test('only http(s) links become anchors', () => {
    const safe = renderMarkdown('[ok](https://example.com)');
    assert.ok(safe.includes('href="https://example.com"'), safe);
    const unsafe = renderMarkdown('[bad](javascript:alert(1))');
    assert.ok(!unsafe.includes('href="javascript'), unsafe);
});

await test('empty input is safe', () => {
    assert.equal(renderMarkdown(''), '');
    assert.equal(renderMarkdown(null), '');
    assert.equal(renderMarkdown(undefined), '');
});

/* ══════════════════════════════════════════════════════════════════════════
   PROVIDER REGISTRY — the credential-leak regression
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nProvider registry');

await test('every provider the Settings dropdown offers is registered', () => {
    for (const id of ['gemini', 'groq', 'claude', 'deepseek', 'qwen', 'openai', 'openrouter', 'xai']) {
        assert.ok(NexusProviders[id], `${id} missing from the registry`);
    }
});

await test('unknown provider resolves to null instead of silently falling back', () => {
    // The old getCloudEndpoint() defaulted every unrecognised provider to the
    // Gemini URL — so an Anthropic or xAI key was POSTed to Google.
    assert.equal(NexusAI.getProvider('definitely-not-a-provider'), null);
});

await test('each cloud provider points at its own host', () => {
    const expected = {
        gemini: 'generativelanguage.googleapis.com',
        groq: 'api.groq.com',
        deepseek: 'api.deepseek.com',
        openai: 'api.openai.com',
        xai: 'api.x.ai',
        openrouter: 'openrouter.ai',
        anthropic: 'api.anthropic.com',
    };
    for (const [id, host] of Object.entries(expected)) {
        const cfg = NexusAI.getProvider(id);
        assert.ok(cfg, `${id} not resolvable`);
        assert.ok(cfg.baseUrl.includes(host), `${id} → ${cfg.baseUrl}`);
    }
});

await test('the legacy "claude" alias resolves to Anthropic', () => {
    const viaAlias = NexusAI.getProvider('claude');
    assert.equal(viaAlias.id, 'anthropic');
    assert.equal(viaAlias.dialect, 'anthropic');
});

await test('Anthropic is flagged as a native dialect, everyone else is OpenAI-compatible', () => {
    assert.equal(NexusAI.getProvider('anthropic').dialect, 'anthropic');
    assert.equal(NexusAI.getProvider('openai').dialect, undefined);
});

await test('provider keys are read from the shared credential store', () => {
    store.set('nexus_credentials', JSON.stringify({ 'prov-xai-key': 'xai-secret' }));
    assert.equal(NexusAI.getProvider('xai').apiKey, 'xai-secret');
    store.delete('nexus_credentials');
});

await test('the engine chain is ordered and de-duplicated', () => {
    NexusAI.saveConfig({ provider: 'cloud', cloudProvider: 'xai' });
    const chain = NexusAI.resolveChain();
    assert.equal(chain[0].id, 'xai', 'primary provider must come first');
    const ids = chain.map((c) => c.id);
    assert.equal(new Set(ids).size, ids.length, `duplicates in chain: ${ids}`);
    assert.ok(ids.length > 1, 'a cloud chain must have fail-over targets');
});

await test('local engine chains the configured local server first', () => {
    NexusAI.saveConfig({ provider: 'local', localType: 'ollama', localUrl: 'http://localhost:11434/v1' });
    const chain = NexusAI.resolveChain();
    assert.equal(chain[0].id, 'ollama');
    assert.equal(chain[0].baseUrl, 'http://localhost:11434/v1');
});

await test('antigravity/builtin make no network calls', () => {
    NexusAI.saveConfig({ provider: 'antigravity' });
    assert.deepEqual(NexusAI.resolveChain(), []);
    NexusAI.saveConfig({ provider: 'builtin' });
    assert.deepEqual(NexusAI.resolveChain(), []);
});

/* ══════════════════════════════════════════════════════════════════════════
   BRAND GUARDRAILS
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nBrand guardrails');

await test('banned terms are replaced and reported', () => {
    const { text, warnings } = applyGuardrails('This is a cheap hack that guarantees results.',
        { bannedWords: ['cheap', 'guarantees results'], maxHashtags: 30, allowEmoji: true });
    assert.ok(!/cheap/i.test(text), text);
    assert.ok(!/guarantees results/i.test(text), text);
    assert.ok(warnings.length >= 1, JSON.stringify(warnings));
});

await test('hashtags are capped when the policy is exceeded', () => {
    const md = 'Post body\n\n#a #b #c #d #e #f';
    const { text, warnings } = applyGuardrails(md, { maxHashtags: 3, allowEmoji: true });
    assert.equal((text.match(/#\w+/g) || []).length, 3, text);
    assert.ok(warnings.some((w) => w.includes('hashtags')), JSON.stringify(warnings));
});

await test('hashtags are untouched when within policy', () => {
    const { text, warnings } = applyGuardrails('#a #b', { maxHashtags: 5, allowEmoji: true });
    assert.equal(text, '#a #b');
    assert.deepEqual(warnings, []);
});

await test('emoji are stripped only when the brand forbids them', () => {
    const withEmoji = applyGuardrails('Launch day 🚀🎉', { allowEmoji: false, maxHashtags: 30 });
    assert.ok(!/[\u{1F300}-\u{1FAFF}]/u.test(withEmoji.text), withEmoji.text);
    const allowed = applyGuardrails('Launch day 🚀', { allowEmoji: true, maxHashtags: 30 });
    assert.ok(allowed.text.includes('🚀'));
});

await test('a required disclaimer is appended exactly once', () => {
    const policy = { requiredDisclaimer: 'Not financial advice.', maxHashtags: 30, allowEmoji: true };
    const first = applyGuardrails('Body', policy);
    assert.ok(first.text.endsWith('Not financial advice.'));
    const second = applyGuardrails(first.text, policy);
    assert.equal((second.text.match(/Not financial advice\./g) || []).length, 1);
});

await test('guardrails are a no-op when disabled or absent', () => {
    assert.equal(applyGuardrails('anything 🚀 #a #b', null).text, 'anything 🚀 #a #b');
    assert.deepEqual(applyGuardrails('text', null).warnings, []);
});

/* ══════════════════════════════════════════════════════════════════════════
   CREDENTIAL STORE
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nCredential store');

await test('switching provider does not delete another provider\'s key', () => {
    // Regression: saveConfig() used to mirror `cloudApiKey` into the credential
    // store, and the provider-switch handler passes an empty key — so merely
    // changing the dropdown wiped the key already saved for that provider.
    store.delete('nexus_credentials');
    NexusAI.saveConfig({ cloudProvider: 'gemini', cloudApiKey: 'gemini-secret' });
    NexusAI.saveProviderKey('gemini', 'gemini-secret');
    NexusAI.saveProviderKey('xai', 'xai-secret');

    // What the change handler does now:
    NexusAI.saveConfig({ cloudProvider: 'xai' });
    NexusAI.saveConfig({ cloudProvider: 'gemini' });

    assert.equal(NexusAI.getProvider('gemini').apiKey, 'gemini-secret');
    assert.equal(NexusAI.getProvider('xai').apiKey, 'xai-secret', 'xai key was destroyed');
});

await test('an empty key is a no-op, null clears', () => {
    store.delete('nexus_credentials');
    NexusAI.saveProviderKey('groq', 'groq-secret');
    NexusAI.saveProviderKey('groq', '');            // empty form field
    assert.equal(NexusAI.getProvider('groq').apiKey, 'groq-secret', 'empty string deleted the key');
    NexusAI.saveProviderKey('groq', null);          // explicit clear
    assert.equal(NexusAI.getProvider('groq').apiKey, '');
});

await test('the legacy claude alias writes to the anthropic slot', () => {
    store.delete('nexus_credentials');
    NexusAI.saveProviderKey('claude', 'claude-secret');
    assert.equal(NexusAI.getProvider('anthropic').apiKey, 'claude-secret');
    assert.equal(NexusAI.getProvider('claude').apiKey, 'claude-secret');
});

await test('a provider never sees another provider\'s legacy key', () => {
    store.delete('nexus_credentials');
    NexusAI.saveConfig({ cloudProvider: 'gemini', cloudApiKey: 'only-for-gemini' });
    assert.equal(NexusAI.getProvider('gemini').apiKey, 'only-for-gemini');
    assert.equal(NexusAI.getProvider('xai').apiKey, '', 'xai inherited the gemini key');
    assert.equal(NexusAI.getProvider('openai').apiKey, '');
});

await test('saveConfig does not touch credentials at all', () => {
    store.delete('nexus_credentials');
    NexusAI.saveProviderKey('openai', 'keep-me');
    NexusAI.saveConfig({ cloudProvider: 'openai', cloudApiKey: '' });
    NexusAI.saveConfig({ cloudApiKey: '' });
    assert.equal(NexusAI.getProvider('openai').apiKey, 'keep-me');
});

/* ══════════════════════════════════════════════════════════════════════════
   CONCURRENT RUNS
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nConcurrent runs');

await test('a completed run leaves nothing in the active set', async () => {
    NexusAI.saveConfig({ provider: 'builtin' });
    await NexusAI.generate({ instruction: 'x', platforms: ['instagram'] });
    assert.equal(NexusAI.activeRuns, 0);
});

await test('cancel with nothing running is a no-op, not a crash', () => {
    assert.equal(NexusAI.abort(), 0);
});

await test('abort() cancels exactly the runs that are in flight', () => {
    // Two overlapping runs (e.g. a hub tool plus the main form) each carry their
    // own controller, so one cancel reaches both and neither clobbers the other.
    const a = { abort: new AbortController(), hook: null };
    const b = { abort: new AbortController(), hook: null };
    NexusAI._runs.add(a);
    NexusAI._runs.add(b);
    try {
        assert.equal(NexusAI.activeRuns, 2);
        assert.equal(NexusAI.abort(), 2);
        assert.ok(a.abort.signal.aborted && b.abort.signal.aborted);
    } finally {
        NexusAI._runs.clear();
    }
});

await test('a run keeps its own log sink', () => {
    const seen = [];
    const run = { abort: new AbortController(), hook: (lvl, src, msg) => seen.push(msg) };
    NexusAI._log('info', 'test.source', 'hello from run A', undefined, run.hook);
    assert.deepEqual(seen, ['hello from run A']);
    assert.equal(seen.length, 1, 'the shared fallback hook must not also receive it');
});

/* ══════════════════════════════════════════════════════════════════════════
   TOKEN BUDGETS
   ══════════════════════════════════════════════════════════════════════════ */
console.log('\nToken budgets');

await test('long-form platforms get a larger budget than the base', () => {
    assert.ok(tokenBudgetFor('blog', 1400) > 1400);
    assert.ok(tokenBudgetFor('youtube', 1400) > 1400);
});

await test('short-form platforms keep the base budget', () => {
    assert.equal(tokenBudgetFor('instagram', 1400), 1400);
    assert.equal(tokenBudgetFor('twitter', 1400), 1400);
});

/* ══════════════════════════════════════════════════════════════════════════ */
console.log(`\n${passed} passed, ${failed} failed\n`);
process.exit(failed ? 1 : 0);
