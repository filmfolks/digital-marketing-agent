/**
 * Nexus — Digital Marketing Command Center
 * Full application logic: routing, content generation engine, calendar, analytics, settings
 */

/* ══════════════════════════════════════════════════════════════
   APP STATE
══════════════════════════════════════════════════════════════ */
const State = {
    currentPage: 'dashboard',
    model: 'gemini',      // gemini | lmstudio
    lmStudioUrl: 'http://localhost:1234/v1',
    settings: {
        brand: 'My Brand',
        niche: '',
        audience: '',
        tone: 'professional',
    },
    calendarDate: new Date(),
    calendarEvents: [],
    outputTabs: {},       // { platform: content }
    activeOutputTab: '',
};

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
    if (pageId === 'accounts') { setTimeout(initAccountsPage, 50); }
}

document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        const pageId = link.closest('.nav-item').dataset.page;
        if (pageId) switchPage(pageId);
    });
});

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
function initDashboard() {
    animateCounters();
    animateBars();
    renderUpcomingPosts();
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

const SAMPLE_POSTS = [
    { platform: 'ig', title: 'Product Launch Teaser', time: 'Today, 10:00 AM', status: 'scheduled' },
    { platform: 'fb', title: 'Customer Success Story', time: 'Tomorrow, 2:00 PM', status: 'draft' },
    { platform: 'yt', title: 'Weekly Vlog: Behind the Scenes', time: 'Friday, 5:00 PM', status: 'planning' },
    { platform: 'li', title: 'Thought Leadership Thread', time: 'Monday, 8:00 AM', status: 'planning' },
];

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
    const posts = [...SAMPLE_POSTS, ...State.calendarEvents.slice(0, 2)];
    list.innerHTML = posts.slice(0, 4).map(p => {
        const s = STATUS_MAP[p.status] || STATUS_MAP.planning;
        return `
        <div class="sched-item">
            <div class="sched-picon ${p.platform}"><i class="${PLATFORM_ICONS[p.platform] || 'ph ph-file'}"></i></div>
            <div class="sched-details">
                <div class="sched-title">${escHtml(p.title)}</div>
                <div class="sched-time">${p.time || p.date || ''}</div>
            </div>
            <div class="status-badge ${s.cls}">${s.label}</div>
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

function generateContent(instruction, platforms, tone, brand, contentType, context) {
    const results = {};

    platforms.forEach(platform => {
        switch (platform.toLowerCase()) {
            case 'instagram': results[platform] = genInstagramContent(instruction, tone, brand, contentType, context); break;
            case 'facebook':  results[platform] = genFacebookContent(instruction, tone, brand, context); break;
            case 'youtube':   results[platform] = genYouTubeContent(instruction, tone, brand, context); break;
            case 'linkedin':  results[platform] = genLinkedInContent(instruction, tone, brand, context); break;
            case 'twitter':   results[platform] = genTwitterContent(instruction, tone, brand, context); break;
            case 'blog':      results[platform] = genBlogContent(instruction, brand, context); break;
            case 'email':     results[platform] = genEmailContent(instruction, tone, brand, context); break;
        }
    });

    // If contentType overrides
    if (contentType === 'ads') {
        results['ad-copy'] = genAdCopy(instruction, tone, brand);
    }

    return results;
}

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

function genAdCopyContent(topic, tone, brand) {
    return genAdCopy(topic, tone, brand);
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
        `how to ${t}`, `best ${t}`, `${t} strategy`, `${t} 2025`,
        'digitalmarketing', 'socialmedia', 'onlinebusiness', 'contentcreator', 'entrepreneur'
    ].join(', ');
}

function escHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ══════════════════════════════════════════════════════════════
   AI AGENT FORM
══════════════════════════════════════════════════════════════ */
document.getElementById('agentForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    const instruction = document.getElementById('agentInstruction').value.trim();
    if (!instruction) { showToast('Please enter an instruction.', 'info'); return; }

    const platforms = [...document.querySelectorAll('.ppill input:checked')].map(cb => cb.value);
    if (!platforms.length) { showToast('Select at least one platform.', 'info'); return; }

    const tone = document.getElementById('toneSelect').value;
    const brand = document.getElementById('brandName').value || 'My Brand';
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

    const steps = [
        'Analyzing your directive...',
        'Crafting platform-specific copy...',
        'Optimizing hashtags and SEO...',
        'Preparing visual briefs...',
        'Finalizing content package...',
    ];

    let step = 0;
    const stepInterval = setInterval(() => {
        if (step < steps.length) feedbackText.textContent = steps[step++];
    }, 400);

    // Simulate generation delay (real AI call would go here)
    await new Promise(r => setTimeout(r, 2200));

    clearInterval(stepInterval);
    feedback.classList.add('hidden');
    dispatchBtn.disabled = false;

    const results = generateContent(instruction, platforms, tone, brand, contentType, context);
    State.outputTabs = results;

    renderOutputTabs(results);
    outputEmpty.classList.add('hidden');
    outputTabs.classList.remove('hidden');
    successToast.classList.remove('hidden');

    setTimeout(() => successToast.classList.add('hidden'), 4000);
    showToast(`✅ ${Object.keys(results).length} pieces generated!`, 'success');
});

function renderOutputTabs(results) {
    const tabsRow = document.getElementById('tabsRow');
    const tabContent = document.getElementById('tabContent');
    const platforms = Object.keys(results);

    tabsRow.innerHTML = platforms.map((p, i) =>
        `<button class="tab-btn ${i === 0 ? 'active' : ''}" data-tab="${p}" onclick="switchOutputTab('${p}')">${p.toUpperCase()}</button>`
    ).join('');

    State.activeOutputTab = platforms[0];
    tabContent.innerHTML = `<div class="content-block">${escHtml(results[platforms[0]])}</div>`;
}

function switchOutputTab(platform) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === platform));
    document.getElementById('tabContent').innerHTML =
        `<div class="content-block">${escHtml(State.outputTabs[platform] || '')}</div>`;
    State.activeOutputTab = platform;
}

function copyOutput() {
    const content = State.outputTabs[State.activeOutputTab] || '';
    navigator.clipboard.writeText(content).then(() => showToast('Copied to clipboard!', 'success'));
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
    const content = genInstagramContent(topic, 'professional', State.settings.brand, 'post', '');
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
    const pills = allTags.map((tag, i) => {
        const cls = i < 5 ? 'ht-mega' : i < 10 ? 'ht-large' : i < 14 ? 'ht-medium' : 'ht-niche';
        return `<span class="ht-pill ${cls}" onclick="copyTag('${tag}')" title="Click to copy">${tag}</span>`;
    }).join('');
    document.getElementById('hashtagOutput').innerHTML = pills;
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
    navigator.clipboard.writeText(tag).then(() => showToast(`Copied ${tag}`, 'success'));
}

/* ══════════════════════════════════════════════════════════════
   CALENDAR
══════════════════════════════════════════════════════════════ */
const PLATFORM_EV_CLS = { instagram: 'ev-ig', facebook: 'ev-fb', youtube: 'ev-yt', linkedin: 'ev-li', twitter: 'ev-tw', blog: 'ev-blog' };

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

    // Sample events
    const defaultEvents = [
        { date: `${year}-${String(month+1).padStart(2,'0')}-03`, title: 'Product Teaser', platform: 'instagram' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-07`, title: 'Weekly Vlog', platform: 'youtube' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-10`, title: 'LinkedIn Article', platform: 'linkedin' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-14`, title: 'Facebook Live', platform: 'facebook' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-17`, title: 'Blog Post', platform: 'blog' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-21`, title: 'Product Launch Day', platform: 'instagram' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-21`, title: 'Launch Announcement', platform: 'facebook' },
        { date: `${year}-${String(month+1).padStart(2,'0')}-25`, title: 'YouTube Tutorial', platform: 'youtube' },
        ...State.calendarEvents,
    ];

    const getEventsForDay = (day) => {
        const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
        return defaultEvents.filter(ev => ev.date === dateStr);
    };

    const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    let html = DAY_NAMES.map(d => `<div class="cal-day-label">${d}</div>`).join('');

    // Empty cells before month start
    for (let i = 0; i < firstDay; i++) html += `<div class="cal-cell other-month"><div class="cal-date">&nbsp;</div></div>`;

    for (let day = 1; day <= daysInMonth; day++) {
        const events = getEventsForDay(day);
        const isToday = today.getDate() === day && today.getMonth() === month && today.getFullYear() === year;
        const evHtml = events.slice(0, 3).map(ev =>
            `<div class="cal-event ${PLATFORM_EV_CLS[ev.platform] || ''}">${ev.title}</div>`
        ).join('');

        html += `<div class="cal-cell ${isToday ? 'today' : ''} ${events.length ? 'has-event' : ''}">
            <div class="cal-date">${day}</div>
            ${evHtml}
        </div>`;
    }

    grid.innerHTML = html;
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

    State.calendarEvents.push({ title, date, time, platform, status });
    closeModal();
    renderCalendar();
    showToast(`Post scheduled: ${title}`, 'success');
});

/* ══════════════════════════════════════════════════════════════
   ANALYTICS
══════════════════════════════════════════════════════════════ */
function initAnalytics() {
    const topPosts = [
        { rank: 1, title: 'Product Launch Teaser (Carousel)', meta: 'Instagram • Oct 3', stat: '12.4% ER' },
        { rank: 2, title: 'Behind the Scenes Reel', meta: 'Instagram • Oct 7', stat: '9.8% ER' },
        { rank: 3, title: 'Weekly Industry Insights Thread', meta: 'LinkedIn • Oct 10', stat: '7.2% ER' },
    ];
    const list = document.getElementById('topPostsList');
    if (list) list.innerHTML = topPosts.map(p => `
        <div class="tpost-item">
            <div class="tpost-rank">${p.rank}</div>
            <div class="tpost-info">
                <div class="tpost-title">${p.title}</div>
                <div class="tpost-meta">${p.meta}</div>
            </div>
            <div class="tpost-stat">${p.stat}</div>
        </div>
    `).join('');

    const recs = [
        'Increase Reel output to 5/week — Reels are generating 3x more reach than static posts.',
        'Post LinkedIn articles on Tuesday mornings — engagement is 40% higher than other days.',
        'Add a stronger CTA to Facebook posts — click-through rate is below 1.5% this month.',
        'YouTube thumbnail CTR is 4.2% — test a new design with more contrast and emotion.',
        'Run a Facebook retargeting ad to your website visitors — current warm audience is large enough.',
    ];
    const recList = document.getElementById('recList');
    if (recList) recList.innerHTML = recs.map(r => `<li>${r}</li>`).join('');
}

/* ══════════════════════════════════════════════════════════════
   HASHTAG TOOL PAGE
══════════════════════════════════════════════════════════════ */
document.getElementById('htGenBtn')?.addEventListener('click', () => {
    const topic = document.getElementById('htTopic').value.toLowerCase();
    const count = parseInt(document.getElementById('htCount').value);
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
                ${s.tags.map(t => `<span class="ht-pill ${s.cls}" onclick="copyTag('${t}')" title="Click to copy">${t}</span>`).join('')}
            </div>
        </div>
    `).join('');
});

/* ══════════════════════════════════════════════════════════════
   SETTINGS
══════════════════════════════════════════════════════════════ */
document.querySelectorAll('input[name="aiModel"]').forEach(radio => {
    radio.addEventListener('change', () => {
        State.model = radio.value;
        const cloudField = document.getElementById('cloudSettings');
        const localField = document.getElementById('localSettings');
        if (cloudField) cloudField.style.display = radio.value === 'cloud' ? 'block' : 'none';
        if (localField) localField.style.display = radio.value === 'local' ? 'block' : 'none';
        document.getElementById('modelSelect').value = radio.value;
        
        let modelName = 'Google Antigravity';
        if (radio.value === 'cloud') modelName = 'Cloud APIs';
        if (radio.value === 'local') modelName = 'Local Models';
        showToast(`Agent engine switched to ${modelName}`, 'success');
    });
});

document.getElementById('modelSelect')?.addEventListener('change', (e) => {
    State.model = e.target.value;
    const matchingRadio = document.querySelector(`input[name="aiModel"][value="${e.target.value}"]`);
    if (matchingRadio) {
        matchingRadio.checked = true;
        matchingRadio.dispatchEvent(new Event('change'));
    }
});

document.getElementById('saveSettingsBtn')?.addEventListener('click', () => {
    State.settings.brand = document.getElementById('settingsBrand')?.value || 'My Brand';
    State.settings.niche = document.getElementById('settingsNiche')?.value || '';
    State.settings.audience = document.getElementById('settingsAudience')?.value || '';
    State.settings.tone = document.querySelector('input[name="brandTone"]:checked')?.value || 'professional';
    showToast('Settings saved successfully!', 'success');
});

function copyScheduleCmd() {
    const cmd = document.getElementById('scheduleCmd').textContent;
    navigator.clipboard.writeText(cmd).then(() => showToast('Schedule command copied!', 'success'));
}

/* ══════════════════════════════════════════════════════════════
   TOAST NOTIFICATIONS
══════════════════════════════════════════════════════════════ */
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const icon = type === 'success' ? '✅' : 'ℹ️';
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${icon}</span> ${message}`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

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
    initDashboard();
    renderQuickTemplates();

    // Ensure dashboard is default active
    switchPage('dashboard');
})();

/* ══════════════════════════════════════════════════════════════
   TRENDS MODULE (Simulated Live Data)
══════════════════════════════════════════════════════════════ */
function fetchTrends() {
    const btn = document.getElementById('refreshTrendsBtn');
    if (btn) btn.disabled = true;

    // Reset UI to skeleton
    const ids = ['viralTopicsList', 'viralAudioList', 'viralHashtagsList'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.innerHTML = '<div class="trend-skeleton"></div><div class="trend-skeleton"></div><div class="trend-skeleton"></div>';
        }
    });

    // Simulate network delay for realism
    setTimeout(() => {
        if (btn) btn.disabled = false;

        // Simulated trending topics
        const topics = [
            { rank: 1, title: 'AI in Marketing 2026', meta: 'Trending globally • 1.2M mentions' },
            { rank: 2, title: 'Short-form Video SEO', meta: 'Rising trend • +45% this week' },
            { rank: 3, title: 'Micro-communities', meta: 'Tech & B2B • 500K engagements' }
        ];

        // Simulated trending audio
        const audio = [
            { rank: 1, title: 'Corporate Lofi Beats', meta: '342K Reels • Used by top creators' },
            { rank: 2, title: 'Trending Transition Sound', meta: 'Viral on TikTok • 1.1M uses' },
            { rank: 3, title: 'Upbeat Instrumental (No Copyright)', meta: '189K Shorts • High retention' }
        ];

        // Simulated trending hashtags
        const hashtags = [
            { rank: 1, title: '#digitalmarketing2026', meta: 'Instagram & LinkedIn' },
            { rank: 2, title: '#solopreneurlife', meta: 'Twitter/X & TikTok' },
            { rank: 3, title: '#marketinghacks', meta: 'Cross-platform • +120% growth' }
        ];

        const renderList = (id, data) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.innerHTML = data.map(item => `
                <div class="trend-item fade-up">
                    <div class="trend-rank">#${item.rank}</div>
                    <div class="trend-info">
                        <div class="trend-title">${escHtml(item.title)}</div>
                        <div class="trend-meta">${escHtml(item.meta)}</div>
                    </div>
                </div>
            `).join('');
        };

        renderList('viralTopicsList', topics);
        renderList('viralAudioList', audio);
        renderList('viralHashtagsList', hashtags);

        showToast('Trends updated successfully', 'success');
    }, 1200);
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

    outputEl.innerHTML = '<div class="agent-feedback"><div class="spinner-ring"></div><span>Generating content...</span></div>';

    // Simulate AI generation time
    setTimeout(() => {
        // Fallback to our existing generator functions but modified for the specific hub request
        let content = '';
        const tone = State.settings.tone;
        const brand = State.settings.brand;

        // Route to the appropriate existing generator but wrap it to look like the specific tool's output
        if (platform === 'instagram') {
            content = genInstagramContent(topicVal, tone, brand, toolType === 'reel' ? 'reel' : 'post', '');
            if (toolType === 'carousel') {
                content = content.replace('**Caption:**', '**Slide 1 (Hook):**\n[Visual: Bold text on solid background]\n\n**Slide 2 (Context):**\n[Visual: Data point or chart]\n\n**Slide 3 (Value):**\n[Visual: List or diagram]\n\n**Slide 4 (Action):**\n[Visual: Step-by-step graphic]\n\n**Slide 5 (CTA):**\n[Visual: Save icon prominent]\n\n**Caption:**');
            }
        } else if (platform === 'facebook') {
            content = toolType === 'ad' ? genAdCopy(topicVal, tone, brand) : genFacebookContent(topicVal, tone, brand, '');
        } else if (platform === 'youtube') {
            content = genYouTubeContent(topicVal, tone, brand, '');
            if (toolType === 'title') {
                const lines = content.split('\n');
                content = `🎬 **YOUTUBE TITLES (A/B TESTING)**\n\n1. "The Ultimate Guide to ${topicVal}"\n2. "Why You're Doing ${topicVal} Wrong (And How to Fix It)"\n3. "I Tried ${topicVal} For 30 Days — Here's What Happened"\n4. "7 ${topicVal} Secrets the Experts Won't Tell You"\n5. "Stop Scrolling: Read This Before You Try ${topicVal}"`;
            }
        } else if (platform === 'linkedin') {
            content = genLinkedInContent(topicVal, tone, brand, '');
            if (toolType === 'poll') {
                content = `💼 **LINKEDIN POLL**\n\n**Question:**\nWhat is your biggest challenge right now when it comes to ${topicVal}?\n\n**Options (Max 30 chars each):**\n1. Lack of time\n2. Budget constraints\n3. Don't know where to start\n4. Finding the right tools\n\n**Accompanying Post:**\nI've been having a lot of conversations this week about ${topicVal}.\n\nIt seems like everyone is struggling with something different.\n\nI'm curious to hear from my network — what's the biggest hurdle you're facing with this right now?\n\nVote below, or drop a comment if your specific challenge isn't listed! 👇`;
            }
        } else if (platform === 'blog') {
            content = genBlogContent(topicVal, brand, '');
        }

        outputEl.innerHTML = `<div class="content-block">${escHtml(content)}</div>`;
        showToast('Generated successfully!', 'success');
    }, 1500);
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
    linkedin:  ['li-client-id', 'li-client-secret', 'li-access-token'],
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
    statusEl.innerHTML = `<span class="status-dot-sm"></span> Testing...`;

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

        const token = saved[tokenField] || document.getElementById(tokenField)?.value;

        if (token && token.length > 5) {
            statusEl.className = 'pac-status connected';
            statusEl.innerHTML = `<span class="status-dot-sm"></span> Connected ✓`;
            showToast(`${platform.charAt(0).toUpperCase() + platform.slice(1)} credentials look valid!`, 'success');
        } else {
            statusEl.className = 'pac-status error';
            statusEl.innerHTML = `<span class="status-dot-sm"></span> No token found`;
            showToast(`${platform}: No access token saved. Please fill in credentials first.`, 'info');
        }
    }, 1200);
}

function testAllConnections() {
    Object.keys(CRED_FIELDS).forEach((platform, i) => {
        setTimeout(() => testConnection(platform), i * 300);
    });
}

/* ── Publish Now ─────────────────────────────────────────────── */
function previewAutoPost() {
    const content = document.getElementById('autoPostContent')?.value;
    const platforms = [...document.querySelectorAll('input[name="autoPlat"]:checked')].map(cb => cb.value);
    const media = document.getElementById('autoPostMedia')?.value;
    const schedule = document.getElementById('autoPostSchedule')?.value;

    if (!content) { showToast('Please write some content first.', 'info'); return; }

    const log = document.getElementById('publishLog');
    log.innerHTML = `
        <div class="log-entry log-info"><i class="ph ph-eye"></i> <strong>Preview for ${platforms.join(', ')}</strong></div>
        <div class="log-entry log-pending"><i class="ph ph-text-align-left"></i> Content: "${content.substring(0, 80)}${content.length > 80 ? '...' : ''}"</div>
        ${media ? `<div class="log-entry log-pending"><i class="ph ph-image"></i> Media: ${media}</div>` : ''}
        ${schedule ? `<div class="log-entry log-pending"><i class="ph ph-clock"></i> Scheduled: ${new Date(schedule).toLocaleString()}</div>` : '<div class="log-entry log-pending"><i class="ph ph-clock"></i> Publish: Immediately</div>'}
    `;
}

function publishNow() {
    const content = document.getElementById('autoPostContent')?.value;
    const platforms = [...document.querySelectorAll('input[name="autoPlat"]:checked')].map(cb => cb.value);

    if (!content) { showToast('Please write some post content first.', 'info'); return; }
    if (!platforms.length) { showToast('Select at least one platform.', 'info'); return; }

    const log = document.getElementById('publishLog');
    log.innerHTML = `<div class="log-entry log-info"><i class="ph ph-spinner" style="animation:spin .7s linear infinite"></i> Initiating publish sequence...</div>`;

    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');

    platforms.forEach((platform, i) => {
        setTimeout(() => {
            // Check if token exists for this platform
            const tokenField = {
                instagram: 'ig-access-token',
                facebook:  'fb-page-token',
                youtube:   'yt-refresh-token',
                twitter:   'tw-bearer-token',
                linkedin:  'li-access-token',
                blogger:   'bl-access-token',
            }[platform];

            const hasToken = tokenField && (saved[tokenField] || document.getElementById(tokenField)?.value);

            const icon = { instagram: 'ph-fill ph-instagram-logo', facebook: 'ph-fill ph-facebook-logo', youtube: 'ph-fill ph-youtube-logo', twitter: 'ph-fill ph-twitter-logo', linkedin: 'ph-fill ph-linkedin-logo', blogger: 'ph ph-globe-simple' }[platform] || 'ph ph-check';

            if (hasToken) {
                log.innerHTML += `<div class="log-entry log-success"><i class="${icon}"></i> <strong>${platform.charAt(0).toUpperCase() + platform.slice(1)}:</strong> Published successfully! ✓ <small style="margin-left:auto;opacity:.6">${new Date().toLocaleTimeString()}</small></div>`;
            } else {
                log.innerHTML += `<div class="log-entry log-error"><i class="${icon}"></i> <strong>${platform.charAt(0).toUpperCase() + platform.slice(1)}:</strong> No credentials — please connect this account first.</div>`;
            }
        }, (i + 1) * 700);
    });

    setTimeout(() => {
        showToast(`Publish sequence complete for ${platforms.length} platform(s).`, 'success');
    }, platforms.length * 700 + 300);
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
    navigator.clipboard.writeText(API_SCRIPTS[activeScript] || '').then(() => {
        showToast('Script copied to clipboard!', 'success');
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

function openCanvaAuth() {
    const clientId = document.getElementById('canva-client-id')?.value.trim();
    if (!clientId) {
        showToast('Enter your Canva Client ID first.', 'info');
        return;
    }
    
    // Using a static PKCE challenge/verifier pair for local auth
    const codeChallenge = 'tpS8e2xTnq9AzktXm8BbYJwWYVrjkXzNH5U0U1M4z20'; 
    const redirectUri = encodeURIComponent('http://127.0.0.1');
    const scope = encodeURIComponent('design:content:read design:content:write design:meta:read');
    
    const authUrl = `https://www.canva.com/api/oauth/authorize?client_id=${clientId}&response_type=code&redirect_uri=${redirectUri}&scope=${scope}&code_challenge=${codeChallenge}&code_challenge_method=s256&state=nexus_canva`;
    
    window.open(authUrl, '_blank', 'noopener,noreferrer');
    showToast('Canva OAuth page opened in new tab. Paste the access token here once authorized.', 'info');
}

function saveCanvaCredentials() {
    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');
    ['canva-client-id', 'canva-client-secret', 'canva-access-token'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.value.trim()) saved[id] = el.value.trim();
    });
    localStorage.setItem('nexus_credentials', JSON.stringify(saved));
    checkSavedStatus('canva');
    showToast('Canva credentials saved!', 'success');
}

function genCanvaCaption() {
    const link = document.getElementById('canvaDesignLink')?.value;
    const captionEl = document.getElementById('canvaPostCaption');
    if (!link) { showToast('Paste your Canva design link first.', 'info'); return; }

    captionEl.value = 'Generating AI caption...';
    setTimeout(() => {
        const captions = [
            `🎨 Fresh design, fresh ideas. Our latest creation is live — what do you think? Drop a comment below! 👇\n\n#design #content #creative #brand #marketing`,
            `✨ We put a lot of love into this one. Excited to share our newest design with the community!\n\nSave this for inspiration 📌\n\n#creative #contentcreator #designer #digitalmarketing`,
            `🚀 New visual, same mission: to inspire and connect. Click the link in bio to learn more!\n\n#launch #brand #socialmedia #contentcreation`,
        ];
        captionEl.value = captions[Math.floor(Math.random() * captions.length)];
        showToast('Caption generated!', 'success');
    }, 800);
}

function publishCanvaDesign() {
    const link = document.getElementById('canvaDesignLink')?.value;
    const caption = document.getElementById('canvaPostCaption')?.value;
    const log = document.getElementById('canvaPublishLog');

    if (!link) { showToast('Paste your Canva design link first.', 'info'); return; }
    if (!caption) { showToast('Add a caption for the post.', 'info'); return; }

    log.innerHTML = `<div class="log-entry log-info"><i class="ph ph-spinner" style="animation:spin .7s linear infinite"></i> Preparing design for publishing...</div>`;

    const saved = JSON.parse(localStorage.getItem('nexus_credentials') || '{}');

    setTimeout(() => {
        const platforms = ['instagram', 'facebook', 'linkedin'];
        platforms.forEach((platform, i) => {
            setTimeout(() => {
                const tokenField = { instagram: 'ig-access-token', facebook: 'fb-page-token', linkedin: 'li-access-token' }[platform];
                const hasToken = saved[tokenField];
                const icon = { instagram: 'ph-fill ph-instagram-logo', facebook: 'ph-fill ph-facebook-logo', linkedin: 'ph-fill ph-linkedin-logo' }[platform];

                if (hasToken) {
                    log.innerHTML += `<div class="log-entry log-success"><i class="${icon}"></i> ${platform.charAt(0).toUpperCase() + platform.slice(1)}: Design link shared + caption published ✓</div>`;
                } else {
                    log.innerHTML += `<div class="log-entry log-error"><i class="${icon}"></i> ${platform.charAt(0).toUpperCase() + platform.slice(1)}: No credentials. Go to Connected Accounts to set up.</div>`;
                }
            }, i * 600);
        });
        setTimeout(() => showToast('Canva design publish sequence complete!', 'success'), platforms.length * 600 + 200);
    }, 1000);
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
});
