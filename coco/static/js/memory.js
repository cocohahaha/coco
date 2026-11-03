/* coco · long-term memory: browse, search and edit entries; glossary and Markdown tools */
import { $, qsa, state, t, esc, md, api, post, del, toast, confirmBox, promptText, openDialog, closeDialog,
  registerView, pollJob, download, fmtDate } from './core.js';
import { askAbout } from './chat.js';
import { openMeeting } from './library.js';

const TYPES = ['', 'person', 'project', 'commitment', 'term'];
const STATUSES = ['open', '', 'done', 'cancelled', 'unknown'];
const ms = { type: '', status: 'open', q: '', current: null, stats: {}, items: [], editing: false };
let searchTimer;

/* ---------- list ---------- */
async function loadList() {
  const p = new URLSearchParams();
  if (ms.type) p.set('type', ms.type);
  if (ms.q) p.set('q', ms.q);
  if (ms.type === 'commitment' && ms.status) p.set('status', ms.status);
  let r;
  try { r = await api('/api/memory/entities?' + p.toString()); } catch (e) { toast(e.message, 6000); return; }
  ms.items = r.items; ms.stats = r.stats;
  renderTypes(); renderList();
}

function renderTypes() {
  const st = ms.stats || {};
  const total = TYPES.slice(1).reduce((n, k) => n + (st[k] || 0), 0);
  $('mem-types').innerHTML = TYPES.map(k => {
    const n = k ? (st[k] || 0) : total;
    return `<button data-type="${k}" class="${ms.type === k ? 'on' : ''}">${esc(k ? t('mem.type_' + k) : t('mem.type_all'))} ${n}</button>`;
  }).join('');
  qsa('#mem-types button').forEach(b => { b.onclick = () => { ms.type = b.dataset.type; loadList(); }; });
  const ss = $('mem-status');
  ss.hidden = ms.type !== 'commitment';
  ss.innerHTML = STATUSES.map(k => `<button data-st="${k}" class="${ms.status === k ? 'on' : ''}">${esc(k ? t('mem.status_' + k) : t('mem.status_all'))}</button>`).join('');
  qsa('#mem-status button').forEach(b => { b.onclick = () => { ms.status = b.dataset.st; loadList(); }; });
}

function itemSub(e) {
  const bits = [];
  if (!ms.type) bits.push(t('mem.type_' + e.type));
  if (e.type === 'commitment' && e.status) bits.push(t('mem.status_' + e.status));
  if (e.owner) bits.push(e.owner);
  if (e.facts) bits.push(t('mem.n_facts', { n: e.facts }));
  if (e.updated) bits.push(e.updated.slice(0, 10));
  return bits.map(esc).join(' · ');
}

function renderList() {
  const list = $('mem-list');
  if (!ms.items.length) {
    list.innerHTML = `<div class="list-empty">${esc(ms.q ? t('mem.none_match') : t('mem.empty'))}</div>`;
    return;
  }
  list.innerHTML = '';
  ms.items.forEach(e => {
    const div = document.createElement('div');
    div.className = 'item' + (ms.current === e.id ? ' on' : '');
    const text = e.snippet || e.summary || '';
    div.innerHTML = `<div class="body"><div class="t">${esc(e.name)}</div><div class="s">${itemSub(e)}</div>
      ${text ? `<div class="d">${esc(text)}</div>` : ''}</div>`;
    div.onclick = () => openEntity(e.id);
    list.appendChild(div);
  });
}

/* ---------- overview ---------- */
async function showOverview() {
  ms.current = null; ms.editing = false; renderList();
  const box = $('mem-content');
  let k;
  try { k = await api('/api/knowledge'); } catch (e) { box.innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; return; }
  const s = k.stats;
  const recent = (await api('/api/memory/entities').catch(() => ({ items: [] }))).items.slice(0, 10);
  const stat = (n, label) => `<div><b>${esc(n)}</b><span>${esc(label)}</span></div>`;
  box.innerHTML = `
    <h1>${esc(t('mem.title'))}</h1>
    <p class="muted small" style="max-width:44em;margin-top:6px">${esc(t('mem.lead'))}</p>
    <div class="stats">
      ${stat(s.meetings, t('knowledge.stat_meetings'))}${stat(s.persons, t('mem.type_person'))}${stat(s.projects, t('mem.type_project'))}
      ${stat(s.promises, t('mem.stat_commitments', { open: s.open }))}${stat(s.terms, t('mem.type_term'))}${stat(s.glossary, t('knowledge.stat_glossary'))}
    </div>
    <div class="tools">
      <button class="link" id="mt-markdown">${esc(t('mem.markdown'))}</button>
      <button class="link" id="mt-compact" title="${esc(t('memory.compact_title'))}">${esc(t('memory.compact'))}</button>
      <button class="link" id="mt-glossary" title="${esc(t('memory.extract_title'))}">${esc(t('memory.extract'))}</button>
      <button class="link" id="mt-fixall">${esc(t('memory.fix_all'))}</button>
    </div>
    ${k.person_names.length ? `<div class="section-title">${esc(t('mem.people_index'))}</div>
      <div class="names">${k.person_names.map(n => `<button class="link" data-person="${esc(n)}">${esc(n)}</button>`).join('')}</div>` : ''}
    ${recent.length ? `<div class="section-title">${esc(t('mem.recent'))}</div>
      <div class="facts">${recent.map(e => `<div class="fact"><span class="when">${esc((e.updated || '').slice(0, 10))}</span>
        <span><button class="link" data-ent="${esc(e.id)}">${esc(e.name)}</button>
        <span class="muted small"> · ${esc(t('mem.type_' + e.type))}</span><br>${esc((e.summary || '').slice(0, 160))}</span></div>`).join('')}</div>` : ''}`;
  $('mt-markdown').onclick = () => openMarkdown('longterm');
  $('mt-compact').onclick = e => runCompact(e.target);
  $('mt-glossary').onclick = e => runExtractGlossary(e.target);
  $('mt-fixall').onclick = e => runFixAll(e.target);
  qsa('[data-person]', box).forEach(b => { b.onclick = async () => {
    const r = await api('/api/memory/entities?type=person&q=' + encodeURIComponent(b.dataset.person));
    const hit = r.items.find(i => i.name === b.dataset.person) || r.items[0];
    if (hit) openEntity(hit.id);
  }; });
  qsa('[data-ent]', box).forEach(b => { b.onclick = () => openEntity(b.dataset.ent); });
}

/* ---------- one entry ---------- */
export async function openEntity(id) {
  ms.current = id; ms.editing = false; renderList();
  let e;
  try { e = await api('/api/memory/entities/' + encodeURIComponent(id)); }
  catch (err) { toast(err.message, 6000); return showOverview(); }
  renderEntity(e);
}

function entityMeta(e) {
  const bits = [t('mem.type_' + e.type)];
  if (e.aliases && e.aliases.length) bits.push(t('mem.aka', { names: e.aliases.join('、') }));
  if (e.type === 'commitment') {
    bits.push(t('mem.status_' + (e.status || 'unknown')));
    if (e.owner) bits.push(t('mem.owner_is', { owner: e.owner }));
    if (e.due) bits.push(t('mem.due_is', { due: e.due }));
  }
  if (e.updated) bits.push(t('mem.updated', { date: fmtDate(e.updated) }));
  return bits.map(esc).join(' · ');
}

function renderEntity(e) {
  const box = $('mem-content');
  const facts = (e.facts || []).slice().reverse();  // newest first
  const canAsk = e.type === 'person' || e.type === 'project';
  box.innerHTML = `
    <div class="ent-head"><h1>${esc(e.name)}</h1>
      <div class="ent-actions">
        ${canAsk ? `<button class="link" id="me-ask">${esc(t('mem.ask_about'))}</button>` : ''}
        <button class="link" id="me-edit">${esc(t('common.edit'))}</button>
        <button class="link" id="me-tidy" title="${esc(t('mem.tidy_title'))}">${esc(t('mem.tidy'))}</button>
        <button class="link" id="me-merge">${esc(t('mem.merge'))}</button>
        <button class="link bad" id="me-del">${esc(t('common.delete'))}</button>
      </div></div>
    <div class="meta">${entityMeta(e)}</div>
    ${e.summary ? `<div class="ent-summary md">${md(e.summary)}</div>` : `<p class="muted small ent-summary">${esc(t('mem.no_summary'))}</p>`}
    <div class="section-title">${esc(t('mem.facts', { n: facts.length }))}</div>
    <div class="facts">${facts.map(f => `<div class="fact"><span class="when">${esc(f.date || '')}</span>
      <span>${esc(f.text)}${f.source ? `<button class="link src" data-src="${esc(f.source)}">${esc(f.source_title || f.source)}</button>` : ''}</span></div>`).join('')
      || `<p class="muted small" style="padding:10px 0">${esc(t('mem.no_facts'))}</p>`}</div>
    ${(e.sources || []).length ? `<div class="section-title">${esc(t('mem.sources'))}</div>
      <div class="names">${e.sources.map(s => s.exists
        ? `<button class="link" data-src="${esc(s.id)}">${esc(s.date ? s.date + ' ' : '')}${esc(s.title)}</button>`
        : `<span class="muted small" style="margin-right:14px">${esc(s.id)}</span>`).join('')}</div>` : ''}`;
  qsa('[data-src]', box).forEach(b => { b.onclick = () => openMeeting(b.dataset.src); });
  if (canAsk) $('me-ask').onclick = () => askAbout(t('knowledge.person_prompt', { name: e.name }), true);
  $('me-edit').onclick = () => renderEdit(e);
  $('me-tidy').onclick = async ev => {
    const b = ev.target; b.disabled = true; b.textContent = t('mem.tidying');
    try { await post(`/api/memory/entities/${encodeURIComponent(e.id)}/tidy`); toast(t('mem.tidy_done')); openEntity(e.id); loadList(); }
    catch (err) { toast(t('common.failed', { msg: err.message }), 8000); b.disabled = false; b.textContent = t('mem.tidy'); }
  };
  $('me-merge').onclick = async () => {
    const pool = (await api('/api/memory/entities?type=' + e.type)).items.filter(i => i.id !== e.id);
    const name = await promptText(t('mem.merge'), t('mem.merge_body', { name: e.name }), '', pool.map(i => i.name));
    if (!name) return;
    const target = pool.find(i => i.name === name) || pool.find(i => i.name.toLowerCase() === name.toLowerCase());
    if (!target) { toast(t('mem.merge_none', { name }), 6000); return; }
    try { const r = await post(`/api/memory/entities/${encodeURIComponent(e.id)}/merge`, { into: target.id }); toast(t('mem.merge_done', { name: target.name })); await loadList(); openEntity(r.id); }
    catch (err) { toast(t('common.failed', { msg: err.message }), 8000); }
  };
  $('me-del').onclick = async () => {
    if (!await confirmBox(t('common.delete'), t('mem.delete_confirm', { name: e.name }), t('common.delete'))) return;
    try { await del('/api/memory/entities/' + encodeURIComponent(e.id)); toast(t('mem.deleted')); await loadList(); showOverview(); }
    catch (err) { toast(t('common.failed', { msg: err.message }), 8000); }
  };
}

/* edit form (also used for a new entry: e.id empty) */
function renderEdit(e) {
  ms.editing = true;
  const box = $('mem-content');
  const isNew = !e.id;
  const typeOpts = TYPES.slice(1).map(k => `<option value="${k}"${e.type === k ? ' selected' : ''}>${esc(t('mem.type_' + k))}</option>`).join('');
  const stOpts = STATUSES.filter(Boolean).map(k => `<option value="${k}"${(e.status || 'unknown') === k ? ' selected' : ''}>${esc(t('mem.status_' + k))}</option>`).join('');
  box.innerHTML = `<div class="form">
    <h1>${esc(isNew ? t('mem.new') : t('mem.edit_title', { name: e.name }))}</h1>
    <div class="grid2">
      <div><label class="fld">${esc(t('mem.f_name'))}</label><input id="fe-name" class="full" value="${esc(e.name || '')}"></div>
      <div><label class="fld">${esc(t('mem.f_type'))}</label><select id="fe-type" class="full">${typeOpts}</select></div>
    </div>
    <label class="fld">${esc(t('mem.f_aliases'))}</label><input id="fe-aliases" class="full" value="${esc((e.aliases || []).join('、'))}">
    <div id="fe-commit" class="grid2">
      <div><label class="fld">${esc(t('mem.f_status'))}</label><select id="fe-status" class="full">${stOpts}</select></div>
      <div><label class="fld">${esc(t('mem.f_owner'))}</label><input id="fe-owner" class="full" value="${esc(e.owner || '')}"></div>
      <div><label class="fld">${esc(t('mem.f_due'))}</label><input id="fe-due" class="full" value="${esc(e.due || '')}"></div>
    </div>
    <label class="fld">${esc(t('mem.f_summary'))}</label><textarea id="fe-summary" class="full" rows="4">${esc(e.summary || '')}</textarea>
    <label class="fld">${esc(t('mem.f_facts'))}</label>
    <div id="fe-facts" class="facts"></div>
    <button class="link" id="fe-add">${esc(t('mem.add_fact'))}</button>
    <div class="row"><button class="primary" id="fe-save">${esc(t('common.save'))}</button><button id="fe-cancel">${esc(t('common.cancel'))}</button></div>
  </div>`;
  const facts = (e.facts || []).map(f => ({ text: f.text, date: f.date || '', source: f.source || '' }));
  const drawFacts = () => {
    $('fe-facts').innerHTML = facts.map((f, i) => `<div class="fact"><input class="when" data-i="${i}" data-k="date" value="${esc(f.date)}" placeholder="YYYY-MM-DD">
      <span><textarea data-i="${i}" data-k="text" rows="2" class="full">${esc(f.text)}</textarea>
      <span class="muted small">${esc(f.source)}</span><button class="link bad x" data-rm="${i}">${esc(t('common.remove'))}</button></span></div>`).join('');
    qsa('#fe-facts [data-k]').forEach(el => { el.oninput = () => { facts[+el.dataset.i][el.dataset.k] = el.value; }; });
    qsa('#fe-facts [data-rm]').forEach(b => { b.onclick = () => { facts.splice(+b.dataset.rm, 1); drawFacts(); }; });
  };
  drawFacts();
  const syncType = () => { $('fe-commit').hidden = $('fe-type').value !== 'commitment'; };
  $('fe-type').onchange = syncType; syncType();
  $('fe-add').onclick = () => { facts.push({ text: '', date: new Date().toISOString().slice(0, 10), source: '' }); drawFacts(); };
  $('fe-cancel').onclick = () => { ms.editing = false; isNew ? showOverview() : openEntity(e.id); };
  $('fe-save').onclick = async () => {
    const body = { name: $('fe-name').value.trim(), type: $('fe-type').value, summary: $('fe-summary').value.trim(),
      aliases: $('fe-aliases').value.split(/[、,，]/).map(s => s.trim()).filter(Boolean),
      facts: facts.filter(f => f.text.trim()) };
    if (body.type === 'commitment') Object.assign(body, { status: $('fe-status').value, owner: $('fe-owner').value.trim(), due: $('fe-due').value.trim() });
    try {
      const r = isNew ? await post('/api/memory/entities', body) : await post('/api/memory/entities/' + encodeURIComponent(e.id), body);
      toast(t('mem.saved')); ms.editing = false; await loadList(); openEntity(r.id);
    } catch (err) { toast(t('common.save_failed', { msg: err.message }), 8000); }
  };
}

/* ---------- tools ---------- */
async function runCompact(btn) {
  if (!await confirmBox(t('memory.compact'), t('memory.compact_confirm'), t('memory.compact'))) return;
  const label = btn.textContent; btn.disabled = true; btn.textContent = t('memory.compacting');
  try {
    const { job } = await post('/api/memory/compact');
    const j = await pollJob(job, d => { btn.textContent = d.length > 28 ? d.slice(0, 28) + '…' : d; });
    toast(j.detail || t('memory.compact_done'), 6000); loadList();
  } catch (e) { toast(t('common.failed', { msg: e.message }), 9000); }
  btn.disabled = false; btn.textContent = label;
}
async function runExtractGlossary(btn) {
  if (!await confirmBox(t('memory.extract'), t('memory.extract_confirm'), t('memory.extract'))) return;
  const label = btn.textContent; btn.disabled = true; btn.textContent = t('memory.extracting');
  toast(t('memory.extract_toast'), 8000);
  try { await post('/api/glossary/extract'); toast(t('memory.extract_done')); }
  catch (e) { toast(t('memory.extract_failed', { msg: e.message }), 9000); }
  btn.disabled = false; btn.textContent = label;
}
async function runFixAll(btn) {
  if (!await confirmBox(t('memory.fix_all'), t('memory.fixall_confirm'), t('memory.fix_all'))) return;
  const label = btn.textContent; btn.disabled = true; btn.textContent = t('memory.fixing');
  try {
    const { job } = await post('/api/fix-all-names', { confirm: true });
    const j = await pollJob(job, d => { btn.textContent = d.length > 28 ? d.slice(0, 28) + '…' : d; });
    toast(j.detail || t('memory.fixall_done'), 8000);
  } catch (e) { toast(t('memory.fixall_start_failed', { msg: e.message }), 9000); }
  btn.disabled = false; btn.textContent = label;
}

/* ---------- the three memory texts as Markdown (manual notes, long-term memory, glossary) ---------- */
let memData = { content: '', longterm: '', glossary: '' }, memLoaded = {}, memTab = 'longterm';
function setMemTab(tab) {
  memData[memTab] = $('memory-text').value;
  memTab = tab;
  $('memory-text').value = memData[tab];
  $('mem-hint').textContent = t('memory.hint_' + tab);
  qsa('#mem-tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
}
export async function openMarkdown(tab = 'longterm') {
  const r = await api('/api/memory');
  memData = { content: r.content, longterm: r.longterm, glossary: r.glossary };
  memLoaded = { ...memData };
  memTab = tab;
  $('memory-text').value = memData[tab];
  setMemTab(tab);
  openDialog('memory-dialog');
}
async function saveMarkdown() {
  memData[memTab] = $('memory-text').value;
  const changed = {};
  for (const k of Object.keys(memData)) if (memData[k] !== memLoaded[k]) changed[k] = memData[k];
  if (!Object.keys(changed).length) { closeDialog('memory-dialog'); toast(t('memory.no_change')); return; }
  try {
    await post('/api/memory', changed);
    memLoaded = { ...memData };
    closeDialog('memory-dialog'); toast(t('memory.saved'));
    if (state.view === 'memory') { loadList(); ms.current ? openEntity(ms.current) : showOverview(); }
  } catch (e) { toast(t('common.save_failed', { msg: e.message }), 8000); }
}
async function clearMarkdown() {
  const label = t('memory.label_' + memTab);
  let msg = t('memory.confirm_clear', { label });
  if (memTab === 'longterm') msg += t('memory.confirm_clear_long');
  if (!await confirmBox(t('memory.clear'), msg, t('memory.clear'))) return;
  try {
    const r = await post('/api/memory/clear', { which: memTab });
    memData[memTab] = r.content; memLoaded[memTab] = r.content; $('memory-text').value = r.content;
    toast(t('memory.cleared', { label }));
    if (state.view === 'memory') { loadList(); showOverview(); }
  } catch (e) { toast(t('memory.clear_failed', { msg: e.message }), 6000); }
}

export function initMemory() {
  $('mem-search').addEventListener('input', e => {
    ms.q = e.target.value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadList, 250);
  });
  $('mem-overview').onclick = showOverview;
  $('mem-new').onclick = () => { ms.current = null; renderList(); renderEdit({ type: ms.type || 'person', name: '', aliases: [], facts: [] }); };
  qsa('#mem-tabs button').forEach(b => { b.onclick = () => setMemTab(b.dataset.tab); });
  $('btn-save-memory').onclick = saveMarkdown;
  $('btn-clear-memory').onclick = clearMarkdown;
  $('btn-dl-memory').onclick = () => download('/api/download/memory/' + memTab);
  registerView('memory', {
    show(opts) {
      loadList();
      if (opts.entity) openEntity(opts.entity);
      else if (!ms.current && !ms.editing) showOverview();
    },
  });
}
