/**
 * Nexus Chat · Task Calendar · Tasks
 * ===================================
 * Talks to the relay (agent/nexus_task_routes.py):
 *   POST /nexus/chat (SSE)  GET /tasks  GET /calendar  POST /tasks/<id>/{update,approve,cancel}
 *   POST /tasks/approve-all  GET /calendar.ics
 *
 * Nothing here posts. Approve = "the scheduler may post this at its time".
 * Pure helpers are exposed on window.NexusTasksUI.__internals for tests/test_tasks_ui.mjs.
 */
(function () {
    'use strict';

    const PLATFORMS = ['x', 'instagram', 'facebook', 'linkedin', 'youtube', 'blog'];
    const STATUS_LABEL = {
        draft: 'Draft', pending_approval: 'Needs approval', approved: 'Approved', running: 'Posting…',
        done: 'Posted', failed: 'Failed', cancelled: 'Cancelled',
    };
    const BRAIN_CHOICES = [
        ['settings', 'Brain from Settings'], ['lmstudio', 'LM Studio (local)'], ['ollama', 'Ollama (local)'],
        ['antigravity', 'Antigravity'], ['sho', 'Sho (external)'], ['openrouter', 'OpenRouter (Hermes 4)'],
    ];

    /* ── pure helpers ─────────────────────────────────────────────────────── */
    function esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    const pad = n => String(n).padStart(2, '0');
    const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

    /** Date part of a stored time. Stored times carry the user's offset, so the
     *  first 10 characters ARE the user's calendar day (no browser-tz shifting). */
    const dayOf = iso => (iso ? String(iso).slice(0, 10) : '');
    const timeOf = iso => (iso ? String(iso).slice(11, 16) : '');
    /** value for <input type=datetime-local> */
    const toLocalInput = iso => (iso ? String(iso).slice(0, 16) : '');

    /** Weeks (Mon-first) covering a month: array of arrays of 'YYYY-MM-DD'. */
    function monthGrid(year, month) {
        const first = new Date(year, month, 1);
        const start = new Date(year, month, 1 - ((first.getDay() + 6) % 7));
        const weeks = [];
        const d = new Date(start);
        do {
            const w = [];
            for (let i = 0; i < 7; i++) { w.push(ymd(d)); d.setDate(d.getDate() + 1); }
            weeks.push(w);
        } while (d.getMonth() === month && weeks.length < 6);
        return weeks;
    }
    function weekOf(dateStr) {
        const [y, m, dd] = dateStr.split('-').map(Number);
        const d = new Date(y, m - 1, dd);
        d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
        const out = [];
        for (let i = 0; i < 7; i++) { out.push(ymd(d)); d.setDate(d.getDate() + 1); }
        return out;
    }
    function groupByDay(tasks) {
        const out = {};
        for (const t of tasks || []) {
            if (!t.scheduled_at || t.status === 'cancelled') continue;
            (out[dayOf(t.scheduled_at)] ||= []).push(t);
        }
        for (const k of Object.keys(out)) out[k].sort((a, b) => String(a.scheduled_at).localeCompare(String(b.scheduled_at)));
        return out;
    }
    /** Keep the time (and offset) of a task, move it to another day. */
    function moveToDay(iso, newDay) {
        if (!iso) return `${newDay}T10:00`;
        return newDay + String(iso).slice(10);
    }
    /** Split an SSE buffer into complete events; returns {events, rest}. */
    function parseSSE(buf) {
        const events = [];
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
            const block = buf.slice(0, i); buf = buf.slice(i + 2);
            let ev = 'message', data = '';
            for (const line of block.split('\n')) {
                if (line.startsWith('event:')) ev = line.slice(6).trim();
                else if (line.startsWith('data:')) data += line.slice(5).trim();
            }
            let parsed = data;
            try { parsed = JSON.parse(data); } catch { /* raw */ }
            events.push([ev, parsed]);
        }
        return { events, rest: buf };
    }
    /** Map the app's brain settings (ai-client.js config) to a relay brain config. */
    function brainFromSettings(cfg, choice, getProvider) {
        cfg = cfg || {};
        const pick = choice && choice !== 'settings' ? choice : null;
        if (pick === 'lmstudio' || pick === 'ollama') return { provider: pick, base_url: '', model: '' };
        if (pick === 'antigravity') return { provider: 'antigravity', model: cfg.antigravityModel || '' };
        if (pick === 'sho') return { provider: 'sho' };
        if (pick === 'openrouter') {
            const p = getProvider?.('openrouter') || {};
            return { provider: 'openrouter', model: p.model || 'nousresearch/hermes-4-405b', api_key: p.apiKey || '' };
        }
        switch (cfg.provider) {
            case 'local':
                return { provider: cfg.localType === 'ollama' ? 'ollama' : 'lmstudio',
                         base_url: (cfg.localUrl || '').replace(/\/+$/, ''), model: cfg.localModel || '' };
            case 'cloud': {
                let id = String(cfg.cloudProvider || 'gemini').toLowerCase();
                if (id === 'anthropic') id = 'claude';
                const p = getProvider?.(id === 'claude' ? 'anthropic' : id) || {};
                return { provider: id, model: cfg.cloudModel || p.model || '', api_key: cfg.cloudApiKey || p.apiKey || '' };
            }
            case 'antigravity': return { provider: 'antigravity', model: cfg.antigravityModel || '' };
            case 'sho': return { provider: 'sho' };
            default: return { provider: 'lmstudio', base_url: '', model: '' };
        }
    }
    function badge(status) {
        return `<span class="nt-badge nt-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
    }
    function taskCardHtml(t) {
        const when = t.scheduled_at ? `${dayOf(t.scheduled_at)} ${timeOf(t.scheduled_at)}` : 'not scheduled';
        const plats = (t.platforms || []).map(p => `<span class="nt-plat">${esc(p)}</span>`).join(' ');
        const canApprove = ['pending_approval', 'draft', 'failed'].includes(t.status);
        const editable = !['running', 'done'].includes(t.status);
        return `<div class="nt-card" data-task="${esc(t.id)}">
            <div class="nt-card-head">${badge(t.status)} <strong>${esc(t.title || '')}</strong></div>
            <div class="nt-meta"><i class="ph ph-clock"></i> ${esc(when)} ${plats}</div>
            ${t.content ? `<div class="nt-content">${esc(t.content)}</div>` : ''}
            ${t.poster_idea ? `<div class="nt-poster"><i class="ph ph-image"></i> ${esc(t.poster_idea)}</div>` : ''}
            ${t.error ? `<div class="nt-error">${esc(t.error)}</div>` : ''}
            <div class="nt-actions">
                ${canApprove ? `<button class="btn btn-primary btn-sm" data-act="approve">Approve</button>` : ''}
                ${t.status === 'approved' ? `<button class="btn btn-ghost btn-sm" data-act="unapprove">Unapprove</button>` : ''}
                ${editable ? `<button class="btn btn-ghost btn-sm" data-act="edit">Edit</button>
                <input type="datetime-local" class="nt-when" value="${esc(toLocalInput(t.scheduled_at))}" aria-label="Schedule">
                <button class="btn btn-ghost btn-sm" data-act="schedule">Schedule</button>
                <button class="btn btn-ghost btn-sm" data-act="cancel">Cancel</button>` : ''}
            </div></div>`;
    }

    /* ── relay plumbing (uses script.js helpers) ───────────────────────────── */
    const call = (path, body, opts = {}) => window.relayCall(path, { method: body ? 'POST' : 'GET', body, ...opts });
    const toast = (m, k = 'info') => (window.showToast ? window.showToast(m, k) : console.log(m));

    async function act(id, action, extra = {}) {
        try {
            const r = await call(`/tasks/${encodeURIComponent(id)}/${action}`, extra);
            toast(`Task ${STATUS_LABEL[r.task.status] || r.task.status}`, 'success');
            return r.task;
        } catch (e) { toast(e.message, 'error'); return null; }
    }

    /** One click handler for every task card on any page. */
    async function onCardClick(ev, refresh) {
        const btn = ev.target.closest('[data-act]');
        if (!btn) return;
        const card = btn.closest('[data-task]');
        const id = card?.dataset.task;
        if (!id) return;
        const a = btn.dataset.act;
        let t = null;
        if (a === 'approve') {
            const when = card.querySelector('.nt-when')?.value;
            t = await act(id, 'approve', when ? { scheduled_at: when } : {});
        } else if (a === 'unapprove' || a === 'cancel') {
            if (a === 'cancel' && !confirm('Cancel this task?')) return;
            t = await act(id, a);
        } else if (a === 'schedule') {
            const when = card.querySelector('.nt-when')?.value;
            if (!when) return toast('Pick a date and time first', 'error');
            t = await act(id, 'update', { scheduled_at: when });
        } else if (a === 'edit') {
            t = await editTask(id);
        }
        if (t) {
            card.outerHTML = taskCardHtml(t);
            refresh?.();
        }
    }

    async function editTask(id) {
        let t;
        try { t = (await call(`/tasks/${encodeURIComponent(id)}`)).task; } catch (e) { toast(e.message, 'error'); return null; }
        return new Promise(resolve => {
            const wrap = document.createElement('div');
            wrap.className = 'modal-overlay';
            wrap.innerHTML = `<div class="modal-card glass-card nt-modal">
                <h3>Edit task</h3>
                <label>Title<input class="form-input" name="title" value="${esc(t.title)}"></label>
                <label>Post text<textarea class="form-input" name="content" rows="7">${esc(t.content)}</textarea></label>
                <label>Poster idea<input class="form-input" name="poster_idea" value="${esc(t.poster_idea || '')}"></label>
                <label>When<input class="form-input" type="datetime-local" name="scheduled_at" value="${esc(toLocalInput(t.scheduled_at))}"></label>
                <div class="nt-plats">${PLATFORMS.map(p => `<label><input type="checkbox" value="${p}" ${t.platforms.includes(p) ? 'checked' : ''}> ${p}</label>`).join(' ')}</div>
                <div class="nt-actions"><button class="btn btn-primary btn-sm" data-m="save">Save</button>
                <button class="btn btn-ghost btn-sm" data-m="close">Close</button></div></div>`;
            document.body.appendChild(wrap);
            wrap.addEventListener('click', async e => {
                const m = e.target.dataset.m;
                if (e.target === wrap || m === 'close') { wrap.remove(); return resolve(null); }
                if (m !== 'save') return;
                const v = n => wrap.querySelector(`[name=${n}]`).value;
                const fields = { title: v('title'), content: v('content'), poster_idea: v('poster_idea'),
                    platforms: [...wrap.querySelectorAll('.nt-plats input:checked')].map(i => i.value) };
                if (v('scheduled_at')) fields.scheduled_at = v('scheduled_at');
                const r = await act(id, 'update', fields);
                if (r) { wrap.remove(); resolve(r); }
            });
        });
    }

    /* ── Chat page ─────────────────────────────────────────────────────────── */
    const chat = { history: [], busy: false };

    function chatAppend(role, html) {
        const log = document.getElementById('ntChatLog');
        const div = document.createElement('div');
        div.className = `nt-msg nt-${role}`;
        div.innerHTML = html;
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
        return div;
    }

    async function sendChat() {
        const input = document.getElementById('ntChatInput');
        const message = input.value.trim();
        if (!message || chat.busy) return;
        chat.busy = true;
        input.value = '';
        chatAppend('user', esc(message));
        const choice = document.getElementById('ntBrain')?.value || 'settings';
        const brain = brainFromSettings(window.NexusAI?.config, choice, id => window.NexusAI?.getProvider?.(id));
        const out = chatAppend('assistant', `<div class="nt-status">Thinking with ${esc(brain.provider)}…</div>`);
        const cards = document.createElement('div');
        cards.className = 'nt-cards';
        let answer = '';
        const created = [];
        try {
            await window.relayStream('/nexus/chat', {
                body: { message, history: chat.history.slice(-10), brain },
                onEvent: (ev, data) => {
                    const st = out.querySelector('.nt-status');
                    if (ev === 'meta' && st) st.textContent = `Brain: ${data.model || data.brain}${data.mode ? ` · ${data.mode}` : ''}`;
                    else if (ev === 'tool_call' && st) st.textContent = `Running ${data.name}…`;
                    else if (ev === 'task') { created.push(data.task); cards.insertAdjacentHTML('beforeend', taskCardHtml(data.task)); out.appendChild(cards); }
                    else if (ev === 'warning') out.insertAdjacentHTML('beforeend', `<div class="nt-warn">${esc(data.text)}</div>`);
                    else if (ev === 'text') { answer = data.text || ''; out.insertAdjacentHTML('afterbegin', `<div class="nt-answer">${window.renderMarkdown ? window.renderMarkdown(answer) : esc(answer)}</div>`); }
                    else if (ev === 'error') out.insertAdjacentHTML('beforeend', `<div class="nt-error">${esc(data.error || data)}</div>`);
                },
            });
            if (created.length > 1) {
                out.insertAdjacentHTML('beforeend', `<button class="btn btn-primary btn-sm nt-approve-all">Approve all ${created.length}</button>`);
                out.querySelector('.nt-approve-all').onclick = async () => {
                    try {
                        const r = await call('/tasks/approve-all', { ids: created.map(t => t.id) });
                        toast(`Approved ${r.approved.length}${Object.keys(r.errors).length ? `, ${Object.keys(r.errors).length} need edits` : ''}`, 'success');
                    } catch (e) { toast(e.message, 'error'); }
                };
            }
        } catch (e) {
            out.insertAdjacentHTML('beforeend', `<div class="nt-error">${esc(e.message)} — is the relay running?</div>`);
        } finally {
            chat.history.push({ role: 'user', content: message });
            if (answer) chat.history.push({ role: 'assistant', content: answer });
            chat.busy = false;
        }
    }

    function initChat() {
        const page = document.getElementById('page-chat');
        if (!page || page.dataset.ready) return;
        page.dataset.ready = '1';
        const sel = document.getElementById('ntBrain');
        sel.innerHTML = BRAIN_CHOICES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('');
        sel.value = localStorage.getItem('nexus_chat_brain') || 'settings';
        sel.onchange = () => localStorage.setItem('nexus_chat_brain', sel.value);
        document.getElementById('ntChatSend').onclick = sendChat;
        document.getElementById('ntChatInput').addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
        });
        document.getElementById('ntChatLog').addEventListener('click', e => onCardClick(e));
        page.querySelectorAll('[data-example]').forEach(b => b.onclick = () => {
            document.getElementById('ntChatInput').value = b.dataset.example;
            document.getElementById('ntChatInput').focus();
        });
    }

    /* ── Task Calendar page ────────────────────────────────────────────────── */
    const cal = { view: 'month', cursor: ymd(new Date()), tasks: [] };

    async function loadCalendar() {
        const [y, m] = cal.cursor.split('-').map(Number);
        const days = cal.view === 'month' ? monthGrid(y, m - 1).flat() : weekOf(cal.cursor);
        try {
            const r = await call(`/calendar?start=${days[0]}&end=${days[days.length - 1]}`);
            cal.tasks = Object.values(r.days || {}).flat();
            document.getElementById('ntCalTz').textContent = r.timezone || '';
        } catch (e) { cal.tasks = []; toast(`Calendar: ${e.message}`, 'error'); }
        renderCalendar();
    }

    function renderCalendar() {
        const grid = document.getElementById('ntCalGrid');
        const [y, m] = cal.cursor.split('-').map(Number);
        const weeks = cal.view === 'month' ? monthGrid(y, m - 1) : [weekOf(cal.cursor)];
        const byDay = groupByDay(cal.tasks);
        const today = ymd(new Date());
        document.getElementById('ntCalLabel').textContent = cal.view === 'month'
            ? new Date(y, m - 1, 1).toLocaleString(undefined, { month: 'long', year: 'numeric' })
            : `Week of ${weeks[0][0]}`;
        const head = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => `<div class="nt-dow">${d}</div>`).join('');
        grid.className = `nt-cal nt-${cal.view}`;
        grid.innerHTML = head + weeks.flat().map(d => `<div class="nt-day${d === today ? ' nt-today' : ''}${Number(d.slice(5, 7)) !== m && cal.view === 'month' ? ' nt-out' : ''}" data-day="${d}">
            <div class="nt-daynum">${Number(d.slice(8))}</div>
            ${(byDay[d] || []).map(t => `<div class="nt-chip nt-${esc(t.status)}" draggable="${!['running', 'done'].includes(t.status)}" data-id="${esc(t.id)}" title="${esc(t.content || t.title)}">
                ${esc(timeOf(t.scheduled_at))} ${esc((t.platforms || []).join('/'))} · ${esc(t.title)}</div>`).join('')}
        </div>`).join('');
        const pending = cal.tasks.filter(t => t.status === 'pending_approval').length;
        document.getElementById('ntCalPending').textContent = pending ? `${pending} need approval` : 'nothing waiting for approval';
    }

    function initCalendar() {
        const page = document.getElementById('page-taskcal');
        if (!page) return;
        if (!page.dataset.ready) {
            page.dataset.ready = '1';
            const shift = dir => {
                const [y, m, d] = cal.cursor.split('-').map(Number);
                const dt = cal.view === 'month' ? new Date(y, m - 1 + dir, 1) : new Date(y, m - 1, d + 7 * dir);
                cal.cursor = ymd(dt); loadCalendar();
            };
            document.getElementById('ntCalPrev').onclick = () => shift(-1);
            document.getElementById('ntCalNext').onclick = () => shift(1);
            document.getElementById('ntCalToday').onclick = () => { cal.cursor = ymd(new Date()); loadCalendar(); };
            document.getElementById('ntCalView').onchange = e => { cal.view = e.target.value; loadCalendar(); };
            document.getElementById('ntCalIcs').onclick = () => {
                const tok = window.relayToken?.() || '';
                window.open(`${window.relayUrl()}/calendar.ics${tok ? `?token=${encodeURIComponent(tok)}` : ''}`, '_blank');
            };
            document.getElementById('ntCalApproveAll').onclick = async () => {
                const ids = cal.tasks.filter(t => t.status === 'pending_approval').map(t => t.id);
                if (!ids.length || !confirm(`Approve ${ids.length} task(s)? Approved posts are published automatically at their time.`)) return;
                try { const r = await call('/tasks/approve-all', { ids }); toast(`Approved ${r.approved.length}`, 'success'); } catch (e) { toast(e.message, 'error'); }
                loadCalendar();
            };
            const grid = document.getElementById('ntCalGrid');
            grid.addEventListener('dragstart', e => { const c = e.target.closest('.nt-chip'); if (c) e.dataTransfer.setData('text/plain', c.dataset.id); });
            grid.addEventListener('dragover', e => { if (e.target.closest('.nt-day')) e.preventDefault(); });
            grid.addEventListener('drop', async e => {
                const day = e.target.closest('.nt-day')?.dataset.day;
                const id = e.dataTransfer.getData('text/plain');
                const t = cal.tasks.find(x => x.id === id);
                if (!day || !t || dayOf(t.scheduled_at) === day) return;
                e.preventDefault();
                if (await act(id, 'update', { scheduled_at: moveToDay(t.scheduled_at, day) })) loadCalendar();
            });
            grid.addEventListener('click', async e => {
                const c = e.target.closest('.nt-chip');
                if (!c) return;
                const t = cal.tasks.find(x => x.id === c.dataset.id);
                const detail = document.getElementById('ntCalDetail');
                detail.innerHTML = taskCardHtml(t);
                detail.classList.remove('hidden');
            });
            document.getElementById('ntCalDetail').addEventListener('click', e => onCardClick(e, loadCalendar));
        }
        loadCalendar();
    }

    /* ── Tasks page ────────────────────────────────────────────────────────── */
    async function loadTasks() {
        const status = document.getElementById('ntTaskStatus').value;
        const list = document.getElementById('ntTaskList');
        try {
            const r = await call(`/tasks${status ? `?status=${status}` : ''}`);
            list.innerHTML = r.tasks.length ? r.tasks.map(taskCardHtml).join('') : '<p class="helper-text">No tasks yet. Ask for some in Nexus Chat.</p>';
        } catch (e) { list.innerHTML = `<div class="nt-error">${esc(e.message)}</div>`; }
    }
    function initTasks() {
        const page = document.getElementById('page-tasks');
        if (!page) return;
        if (!page.dataset.ready) {
            page.dataset.ready = '1';
            document.getElementById('ntTaskStatus').onchange = loadTasks;
            document.getElementById('ntTaskRefresh').onclick = loadTasks;
            document.getElementById('ntTaskList').addEventListener('click', e => onCardClick(e));
            document.getElementById('ntTaskNew').onclick = async () => {
                const title = prompt('Task title');
                if (!title) return;
                try { await call('/tasks', { title, status: 'draft' }); loadTasks(); } catch (e) { toast(e.message, 'error'); }
            };
        }
        loadTasks();
    }

    function onPage(id) {
        if (id === 'chat') initChat();
        if (id === 'taskcal') initCalendar();
        if (id === 'tasks') initTasks();
    }

    window.NexusTasksUI = {
        onPage,
        __internals: { esc, monthGrid, weekOf, groupByDay, moveToDay, parseSSE, brainFromSettings, taskCardHtml, dayOf, timeOf, toLocalInput },
    };
})();
