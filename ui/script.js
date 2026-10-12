/**
 * Nexus — Digital Marketing Command Center
 * Full application logic: routing, content generation engine, calendar, analytics, settings
 */

/* ══════════════════════════════════════════════════════════════
   APP STATE
══════════════════════════════════════════════════════════════ */
function safeJson(key, fallback) {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return fallback;
        return JSON.parse(raw);
    } catch {
        return fallback;
    }
}

const savedSettingsWrap = safeJson('nexus_settings', {});
const savedSettings = savedSettingsWrap.settings && typeof savedSettingsWrap.settings === 'object'
    ? savedSettingsWrap.settings
    : savedSettingsWrap;
const savedEvents = Array.isArray(safeJson('nexus_calendar_events', [])) ? safeJson('nexus_calendar_events', []) : [];
const savedAI = safeJson('nexus_ai_config', {});

const State = {
    currentPage: 'dashboard',
    model: savedAI.provider || savedSettingsWrap.model || 'local',      // antigravity | local | cloud | builtin
    lmStudioUrl: savedAI.localUrl || 'http://localhost:1234/v1',
    settings: {
        brand: savedSettings.brand || 'My Brand',
        niche: savedSettings.niche || '',
        audience: savedSettings.audience || '',
        tone: savedSettings.tone || 'professional',
    },
    cloudProvider: 'gemini',
    calendarDate: new Date(),
    calendarEvents: savedEvents,
    outputTabs: {},       // { platform: content }
    activeOutputTab: '',
};

/* ══════════════════════════════════════════════════════════════
   AGENT EVENT / ERROR LOG  (documents every agent action & failure)
   Persisted to localStorage; viewable on the "Agent Logs" page
   and exportable as JSON for debugging / incident reports.
══════════════════════════════════════════════════════════════ */
const AgentLog = (() => {
    const KEY = 'nexus_agent_log';
    const MAX = 500;

    function read() {
        try { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
        catch { return []; }
    }
    function write(entries) {
        try { localStorage.setItem(KEY, JSON.stringify(entries.slice(-MAX))); } catch {}
    }
    function push(level, source, message, detail = '') {
        const entry = {
            ts: new Date().toISOString(),
            level,                      // info | warn | error | success
            source,                     // e.g. agent.engine, publish, provider:xai
            message,
            detail: typeof detail === 'string' ? detail : JSON.stringify(detail),
        };
        const entries = read();
        entries.push(entry);
        write(entries);
        if (level === 'error') {
            updateErrorBadge();
            showToast(`⚠️ Agent issue logged: ${message}`, 'error');
        }
        return entry;
    }
    const info    = (s, m, d) => push('info', s, m, d);
    const warn    = (s, m, d) => push('warn', s, m, d);
    const error   = (s, m, d) => push('error', s, m, d);
    const success = (s, m, d) => push('success', s, m, d);

    function clear() { write([]); updateErrorBadge(); }
    function all() { return read(); }
    function errors() { return read().filter(e => e.level === 'error' || e.level === 'warn'); }

    function updateErrorBadge() {
        const badge = document.getElementById('logErrorBadge');
        const n = errors().length;
        if (badge) {
            badge.textContent = String(n);
            badge.style.display = n ? 'flex' : 'none';
        }
    }

    function exportJson() {
        const blob = new Blob([JSON.stringify(read(), null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `nexus-agent-log-${new Date().toISOString().slice(0, 10)}.json`;
        a.click();
        URL.revokeObjectURL(a.href);
        info('agent.log', 'Agent log exported');
    }

    return { info, warn, error, success, clear, all, errors, exportJson, updateErrorBadge };
})();

/* ══════════════════════════════════════════════════════════════
   PROVIDER ADAPTER
   The routing engine itself lives in ai-client.js (window.NexusAI):
   ONE registry, ONE credential store, ONE retry/fail-over
   implementation. These thin helpers exist only so the existing
   Settings markup and inline onclick handlers keep working.

   BUG FIX (was critical):
   • script.js used to carry a *second*, divergent provider table plus
     an entire retry/fail-over engine (`runAgent`) that nothing ever
     called — so the fail-over the docs promised never actually ran.
   • Keys saved with "Save Key for Provider" went into
     `nexus_credentials[prov-<id>-key]` while generation read
     `nexus_ai_config.cloudApiKey`; unless you also pressed
     "Save Settings" the engine saw no key and silently fell back to
     templates. Both now read the same store.
══════════════════════════════════════════════════════════════ */
const PROVIDERS = Object.fromEntries(
    Object.entries(window.NexusProviders || {}).map(([id, p]) => [id, {
        label: p.label,
        baseUrl: p.baseUrl,
        model: p.model,
        needsKey: p.needsKey,
        kind: p.kind,
        native: p.dialect === 'anthropic',
    }])
);

/** Resolved provider config (registry defaults ← saved credentials). */
function getProviderConfig(id) {
    const normalized = id === 'claude' ? 'anthropic' : id;
    const resolved = window.NexusAI?.getProvider(normalized);
    if (resolved) return { ...resolved, native: resolved.dialect === 'anthropic' };
    const base = PROVIDERS[normalized] || PROVIDERS.lmstudio || {};
    return { id: normalized, ...base, apiKey: '', model: base.model || 'local-model' };
}

/** Single write path for provider credentials. */
function saveProviderCredentials(id, { key, url, model } = {}) {
    const n = id === 'claude' ? 'anthropic' : id;
    const creds = safeJson('nexus_credentials', {});
    const set = (field, value) => {
        const k = `prov-${n}-${field}`;
        if (value === undefined) return;
        if (value) creds[k] = value; else delete creds[k];
    };
    set('key', key);
    set('url', url);
    set('model', model);
    localStorage.setItem('nexus_credentials', JSON.stringify(creds));
    return creds;
}

async function fetchWithTimeout(url, options = {}, ms = 30000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms);
    try {
        return await fetch(url, { ...options, signal: ctrl.signal });
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Probe one provider. Delegates to the engine so native providers
 * (Anthropic) are tested with a real request instead of a `/models` GET
 * that their API does not implement.
 */
async function testProviderConnection(id) {
    if (!window.NexusAI) return { ok: false, reason: 'AI engine not loaded' };
    const isLocal = (PROVIDERS[id]?.kind === 'local');
    const res = await window.NexusAI.testConnection(
        isLocal
            ? { provider: 'local', localType: id, localUrl: getProviderConfig(id).baseUrl }
            : { provider: 'cloud', cloudProvider: id, cloudApiKey: getProviderConfig(id).apiKey }
    );
    return { ok: res.ok, reason: res.message };
}

/* ══════════════════════════════════════════════════════════════
   ROUTING
══════════════════════════════════════════════════════════════ */
const PAGE_TITLES = {
    dashboard: 'Dashboard',
    agent: 'AI Agent',
    create: 'Content Studio',
    calendar: 'Content Calendar',
    analytics: 'Analytics',
    settings: 'Settings',
    hashtags: 'Hashtag Tool',
    instagram: 'Instagram Hub',
    facebook: 'Facebook Hub',
    youtube: 'YouTube Hub',
    linkedin: 'LinkedIn Hub',
    blog: 'Blog & SEO Hub',
    trends: 'Trending Now',
    browser: 'Browser Agent',
    integrations: 'MCP & Integrations',
    accounts: 'Connected Accounts',
    canva: 'Canva Studio',
    logs: 'Agent Logs',
    tutorial: 'Tutorial',
};

function switchPage(pageId) {
    document.querySelectorAll('.page-content').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(li => li.classList.remove('active'));

    const page = document.getElementById(`page-${pageId}`);
    if (page) {
        page.classList.add('active');
        // Re-trigger animations
        page.querySelectorAll('.fade-up').forEach(el => {
            el.style.animation = 'none';
            el.offsetHeight; // reflow
            el.style.animation = '';
        });
    }

    const navItem = document.querySelector(`.nav-item[data-page="${pageId}"]`);
    if (navItem) navItem.classList.add('active');

    document.getElementById('pageHeading').textContent = PAGE_TITLES[pageId] || pageId;
    State.currentPage = pageId;

    // Page-specific init
    if (pageId === 'dashboard') initDashboard();
    if (pageId === 'calendar') renderCalendar();
    if (pageId === 'analytics') initAnalytics();
    if (pageId === 'trends') fetchTrends();
    if (pageId === 'logs') renderAgentLogs();
    if (pageId === 'tutorial') initTutorial();
    if (pageId === 'accounts') { setTimeout(() => { initAccountsPage(); refreshQueueView(); }, 50); }
    if (pageId === 'browser') initBrowserPage();
    if (pageId === 'utm') applyUtmPreset();
}

document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        const pageId = link.closest('.nav-item')?.dataset.page;
        if (pageId) switchPage(pageId);
        document.querySelector('.app-shell')?.classList.remove('mobile-open');
    });
});

function initChrome() {
    const shell = document.querySelector('.app-shell');
    document.getElementById('sidebarToggle')?.addEventListener('click', () => {
        if (window.innerWidth <= 900) {
            shell?.classList.toggle('mobile-open');
        } else {
            shell?.classList.toggle('sidebar-collapsed');
        }
    });
    document.getElementById('mobileNavBtn')?.addEventListener('click', () => shell?.classList.toggle('mobile-open'));
    document.getElementById('sidebarBackdrop')?.addEventListener('click', () => shell?.classList.remove('mobile-open'));
    document.getElementById('notifBtn')?.addEventListener('click', (e) => {
        e.stopPropagation();
        const panel = document.getElementById('notifPanel');
        if (!panel) return;
        const open = panel.classList.toggle('open');
        if (open) renderNotifications(panel);
    });
    document.addEventListener('click', () => document.getElementById('notifPanel')?.classList.remove('open'));
}

function renderNotifications(panel) {
    const logs = AgentLog.all().slice(-8).reverse();
    if (!logs.length) {
        panel.innerHTML = '<div class="notif-item">No notifications yet. Agent activity will appear here.</div>';
        return;
    }
    panel.innerHTML = logs.map(l => `<div class="notif-item"><strong>${escHtml(l.level)}</strong> · ${escHtml(l.message)}</div>`).join('');
    const dot = document.getElementById('notifDot');
    if (dot) dot.style.display = AgentLog.errors().length ? 'block' : 'none';
}

/* ══════════════════════════════════════════════════════════════
   GREETING
══════════════════════════════════════════════════════════════ */
function setGreeting() {
    const h = new Date().getHours();
    const el = document.getElementById('greetingText');
    if (!el) return;
    if (h < 12) el.textContent = 'Good Morning! ☀️';
    else if (h < 17) el.textContent = 'Good Afternoon! 🚀';
    else el.textContent = 'Good Evening! 🌙';
}

/* ══════════════════════════════════════════════════════════════
   DASHBOARD
══════════════════════════════════════════════════════════════ */
function marketingStats() {
    const events = State.calendarEvents || [];
    const history = safeJson('nexus_content_history', []);
    const queue = safeJson('nexus_publish_queue', []);
    const now = new Date();
    const thisMonth = events.filter(e => {
        if (!e.date) return false;
        const d = new Date(e.date);
        return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
    });
    const published = queue.filter(q => q.status === 'published').length
        + events.filter(e => e.status === 'published').length;
    const scheduled = events.filter(e => e.status === 'scheduled').length
        + queue.filter(q => q.status === 'queued' || q.status === 'scheduled').length;
    const platforms = {};
    events.forEach(e => {
        if (e.platform) platforms[e.platform] = (platforms[e.platform] || 0) + 1;
    });
    const generatedPieces = history.reduce((n, h) => n + Object.keys(h.results || {}).length, 0);
    return { thisMonth: thisMonth.length, published, scheduled, history: history.length, generatedPieces, platforms, queue, events };
}

function initDashboard() {
    const stats = marketingStats();
    const queuedEl = document.getElementById('kpiQueued');
    if (queuedEl) {
        queuedEl.dataset.count = String(stats.scheduled);
        queuedEl.textContent = String(stats.scheduled);
    }
    const genEl = document.getElementById('kpiGenerated');
    if (genEl) genEl.textContent = String(stats.generatedPieces);
    const monthEl = document.getElementById('kpiMonth');
    if (monthEl) {
        monthEl.dataset.count = String(stats.thisMonth);
        monthEl.textContent = String(stats.thisMonth);
    }
    const pubEl = document.getElementById('kpiPublished');
    if (pubEl) {
        pubEl.dataset.count = String(stats.published);
        pubEl.textContent = String(stats.published);
    }
    const monthTrend = document.getElementById('kpiMonthTrend');
    if (monthTrend) monthTrend.innerHTML = `<i class="ph ph-calendar-blank"></i>${stats.thisMonth}`;
    renderPlatformBars(stats.platforms);
    renderUpcomingPosts();
    // animateCounters/animateBars existed but were never called — the KPI numbers
    // therefore never animated despite every card carrying data-count.
    animateCounters();
    animateBars();
}

function renderPlatformBars(platforms) {
    const host = document.getElementById('platformBars');
    if (!host) return;
    const rows = [
        { key: 'instagram', label: 'Instagram', icon: 'ph-fill ph-instagram-logo', fill: 'var(--ig-grad)' },
        { key: 'facebook', label: 'Facebook', icon: 'ph-fill ph-facebook-logo', fill: 'linear-gradient(90deg,#1877f2,#0d65d9)' },
        { key: 'youtube', label: 'YouTube', icon: 'ph-fill ph-youtube-logo', fill: 'linear-gradient(90deg,#ff0000,#cc0000)' },
        { key: 'linkedin', label: 'LinkedIn', icon: 'ph-fill ph-linkedin-logo', fill: 'linear-gradient(90deg,#0a66c2,#004182)' },
        { key: 'tiktok', label: 'TikTok', icon: 'ph-fill ph-tiktok-logo', fill: 'linear-gradient(90deg,#25f4ee,#fe2c55)' },
        { key: 'twitter', label: 'X / Twitter', icon: 'ph-fill ph-twitter-logo', fill: 'linear-gradient(90deg,#1d9bf0,#0f1419)' },
    ];
    const max = Math.max(1, ...rows.map(r => platforms[r.key] || 0));
    const total = Object.values(platforms).reduce((a, b) => a + b, 0);
    if (!total) {
        host.innerHTML = '<p class="helper-text">No calendar posts yet. Add posts or auto-plan a week to see mix.</p>';
        return;
    }
    host.innerHTML = rows.map(r => {
        const n = platforms[r.key] || 0;
        const pct = Math.round((n / max) * 100);
        return `<div class="pbar-row">
            <div class="pbar-label"><i class="${r.icon}"></i> ${r.label}</div>
            <div class="pbar-track"><div class="pbar-fill" style="width:${pct}%;background:${r.fill}"></div></div>
            <div class="pbar-val">${n}</div>
        </div>`;
    }).join('');
}

function animateCounters() {
    document.querySelectorAll('.kpi-val[data-count]').forEach(el => {
        const target = parseInt(el.dataset.count);
        let current = 0;
        const step = target / 50;
        const interval = setInterval(() => {
            current = Math.min(current + step, target);
            el.textContent = target >= 1000
                ? (current / 1000).toFixed(1) + 'K'
                : Math.floor(current).toString();
            if (current >= target) clearInterval(interval);
        }, 20);
    });
}

function animateBars() {
    setTimeout(() => {
        document.querySelectorAll('.pbar-fill').forEach(bar => {
            const w = bar.style.width;
            bar.style.width = '0';
            requestAnimationFrame(() => {
                bar.style.width = w;
            });
        });
    }, 100);
}

const SAMPLE_POSTS = [];

const STATUS_MAP = {
    scheduled: { cls: 'st-scheduled', label: 'Scheduled' },
    draft: { cls: 'st-draft', label: 'Draft' },
    planning: { cls: 'st-planning', label: 'Planning' },
};

const PLATFORM_ICONS = {
    ig: 'ph-fill ph-instagram-logo',
    fb: 'ph-fill ph-facebook-logo',
    yt: 'ph-fill ph-youtube-logo',
    li: 'ph-fill ph-linkedin-logo',
    tw: 'ph-fill ph-twitter-logo',
    blog: 'ph ph-article',
};

function renderUpcomingPosts() {
    const list = document.getElementById('upcomingPostsList');
    if (!list) return;
    const mapped = State.calendarEvents
        .slice()
        .sort((a, b) => String(a.date).localeCompare(String(b.date)))
        .map(e => ({
            platform: ({ instagram: 'ig', facebook: 'fb', youtube: 'yt', linkedin: 'li', twitter: 'tw', tiktok: 'tw', blog: 'blog' })[e.platform] || e.platform,
            title: e.title,
            time: [e.date, e.time].filter(Boolean).join(' '),
            status: e.status || 'planning',
        }));
    const posts = mapped.slice(0, 6);
    if (!posts.length) {
        list.innerHTML = '<p class="helper-text">Nothing scheduled. Use the calendar to add posts or auto-plan 7 days.</p>';
        return;
    }
    list.innerHTML = posts.map(p => {
        const s = STATUS_MAP[p.status] || STATUS_MAP.planning;
        return `
        <div class="sched-item">
            <div class="sched-picon ${escHtml(p.platform)}"><i class="${PLATFORM_ICONS[p.platform] || 'ph ph-file'}"></i></div>
            <div class="sched-details">
                <div class="sched-title">${escHtml(p.title)}</div>
                <div class="sched-time">${escHtml(p.time || p.date || '')}</div>
            </div>
            <div class="status-badge ${s.cls}">${escHtml(s.label)}</div>
        </div>`;
    }).join('');
}

/* ══════════════════════════════════════════════════════════════
   CONTENT GENERATION ENGINE
   All generation happens client-side using a comprehensive 
   knowledge base — no API needed for basic mode.
   When model = 'gemini'|'lmstudio', outputs a ready-to-use prompt
   the user can paste into their chosen AI system.
══════════════════════════════════════════════════════════════ */

const CONTENT_DB = {
    hooks: {
        curiosity: [
            "Nobody talks about this, but {topic} is quietly changing everything...",
            "I discovered something about {topic} that completely changed my strategy.",
            "The {topic} secret that {number}% of people never find out.",
        ],
        controversy: [
            "Hot take: Most advice about {topic} is completely wrong. Here's why ↓",
            "Unpopular opinion: You don't need [expensive tool] for {topic}.",
            "Stop doing {topic} the traditional way. Do this instead.",
        ],
        number: [
            "{number} proven strategies for {topic} that top creators swear by:",
            "{number} things I wish I knew before starting {topic}",
            "I tested {number} ways to improve {topic}. Here's what actually worked.",
        ],
        warning: [
            "This {topic} mistake is costing you followers every single day.",
            "WARNING: If you're still doing {topic} this way, stop immediately.",
            "The {topic} advice everyone gives that's actually ruining your growth.",
        ],
    },
    hashtag_banks: {
        tech: {
            mega: ['#technology', '#tech', '#innovation', '#digital', '#startup'],
            large: ['#techindustry', '#techstartup', '#aitools', '#futuretech', '#digitaltransformation'],
            medium: ['#techfounder', '#saaslife', '#productlaunch', '#techmarketing', '#b2bsaas'],
            niche: ['#founderstories', '#techentrepreneur', '#growthhacking', '#productled'],
        },
        fashion: {
            mega: ['#fashion', '#style', '#ootd', '#fashionista', '#clothing'],
            large: ['#fashionblogger', '#streetstyle', '#fashionlover', '#instafashion', '#outfitoftheday'],
            medium: ['#sustainablefashion', '#slowfashion', '#fashiontips', '#minimalstyle', '#capsulewardrobe'],
            niche: ['#ethicalfashion', '#consciousconsumer', '#thriftedstyle', '#vintagefashion'],
        },
        fitness: {
            mega: ['#fitness', '#workout', '#gym', '#health', '#motivation'],
            large: ['#fitnessmotivation', '#fitnessjourney', '#gymlife', '#workoutroutine', '#fitfam'],
            medium: ['#personaltrainer', '#homeworkout', '#strengthtraining', '#weightloss', '#musclebuilding'],
            niche: ['#coachlife', '#fitnesscoach', '#trainersofinstagram', '#functionalfitness'],
        },
        marketing: {
            mega: ['#marketing', '#digitalmarketing', '#socialmedia', '#business', '#entrepreneur'],
            large: ['#contentmarketing', '#marketingstrategy', '#socialmediamarketing', '#onlinemarketing', '#growthhacking'],
            medium: ['#marketingtips', '#contentcreator', '#brandstrategy', '#emailmarketing', '#seomarketing'],
            niche: ['#marketingmindset', '#b2bmarketing', '#contentcalendar', '#marketingagency'],
        },
    },
    cta_bank: {
        engagement: ['Save this for later! 💾', 'Tag someone who needs this 👇', 'Comment your biggest challenge below!', 'Drop a ❤️ if this helped you', 'Share your experience in the comments!'],
        follow: ['Follow for daily marketing tips 🚀', 'Hit that follow button for more insights!'],
        link: ['Link in bio for the full guide', 'DM me "GUIDE" for the free resource', 'Click the link below to get started'],
        sales: ['Limited spots available — act now!', 'DM us to learn more!', 'Book your free strategy call today'],
    },
    seo_templates: {
        meta_title: '{keyword} | {modifier} Guide {year}',
        meta_desc: 'Looking for {keyword}? Discover {count} proven strategies that {benefit}. Learn how to {outcome} — read our complete guide.',
        h1: 'The Complete Guide to {keyword}: {count} Strategies for {year}',
        h2_ideas: [
            'What is {keyword}? (And why it matters)',
            'The Top {count} Strategies for {keyword}',
            '{count} Common {keyword} Mistakes to Avoid',
            'How to Get Started with {keyword}',
            '{keyword} Tools and Resources',
            'Frequently Asked Questions About {keyword}',
        ],
    },
    email_templates: {
        newsletter: {
            subject_a: "📊 {brand} Weekly: {topic} insights you can't miss",
            subject_b: "{topic}: What happened this week + what to do next",
            preview: "Read time: 3 min — packed with value.",
        },
        launch: {
            subject_a: "🚀 It's live! Introducing {product}",
            subject_b: "We've been building something for you... [reveal inside]",
            preview: "Your {topic} problems end today.",
        },
        promo: {
            subject_a: "⏰ {discount}% OFF ends at midnight — don't miss it",
            subject_b: "Last chance: {product} deal closes tonight",
            preview: "No code needed. Just {cta}.",
        },
    },
};

window.generateSinglePlatformContent = function(topic, platform, tone, brand, type, ctx) {
    switch ((platform || '').toLowerCase()) {
        case 'instagram': return genInstagramContent(topic, tone, brand, type, ctx);
        case 'facebook':  return genFacebookContent(topic, tone, brand, ctx);
        case 'youtube':   return genYouTubeContent(topic, tone, brand, ctx);
        case 'linkedin':  return genLinkedInContent(topic, tone, brand, ctx);
        case 'twitter':   return genTwitterContent(topic, tone, brand, ctx);
        case 'tiktok':    return genTikTokContent(topic, tone, brand, ctx);
        case 'blog':      return genBlogContent(topic, brand, ctx);
        case 'email':     return genEmailContent(topic, tone, brand, ctx);
        case 'ads':       return genAdCopy(topic, tone, brand);
        default: return genInstagramContent(topic, tone, brand, type, ctx);
    }
};

function genInstagramContent(topic, tone, brand, type, ctx) {
    const hooks = Object.values(CONTENT_DB.hooks).flat();
    const hook = hooks[Math.floor(Math.random() * hooks.length)]
        .replace('{topic}', topic).replace('{number}', '7');

    const hashtags = getHashtags('marketing');

    return `📱 **INSTAGRAM ${type === 'reel' ? 'REEL SCRIPT' : 'POST'}**
Brand: ${brand}

**Caption:**
${hook}

Here's what I've learned about "${topic}":

✅ Start with your audience's biggest pain point
✅ Deliver immediate value — no fluff
✅ Use one clear call-to-action
✅ Keep it visually engaging and scroll-stopping

${toneStatement(tone, topic)}

What's your experience with this? Drop a comment below! 👇

**Hashtags (Mix — copy/paste):**
${hashtags}

---
**🎨 Visual Direction:**
• Format: Square (1080×1080) or 4:5 portrait
• Style: Clean, bold typography on dark gradient background
• Overlay text: "${hook.substring(0, 50)}..."
• Mood: ${tone === 'inspirational' ? 'Warm, motivating' : 'Professional, clean'}
• Include: Brand color accent, subtle logo watermark bottom-right${type === 'reel' ? '\n\n**🎬 Reel Script:**\n[0-2s] Hook: "' + hook.substring(0, 60) + '"\n[2-15s] Problem: Paint the pain of NOT knowing this\n[15-40s] Value: Walk through 3 key points with b-roll\n[40-55s] CTA: "Follow for more" + save prompt\n[55-60s] Outro: Brand name + music fade' : ''}`;
}

function genFacebookContent(topic, tone, brand, ctx) {
    return `📘 **FACEBOOK POST**
Brand: ${brand}

**Copy:**
Can I be honest with you for a second?

Most people struggling with "${topic}" aren't lacking effort — they're lacking the right framework.

I see it every day. People working hard but going in circles because nobody told them the three things that actually move the needle:

1️⃣ Clarity on your ideal audience (not everyone is your customer)
2️⃣ Consistent content rhythm (shows up daily, not perfectly)
3️⃣ A clear conversion path (what do you want them to do next?)

${toneStatement(tone, topic)}

${brand} has been helping businesses crack this code. And we're sharing everything we know.

Drop a 🙋 in the comments if you've been there. And tell me — what's your #1 challenge with ${topic} right now?

---
**📊 Post Settings:**
• Best time: Wednesday or Thursday, 1-3 PM local time
• Boost: After 2 hours, if organic reach > 300, boost with $5/day for 3 days
• Target: Interests related to ${topic}, ages 25-45`;
}

function genYouTubeContent(topic, tone, brand, ctx) {
    const titleA = `${topic}: The Complete Guide That ACTUALLY Works (${new Date().getFullYear()})`;
    const titleB = `I Tried Every ${topic} Strategy — Here's What Actually Worked`;
    const tags = generateTags(topic);

    return `🎬 **YOUTUBE CONTENT PACKAGE**
Brand: ${brand}

**Title (A/B Test):**
[Version A] ${titleA}
[Version B] ${titleB}

**Description:**
Everything you need to know about ${topic} — in one video.

In this video, I break down:
• Why most people fail at ${topic} (and how to avoid it)
• The exact framework I've used to get results
• Real examples and case studies you can swipe

🔔 Subscribe + hit the bell to never miss a video
👇 Drop your questions in the comments — I read every one

🔗 Resources mentioned:
• [Add your links here]

📌 Timestamps:
0:00 — Introduction
1:30 — Why ${topic} matters in ${new Date().getFullYear()}
4:00 — The 3 biggest mistakes
8:00 — The framework that works
14:00 — Live examples
18:30 — Q&A
22:00 — Next steps

${brand} | ${topic} | Strategy

**Tags:**
${tags}

---
**📸 Thumbnail Brief:**
• Background: Dark gradient (deep blue to black)
• Your face: Large, showing surprise/excitement expression (left side)
• Text overlay: "${topic.substring(0,20).toUpperCase()}" in bold white, "GAME CHANGER" in yellow
• Maximum 6 words on thumbnail
• A/B test two thumbnail styles`;
}

function genLinkedInContent(topic, tone, brand, ctx) {
    return `💼 **LINKEDIN POST**
Brand: ${brand}

**Copy:**
I've spent years studying ${topic}.

Here's everything I wish I knew earlier: (a thread)

Most professionals overcomplicate it.

The real secret? Three simple principles:

→ Principle 1: Know your outcome before you act
    Every action should trace back to a measurable goal.

→ Principle 2: Consistency > intensity
    Showing up every day at 70% beats showing up once at 100%.

→ Principle 3: Feedback loops are everything
    Measure weekly. Adjust monthly. Never guess quarterly.

${toneStatement(tone, topic)}

I've applied these principles at ${brand} and the results speak for themselves.

What principle would you add? Drop it in the comments below.

♻️ Repost this if it helped someone on your network.

---
**⚙️ LinkedIn Optimization:**
• Do NOT include external links in the post body — put them in first comment
• First comment: "Resources for this post → [your link]"
• Best time: Tuesday-Thursday, 7-9 AM or 12 PM
• Engage with every comment within 1 hour of posting`;
}

function genTikTokContent(topic, tone, brand, ctx) {
    return `🎵 **TIKTOK / SHORT-FORM PACKAGE**
Brand: ${brand}

**Hook (0–2s on-screen text):**
Stop scrolling if you care about ${topic}.

**Script (35–45s):**
[0-2s] Face-to-camera hook + text overlay
[2-12s] Name the mistake most people make with ${topic}
[12-28s] 3 punchy steps (one per cut)
[28-38s] Proof / example — keep captions on screen
[38-45s] CTA: follow + comment the word that matches your niche

**On-screen captions:** 6–8 words per line, high contrast, never cover faces.

**Sound:** trending original audio OR voiceover-first (algorithm prefers watch time).

**Hashtags (3–5, not 20):**
${getHashtags('marketing').split(' ').slice(0, 5).join(' ')}

**CTA:** ${toneStatement(tone, topic)}

**Posting:** 3–5x / week, first comment pinned with the tracked UTM link.`;
}

function genTwitterContent(topic, tone, brand, ctx) {
    return `🐦 **TWITTER / X THREAD**
Brand: ${brand}

**Tweet 1 (Hook):**
I studied ${topic} for 3 years.

Here's everything I know condensed into a thread 🧵

**Tweet 2:**
Most people think ${topic} is complicated.

It's not.

It comes down to 3 core ideas:
• Clarity
• Consistency  
• Conversion

Let me break each one down...

**Tweet 3:**
Clarity first.

Before you do ANYTHING with ${topic}, you need to know:
- Who exactly you're talking to
- What specific problem you solve
- Why you > everyone else

No clarity = no traction.

**Tweet 4:**
Consistency second.

The algorithm doesn't care about your best day.
It cares about your average day.

Post consistently. Engage consistently. Show up.

Results compound — but only if you're consistent.

**Tweet 5:**
Conversion last.

Every piece of content needs ONE job.
Pick one CTA. Stick to it.

Watch → Comment → Follow → Click → Buy

Move people one step forward. Not ten.

**Tweet 6 (CTA):**
That's the ${topic} thread.

Follow @${brand.replace(' ', '')} for more threads like this every week.

RT this if it helped you 🔁

---
**📌 Post Strategy:**
• Schedule Tweet 1, wait 5 min, post Tweet 2 as reply — continue thread
• Like and reply to every comment in first 30 minutes
• Pin the thread to your profile for 7 days`;
}

function genBlogContent(topic, brand, ctx) {
    const seo = CONTENT_DB.seo_templates;
    const year = new Date().getFullYear();
    return `📝 **BLOG POST SEO BRIEF**
Brand: ${brand}
Topic: ${topic}

**Meta Title (55 chars max):**
${seo.meta_title.replace('{keyword}', topic).replace('{modifier}', 'Complete').replace('{year}', year)}

**Meta Description (155 chars max):**
${seo.meta_desc.replace(/{keyword}/g, topic).replace('{count}', '7').replace('{benefit}', 'drive real results').replace('{outcome}', 'get started today')}

**H1:**
${seo.h1.replace('{keyword}', topic).replace('{count}', '7').replace('{year}', year)}

**Recommended H2 Structure:**
${seo.h2_ideas.map((h, i) => `${i+1}. ${h.replace(/{keyword}/g, topic).replace('{count}', 3+i)}`).join('\n')}

**Internal Link Strategy:**
• Link to 3 related posts on your site (add placeholders: [LINK: topic])
• External links: 1-2 authoritative sources (research from Google Scholar, Statista, or HubSpot)

**SEO Checklist:**
✅ Primary keyword "${topic}" in H1, first 100 words, one H2, meta
✅ Secondary keywords in H3s and natural body flow
✅ All images have keyword-rich alt text
✅ Post length: 1500-2000 words minimum
✅ FAQ section targeting featured snippet (question+answer format)
✅ CTA at end: newsletter sign-up or free resource download

**Target Word Count:** 1800 words
**Estimated Read Time:** 7 min`;
}

function genEmailContent(topic, tone, brand, ctx) {
    const tpl = CONTENT_DB.email_templates.newsletter;
    return `📧 **EMAIL / NEWSLETTER**
Brand: ${brand}

**Subject Line A:** ${tpl.subject_a.replace('{brand}', brand).replace('{topic}', topic)}
**Subject Line B:** ${tpl.subject_b.replace('{topic}', topic)}
**Preview Text:** ${tpl.preview}

---

Hi {{first_name || 'there'}},

This week we're diving deep into ${topic} — and I want to share something that's been working incredibly well for us at ${brand}.

**What you'll get in this email:**
• The one insight about ${topic} that changed everything
• A quick 3-step framework you can apply today
• A resource to help you get started

---

**THE INSIGHT:**

[Write your main insight here — 2-3 paragraphs, value-first]

**THE FRAMEWORK:**

1. Step One — [Action + Expected result]
2. Step Two — [Action + Expected result]  
3. Step Three — [Action + Expected result]

---

**THIS WEEK'S RESOURCE:**
📎 [Title of resource] → [link]

---

${toneStatement(tone, topic)}

Until next time,
The ${brand} Team

**P.S.** — [Strongest CTA here: book a call, download the template, grab the deal]

---
**⚙️ Email Settings:**
• Send time: Tuesday or Thursday, 8-10 AM local
• A/B split: 20% each subject line, 60% winner after 4 hours
• Segment: Send to engaged subscribers (opened in last 90 days) first`;
}

function genAdCopy(topic, tone, brand) {
    return `💰 **AD COPY PACKAGE**
Brand: ${brand}

**Meta Ad (Facebook/Instagram):**

Primary Text:
"Struggling with ${topic}? You're not alone.
Most people never figure out the one thing that makes all the difference.
${brand} has helped 1,000+ [audience] solve exactly this — in less time than you think.
See how → [link]"

Headline A: Stop Struggling with ${topic}
Headline A: ${brand}: ${topic} Made Simple
Description: Join 1,000+ [audience] getting real results.

---

**Google Search Ad:**
Headline 1: ${topic} Solutions | ${brand}
Headline 2: Proven Results — Get Started Today
Headline 3: Free Consultation Available Now
Description 1: Discover how ${brand} solves ${topic} for [audience]. Trusted by 1,000+ clients.
Description 2: Book your free strategy session. No commitment. Results guaranteed.

---
**🎯 Audience Targeting (Meta):**
• Interests: [related interests to ${topic}]
• Age: 25-45
• Lookalike: 1-2% of your customer list
• Exclude: Existing customers`;
}

/* ── Helpers ─────────────────────────────────────────────────── */
function toneStatement(tone, topic) {
    const statements = {
        professional: `At a professional level, mastering ${topic} is non-negotiable for sustainable growth.`,
        casual: `Honestly? Once you get the hang of ${topic}, it becomes second nature. Promise! 😄`,
        inspirational: `The journey with ${topic} is where the growth happens. Trust the process. ✨`,
        educational: `Research consistently shows that those who invest in ${topic} see measurable results within 90 days.`,
        humorous: `${topic}... it's like assembling IKEA furniture — confusing at first, but deeply satisfying when it clicks. 😂`,
        urgent: `Every day you wait on ${topic} is a day your competitors get ahead. The window is closing. ⚡`,
        bold: `Let's be blunt: ignoring ${topic} in ${new Date().getFullYear()} is a choice — and it's costing you. Own it. 🔥`,
        playful: `Okay real talk — ${topic} is basically the group project nobody wanted... until now. Let's make it fun! 🎉`,
    };
    return statements[tone] || statements.professional;
}

function getHashtags(niche = 'marketing') {
    const bank = CONTENT_DB.hashtag_banks[niche] || CONTENT_DB.hashtag_banks.marketing;
    return [
        ...bank.mega.slice(0, 2),
        ...bank.large.slice(0, 3),
        ...bank.medium.slice(0, 3),
        ...bank.niche.slice(0, 2),
    ].join(' ');
}

function generateTags(topic) {
    const t = topic.toLowerCase().replace(/\s+/g, ' ');
    return [t, `${t} tutorial`, `${t} tips`, `${t} for beginners`, `${t} guide`,
        `how to ${t}`, `best ${t}`, `${t} strategy`, `${t} ${new Date().getFullYear()}`,
        'digitalmarketing', 'socialmedia', 'onlinebusiness', 'contentcreator', 'entrepreneur'
    ].join(', ');
}

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ══════════════════════════════════════════════════════════════
   AI AGENT FORM
 ══════════════════════════════════════════════════════════════ */
document.getElementById('agentForm')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const instruction = document.getElementById('agentInstruction').value.trim();
    if (!instruction) { showToast('Please enter an instruction.', 'info'); return; }

    const platforms = [...document.querySelectorAll('.ppill input:checked')].map(cb => cb.value);
    if (!platforms.length) { showToast('Select at least one platform.', 'info'); return; }

    const tone = document.getElementById('toneSelect').value;
    const brand = document.getElementById('brandName').value || State.settings.brand || 'My Brand';
    const contentType = document.querySelector('input[name="contentType"]:checked')?.value || 'daily';
    const context = document.getElementById('agentContext').value;

    // UI feedback
    const feedback = document.getElementById('agentFeedback');
    const feedbackText = document.getElementById('agentFeedbackText');
    const dispatchBtn = document.getElementById('dispatchBtn');
    const successToast = document.getElementById('successToast');
    const outputEmpty = document.getElementById('outputEmpty');
    const outputTabs = document.getElementById('outputTabs');

    feedback.classList.remove('hidden');
    successToast.classList.add('hidden');
    dispatchBtn.disabled = true;
    setDispatchCancelVisible(true);

    try {
        if (typeof AgentLog !== 'undefined') {
            AgentLog.info('agent.dispatch', `Task dispatched: "${instruction.slice(0, 80)}"`,
                { platforms, tone, brand, contentType, engine: State.model });
        }

        const results = await window.NexusAI.generate({
            instruction,
            platforms,
            tone,
            brand,
            contentType,
            context,
            onProgress: (statusMsg) => { feedbackText.textContent = statusMsg; },
            // Every provider attempt, retry and fallback is written to the
            // Agent Logs page — this is the wiring that was missing before.
            onLog: (level, source, message, detail) => {
                const fn = AgentLog[level] || AgentLog.info;
                fn.call(AgentLog, source, message, detail);
            },
        });

        feedback.classList.add('hidden');
        dispatchBtn.disabled = false;
        setDispatchCancelVisible(false);

        State.outputTabs = results;
        renderOutputTabs(results);
        outputEmpty.classList.toggle('hidden', Object.keys(results).length > 0);
        outputTabs.classList.toggle('hidden', Object.keys(results).length === 0);
        successToast.classList.remove('hidden');

        // Auto-save generation to local history (with the run metadata so the
        // Analytics page can report which provider actually served each piece).
        const history = safeJson('nexus_content_history', []);
        history.unshift({
            date: new Date().toISOString(),
            instruction,
            brand,
            platforms,
            results,
            meta: window.NexusAI.lastRun || null,
        });
        try {
            localStorage.setItem('nexus_content_history', JSON.stringify(history.slice(0, 30)));
        } catch (e) {
            AgentLog.warn('agent.dispatch', 'History storage full — trimming to the 10 newest runs');
            localStorage.setItem('nexus_content_history', JSON.stringify(history.slice(0, 10)));
        }

        setTimeout(() => successToast.classList.add('hidden'), 4000);
        const engineLabel = (window.NexusAI && window.NexusAI.getDisplayName) ? window.NexusAI.getDisplayName() : (PROVIDERS[State.model]?.label || State.model);
        const run = window.NexusAI.lastRun;
        const degraded = Object.values(run?.pieces || {}).filter(p => p.degraded).length;
        if (typeof AgentLog !== 'undefined') {
            AgentLog.success('agent.dispatch', `Task completed via ${engineLabel}`, {
                pieces: Object.keys(results), elapsedMs: run?.elapsedMs, degradedPieces: degraded,
            });
        }
        const pieceCount = Object.keys(results).length;
        showToast(
            degraded
                ? `⚠️ Generated ${pieceCount} piece(s) — ${degraded} used the offline fallback (see Agent Logs)`
                : `✅ Generated ${pieceCount} piece(s) with ${engineLabel}!`,
            degraded ? 'info' : 'success'
        );
    } catch (err) {
        feedback.classList.add('hidden');
        dispatchBtn.disabled = false;
        setDispatchCancelVisible(false);
        if (typeof AgentLog !== 'undefined') {
            AgentLog.error('agent.dispatch', 'Generation error', err.message);
        }
        showToast(`❌ Generation error: ${err.message}`, 'error');
    }
});

function setDispatchCancelVisible(visible) {
    const btn = document.getElementById('dispatchCancelBtn');
    if (btn) btn.classList.toggle('hidden', !visible);
}

function cancelDispatch() {
    if (window.NexusAI) window.NexusAI.abort();
    showToast('Cancelling generation…', 'info');
}

function renderOutputTabs(results) {
    const tabsRow = document.getElementById('tabsRow');
    const tabContent = document.getElementById('tabContent');
    if (!tabsRow || !tabContent) return;
    const platforms = Object.keys(results || {});

    // BUG FIX: an empty result set used to index platforms[0] (undefined) and
    // render a broken tab + blank panel instead of an explicit message.
    if (!platforms.length) {
        tabsRow.innerHTML = '';
        tabContent.innerHTML = '<div class="content-block"><p>No content was returned. Check the Agent Logs page for the provider error, then retry.</p></div>';
        State.activeOutputTab = '';
        return;
    }

    tabsRow.innerHTML = platforms.map((p, i) =>
        `<button class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${escHtml(p)}">${escHtml(p.toUpperCase())}</button>`
    ).join('');
    tabsRow.querySelectorAll('.tab-btn').forEach(btn =>
        btn.addEventListener('click', () => switchOutputTab(btn.dataset.tab)));

    State.activeOutputTab = platforms[0];
    const initialContent = results[platforms[0]] || '';
    tabContent.innerHTML = `<div class="content-block">${renderContent(initialContent)}</div>`;
    attachContentActions(tabContent);
}

/** Render either markdown (via the shared renderer) or escaped plain text. */
function renderContent(text) {
    const value = text == null ? '' : String(text);
    return window.renderMarkdown ? window.renderMarkdown(value) : escHtml(value);
}

/**
 * Copy helpers. `navigator.clipboard` only exists in a secure context, but the
 * README recommends opening ui/index.html directly from disk — where it is
 * undefined and the old code threw "Cannot read properties of undefined".
 */
async function copyToClipboard(text) {
    const value = String(text ?? '');
    if (!value) return false;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(value);
            return true;
        }
    } catch { /* fall through to the legacy path */ }
    try {
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.setAttribute('readonly', '');
        ta.style.position = 'fixed';
        ta.style.top = '-1000px';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        ta.setSelectionRange(0, value.length);
        const ok = document.execCommand('copy');
        ta.remove();
        return ok;
    } catch {
        return false;
    }
}

function switchOutputTab(platform) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === platform));
    const content = State.outputTabs[platform] || '';
    const host = document.getElementById('tabContent');
    if (!host) return;
    host.innerHTML = `<div class="content-block">${renderContent(content)}</div>`;
    attachContentActions(host);
    State.activeOutputTab = platform;
}

/** Add per-output utility buttons (repurpose / UTM / guardrail report). */
function attachContentActions(host) {
    const block = host.querySelector('.content-block');
    if (!block) return;
    const bar = document.createElement('div');
    bar.className = 'content-actions';
    bar.innerHTML = `
        <button class="btn btn-ghost btn-sm" data-act="copy"><i class="ph ph-copy"></i> Copy</button>
        <button class="btn btn-ghost btn-sm" data-act="utm"><i class="ph ph-link"></i> Add UTM link</button>
        <button class="btn btn-ghost btn-sm" data-act="repurpose"><i class="ph ph-arrows-clockwise"></i> Repurpose</button>
        <button class="btn btn-ghost btn-sm" data-act="calendar"><i class="ph ph-calendar-plus"></i> To calendar</button>
        <button class="btn btn-ghost btn-sm" data-act="publisher"><i class="ph ph-paper-plane-tilt"></i> To publisher</button>`;
    bar.addEventListener('click', (e) => {
        const act = e.target.closest('button')?.dataset.act;
        if (!act) return;
        const text = State.outputTabs[State.activeOutputTab] || '';
        if (act === 'copy') {
            copyToClipboard(text).then(ok => showToast(ok ? 'Copied to clipboard!' : 'Copy failed — select the text manually', ok ? 'success' : 'error'));
        } else if (act === 'utm') {
            buildUtmForActiveOutput();
        } else if (act === 'repurpose') {
            repurposeActiveOutput();
        } else if (act === 'calendar') {
            addOutputToCalendar();
        } else if (act === 'publisher') {
            sendOutputToPublisher();
        }
    });
    block.appendChild(bar);
}

async function copyOutput() {
    const content = State.outputTabs[State.activeOutputTab] || '';
    const ok = await copyToClipboard(content);
    showToast(ok ? 'Copied to clipboard!' : 'Copy failed — your browser blocked clipboard access', ok ? 'success' : 'error');
}

/** Copy the text of a <code>/<pre> block by id (used by inline onclick in index.html). */
async function copyCode(id) {
    const el = document.getElementById(id);
    if (!el) { showToast('Nothing to copy', 'error'); return false; }
    const ok = await copyToClipboard(el.innerText || el.textContent || '');
    showToast(ok ? 'Copied to clipboard!' : 'Copy failed — select the text manually', ok ? 'success' : 'error');
    return ok;
}

function clearOutput() {
    document.getElementById('outputEmpty').classList.remove('hidden');
    document.getElementById('outputTabs').classList.add('hidden');
    State.outputTabs = {};
}

/* ── Quick Templates ─────────────────────────────────────────── */
const QUICK_TEMPLATES = [
    { icon: '🚀', title: 'Product Launch', desc: 'Full campaign across all platforms', instruction: 'Create a 5-day product launch campaign for our new tool.', platforms: ['instagram','facebook','email'], tone: 'urgent', type: 'campaign' },
    { icon: '📱', title: 'Daily Reel', desc: 'Instagram + TikTok hook + script', instruction: 'Create a 60-second educational Reel about productivity tips.', platforms: ['instagram'], tone: 'casual', type: 'reel' },
    { icon: '✉️', title: 'Newsletter', desc: 'Weekly email with subject lines', instruction: 'Write a weekly newsletter about industry trends and insights.', platforms: ['email'], tone: 'professional', type: 'email' },
    { icon: '📝', title: 'SEO Blog Post', desc: 'Full blog brief with SEO structure', instruction: 'Write a comprehensive guide for our primary keyword.', platforms: ['blog'], tone: 'educational', type: 'blog' },
    { icon: '💰', title: 'Ad Copy', desc: 'Meta + Google ad copy package', instruction: 'Create high-converting ad copy for our latest offer.', platforms: ['instagram','facebook'], tone: 'urgent', type: 'ads' },
    { icon: '🎬', title: 'YouTube Video', desc: 'Title, description, tags package', instruction: 'Create a YouTube video package for a tutorial on our product.', platforms: ['youtube'], tone: 'educational', type: 'daily' },
];

function renderQuickTemplates() {
    const grid = document.getElementById('templatesGrid');
    if (!grid) return;
    grid.innerHTML = QUICK_TEMPLATES.map(t => `
        <div class="tpl-card" onclick="applyTemplate(${QUICK_TEMPLATES.indexOf(t)})">
            <div class="tpl-icon">${t.icon}</div>
            <div class="tpl-title">${t.title}</div>
            <div class="tpl-desc">${t.desc}</div>
        </div>
    `).join('');
}

function applyTemplate(idx) {
    const t = QUICK_TEMPLATES[idx];
    document.getElementById('agentInstruction').value = t.instruction;
    document.getElementById('toneSelect').value = t.tone;

    // Set platforms
    document.querySelectorAll('.ppill input').forEach(cb => {
        cb.checked = t.platforms.includes(cb.value);
    });

    // Set content type
    const radio = document.querySelector(`input[name="contentType"][value="${t.type}"]`);
    if (radio) radio.checked = true;

    showToast(`Template "${t.title}" applied!`, 'success');
}

/* ══════════════════════════════════════════════════════════════
   CONTENT STUDIO TABS
══════════════════════════════════════════════════════════════ */
document.querySelectorAll('.studio-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.studio-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.studio-panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(`studio-${tab.dataset.studio}`)?.classList.add('active');
    });
});

// Caption Generator
document.getElementById('genCaptionBtn')?.addEventListener('click', () => {
    const topic = document.getElementById('captionTopic').value;
    const platform = document.getElementById('captionPlatform').value;
    const formula = document.getElementById('captionFormula').value;
    if (!topic) { showToast('Enter a topic first', 'info'); return; }
    const platMap = { Instagram: 'instagram', Facebook: 'facebook', LinkedIn: 'linkedin', 'Twitter/X': 'twitter', TikTok: 'tiktok' };
    const content = window.generateSinglePlatformContent(topic, platMap[platform] || 'instagram', 'professional', State.settings.brand, formula, '');
    document.getElementById('captionOutput').textContent = content;
});

// Hook Generator
document.getElementById('genHookBtn')?.addEventListener('click', () => {
    const topic = document.getElementById('hookTopic').value;
    const hookType = document.getElementById('hookType').value;
    if (!topic) { showToast('Enter a topic first', 'info'); return; }
    const typeMap = { 'Curiosity Gap': 'curiosity', 'Controversy / Hot Take': 'controversy', 'Number / List': 'number', 'Warning / Mistake': 'warning', 'Story / Personal': 'curiosity', 'Question': 'curiosity' };
    const key = typeMap[hookType] || 'curiosity';
    const hooks = CONTENT_DB.hooks[key] || CONTENT_DB.hooks.curiosity;
    const output = hooks.map((h, i) => `${i + 1}. ${h.replace('{topic}', topic).replace('{number}', Math.floor(Math.random() * 8) + 3)}`).join('\n\n');
    document.getElementById('hookOutput').textContent = output;
});

// Hashtag Generator (Studio)
document.getElementById('genHashtagBtn')?.addEventListener('click', () => {
    const topic = document.getElementById('hashtagTopic').value.toLowerCase();
    const niche = Object.keys(CONTENT_DB.hashtag_banks).find(k => topic.includes(k)) || 'marketing';
    const bank = CONTENT_DB.hashtag_banks[niche];
    const allTags = [...bank.mega, ...bank.large, ...bank.medium, ...bank.niche];
    const outEl = document.getElementById('hashtagOutput');
    outEl.innerHTML = allTags.map((tag, i) => {
        const cls = i < 5 ? 'ht-mega' : i < 10 ? 'ht-large' : i < 14 ? 'ht-medium' : 'ht-niche';
        return `<span class="ht-pill ${cls}" data-tag="${escHtml(tag)}" title="Click to copy">${escHtml(tag)}</span>`;
    }).join('');
    outEl.querySelectorAll('.ht-pill').forEach(p =>
        p.addEventListener('click', () => copyTag(p.dataset.tag)));
});

// Email Generator
document.getElementById('genEmailBtn')?.addEventListener('click', () => {
    const type = document.getElementById('emailType').value;
    const audience = document.getElementById('emailAudience').value || 'your audience';
    const message = document.getElementById('emailMessage').value || 'our offer';
    const content = genEmailContent(message, 'professional', State.settings.brand, audience);
    document.getElementById('emailOutput').textContent = content;
});

// SEO Brief
document.getElementById('genSEOBtn')?.addEventListener('click', () => {
    const kw = document.getElementById('seoPrimaryKw').value;
    const wc = document.getElementById('seoWordCount').value;
    if (!kw) { showToast('Enter a keyword first', 'info'); return; }
    const content = genBlogContent(kw, State.settings.brand, '');
    document.getElementById('seoOutput').textContent = content;
});

// A/B Testing
document.getElementById('genABBtn')?.addEventListener('click', () => {
    const type = document.getElementById('abType').value;
    const topic = document.getElementById('abTopic').value;
    if (!topic) { showToast('Enter a topic first', 'info'); return; }

    let out = `🧪 A/B TEST — ${type}\nTopic: ${topic}\n\n`;

    if (type === 'Email Subject Line') {
        out += `[Version A — Curiosity]\n"The ${topic} secret nobody is talking about"\n\n[Version B — Benefit]\n"How to achieve ${topic} results in 30 days or less"`;
    } else if (type === 'YouTube Title') {
        out += `[Version A — Number/List]\n"7 ${topic} Strategies That ACTUALLY Work (${new Date().getFullYear()})"\n\n[Version B — Question]\n"Is Your ${topic} Strategy Actually Working? Watch Before It's Too Late"`;
    } else if (type === 'Ad Headline') {
        out += `[Version A — Pain Point]\n"Stop Wasting Time on ${topic} That Doesn't Work"\n\n[Version B — Aspirational]\n"${topic} Made Simple — Join 10,000+ Professionals"`;
    } else {
        out += `[Version A — Story/Emotional]\n"I was struggling with ${topic} for 2 years. Then I found this..."\n\n[Version B — Data/Fact]\n"Studies show 78% of people doing ${topic} wrong. Are you one of them?"`;
    }

    const outEl = document.getElementById('abOutput');
    outEl.classList.remove('ab-output');
    outEl.textContent = out;
});

function copyTag(tag) {
    copyToClipboard(tag).then(ok => showToast(ok ? `Copied ${tag}` : 'Copy blocked by the browser', ok ? 'success' : 'error'));
}

/* ══════════════════════════════════════════════════════════════
   CALENDAR
══════════════════════════════════════════════════════════════ */
const PLATFORM_EV_CLS = { instagram: 'ev-ig', facebook: 'ev-fb', youtube: 'ev-yt', linkedin: 'ev-li', twitter: 'ev-tw', tiktok: 'ev-tw', blog: 'ev-blog', email: 'ev-blog' };

function renderCalendar() {
    const grid = document.getElementById('calendarGrid');
    if (!grid) return;

    const d = State.calendarDate;
    const year = d.getFullYear();
    const month = d.getMonth();

    document.getElementById('calMonthLabel').textContent =
        d.toLocaleDateString('en-US', { month: 'long', year: 'numeric' });

    const firstDay = new Date(year, month, 1).getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    const today = new Date();

    // BUG FIX: events created before ids existed could not be opened, approved
    // or deleted. Backfill missing ids/status once, on read.
    let needsPersist = false;
    State.calendarEvents.forEach(e => {
        if (!e.id) { e.id = `ev-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`; needsPersist = true; }
        if (!e.status) { e.status = 'draft'; needsPersist = true; }
    });
    if (needsPersist) persistCalendarEvents();

    const getEventsForDay = (day) => {
        const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        return State.calendarEvents
            .filter(ev => ev.date === dateStr)
            .sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));
    };

    const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    let html = DAY_NAMES.map(n => `<div class="cal-day-label">${n}</div>`).join('');

    // Empty cells before month start
    for (let i = 0; i < firstDay; i++) html += `<div class="cal-cell other-month"><div class="cal-date">&nbsp;</div></div>`;

    for (let day = 1; day <= daysInMonth; day++) {
        const events = getEventsForDay(day);
        const isToday = today.getDate() === day && today.getMonth() === month && today.getFullYear() === year;
        const shown = events.slice(0, 3);
        const evHtml = shown.map(ev => {
            const badge = STATUS_BADGE[ev.status] || STATUS_BADGE.draft;
            return `<div class="cal-event ${PLATFORM_EV_CLS[ev.platform] || ''}" data-id="${escHtml(ev.id)}" role="button" tabindex="0"
                        title="${escHtml(`${badge.label} · ${ev.time || 'no time'} · ${ev.title}`)}">${escHtml(ev.title)}</div>`;
        }).join('');
        const more = events.length > shown.length
            ? `<div class="cal-more">+${events.length - shown.length} more</div>`
            : '';

        html += `<div class="cal-cell ${isToday ? 'today' : ''} ${events.length ? 'has-event' : ''}">
            <div class="cal-date">${day}</div>
            ${evHtml}${more}
        </div>`;
    }

    grid.innerHTML = html;

    grid.querySelectorAll('.cal-event[data-id]').forEach(el => {
        const open = () => renderPostDetail(el.dataset.id);
        el.addEventListener('click', open);
        el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });

    renderCalendarSummary();
}

function renderCalendarSummary() {
    const el = document.getElementById('calendarSummary');
    if (!el) return;
    const counts = {};
    State.calendarEvents.forEach(e => { const s = e.status || 'draft'; counts[s] = (counts[s] || 0) + 1; });
    const monthPrefix = `${State.calendarDate.getFullYear()}-${String(State.calendarDate.getMonth() + 1).padStart(2, '0')}`;
    const thisMonth = State.calendarEvents.filter(e => String(e.date || '').startsWith(monthPrefix)).length;
    const parts = POST_STATUS_FLOW.filter(s => counts[s]).map(s => `${counts[s]} ${STATUS_BADGE[s].label.toLowerCase()}`);
    el.textContent = `${thisMonth} post(s) this month` + (parts.length ? ` — ${parts.join(' · ')}` : '');
}

document.getElementById('prevMonth')?.addEventListener('click', () => {
    State.calendarDate = new Date(State.calendarDate.getFullYear(), State.calendarDate.getMonth() - 1, 1);
    renderCalendar();
});
document.getElementById('nextMonth')?.addEventListener('click', () => {
    State.calendarDate = new Date(State.calendarDate.getFullYear(), State.calendarDate.getMonth() + 1, 1);
    renderCalendar();
});

document.getElementById('addEventBtn')?.addEventListener('click', () => {
    document.getElementById('addEventModal').classList.remove('hidden');
    // Set default date to today
    const today = new Date();
    document.getElementById('eventDate').value = today.toISOString().split('T')[0];
});

function closeModal() {
    document.getElementById('addEventModal').classList.add('hidden');
}

document.getElementById('saveEventBtn')?.addEventListener('click', () => {
    const title = document.getElementById('eventTitle').value;
    const date = document.getElementById('eventDate').value;
    const time = document.getElementById('eventTime').value;
    const platform = document.getElementById('eventPlatform').value;
    const status = document.getElementById('eventStatus').value;

    if (!title || !date) { showToast('Enter a title and date', 'info'); return; }

    const newEvent = {
        id: `ev-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        title, date, time, platform,
        status: status || 'draft',
        createdAt: new Date().toISOString(),
        history: [{ status: status || 'draft', at: new Date().toISOString() }],
    };
    State.calendarEvents.push(newEvent);
    persistCalendarEvents();
    closeModal();
    renderCalendar();
    AgentLog.info('calendar', `Post created: "${title}" (${platform}, ${date} ${time || ''})`);
    showToast(`Post added: ${title}`, 'success');
});

/* ══════════════════════════════════════════════════════════════
   ANALYTICS
══════════════════════════════════════════════════════════════ */
function initAnalytics() {
    const stats = marketingStats();
    const perf = safeJson('nexus_performance', null);
    const perfRows = perf?.rows || [];

    const kpis = document.getElementById('analyticsKpis');
    if (kpis) {
        const cards = [];
        if (perfRows.length) {
            const impr = perfRows.reduce((a, c) => a + (c.impressions || 0), 0);
            const spend = perfRows.reduce((a, c) => a + (c.spend || 0), 0);
            const rev = perfRows.reduce((a, c) => a + (c.revenue || 0), 0);
            const clicks = perfRows.reduce((a, c) => a + (c.clicks || 0), 0);
            cards.push(
                { v: impr.toLocaleString(), l: 'Impressions (imported)' },
                { v: clicks.toLocaleString(), l: 'Clicks (imported)' },
                { v: impr ? ((clicks / impr) * 100).toFixed(2) + '%' : 'n/a', l: 'Blended CTR' },
                { v: '$' + spend.toFixed(0), l: 'Ad spend' },
                { v: rev ? (rev / spend).toFixed(2) + '×' : 'n/a', l: 'Blended ROAS' },
                { v: `${perfRows.length}`, l: 'Channels tracked' },
            );
        } else {
            cards.push(
                { v: stats.thisMonth, l: 'Calendar posts this month' },
                { v: stats.generatedPieces, l: 'AI pieces generated' },
                { v: stats.scheduled, l: 'Queued / scheduled' },
                { v: stats.published, l: 'Published via API' },
                { v: stats.history, l: 'Generation sessions' },
                { v: Object.keys(stats.platforms).length, l: 'Active platforms' },
            );
        }
        kpis.innerHTML = cards.map(c =>
            `<div class="akpi"><div class="akpi-val">${escHtml(String(c.v))}</div><div class="akpi-label">${escHtml(c.l)}</div></div>`
        ).join('');
    }

    const summary = document.getElementById('analyticsSummaryLine');
    if (summary && perf?.updatedAt) {
        summary.textContent = `Last import: ${new Date(perf.updatedAt).toLocaleString()}`;
    }

    const history = safeJson('nexus_content_history', []);
    const topPosts = history.slice(0, 5).map((h, i) => ({
        rank: i + 1,
        title: h.instruction || 'Untitled brief',
        meta: `${(h.platforms || []).join(', ')} • ${(h.date || '').slice(0, 10)} • via ${h.meta?.provider || 'built-in'}`,
        stat: `${Object.keys(h.results || {}).length} pieces`,
    }));
    const list = document.getElementById('topPostsList');
    if (list) {
        list.innerHTML = topPosts.length ? topPosts.map(p => `
        <div class="tpost-item">
            <div class="tpost-rank">${p.rank}</div>
            <div class="tpost-info">
                <div class="tpost-title">${escHtml(p.title)}</div>
                <div class="tpost-meta">${escHtml(p.meta)}</div>
            </div>
            <div class="tpost-stat">${escHtml(p.stat)}</div>
        </div>`).join('') : '<p class="helper-text">Generate content to populate this list from real sessions.</p>';
    }

    const recs = [];
    if (!perfRows.length) {
        recs.push('Import a platform export below (Instagram/Facebook Insights, X analytics, LinkedIn or an ads CSV) to unlock engagement rate, CTR, CPC, CPM, CPA and ROAS.');
    }
    if (stats.thisMonth < 8) recs.push(`Only ${stats.thisMonth} calendar post(s) this month — auto-plan 7 days to keep the cadence up.`);
    if (!stats.platforms.tiktok && !stats.platforms.instagram) recs.push('Add short-form (Reels / TikTok) — it is the cheapest organic reach channel for most brands.');
    if (stats.published === 0) recs.push('Connect accounts, start python3 agent/nexus_relay.py, then run a Validate (dry run) followed by Publish.');
    if (stats.generatedPieces && stats.scheduled === 0) recs.push('You have generated copy that is not scheduled. Send it to the Publisher or add it to the calendar.');
    const untagged = history.some(h => Object.values(h.results || {}).some(v => /https?:\/\//.test(v) && !/utm_source/.test(v)));
    if (untagged) recs.push('Some generated copy contains links without UTM parameters. Run them through the UTM Builder so performance is attributable.');
    if (!recs.length) recs.push('Cadence looks healthy. Next: review Agent Logs after each publish and A/B test one variable at a time.');
    const recList = document.getElementById('recList');
    if (recList) recList.innerHTML = recs.map(r => `<li>${escHtml(r)}</li>`).join('');
}

/* ══════════════════════════════════════════════════════════════
   HASHTAG TOOL PAGE
══════════════════════════════════════════════════════════════ */
document.getElementById('htGenBtn')?.addEventListener('click', () => {
    const topic = document.getElementById('htTopic').value.toLowerCase();
    const count = parseInt(document.getElementById('htCount')?.value || '20', 10) || 20;
    if (!topic) { showToast('Enter a topic first', 'info'); return; }

    const niche = Object.keys(CONTENT_DB.hashtag_banks).find(k => topic.includes(k)) || 'marketing';
    const bank = CONTENT_DB.hashtag_banks[niche];

    const sections = [
        { label: '🔴 Mega (1M+ posts) — high reach, competitive', tags: bank.mega, cls: 'ht-mega' },
        { label: '🟠 Large (100K-1M) — strong reach', tags: bank.large, cls: 'ht-large' },
        { label: '🟢 Medium (10K-100K) — sweet spot for engagement', tags: bank.medium, cls: 'ht-medium' },
        { label: '🟣 Niche (<10K) — targeted, loyal audience', tags: bank.niche, cls: 'ht-niche' },
    ];

    const resultsEl = document.getElementById('hashtagResults');
    resultsEl.innerHTML = sections.map(s => `
        <div class="ht-section">
            <div class="ht-section-label">${s.label}</div>
            <div class="ht-section-pills">
                ${s.tags.slice(0, Math.max(3, Math.ceil(count / 4))).map(t => `<span class="ht-pill ${s.cls}" data-tag="${escHtml(t)}" title="Click to copy">${escHtml(t)}</span>`).join('')}
            </div>
        </div>
    `).join('');
    resultsEl.querySelectorAll('.ht-pill').forEach(p =>
        p.addEventListener('click', () => copyTag(p.dataset.tag)));
});

/* ══════════════════════════════════════════════════════════════
   SETTINGS
══════════════════════════════════════════════════════════════ */
function applyEngineToUi(engine, { notify = false } = {}) {
    State.model = engine;
    const cloudField = document.getElementById('cloudSettings');
    const localField = document.getElementById('localSettings');
    if (cloudField) cloudField.style.display = engine === 'cloud' ? 'block' : 'none';
    if (localField) localField.style.display = engine === 'local' ? 'block' : 'none';
    const sel = document.getElementById('modelSelect');
    if (sel) sel.value = engine;
    const radio = document.querySelector(`input[name="aiModel"][value="${engine}"]`);
    if (radio) radio.checked = true;
    if (window.NexusAI) {
        window.NexusAI.saveConfig({ provider: engine });
    }
    const names = { antigravity: 'Google Antigravity (agentic handoff)', cloud: 'Cloud APIs', local: 'Local Models (LM Studio / Ollama)', builtin: 'Built-in templates' };
    if (notify) showToast(`Agent engine: ${names[engine] || engine}`, 'success');
    saveSettings();
}

document.querySelectorAll('input[name="aiModel"]').forEach(radio => {
    radio.addEventListener('change', () => applyEngineToUi(radio.value, { notify: true }));
});


document.getElementById('modelSelect')?.addEventListener('change', (e) => {
    applyEngineToUi(e.target.value, { notify: true });
});

// Test AI Connection Buttons
document.getElementById('testLocalAIBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('testLocalAIBtn');
    btn.innerHTML = '<i class="ph ph-spinner" style="animation:spin .7s linear infinite"></i> Testing...';
    btn.disabled = true;

    const localUrl = document.getElementById('localModelUrl')?.value || 'http://localhost:1234/v1';
    const localType = document.getElementById('localHarnessType')?.value || 'lmstudio';
    const localModel = document.getElementById('localModelName')?.value || 'hermes-3-llama-3.1-8b';

    const res = await window.NexusAI.testConnection({
        provider: 'local',
        localUrl,
        localType,
        localModel
    });

    btn.innerHTML = '<i class="ph ph-plug"></i> Test Local Server';
    btn.disabled = false;
    showToast(res.message, res.ok ? 'success' : 'error');
});

document.getElementById('testCloudAIBtn')?.addEventListener('click', async () => {
    const btn = document.getElementById('testCloudAIBtn');
    btn.innerHTML = '<i class="ph ph-spinner" style="animation:spin .7s linear infinite"></i> Testing...';
    btn.disabled = true;

    const cloudProvider = document.getElementById('cloudProvider')?.value || 'gemini';
    const cloudApiKey = document.getElementById('cloudApiKey')?.value || '';
    const cloudModel = document.getElementById('cloudModelInput')?.value || '';

    const res = await window.NexusAI.testConnection({
        provider: 'cloud',
        cloudProvider,
        cloudApiKey,
        cloudModel
    });

    btn.innerHTML = '<i class="ph ph-plug"></i> Test Cloud AI';
    btn.disabled = false;
    showToast(res.message, res.ok ? 'success' : 'error');
});

document.getElementById('cloudProvider')?.addEventListener('change', (e) => {
    const id = e.target.value;
    State.cloudProvider = id;
    // BUG FIX: the engine used to keep the OLD provider until "Save Settings"
    // was pressed, so generating right after switching sent the request (and the
    // key) to the previously selected vendor. Sync the engine immediately.
    if (window.NexusAI) window.NexusAI.saveConfig({ cloudProvider: id });
    // prefill the key field if we already have one saved for this provider
    const kf = document.getElementById('cloudApiKey');
    if (kf) kf.value = getProviderConfig(id).apiKey || '';
    const modelInput = document.getElementById('cloudModelInput');
    if (modelInput) modelInput.value = getProviderConfig(id).model || '';
    saveSettings();
    updateProviderHint();
});

/** Show the docs link + key status for the selected provider. */
function updateProviderHint() {
    const id = currentProviderId();
    const p = window.NexusProviders?.[id === 'claude' ? 'anthropic' : id];
    const hint = document.getElementById('providerHint');
    if (!hint || !p) return;
    const hasKey = !!getProviderConfig(id).apiKey;
    hint.innerHTML = `${escHtml(p.label)} · default model <code>${escHtml(p.model)}</code> · ` +
        (p.needsKey
            ? (hasKey ? '<span style="color:#4ade80">key stored ✔</span>' : '<span style="color:#fbbf24">no key yet</span>')
            : '<span style="color:#4ade80">local, no key needed</span>') +
        (p.keysUrl ? ` · <a href="${escHtml(p.keysUrl)}" target="_blank" rel="noopener noreferrer">get a key</a>` : '');
}

/* ── Provider key management (Settings page) ── */
function currentProviderId() {
    return document.getElementById('cloudProvider')?.value || State.cloudProvider || 'gemini';
}

function saveProviderKey() {
    const id = currentProviderId();
    const key = document.getElementById('cloudApiKey')?.value.trim();
    if (!key) { showToast('Enter an API key first.', 'info'); return; }
    saveProviderCredentials(id, { key });
    // Keep the engine's mirror of the active provider in sync so generation
    // works immediately, without a second trip through "Save Settings".
    if (window.NexusAI && (id === 'claude' ? 'anthropic' : id) === (window.NexusAI.config.cloudProvider === 'claude' ? 'anthropic' : window.NexusAI.config.cloudProvider)) {
        window.NexusAI.saveConfig({ cloudApiKey: key });
    }
    if (typeof AgentLog !== 'undefined') {
        AgentLog.info(`provider:${id}`, 'API key saved to browser storage (never transmitted to any Nexus server)');
    }
    updateProviderHint();
    showToast(`${PROVIDERS[id]?.label || id} key saved — it is used immediately`, 'success');
}

async function pingProvider() {
    const id = currentProviderId();
    const typed = document.getElementById('cloudApiKey')?.value.trim();
    if (typed) saveProviderCredentials(id, { key: typed });
    const out = document.getElementById('providerTestResult');
    if (out) out.textContent = 'Testing…';
    const res = await testProviderConnection(id);
    if (out) out.textContent = res.ok ? '✔ Reachable' : `✘ ${res.reason}`;
    if (typeof AgentLog !== 'undefined') {
        if (res.ok) AgentLog.success(`provider:${id}`, 'Connection test passed');
        else AgentLog.error(`provider:${id}`, `Connection test failed: ${res.reason}`);
    }
    updateProviderHint();
    showToast(res.ok ? `${PROVIDERS[id]?.label || id}: reachable` : `${PROVIDERS[id]?.label || id}: ${res.reason}`, res.ok ? 'success' : 'error');
}

// Persist local model base URL + model so the engine survives reloads
document.getElementById('localModelUrl')?.addEventListener('change', (e) => {
    const url = e.target.value.trim();
    State.lmStudioUrl = url;
    const isOllama = url.includes('11434');
    if (url) saveProviderCredentials(isOllama ? 'ollama' : 'lmstudio', { url });
    if (window.NexusAI) {
        window.NexusAI.saveConfig({ localUrl: url, localType: isOllama ? 'ollama' : 'lmstudio' });
    }
});

document.getElementById('localModelName')?.addEventListener('change', (e) => {
    const model = e.target.value.trim();
    const isOllama = (document.getElementById('localHarnessType')?.value === 'ollama');
    if (model) saveProviderCredentials(isOllama ? 'ollama' : 'lmstudio', { model });
    if (window.NexusAI) window.NexusAI.saveConfig({ localModel: model });
});

document.getElementById('localHarnessType')?.addEventListener('change', (e) => {
    const type = e.target.value;
    const url = type === 'ollama' ? 'http://localhost:11434/v1' : 'http://localhost:1234/v1';
    const urlInput = document.getElementById('localModelUrl');
    if (urlInput) urlInput.value = url;
    State.lmStudioUrl = url;
    if (window.NexusAI) window.NexusAI.saveConfig({ localType: type, localUrl: url });
});

const SETTINGS_KEY = 'nexus_settings';

function saveSettings() {
    try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify({
            settings: State.settings,
            model: State.model,
            cloudProvider: State.cloudProvider,
        }));
    } catch {}
}

function loadSettings() {
    let s;
    try { s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || 'null'); } catch { s = null; }
    if (!s) return;
    Object.assign(State.settings, s.settings || {});
    State.cloudProvider = s.cloudProvider || 'gemini';
    const brand = State.settings.brand;
    const bEl = document.getElementById('brandName'); if (bEl) bEl.value = brand;
    const sb = document.getElementById('settingsBrand'); if (sb) sb.value = brand;
    const sn = document.getElementById('settingsNiche'); if (sn) sn.value = State.settings.niche;
    const sa = document.getElementById('settingsAudience'); if (sa) sa.value = State.settings.audience;
    const toneRadio = document.querySelector(`input[name="brandTone"][value="${State.settings.tone}"]`);
    if (toneRadio) toneRadio.checked = true;
    applyEngineToUi(s.model || 'antigravity');
    const cp = document.getElementById('cloudProvider'); if (cp) cp.value = State.cloudProvider;
    const savedCreds = safeJson('nexus_credentials', {});
    const lm = document.getElementById('localModelUrl');
    if (lm && savedCreds['local-model-url']) { lm.value = savedCreds['local-model-url']; State.lmStudioUrl = savedCreds['local-model-url']; }
    const kf = document.getElementById('cloudApiKey');
    if (kf) kf.value = getProviderConfig(State.cloudProvider).apiKey || '';
    const cmi = document.getElementById('cloudModelInput');
    if (cmi) cmi.value = getProviderConfig(State.cloudProvider).model || '';
    const tgt = document.getElementById('tgToken'); if (tgt) tgt.value = savedCreds['telegram-token'] || '';
    const tgc = document.getElementById('tgChatId'); if (tgc) tgc.value = savedCreds['telegram-chat-id'] || '';
    const bap = document.getElementById('browserActionsPermission'); if (bap) bap.checked = !!savedCreds['browser-actions-permission'];
    const rt = document.getElementById('relayToken'); if (rt) rt.value = savedCreds['relay-token'] || '';
    const ru = document.getElementById('relayUrl'); if (ru) ru.value = savedCreds['relay-url'] || '';
    loadGuardrailsIntoUi();
    updateProviderHint();
}

document.getElementById('saveSettingsBtn')?.addEventListener('click', () => {
    State.settings.brand = document.getElementById('settingsBrand')?.value || 'My Brand';
    State.settings.niche = document.getElementById('settingsNiche')?.value || '';
    State.settings.audience = document.getElementById('settingsAudience')?.value || '';
    State.settings.tone = document.querySelector('input[name="brandTone"]:checked')?.value || 'professional';

    // Save AI Config
    const aiProvider = document.querySelector('input[name="aiModel"]:checked')?.value || State.model || 'local';
    const cloudProvider = document.getElementById('cloudProvider')?.value || 'gemini';
    const cloudApiKey = document.getElementById('cloudApiKey')?.value.trim() || '';
    const cloudModel = document.getElementById('cloudModelInput')?.value.trim() || '';
    const localUrl = document.getElementById('localModelUrl')?.value.trim() || 'http://localhost:1234/v1';
    const localModel = document.getElementById('localModelName')?.value.trim() || 'hermes-3-llama-3.1-8b';
    const localType = document.getElementById('localHarnessType')?.value || 'lmstudio';

    if (window.NexusAI) {
        window.NexusAI.saveConfig({
            provider: aiProvider,
            localType, localModel, localUrl,
            cloudProvider, cloudApiKey, cloudModel,
            guardrails: readGuardrailsFromUi(),
        });
    }

    // Per-provider credentials go to the shared store (one write path).
    if (cloudApiKey) saveProviderCredentials(cloudProvider, { key: cloudApiKey, model: cloudModel });
    else if (cloudModel) saveProviderCredentials(cloudProvider, { model: cloudModel });
    saveProviderCredentials(localType, { url: localUrl, model: localModel });

    State.model = aiProvider;
    State.cloudProvider = cloudProvider;
    saveSettings();
    if (typeof AgentLog !== 'undefined') {
        AgentLog.info('settings', 'User settings saved', State.settings);
    }

    // Sync Brand into main input if empty
    const brandInput = document.getElementById('brandName');
    if (brandInput && !brandInput.value) brandInput.value = State.settings.brand;

    updateProviderHint();
    showToast('Settings & AI configuration saved successfully!', 'success');
});

/* ── Brand guardrails (applied to every generation) ───────────── */
function readGuardrailsFromUi() {
    const bannedRaw = document.getElementById('guardBannedWords')?.value || '';
    return {
        enabled: document.getElementById('guardEnabled') ? !!document.getElementById('guardEnabled').checked : true,
        bannedWords: bannedRaw.split(/[,\n]/).map(s => s.trim()).filter(Boolean),
        requiredDisclaimer: document.getElementById('guardDisclaimer')?.value.trim() || '',
        maxHashtags: parseInt(document.getElementById('guardMaxHashtags')?.value || '30', 10),
        allowEmoji: document.getElementById('guardAllowEmoji') ? !!document.getElementById('guardAllowEmoji').checked : true,
    };
}

function loadGuardrailsIntoUi() {
    const g = window.NexusAI?.config.guardrails || {};
    const map = {
        guardEnabled: g.enabled !== false,
        guardBannedWords: (g.bannedWords || []).join(', '),
        guardDisclaimer: g.requiredDisclaimer || '',
        guardMaxHashtags: Number.isFinite(g.maxHashtags) ? g.maxHashtags : 30,
        guardAllowEmoji: g.allowEmoji !== false,
    };
    const en = document.getElementById('guardEnabled'); if (en) en.checked = map.guardEnabled;
    const bw = document.getElementById('guardBannedWords'); if (bw) bw.value = map.guardBannedWords;
    const dl = document.getElementById('guardDisclaimer'); if (dl) dl.value = map.guardDisclaimer;
    const mh = document.getElementById('guardMaxHashtags'); if (mh) mh.value = map.guardMaxHashtags;
    const ae = document.getElementById('guardAllowEmoji'); if (ae) ae.checked = map.guardAllowEmoji;
}

function saveGuardrails() {
    const g = readGuardrailsFromUi();
    if (window.NexusAI) window.NexusAI.saveConfig({ guardrails: g });
    AgentLog.info('settings.guardrails', 'Brand guardrails updated', g);
    showToast('Brand guardrails saved — applied to every future generation', 'success');
}

function copyScheduleCmd() {
    const cmd = document.getElementById('scheduleCmd')?.textContent || '';
    copyToClipboard(cmd).then(ok => showToast(ok ? 'Schedule command copied!' : 'Copy failed', ok ? 'success' : 'error'));
}

/* ══════════════════════════════════════════════════════════════
   BROWSER AGENT SKILL (Playwright / Chrome DevTools Protocol)
   UI front-end for agent/browser_automation.py. Talks to a local
   relay (python3 agent/nexus_relay.py → http://localhost:8712) when
   present; otherwise shows the exact CLI command + simulated result
   so the workflow is learnable offline. Permission-gated actions.
══════════════════════════════════════════════════════════════ */
/* ══════════════════════════════════════════════════════════════
   LOCAL RELAY CLIENT
   The browser cannot execute Python, so all machine-side work
   (browser automation, publishing, queue) goes through
   `python3 agent/nexus_relay.py` on 127.0.0.1:8712.

   Security: the relay validates Origin AND (optionally) a shared
   token. The token is stored here and sent as X-Nexus-Token so a
   random website you have open cannot drive your machine.
═════════════════════════════════════════════════════════════ */
const DEFAULT_RELAY_URL = 'http://127.0.0.1:8712';

/**
 * Where the relay lives.
 *   1. An address saved in Settings (phone → desktop over LAN, or a hosted relay)
 *   2. The page's own origin when the PWA was loaded FROM the relay (/app/…)
 *   3. This computer (127.0.0.1:8712)
 */
function relayUrl() {
    const saved = (safeJson('nexus_credentials', {})['relay-url'] || '').trim();
    if (saved) return saved.replace(/\/+$/, '');
    if (typeof location !== 'undefined' && /^https?:$/.test(location.protocol || '') &&
        (location.pathname || '').startsWith('/app/')) {
        return location.origin;
    }
    return DEFAULT_RELAY_URL;
}

function relayToken() {
    return safeJson('nexus_credentials', {})['relay-token'] || '';
}

function saveRelayToken() {
    const token = document.getElementById('relayToken')?.value.trim() || '';
    const url = document.getElementById('relayUrl')?.value.trim() || '';
    const creds = safeJson('nexus_credentials', {});
    if (token) creds['relay-token'] = token; else delete creds['relay-token'];
    if (url && !/^https?:\/\/[^/]+/.test(url)) { showToast('Relay address must start with http:// or https://', 'error'); return; }
    if (url) creds['relay-url'] = url.replace(/\/+$/, ''); else delete creds['relay-url'];
    localStorage.setItem('nexus_credentials', JSON.stringify(creds));
    showToast(token ? 'Relay token saved' : 'Relay token cleared', 'success');
    checkRelayHealth();
}

/** Low-level relay call. Returns the parsed body plus the HTTP status. */
async function relayCall(path, { method = 'POST', body = null, ms = 30000 } = {}) {
    const headers = { 'Content-Type': 'application/json' };
    const token = relayToken();
    if (token) headers['X-Nexus-Token'] = token;
    const res = await fetchWithTimeout(`${relayUrl()}${path}`, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
    }, ms);
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    if (!res.ok) {
        const err = new Error(data?.error || data?.reason || `relay HTTP ${res.status}`);
        err.status = res.status;
        err.data = data;
        throw err;
    }
    return data;
}

/** Probe the relay and paint the status chip. Safe to call on every page load. */
async function checkRelayHealth() {
    const chip = document.getElementById('relayChip');
    try {
        const data = await relayCall('/health', { method: 'GET', ms: 2500 });
        if (chip) {
            chip.textContent = `🟢 Relay online · v${data.version || '?'} · queue ${data.queue ?? 0}`;
            chip.style.color = '#4ade80';
        }
        State.relayOnline = true;
        return data;
    } catch (e) {
        if (chip) {
            chip.textContent = e.status === 401
                ? '🔑 Relay online but the token is wrong — paste it in Settings'
                : '⚪ Relay offline — run: python3 agent/nexus_relay.py';
            chip.style.color = e.status === 401 ? '#f87171' : '';
        }
        State.relayOnline = false;
        return null;
    }
}

const BROWSER_DENY = [
    /delete[_ -]?(my[_ -])?account/i, /log\s*out/i, /sign\s*out/i,
    /remove[_ -]?payment/i, /cancel[_ -]?subscription/i, /\bclose[_ -]?account\b/i,
];

function browserPermissionGranted() {
    return !!document.getElementById('browserActionsPermission')?.checked;
}

function initBrowserPage() {
    const cb = document.getElementById('browserActionsPermission');
    const chip = document.getElementById('browserPermChip');
    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    if (cb) {
        cb.checked = !!saved['browser-actions-permission'];
        cb.onchange = () => {
            const c = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
            c['browser-actions-permission'] = cb.checked ? Date.now() : 0;
            localStorage.setItem('nexus_credentials', JSON.stringify(c));
            updateBrowserChip();
            AgentLog.info('browser.permission', cb.checked
                ? 'User GRANTED browser actions permission (click/type allowed)'
                : 'User REVOKED browser actions permission (back to read-only)');
            showToast(cb.checked ? '🔓 Browser actions unlocked' : '🔒 Read-only mode restored', 'success');
        };
    }
    updateBrowserChip();
}

function updateBrowserChip() {
    const chip = document.getElementById('browserPermChip');
    if (!chip) return;
    chip.textContent = browserPermissionGranted() ? '🔓 Actions allowed' : '🔒 Read-only mode';
    chip.style.color = browserPermissionGranted() ? '#4ade80' : '';
}

function browserOut(text) {
    const el = document.getElementById('browserOutput');
    if (el) el.textContent = text;
}

function clearBrowserOut() { browserOut('Cleared.'); }

function copyBrowserOut() {
    const txt = document.getElementById('browserOutput')?.textContent || '';
    copyToClipboard(txt).then(ok => showToast(ok ? 'Output copied' : 'Copy failed', ok ? 'success' : 'error'));
}

function updateBrowserPermChip() {
    const chip = document.getElementById('browserPermChip');
    const granted = document.getElementById('browserActionsPermission')?.checked;
    if (!chip) return;
    if (granted) {
        chip.textContent = '\u2705 Actions permitted';
        chip.style.background = 'rgba(74,222,128,.15)';
        chip.style.color = '#4ade80';
        showToast('Browser actions permission granted \u2014 use responsibly', 'success');
        AgentLog.info('browser.permission', 'User granted browser actions permission');
    } else {
        chip.textContent = '\uD83D\uDD12 Read-only';
        chip.style.background = 'rgba(239,68,68,.15)';
        chip.style.color = '#ef4444';
        AgentLog.warn('browser.permission', 'Browser actions permission revoked');
    }
}

function socialAgentBuildPayload(workflow) {
    const get = id => (document.getElementById(id)?.value || '').trim();
    switch (workflow) {
        case 'instagram_post':
            return { workflow, caption: get('sa-ig-caption'), image: get('sa-ig-image') };
        case 'facebook_post':
            return { workflow, message: get('sa-fb-message'), page_url: get('sa-fb-page'), image: get('sa-fb-image') };
        case 'blogger_post':
            return { workflow, title: get('sa-bl-title'), body: get('sa-bl-body'), labels: get('sa-bl-labels') };
        case 'canva_to_instagram':
            return { workflow, design_url: get('sa-cig-url'), caption: get('sa-cig-caption') };
        case 'canva_create':
            return { workflow, template: get('sa-cv-template') || 'instagram post', headline: get('sa-cv-headline') };
        case 'generic_post':
            return { workflow, url: get('sa-gp-url'), selector: get('sa-gp-selector') || 'textarea', text: get('sa-gp-text'), submit: get('sa-gp-submit') };
        default:
            return { workflow };
    }
}

function socialAgentCli(payload) {
    const base = 'python3 agent/social_browser_agent.py --allow-actions';
    const q = s => (s||'').replace(/"/g, "\'");
    switch (payload.workflow) {
        case 'instagram_post':
            return base + ' instagram_post --caption "' + q(payload.caption) + '"' + (payload.image ? ' --image "' + payload.image + '"' : '');
        case 'facebook_post':
            return base + ' facebook_post --message "' + q(payload.message) + '"' + (payload.page_url ? ' --page-url "' + payload.page_url + '"' : '') + (payload.image ? ' --image "' + payload.image + '"' : '');
        case 'blogger_post':
            return base + ' blogger_post --title "' + q(payload.title) + '" --body "' + q(payload.body) + '"' + (payload.labels ? ' --labels "' + payload.labels + '"' : '');
        case 'canva_to_instagram':
            return base + ' canva_to_instagram --design-url "' + payload.design_url + '" --caption "' + q(payload.caption) + '"';
        case 'canva_create':
            return base + ' canva_create --template "' + payload.template + '"' + (payload.headline ? ' --headline "' + payload.headline + '"' : '');
        case 'generic_post':
            return base + ' generic_post --url "' + payload.url + '" --selector "' + payload.selector + '" --text "' + q(payload.text) + '"';
        default:
            return base + ' status';
    }
}

async function socialAgentRun(workflow) {
    if (!document.getElementById('browserActionsPermission')?.checked) {
        browserOut('\uD83D\uDD10 PERMISSION REQUIRED\n\nGrant "Browser Actions Permission" at the top of this page first.\n\nThis workflow runs:\n' + socialAgentCli(socialAgentBuildPayload(workflow)));
        showToast('Grant Browser Actions Permission first', 'error');
        return;
    }
    const payload = socialAgentBuildPayload(workflow);
    const cliCmd = socialAgentCli(payload);
    browserOut('\u23F3 Running social browser workflow: ' + workflow + '\n\nSending to relay...\n\nCLI equivalent:\n' + cliCmd);
    AgentLog.info('social.agent', 'Dispatching ' + workflow, payload);

    try {
        const data = await relayCall('/social', { body: { ...payload, allowActions: true }, ms: 120000 });
        const out = data?.stdout || JSON.stringify(data, null, 2);
        let parsed = {};
        try { parsed = JSON.parse(out); } catch { parsed = {}; }
        browserOut('\u2705 ' + workflow + ' completed!\n\n' + String(out).slice(0, 6000));
        AgentLog.success('social.agent', workflow + ' completed', parsed);
        showToast(workflow.replace(/_/g,' ') + ' done \u2705', 'success');
        if (parsed.screenshot) {
            const shotDiv = document.getElementById('socialAgentScreenshot');
            const shotImg = document.getElementById('socialAgentScreenshotImg');
            if (shotDiv && shotImg) {
                shotImg.src = relayUrl() + '/shots/' + parsed.screenshot.split('/').pop() + '?t=' + Date.now();
                shotDiv.style.display = 'block';
            }
        }
        State.relayOnline = true;
    } catch (e) {
        if (e.status === 403) {
            browserOut('\u26D4 BLOCKED by safety policy:\n\n' + (e.data?.reason || e.message) + '\n\nCLI:\n' + cliCmd);
            AgentLog.warn('social.agent.blocked', workflow + ' blocked', e.data);
            showToast('Blocked by safety policy', 'error');
            return;
        }
        browserOut('\u2139\uFE0F Local relay not running (' + e.message + ').\n\nTo run ' + workflow + ':\n\nStep 1 - Start the relay:\n   python3 agent/nexus_relay.py\n\nStep 2 - Or run directly:\n   ' + cliCmd + '\n\nFirst-time login (opens real Chrome window):\n   ' + cliCmd.replace('--allow-actions', '--allow-actions --headed') + '\n\nSessions saved in logs/social_profile/ - log in only once per platform.\nAll actions audited in logs/browser_audit.jsonl.');
        AgentLog.info('social.agent.sim', workflow + ' relay offline', payload);
        State.relayOnline = false;
    }
}

function socialAgentLoginFirst(platform) {
    const platformUrls = { instagram: 'https://www.instagram.com/accounts/login/', facebook: 'https://www.facebook.com/login/', blogger: 'https://accounts.google.com/signin', canva: 'https://www.canva.com/login/' };
    const url = platformUrls[platform] || 'https://www.instagram.com/accounts/login/';
    browserOut('\uD83E\uDEAA First-time Login for ' + platform.charAt(0).toUpperCase() + platform.slice(1) + '\n\n1. Run in Terminal (opens Chrome window):\n   python3 agent/social_browser_agent.py --headed status\n\n2. Navigate to: ' + url + '\n\n3. Log in normally, then close browser.\n\n4. Session saved to logs/social_profile/ - automation uses it automatically.\n\nOnly needed once per platform (or when session expires).');
    AgentLog.info('social.agent', 'Login instructions shown for ' + platform);
}


function cliFor(cmd, url, sel, value) {
    const base = `python3 agent/browser_automation.py`;
    switch (cmd) {
        case 'navigate':          return `${base} navigate --url "${url}"`;
        case 'snapshot':          return `${base} snapshot`;
        case 'extract':           return `${base} extract --selector "${sel}"`;
        case 'screenshot':        return `${base} screenshot --out shots/page.png`;
        case 'devtools-console':  return `${base} devtools --what console`;
        case 'devtools-perf':     return `${base} devtools --what perf`;
        case 'act-click':         return `${base} act --allow-actions --action click --target "${sel}"`;
        case 'act-type':          return `${base} act --allow-actions --action type --target "${sel}" --value "${value}"`;
        default: return base;
    }
}

async function browserCmd(cmd) {
    const url = (document.getElementById('browserUrl')?.value || '').trim();
    const sel = (document.getElementById('browserSelector')?.value || '').trim();
    const value = (document.getElementById('browserTypeValue')?.value || '').trim();
    const isAction = cmd.startsWith('act-');
    const vf = document.getElementById('browserValueField');
    if (vf) vf.style.display = cmd === 'act-type' ? 'block' : 'none';

    // URL sanity + deny-list
    if (['navigate','screenshot'].includes(cmd) || isAction) {
        if (cmd !== 'screenshot' && !/^https?:\/\//i.test(url)) {
            browserOut(`❌ Enter a full URL starting with http(s)://`);
            AgentLog.warn('browser.cmd', `Rejected malformed URL for ${cmd}`, url);
            return;
        }
    }
    if (BROWSER_DENY.some(re => re.test(url) || re.test(sel))) {
        browserOut(`⛔ BLOCKED by safety deny-list: destructive target ("${sel || url}").\nThe agent refuses logout/delete-account/payment-removal operations even with permission.`);
        AgentLog.error('browser.safety', `Deny-list blocked: ${cmd}`, { url, sel });
        showToast('Browser action blocked by safety policy', 'error');
        return;
    }
    if (isAction && !browserPermissionGranted()) {
        browserOut(`🔐 PERMISSION REQUIRED\n\n"${cmd}" changes page state (click/type). Grant "Browser Actions Permission" at the top of this page first — it mirrors the --allow-actions flag in the CLI and the user_permission_granted gate in MCP.\n\nCLI equivalent:\n${cliFor(cmd, url, sel, value)}`);
        AgentLog.warn('browser.permission', `Blocked ${cmd}: user has not granted browser actions`, { url, sel });
        showToast('Grant Browser Actions Permission first', 'error');
        return;
    }
    if (['extract','act-click','act-type'].includes(cmd) && !sel) {
        browserOut(`❌ Fill in the CSS selector field for "${cmd}".`);
        return;
    }

    AgentLog.info('browser.cmd', `Dispatching browser command: ${cmd}`, { url, sel });
    browserOut(`⏳ ${cmd} … talking to the relay (${relayUrl()})`);

    // Real execution via the Python relay (agent/nexus_relay.py).
    try {
        const data = await relayCall('/browser', {
            body: { cmd, url, selector: sel, value, allowActions: isAction && browserPermissionGranted() },
            ms: 95000,
        });
        const stdout = data?.stdout || JSON.stringify(data, null, 2);
        browserOut(`✅ ${cmd} completed on the real browser\n\n${String(stdout).slice(0, 8000)}`);
        AgentLog.success('browser.cmd', `${cmd} executed on the real browser`, { url, sel });
        showToast(`Browser: ${cmd} done`, 'success');
        State.relayOnline = true;
        return;
    } catch (e) {
        // 403 = a deliberate policy refusal, not an offline relay. Surfacing the
        // refusal is the whole point; never hide it behind a simulated result.
        if (e.status === 403) {
            const reason = e.data?.reason || e.message;
            browserOut(`⛔ BLOCKED by the agent's permission policy:\n\n${reason}\n\nCLI equivalent:\n${cliFor(cmd, url, sel, value)}`);
            AgentLog.warn('browser.blocked', `Relay refused ${cmd} (policy block)`, { url, sel });
            showToast('Browser action blocked — permission required', 'error');
            return;
        }
        if (e.status === 401) {
            browserOut('🔑 The relay rejected the request token.\n\nPaste the token printed when you started `python3 agent/nexus_relay.py` into Settings → Relay token.');
            AgentLog.error('browser.auth', 'Relay token mismatch');
            return;
        }
        if (e.status === 500 && e.data) {
            browserOut(`❌ ${cmd} failed inside the browser skill:\n\n${JSON.stringify(e.data, null, 2).slice(0, 4000)}`);
            AgentLog.error('browser.cmd', `${cmd} failed: ${e.data.stderr || e.data.error || 'unknown'}`, e.data);
            showToast(`Browser: ${cmd} failed — see the output panel`, 'error');
            return;
        }
        // Offline: show the exact reproducible command + an explicitly labelled demo
        const sim = simulateBrowserResult(cmd, url, sel);
        browserOut(
`ℹ️ Local relay not running (${e.message}).

Start it in a terminal inside the project folder:
   pip install playwright && playwright install chromium
   python3 agent/nexus_relay.py        # stdlib HTTP → CLI bridge on 127.0.0.1:8712

Then this button runs:
$ ${cliFor(cmd, url, sel, value)}

── SIMULATED RESULT — NOT a real page (demo only) ──
${sim}

Real runs are appended to Agent Logs and logs/browser_audit.jsonl.`);
        AgentLog.info('browser.sim', `${cmd} simulated (relay offline)`, { url, sel });
        State.relayOnline = false;
    }
}

function simulateBrowserResult(cmd, url, sel) {
    switch (cmd) {
        case 'navigate': return `{
  "url": "${url}",
  "title": "Example Domain",
  "loadMs": 812,
  "engine": "playwright-chromium"
}`;
        case 'snapshot': return `h1 :: Example Domain
a#more-info :: More information…
p :: This domain is for use in illustrative examples…`;
        case 'extract':  return `{
  "selector": "${sel}",
  "texts": ["Example Domain", "This domain is for use in illustrative examples…"]
}`;
        case 'screenshot': return `{ "screenshot": "shots/nexus_page.png", "bytes": 84213 }`;
        case 'devtools-console': return `[{"type":"error","text":"Uncaught TypeError: Cannot read properties of undefined","ts":"…"}]  ← agent detected 1 JS error on the page`;
        case 'devtools-perf': return `{ "domContentLoaded": 412, "loadComplete": 812, "resourceCount": 17 }`;
        case 'act-click': return `{ "result": "click '${sel}' done — page title: Example Domain", "audit": "logs/browser_audit.jsonl" }`;
        case 'act-type':  return `{ "result": "type '${sel}' done", "charsTyped": ${(document.getElementById('browserTypeValue')?.value||'').length} }`;
        default: return '{}';
    }
}

/* ══════════════════════════════════════════════════════════════
   MCP & INTEGRATIONS PAGE
══════════════════════════════════════════════════════════════ */
const MCP_TOOLS_CATALOG = [
    ['generate_content',   'Platform-native marketing copy (tone/brand/platform aware)'],
    ['plan_campaign',      'Multi-day campaign incl. Meta Ads & Google Ads phases + budget split'],
    ['hashtag_research',   'Tiered hashtag sets (broad/mid/niche)'],
    ['browser_navigate',   'Open URL in agent browser — read-only'],
    ['browser_snapshot',   'Accessibility-style DOM snapshot'],
    ['browser_screenshot', 'Full-page screenshot to ./shots'],
    ['browser_act',        'Click/type — REQUIRES user_permission_granted=true'],
    ['devtools_inspect',   'Chrome DevTools: console errors / perf / network timings'],
    ['render_video',       'Poster images → MP4 (FFmpeg now; After Effects bridge next)'],
    ['telegram_notify',    'Push reports/messages to your Telegram'],
    ['read_agent_logs',    'Pull browser+agent audit log for debugging'],
    ['publish_post',       'Publish to a live platform (needs credentials)'],
    ['read_publish_queue', 'Queued, scheduled and completed publish jobs'],
];

function copyMcpCmd() {
    const txt = document.getElementById('mcpCmdBlock')?.textContent || '';
    copyToClipboard(txt).then(ok => showToast(
        ok ? 'MCP config copied — paste into Claude Desktop / Antigravity / LM Studio' : 'Copy blocked by the browser',
        ok ? 'success' : 'error'));
}

function showMcpTools() {
    const out = document.getElementById('mcpToolsOut');
    if (!out) return;
    out.classList.remove('hidden');
    out.textContent = 'nexus-social-manager v3.0 exposes ' + MCP_TOOLS_CATALOG.length + ' tools:\n\n' +
        MCP_TOOLS_CATALOG.map(([n, d]) => `• ${n.padEnd(20)} ${d}`).join('\n') +
        '\n\nVerify live: python3 agent/nexus_mcp.py --self-test';
    AgentLog.info('integrations.mcp', 'Tool catalog displayed', { count: MCP_TOOLS_CATALOG.length });
}

/* ── Telegram bot wiring ── */
function saveTelegram() {
    const token = document.getElementById('tgToken')?.value.trim();
    const chat = document.getElementById('tgChatId')?.value.trim();
    if (!token || !chat) { showToast('Token and Chat ID are both required', 'error'); return; }
    const c = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    c['telegram-token'] = token; c['telegram-chat-id'] = chat;
    localStorage.setItem('nexus_credentials', JSON.stringify(c));
    document.getElementById('tgStatus').innerHTML = '<span style="color:#4ade80">✔ Saved locally. Export as env vars before launching the bot.</span>';
    AgentLog.success('integrations.telegram', 'Telegram credentials saved (browser storage only)');
    showToast('Telegram credentials saved', 'success');
}

function tgCreds() {
    const c = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    return { token: c['telegram-token'] || '', chat: c['telegram-chat-id'] || '' };
}

async function callTelegramApi(method, params) {
    const { token } = tgCreds();
    if (!token) return { ok: false, error: 'No bot token saved — use Save credentials first.' };
    try {
        const res = await fetchWithTimeout(`https://api.telegram.org/bot${token}/${method}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params || {}),
        }, 15000);
        return await res.json();
    } catch (e) {
        return { ok: false, error: e.name === 'AbortError' ? 'Timeout' : e.message };
    }
}

async function testTelegram() {
    const out = document.getElementById('tgStatus');
    out.textContent = 'Testing getMe…';
    const r = await callTelegramApi('getMe', {});
    if (r.ok) {
        out.innerHTML = `<span style="color:#4ade80">✔ Bot online: @${r.result.username}</span>`;
        AgentLog.success('integrations.telegram', `Bot @${r.result.username} reachable`);
    } else {
        out.innerHTML = `<span style="color:#f87171">✘ ${escHtml(r.error || r.description || 'failed')}</span>`;
        AgentLog.error('integrations.telegram', `Connection test failed: ${r.error || r.description}`);
    }
}

async function sendTgTest() {
    const { chat } = tgCreds();
    if (!chat) { showToast('Save Chat ID first', 'error'); return; }
    const brand = State.settings.brand || 'Nexus';
    const r = await callTelegramApi('sendMessage', {
        chat_id: chat,
        text: `🤖 Nexus test — engine: ${State.model}, brand: ${brand}. If you can read this, control from phone works. Try /help on the bot.`,
    });
    if (r.ok) { showToast('Test message sent to Telegram ✔', 'success'); AgentLog.success('integrations.telegram', 'Test message delivered', { chat }); }
    else { showToast(`Send failed: ${r.error || r.description}`, 'error'); AgentLog.error('integrations.telegram', `Send failed: ${r.error || r.description}`, { chat }); }
}

/* ══════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
══════════════════════════════════════════════════════════════ */
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const icon = type === 'success' ? '✅' : type === 'error' ? '🚨' : 'ℹ️';
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${icon}</span> ${escHtml(message)}`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), type === 'error' ? 6000 : 3500);
}

/* Global safety net: any uncaught JS error or rejected promise is documented. */
window.addEventListener('error', (e) => {
    AgentLog.error('app.runtime', e.message || 'Uncaught script error',
        e.filename ? `${e.filename}:${e.lineno}:${e.colno}` : '');
});
window.addEventListener('unhandledrejection', (e) => {
    AgentLog.error('app.promise', 'Unhandled promise rejection', String(e.reason));
});

/* ══════════════════════════════════════════════════════════════
   SAVE DRAFT
══════════════════════════════════════════════════════════════ */
document.getElementById('saveDraftBtn')?.addEventListener('click', () => {
    const instruction = document.getElementById('agentInstruction').value;
    if (!instruction) { showToast('Nothing to save', 'info'); return; }
    const drafts = JSON.parse(localStorage.getItem('nexus_drafts') || '[]');
    drafts.push({ instruction, date: new Date().toISOString() });
    localStorage.setItem('nexus_drafts', JSON.stringify(drafts));
    showToast('Draft saved!', 'success');
});

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
(function init() {
    setGreeting();
    loadSettings();
    initDashboard();
    renderQuickTemplates();
    AgentLog.updateErrorBadge();

    // Relay health, Canva OAuth callback and guardrail state are all resolved
    // asynchronously and must never block first paint.
    // NOTE: only call things whose dependencies are already initialised here.
    // `applyUtmPreset()` must NOT be called from this IIFE: it reads the
    // UTM_PRESETS const declared further down the file, and touching it this
    // early threw a ReferenceError that aborted init() entirely. The UTM page
    // prefills itself via switchPage('utm') instead.
    initChrome();
    checkRelayHealth();
    handleCanvaCallback().catch(() => {});
    loadGuardrailsIntoUi();
    initCommandPalette();

    // Populate Brand & Settings fields from state
    if (document.getElementById('brandName')) {
        document.getElementById('brandName').value = State.settings.brand || 'My Brand';
    }
    if (document.getElementById('settingsBrand')) {
        document.getElementById('settingsBrand').value = State.settings.brand || '';
    }
    if (document.getElementById('settingsNiche')) {
        document.getElementById('settingsNiche').value = State.settings.niche || '';
    }
    if (document.getElementById('settingsAudience')) {
        document.getElementById('settingsAudience').value = State.settings.audience || '';
    }
    if (State.settings.tone) {
        const toneRadio = document.querySelector(`input[name="brandTone"][value="${State.settings.tone}"]`);
        if (toneRadio) toneRadio.checked = true;
        const toneSelect = document.getElementById('toneSelect');
        if (toneSelect) toneSelect.value = State.settings.tone;
    }

    // Populate AI harness configuration from NexusAI
    if (window.NexusAI && window.NexusAI.config) {
        const cfg = window.NexusAI.config;
        const aiRadio = document.querySelector(`input[name="aiModel"][value="${cfg.provider || 'local'}"]`);
        if (aiRadio) {
            aiRadio.checked = true;
            aiRadio.dispatchEvent(new Event('change'));
        }
        const modelSelect = document.getElementById('modelSelect');
        if (modelSelect) modelSelect.value = cfg.provider || 'local';

        if (document.getElementById('localHarnessType')) {
            document.getElementById('localHarnessType').value = cfg.localType || 'lmstudio';
        }
        if (document.getElementById('localModelName')) {
            document.getElementById('localModelName').value = cfg.localModel || 'hermes-3-llama-3.1-8b';
        }
        if (document.getElementById('localModelUrl')) {
            document.getElementById('localModelUrl').value = cfg.localUrl || 'http://localhost:1234/v1';
        }
        if (document.getElementById('cloudProvider')) {
            document.getElementById('cloudProvider').value = cfg.cloudProvider || 'gemini';
        }
        if (document.getElementById('cloudApiKey')) {
            document.getElementById('cloudApiKey').value = cfg.cloudApiKey || '';
        }
        if (document.getElementById('cloudModelInput')) {
            document.getElementById('cloudModelInput').value = cfg.cloudModel || '';
        }
    }

    // Ensure dashboard is default active
    switchPage('dashboard');
})();

/* ══════════════════════════════════════════════════════════════
   TREND RADAR
   HONESTY FIX: this used to render hard-coded, invented numbers
   ("1.2M mentions", "+45% this week") with no label, and toast
   "Trends updated successfully" — i.e. fabricated data presented
   as live. For a marketing tool that is a real liability: someone
   could put those figures in a client deck.

   Two modes now:
     1. Seed examples, clearly watermarked "EXAMPLE DATA" until a
        real source is loaded.
     2. Paste your own source (a platform export, Google Trends CSV,
        or notes) → the configured AI engine turns it into ranked,
        prioritised opportunities you can act on today.
═════════════════════════════════════════════════════════════ */
const TREND_SEED = {
    label: 'EXAMPLE DATA — not live metrics',
    topics: [
        { rank: 1, title: 'AI-assisted content workflows', meta: 'Evergreen demand — teach a specific workflow, not the tool' },
        { rank: 2, title: 'Short-form video SEO', meta: 'High intent — pair with a long-form YouTube version' },
        { rank: 3, title: 'Micro-communities over megaphones', meta: 'Strong B2B fit — newsletter + private group play' },
    ],
    audio: [
        { rank: 1, title: 'Calm lo-fi instrumental', meta: 'Best for authority/education reels' },
        { rank: 2, title: 'Fast percussive transition', meta: 'Best for listicle and before/after edits' },
        { rank: 3, title: 'Voiceover-first (no music bed)', meta: 'Best retention for tutorials over 30s' },
    ],
    hashtags: [
        { rank: 1, title: '#digitalmarketing', meta: 'Mega tier — reach, low intent' },
        { rank: 2, title: '#contentstrategy', meta: 'Large tier — balanced' },
        { rank: 3, title: '#marketingworkflow', meta: 'Niche tier — highest engagement rate' },
    ],
};

let trendSourceText = '';

function fetchTrends() {
    const btn = document.getElementById('refreshTrendsBtn');
    if (btn) btn.disabled = true;
    renderTrendWatermark(TREND_SEED.label);

    setTimeout(() => {
        if (btn) btn.disabled = false;
        renderTrendList('viralTopicsList', TREND_SEED.topics);
        renderTrendList('viralAudioList', TREND_SEED.audio);
        renderTrendList('viralHashtagsList', TREND_SEED.hashtags);
        showToast('Loaded the example trend set — paste your own source for real data', 'info');
    }, 400);
}

function renderTrendWatermark(text) {
    const el = document.getElementById('trendSourceNote');
    if (el) {
        el.textContent = text;
        el.style.color = text.startsWith('EXAMPLE') ? '#fbbf24' : '';
    }
}

function renderTrendList(id, data) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = data.map(item => `
        <div class="trend-item fade-up">
            <div class="trend-rank">#${escHtml(String(item.rank))}</div>
            <div class="trend-info">
                <div class="trend-title">${escHtml(item.title)}</div>
                <div class="trend-meta">${escHtml(item.meta)}</div>
            </div>
        </div>
    `).join('');
}

/**
 * Turn a pasted trend source into actionable, prioritised angles using the
 * configured AI engine. This is the genuinely valuable half of the page.
 */
async function analyzeTrends() {
    const input = document.getElementById('trendSourceInput');
    const out = document.getElementById('trendAnalysisOutput');
    const btn = document.getElementById('trendAnalyzeBtn');
    const text = (input?.value || '').trim();
    if (!text) { showToast('Paste your trend source first (CSV, export or notes).', 'info'); return; }
    if (!out) return;

    trendSourceText = text;
    const rows = parseTrendSource(text);
    renderTrendSourceSummary(rows);

    btn && (btn.disabled = true);
    out.innerHTML = '<div class="agent-feedback"><div class="spinner-ring"></div><span>Analysing opportunities…</span></div>';

    const instruction =
`You are given raw trend data for the brand "${State.settings.brand || 'the brand'}" (niche: ${State.settings.niche || 'unspecified'}, audience: ${State.settings.audience || 'unspecified'}).

RAW TREND SOURCE:
${text.slice(0, 6000)}

Task:
1. Rank the top 5 opportunities for THIS brand and audience. For each give: the trend, why it fits, the specific content angle to publish, the best platform and format, and a 0-100 confidence score.
2. Flag any trend that is NOT worth pursuing for this brand, and say why.
3. Give one 7-day posting sequence that rides the top opportunity.
Use a markdown table for the ranked opportunities. Do not invent metrics that are not in the source data — if a number is unavailable, write "n/a".`;

    try {
        const results = await window.NexusAI.generate({
            instruction,
            platforms: ['trend analysis'],
            tone: 'analytical',
            brand: State.settings.brand || 'My Brand',
            contentType: 'trend analysis',
            context: 'Output a ranked opportunity table, not a social post.',
            onLog: (level, source, message, detail) => (AgentLog[level] || AgentLog.info).call(AgentLog, source, message, detail),
        });
        const content = results['trend analysis'] || Object.values(results)[0] || '';
        out.innerHTML = `<div class="content-block">${renderContent(content)}</div>`;
        AgentLog.success('trends', 'Trend source analysed', { chars: text.length, rows: rows.length });
        showToast('Opportunity analysis ready', 'success');
    } catch (e) {
        out.innerHTML = `<div class="content-block error-block">Analysis failed: ${escHtml(e.message)}</div>`;
        AgentLog.error('trends', 'Trend analysis failed', e.message);
    } finally {
        btn && (btn.disabled = false);
    }
}

/** Best-effort CSV/TSV parser so pasted exports are actually understood. */
function parseTrendSource(text) {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const delim = lines[0]?.includes('\t') ? '\t' : ',';
    return lines.slice(0, 800).map(line => {
        const cells = line.split(delim).map(c => c.trim().replace(/^"|"$/g, ''));
        return { term: cells[0], volume: cells[1] || '', delta: cells.slice(2).join(' ') };
    }).filter(r => r.term);
}

function renderTrendSourceSummary(rows) {
    const el = document.getElementById('trendSourceSummary');
    if (!el) return;
    if (!rows.length) { el.textContent = ''; return; }
    const looksLikeHeader = /term|keyword|trend|search|hashtag|topic/i.test(rows[0].term);
    el.textContent = `Parsed ${rows.length} row(s)${looksLikeHeader ? ' (first row looks like a header)' : ''} — first: "${rows[0].term}"`;
}

function clearTrendSource() {
    const input = document.getElementById('trendSourceInput');
    const out = document.getElementById('trendAnalysisOutput');
    if (input) input.value = '';
    if (out) out.innerHTML = '<p class="helper-text">No analysis yet.</p>';
    renderTrendSourceSummary([]);
}

document.getElementById('refreshTrendsBtn')?.addEventListener('click', fetchTrends);

/* ══════════════════════════════════════════════════════════════
   FUNCTIONAL HUBS LOGIC
══════════════════════════════════════════════════════════════ */
function generateHubContent(platform, toolType) {
    let topicId = '';
    let outputId = '';
    let instruction = '';

    if (platform === 'instagram') {
        outputId = 'igHubOutput';
        if (toolType === 'carousel') {
            topicId = 'igCarouselTopic';
            instruction = `Create a 5-slide Instagram carousel script about: ${document.getElementById(topicId)?.value}`;
        } else if (toolType === 'reel') {
            topicId = 'igReelTopic';
            instruction = `Create a 60-second Instagram Reel script with visual cues about: ${document.getElementById(topicId)?.value}`;
        }
    } else if (platform === 'facebook') {
        outputId = 'fbHubOutput';
        if (toolType === 'group') {
            topicId = 'fbGroupTopic';
            instruction = `Write a highly engaging Facebook Group post designed to spark discussion about: ${document.getElementById(topicId)?.value}`;
        } else if (toolType === 'ad') {
            topicId = 'fbAdTopic';
            instruction = `Write high-converting Meta Ad copy (Primary text, Headline, Description) for: ${document.getElementById(topicId)?.value}`;
        }
    } else if (platform === 'youtube') {
        outputId = 'ytHubOutput';
        if (toolType === 'title') {
            topicId = 'ytTitleTopic';
            instruction = `Generate 5 highly clickable, SEO-optimized YouTube titles (A/B variants) for a video about: ${document.getElementById(topicId)?.value}`;
        } else if (toolType === 'desc') {
            topicId = 'ytDescTopic';
            instruction = `Write a YouTube video description including chapters/timestamps and relevant tags for a video about: ${document.getElementById(topicId)?.value}`;
        }
    } else if (platform === 'linkedin') {
        outputId = 'liHubOutput';
        if (toolType === 'thought') {
            topicId = 'liThoughtTopic';
            instruction = `Write a thought-leadership LinkedIn post (using proper spacing and hooks) about: ${document.getElementById(topicId)?.value}`;
        } else if (toolType === 'poll') {
            topicId = 'liPollTopic';
            instruction = `Create a professional LinkedIn poll (Question + 3-4 options + explanatory text) about: ${document.getElementById(topicId)?.value}`;
        }
    } else if (platform === 'blog') {
        outputId = 'blogHubOutput';
        if (toolType === 'outline') {
            topicId = 'blogTopic';
            const kw = document.getElementById('blogPrimaryKw')?.value || '';
            instruction = `Create a comprehensive SEO blog post outline (H1, H2s, H3s) for the keyword "${kw}". Premise: ${document.getElementById(topicId)?.value}`;
        }
    }

    const topicVal = document.getElementById(topicId)?.value.trim();
    if (!topicVal) {
        showToast('Please enter a topic first.', 'info');
        return;
    }

    const outputEl = document.getElementById(outputId);
    if (!outputEl) return;

    outputEl.innerHTML = '<div class="agent-feedback"><div class="spinner-ring"></div><span>AI Agent is crafting content...</span></div>';

    (async () => {
        try {
            const tone = State.settings.tone;
            const brand = State.settings.brand;

            const res = await window.NexusAI.generate({
                instruction,
                platforms: [platform],
                tone,
                brand,
                contentType: toolType,
                context: `Tool: ${toolType} for ${platform}`,
                onProgress: (msg) => {
                    outputEl.innerHTML = `<div class="agent-feedback"><div class="spinner-ring"></div><span>${msg}</span></div>`;
                }
            });

            const content = res[platform] || Object.values(res)[0] || '';
            outputEl.innerHTML = `<div class="content-block">${window.renderMarkdown ? window.renderMarkdown(content) : escHtml(content)}</div>`;
            showToast('Generated successfully!', 'success');
        } catch (err) {
            outputEl.innerHTML = `<div class="content-block error-block">❌ Error: ${err.message}</div>`;
            showToast(`Failed: ${err.message}`, 'error');
        }
    })();
}

/* ══════════════════════════════════════════════════════════════
   CONNECTED ACCOUNTS — CREDENTIAL MANAGEMENT
   All credentials stored in localStorage (client-side only)
══════════════════════════════════════════════════════════════ */

// Map of platform → field IDs
const CRED_FIELDS = {
    instagram: ['ig-app-id', 'ig-app-secret', 'ig-access-token', 'ig-account-id'],
    facebook:  ['fb-page-token', 'fb-page-id'],
    youtube:   ['yt-client-id', 'yt-client-secret', 'yt-refresh-token', 'yt-channel-id'],
    twitter:   ['tw-api-key', 'tw-api-secret', 'tw-access-token', 'tw-access-secret', 'tw-bearer-token'],
    linkedin:  ['li-client-id', 'li-client-secret', 'li-access-token', 'li-person-urn'],
    blogger:   ['bl-access-token', 'bl-blog-id'],
    canva:     ['canva-client-id', 'canva-client-secret', 'canva-access-token'],
};

function loadSavedCredentials() {
    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    Object.entries(CRED_FIELDS).forEach(([platform, fields]) => {
        fields.forEach(fieldId => {
            const el = document.getElementById(fieldId);
            if (el && saved[fieldId]) el.value = saved[fieldId];
        });
    });
    // Update status indicators for platforms that have tokens saved
    Object.keys(CRED_FIELDS).forEach(platform => checkSavedStatus(platform));
}

function checkSavedStatus(platform) {
    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    const tokenField = {
        instagram: 'ig-access-token',
        facebook:  'fb-page-token',
        youtube:   'yt-refresh-token',
        twitter:   'tw-bearer-token',
        linkedin:  'li-access-token',
        blogger:   'bl-access-token',
        canva:     'canva-access-token',
    }[platform];

    if (!tokenField) return;
    const statusEl = document.getElementById(`status-${platform}`);
    if (!statusEl) return;

    if (saved[tokenField]) {
        statusEl.className = 'pac-status connected';
        statusEl.innerHTML = `<span class="status-dot-sm"></span> Connected`;
    } else {
        statusEl.className = 'pac-status';
        statusEl.innerHTML = `<span class="status-dot-sm"></span> Not Connected`;
    }
}

function saveAllCredentials() {
    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    let count = 0;
    Object.values(CRED_FIELDS).flat().forEach(fieldId => {
        const el = document.getElementById(fieldId);
        if (el && el.value.trim()) {
            saved[fieldId] = el.value.trim();
            count++;
        }
    });
    localStorage.setItem('nexus_credentials', JSON.stringify(saved));
    Object.keys(CRED_FIELDS).forEach(p => checkSavedStatus(p));
    showToast(`✅ Saved ${count} credential fields securely in browser.`, 'success');
}

function testConnection(platform) {
    const statusEl = document.getElementById(`status-${platform}`);
    if (!statusEl) return;

    statusEl.className = 'pac-status log-info';
    statusEl.innerHTML = `<span class="status-dot-sm"></span> Testing credentials...`;

    setTimeout(() => {
        const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
        const tokenField = {
            instagram: 'ig-access-token',
            facebook:  'fb-page-token',
            youtube:   'yt-refresh-token',
            twitter:   'tw-bearer-token',
            linkedin:  'li-access-token',
            blogger:   'bl-access-token',
            canva:     'canva-access-token',
        }[platform];

        const token = (saved[tokenField] || document.getElementById(tokenField)?.value || '').trim();

        if (token && token.length > 10) {
            statusEl.className = 'pac-status connected';
            statusEl.innerHTML = `<span class="status-dot-sm"></span> Ready ✓`;
            showToast(`${platform.charAt(0).toUpperCase() + platform.slice(1)}: Token configured and ready for automation.`, 'success');
        } else if (token) {
            statusEl.className = 'pac-status log-info';
            statusEl.innerHTML = `<span class="status-dot-sm"></span> Token format warning`;
            showToast(`${platform}: Token seems unusually short. Verify your OAuth/API token.`, 'info');
        } else {
            statusEl.className = 'pac-status error';
            statusEl.innerHTML = `<span class="status-dot-sm"></span> Missing credentials`;
            showToast(`${platform}: No access token found. Please enter and save credentials.`, 'info');
        }
    }, 600);
}

function testAllConnections() {
    Object.keys(CRED_FIELDS).forEach((platform, i) => {
        setTimeout(() => testConnection(platform), i * 200);
    });
}

/* ── Publish Pipeline ─────────────────────────────────────────── */

/** Turn the rich 6-tier editor copy into a clean publishable body. */
function stripMarkdown(text) {
    return String(text || '')
        .replace(/```[\s\S]*?```/g, m => m.replace(/```[a-z]*\n?/gi, ''))
        .replace(/^#{1,6}\s+/gm, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/\*([^*\n]+)\*/g, '$1')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/^>\s?/gm, '')
        .trim();
}

/** Platform rules that produce silent API failures if ignored. */
const PLATFORM_LIMITS = {
    twitter: { chars: 280, label: 'X / Twitter' },
    instagram: { chars: 2200, label: 'Instagram', requiresMedia: true },
    facebook: { chars: 63206, label: 'Facebook' },
    linkedin: { chars: 3000, label: 'LinkedIn' },
    blogger: { chars: 100000, label: 'Blogger' },
};

function previewAutoPost() {
    const content = document.getElementById('autoPostContent')?.value || '';
    const platforms = [...document.querySelectorAll('input[name="autoPlat"]:checked')].map(cb => cb.value);
    const media = document.getElementById('autoPostMedia')?.value?.trim() || '';
    const schedule = document.getElementById('autoPostSchedule')?.value || '';
    const log = document.getElementById('publishLog');
    if (!log) return;

    if (!content) { showToast('Please write some content first.', 'info'); return; }
    if (!platforms.length) { showToast('Select at least one platform.', 'info'); return; }

    const clean = stripMarkdown(content);
    const urlMatch = clean.match(/https?:\/\/\S+/);
    const rows = platforms.map(p => {
        const rule = PLATFORM_LIMITS[p] || { chars: 5000, label: p };
        const over = clean.length > rule.chars;
        const problems = [];
        if (over) problems.push(`${clean.length - rule.chars} chars over the ${rule.chars} limit`);
        if (rule.requiresMedia && !media) problems.push('needs a public image URL (Graph API requirement)');
        if (p === 'linkedin' && urlMatch && !/link in (the )?first comment/i.test(clean)) {
            problems.push('external link in the post body — LinkedIn suppresses reach; move it to the first comment');
        }
        return { p, rule, problems };
    });

    const blocked = rows.filter(r => r.problems.length);
    log.innerHTML = `
        <div class="log-entry log-info"><i class="ph ph-eye"></i> <strong>Pre-flight check</strong> — ${platforms.length} platform(s) · ${clean.length} characters after markdown cleanup</div>
        ${rows.map(r => r.problems.length
            ? `<div class="log-entry log-error"><i class="ph ph-warning"></i> <strong>${escHtml(r.rule.label)}:</strong> ${escHtml(r.problems.join('; '))}</div>`
            : `<div class="log-entry log-success"><i class="ph ph-check-circle"></i> <strong>${escHtml(r.rule.label)}:</strong> ready</div>`
        ).join('')}
        ${media ? `<div class="log-entry log-pending"><i class="ph ph-image"></i> Media: ${escHtml(media)}</div>` : '<div class="log-entry log-pending"><i class="ph ph-image"></i> Media: text-only post</div>'}
        ${schedule ? `<div class="log-entry log-pending"><i class="ph ph-clock"></i> Scheduled for ${escHtml(new Date(schedule).toLocaleString())}</div>` : '<div class="log-entry log-pending"><i class="ph ph-clock"></i> Publishes immediately</div>'}
        <div class="log-entry ${blocked.length ? 'log-error' : 'log-success'}">
            <i class="ph ${blocked.length ? 'ph-x-circle' : 'ph-check-circle'}"></i>
            ${blocked.length ? 'Fix the flagged platforms before publishing — the APIs reject them.' : 'All platforms pass. Safe to publish.'}
        </div>`;
}

function publishCredSlice(saved) {
    const keys = [
        'ig-access-token', 'ig-account-id', 'fb-page-token', 'fb-page-id',
        'tw-bearer-token', 'li-access-token', 'li-person-urn',
        'bl-access-token', 'bl-blog-id',
    ];
    const out = {};
    keys.forEach(k => { if (saved[k]) out[k] = saved[k]; });
    return out;
}

function appendPublishLog(html) {
    const log = document.getElementById('publishLog');
    if (log) log.innerHTML += html;
}

function persistPublishQueue(job) {
    const q = safeJson('nexus_publish_queue', []);
    q.push(job);
    localStorage.setItem('nexus_publish_queue', JSON.stringify(q.slice(-200)));
}

/**
 * Queue a job. The relay owns the persistent queue (logs/publish_queue.json)
 * so scheduled posts survive a browser restart; the browser copy is a mirror
 * for the dashboard KPIs.
 */
async function queuePublishJob(job) {
    const data = await relayCall('/publish', { body: job, ms: 120000 });
    job.status = data?.status || 'published';
    persistPublishQueue(job);
    return data;
}

async function publishNow(dryRun = false) {
    const content = document.getElementById('autoPostContent')?.value;
    const platforms = [...document.querySelectorAll('input[name="autoPlat"]:checked')].map(cb => cb.value);
    const media = document.getElementById('autoPostMedia')?.value.trim() || '';
    const schedule = document.getElementById('autoPostSchedule')?.value || '';

    if (!content) { showToast('Please write some post content first.', 'info'); return; }
    if (!platforms.length) { showToast('Select at least one platform.', 'info'); return; }

    const log = document.getElementById('publishLog');
    const saved = safeJson('nexus_credentials', {});
    const job = {
        id: `job-${Date.now()}`,
        content,
        platforms,
        media,
        schedule: schedule ? new Date(schedule).toISOString() : null,
        status: schedule ? 'scheduled' : 'queued',
        dry_run: dryRun,
        created_at: new Date().toISOString(),
        credentials: publishCredSlice(saved),
    };

    if (log) {
        log.innerHTML = `<div class="log-entry log-info"><i class="ph ph-spinner" style="animation:spin .7s linear infinite"></i> ${dryRun ? 'Validation run' : 'Dispatching'} → local publisher (agent/publish.py via relay)…</div>`;
    }

    if (platforms.includes('instagram') && !media) {
        appendPublishLog('<div class="log-entry log-error"><i class="ph ph-instagram-logo"></i> Instagram Graph API needs a public Media URL — this platform will fail while the others continue.</div>');
    }

    try {
        const data = await queuePublishJob(job);
        const results = data?.results || data?.jobs || [data];
        (Array.isArray(results) ? results : [results]).forEach(r => {
            const ok = r.ok || r.status === 'published' || r.dry_run;
            const plat = r.platform || (r.platforms || []).join(', ');
            const detail = ok
                ? (r.dry_run ? 'Validated (dry run) — no API call made' : `Posted${r.id ? ` id=${r.id}` : ''}`)
                : (typeof r.error === 'string' ? r.error : JSON.stringify(r.error || r));
            appendPublishLog(`<div class="log-entry ${ok ? 'log-success' : 'log-error'}"><i class="ph ph-paper-plane-tilt"></i> <strong>${escHtml(String(plat))}:</strong> ${escHtml(detail)}</div>`);
        });
        if (schedule) {
            appendPublishLog(`<div class="log-entry log-pending"><i class="ph ph-clock-clockwise"></i> Scheduled job <code>${escHtml(job.id)}</code> is stored in logs/publish_queue.json. Publish it when due with: <code>python3 agent/publish.py --due</code></div>`);
        }
        AgentLog.success('publish', `Publish job ${job.id} → ${data?.status || 'done'}`);
        showToast(schedule ? 'Post scheduled on the local queue.' : 'Publish job completed.', 'success');
    } catch (err) {
        persistPublishQueue(job);
        const why = err.status === 401
            ? 'the relay token is wrong (Settings → Relay token)'
            : err.status
                ? `the relay answered HTTP ${err.status}`
                : 'the local relay is not reachable';
        appendPublishLog(`<div class="log-entry log-error"><i class="ph ph-warning"></i> Not sent — ${escHtml(why)}. The job is saved in the browser queue.</div>
            <div class="log-entry log-pending">Start the relay, then retry:<br><code>python3 agent/nexus_relay.py</code></div>`);
        AgentLog.error('publish', `Publish failed: ${why}`, err.message);
        showToast('Queued locally — start the Nexus relay to post to live APIs.', 'info');
    }
    refreshQueueView();
}

/** Dry-run: validates every credential + payload WITHOUT posting publicly. */
function validatePublish() {
    publishNow(true);
}

/* ── Queue viewer (real state from the relay) ─────────────────── */
async function refreshQueueView() {
    const host = document.getElementById('queueList');
    if (!host) return;
    host.innerHTML = '<p class="helper-text">Loading queue…</p>';
    try {
        const data = await relayCall('/queue', { method: 'GET', ms: 6000 });
        const jobs = data?.jobs || [];
        const counts = data?.counts || {};
        const summary = document.getElementById('queueSummary');
        if (summary) {
            summary.textContent = `${jobs.length} job(s) · ${counts.published || 0} published · ${counts.failed || 0} failed · ${counts.scheduled || 0} scheduled · ${counts.queued || 0} queued`;
        }
        if (!jobs.length) {
            host.innerHTML = '<p class="helper-text">The local queue is empty. Publish or schedule something above.</p>';
            return;
        }
        host.innerHTML = jobs.slice().reverse().map(j => {
            const st = String(j.status || 'unknown');
            const cls = st === 'published' ? 'log-success' : st === 'failed' ? 'log-error' : 'log-pending';
            const results = (j.results || []).map(r =>
                `${r.platform}: ${r.ok ? '✔' : '✘ ' + escHtml(String(r.error || 'failed').slice(0, 80))}`).join(' · ');
            return `<div class="log-entry ${cls}">
                <i class="ph ph-package"></i>
                <strong>${escHtml(String(j.id || '?'))}</strong>
                <span class="chip">${escHtml(st)}</span>
                <div class="queue-meta">${escHtml(String((j.content || '').slice(0, 110)))}${(j.content || '').length > 110 ? '…' : ''}</div>
                <div class="queue-meta">${escHtml((j.platforms || []).join(', '))}${j.schedule ? ` · scheduled ${escHtml(new Date(j.schedule).toLocaleString())}` : ''}${results ? ` · ${results}` : ''}</div>
            </div>`;
        }).join('');
    } catch (e) {
        const summary = document.getElementById('queueSummary');
        if (summary) summary.textContent = '';
        host.innerHTML = `<p class="helper-text">Queue unavailable — ${escHtml(e.status === 401 ? 'relay token rejected (Settings → Relay token).' : 'start the relay: <code>python3 agent/nexus_relay.py</code>')}</p>`;
    }
}

async function publishDueJobs() {
    try {
        const data = await relayCall('/publish-due', { body: {}, ms: 180000 });
        showToast(`Processed ${data?.processed ?? 0} due job(s)`, 'success');
        AgentLog.info('publish', `--due run processed ${data?.processed ?? 0} job(s)`);
    } catch (e) {
        showToast(`Could not process the queue: ${e.message}`, 'error');
        AgentLog.error('publish', 'Queue processing failed', e.message);
    }
    refreshQueueView();
}

function sendOutputToPublisher() {
    const text = State.outputTabs[State.activeOutputTab] || Object.values(State.outputTabs || {})[0];
    if (!text) { showToast('Generate content first.', 'info'); return; }
    switchPage('accounts');
    const ta = document.getElementById('autoPostContent');
    if (ta) ta.value = stripMarkdown(text).slice(0, 4000);
    previewAutoPost();
    showToast('Copied into the publisher. Review the pre-flight check, pick platforms, then Publish.', 'success');
}

function addOutputToCalendar() {
    const text = State.outputTabs[State.activeOutputTab] || '';
    if (!text) { showToast('Generate content first.', 'info'); return; }
    const title = (document.getElementById('agentInstruction')?.value || State.activeOutputTab || 'Generated post').slice(0, 80);
    const today = new Date();
    const date = today.toISOString().slice(0, 10);
    State.calendarEvents.push({
        id: `ev-${Date.now()}`,
        title,
        date,
        time: '09:00',
        platform: canonicalPlatform(State.activeOutputTab || 'instagram'),
        status: 'draft',
        body: text,
    });
    localStorage.setItem('nexus_calendar_events', JSON.stringify(State.calendarEvents));
    AgentLog.info('calendar', `Draft added from Content Studio: "${title}"`);
    showToast('Draft added to the content calendar.', 'success');
    switchPage('calendar');
}

/** Normalise ui labels ("Twitter/X", "instagram") to the internal key. */
function canonicalPlatform(value) {
    const v = String(value || '').toLowerCase();
    if (v.includes('twitter') || v === 'x') return 'twitter';
    if (v.includes('ig') || v.includes('insta')) return 'instagram';
    if (v.includes('fb') || v.includes('facebook')) return 'facebook';
    if (v.includes('yt') || v.includes('youtube')) return 'youtube';
    if (v.includes('li') || v.includes('linkedin')) return 'linkedin';
    if (v.includes('blog')) return 'blog';
    if (v.includes('tik')) return 'tiktok';
    if (v.includes('mail')) return 'email';
    return v || 'instagram';
}

/* ══════════════════════════════════════════════════════════════
   UTM CAMPAIGN LINK BUILDER
   Every CTA that leaves the app should be attributable. Marketers
   lose weeks of data to untagged links; this makes tagging the
   default rather than an afterthought.
═════════════════════════════════════════════════════════════ */
const UTM_PRESETS = {
    instagram: { source: 'instagram', medium: 'social' },
    facebook: { source: 'facebook', medium: 'social' },
    linkedin: { source: 'linkedin', medium: 'social' },
    twitter: { source: 'twitter', medium: 'social' },
    tiktok: { source: 'tiktok', medium: 'social' },
    youtube: { source: 'youtube', medium: 'video' },
    email: { source: 'newsletter', medium: 'email' },
    blog: { source: 'blog', medium: 'organic' },
    ads: { source: 'meta', medium: 'paid_social' },
    googleads: { source: 'google', medium: 'cpc' },
};

function utmSlug(value) {
    return String(value || '').trim().toLowerCase()
        .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

function applyUtmPreset() {
    const preset = UTM_PRESETS[document.getElementById('utmPreset')?.value] || UTM_PRESETS.instagram;
    const s = document.getElementById('utmSource');
    const m = document.getElementById('utmMedium');
    if (s && !s.value) s.value = preset.source;
    if (m && !m.value) m.value = preset.medium;
    buildUtm();
}

function buildUtm() {
    const out = document.getElementById('utmOutput');
    if (!out) return;

    const base = (document.getElementById('utmBase')?.value || '').trim();
    const source = utmSlug(document.getElementById('utmSource')?.value);
    const medium = utmSlug(document.getElementById('utmMedium')?.value);
    const campaign = utmSlug(document.getElementById('utmCampaign')?.value);
    const term = utmSlug(document.getElementById('utmTerm')?.value);
    const content = utmSlug(document.getElementById('utmContent')?.value);

    const problems = [];
    if (!base) problems.push('Add the destination URL.');
    else if (!/^https?:\/\//i.test(base)) problems.push('The destination URL must start with http:// or https://');
    if (!source) problems.push('utm_source is required — it is what separates channels in GA4.');
    if (!medium) problems.push('utm_medium is required (social, email, cpc, organic…).');
    if (!campaign) problems.push('utm_campaign is required for campaign-level reporting.');

    if (problems.length) {
        out.textContent = `Fix first:\n• ${problems.join('\n• ')}`;
        out.style.color = '#fbbf24';
        return;
    }

    let url;
    try {
        url = new URL(base);
    } catch {
        out.textContent = 'That is not a valid URL.';
        out.style.color = '#f87171';
        return;
    }

    // Preserve any existing query params (e.g. an existing ?ref=).
    url.searchParams.set('utm_source', source);
    url.searchParams.set('utm_medium', medium);
    url.searchParams.set('utm_campaign', campaign);
    if (term) url.searchParams.set('utm_term', term);
    if (content) url.searchParams.set('utm_content', content);

    out.textContent = url.toString();
    out.style.color = '';
    return url.toString();
}

function copyUtm() {
    const url = buildUtm();
    if (typeof url !== 'string') { showToast('Complete the required fields first.', 'info'); return; }
    copyToClipboard(url).then(ok => showToast(ok ? 'Tracked link copied' : 'Copy blocked by the browser', ok ? 'success' : 'error'));
}

/** Register the tracked link against the currently selected output. */
function buildUtmForActiveOutput() {
    const platform = canonicalPlatform(State.activeOutputTab || 'instagram');
    switchPage('utm');
    const preset = document.getElementById('utmPreset');
    if (preset) preset.value = UTM_PRESETS[platform] ? platform : 'instagram';
    const s = document.getElementById('utmSource');
    const m = document.getElementById('utmMedium');
    const p = UTM_PRESETS[preset?.value] || UTM_PRESETS.instagram;
    if (s) s.value = p.source;
    if (m) m.value = p.medium;
    const camp = document.getElementById('utmCampaign');
    if (camp && !camp.value) {
        camp.value = utmSlug((document.getElementById('agentInstruction')?.value || 'campaign').slice(0, 40));
    }
    buildUtm();
    showToast('Pre-filled for this platform — set the destination URL, then copy.', 'info');
}

/* ══════════════════════════════════════════════════════════════
   CONTENT REPURPOSING
   One strong idea should become six platform-native assets. This
   runs the full engine so each variant is genuinely re-written for
   its platform instead of being truncated.
═════════════════════════════════════════════════════════════ */
async function repurposeActiveOutput() {
    const source = State.outputTabs[State.activeOutputTab] || '';
    if (!source) { showToast('Generate content first.', 'info'); return; }

    const targets = ['instagram', 'linkedin', 'twitter', 'facebook'];
    const chosen = targets.filter(t => t !== canonicalPlatform(State.activeOutputTab));
    if (!chosen.length) { showToast('Nothing to repurpose into.', 'info'); return; }

    const feedback = document.getElementById('agentFeedback');
    const feedbackText = document.getElementById('agentFeedbackText');
    if (feedback) feedback.classList.remove('hidden');
    if (feedbackText) feedbackText.textContent = `Repurposing into ${chosen.join(', ')}…`;
    AgentLog.info('agent.repurpose', `Repurposing ${State.activeOutputTab} → ${chosen.join(', ')}`);

    try {
        const results = await window.NexusAI.generate({
            instruction: `Repurpose the following ${State.activeOutputTab} asset for other platforms. Keep the same core message and CTA, but re-write it natively for each target platform — do not simply truncate it.\n\nSOURCE ASSET:\n${source.slice(0, 4000)}`,
            platforms: chosen,
            tone: State.settings.tone || 'professional',
            brand: State.settings.brand,
            contentType: 'repurpose',
            context: 'Each output must stand alone and be publish-ready.',
            onProgress: (m) => { if (feedbackText) feedbackText.textContent = m; },
            onLog: (level, source2, message, detail) => (AgentLog[level] || AgentLog.info).call(AgentLog, source2, message, detail),
        });
        Object.assign(State.outputTabs, results);
        renderOutputTabs(State.outputTabs);
        const empty = document.getElementById('outputEmpty');
        const tabs = document.getElementById('outputTabs');
        if (empty) empty.classList.add('hidden');
        if (tabs) tabs.classList.remove('hidden');
        showToast(`Repurposed into ${Object.keys(results).length} extra platform(s)`, 'success');
    } catch (e) {
        AgentLog.error('agent.repurpose', 'Repurposing failed', e.message);
        showToast(`Repurposing failed: ${e.message}`, 'error');
    } finally {
        if (feedback) feedback.classList.add('hidden');
    }
}

/* ══════════════════════════════════════════════════════════════
   PERFORMANCE ANALYTICS — REAL MATH
   Paste a native platform export (Instagram/Facebook Insights, X
   analytics, LinkedIn, GA4 or an ad-manager CSV) and Nexus computes
   the metrics that actually drive decisions: engagement rate, CTR,
   CPC, CPM, CPA and ROAS — per platform and blended.

   No invented numbers: if a column is missing, the metric is left
   blank rather than guessed.
═════════════════════════════════════════════════════════════ */
const METRIC_ALIASES = {
    date: ['date', 'day', 'reporting starts', 'period'],
    platform: ['platform', 'channel', 'network', 'source'],
    campaign: ['campaign', 'campaign name', 'ad set', 'adset'],
    impressions: ['impressions', 'impr', 'impressions total'],
    reach: ['reach', 'accounts reached', 'unique reach'],
    likes: ['likes', 'reactions', 'post reactions'],
    comments: ['comments', 'replies'],
    shares: ['shares', 'reposts', 'retweets', 'shares/retweets'],
    saves: ['saves', 'bookmarks', 'saved'],
    clicks: ['clicks', 'link clicks', 'clicks (all)', 'outbound clicks'],
    conversions: ['conversions', 'results', 'purchases', 'leads', 'signups'],
    spend: ['spend', 'amount spent', 'cost', 'ad spend'],
    revenue: ['revenue', 'purchase value', 'conversion value', 'sales'],
    video_views: ['video views', 'views', 'plays', '3-second video views'],
};

function parseDelimited(text) {
    const lines = String(text || '').split(/\r?\n/).filter(l => l.trim());
    if (!lines.length) return { header: [], rows: [] };
    const delim = lines[0].includes('\t') ? '\t' : lines[0].split(';').length > lines[0].split(',').length ? ';' : ',';
    // Minimal RFC4180-ish splitter that respects quoted fields.
    const splitRow = (line) => {
        const cells = [];
        let cur = '', quoted = false;
        for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (quoted) {
                if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
                else if (ch === '"') quoted = false;
                else cur += ch;
            } else if (ch === '"') quoted = true;
            else if (ch === delim) { cells.push(cur); cur = ''; }
            else cur += ch;
        }
        cells.push(cur);
        return cells.map(c => c.trim());
    };
    const header = splitRow(lines[0]).map(h => h.toLowerCase());
    const rows = lines.slice(1).map(splitRow).filter(r => r.some(c => c !== ''));
    return { header, rows };
}

function numberOrNull(value) {
    if (value === undefined || value === null) return null;
    const cleaned = String(value).replace(/[^0-9.\-]/g, '');
    if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
}

/** Map a CSV header row onto our canonical metric names. */
function mapHeader(header) {
    const map = {};
    header.forEach((h, i) => {
        const clean = h.replace(/[_\-]+/g, ' ').replace(/\s+/g, ' ').trim();
        for (const [metric, aliases] of Object.entries(METRIC_ALIASES)) {
            if (map[metric] !== undefined) continue;
            if (aliases.some(a => clean === a || clean.startsWith(a))) { map[metric] = i; break; }
        }
    });
    return map;
}

function analyzePerformance() {
    const input = document.getElementById('analyticsCsv')?.value || '';
    const host = document.getElementById('analyticsTable');
    const insightsHost = document.getElementById('analyticsInsights');
    const summaryHost = document.getElementById('analyticsSummaryLine');
    if (!host) return;

    if (!input.trim()) { showToast('Paste a platform export first.', 'info'); return; }

    const { header, rows } = parseDelimited(input);
    if (header.length < 2) { showToast('That does not look like a CSV with a header row.', 'error'); return; }

    const idx = mapHeader(header);
    if (idx.impressions === undefined && idx.clicks === undefined && idx.spend === undefined) {
        host.innerHTML = `<p class="helper-text">Could not find an impressions, clicks or spend column. Detected headers: ${escHtml(header.join(', '))}</p>`;
        return;
    }

    // Group by platform (fall back to a single blended bucket).
    const groups = new Map();
    rows.forEach(r => {
        const key = idx.platform !== undefined && r[idx.platform] ? r[idx.platform] : 'all channels';
        if (!groups.has(key)) groups.set(key, {});
        const g = groups.get(key);
        ['impressions', 'reach', 'likes', 'comments', 'shares', 'saves', 'clicks', 'conversions', 'spend', 'revenue', 'video_views'].forEach(m => {
            if (idx[m] === undefined) return;
            const v = numberOrNull(r[idx[m]]);
            if (v === null) return;
            g[m] = (g[m] || 0) + v;
        });
    });

    const computed = [...groups.entries()].map(([name, g]) => {
        const engagement = (g.likes || 0) + (g.comments || 0) + (g.shares || 0) + (g.saves || 0);
        const impressions = g.impressions ?? null;
        const reach = g.reach ?? null;
        // Instagram/Meta define ER against reach when available, otherwise impressions.
        const erBasis = reach || impressions;
        return {
            platform: name,
            impressions, reach,
            engagement: (g.likes || g.comments || g.shares || g.saves) ? engagement : null,
            engagementRate: erBasis ? (engagement / erBasis) * 100 : null,
            clicks: g.clicks ?? null,
            ctr: impressions ? ((g.clicks || 0) / impressions) * 100 : null,
            spend: g.spend ?? null,
            cpc: g.spend && g.clicks ? g.spend / g.clicks : null,
            cpm: g.spend && impressions ? (g.spend / impressions) * 1000 : null,
            conversions: g.conversions ?? null,
            cpa: g.spend && g.conversions ? g.spend / g.conversions : null,
            revenue: g.revenue ?? null,
            roas: g.spend && g.revenue ? g.revenue / g.spend : null,
        };
    }).sort((a, b) => (b.impressions || 0) - (a.impressions || 0));

    const fmt = (v, unit = '', digits = 2) => (v === null || v === undefined || !Number.isFinite(v))
        ? '<span class="metric-na">n/a</span>'
        : unit === '$' ? `$${v.toFixed(digits)}` : unit === '%' ? `${v.toFixed(digits)}%` : v.toLocaleString();

    host.innerHTML = `
        <table class="metrics-table">
            <thead><tr>
                <th>Channel</th><th>Impressions</th><th>Reach</th><th>Engagement</th><th>ER</th>
                <th>Clicks</th><th>CTR</th><th>Spend</th><th>CPC</th><th>CPM</th>
                <th>Conv.</th><th>CPA</th><th>ROAS</th>
            </tr></thead>
            <tbody>
                ${computed.map(c => `<tr>
                    <td><strong>${escHtml(c.platform)}</strong></td>
                    <td>${fmt(c.impressions)}</td>
                    <td>${fmt(c.reach)}</td>
                    <td>${fmt(c.engagement)}</td>
                    <td>${fmt(c.engagementRate, '%')}</td>
                    <td>${fmt(c.clicks)}</td>
                    <td>${fmt(c.ctr, '%')}</td>
                    <td>${fmt(c.spend, '$')}</td>
                    <td>${fmt(c.cpc, '$')}</td>
                    <td>${fmt(c.cpm, '$')}</td>
                    <td>${fmt(c.conversions)}</td>
                    <td>${fmt(c.cpa, '$')}</td>
                    <td>${fmt(c.roas)}</td>
                </tr>`).join('')}
            </tbody>
        </table>`;

    // Persist the parsed result so the Analytics page KPIs use real data.
    try {
        localStorage.setItem('nexus_performance', JSON.stringify({ updatedAt: new Date().toISOString(), rows: computed }));
    } catch { /* storage full — the table is still on screen */ }

    if (summaryHost) {
        const totalImpr = computed.reduce((a, c) => a + (c.impressions || 0), 0);
        const totalSpend = computed.reduce((a, c) => a + (c.spend || 0), 0);
        const totalRev = computed.reduce((a, c) => a + (c.revenue || 0), 0);
        summaryHost.textContent = `${computed.length} channel(s) · ${totalImpr.toLocaleString()} impressions · $${totalSpend.toFixed(2)} spend` +
            (totalRev ? ` · $${totalRev.toFixed(2)} revenue · blended ROAS ${(totalRev / totalSpend).toFixed(2)}×` : '');
    }

    if (insightsHost) insightsHost.innerHTML = buildPerformanceInsights(computed).map(i => `<li>${escHtml(i)}</li>`).join('');
    AgentLog.success('analytics', `Performance data analysed: ${rows.length} rows, ${computed.length} channel(s)`);
    showToast(`Analysed ${rows.length} rows`, 'success');
}

/** Deterministic, defensible observations — no invented benchmarks. */
function buildPerformanceInsights(computed) {
    const out = [];
    const withEr = computed.filter(c => c.engagementRate !== null);
    const withCtr = computed.filter(c => c.ctr !== null);

    if (withEr.length > 1) {
        const best = withEr.slice().sort((a, b) => b.engagementRate - a.engagementRate)[0];
        const worst = withEr.slice().sort((a, b) => a.engagementRate - b.engagementRate)[0];
        out.push(`Highest engagement rate: ${best.platform} at ${best.engagementRate.toFixed(2)}%. Lowest: ${worst.platform} at ${worst.engagementRate.toFixed(2)}% — shift production effort toward ${best.platform} before adding new channels.`);
    }
    if (withCtr.length) {
        const best = withCtr.slice().sort((a, b) => b.ctr - a.ctr)[0];
        out.push(`Best click-through rate: ${best.platform} at ${best.ctr.toFixed(2)}%. That is where your CTA copy and offer are landing.`);
    }
    const paid = computed.filter(c => c.spend && c.roas !== null);
    if (paid.length) {
        const best = paid.slice().sort((a, b) => b.roas - a.roas)[0];
        const bad = paid.filter(c => c.roas < 1);
        out.push(`Best return on ad spend: ${best.platform} at ${best.roas.toFixed(2)}×.`);
        if (bad.length) out.push(`${bad.map(c => c.platform).join(', ')} returned less than 1× spend — pause or rework the creative and audience before adding budget.`);
    }
    const expensive = computed.filter(c => c.cpm !== null).sort((a, b) => b.cpm - a.cpm)[0];
    if (expensive) out.push(`Highest CPM: ${expensive.platform} at $${expensive.cpm.toFixed(2)} per 1,000 impressions — creative fatigue or a narrow audience is the usual cause.`);
    if (!out.length) out.push('Not enough overlapping metrics to compare channels. Include at least impressions plus one of engagement, clicks or spend.');
    out.push('Next step: tag every outbound link with the UTM Builder so the next export attributes revenue to the exact post.');
    return out;
}

function loadSampleAnalytics() {
    const ta = document.getElementById('analyticsCsv');
    if (!ta) return;
    ta.value = [
        'date,platform,campaign,impressions,reach,likes,comments,shares,saves,clicks,conversions,spend,revenue',
        '2026-01-05,instagram,launch-week,48200,31500,1420,180,95,240,610,42,120,980',
        '2026-01-05,linkedin,launch-week,18400,12200,540,96,120,60,320,28,90,1240',
        '2026-01-06,facebook,launch-week,30100,21000,610,140,70,110,280,35,150,760',
        '2026-01-06,twitter,launch-week,9600,8100,240,45,38,12,140,9,0,0',
    ].join('\n');
    analyzePerformance();
}

function clearAnalytics() {
    const ta = document.getElementById('analyticsCsv');
    if (ta) ta.value = '';
    try { localStorage.removeItem('nexus_performance'); } catch {}
    const host = document.getElementById('analyticsTable');
    if (host) host.innerHTML = '<p class="helper-text">No performance data loaded yet.</p>';
    const ins = document.getElementById('analyticsInsights');
    if (ins) ins.innerHTML = '';
    const sum = document.getElementById('analyticsSummaryLine');
    if (sum) sum.textContent = '';
    initAnalytics();
}

function downloadAnalyticsTemplate() {
    const csv = 'date,platform,campaign,impressions,reach,likes,comments,shares,saves,clicks,conversions,spend,revenue\n' +
        '2026-01-01,instagram,my-campaign,0,0,0,0,0,0,0,0,0,0\n';
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'nexus-analytics-template.csv';
    a.click();
    URL.revokeObjectURL(a.href);
}

function handleAnalyticsFile(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        const ta = document.getElementById('analyticsCsv');
        if (ta) ta.value = String(reader.result || '');
        analyzePerformance();
    };
    reader.onerror = () => showToast('Could not read that file', 'error');
    reader.readAsText(file);
}

/* ── API Automation Scripts ──────────────────────────────────── */
const API_SCRIPTS = {
    'python-ig': `#!/usr/bin/env python3
"""Instagram Graph API — Auto-Post Script
Requirements: pip install requests
"""
import requests, json

# --- CREDENTIALS (from Connected Accounts page) ---
ACCESS_TOKEN   = "YOUR_LONG_LIVED_ACCESS_TOKEN"
IG_ACCOUNT_ID  = "YOUR_IG_BUSINESS_ACCOUNT_ID"

# --- POST CONTENT ---
IMAGE_URL = "https://your-public-image-url.jpg"
CAPTION   = "Your caption text here. #hashtag1 #hashtag2"

def post_to_instagram(image_url, caption):
    base = f"https://graph.facebook.com/v19.0/{IG_ACCOUNT_ID}"

    # Step 1: Create media container
    r1 = requests.post(f"{base}/media", data={
        "image_url": image_url,
        "caption": caption,
        "access_token": ACCESS_TOKEN,
    })
    container_id = r1.json().get("id")
    if not container_id:
        print("Error creating container:", r1.json())
        return

    # Step 2: Publish
    r2 = requests.post(f"{base}/media_publish", data={
        "creation_id": container_id,
        "access_token": ACCESS_TOKEN,
    })
    print("Published:", r2.json())

if __name__ == "__main__":
    post_to_instagram(IMAGE_URL, CAPTION)`,

    'python-fb': `#!/usr/bin/env python3
"""Facebook Pages API — Auto-Post Script
Requirements: pip install requests
"""
import requests

PAGE_ACCESS_TOKEN = "YOUR_PAGE_ACCESS_TOKEN"
PAGE_ID           = "YOUR_PAGE_ID"
MESSAGE           = "Your post content here."
IMAGE_URL         = ""  # optional

def post_to_facebook(message, image_url=""):
    url = f"https://graph.facebook.com/v19.0/{PAGE_ID}/feed"
    data = {"message": message, "access_token": PAGE_ACCESS_TOKEN}
    if image_url:
        data["link"] = image_url
    r = requests.post(url, data=data)
    print("Facebook response:", r.json())

if __name__ == "__main__":
    post_to_facebook(MESSAGE, IMAGE_URL)`,

    'python-tw': `#!/usr/bin/env python3
"""Twitter / X API v2 — Auto-Tweet Script
Requirements: pip install requests requests-oauthlib
"""
import requests
from requests_oauthlib import OAuth1

API_KEY        = "YOUR_API_KEY"
API_SECRET     = "YOUR_API_SECRET"
ACCESS_TOKEN   = "YOUR_ACCESS_TOKEN"
ACCESS_SECRET  = "YOUR_ACCESS_TOKEN_SECRET"

def post_tweet(text):
    url  = "https://api.twitter.com/2/tweets"
    auth = OAuth1(API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET)
    r    = requests.post(url, auth=auth, json={"text": text})
    print("Twitter response:", r.json())

if __name__ == "__main__":
    post_tweet("Your tweet text here! #marketing #automation")`,

    'python-li': `#!/usr/bin/env python3
"""LinkedIn API — Auto-Post Script (UGC Posts)
Requirements: pip install requests
"""
import requests, json

ACCESS_TOKEN = "YOUR_LINKEDIN_ACCESS_TOKEN"
PERSON_URN   = "urn:li:person:YOUR_PERSON_ID"  # from /v2/me

def post_to_linkedin(text):
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    body = {
        "author": PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    r = requests.post(url, headers=headers, data=json.dumps(body))
    print("LinkedIn response:", r.status_code, r.text)

if __name__ == "__main__":
    post_to_linkedin("Your LinkedIn post text here.")`,

    'python-yt': `#!/usr/bin/env python3
"""YouTube Data API v3 — Community Post / Video Upload
Requirements: pip install google-api-python-client google-auth-oauthlib
"""
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

CLIENT_ID      = "YOUR_GOOGLE_CLIENT_ID"
CLIENT_SECRET  = "YOUR_GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN  = "YOUR_REFRESH_TOKEN"

def get_youtube_service():
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    return build("youtube", "v3", credentials=creds)

def upload_video(file_path, title, description, tags):
    yt = get_youtube_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": "public"},
    }
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    request = yt.videos().insert(part=",".join(body.keys()), body=body, media_body=media)
    response = request.execute()
    print("Uploaded video ID:", response.get("id"))

if __name__ == "__main__":
    upload_video("my_video.mp4", "My Title", "My Description", ["tag1", "tag2"])`,

    'python-bl': `#!/usr/bin/env python3
"""Blogger API v3 — Auto-Publish Post Script
Requirements: pip install requests
"""
import requests, json

ACCESS_TOKEN = "YOUR_GOOGLE_OAUTH_ACCESS_TOKEN"
BLOG_ID      = "YOUR_BLOGGER_BLOG_ID"

def publish_blog_post(title, content_html, labels=None):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {
        "kind": "blogger#post",
        "title": title,
        "content": content_html,
    }
    if labels:
        body["labels"] = labels
    r = requests.post(url, headers=headers, data=json.dumps(body))
    result = r.json()
    print("Blog post URL:", result.get("url", "Error: " + str(result)))

if __name__ == "__main__":
    publish_blog_post(
        title="My New Blog Post",
        content_html="<p>Your post content in <strong>HTML</strong> here.</p>",
        labels=["marketing", "tech"],
    )`,
};

let activeScript = 'python-ig';

function switchScriptTab(btn, scriptKey) {
    document.querySelectorAll('.script-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    activeScript = scriptKey;
    renderScript(scriptKey);
}

function renderScript(key) {
    const el = document.getElementById('scriptDisplay');
    if (el) el.textContent = API_SCRIPTS[key] || '# Script not found';
}

function copyScript() {
    copyToClipboard(API_SCRIPTS[activeScript] || '').then(ok => {
        showToast(ok ? 'Script copied to clipboard!' : 'Copy blocked by the browser', ok ? 'success' : 'error');
    });
}

/* ── Page init for Accounts ──────────────────────────────────── */
function initAccountsPage() {
    loadSavedCredentials();
    renderScript('python-ig');
}

/* ══════════════════════════════════════════════════════════════
   CANVA STUDIO LOGIC
══════════════════════════════════════════════════════════════ */
const CANVA_TEMPLATES = {
    'ig-post':    'https://www.canva.com/create/instagram-posts/',
    'ig-story':   'https://www.canva.com/create/instagram-stories/',
    'fb-cover':   'https://www.canva.com/create/facebook-covers/',
    'yt-thumb':   'https://www.canva.com/create/youtube-thumbnails/',
    'li-post':    'https://www.canva.com/create/linkedin-banners/',
    'tw-header':  'https://www.canva.com/create/twitter-headers/',
    'blog-banner':'https://www.canva.com/create/blog-banners/',
    'custom':     'https://www.canva.com/design/newdesign',
};

function openCanvaTemplate(type) {
    const url = CANVA_TEMPLATES[type] || 'https://www.canva.com';
    window.open(url, '_blank', 'noopener,noreferrer');
    showToast(`Opening Canva — ${type.replace(/-/g,' ')} editor...`, 'success');
}

/**
 * Canva Connect OAuth with a REAL PKCE pair.
 *
 * BUG FIX: the old code sent a hard-coded code_challenge with no matching
 * code_verifier stored anywhere, which can never complete an OAuth flow — the
 * user was told to "paste the access token" with no way to obtain one.
 * PKCE requires a fresh, high-entropy verifier per attempt, and the verifier
 * must be kept until the authorization code comes back.
 */
function base64UrlEncode(bytes) {
    let str = '';
    bytes.forEach(b => { str += String.fromCharCode(b); });
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function openCanvaAuth() {
    const clientId = document.getElementById('canva-client-id')?.value.trim();
    if (!clientId) { showToast('Enter your Canva Client ID first.', 'info'); return; }
    if (!window.crypto?.subtle) {
        showToast('PKCE needs a secure context — serve the UI over http://localhost instead of opening the file directly.', 'error');
        return;
    }

    const verifierBytes = new Uint8Array(48);
    crypto.getRandomValues(verifierBytes);
    const codeVerifier = base64UrlEncode(verifierBytes);
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier));
    const codeChallenge = base64UrlEncode(new Uint8Array(digest));
    const state = base64UrlEncode(crypto.getRandomValues(new Uint8Array(16)));

    const redirectUri = (document.getElementById('canva-redirect-uri')?.value.trim())
        || `${location.origin}${location.pathname}`;

    // The verifier must survive the round-trip to Canva.
    sessionStorage.setItem('canva_pkce_verifier', codeVerifier);
    sessionStorage.setItem('canva_oauth_state', state);
    sessionStorage.setItem('canva_redirect_uri', redirectUri);

    const authUrl = 'https://www.canva.com/api/oauth/authorize?' + new URLSearchParams({
        client_id: clientId,
        response_type: 'code',
        redirect_uri: redirectUri,
        scope: 'design:content:read design:content:write design:meta:read',
        code_challenge: codeChallenge,
        code_challenge_method: 'S256',
        state,
    });

    AgentLog.info('canva.oauth', 'Canva authorization started (PKCE verifier stored for this tab)');
    window.open(authUrl.toString(), '_blank', 'noopener,noreferrer');
    showToast('Authorize in the new tab — Nexus will catch the callback automatically.', 'info');
}

/**
 * Handle the ?code=...&state=... redirect from Canva, exchange it for tokens,
 * and store them. Called on page load.
 */
async function handleCanvaCallback() {
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    if (!code && !params.get('error')) return false;

    const expectedState = sessionStorage.getItem('canva_oauth_state');
    const verifier = sessionStorage.getItem('canva_pkce_verifier');
    const redirectUri = sessionStorage.getItem('canva_redirect_uri') || `${location.origin}${location.pathname}`;

    if (params.get('error')) {
        AgentLog.error('canva.oauth', `Canva denied the request: ${params.get('error')}`);
        showToast(`Canva authorization failed: ${params.get('error')}`, 'error');
        return true;
    }
    if (expectedState && params.get('state') !== expectedState) {
        AgentLog.error('canva.oauth', 'State mismatch — possible CSRF, token exchange aborted');
        showToast('Canva callback rejected: state mismatch.', 'error');
        return true;
    }
    if (!verifier) {
        showToast('Canva callback received but the PKCE verifier is missing (different tab/session). Start again.', 'error');
        return true;
    }

    const clientId = document.getElementById('canva-client-id')?.value.trim()
        || safeJson('nexus_credentials', {})['canva-client-id'] || '';
    const clientSecret = safeJson('nexus_credentials', {})['canva-client-secret'] || '';

    try {
        const body = new URLSearchParams({
            grant_type: 'authorization_code',
            code,
            code_verifier: verifier,
            redirect_uri: redirectUri,
        });
        const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
        if (clientSecret) {
            headers.Authorization = `Basic ${btoa(`${clientId}:${clientSecret}`)}`;
        } else {
            body.set('client_id', clientId);
        }
        const res = await fetchWithTimeout('https://api.canva.com/rest/v1/oauth/token', {
            method: 'POST', headers, body: body.toString(),
        }, 20000);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error_description || data.error || `HTTP ${res.status}`);

        const creds = safeJson('nexus_credentials', {});
        creds['canva-access-token'] = data.access_token || '';
        if (data.refresh_token) creds['canva-refresh-token'] = data.refresh_token;
        creds['canva-token-expires'] = String(Date.now() + (data.expires_in || 3600) * 1000);
        localStorage.setItem('nexus_credentials', JSON.stringify(creds));
        checkSavedStatus('canva');
        AgentLog.success('canva.oauth', 'Canva tokens stored (valid for ' + Math.round((data.expires_in || 3600) / 60) + ' min)');
        showToast('Canva connected ✔', 'success');
        history.replaceState({}, '', location.pathname);
    } catch (e) {
        AgentLog.error('canva.oauth', `Token exchange failed: ${e.message}`,
            'If the browser blocked the cross-origin token call, run the exchange server-side with the same client_id and code_verifier.');
        showToast(`Canva token exchange failed: ${e.message}`, 'error');
    } finally {
        sessionStorage.removeItem('canva_pkce_verifier');
        sessionStorage.removeItem('canva_oauth_state');
    }
    return true;
}

function saveCanvaCredentials() {
    const saved = safeJson('nexus_credentials', {});
    ['canva-client-id', 'canva-client-secret', 'canva-access-token', 'canva-redirect-uri'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.value.trim()) saved[id] = el.value.trim();
    });
    localStorage.setItem('nexus_credentials', JSON.stringify(saved));
    checkSavedStatus('canva');
    showToast('Canva credentials saved!', 'success');
}

/** Generate a real, brand-aware caption through the configured AI engine. */
async function genCanvaCaption() {
    const link = document.getElementById('canvaDesignLink')?.value?.trim();
    const topic = document.getElementById('canvaBriefTopic')?.value?.trim() || '';
    const platform = document.getElementById('canvaBriefPlatform')?.value || 'Instagram Post';
    const captionEl = document.getElementById('canvaPostCaption');
    if (!link && !topic) { showToast('Paste your Canva design link or describe the design first.', 'info'); return; }
    if (!captionEl) return;

    captionEl.value = 'Generating caption…';
    try {
        const results = await window.NexusAI.generate({
            instruction: `Write social captions for this design. Design link: ${link || 'n/a'}. Design subject: ${topic || link}.`,
            platforms: ['instagram'],
            tone: State.settings.tone || 'professional',
            brand: State.settings.brand,
            contentType: 'caption',
            context: `The visual is a ${platform}. Output ONLY the caption text (hook, body, CTA, hashtags) — no headings or commentary.`,
            onLog: (level, source, message, detail) => (AgentLog[level] || AgentLog.info).call(AgentLog, source, message, detail),
        });
        const raw = results.instagram || Object.values(results)[0] || '';
        captionEl.value = stripMarkdown(raw);
        showToast('Caption generated — edit before publishing', 'success');
    } catch (e) {
        captionEl.value = '';
        showToast(`Caption failed: ${e.message}`, 'error');
        AgentLog.error('canva.caption', 'Caption generation failed', e.message);
    }
}

/**
 * Hand the design off to the real publisher.
 *
 * HONESTY FIX: the old implementation waited ~1s and then printed
 * "Design link shared + caption published ✓" for every platform that merely had
 * a token stored — nothing was ever sent anywhere. Faking a successful publish
 * in a marketing tool is how people ship nothing while believing they shipped.
 * The job now goes to agent/publish.py through the relay, and the UI reports the
 * true per-platform result.
 */
async function publishCanvaDesign() {
    const link = document.getElementById('canvaDesignLink')?.value?.trim();
    const caption = document.getElementById('canvaPostCaption')?.value?.trim();
    const log = document.getElementById('canvaPublishLog');
    if (!log) return;

    if (!link) { showToast('Paste your Canva design link first.', 'info'); return; }
    if (!caption) { showToast('Add a caption for the post.', 'info'); return; }

    // The Graph APIs accept a link post on Facebook/LinkedIn; Instagram needs a
    // direct public image URL, so it is deliberately excluded from this hand-off.
    const platforms = ['facebook', 'linkedin'];
    log.innerHTML = `<div class="log-entry log-info"><i class="ph ph-paper-plane-tilt"></i> Sending the design link + caption to the local publisher…</div>
        <div class="log-entry log-pending"><i class="ph ph-info"></i> Instagram needs a direct public image URL (export from Canva and host it), so it is not part of this hand-off.</div>`;

    try {
        const data = await queuePublishJob({
            id: `job-canva-${Date.now()}`,
            content: `${caption}\n\n${link}`,
            platforms,
            media: link,
            schedule: null,
            status: 'queued',
            created_at: new Date().toISOString(),
            credentials: publishCredSlice(safeJson('nexus_credentials', {})),
        });
        (data?.results || []).forEach(r => {
            log.innerHTML += `<div class="log-entry ${r.ok ? 'log-success' : 'log-error'}"><i class="ph ph-paper-plane-tilt"></i> <strong>${escHtml(String(r.platform))}:</strong> ${escHtml(r.ok ? `Posted${r.id ? ' id=' + r.id : ''}` : String(r.error || 'failed'))}</div>`;
        });
        AgentLog.success('canva.publish', `Design hand-off → ${data?.status || 'done'}`);
        showToast(data?.status === 'published' ? 'Design published ✔' : 'Hand-off finished — check the log above', data?.status === 'published' ? 'success' : 'info');
    } catch (e) {
        log.innerHTML += `<div class="log-entry log-error"><i class="ph ph-warning"></i> Not published: ${escHtml(e.message)}. Start the relay (<code>python3 agent/nexus_relay.py</code>) and retry.</div>`;
        AgentLog.error('canva.publish', 'Design hand-off failed', e.message);
        showToast('Nothing was published — see the log above', 'error');
    }
    refreshQueueView();
}

function generateVisualBrief() {
    const topic = document.getElementById('canvaBriefTopic')?.value.trim();
    const platform = document.getElementById('canvaBriefPlatform')?.value || 'Instagram Post';
    const style = document.getElementById('canvaBriefStyle')?.value || 'Modern & Minimal';
    const output = document.getElementById('canvaBriefOutput');

    if (!topic) { showToast('Describe your post topic first.', 'info'); return; }

    const dims = {
        'Instagram Post': '1080×1080px (square)',
        'Instagram Story': '1080×1920px (portrait 9:16)',
        'YouTube Thumbnail': '1280×720px (16:9)',
        'Facebook Cover': '851×315px (landscape)',
        'LinkedIn Banner': '1584×396px (landscape)',
        'Blog Header': '1920×600px (wide banner)',
    }[platform] || '1080×1080px';

    const palettes = {
        'Modern & Minimal': 'White (#FFFFFF), Light grey (#F4F4F4), Black (#111111), One accent: Deep Purple (#5C33F6)',
        'Bold & Vibrant': 'Hot coral (#FF4D4D), Electric blue (#0066FF), Bright yellow (#FFE600), Black text on white',
        'Corporate & Professional': 'Navy (#002D62), White (#FFFFFF), Silver grey (#E8E8E8), Gold accent (#C9A84C)',
        'Playful & Colorful': 'Soft pink (#FFB6C1), Sky blue (#87CEEB), Mint green (#98FF98), Lavender (#E6E6FA)',
        'Dark & Premium': 'Near-black (#0A0A0F), Deep purple (#3B1F7C), Gold (#FFD700), White text',
        'Retro & Vintage': 'Warm cream (#F5E6C8), Dusty rose (#C4858A), Forest green (#2D5A27), Brown (#6B4423)',
    }[style] || 'White, Dark, Accent color';

    output.textContent = `🎨 VISUAL DESIGN BRIEF
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 PLATFORM: ${platform}
📐 DIMENSIONS: ${dims}
🎭 STYLE: ${style}
📝 TOPIC: ${topic}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎨 COLOR PALETTE
${palettes}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 TYPOGRAPHY
• Headline: Bold sans-serif (Outfit, Inter, or Poppins) — 48-72pt
• Body: Regular weight — 18-24pt
• Max 2 font families. Never more.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🖼️ VISUAL COMPOSITION
• Primary focal point: Center or left-third of frame
• Headline placement: Upper quarter (for thumbnails: left side)
• White space: Generous — avoid clutter
• Brand logo: Bottom-right corner, 8% opacity or full color watermark

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✍️ TEXT OVERLAY (Suggested)
• Headline: "${topic.substring(0, 40)}"
• Subheadline: A 5-7 word supporting statement
• CTA badge: "Learn More →" or "Save This →" in accent color

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 CANVA AI PROMPT (paste into Canva Magic Design)
"Create a ${style.toLowerCase()} ${platform.toLowerCase()} graphic for a post about '${topic}'. Use ${palettes.split(',')[0].trim()} as the primary background color. Include bold headline text and minimal design elements. Professional and premium feel."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📤 EXPORT SETTINGS
• Format: PNG (for photos) or PDF Print (for text-heavy)
• Resolution: 2× for retina displays
• Compression: Medium (for web), High quality (for ads)`;

    showToast('Visual design brief generated!', 'success');
}

/* ══════════════════════════════════════════════════════════════
   EXTEND PAGE_TITLES for new pages
══════════════════════════════════════════════════════════════ */
Object.assign(PAGE_TITLES, {
    accounts: 'Connected Accounts',
    canva:    'Canva Studio',
    utm:      'UTM Campaign Link Builder',
    logs:     'Agent Error & Activity Logs',
    tutorial: 'Tutorial — How to Use Nexus',
});

/* ══════════════════════════════════════════════════════════════
   AGENT LOGS PAGE
══════════════════════════════════════════════════════════════ */
let logFilter = 'all';

function setLogFilter(f, btn) {
    logFilter = f;
    document.querySelectorAll('#logFilterBar .tab-btn').forEach(b => b.classList.toggle('active', b === btn));
    renderAgentLogs();
}

function renderAgentLogs() {
    const list = document.getElementById('agentLogList');
    if (!list) return;
    let entries = AgentLog.all().slice().reverse();
    if (logFilter !== 'all') entries = entries.filter(e => e.level === logFilter);

    const counts = { error: 0, warn: 0 };
    AgentLog.all().forEach(e => { if (counts[e.level] !== undefined) counts[e.level]++; });
    const stats = document.getElementById('logStats');
    if (stats) stats.textContent = `${AgentLog.all().length} events • ${counts.error} errors • ${counts.warn} warnings`;

    if (!entries.length) {
        list.innerHTML = '<div class="log-empty">No log entries yet. The agent documents every action and failure here automatically.</div>';
        return;
    }
    const ICONS = { info: 'ph-info', warn: 'ph-warning-circle', error: 'ph-x-circle', success: 'ph-check-circle' };
    list.innerHTML = entries.map(e => `
        <div class="agent-log-row lvl-${e.level}">
            <span class="al-time">${new Date(e.ts).toLocaleString()}</span>
            <span class="al-level"><i class="ph-fill ${ICONS[e.level] || 'ph-info'}"></i> ${e.level.toUpperCase()}</span>
            <span class="al-src">${escHtml(e.source)}</span>
            <span class="al-msg">${escHtml(e.message)}${e.detail ? `<details><summary>context</summary><pre>${escHtml(e.detail)}</pre></details>` : ''}</span>
        </div>`).join('');
}

function clearAgentLogs() {
    AgentLog.clear();
    renderAgentLogs();
    showToast('Agent logs cleared', 'success');
}


/* ══════════════════════════════════════════════════════════════
   IN-APP TUTORIAL (guided steps + engine cheat-sheet)
══════════════════════════════════════════════════════════════ */
const TUTORIAL_STEPS = [
    { title: 'Welcome to Nexus', body: 'Nexus is an AI-powered Social Media Manager. It generates platform-native content, plans ad campaigns (Meta Ads & Google Ads), builds calendars, and documents every agent action \u2014 including failures \u2014 in the Agent Logs page so nothing is ever lost.' },
    { title: 'Step 1 \u2014 Set your brand profile', body: 'Go to Settings. Enter Brand Name, Niche, Audience and Tone, then pick your AI engine:\n\u2022 Google Antigravity \u2014 agentic handoff (uses the digital-marketer skill in this repo)\n\u2022 Cloud APIs \u2014 xAI Grok, Groq, OpenRouter (Hermes 4), Gemini, Claude\n\u2022 Local Models \u2014 LM Studio (Hermes/Llama GGUF) or Ollama\nSettings persist automatically in your browser.' },
    { title: 'Step 2 \u2014 Connect accounts & providers', body: 'Open Connected Accounts. Paste API keys/tokens for each platform (Instagram Graph, Facebook Page, YouTube, X, LinkedIn, Blogger, Canva) and for cloud providers (xAI, Groq, OpenRouter, Gemini, Anthropic). Click Save, then Test Connections. Keys stay in your browser only \u2014 never sent to any Nexus server.' },
    { title: 'Step 3 \u2014 Dispatch a task to the agent', body: 'On the AI Agent page, write one directive (e.g. "Launch campaign for our new AI scheduling feature"), tick platforms, choose tone and content type, then Dispatch. If your chosen engine fails (offline model, bad key, rate limit), Nexus retries with exponential backoff, fails over down the provider chain, logs the failure, and still delivers built-in template output so you are never blocked.' },
    { title: 'Step 4 \u2014 Use the specialist hubs', body: 'Instagram/Facebook/YouTube/LinkedIn/Blog hubs generate carousel scripts, Reel scripts, ad copy, titles, polls and SEO outlines. Canva Studio creates visual design briefs + Canva AI prompts. Content Studio has caption, hook, hashtag, email, SEO and A/B tools. Trends shows what is hot right now.' },
    { title: 'Step 5 \u2014 Schedule & calendar', body: 'Add posts to the Content Calendar. For hands-free automation use the scheduler command from Settings with Google Antigravity, or run daily_run.sh from the skill folder with LM Studio \u2014 both documented in the README Quick Start.' },
    { title: 'Step 6 \u2014 Browser automation, MCP & Telegram', body: 'Browser Agent page: let the agent drive Chrome via Playwright/DevTools Protocol — navigate, snapshot, extract, screenshot, inspect console errors like a developer. Clicking/typing stays LOCKED until you tick "Grant Browser Actions Permission"; destructive targets (logout/delete account) are always refused. MCP & Integrations page: register agent/nexus_mcp.py in Google Antigravity, Claude Desktop or LM Studio so Hermes/Grok agents can call all 11 Nexus tools; connect your Telegram bot (token + chat id) to run everything from your phone with tap-to-approve security.' },
    { title: 'Step 7 \u2014 Review agent health', body: 'Visit Agent Logs after every session. Errors (red) show exactly which provider failed and why; export the full JSON log and attach it when asking for support. A running app should be debuggable \u2014 that is the whole point of the error-report system.' },
];

let tutorialIndex = 0;

function initTutorial() {
    const dots = document.getElementById('tutorialDots');
    if (dots) dots.textContent = `${tutorialIndex + 1} / ${TUTORIAL_STEPS.length}`;
    const step = TUTORIAL_STEPS[tutorialIndex];
    const t = document.getElementById('tutorialTitle');
    const b = document.getElementById('tutorialBody');
    if (t) t.textContent = step.title;
    if (b) b.textContent = step.body;
    const prev = document.getElementById('tutPrevBtn');
    const next = document.getElementById('tutNextBtn');
    if (prev) prev.disabled = tutorialIndex === 0;
    if (next) next.textContent = tutorialIndex === TUTORIAL_STEPS.length - 1 ? 'Finish \u2713' : 'Next \u2192';
}

function tutNav(dir) {
    if (dir > 0 && tutorialIndex === TUTORIAL_STEPS.length - 1) { switchPage('agent'); return; }
    tutorialIndex = Math.max(0, Math.min(TUTORIAL_STEPS.length - 1, tutorialIndex + dir));
    initTutorial();
}

function jumpTo(page) { switchPage(page); }

/* ══════════════════════════════════════════════════════════════
   APPROVAL WORKFLOW
   Publishing to a live brand account should never be a single
   unchecked click. Post status moves strictly forward:

       draft → in_review → approved → scheduled → published

   Every transition is written to the Agent Log with a timestamp, so
   there is an auditable answer to "who approved this?".
═════════════════════════════════════════════════════════════ */
const POST_STATUS_FLOW = ['draft', 'in_review', 'approved', 'scheduled', 'published'];

const STATUS_BADGE = {
    draft: { cls: 'st-draft', label: 'Draft' },
    in_review: { cls: 'st-planning', label: 'In review' },
    approved: { cls: 'st-approved', label: 'Approved' },
    scheduled: { cls: 'st-scheduled', label: 'Scheduled' },
    published: { cls: 'st-published', label: 'Published' },
    planning: { cls: 'st-planning', label: 'Planning' },
};

function nextStatus(current) {
    const i = POST_STATUS_FLOW.indexOf(current || 'draft');
    return i === -1 || i === POST_STATUS_FLOW.length - 1 ? null : POST_STATUS_FLOW[i + 1];
}

function advancePostStatus(eventId) {
    const ev = State.calendarEvents.find(e => e.id === eventId);
    if (!ev) return;
    const next = nextStatus(ev.status || 'draft');
    if (!next) { showToast('Already published — nothing further to approve.', 'info'); return; }
    ev.status = next;
    ev.updatedAt = new Date().toISOString();
    ev.history = [...(ev.history || []), { status: next, at: ev.updatedAt }];
    persistCalendarEvents();
    AgentLog.info('workflow', `"${ev.title}" → ${STATUS_BADGE[next].label}`, { id: ev.id, platform: ev.platform });
    showToast(`Status: ${STATUS_BADGE[next].label}`, 'success');
    renderCalendar();
    renderPostDetail(ev.id);
}

function revertPostStatus(eventId) {
    const ev = State.calendarEvents.find(e => e.id === eventId);
    if (!ev) return;
    const i = POST_STATUS_FLOW.indexOf(ev.status || 'draft');
    if (i <= 0) { showToast('Already at the first stage.', 'info'); return; }
    ev.status = POST_STATUS_FLOW[i - 1];
    ev.updatedAt = new Date().toISOString();
    ev.history = [...(ev.history || []), { status: ev.status, at: ev.updatedAt }];
    persistCalendarEvents();
    AgentLog.warn('workflow', `"${ev.title}" reverted to ${STATUS_BADGE[ev.status].label}`, { id: ev.id });
    renderCalendar();
    renderPostDetail(ev.id);
}

function deletePost(eventId) {
    const ev = State.calendarEvents.find(e => e.id === eventId);
    if (!ev) return;
    if (!confirm(`Delete "${ev.title}"? This cannot be undone.`)) return;
    State.calendarEvents = State.calendarEvents.filter(e => e.id !== eventId);
    persistCalendarEvents();
    AgentLog.warn('workflow', `Deleted calendar post "${ev.title}"`, { id: eventId });
    showToast('Post deleted', 'success');
    closePostDetail();
    renderCalendar();
}

function persistCalendarEvents() {
    try {
        localStorage.setItem('nexus_calendar_events', JSON.stringify(State.calendarEvents));
    } catch {
        showToast('Could not save — browser storage is full', 'error');
    }
}

/** Detail drawer for a calendar post, with the approval controls. */
function renderPostDetail(eventId) {
    const host = document.getElementById('postDetail');
    if (!host) return;
    const ev = State.calendarEvents.find(e => e.id === eventId);
    if (!ev) { host.classList.add('hidden'); return; }

    const status = ev.status || 'draft';
    const badge = STATUS_BADGE[status] || STATUS_BADGE.draft;
    const next = nextStatus(status);
    host.classList.remove('hidden');
    host.innerHTML = `
        <div class="card-header">
            <h3>${escHtml(ev.title)}</h3>
            <button class="icon-btn" onclick="closePostDetail()" aria-label="Close"><i class="ph ph-x"></i></button>
        </div>
        <div class="detail-row"><span class="status-badge ${badge.cls}">${escHtml(badge.label)}</span>
            <span class="chip">${escHtml(ev.platform || 'unassigned')}</span>
            <span class="chip">${escHtml([ev.date, ev.time].filter(Boolean).join(' '))}</span></div>
        ${ev.body ? `<div class="content-block detail-body">${renderContent(ev.body)}</div>` : '<p class="helper-text">No body copy attached yet. Generate content and use "To calendar" to attach it.</p>'}
        ${(ev.history || []).length ? `<div class="detail-history"><strong>History</strong><ul>${ev.history.map(h => `<li>${escHtml(new Date(h.at).toLocaleString())} → ${escHtml((STATUS_BADGE[h.status] || {}).label || h.status)}</li>`).join('')}</ul></div>` : ''}
        <div class="detail-actions">
            ${next ? `<button class="btn btn-primary btn-sm" onclick="advancePostStatus('${escHtml(ev.id)}')"><i class="ph ph-arrow-right"></i> Move to ${escHtml(STATUS_BADGE[next].label)}</button>` : ''}
            <button class="btn btn-ghost btn-sm" onclick="revertPostStatus('${escHtml(ev.id)}')"><i class="ph ph-arrow-left"></i> Revert</button>
            <button class="btn btn-ghost btn-sm" onclick="postToPublisher('${escHtml(ev.id)}')"><i class="ph ph-paper-plane-tilt"></i> Send to publisher</button>
            <button class="btn btn-ghost btn-sm btn-danger" onclick="deletePost('${escHtml(ev.id)}')"><i class="ph ph-trash"></i> Delete</button>
        </div>`;
}

function closePostDetail() {
    document.getElementById('postDetail')?.classList.add('hidden');
}

function postToPublisher(eventId) {
    const ev = State.calendarEvents.find(e => e.id === eventId);
    if (!ev) return;
    if ((ev.status || 'draft') !== 'approved' && (ev.status || 'draft') !== 'scheduled') {
        showToast('Approve the post before sending it to the publisher.', 'info');
        AgentLog.warn('workflow', `Blocked publish of "${ev.title}" — status is ${ev.status || 'draft'}`);
        return;
    }
    switchPage('accounts');
    const ta = document.getElementById('autoPostContent');
    if (ta) ta.value = stripMarkdown(ev.body || ev.title).slice(0, 4000);
    const cb = document.querySelector(`input[name="autoPlat"][value="${canonicalPlatform(ev.platform)}"]`);
    if (cb) cb.checked = true;
    previewAutoPost();
}

/* ══════════════════════════════════════════════════════════════
   COMMAND PALETTE  (⌘K / Ctrl+K)
   Production polish: every action reachable from the keyboard.
═════════════════════════════════════════════════════════════ */
const PALETTE_ACTIONS = [
    { label: 'Go to Dashboard', hint: 'page', run: () => switchPage('dashboard') },
    { label: 'Go to AI Agent', hint: 'page', run: () => switchPage('agent') },
    { label: 'Go to Content Studio', hint: 'page', run: () => switchPage('create') },
    { label: 'Go to Content Calendar', hint: 'page', run: () => switchPage('calendar') },
    { label: 'Go to Analytics', hint: 'page', run: () => switchPage('analytics') },
    { label: 'Go to Trend Radar', hint: 'page', run: () => switchPage('trends') },
    { label: 'Go to UTM Builder', hint: 'page', run: () => switchPage('utm') },
    { label: 'Go to Connected Accounts', hint: 'page', run: () => switchPage('accounts') },
    { label: 'Go to Browser Agent', hint: 'page', run: () => switchPage('browser') },
    { label: 'Go to Agent Logs', hint: 'page', run: () => switchPage('logs') },
    { label: 'Go to Settings', hint: 'page', run: () => switchPage('settings') },
    { label: 'Dispatch a new task', hint: 'action', run: () => { switchPage('agent'); document.getElementById('agentInstruction')?.focus(); } },
    { label: 'Cancel running generation', hint: 'action', run: () => cancelDispatch() },
    { label: 'Copy the active output', hint: 'action', run: () => copyOutput() },
    { label: 'Repurpose the active output', hint: 'action', run: () => repurposeActiveOutput() },
    { label: 'Add the active output to the calendar', hint: 'action', run: () => addOutputToCalendar() },
    { label: 'Send the active output to the publisher', hint: 'action', run: () => sendOutputToPublisher() },
    { label: 'Validate the publish payload (dry run)', hint: 'action', run: () => validatePublish() },
    { label: 'Process scheduled posts that are due', hint: 'action', run: () => publishDueJobs() },
    { label: 'Refresh the local queue', hint: 'action', run: () => refreshQueueView() },
    { label: 'Check relay health', hint: 'action', run: () => checkRelayHealth().then(d => showToast(d ? 'Relay reachable' : 'Relay offline', d ? 'success' : 'error')) },
    { label: 'Test all social connections', hint: 'action', run: () => testAllConnections() },
    { label: 'Import performance data', hint: 'action', run: () => { switchPage('analytics'); document.getElementById('analyticsCsv')?.focus(); } },
    { label: 'Export the agent log (JSON)', hint: 'action', run: () => AgentLog.exportJson() },
    { label: 'Show the tutorial', hint: 'help', run: () => switchPage('tutorial') },
];

let paletteIndex = 0;
let paletteResults = [];

function initCommandPalette() {
    const overlay = document.getElementById('paletteOverlay');
    const input = document.getElementById('paletteInput');
    if (!overlay || !input) return;

    document.addEventListener('keydown', (e) => {
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName) || e.target?.isContentEditable;
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            openPalette();
            return;
        }
        if (!overlay.classList.contains('hidden')) {
            if (e.key === 'Escape') { closePalette(); }
            else if (e.key === 'ArrowDown') { e.preventDefault(); paletteMove(1); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); paletteMove(-1); }
            else if (e.key === 'Enter') { e.preventDefault(); paletteResults[paletteIndex]?.run(); closePalette(); }
        } else if (e.key === '/' && !typing) {
            e.preventDefault();
            openPalette();
        }
    });

    input.addEventListener('input', () => renderPaletteResults(input.value));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closePalette(); });
}

function openPalette() {
    const overlay = document.getElementById('paletteOverlay');
    const input = document.getElementById('paletteInput');
    if (!overlay || !input) return;
    overlay.classList.remove('hidden');
    input.value = '';
    renderPaletteResults('');
    input.focus();
}

function closePalette() {
    document.getElementById('paletteOverlay')?.classList.add('hidden');
}

function renderPaletteResults(query) {
    const list = document.getElementById('paletteResults');
    if (!list) return;
    const q = String(query || '').toLowerCase().trim();
    paletteResults = PALETTE_ACTIONS.filter(a =>
        !q || a.label.toLowerCase().includes(q) || a.hint.includes(q));
    paletteIndex = 0;
    if (!paletteResults.length) {
        list.innerHTML = '<div class="palette-item disabled">No matching action</div>';
        return;
    }
    list.innerHTML = paletteResults.map((a, i) =>
        `<div class="palette-item ${i === 0 ? 'active' : ''}" data-i="${i}">
            <span>${escHtml(a.label)}</span><span class="palette-hint">${escHtml(a.hint)}</span>
        </div>`).join('');
    list.querySelectorAll('.palette-item').forEach(el =>
        el.addEventListener('click', () => {
            paletteResults[Number(el.dataset.i)]?.run();
            closePalette();
        }));
}

function paletteMove(delta) {
    if (!paletteResults.length) return;
    paletteIndex = (paletteIndex + delta + paletteResults.length) % paletteResults.length;
    document.querySelectorAll('#paletteResults .palette-item').forEach((el, i) =>
        el.classList.toggle('active', i === paletteIndex));
    document.querySelector('#paletteResults .palette-item.active')?.scrollIntoView({ block: 'nearest' });
}

/* ══════════════════════════════════════════════════════════════
   WORKSPACE BACKUP / RESTORE
   Everything the app learns about your brand lives in localStorage.
   Losing it to a cleared cache is a real risk, so export/import is
   a first-class feature. Credentials are excluded by default.
═════════════════════════════════════════════════════════════ */
const BACKUP_KEYS = [
    'nexus_settings', 'nexus_ai_config', 'nexus_calendar_events',
    'nexus_content_history', 'nexus_agent_log', 'nexus_drafts',
    'nexus_publish_queue', 'nexus_performance',
];

function exportWorkspace({ includeSecrets = false } = {}) {
    const payload = {
        app: 'nexus-marketing-command-center',
        version: 3,
        exportedAt: new Date().toISOString(),
        data: {},
    };
    BACKUP_KEYS.forEach(k => {
        const raw = localStorage.getItem(k);
        if (raw !== null) {
            try { payload.data[k] = JSON.parse(raw); } catch { payload.data[k] = raw; }
        }
    });
    if (includeSecrets) {
        payload.data['nexus_credentials'] = safeJson('nexus_credentials', {});
        payload.warning = 'This file contains API keys and access tokens in plain text. Store it accordingly.';
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `nexus-workspace-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    AgentLog.info('workspace', `Workspace exported${includeSecrets ? ' (including credentials)' : ''}`);
    showToast(includeSecrets ? 'Exported with credentials — keep the file safe' : 'Workspace exported', 'success');
}

function importWorkspace(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        try {
            const payload = JSON.parse(String(reader.result || '{}'));
            if (payload.app !== 'nexus-marketing-command-center' || !payload.data) {
                throw new Error('This is not a Nexus workspace file.');
            }
            if (!confirm(`Importing will overwrite your current workspace with the backup from ${payload.exportedAt || 'unknown date'}. Continue?`)) return;
            Object.entries(payload.data).forEach(([k, v]) => {
                localStorage.setItem(k, typeof v === 'string' ? v : JSON.stringify(v));
            });
            AgentLog.success('workspace', `Workspace imported from ${file.name}`, { keys: Object.keys(payload.data) });
            showToast('Workspace imported — reloading…', 'success');
            setTimeout(() => location.reload(), 700);
        } catch (e) {
            AgentLog.error('workspace', `Import failed: ${e.message}`);
            showToast(`Import failed: ${e.message}`, 'error');
        } finally {
            if (input) input.value = '';
        }
    };
    reader.onerror = () => showToast('Could not read that file', 'error');
    reader.readAsText(file);
}

function clearWorkspace() {
    if (!confirm('Delete ALL Nexus data in this browser (settings, calendar, history, logs)? Credentials are kept. This cannot be undone.')) return;
    BACKUP_KEYS.forEach(k => { if (k !== 'nexus_credentials') localStorage.removeItem(k); });
    AgentLog.warn('workspace', 'Workspace cleared by user');
    setTimeout(() => location.reload(), 500);
}
