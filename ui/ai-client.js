/**
 * AIClient — Unified Multi-Harness AI Engine for Digital Marketing Agent
 * Supports:
 *  - Google Antigravity (Agentic prompt & skill workflow)
 *  - Local Models: LM Studio & Ollama (Hermes 3, Llama 3, Mistral, Phi-3)
 *  - Cloud Providers: Google Gemini, Groq, Claude, DeepSeek, Qwen, OpenAI
 *  - Offline / Resilient Fallback Engine
 */

class AIClient {
    constructor() {
        this.loadConfig();
    }

    loadConfig() {
        const stored = JSON.parse(localStorage.getItem('nexus_ai_config') || '{}');
        this.config = {
            provider: stored.provider || 'local', // 'antigravity' | 'local' | 'cloud'
            localType: stored.localType || 'lmstudio', // 'lmstudio' | 'ollama'
            localUrl: stored.localUrl || 'http://localhost:1234/v1',
            localModel: stored.localModel || 'hermes-3-llama-3.1-8b',
            cloudProvider: stored.cloudProvider || 'gemini',
            cloudApiKey: stored.cloudApiKey || '',
            cloudModel: stored.cloudModel || 'gemini-2.0-flash',
            temperature: stored.temperature !== undefined ? stored.temperature : 0.7,
            maxTokens: stored.maxTokens || 1200,
            ...stored
        };
    }

    saveConfig(updates = {}) {
        this.config = { ...this.config, ...updates };
        localStorage.setItem('nexus_ai_config', JSON.stringify(this.config));
    }

    /**
     * Test connection to configured backend
     */
    async testConnection(overrideConfig = null) {
        const cfg = overrideConfig || this.config;
        const provider = cfg.provider;

        if (provider === 'antigravity') {
            return {
                ok: true,
                provider: 'antigravity',
                message: 'Google Antigravity mode active. Direct agentic prompts and skills enabled.'
            };
        }

        if (provider === 'local') {
            const url = (cfg.localUrl || 'http://localhost:1234/v1').replace(/\/+$/, '');
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 3500);

                // Try fetching /models endpoint standard in OpenAI-compatible local servers
                const res = await fetch(`${url}/models`, {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' },
                    signal: controller.signal
                }).catch(async (err) => {
                    // Try Ollama native endpoint if /models failed
                    if (url.includes('11434')) {
                        return await fetch(`http://localhost:11434/api/tags`, { signal: controller.signal });
                    }
                    throw err;
                });
                clearTimeout(timeoutId);

                if (res.ok) {
                    const data = await res.json().catch(() => ({}));
                    const modelCount = data.data?.length || data.models?.length || 1;
                    return {
                        ok: true,
                        provider: cfg.localType || 'local',
                        message: `Connected successfully to ${url} (${modelCount} model(s) available)`
                    };
                }
                return {
                    ok: false,
                    provider: 'local',
                    message: `Local server responded with HTTP ${res.status}. Check if server is running.`
                };
            } catch (err) {
                return {
                    ok: false,
                    provider: 'local',
                    message: `Cannot connect to ${url}. Is LM Studio or Ollama running with Local Server active? (${err.message})`
                };
            }
        }

        if (provider === 'cloud') {
            const apiKey = cfg.cloudApiKey;
            if (!apiKey) {
                return { ok: false, provider: 'cloud', message: 'API Key is missing. Please enter your API key.' };
            }

            try {
                // Test lightweight query depending on cloud provider
                const endpoint = this.getCloudEndpoint(cfg.cloudProvider);
                const testReq = await fetch(endpoint.url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${apiKey}`
                    },
                    body: JSON.stringify({
                        model: endpoint.defaultModel,
                        messages: [{ role: 'user', content: 'ping' }],
                        max_tokens: 2
                    })
                });

                if (testReq.ok) {
                    return { ok: true, provider: cfg.cloudProvider, message: `Successfully authenticated with ${cfg.cloudProvider}!` };
                }
                const errData = await testReq.json().catch(() => ({}));
                return {
                    ok: false,
                    provider: cfg.cloudProvider,
                    message: `API Error ${testReq.status}: ${errData.error?.message || testReq.statusText}`
                };
            } catch (err) {
                return { ok: false, provider: cfg.cloudProvider, message: `Connection failed: ${err.message}` };
            }
        }

        return { ok: true, provider: 'unknown', message: 'Provider check passed' };
    }

    getCloudEndpoint(provider) {
        switch (provider) {
            case 'gemini':
                return {
                    url: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                    defaultModel: 'gemini-2.0-flash'
                };
            case 'groq':
                return {
                    url: 'https://api.groq.com/openai/v1/chat/completions',
                    defaultModel: 'llama-3.3-70b-versatile'
                };
            case 'deepseek':
                return {
                    url: 'https://api.deepseek.com/v1/chat/completions',
                    defaultModel: 'deepseek-chat'
                };
            case 'qwen':
                return {
                    url: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                    defaultModel: 'qwen-plus'
                };
            case 'openai':
                return {
                    url: 'https://api.openai.com/v1/chat/completions',
                    defaultModel: 'gpt-4o-mini'
                };
            default:
                return {
                    url: 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                    defaultModel: 'gemini-2.0-flash'
                };
        }
    }

    /**
     * Build rich system prompt tailored for senior social media marketer
     */
    buildSystemPrompt(options = {}) {
        const { tone = 'professional', brand = 'My Brand', isHermes = false } = options;
        
        let hermesDirective = '';
        if (isHermes) {
            hermesDirective = `\nSpecial Instruction: You are powered by Hermes. Execute with strict adherence to high-converting copywriting frameworks (AIDA, PAS, HOOK->VALUE->CTA), sharp formatting, and actionable marketing intelligence.\n`;
        }

        return `You are a world-class Senior Digital Marketing Director and Social Media Strategist with 10+ years of high-growth experience.
Brand Name: "${brand}"
Default Tone: ${tone}
${hermesDirective}
Core Rules:
1. Every piece of copy must be tailored specifically to the platform's audience and algorithmic quirks.
2. Instagram: First line is a thumb-stopping hook. 3-4 lines of value. Clear engagement CTA. 5-8 relevant hashtags. Include visual direction.
3. Facebook: Conversational, community-building, storytelling angle with open-ended discussion question.
4. YouTube: CTR-optimized A/B titles (under 60 chars), SEO keyword-rich description with timestamps outline, 12-15 tags, and thumbnail visual brief.
5. LinkedIn: Clean whitespace, bold 1-line hook, bullet-pointed business insights, no links in post body (link in comments).
6. Twitter/X: Concise punchy thread format with hook tweet, core insights, and engagement call-to-action.
7. Blog/SEO: Focus keyword, meta title (55 chars), meta desc (155 chars), H1 and H2 structure, internal link ideas.
8. Output formatted in clean, beautiful Markdown with clear bold sections.`;
    }

    /**
     * Build user prompt for a specific platform & directive
     */
    buildUserPrompt({ instruction, platform, tone, brand, contentType, context }) {
        return `CAMPAIGN BRIEF:
- Topic / Goal: "${instruction}"
- Target Platform: ${platform}
- Tone of Voice: ${tone}
- Brand Name: ${brand}
- Content Type: ${contentType}
${context ? `- Additional Context: ${context}` : ''}

Generate an exceptional, ready-to-publish content package for ${platform.toUpperCase()}.
Include:
1. Ready-to-copy Post / Caption / Script
2. Strategic Hashtag Set (Mega, Large, Medium, Niche)
3. Call-To-Action (Engagement + Conversion)
4. Visual Direction Brief (composition, text overlay, colors)`;
    }

    /**
     * Generate content across one or more platforms
     */
    async generate({ instruction, platforms, tone, brand, contentType, context, onProgress }) {
        const results = {};
        const isAntigravity = this.config.provider === 'antigravity';
        
        // If Antigravity mode: produce native agentic instructions & formatted content
        if (isAntigravity) {
            if (onProgress) onProgress('Antigravity mode: Generating comprehensive Agent Directive...');
            
            for (const platform of platforms) {
                if (onProgress) onProgress(`Synthesizing ${platform} strategy for Antigravity Agent...`);
                // Check if local or cloud can be queried as co-pilot, else synthesize rich agentic copy
                try {
                    const res = await this.queryLLM({
                        instruction, platform, tone, brand, contentType, context
                    });
                    results[platform] = res;
                } catch {
                    results[platform] = this.formatAntigravityDirective({
                        instruction, platform, tone, brand, contentType, context
                    });
                }
            }
            return results;
        }

        // Live LLM mode (Local LM Studio, Ollama, Hermes, or Cloud)
        for (let i = 0; i < platforms.length; i++) {
            const platform = platforms[i];
            if (onProgress) onProgress(`Generating ${platform} with ${this.getDisplayName()} (${i + 1}/${platforms.length})...`);

            try {
                const content = await this.queryLLM({
                    instruction, platform, tone, brand, contentType, context
                });
                results[platform] = content;
            } catch (err) {
                console.warn(`LLM call failed for ${platform}, falling back to template engine:`, err);
                if (onProgress) onProgress(`Falling back to copywriting engine for ${platform} (${err.message})...`);
                results[platform] = this.templateFallback(instruction, platform, tone, brand, contentType, context, err.message);
            }
        }

        return results;
    }

    /**
     * Dispatch LLM query to active provider
     */
    async queryLLM({ instruction, platform, tone, brand, contentType, context }) {
        const isHermes = (this.config.localModel || '').toLowerCase().includes('hermes');
        const systemPrompt = this.buildSystemPrompt({ tone, brand, isHermes });
        const userPrompt = this.buildUserPrompt({ instruction, platform, tone, brand, contentType, context });

        if (this.config.provider === 'local') {
            return await this.callOpenAICompatible({
                baseUrl: (this.config.localUrl || 'http://localhost:1234/v1').replace(/\/+$/, ''),
                apiKey: 'local',
                model: this.config.localModel || 'local-model',
                systemPrompt,
                userPrompt
            });
        }

        if (this.config.provider === 'cloud') {
            const endpoint = this.getCloudEndpoint(this.config.cloudProvider);
            return await this.callOpenAICompatible({
                baseUrl: endpoint.url.replace('/chat/completions', ''),
                apiKey: this.config.cloudApiKey,
                model: this.config.cloudModel || endpoint.defaultModel,
                systemPrompt,
                userPrompt
            });
        }

        throw new Error('Unknown AI harness');
    }

    async callOpenAICompatible({ baseUrl, apiKey, model, systemPrompt, userPrompt }) {
        const targetUrl = baseUrl.endsWith('/chat/completions') ? baseUrl : `${baseUrl}/chat/completions`;
        const headers = {
            'Content-Type': 'application/json'
        };
        if (apiKey) {
            headers['Authorization'] = `Bearer ${apiKey}`;
        }

        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 45000); // 45s timeout for local models

        try {
            const res = await fetch(targetUrl, {
                method: 'POST',
                headers,
                signal: controller.signal,
                body: JSON.stringify({
                    model: model || 'local-model',
                    messages: [
                        { role: 'system', content: systemPrompt },
                        { role: 'user', content: userPrompt }
                    ],
                    temperature: this.config.temperature,
                    max_tokens: this.config.maxTokens
                })
            });
            clearTimeout(timeout);

            if (!res.ok) {
                const errorBody = await res.text();
                throw new Error(`HTTP ${res.status}: ${errorBody.substring(0, 150)}`);
            }

            const data = await res.json();
            const choice = data.choices?.[0]?.message?.content;
            if (!choice) {
                throw new Error('Received empty response from AI model');
            }
            return choice.trim();
        } catch (err) {
            clearTimeout(timeout);
            throw err;
        }
    }

    getDisplayName() {
        if (this.config.provider === 'antigravity') return 'Google Antigravity (Agentic)';
        if (this.config.provider === 'local') {
            const model = this.config.localModel || 'Local Model';
            return `${this.config.localType === 'ollama' ? 'Ollama' : 'LM Studio'} (${model})`;
        }
        return `Cloud API (${this.config.cloudProvider.toUpperCase()})`;
    }

    /**
     * Fallback template generator when offline or local server is unreachable
     */
    templateFallback(topic, platform, tone, brand, type, ctx, reason = '') {
        const warning = reason ? `> ℹ️ *Note: AI server offline (${reason}). Generated with built-in marketing framework.*\n\n` : '';
        
        if (typeof window.generateSinglePlatformContent === 'function') {
            return warning + window.generateSinglePlatformContent(topic, platform, tone, brand, type, ctx);
        }

        return `${warning}### 📱 ${platform.toUpperCase()} POST — ${brand}

**Hook:**
Stop scrolling. If you care about ${topic}, here is the breakdown you need to hear today. 👇

**Key Insights:**
- 💡 Focus on actionable execution, not vanity metrics.
- 🎯 Speak directly to your core customer's pain point.
- ⚡ Consistency compounds faster than perfection.

**Call-To-Action:**
What is your biggest roadblock with ${topic}? Drop your thoughts below! 💬

**Hashtags:**
#digitalmarketing #growthhacking #contentcreator #${topic.toLowerCase().replace(/[^a-z0-9]/g, '')}`;
    }

    formatAntigravityDirective({ instruction, platform, tone, brand, contentType, context }) {
        return `## 🚀 Antigravity Agent Directive for ${platform.toUpperCase()}

**Task:** Generate high-converting ${contentType} for **${platform}**
**Brand:** ${brand}
**Tone:** ${tone}
**Topic:** ${instruction}
${context ? `**Context:** ${context}\n` : ''}
---
### Suggested Skill Invocation:
Run the following in your Antigravity chat:
\`\`\`bash
Activate the digital-marketer skill.
Campaign: "${instruction}"
Platform: ${platform}
Brand: "${brand}"
Tone: ${tone}
Generate complete caption, visual direction, and hashtag strategy.
\`\`\`

---
### 📋 Agent Blueprint
1. **Hook:** Formulate a high-impact opening using the AIDA framework.
2. **Body:** 3 concise value-dense points explaining "${instruction}".
3. **CTA:** Specific follow or link-in-bio prompt.
4. **Hashtags:** Mega, Large, Medium, and Brand mix.`;
    }
}

// Simple client-side Markdown to HTML converter for beautiful output rendering
function renderMarkdown(md) {
    if (!md) return '';
    let html = md
        // Escape bare HTML tags
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        // Blockquotes
        .replace(/^&gt; (.*$)/gim, '<blockquote>$1</blockquote>')
        // Headings
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^## (.*$)/gim, '<h2>$1</h2>')
        .replace(/^# (.*$)/gim, '<h1>$1</h1>')
        // Bold and Italic
        .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/gim, '<em>$1</em>')
        // Code blocks
        .replace(/```([\s\S]*?)```/gim, '<pre><code>$1</code></pre>')
        .replace(/`([^`]+)`/gim, '<code>$1</code>')
        // Unordered lists
        .replace(/^\s*[-•*]\s+(.*$)/gim, '<li>$1</li>')
        // Ordered lists
        .replace(/^\s*\d+\.\s+(.*$)/gim, '<li>$1</li>')
        // Horizontal rule
        .replace(/^---$/gim, '<hr class="output-divider">')
        // Linebreaks
        .replace(/\n\n/gim, '<br><br>');

    // Wrap adjacent <li> in <ul>
    html = html.replace(/(<li>.*?<\/li>)+/gim, (match) => `<ul>${match}</ul>`);
    return html;
}

// Global instance
window.NexusAI = new AIClient();
window.renderMarkdown = renderMarkdown;
