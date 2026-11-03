/* coco · shared plumbing: state, i18n, requests, Markdown, dialogs, views */

export const $ = id => document.getElementById(id);
export const qs = (sel, root = document) => root.querySelector(sel);
export const qsa = (sel, root = document) => [...root.querySelectorAll(sel)];
const GUESS_PLATFORM = /win/i.test(navigator.platform) ? 'windows' : /mac/i.test(navigator.platform) ? 'mac' : 'linux';

export const state = {
  view: 'library',
  caps: { transcribe: '', record: true, platform: GUESS_PLATFORM, device: '', textExts: [], audioExts: [], recordMode: 'browser' },
  cfg: {}, meetings: [], current: null, currentData: null, tab: 'transcript', editing: false,
  selected: new Set(), filterFrom: '', filterTo: '', searchQuery: '', chat: [], pending: null,
  templates: [], trackModes: null, recording: false, recSeconds: 0, quitting: false,
};

/* ---------- i18n ---------- */
export const I18N = { lang: 'en', strings: {}, available: [] };
export const OUTPUT_LANGS = [['zh-CN', '简体中文'], ['en', 'English'], ['fr', 'Français'], ['de', 'Deutsch'], ['es', 'Español'],
  ['it', 'Italiano'], ['pt', 'Português'], ['ja', '日本語'], ['ko', '한국어'], ['ru', 'Русский'], ['ar', 'العربية'],
  ['hi', 'हिन्दी'], ['th', 'ไทย'], ['vi', 'Tiếng Việt'], ['id', 'Bahasa Indonesia'], ['nl', 'Nederlands'], ['tr', 'Türkçe'], ['pl', 'Polski']];

export function t(key, vars) {
  let cur = I18N.strings;
  for (const part of key.split('.')) cur = cur && typeof cur === 'object' ? cur[part] : undefined;
  let s = typeof cur === 'string' ? cur : key;
  if (vars) for (const k of Object.keys(vars)) s = s.split(`{${k}}`).join(String(vars[k]));
  return s;
}
export function applyI18n(root = document) {
  document.documentElement.lang = I18N.lang;
  document.title = 'coco · ' + t('app.subtitle');
  qsa('[data-i18n]', root).forEach(el => { el.textContent = t(el.dataset.i18n); });
  qsa('[data-i18n-title]', root).forEach(el => { el.title = t(el.dataset.i18nTitle); });
  qsa('[data-i18n-placeholder]', root).forEach(el => { el.placeholder = t(el.dataset.i18nPlaceholder); });
}
export function pickLang(cfg) {
  const fromUrl = new URLSearchParams(location.search).get('lang');  // ?lang=fr forces a language (and remembers it)
  if (fromUrl) { localStorage.setItem('coco-lang', fromUrl); return fromUrl; }
  const saved = localStorage.getItem('coco-lang');
  if (saved) return saved;
  if (cfg.ui_language && cfg.ui_language !== 'auto') return cfg.ui_language;
  const nav = (navigator.language || 'en').toLowerCase();
  if (nav.startsWith('zh')) return 'zh-CN';
  if (nav.startsWith('fr')) return 'fr';
  return 'en';
}
export async function loadI18n(lang) {
  const r = await fetch('/api/i18n?lang=' + encodeURIComponent(lang));
  const d = await r.json();
  Object.assign(I18N, { lang: d.lang, strings: d.strings, available: d.available });
  applyI18n();
}

/* ---------- small utilities ---------- */
export function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
export function throttle(fn, ms) {
  let last = 0, tm = null;
  return (...a) => {
    const now = Date.now();
    if (now - last >= ms) { last = now; fn(...a); }
    else { clearTimeout(tm); tm = setTimeout(() => { last = Date.now(); fn(...a); }, ms - (now - last)); }
  };
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export function toast(msg, ms = 3600) {
  const el = $('toast');
  el.textContent = msg; el.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => el.classList.remove('show'), ms);
}
export function fmtDate(iso) { return iso ? String(iso).slice(0, 16).replace('T', ' ') : ''; }

/* ---------- requests ---------- */
export async function api(path, opts = {}) {
  opts.headers = Object.assign({ 'X-Coco-Lang': I18N.lang }, opts.headers || {});
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
export const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}) });
export const del = path => api(path, { method: 'DELETE' });

/* Server-sent events over fetch: onDelta(text) per fragment; resolves with the `done` payload. */
export async function streamApi(path, body, onDelta) {
  const res = await fetch(path, { method: 'POST', body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json', 'X-Coco-Lang': I18N.lang } });
  if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || res.statusText); }
  const reader = res.body.getReader(); const dec = new TextDecoder();
  let buf = '', result = null, error = null;
  const handle = frame => {
    let ev = 'message', data = '';
    frame.split('\n').forEach(l => { if (l.startsWith('event:')) ev = l.slice(6).trim(); else if (l.startsWith('data:')) data += l.slice(5).trim(); });
    if (!data) return;
    let d; try { d = JSON.parse(data); } catch { return; }
    if (ev === 'delta') onDelta(d.text || '');
    else if (ev === 'done') result = d;
    else if (ev === 'error') error = d.detail || 'error';
  };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) { handle(buf.slice(0, idx)); buf = buf.slice(idx + 2); }
  }
  if (buf.trim()) handle(buf);
  if (error) throw new Error(error);
  if (!result) throw new Error(t('common.stream_interrupted'));
  return result;
}

/* Poll a background job until it ends: onProgress(detail) while running. */
export function pollJob(jid, onProgress, every = 2000) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const j = await api('/api/jobs/' + jid);
        if (j.status === 'done') { clearInterval(timer); resolve(j); }
        else if (j.status === 'error') { clearInterval(timer); reject(new Error(j.detail)); }
        else if (onProgress) onProgress(j.detail || '');
      } catch (e) { clearInterval(timer); reject(e); }
    }, every);
  });
}

/* ---------- Markdown (headings, bold, lists, tables, quotes, code, links, timestamps) ---------- */
export function md(src, highlight = '') {
  const lines = esc(src || '').split('\n');
  let html = '', listMode = '', inTable = false;
  const hl = highlight ? new RegExp(highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi') : null;
  const closeList = () => { if (listMode) { html += `</${listMode}>`; listMode = ''; } };
  const closeTable = () => { if (inTable) { html += '</table>'; inTable = false; } };
  const inline = s => {
    let out = s
      .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\[(\d+:\d{2}(?::\d{2})?)\]/g, '<span class="ts">[$1]</span>');
    if (hl) out = out.replace(/(^|>)([^<]+)/g, (m, a, b) => a + b.replace(hl, x => `<mark>${x}</mark>`));
    return out;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\|.+\|$/.test(line.trim())) {
      closeList();
      const cells = line.trim().slice(1, -1).split('|').map(c => c.trim());
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) continue;
      if (!inTable) { html += '<table>'; inTable = true; html += '<tr>' + cells.map(c => `<th>${inline(c)}</th>`).join('') + '</tr>'; }
      else html += '<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>';
      continue;
    }
    closeTable();
    let m;
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { closeList(); html += '<hr>'; }
    else if ((m = line.match(/^(#{1,4})\s+(.+)/))) { closeList(); const n = Math.min(m[1].length, 3); html += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.+)/))) { if (listMode !== 'ul') { closeList(); html += '<ul>'; listMode = 'ul'; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+[.、)]\s+(.+)/))) { if (listMode !== 'ol') { closeList(); html += '<ol>'; listMode = 'ol'; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^(?:>|&gt;)\s?(.*)/))) { closeList(); html += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if (line === '') closeList();
    else { closeList(); html += `<p>${inline(line)}</p>`; }
  }
  closeList(); closeTable();
  return html;
}

/* ---------- dialogs ---------- */
const openStack = [];
export function openDialog(id) { const el = $(id); el.classList.add('show'); if (!openStack.includes(id)) openStack.push(id); }
export function closeDialog(id) {
  const el = $(id); if (!el) return;
  el.classList.remove('show');
  const i = openStack.indexOf(id); if (i >= 0) openStack.splice(i, 1);
  if (el._onclose) { const f = el._onclose; el._onclose = null; f(); }
}
export function initDialogs() {
  qsa('.ovl').forEach(o => o.addEventListener('click', e => { if (e.target === o) closeDialog(o.id); }));
  qsa('[data-close]').forEach(b => b.addEventListener('click', () => closeDialog(b.dataset.close)));
  window.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (closeMenu()) return;
    const top = openStack[openStack.length - 1];
    if (top) closeDialog(top);
  });
}

/* choice dialog → key of the button pressed (null on Esc / backdrop) */
export function choose(title, body, buttons) {
  return new Promise(resolve => {
    $('choose-title').textContent = title;
    $('choose-body').textContent = body;
    const row = $('choose-buttons'); row.innerHTML = '<span class="fill"></span>';
    let result = null;
    $('choose-dialog')._onclose = () => resolve(result);
    buttons.forEach(([key, label, primary]) => {
      const b = document.createElement('button'); b.textContent = label; if (primary) b.className = 'primary';
      b.onclick = () => { result = key; closeDialog('choose-dialog'); };
      row.appendChild(b);
    });
    openDialog('choose-dialog');
  });
}
export const confirmBox = async (title, body, ok) =>
  (await choose(title, body, [['no', t('common.cancel')], ['yes', ok || t('common.ok'), true]])) === 'yes';

/* one-line text input dialog → string, or null when cancelled */
export function promptText(title, body = '', value = '', options = []) {
  return new Promise(resolve => {
    $('prompt-title').textContent = title;
    $('prompt-body').textContent = body;
    const input = $('prompt-input'); input.value = value;
    $('prompt-options').innerHTML = options.map(o => `<option value="${esc(o)}">`).join('');
    let result = null;
    $('prompt-dialog')._onclose = () => resolve(result);
    $('prompt-ok').onclick = () => { result = input.value.trim(); closeDialog('prompt-dialog'); };
    $('prompt-cancel').onclick = () => closeDialog('prompt-dialog');
    input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('prompt-ok').click(); } };
    openDialog('prompt-dialog');
    setTimeout(() => input.focus(), 30);
  });
}

/* ---------- popover menu ---------- */
let menuEl = null;
export function closeMenu() { if (!menuEl) return false; menuEl.remove(); menuEl = null; return true; }
export function menu(anchor, items) {
  closeMenu();
  const m = document.createElement('div'); m.className = 'menu';
  items.forEach(it => {
    if (it === '-') { m.appendChild(document.createElement('hr')); return; }
    const b = document.createElement('button'); b.textContent = it.label;
    if (it.bad) b.className = 'bad-text';
    b.disabled = !!it.disabled;
    b.onclick = () => { closeMenu(); it.run(); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(r.left, window.innerWidth - m.offsetWidth - 12) + 'px';
  m.style.top = (r.bottom + 4 + window.scrollY) + 'px';
  menuEl = m;
  setTimeout(() => document.addEventListener('click', closeMenu, { once: true }), 0);
}

/* ---------- views ---------- */
const views = {};
export function registerView(name, impl) { views[name] = impl; }
export function setView(name, opts) {
  state.view = name;
  qsa('.nav-item[data-view]').forEach(b => b.classList.toggle('on', b.dataset.view === name));
  qsa('.list-view').forEach(v => { v.hidden = v.dataset.list !== name; });
  qsa('#content > .view').forEach(v => { v.hidden = v.dataset.view !== name; });
  $('list').hidden = name === 'live' ? $('list').hidden : false;
  if (views[name] && views[name].show) views[name].show(opts || {});
  emit('view', name);
}
/* a streaming result that is not tied to a meeting (brief, prep, insight while generating) */
export function showLive(title, headline) {
  const box = $('live-content');
  qsa('#content > .view').forEach(v => { v.hidden = v.dataset.view !== 'live'; });
  box.innerHTML = `<h1>${esc(title)}</h1><div class="live-head">${esc(headline)}</div><div class="md cursor" id="live-md"></div>`;
  return throttle(text => { const el = $('live-md'); if (el) { el.innerHTML = md(text); box.scrollTop = box.scrollHeight; } }, 160);
}

/* ---------- tiny event bus ---------- */
const handlers = {};
export function on(ev, fn) { (handlers[ev] = handlers[ev] || []).push(fn); }
export function emit(ev, data) { (handlers[ev] || []).forEach(fn => { try { fn(data); } catch (e) { console.error(e); } }); }

export function download(url) { window.location.href = url + (url.includes('?') ? '&' : '?') + 'lang=' + I18N.lang; }
