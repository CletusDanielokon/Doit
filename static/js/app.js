/* Do.iT — app logic (vanilla JS, no dependencies) */
(() => {
'use strict';
const boot = JSON.parse(document.getElementById('boot').textContent);
const A = boot.account || {};
const CSRF = document.querySelector('meta[name=csrf-token]').content;
const $ = (id) => document.getElementById(id);
const store = { get: (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };
const S = { tasks: boot.tasks, tt: boot.timetable || [], notes: boot.notes || [], tab: 'home', view: 'all', q: '', prio: '', tag: '', sort: 'smart',
  open: new Set(), editing: null, tview: 'list', cal: new Date(), calSel: new Date(),
  ttview: store.get('ttview', innerWidth < 700 ? 'day' : 'table'), ttDay: (new Date().getDay() + 6) % 7, ttEditing: null,
  noteId: null, nq: '' };
const PRIO = { high: 3, medium: 2, low: 1 };
const DAYS = ['sunday','monday','tuesday','wednesday','thursday','friday','saturday'];
const DN = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], DF = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const TABS = ['home', 'tasks', 'timetable', 'notes', 'me'];
const QUOTES = [['Well done is better than well said.', 'Benjamin Franklin'], ['Lost time is never found again.', 'Benjamin Franklin'],
  ['Energy and persistence conquer all things.', 'Benjamin Franklin'], ['Dost thou love life? Then do not squander time, for that is the stuff life is made of.', 'Benjamin Franklin'],
  ['A journey of a thousand miles begins with a single step.', 'Lao Tzu'], ['Well begun is half done.', 'Proverb'],
  ['We are what we repeatedly do. Excellence, then, is not an act, but a habit.', 'Will Durant']];

/* ---------- helpers ---------- */
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
const icon = (n) => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('class', 'ic');
  const u = document.createElementNS('http://www.w3.org/2000/svg', 'use'); u.setAttribute('href', '#i-' + n); s.append(u); return s; };
const p2 = (n) => String(n).padStart(2, '0');
const ymd = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
const ymdhm = (d) => `${ymd(d)}T${p2(d.getHours())}:${p2(d.getMinutes())}`;
const parseLocal = (s) => { if (!s) return null; const [d, t] = s.split('T'); const [y, m, dd] = d.split('-').map(Number);
  const [hh, mm] = (t || '0:0').split(':').map(Number); return new Date(y, m - 1, dd, hh, mm); };
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const dayDiff = (a, b) => Math.round((startOfDay(a) - startOfDay(b)) / 864e5);
const fmtTime = (d) => d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const fmtDay = (d) => d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
const fmtMin = (m) => fmtTime(new Date(2000, 0, 1, Math.floor(m / 60), m % 60));
const hhmm = (m) => `${p2(Math.floor(m / 60))}:${p2(m % 60)}`;
const todayIdx = (d = new Date()) => (d.getDay() + 6) % 7;
const nowMin = (d = new Date()) => d.getHours() * 60 + d.getMinutes();
const buzz = (p) => { if (navigator.vibrate && navigator.userActivation && navigator.userActivation.hasBeenActive) navigator.vibrate(p); };
const closeDlg = (d) => { if (d.open) d.close(); };
const showErr = (m, id = 'ed-error') => { $(id).textContent = m; $(id).hidden = false; };

async function api(method, url, body) {
  try {
    const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin' });
    if (r.status === 401) { location.href = '/login?next=/app'; return null; }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || 'Something went wrong.');
    return data;
  } catch (err) { toast(err.message || 'Network problem. Please try again.'); return null; }
}
function toast(msg, undo, label) {
  const t = h('div', { class: 'toast', role: 'status' }, msg);
  if (undo) t.append(h('button', { type: 'button', onclick: () => { undo(); t.remove(); } }, label ? label : [icon('undo'), 'Undo']));
  $('toasts').append(t); setTimeout(() => t.remove(), undo ? 7000 : 3200);
}
const put = (task) => { const i = S.tasks.findIndex((t) => t.id === task.id); i >= 0 ? (S.tasks[i] = task) : S.tasks.unshift(task); };
const emptyBox = (t, s, ...extra) => h('div', { class: 'empty' }, icon('check'), h('div', {}, h('b', {}, t)), h('div', {}, s), ...extra);

/* ---------- task state ---------- */
const due = (t) => parseLocal(t.due);
function state(t, now = new Date()) {
  if (t.completed_at) return 'done';
  const d = due(t); if (!d) return 'none';
  if (t.due_has_time ? d < now : dayDiff(d, now) < 0) return 'overdue';
  return dayDiff(d, now) === 0 ? 'today' : 'upcoming';
}
function dueLabel(t, now = new Date()) {
  const d = due(t); if (!d) return null; const n = dayDiff(d, now), st = state(t, now);
  const time = t.due_has_time ? ', ' + fmtTime(d) : '';
  if (st === 'overdue') return n === 0 ? 'Overdue · ' + fmtTime(d) : `Overdue ${-n} day${n === -1 ? '' : 's'}`;
  return n === 0 ? 'Today' + time : n === 1 ? 'Tomorrow' + time : fmtDay(d) + time;
}
const byDue = (a, b) => (due(a) || 8e15) - (due(b) || 8e15);
const smart = (a, b) => byDue(a, b) || PRIO[b.priority] - PRIO[a.priority];
function visible() {
  const now = new Date(), q = S.q.toLowerCase();
  const list = S.tasks.filter((t) => {
    const st = state(t, now);
    if (S.view === 'done' ? st !== 'done' : st === 'done') return false;
    if (S.view === 'today' && st !== 'today' && st !== 'overdue') return false;
    if (S.view === 'upcoming' && st !== 'upcoming') return false;
    if (S.view === 'overdue' && st !== 'overdue') return false;
    if (S.prio && t.priority !== S.prio) return false;
    if (S.tag && !t.tags.some((x) => x.toLowerCase() === S.tag)) return false;
    return !q || (t.title + ' ' + t.notes + ' ' + t.tags.join(' ') + ' ' + t.subtasks.map((s) => s.title).join(' ')).toLowerCase().includes(q);
  });
  const cmp = { smart, due: byDue, priority: (a, b) => PRIO[b.priority] - PRIO[a.priority] || byDue(a, b),
    newest: (a, b) => b.id - a.id, alpha: (a, b) => a.title.localeCompare(b.title) }[S.sort];
  S.view === 'done' ? list.sort((a, b) => b.completed_at.localeCompare(a.completed_at)) : list.sort(cmp);
  return list;
}
function longestStreak(days) {
  const sorted = [...days].sort(); let best = 0, run = 0, prev = null;
  for (const d of sorted) { const cur = parseLocal(d); run = prev && dayDiff(cur, prev) === 1 ? run + 1 : 1; best = Math.max(best, run); prev = cur; }
  return best;
}

/* ---------- render ---------- */
function render() {
  const now = new Date(), all = S.tasks, states = all.map((t) => state(t, now));
  const count = (s) => states.filter((x) => x === s).length;
  const doneDays = all.filter((t) => t.completed_at).map((t) => ymd(new Date(t.completed_at)));
  const today = ymd(now), doneToday = doneDays.filter((d) => d === today).length;
  const pending = all.length - count('done'), dueNow = count('today'), od = count('overdue');

  const hr = now.getHours(), part = hr < 12 ? 'Good morning' : hr < 17 ? 'Good afternoon' : 'Good evening';
  $('today-date').textContent = part + ' · ' + now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  $('greeting').textContent = boot.user.name;
  const initial = (boot.user.name || '?').charAt(0).toUpperCase(); $('avatar').textContent = initial; $('avatar-lg').textContent = initial;
  const todayTotal = doneToday + dueNow + od, pct = todayTotal ? Math.round(100 * doneToday / todayTotal) : (all.length && !pending ? 100 : 0);
  $('hero-big').textContent = pct + '%'; $('progress-bar').style.width = pct + '%';
  $('summary').textContent = !all.length ? 'Add your first task to get started.' : todayTotal ? `${doneToday} of ${todayTotal} tasks for today completed` + (od ? ` · ${od} overdue` : '')
    : pending ? `Nothing due today · ${pending} pending` : 'All caught up. Well done!';
  $('st-today').textContent = dueNow + od; $('st-upcoming').textContent = count('upcoming'); $('st-overdue').textContent = od; $('st-done').textContent = count('done');
  const set = new Set(doneDays); let streak = 0, d = startOfDay(now);
  if (!set.has(ymd(d))) d.setDate(d.getDate() - 1);
  while (set.has(ymd(d))) { streak++; d.setDate(d.getDate() - 1); }
  $('st-streak').textContent = streak;
  const counts = [...Array(7)].map((_, i) => { const x = new Date(now); x.setDate(x.getDate() - (6 - i)); return { x, n: doneDays.filter((dd) => dd === ymd(x)).length }; });
  const max = Math.max(1, ...counts.map((c) => c.n));
  $('week').replaceChildren(...counts.map((c, i) => h('div', { class: i === 6 ? 'today' : '', title: `${c.n} done · ${fmtDay(c.x)}` },
    h('i', { style: `height:${4 + Math.round(30 * c.n / max)}px` }), c.x.toLocaleDateString([], { weekday: 'narrow' }))));
  const next = all.filter((t, i) => states[i] !== 'done').sort(smart).slice(0, 5);
  $('upnext').replaceChildren(...(next.length ? next.map((t) => row(t, now)) : [emptyBox('Nothing pending', 'Tap + to add a task.')]));
  renderNextStrip(now); renderAgenda(now); renderDiaryPrompt();
  const q = QUOTES[Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 864e5) % QUOTES.length];
  $('quote-text').textContent = '“' + q[0] + '”'; $('quote-by').textContent = '— ' + q[1];

  const tabs = [['all', 'All', pending], ['today', 'Today', dueNow + od], ['upcoming', 'Upcoming', count('upcoming')], ['overdue', 'Overdue', od], ['done', 'Done', count('done')]];
  $('tabs').replaceChildren(...tabs.map(([k, label, n]) => h('button', { class: 'tab', role: 'tab', 'aria-selected': S.view === k, onclick: () => { S.view = k; render(); } }, label, n ? h('small', {}, n) : null)));
  const tags = new Map(); all.forEach((t) => t.tags.forEach((x) => tags.set(x.toLowerCase(), x)));
  $('tagbar').replaceChildren(...[...tags].sort().map(([k, v]) => h('button', { class: 'pill tag' + (S.tag === k ? ' on' : ''), type: 'button', onclick: () => { S.tag = S.tag === k ? '' : k; render(); } }, icon('tag'), v)));
  const list = visible();
  $('list').replaceChildren(...(list.length ? list.map((t) => row(t, now)) :
    [emptyBox(S.q || S.prio || S.tag ? 'No matching tasks' : S.view === 'done' ? 'Nothing completed yet' : S.view === 'overdue' ? 'Nothing overdue. Well done!' : 'No tasks here yet', 'Tap + to add one.')]));
  $('btn-clear').hidden = !(S.view === 'done' && list.length && S.tview === 'list');
  $('tasks-list').hidden = S.tview !== 'list'; $('tasks-cal').hidden = S.tview !== 'cal';
  document.querySelectorAll('#tview button').forEach((b) => b.setAttribute('aria-checked', b.dataset.v === S.tview));
  if (S.tab === 'tasks' && S.tview === 'cal') renderCalendar();
  if (S.tab === 'timetable') renderTimetable();
  if (S.tab === 'notes') renderNoteList();
  renderFocusTasks(); renderProfile(doneDays, all);
  document.title = (od ? `(${od}) ` : '') + 'My Dashboard – Do.iT';
}

function row(t, now) {
  const st = state(t, now), done = st === 'done', open = S.open.has(t.id);
  const lbl = dueLabel(t, now), sd = t.subtasks.filter((s) => s.done).length;
  const meta = h('div', { class: 'meta' },
    lbl && !done ? h('span', { class: 'pill ' + st }, icon('cal'), lbl) : null,
    done ? h('span', { class: 'pill plain' }, icon('check'), 'Done ' + fmtDay(new Date(t.completed_at))) : null,
    t.reminder && !done ? h('span', { class: 'pill rem', title: 'Alarm' }, icon('bell'), fmtTime(parseLocal(t.reminder))) : null,
    t.recurring !== 'none' ? h('span', { class: 'pill plain' }, icon('repeat'), t.recurring) : null,
    t.subtasks.length ? h('span', { class: 'pill plain' }, icon('list'), `${sd}/${t.subtasks.length}`, h('span', { class: 'sub-bar' }, h('i', { style: `width:${100 * sd / t.subtasks.length}%` }))) : null,
    t.tags.map((x) => h('button', { class: 'pill tag', type: 'button', onclick: () => { S.tag = x.toLowerCase(); S.tview = 'list'; go('tasks'); } }, icon('tag'), x)));
  const detail = open ? h('div', { class: 'detail' }, t.notes ? h('p', {}, t.notes) : null,
    t.subtasks.length ? h('ul', { class: 'steps' }, t.subtasks.map((s) => h('li', {}, h('input', { type: 'checkbox', checked: s.done, 'aria-label': s.title, onchange: () => toggleSub(s.id) }),
      h('span', { class: s.done ? 'sdone' : '' }, s.title)))) : null,
    !t.notes && !t.subtasks.length ? h('p', {}, 'No notes or checklist.') : null) : null;
  return h('article', { class: 'task' + (done ? ' done' : ''), 'data-p': t.priority },
    h('button', { class: 'tick', type: 'button', 'aria-label': done ? 'Mark as not done' : 'Mark as done', onclick: () => done ? reopen(t) : complete(t) }, icon('check')),
    h('div', {}, h('button', { class: 't-title', type: 'button', 'aria-expanded': open, onclick: () => { open ? S.open.delete(t.id) : S.open.add(t.id); render(); } }, t.title), meta),
    h('div', { class: 'acts' },
      h('button', { type: 'button', title: 'Edit', 'aria-label': 'Edit ' + t.title, onclick: () => openEditor(t) }, icon('edit')),
      h('button', { type: 'button', title: 'Delete', 'aria-label': 'Delete ' + t.title, onclick: () => remove(t) }, icon('trash'))),
    detail);
}

/* ---------- navigation ---------- */
async function go(tab) {
  if (S.tab === 'notes' && tab !== 'notes') { dNote.stop(); await flushNote(); }
  S.tab = tab; document.querySelectorAll('.page').forEach((p) => { p.hidden = p.dataset.page !== tab; });
  document.querySelectorAll('.nav-btn').forEach((b) => (b.dataset.tab === tab ? b.setAttribute('aria-current', 'page') : b.removeAttribute('aria-current')));
  const m = { timetable: ['New activity', 'Add a new activity'], notes: ['New entry', 'Write a new diary entry'] }[tab] || ['New task', 'Add a new task'];
  $('fab-text').textContent = m[0]; $('fab').setAttribute('aria-label', m[1]);
  if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab);
  window.scrollTo(0, 0); render();
}
document.querySelectorAll('.nav-btn').forEach((b) => b.addEventListener('click', () => go(b.dataset.tab)));
document.querySelectorAll('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));
document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => { S.view = b.dataset.view; S.tview = 'list'; go('tasks'); }));
document.querySelectorAll('#tview button').forEach((b) => b.addEventListener('click', () => { S.tview = b.dataset.v; render(); }));
window.addEventListener('hashchange', () => { const t = location.hash.slice(1); if (TABS.includes(t) && t !== S.tab) go(t); });
$('fab').addEventListener('click', () => { if (S.tab === 'timetable') openTT(null); else if (S.tab === 'notes') newNote(); else openEditor(null); });

/* ---------- task actions ---------- */
async function complete(t) {
  const r = await api('POST', `/api/tasks/${t.id}/complete`, { now: ymdhm(new Date()) }); if (!r) return;
  put(r.task); if (r.spawned) put(r.spawned); dismissRing(t.id); render();
  toast(r.spawned ? 'Done! Next one scheduled' : 'Task completed', async () => {
    if (r.spawned) { await api('DELETE', `/api/tasks/${r.spawned.id}`); S.tasks = S.tasks.filter((x) => x.id !== r.spawned.id); }
    const u = await api('POST', `/api/tasks/${t.id}/reopen`); if (u) { put(u.task); render(); } });
}
async function reopen(t) { const r = await api('POST', `/api/tasks/${t.id}/reopen`); if (r) { put(r.task); render(); } }
async function toggleSub(id) { const r = await api('POST', `/api/subtasks/${id}/toggle`); if (r) { put(r.task); render(); } }
async function remove(t) {
  if (!confirm(`Delete "${t.title}"? This cannot be undone.`)) return;
  if (await api('DELETE', `/api/tasks/${t.id}`)) { S.tasks = S.tasks.filter((x) => x.id !== t.id); dismissRing(t.id); closeDlg(ed); render(); toast('Task deleted'); }
}
$('btn-clear').addEventListener('click', async () => {
  if (!confirm('Permanently remove all completed tasks?')) return;
  if (await api('POST', '/api/tasks/clear-completed')) { S.tasks = S.tasks.filter((t) => !t.completed_at); render(); }
});

/* ---------- quick add ---------- */
function parseQuick(text) {
  let s = ' ' + text + ' ', out = { priority: 'medium', tags: [], recurring: 'none' }, now = new Date();
  s = s.replace(/\s#([\w-]+)/g, (m, g) => (out.tags.push(g), ' '));
  s = s.replace(/\s!(high|h|medium|med|m|low|l)\b/i, (m, g) => (out.priority = { h: 'high', m: 'medium', l: 'low', med: 'medium' }[g.toLowerCase()] || g.toLowerCase(), ' '));
  s = s.replace(/\s(?:every\s+(day|week|month)|(daily|weekly|monthly))\b/i, (m, a, b) => (out.recurring = ({ day: 'daily', week: 'weekly', month: 'monthly' }[(a || '').toLowerCase()] || (b || '').toLowerCase()), ' '));
  let hh = null, mm = 0;
  s = s.replace(/\s(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s?(am|pm)\b/i, (m, a, b, c) => { hh = +a % 12 + (c.toLowerCase() === 'pm' ? 12 : 0); mm = +(b || 0); return ' '; });
  if (hh === null) s = s.replace(/\s(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b/, (m, a, b) => (hh = +a, mm = +b, ' '));
  let day = null;
  s = s.replace(/\s(today|tonight|tomorrow|tmrw|(?:next\s+)?(?:sunday|monday|tuesday|wednesday|thursday|friday|saturday))\b/i, (m, w) => {
    w = w.toLowerCase().replace('next ', ''); day = startOfDay(now);
    if (w === 'tomorrow' || w === 'tmrw') day.setDate(day.getDate() + 1);
    else if (DAYS.includes(w)) day.setDate(day.getDate() + ((DAYS.indexOf(w) - day.getDay() + 7) % 7 || 7));
    return ' '; });
  if (hh !== null && !day) { day = startOfDay(now); if (new Date(day.getFullYear(), day.getMonth(), day.getDate(), hh, mm) < now) day.setDate(day.getDate() + 1); }
  out.title = s.replace(/\s+/g, ' ').trim(); out.alarm_sound = store.get('defSound', 'classic');
  if (day) { out.due = ymd(day); if (hh !== null) { out.due_time = `${p2(hh)}:${p2(mm)}`; out.reminder = `${out.due}T${out.due_time}`; } }
  return out;
}
$('quick-input').addEventListener('input', (e) => {
  const v = e.target.value.trim(), box = $('quick-preview'); box.replaceChildren(); if (!v) return;
  const p = parseQuick(v), add = (ic, txt) => box.append(h('span', { class: 'chip' }, icon(ic), txt));
  if (p.due) add('cal', fmtDay(parseLocal(p.due)) + (p.due_time ? ', ' + fmtTime(parseLocal(p.reminder)) : ''));
  if (p.reminder) add('bell', 'alarm on');
  if (p.priority !== 'medium') add('list', p.priority); p.tags.forEach((x) => add('tag', x)); if (p.recurring !== 'none') add('repeat', p.recurring);
});
$('quick').addEventListener('submit', async (e) => {
  e.preventDefault(); const input = $('quick-input'), p = parseQuick(input.value.trim());
  if (!p.title) { if (input.value.trim()) toast('Add a few words describing the task.'); return; }
  const r = await api('POST', '/api/tasks', p); if (!r) return;
  put(r.task); input.value = ''; $('quick-preview').replaceChildren(); render(); toast(r.task.reminder ? 'Task added with alarm' : 'Task added');
});
$('search').addEventListener('input', (e) => { S.q = e.target.value.trim(); render(); });
$('f-prio').addEventListener('change', (e) => { S.prio = e.target.value; render(); });
$('f-sort').addEventListener('change', (e) => { S.sort = e.target.value; render(); });

/* ---------- task editor ---------- */
const ed = $('editor'); let edPrio = 'medium', edSubs = [], remindTouched = false;
function setPrio(v) { edPrio = v; document.querySelectorAll('#ed-prio button').forEach((b) => b.setAttribute('aria-checked', b.dataset.v === v)); }
document.querySelectorAll('#ed-prio button').forEach((b) => b.addEventListener('click', () => setPrio(b.dataset.v)));
function drawSubs() {
  $('ed-subs').replaceChildren(...edSubs.map((s, i) => h('li', {},
    h('input', { type: 'text', value: s.title, maxlength: 255, 'aria-label': 'Checklist step ' + (i + 1), placeholder: 'Step ' + (i + 1), oninput: (e) => { s.title = e.target.value; },
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); addSub(); } } }),
    h('button', { class: 'round-btn', type: 'button', 'aria-label': 'Remove step', onclick: () => { edSubs.splice(i, 1); drawSubs(); } }, icon('x')))));
}
function addSub() { edSubs.push({ title: '', done: false }); drawSubs(); $('ed-subs').lastChild.querySelector('input').focus(); }
$('ed-addsub').addEventListener('click', addSub);
function remindHint() {
  const v = $('ed-remind').value; $('ed-reminder').hidden = v !== 'custom';
  $('ed-remind-hint').textContent = v === 'none' ? (($('ed-date').value && $('ed-time').value) ? 'No alarm will ring for this task.' : 'Set a date and time to use an alarm.')
    : v === 'custom' ? 'Choose exactly when the alarm should ring.' : 'The alarm rings while Do.iT is open on your phone or computer.';
}
function openEditor(t, preDate) {
  S.editing = t ? t.id : null; $('ed-title').textContent = t ? 'Edit task' : 'New task'; $('ed-error').hidden = true; remindTouched = !!t;
  $('ed-name').value = t ? t.title : ''; $('ed-notes').value = t ? t.notes : '';
  $('ed-date').value = t && t.due ? t.due.slice(0, 10) : preDate || ''; $('ed-time').value = t && t.due && t.due_has_time ? t.due.slice(11, 16) : '';
  $('ed-tags').value = t ? t.tags.join(', ') : ''; $('ed-repeat').value = t ? t.recurring : 'none';
  $('ed-sound').value = t ? t.alarm_sound : store.get('defSound', 'classic'); $('ed-reminder').value = '';
  let sel = 'none';
  if (t && t.reminder) { sel = 'custom'; $('ed-reminder').value = t.reminder;
    if (t.due && t.due_has_time) { const diff = Math.round((parseLocal(t.due) - parseLocal(t.reminder)) / 6e4); if ([0, 5, 10, 30, 60, 1440].includes(diff)) sel = String(diff); } }
  $('ed-remind').value = sel; remindHint();
  setPrio(t ? t.priority : 'medium'); edSubs = t ? t.subtasks.map((s) => ({ ...s })) : []; drawSubs();
  $('ed-delete').hidden = !t; $('ed-save').textContent = t ? 'Save changes' : 'Add task';
  ed.showModal(); if (!t) $('ed-name').focus();
}
ed.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeDlg(ed)));
$('ed-delete').addEventListener('click', () => { const t = S.tasks.find((x) => x.id === S.editing); if (t) remove(t); });
$('ed-remind').addEventListener('change', () => { remindTouched = true; remindHint(); });
$('ed-time').addEventListener('change', () => { if ($('ed-time').value && $('ed-date').value && !remindTouched && $('ed-remind').value === 'none') $('ed-remind').value = '0'; remindHint(); });
$('ed-date').addEventListener('change', remindHint);
let prev = null; const stopPrev = () => { if (prev) { prev.pause(); prev = null; } };
$('ed-preview').addEventListener('click', () => { stopPrev(); armAudio(); prev = new Audio(window.SOUNDS[$('ed-sound').value]); prev.play().catch(() => beepOnce()); setTimeout(stopPrev, 3500); });
ed.addEventListener('close', stopPrev);
$('ed-form').addEventListener('submit', async (e) => {
  e.preventDefault(); const title = $('ed-name').value.trim(); if (!title) return showErr('Please enter a task name.');
  const date = $('ed-date').value, time = $('ed-time').value, mode = $('ed-remind').value; let reminder = '';
  if (mode === 'custom') { reminder = $('ed-reminder').value; if (!reminder) return showErr('Pick a date and time for the alarm.'); }
  else if (mode !== 'none') {
    if (!date || !time) return showErr('Choose a due date and time so the alarm knows when to ring.');
    const when = parseLocal(date + 'T' + time); when.setMinutes(when.getMinutes() - +mode); reminder = ymdhm(when);
  }
  const body = { title, notes: $('ed-notes').value, due: date, due_time: time, reminder, priority: edPrio, tags: $('ed-tags').value,
    recurring: $('ed-repeat').value, alarm_sound: $('ed-sound').value, subtasks: edSubs.filter((s) => s.title.trim()) };
  if (body.recurring !== 'none' && !body.due && !body.reminder) return showErr('Choose a due date to repeat this task.');
  const btn = $('ed-save'); btn.disabled = true;
  const r = S.editing ? await api('PATCH', `/api/tasks/${S.editing}`, body) : await api('POST', '/api/tasks', body);
  btn.disabled = false; if (!r) return;
  const wasEdit = !!S.editing; put(r.task); closeDlg(ed); render(); toast(wasEdit ? 'Task updated' : r.task.reminder ? 'Task added with alarm' : 'Task added');
});

/* ---------- calendar (inside Tasks) ---------- */
function renderCalendar() {
  const y = S.cal.getFullYear(), m = S.cal.getMonth(), first = new Date(y, m, 1), now = new Date();
  $('cal-title').textContent = first.toLocaleDateString([], { month: 'long', year: 'numeric' });
  const byDay = {}; S.tasks.forEach((t) => { if (t.due) (byDay[t.due.slice(0, 10)] ||= []).push(t); });
  const cells = [], start = new Date(y, m, 1 - first.getDay());
  for (let i = 0; i < 42; i++) { const d = new Date(start); d.setDate(start.getDate() + i); const k = ymd(d), ts = byDay[k] || [];
    cells.push(h('button', { class: 'cal-day' + (d.getMonth() !== m ? ' dim' : '') + (k === ymd(now) ? ' today' : '') + (k === ymd(S.calSel) ? ' sel' : ''),
      type: 'button', 'aria-label': fmtDay(d) + (ts.length ? `, ${ts.length} tasks` : ''), onclick: () => { S.calSel = d; renderCalendar(); } },
      d.getDate(), ts.length ? h('i', { class: ts.some((t) => state(t, now) === 'overdue') ? 'od' : '' }) : null)); }
  $('cal-grid').replaceChildren(...cells);
  const k = ymd(S.calSel), list = (byDay[k] || []).sort(smart);
  $('cal-day-title').textContent = S.calSel.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
  $('cal-list').replaceChildren(...(list.length ? list.map((t) => row(t, now)) : [emptyBox('Nothing planned', 'Add a task for this day.')]),
    h('button', { class: 'btn primary', type: 'button', onclick: () => openEditor(null, k) }, icon('plus'), 'Add task on this day'));
}
$('cal-prev').addEventListener('click', () => { S.cal = new Date(S.cal.getFullYear(), S.cal.getMonth() - 1, 1); renderCalendar(); });
$('cal-next').addEventListener('click', () => { S.cal = new Date(S.cal.getFullYear(), S.cal.getMonth() + 1, 1); renderCalendar(); });

/* ---------- TIMETABLE ---------- */
const ttSorted = () => [...S.tt].sort((a, b) => a.day - b.day || a.start - b.start);
function nextEntry(now = new Date()) {
  const di = todayIdx(now), nm = nowMin(now); let best = null;
  for (const e of S.tt) { const off = (e.day - di + 7) % 7; if (off === 0 && e.start <= nm) continue;
    const key = off * 1440 + e.start; if (!best || key < best.key) best = { e, off, key }; }
  return best ? { ...best, mins: best.key - nm } : null;
}
const currentEntries = (now = new Date()) => S.tt.filter((e) => e.day === todayIdx(now) && e.start <= nowMin(now) && nowMin(now) < e.end);
function untilText(mins) { return mins < 60 ? `in ${mins} min` : mins < 1440 ? `in ${Math.floor(mins / 60)} h ${mins % 60 ? (mins % 60) + ' min' : ''}`.trim() : ''; }
function renderNextStrip(now) {
  const el = $('next-strip'), cur = currentEntries(now)[0], nx = nextEntry(now);
  if (!S.tt.length) { el.hidden = true; return; }
  el.hidden = false; el.replaceChildren();
  if (cur) el.append(icon('clock'), h('span', {}, 'Now: ', h('b', {}, cur.title)), h('span', { class: 'when' }, 'until ' + fmtMin(cur.end)));
  else if (nx) el.append(icon('clock'), h('span', {}, 'Next: ', h('b', {}, nx.e.title)),
    h('span', { class: 'when' }, (nx.off === 0 ? '' : nx.off === 1 ? 'Tomorrow ' : DN[nx.e.day] + ' ') + fmtMin(nx.e.start) + (nx.off === 0 ? ' · ' + untilText(nx.mins) : '')));
  else el.hidden = true;
}
function renderAgenda(now) {
  const di = todayIdx(now), nm = nowMin(now), items = [];
  S.tt.filter((e) => e.day === di).forEach((e) => items.push({ at: e.start, time: fmtMin(e.start), title: e.title, sub: (e.location ? e.location + ' · ' : '') + 'until ' + fmtMin(e.end),
    c: e.color, past: e.end <= nm, now: e.start <= nm && nm < e.end, click: () => openTT(e) }));
  S.tasks.forEach((t) => { if (state(t, now) !== 'today' && state(t, now) !== 'overdue') return; const d = due(t); if (d && dayDiff(d, now) !== 0 && state(t, now) !== 'overdue') return;
    const at = t.due_has_time && d ? d.getHours() * 60 + d.getMinutes() : -1;
    items.push({ at, time: t.due_has_time && d ? fmtTime(d) : 'Anytime', title: t.title, sub: state(t, now) === 'overdue' ? 'Overdue task' : 'Task', c: 'blue', task: true, click: () => openEditor(t) }); });
  items.sort((a, b) => a.at - b.at);
  $('agenda').replaceChildren(...(items.length ? items.map((i) => h('button', { class: 'ag' + (i.past ? ' past' : '') + (i.now ? ' now' : '') + (i.task ? ' kind-task' : ''), type: 'button', 'data-c': i.c, onclick: i.click },
    h('span', { class: 't' }, i.time), h('span', { class: 'b' }, h('b', {}, i.title), h('small', {}, i.sub)))) : [emptyBox('Nothing scheduled today', 'Add timetable activities or tasks with due dates.')]));
}
function ttCell(e, now) {
  const isNow = e.day === todayIdx(now) && e.start <= nowMin(now) && nowMin(now) < e.end;
  return h('button', { class: 'tt-cell' + (isNow ? ' now' : ''), type: 'button', 'data-c': e.color, onclick: () => openTT(e), 'aria-label': `${e.title}, ${DF[e.day]} ${fmtMin(e.start)} to ${fmtMin(e.end)}. Edit` },
    h('b', {}, e.title), e.location ? h('small', {}, e.location) : null, !e.alerts ? h('small', {}, 'Reminders off') : null);
}
function renderTimetable() {
  const now = new Date(), ti = todayIdx(now), tableMode = S.ttview === 'table';
  document.querySelectorAll('#tt-view button').forEach((b) => b.setAttribute('aria-checked', b.dataset.v === S.ttview));
  $('tt-wrap').hidden = !tableMode; $('tt-day').hidden = tableMode; $('tt-day-tabs').hidden = tableMode;
  const cur = currentEntries(now)[0], nx = nextEntry(now), cards = [];
  if (cur) cards.push(h('div', { class: 'now-card', 'data-c': cur.color }, h('small', {}, 'Happening now'), h('b', {}, cur.title), h('span', {}, `Until ${fmtMin(cur.end)}` + (cur.location ? ' · ' + cur.location : ''))));
  if (nx) cards.push(h('div', { class: 'now-card', 'data-c': nx.e.color }, h('small', {}, 'Up next'), h('b', {}, nx.e.title),
    h('span', {}, (nx.off === 0 ? 'Today' : nx.off === 1 ? 'Tomorrow' : DF[nx.e.day]) + ' at ' + fmtMin(nx.e.start) + (nx.off === 0 ? ' · ' + untilText(nx.mins) : ''))));
  $('tt-now').replaceChildren(...cards);
  if (!S.tt.length) {
    $('tt-day').hidden = true; $('tt-day-tabs').hidden = true; $('tt-wrap').hidden = false;
    $('tt-wrap').replaceChildren(emptyBox('Your timetable is empty', 'Add your first activity, or start with an example week you can edit.',
      h('div', { class: 'head-tools', style: 'justify-content:center;margin-top:.8rem' }, h('button', { class: 'btn primary', onclick: () => openTT(null) }, 'Add activity'), h('button', { class: 'btn', onclick: addSample }, 'Add example week'))));
    return;
  }
  if (tableMode) {
    const slots = [...new Map(S.tt.map((e) => [e.start + '-' + e.end, [e.start, e.end]])).values()].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const table = h('table', { class: 'tt' },
      h('caption', { class: 'sr' }, 'Weekly timetable'),
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Time'), DN.map((d, i) => h('th', { scope: 'col', class: i === ti ? 'today' : '' }, d)))),
      h('tbody', {}, slots.map(([s, e]) => h('tr', {}, h('th', { scope: 'row' }, fmtMin(s), h('br'), '– ' + fmtMin(e)),
        DN.map((_, d) => { const here = S.tt.filter((x) => x.day === d && x.start === s && x.end === e);
          return h('td', { class: d === ti ? 'today' : '' }, here.length ? here.map((x) => ttCell(x, now)) :
            h('button', { class: 'tt-empty', type: 'button', 'aria-label': `Add activity on ${DF[d]} ${fmtMin(s)}`, onclick: () => openTT(null, { day: d, start: s, end: e }) }, icon('plus'))); })))));
    $('tt-wrap').replaceChildren(table);
  } else {
    $('tt-day-tabs').replaceChildren(...DN.map((d, i) => h('button', { role: 'tab', class: i === ti ? 'today' : '', 'aria-selected': S.ttDay === i, onclick: () => { S.ttDay = i; renderTimetable(); } }, d)));
    const list = ttSorted().filter((e) => e.day === S.ttDay);
    $('tt-day').replaceChildren(...(list.length ? list.map((e) => h('button', { class: 'tt-card', type: 'button', 'data-c': e.color, onclick: () => openTT(e) },
      h('span', { class: 'tm' }, fmtMin(e.start), h('br'), '– ' + fmtMin(e.end)), h('span', {}, h('b', {}, e.title), e.location ? h('small', {}, e.location) : null), e.alerts ? icon('bell') : null))
      : [emptyBox('Nothing on ' + DF[S.ttDay], 'Tap Add activity to plan this day.')]));
  }
}
$('tt-view').addEventListener('click', (e) => { const v = e.target.closest('button'); if (!v) return; S.ttview = v.dataset.v; store.set('ttview', S.ttview); renderTimetable(); });
$('tt-add').addEventListener('click', () => openTT(null, S.ttview === 'day' ? { day: S.ttDay } : null));
$('tt-print').addEventListener('click', () => { S.ttview = 'table'; renderTimetable(); window.print(); });
$('tt-clear').addEventListener('click', async () => { if (!S.tt.length || !confirm('Remove every activity from your timetable?')) return;
  if (await api('POST', '/api/timetable/clear')) { S.tt = []; render(); toast('Timetable cleared'); } });
async function addSample() {
  const sample = [['Morning study', [0,1,2,3,4], '07:00', '08:00', 'sky', ''], ['Lectures', [0,1,2,3,4], '08:00', '12:00', 'blue', 'Main hall'], ['Lunch break', [0,1,2,3,4], '12:00', '13:00', 'amber', ''],
    ['Practical / lab', [1,3], '13:00', '15:00', 'teal', 'Lab'], ['Library & assignments', [0,2,4], '15:00', '17:00', 'purple', 'Library'], ['Exercise', [0,2,4], '17:30', '18:30', 'green', ''],
    ['Weekly review', [5], '10:00', '11:00', 'red', ''], ['Worship / family time', [6], '09:00', '11:00', 'navy', '']];
  for (const [title, days, start, end, color, location] of sample) { const r = await api('POST', '/api/timetable', { title, days, start, end, color, location, alerts: true }); if (r) S.tt.push(...r.entries); }
  render(); toast('Example week added. Tap any activity to edit it.');
}
/* timetable dialog */
const tdlg = $('tt-dlg'); const COLORS = ['blue', 'navy', 'sky', 'teal', 'green', 'amber', 'red', 'purple'];
$('tt-days').replaceChildren(...DN.map((d, i) => h('label', {}, h('input', { type: 'checkbox', name: 'ttday', value: i }), h('span', {}, d))));
$('tt-colors').replaceChildren(...COLORS.map((c) => h('label', { 'data-c': c, title: c }, h('input', { type: 'radio', name: 'ttcolor', value: c, 'aria-label': c }), h('span', {}))));
const pickedDays = () => [...document.querySelectorAll('#tt-days input:checked')].map((i) => +i.value);
document.querySelectorAll('#tt-days input').forEach((i) => i.addEventListener('change', () => { if (S.ttEditing) document.querySelectorAll('#tt-days input').forEach((o) => { if (o !== i) o.checked = false; }); }));
function openTT(e, pre) {
  S.ttEditing = e ? e.id : null; $('tt-title').textContent = e ? 'Edit activity' : 'Add activity'; $('tt-error').hidden = true;
  $('tt-name').value = e ? e.title : ''; $('tt-start').value = e ? hhmm(e.start) : pre && pre.start != null ? hhmm(pre.start) : '';
  $('tt-end').value = e ? hhmm(e.end) : pre && pre.end != null ? hhmm(pre.end) : ''; $('tt-loc').value = e ? e.location : '';
  document.querySelectorAll('#tt-days input').forEach((i) => { i.checked = e ? +i.value === e.day : pre && pre.day != null ? +i.value === pre.day : +i.value === todayIdx(); });
  const col = e ? e.color : 'blue'; document.querySelectorAll('#tt-colors input').forEach((i) => { i.checked = i.value === col; });
  $('tt-alerts').checked = e ? e.alerts : true; $('tt-delete').hidden = !e; $('tt-save').textContent = e ? 'Save changes' : 'Add activity';
  $('tt-days-l').textContent = e ? 'Day' : 'Day(s)'; tdlg.showModal(); if (!e) $('tt-name').focus();
}
tdlg.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeDlg(tdlg)));
$('tt-delete').addEventListener('click', async () => { const e = S.tt.find((x) => x.id === S.ttEditing); if (!e || !confirm(`Delete "${e.title}" from ${DF[e.day]}?`)) return;
  if (await api('DELETE', `/api/timetable/${e.id}`)) { S.tt = S.tt.filter((x) => x.id !== e.id); closeDlg(tdlg); render(); toast('Activity deleted'); } });
$('tt-form').addEventListener('submit', async (ev) => {
  ev.preventDefault(); const title = $('tt-name').value.trim(), days = pickedDays(), start = $('tt-start').value, end = $('tt-end').value;
  if (!title) return showErr('Please give the activity a name.', 'tt-error'); if (!days.length) return showErr('Choose at least one day.', 'tt-error');
  if (!start || !end) return showErr('Please set the start and end times.', 'tt-error'); if (end <= start) return showErr('The end time must be after the start time.', 'tt-error');
  const color = (document.querySelector('#tt-colors input:checked') || {}).value || 'blue';
  const body = { title, start, end, location: $('tt-loc').value, color, alerts: $('tt-alerts').checked }, btn = $('tt-save'); btn.disabled = true;
  let r;
  if (S.ttEditing) { r = await api('PATCH', `/api/timetable/${S.ttEditing}`, { ...body, day: days[0] }); if (r) S.tt = S.tt.map((x) => (x.id === r.entry.id ? r.entry : x)); }
  else { r = await api('POST', '/api/timetable', { ...body, days }); if (r) S.tt.push(...r.entries); }
  btn.disabled = false; if (!r) return; closeDlg(tdlg); render(); toast(S.ttEditing ? 'Activity updated' : 'Activity added');
});

/* ---------- timetable reminders: 30 min, 5 min, at start ---------- */
const TT_OFFSETS = [30, 5, 0];
function checkTimetable() {
  if (store.get('ttAlerts', 'on') === 'off' || !S.tt.length) return;
  const now = new Date(), di = todayIdx(now), key = 'ttFired-' + ymd(now);
  S.tt.forEach((e) => { if (e.day !== di || !e.alerts) return;
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, e.start);
    TT_OFFSETS.forEach((off) => {
      const late = now - (start - off * 6e4), id = e.id + '-' + off;
      if (late < 0 || late > 3 * 6e4) return;
      let fired = []; try { fired = JSON.parse(store.get(key, '[]')); } catch (x) {}
      if (fired.includes(id)) return; fired.push(id); store.set(key, JSON.stringify(fired)); fireTT(e, off); }); });
}
function fireTT(e, off) {
  const where = fmtMin(e.start) + (e.location ? ' · ' + e.location : '');
  if (off === 0) { ring({ id: -(1000 + e.id), title: e.title, when: 'Starting now · ' + where, kind: 'Timetable', simple: true, alarm_sound: store.get('defSound', 'classic') });
    return notify('⏰ ' + e.title, 'Starting now · ' + where, 'tt-' + e.id + '-0'); }
  const label = off === 30 ? 'In 30 minutes' : 'In 5 minutes';
  const card = h('div', { class: 'head-card', 'data-c': e.color }, h('span', { class: 'hc-ic' }, icon('bell')),
    h('div', {}, h('small', {}, label), h('b', {}, e.title), h('div', { class: 'muted small' }, where)),
    h('button', { class: 'round-btn flat', type: 'button', 'aria-label': 'Dismiss', onclick: () => card.remove() }, icon('x')));
  $('heads').append(card); setTimeout(() => card.remove(), 30000);
  chime(); notify(`${label}: ${e.title}`, where, `tt-${e.id}-${off}`); buzz([250, 120, 250]);
}

/* ---------- DIARY ---------- */
const MOODS = [['happy', '😄', 'Happy'], ['good', '🙂', 'Good'], ['okay', '😐', 'Okay'], ['sad', '😔', 'Sad'], ['angry', '😠', 'Stressed']];
let ntMood = '', ntPinned = false, ntTimer = null, ntSaving = false, ntDirty = false;
const wrap = $('notes-wrap'); $('nt-edit').hidden = false;
$('nt-moods').replaceChildren(...MOODS.map(([k, e, l]) => h('button', { type: 'button', role: 'radio', 'aria-checked': 'false', 'aria-label': l, title: l, 'data-k': k,
  onclick: () => { ntMood = ntMood === k ? '' : k; paintMood(); queueSave(); } }, e)));
const paintMood = () => document.querySelectorAll('#nt-moods button').forEach((b) => b.setAttribute('aria-checked', b.dataset.k === ntMood));
const paintPin = () => { $('nt-pin').classList.toggle('on', ntPinned); $('nt-pin').setAttribute('aria-pressed', ntPinned); };
const noteTitle = (n) => n.title || (n.body.trim().split('\n')[0] || '').slice(0, 60) || 'Untitled entry';
const sortNotes = () => S.notes.sort((a, b) => b.date.localeCompare(a.date) || (b.updated_at || '').localeCompare(a.updated_at || ''));
function renderNoteList() {
  const q = S.nq.toLowerCase(), list = S.notes.filter((n) => !q || (n.title + ' ' + n.body).toLowerCase().includes(q)), box = $('nt-list'); box.replaceChildren();
  if (!list.length) { box.append(emptyBox(S.nq ? 'No matching entries' : 'No entries yet', 'Your diary pages will appear here.')); return; }
  const item = (n) => { const d = parseLocal(n.date), mood = MOODS.find((m) => m[0] === n.mood);
    return h('button', { class: 'nt-item' + (n.id === S.noteId ? ' sel' : ''), type: 'button', onclick: () => openNote(n.id) },
      h('span', { class: 'nt-date' }, h('b', {}, d.getDate()), h('small', {}, d.toLocaleDateString([], { weekday: 'short' }))),
      h('span', {}, h('h3', {}, n.pinned ? icon('pin') : null, noteTitle(n), mood ? ' ' + mood[1] : ''), h('p', {}, n.body.replace(/\s+/g, ' ').trim() || 'Empty entry'))); };
  const pinned = list.filter((n) => n.pinned); if (pinned.length) { box.append(h('div', { class: 'nt-month' }, 'Pinned'), ...pinned.map(item)); }
  let last = '';
  list.filter((n) => !n.pinned).forEach((n) => { const m = parseLocal(n.date).toLocaleDateString([], { month: 'long', year: 'numeric' });
    if (m !== last) { box.append(h('div', { class: 'nt-month' }, m)); last = m; } box.append(item(n)); });
}
function fillNote(n) {
  $('nt-date').value = n ? n.date : ymd(new Date()); $('nt-title').value = n ? n.title : ''; $('nt-body').value = n ? n.body : '';
  ntMood = n ? n.mood : ''; ntPinned = n ? n.pinned : false; paintMood(); paintPin(); countWords();
  $('nt-status').textContent = n ? 'Saved' : 'New entry'; wrap.classList.add('editing');
}
async function openNote(id) { dNote.stop(); await flushNote(); S.noteId = id; fillNote(S.notes.find((n) => n.id === id)); renderNoteList(); }
async function newNote(date) {
  await flushNote(); S.noteId = 'new'; fillNote(null); if (date) $('nt-date').value = date;
  if (S.tab !== 'notes') await go('notes'); renderNoteList(); $('nt-body').focus();
}
async function openToday() {
  const t = S.notes.find((n) => n.date === ymd(new Date()));
  if (S.tab !== 'notes') await go('notes'); if (t) openNote(t.id); else newNote();
}
function countWords() { const w = ($('nt-body').value.trim().match(/\S+/g) || []).length; $('nt-count').textContent = w + (w === 1 ? ' word' : ' words'); }
function queueSave() { if (!S.noteId) return; $('nt-status').textContent = 'Writing…'; clearTimeout(ntTimer); ntTimer = setTimeout(flushNote, 800); }
async function flushNote() {
  clearTimeout(ntTimer); ntTimer = null; if (!S.noteId) return;
  if (ntSaving) { ntDirty = true; return; }
  const data = { date: $('nt-date').value, title: $('nt-title').value, body: $('nt-body').value, mood: ntMood, pinned: ntPinned };
  if (S.noteId === 'new' && !data.title.trim() && !data.body.trim()) { $('nt-status').textContent = 'New entry'; return; }
  ntSaving = true; $('nt-status').textContent = 'Saving…';
  const r = S.noteId === 'new' ? await api('POST', '/api/notes', data) : await api('PATCH', `/api/notes/${S.noteId}`, data);
  ntSaving = false;
  if (r) { if (S.noteId === 'new') { S.notes.unshift(r.note); S.noteId = r.note.id; } else S.notes = S.notes.map((n) => (n.id === r.note.id ? r.note : n));
    sortNotes(); $('nt-status').textContent = 'Saved at ' + fmtTime(new Date()); renderNoteList(); renderDiaryPrompt(); }
  else $('nt-status').textContent = 'Could not save. Check your connection.';
  if (ntDirty) { ntDirty = false; flushNote(); }
}
['nt-title', 'nt-body', 'nt-date'].forEach((id) => $(id).addEventListener('input', () => { countWords(); queueSave(); }));
$('nt-date').addEventListener('change', queueSave);
$('nt-pin').addEventListener('click', () => { ntPinned = !ntPinned; paintPin(); queueSave(); });
$('nt-del').addEventListener('click', async () => {
  if (S.noteId === 'new' || !confirm('Delete this diary entry? This cannot be undone.')) { if (S.noteId === 'new') { S.noteId = null; wrap.classList.remove('editing'); } return; }
  if (await api('DELETE', `/api/notes/${S.noteId}`)) { S.notes = S.notes.filter((n) => n.id !== S.noteId); S.noteId = null; wrap.classList.remove('editing'); renderNoteList(); renderDiaryPrompt(); toast('Entry deleted'); }
});
$('nt-back').addEventListener('click', async () => { await flushNote(); S.noteId = null; wrap.classList.remove('editing'); renderNoteList(); });
$('nt-new').addEventListener('click', () => newNote()); $('nt-today').addEventListener('click', openToday); $('dp-btn').addEventListener('click', openToday);
$('nt-search').addEventListener('input', (e) => { S.nq = e.target.value.trim(); renderNoteList(); });
function renderDiaryPrompt() {
  const t = S.notes.find((n) => n.date === ymd(new Date())), w = t ? (t.body.trim().match(/\S+/g) || []).length : 0;
  $('dp-text').textContent = t ? `You have written ${w} word${w === 1 ? '' : 's'} today. Come back any time to add more.` : 'How was your day? Take a minute to write it down while it is fresh.';
  $('dp-btn-t').textContent = t ? "Continue today's entry" : "Write today's entry";
}
document.addEventListener('visibilitychange', () => { if (document.hidden && ntTimer) flushNote(); });
addEventListener('pagehide', () => { if (ntTimer) flushNote(); });

/* ---------- sound (desktop, Android and iOS) ---------- */
const player = new Audio(); player.preload = 'auto'; let actx = null, unlocked = false, beepTimer = null, ringSound = 'classic';
function armAudio() {
  try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === 'suspended') actx.resume(); } catch (e) {}
  if (!unlocked) { unlocked = true; player.muted = true; player.src = window.SOUNDS.digital;
    player.play().then(() => { player.pause(); player.muted = false; }).catch(() => { player.muted = false; }); }
  paintSound();
  if (ringing.size && player.paused && !beepTimer) startSound(ringSound);
}
['pointerdown', 'keydown', 'touchstart'].forEach((ev) => document.addEventListener(ev, armAudio, { passive: true }));
function beepOnce() {
  if (!actx) return; const t0 = actx.currentTime;
  [0, .25, .5].forEach((o) => { const osc = actx.createOscillator(), g = actx.createGain(); osc.type = 'square'; osc.frequency.value = 880;
    g.gain.setValueAtTime(.0001, t0 + o); g.gain.exponentialRampToValueAtTime(.35, t0 + o + .02); g.gain.exponentialRampToValueAtTime(.0001, t0 + o + .2);
    osc.connect(g).connect(actx.destination); osc.start(t0 + o); osc.stop(t0 + o + .22); });
}
function chime() { armAudio(); const a = new Audio(window.SOUNDS.digital); a.play().then(() => setTimeout(() => a.pause(), 2500)).catch(beepOnce); }
function startSound(name) {
  ringSound = name; stopSound(); player.loop = true; player.muted = false; player.src = window.SOUNDS[name] || window.SOUNDS.classic;
  player.play().catch(() => { beepOnce(); beepTimer = setInterval(beepOnce, 1200); });
}
function stopSound() { player.pause(); clearInterval(beepTimer); beepTimer = null; }
const soundReady = () => unlocked || (navigator.userActivation && navigator.userActivation.hasBeenActive);
function paintSound() { const ready = !!soundReady(); $('sound-banner').hidden = ready; $('alarm-pip').classList.toggle('on', ready); }
$('sound-banner').addEventListener('click', () => { armAudio(); testAlarm(); });
$('btn-alarm').addEventListener('click', () => { armAudio(); toast(soundReady() ? 'Alarm sound is on' : 'Tap anywhere to turn on alarm sound'); });

/* ---------- alarms ---------- */
const ringing = new Map(); let flashTimer = null, vibTimer = null;
function dismissRing(id) {
  const r = ringing.get(id); if (!r) return; r.card.remove(); ringing.delete(id);
  if (!ringing.size) { stopSound(); clearInterval(flashTimer); clearInterval(vibTimer); flashTimer = vibTimer = null; render(); }
}
function notify(title, body, tag) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const opts = { body, tag, requireInteraction: true, icon: '/static/images/icon-192.png' };
  if (navigator.serviceWorker) navigator.serviceWorker.getRegistration().then((reg) => reg ? reg.showNotification(title, opts) : new Notification(title, opts)).catch(() => {});
  else try { new Notification(title, opts); } catch (e) {}
}
function ring(t) {
  if (ringing.has(t.id)) return;
  const local = t.id < 0, first = !ringing.size;
  const snooze = h('select', { 'aria-label': 'Snooze length' }, [[5, '5 min'], [10, '10 min'], [30, '30 min'], [60, '1 hour']].map(([v, l]) => h('option', { value: v }, l)));
  const act = async (fn) => { if (local) return dismissRing(t.id); await fn(); };
  const buttons = t.simple ? [h('button', { class: 'btn primary full', type: 'button', onclick: () => dismissRing(t.id) }, 'Dismiss')] : [snooze,
    h('button', { class: 'btn', type: 'button', onclick: () => act(async () => { const r = await api('POST', `/api/tasks/${t.id}/snooze`, { minutes: +snooze.value, now: ymdhm(new Date()) }); dismissRing(t.id); if (r) { put(r.task); render(); toast('Snoozed'); } }) }, 'Snooze'),
    h('button', { class: 'btn', type: 'button', onclick: () => act(() => complete(t)) }, 'Mark done'),
    h('button', { class: 'btn primary', type: 'button', onclick: () => act(async () => { const r = await api('POST', `/api/tasks/${t.id}/stop`); dismissRing(t.id); if (r) { put(r.task); render(); } }) }, 'Stop alarm')];
  const card = h('div', { class: 'alarm', role: 'alertdialog', 'aria-label': 'Alarm: ' + t.title },
    h('div', { class: 'alarm-ic' }, icon('bell')), h('small', {}, t.kind || (t.id === -1 ? 'Test alarm' : 'Reminder')), h('h3', {}, t.title),
    h('p', { class: 'when' }, t.when || (t.due ? dueLabel(t) : 'It is time!')), h('div', { class: 'row' }, buttons));
  ringing.set(t.id, { card }); $('alarms').append(card);
  if (first) { startSound(t.alarm_sound);
    flashTimer = setInterval(() => { document.title = document.title.startsWith('⏰') ? '🔔 Reminder!' : '⏰ ' + t.title; }, 900);
    vibTimer = setInterval(() => buzz([400, 200, 400]), 2000); }
  if (!local) notify('⏰ ' + t.title, t.due ? 'Due ' + dueLabel(t) : 'Your reminder is due', 'doit-' + t.id);
}
const testAlarm = () => ring({ id: -1, title: 'Alarm test: this is how your reminders will ring', due: null, alarm_sound: store.get('defSound', 'classic') });
function checkReminders() {
  const now = new Date();
  S.tasks.forEach((t) => { if (t.reminder && !t.dismissed && !t.completed_at && !ringing.has(t.id) && parseLocal(t.reminder) <= now) ring(t); });
}
async function refresh() { if (ringing.size || ed.open) return; const r = await api('GET', '/api/tasks'); if (r) { S.tasks = r.tasks; render(); } }

/* ---------- focus timer ---------- */
const F = { mode: +store.get('fmode', 25), endAt: +store.get('fend', 0), left: 0, running: false };
const fkey = () => 'sessions-' + ymd(new Date()), C = 339.3;
function renderFocusTasks() {
  const sel = $('f-task'), keep = sel.value || store.get('ftask', '');
  sel.replaceChildren(h('option', { value: '' }, 'No task selected'), ...S.tasks.filter((t) => !t.completed_at).sort(smart).map((t) => h('option', { value: t.id }, t.title)));
  sel.value = keep;
}
function paintFocus() {
  const total = F.mode * 60, left = F.running ? Math.max(0, Math.ceil((F.endAt - Date.now()) / 1000)) : F.left || total;
  $('f-time').textContent = `${p2(Math.floor(left / 60))}:${p2(left % 60)}`; $('ring-fg').style.strokeDashoffset = String(C * (1 - left / total));
  $('f-start').replaceChildren(icon(F.running ? 'pause' : 'play'), F.running ? 'Pause' : left < total ? 'Resume' : 'Start');
  $('f-count').textContent = store.get(fkey(), '0');
  document.querySelectorAll('#f-mode button').forEach((b) => b.setAttribute('aria-checked', +b.dataset.m === F.mode));
}
function focusTick() {
  if (!F.running) return; if (Date.now() < F.endAt) return paintFocus();
  F.running = false; F.left = 0; store.set('fend', 0);
  if (F.mode === 25) store.set(fkey(), String(+store.get(fkey(), 0) + 1));
  chime(); toast(F.mode === 25 ? 'Focus session complete! Take a break.' : 'Break over. Ready to focus?'); buzz([300, 150, 300]); paintFocus();
}
$('f-start').addEventListener('click', () => { armAudio();
  if (F.running) { F.left = Math.ceil((F.endAt - Date.now()) / 1000); F.running = false; store.set('fend', 0); }
  else { F.endAt = Date.now() + (F.left || F.mode * 60) * 1000; F.running = true; store.set('fend', F.endAt); store.set('fmode', F.mode); }
  paintFocus(); });
$('f-reset').addEventListener('click', () => { F.running = false; F.left = 0; store.set('fend', 0); paintFocus(); });
$('f-mode').addEventListener('click', (e) => { const m = e.target.dataset && +e.target.dataset.m; if (!m) return; F.mode = m; F.running = false; F.left = 0; store.set('fend', 0); store.set('fmode', m); paintFocus(); });
$('f-task').addEventListener('change', (e) => store.set('ftask', e.target.value));
if (F.endAt > Date.now()) F.running = true; else store.set('fend', 0);

/* ---------- profile ---------- */
function renderProfile(doneDays, all) {
  $('me-name').textContent = boot.user.full_name || boot.user.name; $('me-user').textContent = '@' + boot.user.username;
  const week = doneDays.filter((d) => dayDiff(new Date(), parseLocal(d)) < 7).length, total = doneDays.length, best = longestStreak(new Set(doneDays));
  $('me-total').textContent = total; $('me-week').textContent = week; $('me-rate').textContent = (all.length ? Math.round(100 * total / all.length) : 0) + '%';
  const defs = [['First step', 'Complete 1 task', total >= 1, 'check'], ['On a roll', 'Complete 10 tasks', total >= 10, 'tasks'], ['3-day streak', 'Be active 3 days in a row', best >= 3, 'flame'],
    ['Week warrior', '7-day streak', best >= 7, 'trophy'], ['Planner', 'Add 5 timetable activities', S.tt.length >= 5, 'table'], ['Diarist', 'Write 3 diary entries', S.notes.length >= 3, 'book']];
  $('badges').replaceChildren(...defs.map(([n, d, on, ic]) => h('div', { class: 'badge' + (on ? ' on' : '') }, h('span', { class: 'qi' }, icon(ic)), h('div', {}, h('b', {}, n), h('small', {}, on ? 'Unlocked!' : d)))));
}
$('def-sound').value = store.get('defSound', 'classic');
$('def-sound').addEventListener('change', (e) => { store.set('defSound', e.target.value); armAudio(); toast('Default alarm sound saved'); });
$('tt-alerts-on').checked = store.get('ttAlerts', 'on') === 'on';
$('tt-alerts-on').addEventListener('change', (e) => { store.set('ttAlerts', e.target.checked ? 'on' : 'off'); toast(e.target.checked ? 'Timetable reminders on' : 'Timetable reminders off'); });
$('m-test').addEventListener('click', () => { armAudio(); testAlarm(); });
$('m-notify').addEventListener('click', async () => {
  if (!('Notification' in window)) return toast('This browser does not support notifications.');
  const p = await Notification.requestPermission(); toast(p === 'granted' ? 'Notifications enabled' : 'Notifications are blocked in your browser settings'); });
const pd = $('pass-dlg'); pd.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => closeDlg(pd)));
$('m-pass').addEventListener('click', () => { $('pass-error').hidden = true; $('p-cur').value = $('p-new').value = ''; pd.showModal(); });
$('pass-form').addEventListener('submit', async (e) => { e.preventDefault();
  if ($('p-new').value.length < 8) return showErr('Choose a new password of at least 8 characters.', 'pass-error');
  const r = await api('POST', '/api/account/password', { current: $('p-cur').value, new: $('p-new').value });
  if (r) { closeDlg(pd); toast('Password updated'); } });


/* ---------- VOICE TYPING (Web Speech API: Chrome, Edge, Safari) ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
function formatSpeech(t, before) {
  let s = t.trim()
    .replace(/\s*\bnew paragraph\b\s*/gi, '\n\n').replace(/\s*\bnew line\b\s*/gi, '\n').replace(/\s*\bfull stop\b/gi, '.')
    .replace(/\s*\bcomma\b/gi, ',').replace(/\s*\bquestion mark\b/gi, '?').replace(/\s*\bexclamation (?:mark|point)\b/gi, '!');
  if (!s) return '';
  if (!before.trim() || /[.!?]\s*$/.test(before) || /\n\s*$/.test(before)) s = s.charAt(0).toUpperCase() + s.slice(1);
  s = s.replace(/([.!?]\s+|\n)([a-z])/g, (m, a, b) => a + b.toUpperCase());
  return (before && !/\s$/.test(before) && !/^[.,!?\n]/.test(s) ? ' ' : '') + s;
}
function makeDictation({ input, button, getLang, onText, onState, once, onDone }) {
  let rec = null, on = false, base = '', after = '', fin = '';
  const paint = (v) => { button.classList.toggle('on', v); button.setAttribute('aria-pressed', v); if (onState) onState(v); };
  function stop() {
    if (!on) return; on = false; try { rec.onend = null; rec.stop(); } catch (e) {}
    input.value = base + fin + after; const pos = (base + fin).length; try { input.setSelectionRange(pos, pos); } catch (e) {}
    paint(false); onText && onText(); if (onDone && fin.trim()) onDone(fin.trim());
  }
  function start() {
    if (!SR) return toast('Voice typing works in Chrome, Edge and Safari.');
    armAudio(); const a = input.selectionStart ?? input.value.length, b = input.selectionEnd ?? a;
    base = input.value.slice(0, a); after = input.value.slice(b); fin = '';
    rec = new SR(); rec.lang = getLang(); rec.continuous = !once; rec.interimResults = true;
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) { const r = e.results[i];
        if (r.isFinal) { fin += formatSpeech(r[0].transcript, base + fin); if (once) setTimeout(stop, 0); } else interim += r[0].transcript; }
      input.value = base + fin + (interim ? (fin && !/\s$/.test(fin) ? ' ' : '') + interim : '') + after; onText && onText(); };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') { toast('Microphone is blocked. Allow it in your browser (lock icon near the address bar), then try again.'); stop(); }
      else if (e.error === 'network') { toast('Voice typing needs an internet connection.'); stop(); }
      else if (e.error === 'audio-capture') { toast('No microphone was found.'); stop(); } };
    rec.onend = () => { if (on) { try { rec.start(); } catch (e) { stop(); } } };
    on = true; try { rec.start(); paint(true); } catch (e) { on = false; toast('Could not start the microphone.'); } }
  return { toggle: () => (on ? stop() : start()), stop, get active() { return on; } };
}
document.querySelectorAll('.mic, .mic-btn').forEach((b) => { b.hidden = !SR; });
$('nt-lang').value = store.get('speechLang', 'en-NG'); $('nt-lang').addEventListener('change', (e) => store.set('speechLang', e.target.value));
const dNote = makeDictation({ input: $('nt-body'), button: $('nt-mic'), getLang: () => $('nt-lang').value, onText: () => { countWords(); queueSave(); },
  onState: (v) => { $('nt-hint').hidden = !v; $('nt-mic').lastElementChild.textContent = v ? 'Stop' : 'Speak'; },
  onDone: (t) => { if (A.ai_enabled && t.split(/\s+/).length >= 8) toast('Finished speaking?', () => runNoteAI('polish'), 'Tidy up with AI'); } });
$('nt-mic').addEventListener('click', () => { if (S.noteId === null) newNote().then(() => dNote.toggle()); else dNote.toggle(); });
const dQuick = makeDictation({ input: $('quick-input'), button: $('quick-mic'), getLang: () => $('nt-lang').value, once: true, onText: () => $('quick-input').dispatchEvent(new Event('input')) });
$('quick-mic').addEventListener('click', () => dQuick.toggle());

/* ---------- AI: diary tools ---------- */
let aiNoteRange = null;
async function runNoteAI(action) {
  if (!A.ai_enabled) return toast('AI is not set up on this server yet.');
  const body = $('nt-body'), sel = body.value.slice(body.selectionStart, body.selectionEnd).trim(), text = sel || body.value.trim();
  if (!text) return toast('Write or speak something first.');
  aiNoteRange = sel ? [body.selectionStart, body.selectionEnd] : null; const panel = $('nt-ai');
  $('nt-ai-title').textContent = { polish: 'Tidied version', summarize: 'Summary', reflect: 'Questions to think about' }[action];
  $('nt-ai-text').textContent = 'Thinking…'; panel.hidden = false; $('nt-ai-replace').hidden = true; $('nt-ai-append').hidden = true;
  const r = await api('POST', '/api/ai/note', { action, text });
  if (!r) { panel.hidden = true; return; }
  $('nt-ai-text').textContent = r.text; $('nt-ai-replace').hidden = action !== 'polish'; $('nt-ai-append').hidden = false; $('nt-ai-replace').dataset.action = action;
}
document.querySelectorAll('[data-ai-note]').forEach((b) => b.addEventListener('click', () => runNoteAI(b.dataset.aiNote)));
$('nt-ai-x').addEventListener('click', () => { $('nt-ai').hidden = true; });
$('nt-ai-replace').addEventListener('click', () => { const b = $('nt-body'), t = $('nt-ai-text').textContent;
  b.value = aiNoteRange ? b.value.slice(0, aiNoteRange[0]) + t + b.value.slice(aiNoteRange[1]) : t; countWords(); queueSave(); $('nt-ai').hidden = true; toast('Entry updated'); });
$('nt-ai-append').addEventListener('click', () => { const b = $('nt-body'); b.value = (b.value.trimEnd() + '\n\n' + $('nt-ai-text').textContent).trim(); countWords(); queueSave(); $('nt-ai').hidden = true; });

/* ---------- AI assistant chat ---------- */
const aiDlg = $('ai-dlg'); let aiMode = 'chat'; const aiHist = [];
const bubble = (cls, ...kids) => { const m = h('div', { class: 'msg ' + cls }, ...kids); $('ai-log').append(m); $('ai-log').scrollTop = 1e9; return m; };
function openAI() {
  aiDlg.showModal(); $('ai-note').textContent = A.ai_enabled ? 'AI can make mistakes. Your task titles and today’s timetable are shared with the AI service to answer you.' : 'AI is not set up on this server yet. The site owner needs to add an API key.';
  if (!$('ai-log').children.length) bubble('bot', `Hi ${boot.user.name}! I can plan your day, break a big goal into steps, or help you fit study time into your timetable. What would you like?`);
  $('ai-input').focus();
}
const AI_CHIPS = [['Plan my day', 'What should I do first today? Give me a simple plan for the rest of my day.'], ['Break down a goal', null],
  ['Study time', 'Look at my timetable and tasks and suggest when I can study this week.'], ['Motivate me', 'Give me a short, encouraging message to help me start my next task.']];
$('ai-chips').replaceChildren(...AI_CHIPS.map(([l, q]) => h('button', { type: 'button', onclick: () => { if (q) sendAI(q); else { aiMode = 'goal'; $('ai-input').placeholder = 'Describe your goal, e.g. “Prepare for my chemistry exam”'; $('ai-input').focus(); bubble('bot', 'Tell me the goal and I will turn it into a checklist.'); } } }, l)));
async function sendAI(text) {
  text = (text || '').trim(); if (!text) return; if (!A.ai_enabled) return toast('AI is not set up on this server yet.');
  bubble('user', text); $('ai-input').value = ''; const typing = bubble('bot typing', 'Thinking…'); $('ai-send').disabled = true;
  if (aiMode === 'goal') {
    aiMode = 'chat'; $('ai-input').placeholder = 'Ask me anything about your day…';
    const r = await api('POST', '/api/ai/breakdown', { goal: text }); typing.remove(); $('ai-send').disabled = false;
    if (!r) return bubble('bot', 'Sorry, I could not do that right now.');
    const m = bubble('bot', h('b', {}, r.title), h('ol', {}, r.steps.map((x) => h('li', {}, x))),
      h('button', { class: 'btn primary sm', type: 'button', onclick: async (e) => { e.target.disabled = true;
        const t = await api('POST', '/api/tasks', { title: r.title, priority: 'medium', alarm_sound: store.get('defSound', 'classic'), subtasks: r.steps.map((x) => ({ title: x, done: false })) });
        if (t) { put(t.task); S.open.add(t.task.id); render(); e.target.textContent = 'Added to your tasks ✓'; toast('Task with checklist added'); } else e.target.disabled = false; } }, 'Add as a task with checklist'));
    return m;
  }
  aiHist.push({ role: 'user', content: text });
  const r = await api('POST', '/api/ai/chat', { messages: aiHist, now: ymdhm(new Date()) }); typing.remove(); $('ai-send').disabled = false;
  if (!r) { aiHist.pop(); return bubble('bot', 'Sorry, I could not answer just now. Please try again.'); }
  aiHist.push({ role: 'assistant', content: r.reply }); bubble('bot', r.reply);
}
$('ai-form').addEventListener('submit', (e) => { e.preventDefault(); sendAI($('ai-input').value); });
const dAI = makeDictation({ input: $('ai-input'), button: $('ai-mic'), getLang: () => $('nt-lang').value, once: true, onDone: (t) => sendAI(t) });
$('ai-mic').addEventListener('click', () => dAI.toggle());
aiDlg.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => { dAI.stop(); closeDlg(aiDlg); }));
aiDlg.addEventListener('close', () => dAI.stop());
['btn-ai', 'nav-ai'].forEach((id) => $(id).addEventListener('click', openAI));

/* ---------- email & recovery settings ---------- */
function paintEmail() {
  $('em-input').value = A.email || '';
  $('em-status').textContent = !A.email ? 'Add your email so you can recover your username or password, and receive reminders.'
    : A.verified ? 'Confirmed ✓ — we can email you reminders and recovery links.' : 'Not confirmed yet. Open the confirmation link we emailed you.';
  if (!A.mail_enabled) $('em-status').textContent += ' (Email sending is not set up on this server yet.)';
  $('em-resend').hidden = !A.email || A.verified; $('em-rem').checked = !!A.email_reminders; $('em-dig').checked = !!A.email_digest;
}
$('em-save').addEventListener('click', async () => {
  const r = await api('POST', '/api/account/email', { email: $('em-input').value }); if (!r) return;
  A.email = $('em-input').value.trim().toLowerCase(); A.verified = r.verified; paintEmail();
  toast(r.warning ? 'Saved, but the confirmation email failed to send.' : r.verified ? 'Email saved' : 'Saved. Check your inbox to confirm it.');
});
$('em-resend').addEventListener('click', async () => { if (await api('POST', '/api/account/resend')) toast('Confirmation email sent'); });
$('em-test').addEventListener('click', async () => { if (await api('POST', '/api/account/test-email')) toast('Test email sent. Check your inbox.'); });
$('em-rem').addEventListener('change', (e) => { A.email_reminders = e.target.checked; api('POST', '/api/account/prefs', { email_reminders: e.target.checked }); });
$('em-dig').addEventListener('change', (e) => { A.email_digest = e.target.checked; api('POST', '/api/account/prefs', { email_digest: e.target.checked }); });
try { const tz = Intl.DateTimeFormat().resolvedOptions().timeZone; if (tz && A.tz !== tz) { A.tz = tz; api('POST', '/api/account/timezone', { tz }); } } catch (e) {}
paintEmail();

/* ---------- boot ---------- */
[ed, tdlg, pd, aiDlg].forEach((d) => d.addEventListener('click', (e) => { if (e.target === d) closeDlg(d); }));
document.addEventListener('keydown', (e) => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || ed.open || tdlg.open || pd.open || aiDlg.open || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'n') { e.preventDefault(); $('fab').click(); } if (e.key === '/') { e.preventDefault(); go('tasks').then(() => $('search').focus()); }
});
function tick() { checkReminders(); checkTimetable(); focusTick(); }
try { const w = new Worker(URL.createObjectURL(new Blob(['setInterval(()=>postMessage(1),1000)']))); w.onmessage = tick; } catch (e) { setInterval(tick, 1000); }
setInterval(refresh, 45000); setInterval(render, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); tick(); } });
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});

sortNotes();
let start = location.hash.slice(1); if (start === 'calendar') { start = 'tasks'; S.tview = 'cal'; }
go(TABS.includes(start) ? start : 'home'); paintFocus(); paintSound(); checkReminders();
if (new URLSearchParams(location.search).has('new')) { history.replaceState(null, '', '/app'); openEditor(null); }
})();
