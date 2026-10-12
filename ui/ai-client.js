/**
 * NexusAI — Unified Multi-Harness AI Engine  (v3)
 * =============================================================================
 * SINGLE SOURCE OF TRUTH for provider routing in the Nexus Command Centre.
 *
 * Supports
 *   • Local   : LM Studio (Hermes/Llama GGUF), Ollama          (OpenAI-compatible)
 *   • Cloud   : Google Gemini, Groq, DeepSeek, Qwen, OpenAI,
 *               xAI Grok, OpenRouter, Anthropic Claude          (native Messages API)
 *   • Agentic : Google Antigravity live brain via the relay (POST /antigravity/chat, SSE);
 *               falls back to the skill hand-off directive when the app/relay is unavailable
 *   • Offline : deterministic built-in copywriting engine (never blocks the user)
 *
 * Engineering guarantees
 *   1. NO SILENT PROVIDER SUBSTITUTION. An unknown provider id raises a typed
 *      error instead of quietly posting the request (and the user's API key) to
 *      a different vendor's endpoint.
 *   2. One credential store. Keys live in `nexus_credentials` under
 *      `prov-<id>-key`, which is the same store the Settings page writes to.
 *      (`nexus_ai_config` is still read for backwards compatibility.)
 *   3. Retries with exponential backoff + jitter, per-attempt timeout, and
 *      abort support. 4xx (except 408/409/429) is treated as fatal.
 *   4. Automatic fail-over down a provider chain, with every attempt recorded
 *      through the `onLog` hook so the Agent Logs page shows the real story.
 *   5. Bounded-concurrency parallel generation with per-platform error
 *      isolation — one failing platform never sinks the batch.
 */

/* ────────────────────────────────────────────────────────────────────────────
   PROVIDER REGISTRY
   ──────────────────────────────────────────────────────────────────────────── */
const NEXUS_PROVIDERS = {
    /* ── Cloud ─────────────────────────────────────────────────────────────── */
    gemini: {
        label: 'Google Gemini', kind: 'cloud', needsKey: true,
        baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
        model: 'gemini-2.5-flash',
        keysUrl: 'https://aistudio.google.com/app/apikey',
    },
    groq: {
        label: 'Groq', kind: 'cloud', needsKey: true,
        baseUrl: 'https://api.groq.com/openai/v1',
        model: 'llama-3.3-70b-versatile',
        keysUrl: 'https://console.groq.com/keys',
    },
    deepseek: {
        label: 'DeepSeek', kind: 'cloud', needsKey: true,
        baseUrl: 'https://api.deepseek.com/v1',
        model: 'deepseek-chat',
        keysUrl: 'https://platform.deepseek.com/api_keys',
    },
    qwen: {
        label: 'Qwen (DashScope)', kind: 'cloud', needsKey: true,
        baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        model: 'qwen-plus',
        keysUrl: 'https://bailian.console.aliyun.com/',
    },
    openai: {
        label: 'OpenAI', kind: 'cloud', needsKey: true,
        baseUrl: 'https://api.openai.com/v1',
        model: 'gpt-4o-mini',
        keysUrl: 'https://platform.openai.com/api-keys',
    },
    xai: {
        label: 'xAI Grok', kind: 'cloud', needsKey: true,
        baseUrl: 'https://api.x.ai/v1',
        model: 'grok-4',
        keysUrl: 'https://console.x.ai/',
    },
    openrouter: {
        label: 'OpenRouter', kind: 'cloud', needsKey: true,
        baseUrl: 'https://openrouter.ai/api/v1',
        model: 'nousresearch/hermes-4-405b',
        keysUrl: 'https://openrouter.ai/keys',
        // OpenRouter asks for attribution headers; they also raise rate limits.
        extraHeaders: () => ({
            'HTTP-Referer': location.origin && location.origin !== 'null' ? location.origin : 'https://localhost',
            'X-Title': 'Nexus Marketing Command Center',
        }),
    },
    anthropic: {
        label: 'Anthropic Claude', kind: 'cloud', needsKey: true, dialect: 'anthropic',
        baseUrl: 'https://api.anthropic.com/v1',
        model: 'claude-sonnet-4-20250514',
        keysUrl: 'https://console.anthropic.com/settings/keys',
    },

    /* ── External agent brain ─────────────────────────────────────────────── */
    // Not an HTTP model: requests become job files via the relay (/brain/job)
    // and the Sho agent answers them. See agent/brain_bridge.py.
    sho: {
        label: 'Sho (external agent brain)', kind: 'brain', needsKey: false,
        baseUrl: '', model: 'sho',
    },

    /* ── Local ─────────────────────────────────────────────────────────────── */
    lmstudio: {
        label: 'LM Studio (local)', kind: 'local', needsKey: false,
        baseUrl: 'http://localhost:1234/v1',
        model: 'hermes-3-llama-3.1-8b',
    },
    ollama: {
        label: 'Ollama (local)', kind: 'local', needsKey: false,
        baseUrl: 'http://localhost:11434/v1',
        model: 'hermes3:8b',
    },
};

// Legacy alias kept so old saved configs and Settings markup keep working.
NEXUS_PROVIDERS.claude = { ...NEXUS_PROVIDERS.anthropic, alias: 'anthropic' };

const CLOUD_FAILOVER_ORDER = [
    'gemini', 'groq', 'deepseek', 'openai', 'xai', 'openrouter', 'anthropic',
];

/** Errors that a retry cannot fix. */
function isFatal(err) {
    return err && err.fatal === true;
}

/** Abortable sleep so a cancelled batch stops immediately instead of hanging. */
function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
        const t = setTimeout(resolve, ms);
        if (signal) {
            signal.addEventListener('abort', () => {
                clearTimeout(t);
                reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
            }, { once: true });
        }
    });
}

class AIClient {
    constructor() {
        this.storageKey = 'nexus_ai_config';
        this.credsKey = 'nexus_credentials';
        this.lastRun = null;          // metadata for the most recent generate()
        // Concurrent generations are supported (e.g. a hub tool while the main
        // form is running), so cancellation and logging are tracked per run
        // rather than in one shared slot that a second run would clobber.
        this._runs = new Set();
        this._logHook = null;         // fallback hook for calls outside a run
        this.brandKey = 'nexus_active_brand_profile';
        this.brandProfile = null;     // active Brand Profile (Brand Setup page)
        try { this.brandProfile = JSON.parse(localStorage.getItem(this.brandKey) || 'null'); } catch { this.brandProfile = null; }
        this.loadConfig();
    }

    /** Make every generation and guardrail pass use this brand. null clears it. */
    setBrandProfile(profile) {
        this.brandProfile = profile && typeof profile === 'object' ? profile : null;
        try {
            if (this.brandProfile) localStorage.setItem(this.brandKey, JSON.stringify(this.brandProfile));
            else localStorage.removeItem(this.brandKey);
        } catch { /* storage full / private mode: keep in memory */ }
    }

    /* ── Sho external brain ────────────────────────────────────────────────── */

    _relay() { return (typeof window !== 'undefined' && window.nexusRelayCall) || null; }

    /** Create a brain job and poll until Sho answers, the timeout passes or the run is cancelled. */
    async askSho(kind, task, { signal = null, onProgress = null, sleep = (ms) => new Promise(r => setTimeout(r, ms)),
                               now = () => Date.now() } = {}) {
        const relay = this._relay();
        if (!relay) throw new Error('the Sho provider needs the local relay');
        const created = await relay('/brain/job', { body: { kind, task, brand_profile: this.brandProfile || undefined }, ms: 15000 });
        const id = created.job.id;
        const deadline = now() + Number(this.config.brainTimeoutMs || 600000);
        for (;;) {
            if (signal?.aborted) throw new Error('cancelled');
            const { job } = await relay(`/brain/jobs/${encodeURIComponent(id)}`, { method: 'GET', ms: 10000 });
            if (job.status !== 'pending') return shoResultText(job, task.platform);
            if (now() >= deadline) throw new Error(`timed out waiting for job ${id}`);
            if (onProgress) onProgress(`Waiting for Sho (job ${id})…`);
            await sleep(Number(this.config.brainPollMs || 3000));
        }
    }

    /* ── Google Antigravity live brain ─────────────────────────────────────── */

    _relayStream() { return (typeof window !== 'undefined' && window.nexusRelayStream) || null; }

    antigravitySessionId(brand) {
        return `nexus-${slug(brand || 'brand') || 'brand'}`.slice(0, 120);
    }

    /** Stream one prompt through the Antigravity bridge; resolves with the full prose. */
    async askAntigravity(message, { sessionId, signal = null, onProgress = null, onText = null } = {}) {
        const stream = this._relayStream();
        if (!stream) throw new Error('the Antigravity brain needs the local relay');
        const parts = [];
        let failure = null;
        await stream('/antigravity/chat', {
            body: { message, session_id: sessionId, model: this.config.antigravityModel || undefined },
            signal,
            onEvent: (ev, data) => {
                if (ev === 'meta' && onProgress) onProgress(`Antigravity ${data.new ? 'started' : 'continued'} conversation ${String(data.conversation_id).slice(0, 8)}…`);
                else if (ev === 'text') { parts.push(data.text); if (onText) onText(data.text); }
                else if (ev === 'error') failure = data.error || 'Antigravity error';
            },
        });
        if (failure && !parts.length) throw new Error(failure);
        return parts.join('\n\n');
    }

    /* ── configuration ─────────────────────────────────────────────────────── */

    loadConfig() {
        let stored = {};
        try { stored = JSON.parse(localStorage.getItem(this.storageKey) || '{}'); } catch { stored = {}; }
        this.config = {
            provider: 'antigravity',          // antigravity | cloud | local | builtin | sho
            brainTimeoutMs: 600000,           // how long to wait for Sho to answer a job
            brainPollMs: 3000,
            antigravityModel: '',             // '' (app default) | flash_lite | flash | pro
            localType: 'lmstudio',
            localUrl: NEXUS_PROVIDERS.lmstudio.baseUrl,
            localModel: NEXUS_PROVIDERS.lmstudio.model,
            cloudProvider: 'gemini',
            cloudApiKey: '',
            cloudModel: '',
            temperature: 0.7,
            maxTokens: 1400,
            retries: 2,
            timeoutMs: 60000,
            concurrency: 3,
            guardrails: {
                enabled: true,
                bannedWords: [],
                requiredDisclaimer: '',
                maxHashtags: 30,
                allowEmoji: true,
            },
            ...stored,
        };
        // Never let a corrupt value wedge the engine.
        this.config.temperature = clamp(Number(this.config.temperature), 0, 2, 0.7);
        this.config.maxTokens = clamp(Number(this.config.maxTokens), 128, 8000, 1400);
        this.config.retries = clamp(Number(this.config.retries), 0, 5, 2);
        this.config.timeoutMs = clamp(Number(this.config.timeoutMs), 5000, 300000, 60000);
        this.config.concurrency = clamp(Number(this.config.concurrency), 1, 6, 3);
    }

    /**
     * Persist engine settings.
     *
     * NOTE: this deliberately does NOT write to the credential store. An earlier
     * version mirrored `cloudApiKey` here, and because an empty value means
     * "delete" in `saveProviderKey`, merely changing the provider dropdown
     * (which passes an empty key) wiped the key already saved for that provider.
     * Credentials are written only by `saveProviderKey`/`saveProviderCredentials`,
     * which are explicit about intent.
     */
    saveConfig(updates = {}) {
        this.config = { ...this.config, ...updates };
        try {
            localStorage.setItem(this.storageKey, JSON.stringify(this.config));
        } catch (e) {
            console.warn('NexusAI: could not persist config', e);
        }
        return this.config;
    }

    /**
     * Store (or clear) one provider's key.
     * Pass `key = null` to clear it; an empty string is treated as "no change"
     * so an empty form field can never silently delete a stored credential.
     */
    saveProviderKey(id, key) {
        if (key === '' || key === undefined) return this.readCreds();
        try {
            const creds = this.readCreds();
            const k = `prov-${this.normalizeId(id)}-key`;
            if (key === null) delete creds[k]; else creds[k] = key;
            localStorage.setItem(this.credsKey, JSON.stringify(creds));
            return creds;
        } catch (e) {
            console.warn('NexusAI: could not persist provider key', e);
            return {};
        }
    }

    readCreds() {
        try { return JSON.parse(localStorage.getItem(this.credsKey) || '{}'); } catch { return {}; }
    }

    /** Resolve the deprecated `claude` alias to `anthropic`. */
    normalizeId(id) {
        if (!id) return 'gemini';
        return id === 'claude' ? 'anthropic' : id;
    }

    /**
     * Fully-resolved provider config: registry defaults ← saved overrides.
     * Credentials resolve from the shared store first, then the legacy config.
     */
    getProvider(id) {
        const key = this.normalizeId(id);
        const base = NEXUS_PROVIDERS[key];
        if (!base) return null;

        const creds = this.readCreds();
        const isCloud = base.kind === 'cloud';
        const legacyKey = isCloud && this.normalizeId(this.config.cloudProvider) === key
            ? this.config.cloudApiKey
            : '';

        return {
            id: key,
            ...base,
            baseUrl: (creds[`prov-${key}-url`] || base.baseUrl || '').replace(/\/+$/, ''),
            apiKey: creds[`prov-${key}-key`] || legacyKey || '',
            model: creds[`prov-${key}-model`] || base.model || 'local-model',
        };
    }

    /** Provider ids known to the engine (used by the Settings page). */
    listProviders(kind = null) {
        return Object.entries(NEXUS_PROVIDERS)
            .filter(([id, p]) => !p.alias && (!kind || p.kind === kind))
            .map(([id, p]) => ({ id, ...p }));
    }

    /* ── provider chain resolution ─────────────────────────────────────────── */

    /**
     * Build the ordered list of providers to try for the current engine.
     * Fixes the previous behaviour where "cloud" silently hard-coded a
     * two-provider chain and ignored the user's own selection order.
     */
    resolveChain() {
        const engine = this.config.provider;

        if (engine === 'local') {
            const primaryId = this.config.localType === 'ollama' ? 'ollama' : 'lmstudio';
            const primary = this.getProvider(primaryId);
            // Honour an explicitly configured URL (Settings → Local Models).
            if (this.config.localUrl) primary.baseUrl = this.config.localUrl.replace(/\/+$/, '');
            if (this.config.localModel) primary.model = this.config.localModel;
            const backup = this.getProvider(primaryId === 'ollama' ? 'lmstudio' : 'ollama');
            return [primary, backup];
        }

        if (engine === 'cloud') {
            const primaryId = this.normalizeId(this.config.cloudProvider || 'gemini');
            const rest = CLOUD_FAILOVER_ORDER.filter(id => id !== primaryId);
            return [primaryId, ...rest]
                .map(id => this.getProvider(id))
                .filter(Boolean);
        }

        return []; // antigravity → prompt hand-off, builtin → templates
    }

    /* ── transport ─────────────────────────────────────────────────────────── */

    async _fetchWithTimeout(url, options, timeoutMs, signal) {
        const ctrl = new AbortController();
        const onAbort = () => ctrl.abort();
        if (signal) signal.addEventListener('abort', onAbort, { once: true });
        const timer = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
            return await fetch(url, { ...options, signal: ctrl.signal });
        } finally {
            clearTimeout(timer);
            if (signal) signal.removeEventListener('abort', onAbort);
        }
    }

    /** One attempt against one OpenAI-compatible chat-completions endpoint. */
    async _callOpenAICompatible(cfg, { systemPrompt, userPrompt, maxTokens, signal }) {
        const url = /\/chat\/completions$/.test(cfg.baseUrl)
            ? cfg.baseUrl
            : `${cfg.baseUrl}/chat/completions`;

        const headers = { 'Content-Type': 'application/json' };
        if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;
        if (cfg.extraHeaders) Object.assign(headers, cfg.extraHeaders());

        const res = await this._fetchWithTimeout(url, {
            method: 'POST',
            headers,
            body: JSON.stringify({
                model: cfg.model,
                messages: [
                    { role: 'system', content: systemPrompt },
                    { role: 'user', content: userPrompt },
                ],
                temperature: this.config.temperature,
                max_tokens: maxTokens,
                stream: false,
            }),
        }, this.config.timeoutMs, signal);

        if (!res.ok) {
            const body = await res.text().catch(() => '');
            throw httpError(res.status, body, cfg);
        }

        let data;
        try { data = await res.json(); }
        catch { throw new Error('Provider returned a non-JSON body'); }

        const choice = data?.choices?.[0];
        const text = choice?.message?.content ?? choice?.text;
        if (!text) {
            const reason = choice?.finish_reason === 'content_filter'
                ? 'blocked by the provider content filter'
                : 'empty completion';
            throw new Error(`${cfg.label}: ${reason}`);
        }
        return { text: String(text).trim(), usage: data.usage || null, raw: data };
    }

    /** One attempt against the native Anthropic Messages API. */
    async _callAnthropic(cfg, { systemPrompt, userPrompt, maxTokens, signal }) {
        const res = await this._fetchWithTimeout(`${cfg.baseUrl}/messages`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'x-api-key': cfg.apiKey,
                'anthropic-version': '2023-06-01',
                // Required for direct browser → Anthropic calls (CORS preflight).
                'anthropic-dangerous-direct-browser-access': 'true',
            },
            body: JSON.stringify({
                model: cfg.model,
                max_tokens: maxTokens,
                temperature: Math.min(this.config.temperature, 1),
                system: systemPrompt,
                messages: [{ role: 'user', content: userPrompt }],
            }),
        }, this.config.timeoutMs, signal);

        if (!res.ok) {
            const body = await res.text().catch(() => '');
            throw httpError(res.status, body, cfg);
        }
        const data = await res.json();
        const text = (data?.content || []).filter(b => b.type === 'text').map(b => b.text).join('\n');
        if (!text) throw new Error('Anthropic returned no text content');
        return { text: text.trim(), usage: data.usage || null, raw: data };
    }

    /**
     * Call a provider with retries, exponential backoff and jitter.
     * 4xx responses (except 408/409/429) are fatal and not retried.
     */
    async callWithRetry(cfg, payload) {
        const { hook = null } = payload;
        const attemptCount = this.config.retries + 1;
        let lastErr;
        for (let attempt = 1; attempt <= attemptCount; attempt++) {
            const started = performance.now();
            try {
                const out = cfg.dialect === 'anthropic'
                    ? await this._callAnthropic(cfg, payload)
                    : await this._callOpenAICompatible(cfg, payload);
                out.latencyMs = Math.round(performance.now() - started);
                out.attempt = attempt;
                return out;
            } catch (e) {
                lastErr = e;
                const aborted = e.name === 'AbortError';
                this._log('warn', `provider:${cfg.id}`,
                    `Attempt ${attempt}/${attemptCount} via ${cfg.label} failed: ${aborted ? 'timeout' : e.message}`,
                    undefined, hook);
                if (aborted && payload.signal?.aborted) throw e;   // user cancelled
                if (isFatal(e) || attempt === attemptCount) break;
                const backoff = Math.min(500 * 2 ** (attempt - 1), 8000);
                await sleep(backoff + Math.random() * 250, payload.signal);
            }
        }
        throw lastErr;
    }

    /* ── public API: connection test ───────────────────────────────────────── */

    /**
     * Probe a provider. Cloud providers with native endpoints are probed with a
     * real (tiny) request, because a `/models` GET is not part of the Anthropic
     * API and used to report a false failure.
     */
    async testConnection(overrideConfig = null) {
        const engine = overrideConfig?.provider || this.config.provider;

        if (engine === 'antigravity') {
            const relay = this._relay();
            if (!relay) return { ok: true, provider: 'antigravity', live: false,
                                 message: 'Relay not loaded — Antigravity falls back to the skill hand-off directive.' };
            try {
                const st = await relay('/antigravity/status', { method: 'GET', ms: 8000 });
                return st.available
                    ? { ok: true, provider: 'antigravity', live: true, message: 'Google Antigravity is running — live agent brain ready.' }
                    : { ok: false, provider: 'antigravity', live: false, message: `${st.reason} (falls back to the skill hand-off directive)` };
            } catch (e) {
                return { ok: false, provider: 'antigravity', live: false, message: `Relay /antigravity unreachable: ${e.message}` };
            }
        }
        if (engine === 'builtin') {
            return { ok: true, provider: 'builtin', message: 'Built-in copywriting engine active (no API calls).' };
        }
        if (engine === 'sho') {
            const relay = this._relay();
            if (!relay) return { ok: false, provider: 'sho', message: 'Sho needs the local relay (not loaded).' };
            try {
                const res = await relay('/brain/jobs?status=pending', { method: 'GET', ms: 5000 });
                return { ok: true, provider: 'sho',
                         message: `Sho bridge reachable — ${res.jobs.length} job(s) waiting for the agent.` };
            } catch (e) {
                return { ok: false, provider: 'sho', message: `Relay /brain unreachable: ${e.message}` };
            }
        }

        if (engine === 'local') {
            const url = (overrideConfig?.localUrl || this.config.localUrl || NEXUS_PROVIDERS.lmstudio.baseUrl)
                .replace(/\/+$/, '');
            try {
                const res = await this._fetchWithTimeout(`${url}/models`, { method: 'GET' }, 4000);
                if (!res.ok) {
                    return { ok: false, provider: 'local', message: `Local server answered HTTP ${res.status} — is it the OpenAI-compatible endpoint?` };
                }
                const data = await res.json().catch(() => ({}));
                const models = data?.data || data?.models || [];
                const names = models.map(m => m.id || m.name).filter(Boolean);
                const count = names.length || 1;
                const hint = names.length
                    ? ` Loaded: ${names.slice(0, 4).join(', ')}${names.length > 4 ? '…' : ''}`
                    : '';
                return {
                    ok: true, provider: overrideConfig?.localType || this.config.localType,
                    message: `Connected to ${url} — ${count} model(s) available.${hint}`,
                };
            } catch (e) {
                return {
                    ok: false, provider: 'local',
                    message: `Cannot reach ${url}. Start LM Studio's Local Server (port 1234) or Ollama (port 11434). [${e.message}]`,
                };
            }
        }

        // Cloud
        const id = this.normalizeId(overrideConfig?.cloudProvider || this.config.cloudProvider);
        const cfg = this.getProvider(id);
        if (!cfg) {
            return { ok: false, provider: id, message: `Unknown provider "${id}".` };
        }
        if (overrideConfig?.cloudApiKey) cfg.apiKey = overrideConfig.cloudApiKey;
        if (overrideConfig?.cloudModel) cfg.model = overrideConfig.cloudModel;

        if (!cfg.apiKey) {
            return { ok: false, provider: id, message: `${cfg.label} needs an API key. Add one in Settings (stored in this browser only).` };
        }

        try {
            if (cfg.dialect === 'anthropic') {
                await this._callAnthropic(cfg, {
                    systemPrompt: 'Reply with the single word: pong',
                    userPrompt: 'ping',
                    maxTokens: 8,
                    signal: null,
                });
            } else {
                // Cheap authenticated GET works for OpenAI, Groq, xAI, OpenRouter,
                // DeepSeek; Gemini/Qwen expose /models through the same compat path.
                const res = await this._fetchWithTimeout(`${cfg.baseUrl}/models`, {
                    method: 'GET',
                    headers: { Authorization: `Bearer ${cfg.apiKey}` },
                }, 10000);
                if (!res.ok) {
                    const body = await res.text().catch(() => '');
                    return { ok: false, provider: id, message: `${cfg.label} rejected the key (HTTP ${res.status}). ${shorten(body, 140)}` };
                }
            }
            return { ok: true, provider: id, message: `${cfg.label} authenticated successfully.` };
        } catch (e) {
            return {
                ok: false, provider: id,
                message: e.name === 'AbortError'
                    ? `${cfg.label} did not respond in time.`
                    : `${cfg.label}: ${e.message}`,
            };
        }
    }

    /* ── public API: prompts ───────────────────────────────────────────────── */

    buildSystemPrompt({ tone = 'professional', brand = 'My Brand', platform = '', guardrails = null } = {}) {
        const g = guardrails || this.config.guardrails || {};
        const rules = [];

        if (g.bannedWords?.length) {
            rules.push(`NEVER use these words or phrases: ${g.bannedWords.join(', ')}.`);
        }
        if (g.requiredDisclaimer) {
            rules.push(`Always end with this exact disclaimer on its own line: "${g.requiredDisclaimer}"`);
        }
        if (g.allowEmoji === false) {
            rules.push('Do not use emoji anywhere in the output.');
        }
        if (Number.isFinite(g.maxHashtags) && g.maxHashtags >= 0) {
            rules.push(`Never exceed ${g.maxHashtags} hashtags in total.`);
        }

        return `You are a world-class Senior Digital Marketing Director and Social Media Strategist with 10+ years of high-growth experience.
Brand: "${brand}"
Default tone of voice: ${tone}${platform ? `\nCurrent platform: ${platform}` : ''}

PLATFORM PLAYBOOK (apply the relevant section exactly):
• Instagram — line 1 is a thumb-stopping hook (<= 125 chars). 3-5 short value lines. One clear engagement CTA. 5-8 tiered hashtags. Explicit visual direction (format, composition, overlay text, mood).
• Facebook — conversational, community-first storytelling. One open-ended discussion question. No corporate jargon.
• YouTube — two CTR-optimised title variants under 60 characters, keyword-rich description with a timestamp chapter outline, 12-15 tags, thumbnail brief (<= 6 words of overlay).
• LinkedIn — bold one-line hook, generous whitespace, 3-5 business insights as bullets, no external links in the post body (say "link in first comment").
• X / Twitter — a numbered thread: hook tweet, 3-5 value tweets, closing CTA tweet. Each tweet under 280 characters.
• TikTok / Reels — second-by-second script beats with on-screen text, one trending-audio recommendation, 3-5 hashtags only.
• Blog / SEO — focus keyword, meta title (<= 55 chars), meta description (<= 155 chars), H1, H2/H3 outline, internal-link ideas, FAQ block.
• Email — two subject-line variants, preview text, skimmable body, one primary CTA, P.S. line.

CRAFT RULES:
1. Lead with the customer's problem, not the brand's features.
2. Concrete beats clever: specific numbers, examples and outcomes.
3. One primary call-to-action per asset. Never stack three CTAs.
4. Never invent metrics, awards, testimonials, prices or statistics. If a claim needs proof, write "[INSERT PROOF]" instead.
5. Follow AIDA / PAS / Hook-Value-CTA structure where it fits naturally.
${rules.length ? '\nBRAND GUARDRAILS (hard requirements):\n' + rules.map((r, i) => `${i + 1}. ${r}`).join('\n') : ''}${this.brandProfile ? '\n\n' + brandContextBlock(this.brandProfile) + '\nStay strictly on-brand: use only the services, USPs and voice above. Never invent prices, awards or statistics.' : ''}

FORMAT: clean, copy-paste-ready GitHub-flavoured Markdown with bold section labels. No preamble, no "here is your content", no code fences around the whole answer.`;
    }

    buildUserPrompt({ instruction, platform, tone, brand, contentType, context, variant = null }) {
        const lines = [
            'CAMPAIGN BRIEF',
            `- Topic / goal: "${instruction}"`,
            `- Target platform: ${platform}`,
            `- Tone of voice: ${tone}`,
            `- Brand: ${brand}`,
            `- Asset type: ${contentType}`,
        ];
        if (context) lines.push(`- Extra context: ${context}`);
        if (variant) lines.push(`- Variant requested: ${variant}`);

        lines.push(
            '',
            `Produce ONE complete, ready-to-publish ${String(platform).toUpperCase()} package:`,
            '1. The post / caption / script itself (final copy, not an outline).',
            '2. A tiered hashtag set (mega / large / medium / niche) where the platform uses hashtags.',
            '3. Two call-to-actions: one engagement, one conversion.',
            '4. A visual direction brief: composition, text overlay, colours, mood.',
            '5. Posting-time recommendation and one A/B test idea.'
        );
        return lines.join('\n');
    }

    /* ── public API: generation ────────────────────────────────────────────── */

    /**
     * Generate content for one or more platforms.
     * @returns {Promise<Object>} `{ [platform]: markdown }` — always resolves.
     */
    async generate({
        instruction, platforms, tone = 'professional', brand = 'My Brand',
        contentType = 'daily', context = '', onProgress = null, onLog = null,
    } = {}) {
        const wanted = Array.isArray(platforms) ? platforms.filter(Boolean) : [];
        if (!wanted.length) return {};

        // One run context per invocation: its own AbortController and log sink,
        // so two overlapping generations cannot cancel or mis-attribute each
        // other (previously both shared this._abort / this._logHook).
        const run = { abort: new AbortController(), hook: onLog || null, startedAt: Date.now() };
        this._runs.add(run);
        const log = (level, source, message, detail) => this._log(level, source, message, detail, run.hook);

        const startedAt = performance.now();
        const results = {};
        const runMeta = { engine: this.config.provider, provider: null, providers: [], pieces: {}, startedAt: new Date().toISOString() };

        // Antigravity: live agent through the relay bridge; the hand-off directive is the fallback.
        if (this.config.provider === 'antigravity') {
            const guard = this.config.guardrails?.enabled ? mergeBrandGuardrails(this.config.guardrails, this.brandProfile) : null;
            const sys = this.buildSystemPrompt({ tone, brand, guardrails: guard });
            let live = !!this._relayStream();
            for (const platform of wanted) {
                if (live) {
                    if (onProgress) onProgress(`Asking Google Antigravity to write ${platform}…`);
                    try {
                        const prompt = `${sys}\n\n${this.buildUserPrompt({ instruction, platform, tone, brand, contentType, context })}\n\n` +
                            'Reply with the finished post text only. Do not run tools, edit files or publish anything.';
                        const text = await this.askAntigravity(prompt, { sessionId: this.antigravitySessionId(brand), signal: run.abort.signal, onProgress });
                        if (!text.trim()) throw new Error('empty answer');
                        results[platform] = guard ? applyGuardrails(text, guard).text : text;
                        runMeta.pieces[platform] = { provider: 'antigravity', ok: true, live: true };
                        continue;
                    } catch (e) {
                        log('warn', 'antigravity', `Antigravity live brain unavailable for ${platform}; using the hand-off directive`, e.message);
                        live = false;
                    }
                }

                if (onProgress) onProgress(`Preparing Antigravity agent directive for ${platform}…`);
                results[platform] = this.formatAntigravityDirective({ instruction, platform, tone, brand, contentType, context });
                runMeta.pieces[platform] = { provider: 'antigravity', ok: true };
            }
            return this._finishRun(results, runMeta, startedAt, run);
        }

        // Sho: one job per platform, answered by the external agent.
        if (this.config.provider === 'sho') {
            for (const platform of wanted) {
                if (onProgress) onProgress(`Waiting for Sho to write ${platform}…`);
                try {
                    const text = await this.askSho('generate', {
                        topic: instruction, platform, platforms: [platform], tone, brand, contentType, context,
                    }, { signal: run.abort.signal, onProgress });
                    const g = this.config.guardrails?.enabled ? mergeBrandGuardrails(this.config.guardrails, this.brandProfile) : null;
                    results[platform] = g ? applyGuardrails(text, g).text : text;
                    runMeta.pieces[platform] = { provider: 'sho', ok: true };
                } catch (e) {
                    log('error', 'sho', `Sho job for ${platform} failed`, e.message);
                    results[platform] = `⚠️ Sho did not answer: ${e.message}`;
                    runMeta.pieces[platform] = { provider: 'sho', ok: false, error: e.message };
                }
            }
            return this._finishRun(results, runMeta, startedAt, run);
        }

        // 'builtin' short-circuits to the deterministic engine.
        if (this.config.provider === 'builtin') {
            for (const platform of wanted) {
                if (onProgress) onProgress(`Writing ${platform} with the built-in copywriting engine…`);
                results[platform] = this.templateFallback(instruction, platform, tone, brand, contentType, context);
                runMeta.pieces[platform] = { provider: 'builtin', ok: true };
            }
            return this._finishRun(results, runMeta, startedAt, run);
        }

        const chain = this.resolveChain();
        const usable = chain.filter(c => !c.needsKey || c.apiKey);

        if (!chain.length) {
            for (const platform of wanted) {
                results[platform] = this.templateFallback(instruction, platform, tone, brand, contentType, context,
                    'no provider configured');
                runMeta.pieces[platform] = { provider: 'builtin', ok: true, degraded: true };
            }
            return this._finishRun(results, runMeta, startedAt, run);
        }
        if (!usable.length) {
            log('warn', 'agent.engine',
                `No usable provider: ${chain[0].label} has no API key. Using the built-in engine.`);
            for (const platform of wanted) {
                results[platform] = this.templateFallback(instruction, platform, tone, brand, contentType, context,
                    `${chain[0].label} API key missing`);
                runMeta.pieces[platform] = { provider: 'builtin', ok: true, degraded: true };
            }
            return this._finishRun(results, runMeta, startedAt, run);
        }

        const guardrails = this.config.guardrails?.enabled
            ? mergeBrandGuardrails(this.config.guardrails, this.brandProfile) : null;
        const systemPrompt = this.buildSystemPrompt({ tone, brand, guardrails });
        let done = 0;
        const total = wanted.length;

        const runOne = async (platform) => {
            const userPrompt = this.buildUserPrompt({ instruction, platform, tone, brand, contentType, context });
            const budget = tokenBudgetFor(platform, this.config.maxTokens);
            if (onProgress) onProgress(`Generating ${platform} (${++done}/${total})…`);

            for (const cfg of usable) {
                try {
                    const out = await this.callWithRetry(cfg, {
                        systemPrompt, userPrompt, maxTokens: budget,
                        signal: run.abort.signal, hook: run.hook,
                    });
                    const { text, warnings } = applyGuardrails(out.text, guardrails);
                    log('info', `provider:${cfg.id}`,
                        `${platform} generated via ${cfg.label} in ${out.latencyMs} ms` +
                        (out.usage?.completion_tokens ? ` (${out.usage.completion_tokens} tokens)` : '') +
                        (warnings.length ? ` — guardrail adjustments: ${warnings.join('; ')}` : ''));
                    runMeta.pieces[platform] = {
                        provider: cfg.id, model: cfg.model, ok: true,
                        latencyMs: out.latencyMs, usage: out.usage, guardrails: warnings,
                    };
                    if (!runMeta.provider) runMeta.provider = cfg.id;
                    if (!runMeta.providers.includes(cfg.id)) runMeta.providers.push(cfg.id);
                    return text;
                } catch (e) {
                    const reason = e.name === 'AbortError' ? 'timeout' : e.message;
                    log('error', `provider:${cfg.id}`, `${platform} failed on ${cfg.label}: ${reason}`);
                    runMeta.pieces[platform] = { provider: cfg.id, ok: false, error: reason };
                }
            }

            // Every provider failed → deterministic fallback keeps the user unblocked.
            const reason = runMeta.pieces[platform]?.error || 'all providers failed';
            log('warn', 'agent.engine',
                `Falling back to the built-in copywriting engine for ${platform} (${reason})`);
            runMeta.pieces[platform] = { provider: 'builtin', ok: true, degraded: true, error: reason };
            return this.templateFallback(instruction, platform, tone, brand, contentType, context, reason);
        };

        // Bounded concurrency — local servers cannot handle 6 parallel requests.
        const queue = [...wanted];
        const workers = Array.from({ length: Math.min(this.config.concurrency, queue.length) }, async () => {
            while (queue.length) {
                const platform = queue.shift();
                try {
                    results[platform] = await runOne(platform);
                } catch (e) {
                    log('error', 'agent.engine', `Unhandled error generating ${platform}: ${e.message}`);
                    results[platform] = this.templateFallback(instruction, platform, tone, brand, contentType, context, e.message);
                }
            }
        });
        await Promise.all(workers);

        return this._finishRun(results, runMeta, startedAt, run);
    }

    /** How many generations are in flight right now. */
    get activeRuns() { return this._runs.size; }

    /** Cancel every in-flight batch (wired to the UI Cancel button). */
    abort() {
        const runs = [...this._runs];
        if (!runs.length) {
            this._log('warn', 'agent.engine', 'Cancel requested but nothing is running');
            return 0;
        }
        runs.forEach(r => r.abort.abort());
        this._log('warn', 'agent.engine',
            `Cancelled ${runs.length} in-flight generation${runs.length === 1 ? '' : 's'} by user`);
        return runs.length;
    }

    _finishRun(results, meta, startedAt, run = null) {
        meta.elapsedMs = Math.round(performance.now() - startedAt);
        meta.ok = Object.values(meta.pieces).every(p => p.ok) && !Object.values(meta.pieces).some(p => p.degraded);
        this.lastRun = meta;
        if (run) this._runs.delete(run);
        return results;
    }

    _log(level, source, message, detail, hook = null) {
        const sink = hook || this._logHook;
        if (typeof sink === 'function') {
            try { sink(level, source, message, detail); } catch { /* logging must never throw */ }
        }
        const fn = level === 'error' ? console.error : level === 'warn' ? console.warn : console.info;
        fn(`[NexusAI:${source}] ${message}`);
    }

    /* ── display + fallbacks ───────────────────────────────────────────────── */

    getDisplayName() {
        if (this.config.provider === 'antigravity') return `Google Antigravity (live brain${this.config.antigravityModel ? ' — ' + this.config.antigravityModel : ''})`;
        if (this.config.provider === 'sho') return 'Sho (external agent brain)';
        if (this.config.provider === 'builtin') return 'Built-in copywriting engine';
        if (this.config.provider === 'local') {
            const p = this.getProvider(this.config.localType === 'ollama' ? 'ollama' : 'lmstudio');
            return `${p.label} — ${this.config.localModel || p.model}`;
        }
        const p = this.getProvider(this.config.cloudProvider);
        return p ? `${p.label} — ${p.model}` : `Cloud API (${this.config.cloudProvider})`;
    }

    /**
     * Deterministic offline engine. Delegates to the rich template library in
     * script.js when present, otherwise emits a self-contained package.
     */
    templateFallback(topic, platform, tone, brand, type, ctx, reason = '') {
        const note = reason
            ? `> ℹ️ **Built-in engine** — no live AI provider answered (${reason}). This draft uses Nexus's offline copywriting framework, so review it before publishing.\n\n`
            : '';

        if (typeof window.generateSinglePlatformContent === 'function') {
            try {
                return note + window.generateSinglePlatformContent(topic, platform, tone, brand, type, ctx);
            } catch (e) {
                console.warn('templateFallback: template engine failed', e);
            }
        }
        return `${note}### ${String(platform).toUpperCase()} — ${brand}

**Hook:**
Stop scrolling. If ${topic} matters to you, this is the 60-second version. 👇

**Value:**
- 💡 Pick the one metric that actually moves the business, then ignore the rest.
- 🎯 Speak to a single customer's specific problem instead of "everyone".
- ⚡ Consistency compounds faster than perfection — publish, then improve.

**Call to action:**
What is your biggest roadblock with ${topic}? Tell me in the comments. 💬

**Hashtags:**
#digitalmarketing #contentstrategy #socialmediamarketing #${slug(topic)}`;
    }

    formatAntigravityDirective({ instruction, platform, tone, brand, contentType, context }) {
        return `## 🚀 Antigravity Agent Directive — ${String(platform).toUpperCase()}

| Field | Value |
|---|---|
| **Asset** | ${contentType} |
| **Platform** | ${platform} |
| **Brand** | ${brand} |
| **Tone** | ${tone} |
| **Topic** | ${instruction} |
${context ? `| **Context** | ${context} |\n` : ''}
---

### Skill invocation (paste into Google Antigravity)
\`\`\`text
Activate the digital-marketer skill.
Campaign: "${instruction}"
Platform: ${platform}
Brand: "${brand}"
Tone: ${tone}
Asset type: ${contentType}
Deliver: final caption/script, tiered hashtags, engagement + conversion CTA, visual direction brief, posting time, and one A/B test idea.
\`\`\`

### Agent blueprint
1. **Hook** — open with the audience's sharpest pain point (AIDA: Attention).
2. **Value** — exactly 3 concrete, specific insights about "${instruction}".
3. **Proof** — one example, result or mechanism. Never fabricate numbers.
4. **CTA** — a single primary next step, plus one soft engagement ask.
5. **Hashtags** — mega / large / medium / niche mix sized to ${platform}.`;
    }
}

/* ────────────────────────────────────────────────────────────────────────────
   HELPERS
   ──────────────────────────────────────────────────────────────────────────── */

function clamp(n, min, max, fallback) {
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
}

function slug(text) {
    return String(text || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 24) || 'marketing';
}

function shorten(text, n) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    return t.length > n ? `${t.slice(0, n)}…` : t;
}

/** Build a typed error carrying the fatal/retryable decision. */
function httpError(status, body, cfg) {
    let detail = shorten(body, 200);
    try {
        const parsed = JSON.parse(body);
        detail = parsed?.error?.message || parsed?.message || parsed?.error || detail;
        if (typeof detail === 'object') detail = JSON.stringify(detail).slice(0, 200);
    } catch { /* body was not JSON */ }

    const retryable = status === 408 || status === 409 || status === 429 || status >= 500;
    const hint = status === 401 || status === 403
        ? ` — check that the ${cfg.label} API key is valid and has access to "${cfg.model}"`
        : status === 404
            ? ` — model "${cfg.model}" may not exist for ${cfg.label}`
            : status === 429
                ? ' — rate limited, backing off'
                : '';
    return Object.assign(
        new Error(`${cfg.label} HTTP ${status}: ${detail}${hint}`),
        { status, fatal: !retryable, provider: cfg.id }
    );
}

/**
 * Brand guardrails, applied to model output before it reaches the user.
 * Returns the cleaned text plus a list of human-readable adjustments so the
 * compliance pass is auditable rather than invisible.
 */
function applyGuardrails(text, g) {
    const warnings = [];
    let out = String(text ?? '');
    if (!g || !out) return { text: out, warnings };

    if (Array.isArray(g.bannedWords) && g.bannedWords.length) {
        let hits = 0;
        for (const raw of g.bannedWords) {
            const word = String(raw).trim();
            if (!word) continue;
            const re = new RegExp(`\\b${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'gi');
            if (re.test(out)) {
                hits += re.source ? (out.match(re) || []).length : 0;
                out = out.replace(re, '[removed]');
            }
        }
        if (hits) warnings.push(`${hits} banned term(s) replaced with [removed]`);
    }

    if (g.allowEmoji === false) {
        const before = out;
        out = out.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu, '').replace(/[ \t]{2,}/g, ' ');
        if (before !== out) warnings.push('emoji stripped by brand policy');
    }

    const maxTags = Number(g.maxHashtags);
    if (Number.isFinite(maxTags) && maxTags >= 0) {
        const tags = out.match(/(^|\s)#[\p{L}\p{N}_]+/gu) || [];
        if (tags.length > maxTags) {
            let kept = 0;
            out = out.replace(/(^|\s)(#[\p{L}\p{N}_]+)/gu, (m, pre, tag) => {
                kept += 1;
                return kept <= maxTags ? m : '';
            });
            warnings.push(`hashtags trimmed from ${tags.length} to ${maxTags}`);
        }
    }

    if (g.requiredDisclaimer && !out.includes(g.requiredDisclaimer)) {
        out = `${out.trimEnd()}\n\n${g.requiredDisclaimer}`;
        warnings.push('required disclaimer appended');
    }

    return { text: out, warnings };
}

/**
 * Compact brand block for prompts. Mirrors agent/brand_research.py brand_context()
 * so UI and Python generation see the same brand.
 */
function brandContextBlock(p, maxChars = 2500) {
    if (!p || typeof p !== 'object') return '';
    const v = p.brand_voice || {};
    const meta = [p.category, p.location].filter(Boolean).join(', ');
    const lines = [`BRAND PROFILE — ${p.business_name || ''}${meta ? ` (${meta})` : ''}`];
    if (p.value_proposition) lines.push(`Value proposition: ${p.value_proposition}`);
    if (p.services?.length) lines.push('Services: ' + p.services.slice(0, 10).map(s => s.name).join('; '));
    if (p.usps?.length) lines.push('Proven USPs (only claim these): ' + p.usps.slice(0, 6).join('; '));
    if (p.personas?.length) lines.push('Audience: ' + p.personas.slice(0, 3).map(x =>
        x.name + (x.pains?.length ? ` (pains: ${x.pains.slice(0, 2).join(', ')})` : '')).join(' | '));
    if (v.adjectives?.length) lines.push('Voice: ' + v.adjectives.join(', '));
    if (v.do?.length) lines.push('Do: ' + v.do.slice(0, 5).join('; '));
    if (v.dont?.length) lines.push("Don't: " + v.dont.slice(0, 5).join('; '));
    if (p.content_pillars?.length) lines.push('Content pillars: ' + p.content_pillars.map(c => c.name).join(', '));
    const tags = ['branded', 'local', 'niche'].flatMap(k => (p.hashtag_sets?.[k] || []).slice(0, 4));
    if (tags.length) lines.push('Preferred hashtags: ' + tags.join(' '));
    if (p.local_seo_keywords?.length) lines.push('Local keywords: ' + p.local_seo_keywords.slice(0, 8).join(', '));
    if (p.compliance_notes?.length) lines.push('Compliance: ' + p.compliance_notes.slice(0, 4).join('; '));
    return lines.join('\n').slice(0, maxChars);
}

/** Text out of a Sho job result: plain string, {text}, or {content: {platform: text}}. */
function shoResultText(job, platform) {
    const r = job?.result;
    if (typeof r === 'string') return r.trim();
    if (r && typeof r.content === 'object' && r.content) return String(r.content[platform] ?? Object.values(r.content)[0] ?? '').trim();
    if (r && typeof r.text === 'string') return r.text.trim();
    if (r && typeof r.content === 'string') return r.content.trim();
    return r ? JSON.stringify(r) : '';
}

/** Owner guardrails + the brand's avoid-words and emoji policy (stricter wins). */
function mergeBrandGuardrails(g, profile) {
    const base = { ...(g || {}) };
    const v = profile?.brand_voice;
    if (!v) return base;
    const words = [...(base.bannedWords || []), ...(v.avoid_words || [])].map(w => String(w).trim()).filter(Boolean);
    base.bannedWords = [...new Set(words.map(w => w.toLowerCase()))];
    if (v.emoji_policy === 'none') base.allowEmoji = false;
    return base;
}

/** Platforms with long-form output need a bigger completion budget. */
function tokenBudgetFor(platform, base) {
    const p = String(platform || '').toLowerCase();
    if (p === 'blog') return Math.max(base, 2600);
    if (p === 'youtube' || p === 'email') return Math.max(base, 1800);
    if (p === 'twitter' || p === 'tiktok') return Math.max(base, 1200);
    return base;
}

/* ────────────────────────────────────────────────────────────────────────────
   MARKDOWN RENDERER
   Escapes first (so model output can never inject HTML), then applies a small,
   predictable subset of Markdown. The previous regex-only version emitted one
   <ul> per list item and mangled asterisks; this one tracks block structure.
   ──────────────────────────────────────────────────────────────────────────── */
function renderMarkdown(md) {
    if (!md) return '';
    const escaped = String(md)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    const lines = escaped.split(/\r?\n/);
    const html = [];
    let listType = null;    // 'ul' | 'ol' | null
    let inFence = false;
    let fence = [];
    let paragraph = [];

    const closeList = () => { if (listType) { html.push(`</${listType}>`); listType = null; } };
    const closeParagraph = () => {
        if (paragraph.length) { html.push(`<p>${inlineMarkdown(paragraph.join('<br>'))}</p>`); paragraph = []; }
    };

    for (const line of lines) {
        // Fenced code blocks take precedence over everything else.
        if (/^\s*```/.test(line)) {
            if (inFence) {
                html.push(`<pre><code>${fence.join('\n')}</code></pre>`);
                fence = []; inFence = false;
            } else {
                closeParagraph(); closeList(); inFence = true;
            }
            continue;
        }
        if (inFence) { fence.push(line); continue; }

        // Blank line = block separator.
        if (!line.trim()) { closeParagraph(); closeList(); continue; }

        // Horizontal rule.
        if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) {
            closeParagraph(); closeList();
            html.push('<hr class="output-divider">');
            continue;
        }

        // Headings.
        const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
        if (heading) {
            closeParagraph(); closeList();
            const level = heading[1].length;
            html.push(`<h${level}>${inlineMarkdown(heading[2].trim())}</h${level}>`);
            continue;
        }

        // Blockquote.
        const quote = line.match(/^\s{0,3}&gt;\s?(.*)$/);
        if (quote) {
            closeParagraph(); closeList();
            html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
            continue;
        }

        // Unordered list item.
        const ul = line.match(/^\s*[-*+•]\s+(.*)$/);
        if (ul) {
            closeParagraph();
            if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul'; }
            html.push(`<li>${inlineMarkdown(ul[1])}</li>`);
            continue;
        }

        // Ordered list item (keeps the author's numbering via <ol start>).
        const ol = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
        if (ol) {
            closeParagraph();
            if (listType !== 'ol') { closeList(); html.push(`<ol start="${Number(ol[1]) || 1}">`); listType = 'ol'; }
            html.push(`<li>${inlineMarkdown(ol[2])}</li>`);
            continue;
        }

        // Plain text accumulates into a paragraph.
        closeList();
        paragraph.push(line.trim());
    }

    if (inFence && fence.length) html.push(`<pre><code>${fence.join('\n')}</code></pre>`);
    closeParagraph();
    closeList();
    return html.join('\n');
}

/** Inline formatting for text that has already been HTML-escaped. */
function inlineMarkdown(text) {
    return text
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*\w])\*([^*\n]+)\*(?=[^*\w]|$)/g, '$1<em>$2</em>')
        .replace(/(^|[^_\w])_([^_\n]+)_(?=[^_\w]|$)/g, '$1<em>$2</em>')
        .replace(/~~([^~]+)~~/g, '<del>$1</del>')
        // Only http(s) links — javascript:/data: URLs stay inert text.
        .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
            '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

/* ────────────────────────────────────────────────────────────────────────────
   GLOBAL INSTANCE
   ──────────────────────────────────────────────────────────────────────────── */
window.NexusProviders = NEXUS_PROVIDERS;
window.NexusAI = new AIClient();
window.renderMarkdown = renderMarkdown;

// Deterministic helpers exposed for the automated test-suite (tests/test_ui.mjs).
// They are pure functions, so exporting them adds no runtime risk.
window.__nexusInternals = { applyGuardrails, tokenBudgetFor, slug, clamp, httpError, brandContextBlock, mergeBrandGuardrails, shoResultText };

