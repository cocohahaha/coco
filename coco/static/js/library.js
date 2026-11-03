/* coco · meetings: list, search, meeting view, reports, upload / import, recording, jobs */
import { $, qsa, state, t, I18N, esc, md, api, post, del, streamApi, toast, throttle, sleep, choose, confirmBox,
  openDialog, closeDialog, menu, registerView, setView, emit, on, fmtDate, download } from './core.js';

/* ---------- list ---------- */
export async function loadMeetings(keepCurrent = true) {
  state.meetings = await api('/api/meetings');
  if (!state.searchQuery) renderList();
  if (keepCurrent && state.current) refreshIfStale();
  updateSelBar();
}

function effectiveMeetings() {
  let ms = state.meetings;
  if (state.filterFrom) ms = ms.filter(m => (m.date || '') >= state.filterFrom);
  if (state.filterTo) ms = ms.filter(m => (m.date || '') <= state.filterTo);
  return ms;
}

function statusText(m) {
  if (m.status === 'transcribing') return `<span class="status run">${esc(m.job_detail || t('doc.transcribing'))}</span>`;
  if (m.status === 'recording') return `<span class="status bad">${esc(t('library.status_recording'))}</span>`;
  if (m.status === 'error') return `<span class="status bad">${esc(t('library.status_error'))}</span>`;
  if (m.status === 'new' && !m.has_transcript) return `<span class="status">${esc(t('library.status_new'))}</span>`;
  return '';
}

function renderList() {
  if (state.searchQuery || state.editingDate) return;
  const ms = effectiveMeetings();
  const sig = JSON.stringify([ms, state.current, state.filterFrom, state.filterTo, I18N.lang, [...state.selected]]);
  if (sig === renderList._sig) return;
  renderList._sig = sig;
  const list = $('mtg-list');
  if (!state.meetings.length) { list.innerHTML = `<div class="list-empty">${esc(t('sidebar.empty'))}</div>`; return; }
  if (!ms.length) { list.innerHTML = `<div class="list-empty">${esc(t('sidebar.no_match_date'))}</div>`; return; }
  list.innerHTML = '';
  for (const m of ms) {
    const div = document.createElement('div');
    div.className = 'item' + (state.current === m.id ? ' on' : '');
    const time = m.created && m.created.includes('T') ? m.created.slice(11, 16) : '';
    const st = statusText(m);
    div.innerHTML = `<input type="checkbox" class="sel"${state.selected.has(m.id) ? ' checked' : ''} title="${esc(t('sidebar.sel_title'))}">
      <div class="body">
        <div class="t">${esc(m.title)}</div>
        <div class="s"><span class="date" title="${esc(t('sidebar.date_title'))}">${esc(m.date || '')}</span> ${esc(time)}${m.duration ? ' · ' + esc(m.duration) : ''}${st ? ' · ' + st : ''}</div>
        ${m.digest ? `<div class="d">${esc(m.digest)}</div>` : ''}
      </div>`;
    div.onclick = () => openMeeting(m.id);
    const cb = div.querySelector('.sel');
    cb.onclick = e => { e.stopPropagation(); if (cb.checked) state.selected.add(m.id); else state.selected.delete(m.id); updateSelBar(); };
    div.querySelector('.date').onclick = e => { e.stopPropagation(); editDate(m, e.target); };
    list.appendChild(div);
  }
}

function updateSelBar() {
  const ids = new Set(state.meetings.map(m => m.id));
  for (const id of [...state.selected]) if (!ids.has(id)) state.selected.delete(id);
  const n = state.selected.size;
  $('sel-bar').hidden = !n || !!state.searchQuery;
  if (!n) return;
  const visible = new Set(effectiveMeetings().map(m => m.id));
  const hidden = [...state.selected].filter(id => !visible.has(id)).length;
  $('sel-count').textContent = hidden ? t('sidebar.selected_hidden', { n, hidden }) : t('sidebar.selected', { n });
}

function editDate(m, span) {
  state.editingDate = true;
  const input = document.createElement('input');
  input.type = 'date'; input.className = 'date-edit'; input.value = m.date || '';
  span.replaceWith(input); input.focus();
  let done = false;
  const finish = async save => {
    if (done) return; done = true; state.editingDate = false; renderList._sig = null;
    if (save && input.value && input.value !== m.date) {
      try { await post(`/api/meetings/${encodeURIComponent(m.id)}/meta`, { date: input.value }); toast(t('meeting.date_updated')); }
      catch (e) { toast(t('meeting.update_failed', { msg: e.message }), 6000); }
    }
    loadMeetings();
  };
  input.onclick = e => e.stopPropagation();
  input.onchange = () => finish(true);
  input.onblur = () => finish(true);
  input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } };
}

/* ---------- one search box: meetings, memory, saved briefs ---------- */
let searchTimer;
async function doSearch() {
  const q = state.searchQuery;
  if (!q) return;
  let r;
  try { r = await api('/api/find?q=' + encodeURIComponent(q)); } catch (e) { toast(e.message, 5000); return; }
  if (q !== state.searchQuery) return;
  renderList._sig = null;
  const list = $('mtg-list');
  list.innerHTML = '';
  const group = label => { const h = document.createElement('div'); h.className = 'group-head'; h.textContent = label; list.appendChild(h); };
  const hl = s => esc(s).replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), x => `<mark>${x}</mark>`);
  if (r.memory.length) {
    group(t('library.results_memory', { n: r.memory.length }));
    r.memory.slice(0, 8).forEach(e => {
      const div = document.createElement('div'); div.className = 'item';
      div.innerHTML = `<div class="body"><div class="t">${hl(e.name)}</div>
        <div class="s">${esc(t('mem.type_' + e.type))}${e.status ? ' · ' + esc(t('mem.status_' + e.status)) : ''}</div>
        <div class="d">${hl(e.snippet || e.summary || '')}</div></div>`;
      div.onclick = () => setView('memory', { entity: e.id });
      list.appendChild(div);
    });
  }
  if (r.meetings.length) {
    group(t('library.results_meetings', { n: r.meetings.length }));
    r.meetings.forEach(m => {
      const div = document.createElement('div'); div.className = 'item' + (state.current === m.id ? ' on' : '');
      div.innerHTML = `<div class="body"><div class="t">${esc(m.title)}</div>
        ${m.matches.map(h => `<div class="d">${hl(h.line)}</div>`).join('')}</div>`;
      div.onclick = () => openMeeting(m.id, { highlight: q });
      list.appendChild(div);
    });
  }
  if (r.docs.length) {
    group(t('library.results_docs', { n: r.docs.length }));
    r.docs.forEach(d => {
      const div = document.createElement('div'); div.className = 'item';
      div.innerHTML = `<div class="body"><div class="t">${esc(t('library.doc_' + d.type))} · ${esc(d.name)}</div><div class="d">${hl(d.line)}</div></div>`;
      div.onclick = () => {
        if (d.type === 'brief') setView('briefs', { mode: 'day', date: d.name });
        else if (d.type === 'weekly') setView('briefs', { mode: 'week', week: d.name });
        else if (d.type === 'prep') setView('prep', { name: d.name });
        else setView('insight', { name: d.name });
      };
      list.appendChild(div);
    });
  }
  if (!r.memory.length && !r.meetings.length && !r.docs.length) list.innerHTML = `<div class="list-empty">${esc(t('sidebar.no_results'))}</div>`;
}

/* ---------- meeting view ---------- */
export async function openMeeting(id, opts = {}) {
  if (state.view !== 'library') setView('library');
  state.current = id; state.tab = 'transcript'; state.editing = false; state.highlight = opts.highlight || '';
  renderList._sig = null;
  await renderMeeting();
  loadMeetings();
}

function showEmpty() {
  $('empty').hidden = false; $('meeting').hidden = true;
  state.current = null; state.currentData = null;
  emit('meeting', null);
}

function metaLine(m) {
  const bits = [fmtDate(m.created)];
  bits.push(m.duration || (m.language === 'imported' ? t('doc.meta_text') : t('doc.meta_untranscribed')));
  if (m.source_label || m.source) bits.push(m.source_label || m.source);
  if (m.channel) bits.push(t('doc.meta_channel', { channel: t('asr.channel_' + m.channel) }));
  if (m.language && !['auto', 'imported', ''].includes(m.language)) bits.push(t('doc.meta_lang', { lang: m.language }));
  if (m.gaps) bits.push(t('doc.meta_gaps', { n: m.gaps }));
  if (m.glossary_fixed) bits.push(t('doc.meta_fixed', { n: m.glossary_fixed }));
  return bits.filter(Boolean).map(esc).join(' · ');
}

export async function renderMeeting() {
  if (!state.current) return showEmpty();
  let m;
  try { m = await api('/api/meetings/' + encodeURIComponent(state.current)); }
  catch (e) { toast(e.message, 5000); return showEmpty(); }
  state.currentData = m;
  $('empty').hidden = true; $('meeting').hidden = false;
  $('mtg-title').textContent = m.title;
  $('mtg-meta').innerHTML = metaLine(m);
  $('mtg-digest').textContent = m.digest || '';
  $('mtg-digest').hidden = !m.digest;
  renderParticipants(m);
  emit('meeting', m);

  const tabs = $('tabs'); tabs.innerHTML = '';
  const addTab = (key, label, cls = '') => {
    const el = document.createElement('button');
    el.className = 'tab' + (state.tab === key ? ' on' : '') + (cls ? ' ' + cls : '');
    el.textContent = label;
    el.onclick = () => { state.tab = key; state.editing = false; renderMeeting(); };
    tabs.appendChild(el);
  };
  addTab('transcript', t('doc.tab_transcript'));
  m.report_list.forEach(r => addTab('r:' + r.name, r.label || r.name));
  const pendingHere = state.pending && state.pending.mid === m.id;
  if (pendingHere) addTab('gen', t('doc.tab_generating', { tpl: state.pending.tplName }), 'pending');
  else if (state.tab === 'gen') state.tab = 'transcript';

  const bar = $('report-actions'); bar.innerHTML = '';
  const doc = $('doc-md');
  if (m.status === 'recording') {
    bar.hidden = true;
    doc.innerHTML = recPanelHtml(m);
    const sb = $('btn-rec-stop'); if (sb) sb.onclick = () => stopRecording();
    return;
  }
  bar.hidden = false;
  const isT = state.tab === 'transcript', isGen = state.tab === 'gen';
  const report = m.report_list.find(r => 'r:' + r.name === state.tab);

  if (state.editing) {
    bar.innerHTML = `<button id="btn-save" class="primary">${esc(isT ? t('doc.save_transcript') : t('doc.save_report'))}</button>
      <button id="btn-cancel">${esc(t('common.cancel'))}</button>`;
    $('btn-cancel').onclick = () => { state.editing = false; renderMeeting(); };
    $('btn-save').onclick = () => saveEdit(isT, report);
    doc.innerHTML = '';
    const ta = document.createElement('textarea'); ta.id = 'edit-area';
    ta.value = isT ? (m.transcript || '') : (report ? report.content : '');
    doc.appendChild(ta);
    return;
  }

  // generate (always available), then actions for the open tab
  const sel = document.createElement('select'); sel.id = 'tpl-select';
  (state.templates || []).forEach(tp => { const o = document.createElement('option'); o.value = tp.id; o.textContent = tp.name; o.title = tp.desc; sel.appendChild(o); });
  if (state.lastTemplate) sel.value = state.lastTemplate;
  const gen = document.createElement('button'); gen.className = 'primary'; gen.id = 'btn-gen';
  gen.textContent = pendingHere ? t('doc.generating') : t('doc.generate'); gen.disabled = !!pendingHere || !m.has_transcript;
  gen.onclick = genReport;
  bar.append(sel, gen);
  if (!isGen && (m.has_transcript || report)) {
    const sep = document.createElement('span'); sep.className = 'sep'; bar.appendChild(sep);
    if (isT) {
      const fmt = document.createElement('select'); fmt.id = 'dl-fmt'; fmt.title = t('doc.download_format_title');
      ['md', 'txt'].concat(m.has_segments ? ['srt', 'vtt', 'json'] : []).forEach(f => { const o = document.createElement('option'); o.value = f; o.textContent = '.' + f; fmt.appendChild(o); });
      bar.appendChild(fmt);
    }
    const dl = document.createElement('button'); dl.textContent = t('doc.download');
    dl.onclick = () => {
      const mid = encodeURIComponent(state.current);
      download(isT ? `/api/download/meeting/${mid}/transcript?fmt=${encodeURIComponent($('dl-fmt').value)}`
        : `/api/download/meeting/${mid}/report/${encodeURIComponent(state.tab.slice(2))}`);
    };
    const ed = document.createElement('button'); ed.textContent = t('doc.edit');
    ed.onclick = () => { state.editing = true; renderMeeting(); };
    bar.append(dl, ed);
  }
  const more = document.createElement('button'); more.textContent = t('doc.more');
  more.onclick = e => { e.stopPropagation(); menu(more, moreItems(m, isT, report)); };
  bar.appendChild(more);

  if (isGen) { renderLive(); return; }
  if (isT && !m.transcript && m.status === 'transcribing') {
    doc.innerHTML = `<p id="job-status" class="muted">${esc(m.job_detail || t('doc.transcribing'))}</p>`;
  } else if (isT && !m.transcript && m.status === 'error') {
    doc.innerHTML = `<p class="bad-text">${esc(t('doc.transcribe_error'))}</p>${m.error ? `<p class="muted small">${esc(m.error)}</p>` : ''}
      ${m.has_audio !== false ? `<p><button id="btn-retry" class="primary">${esc(t('doc.retranscribe'))}</button></p>` : ''}`;
    const rb = $('btn-retry'); if (rb) rb.onclick = () => retranscribe(m);
  } else {
    const content = isT ? (transcriptBody(m.transcript) || t('doc.no_transcript')) : (report ? stripTitle(report.content) : '');
    doc.innerHTML = md(content, state.highlight);
    doc.classList.toggle('transcript', isT);
    if (state.highlight) { const first = doc.querySelector('mark'); if (first) first.scrollIntoView({ block: 'center' }); }
  }
}

/* the transcript file starts with a title and metadata lines that the header above already shows */
function transcriptBody(md) {
  if (!md) return '';
  const lines = md.split('\n');
  const h2 = lines.findIndex(l => l.startsWith('## '));
  return h2 >= 0 ? lines.slice(h2 + 1).join('\n').trim() : md;
}
const stripTitle = md => (md || '').replace(/^# [^\n]*\n+/, '');

function moreItems(m, isT, report) {
  const items = [];
  if (isT && m.has_transcript) {
    items.push({ label: t('doc.clean'), run: cleanTranscript });
    items.push({ label: t('doc.fix_names'), run: () => fixNames(m) });
    if (m.has_raw) items.push({ label: t('doc.restore'), run: restoreRaw });
  }
  items.push({ label: t('doc.retranscribe'), run: () => retranscribe(m), disabled: m.status === 'transcribing' || m.source === 'text' || m.source === 'paste' });
  if (report) {
    items.push('-');
    items.push({ label: t('doc.delete_report'), bad: true, run: () => deleteReport(m, report) });
  }
  items.push('-');
  items.push({ label: t('doc.delete_meeting'), bad: true, run: () => deleteMeeting(m) });
  return items;
}

function refreshIfStale() {
  if (state.editing || state.tab === 'gen' || state.view !== 'library') return;
  const cur = state.meetings.find(m => m.id === state.current);
  if (!cur || !state.currentData) return;
  if (cur.status !== state.currentData.status || cur.digest !== state.currentData.digest) { renderMeeting(); return; }
  if (cur.status === 'transcribing' && cur.job_detail) { const el = $('job-status'); if (el) el.textContent = cur.job_detail; }
}

async function saveEdit(isT, report) {
  const content = $('edit-area').value;
  try {
    if (isT) await post(`/api/meetings/${encodeURIComponent(state.current)}/transcript`, { content });
    else await post(`/api/meetings/${encodeURIComponent(state.current)}/reports/${encodeURIComponent(report.name)}`, { content });
    state.editing = false; toast(t(isT ? 'transcript.saved' : 'report.saved'));
    await renderMeeting();
  } catch (e) { toast(t('common.save_failed', { msg: e.message }), 6000); }
}

async function cleanTranscript() {
  const mid = state.current;
  try {
    const pre = await post(`/api/meetings/${encodeURIComponent(mid)}/clean`, { apply: false });
    if (!pre.count) { toast(t('doc.clean_none')); return; }
    const sample = pre.removed.slice(0, 8).map(l => '  ' + l.slice(0, 80)).join('\n') + (pre.count > 8 ? `\n  … +${pre.count - 8}` : '');
    if (!await confirmBox(t('doc.clean'), t('doc.clean_confirm', { n: pre.count, sample }), t('doc.clean_apply'))) return;
    const r = await post(`/api/meetings/${encodeURIComponent(mid)}/clean`, { apply: true });
    toast(t('doc.clean_done', { n: r.count }), 6000);
    if (state.current === mid) renderMeeting();
  } catch (e) { toast(t('common.failed', { msg: e.message }), 8000); }
}

async function fixNames(m) {
  if (!await confirmBox(t('doc.fix_names'), t('doc.fix_names_confirm'), t('doc.fix_names'))) return;
  toast(t('doc.fixing'), 20000);
  try { await post('/api/fix-names/' + encodeURIComponent(m.id)); toast(t('doc.fixed')); renderMeeting(); }
  catch (e) { toast(t('common.failed', { msg: e.message }), 9000); }
}

async function restoreRaw() {
  if (!await confirmBox(t('doc.restore'), t('transcript.restore_confirm'))) return;
  try { await post('/api/meetings/' + encodeURIComponent(state.current) + '/restore-raw'); toast(t('transcript.restored')); await renderMeeting(); }
  catch (e) { toast(t('transcript.restore_failed', { msg: e.message }), 8000); }
}

async function deleteReport(m, report) {
  if (!await confirmBox(t('doc.delete_report'), t('report.delete_confirm', { name: report.label || report.name }), t('common.delete'))) return;
  try { await del(`/api/meetings/${encodeURIComponent(m.id)}/reports/${encodeURIComponent(report.name)}`); state.tab = 'transcript'; toast(t('report.deleted')); await renderMeeting(); }
  catch (e) { toast(t('report.delete_failed', { msg: e.message }), 6000); }
}

async function deleteMeeting(m) {
  if (!await confirmBox(t('doc.delete_meeting'), t('meeting.delete_confirm', { title: m.title }), t('common.delete'))) return;
  try {
    await del('/api/meetings/' + encodeURIComponent(m.id));
    toast(t('meeting.deleted'));
    if (state.current === m.id) showEmpty();
    state.searchQuery ? doSearch() : loadMeetings();
  } catch (e) { toast(t('meeting.delete_failed', { msg: e.message }), 6000); }
}

/* transcribe again, with a channel picked from the ready ones */
export async function retranscribe(m) {
  let s;
  try { s = await api('/api/settings/asr'); } catch (e) { toast(e.message, 6000); return; }
  const ready = Object.entries(s.channels).filter(([, c]) => c.ready).map(([k]) => k);
  if (!ready.length) { setView('settings', { section: 'asr' }); toast(t('asr.none_ready'), 8000); return; }
  const buttons = [['', t('common.cancel')]].concat(ready.map(k => [k, t('asr.channel_' + k), k === s.active]));
  const ch = await choose(t('doc.retranscribe'), t('doc.retranscribe_body'), buttons);
  if (!ch) return;
  try {
    const r = await post(`/api/meetings/${encodeURIComponent(m.id)}/transcribe`, { channel: ch });
    toast(t('doc.retranscribe_started', { channel: t('asr.channel_' + r.channel) }));
    watchJob(r.job, m.id);
    await renderMeeting();
  } catch (e) { toast(t('common.failed', { msg: e.message }), 9000); }
}

/* ---------- reports (streamed into their own tab) ---------- */
function renderLive() {
  const p = state.pending; if (!p) return;
  $('doc-md').innerHTML = `<div class="live-head" id="live-status"></div><div id="live-md" class="md cursor"></div>`;
  updateLiveStatus();
  $('live-md').innerHTML = md(p.acc);
}
function updateLiveStatus() {
  const p = state.pending, el = $('live-status'); if (!p || !el) return;
  const s = Math.round((Date.now() - p.startedAt) / 1000);
  el.textContent = p.acc ? t('report.generating_elapsed', { tpl: p.tplName, s }) : t('report.thinking', { tpl: p.tplName, s });
}
async function genReport() {
  if (state.pending) { toast(t('report.busy')); return; }
  const sel = $('tpl-select'); const tpl = sel.value; state.lastTemplate = tpl;
  const tplName = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : tpl;
  const mid = state.current;
  const p = state.pending = { mid, tpl, tplName, acc: '', startedAt: Date.now(), timer: null };
  p.timer = setInterval(updateLiveStatus, 1000);
  state.tab = 'gen'; state.editing = false;
  await renderMeeting();
  const paint = throttle(() => {
    const el = $('live-md');
    if (el && state.current === mid && state.tab === 'gen') { el.innerHTML = md(p.acc); $('doc').scrollTop = $('doc').scrollHeight; }
  }, 160);
  try {
    const r = await streamApi('/api/stream/report', { id: mid, template: tpl }, txt => { p.acc += txt; paint(); updateLiveStatus(); });
    clearInterval(p.timer); state.pending = null;
    toast(t('report.done'));
    if (state.current === mid) { if (state.tab === 'gen') state.tab = 'r:' + r.name; await renderMeeting(); }
    else loadMeetings();
  } catch (e) {
    clearInterval(p.timer); state.pending = null;
    toast(t('report.failed', { msg: e.message }), 8000);
    if (state.current === mid) { if (state.tab === 'gen') state.tab = 'transcript'; renderMeeting(); }
  }
}

/* ---------- participants (names + roles, written into the transcript header) ---------- */
function renderParticipants(m) {
  const row = $('participants-row');
  row.innerHTML = `<span>${esc(t('doc.participants'))}:</span>
    <span id="participants-text" class="${m.participants ? 'v' : ''}">${esc(m.participants || t('doc.participants_empty'))}</span>
    <button class="link" id="btn-participants-edit" title="${esc(t('doc.participants_edit_title'))}">${esc(t('doc.participants_edit'))}</button>
    <button class="link" id="btn-participants-detect" title="${esc(t('doc.participants_detect_title'))}">${esc(t('doc.participants_detect'))}</button>`;
  row.hidden = !m.has_transcript;
  $('btn-participants-edit').onclick = () => editParticipants(m, m.participants || '');
  $('btn-participants-detect').onclick = async () => {
    const b = $('btn-participants-detect'); const label = b.textContent;
    b.disabled = true; b.textContent = t('doc.participants_detecting');
    try {
      const r = await post(`/api/meetings/${encodeURIComponent(m.id)}/participants/detect`);
      editParticipants(m, r.participants);
      toast(t('doc.participants_detected'), 6000);
    } catch (e) { toast(t('doc.participants_failed', { msg: e.message }), 8000); b.disabled = false; b.textContent = label; }
  };
}
function editParticipants(m, value) {
  const row = $('participants-row');
  const span = $('participants-text');
  const input = document.createElement('input');
  input.type = 'text'; input.value = value; input.placeholder = t('doc.participants_placeholder');
  span.replaceWith(input); input.focus();
  row.querySelectorAll('button').forEach(b => { b.hidden = true; });
  let done = false;
  const finish = async save => {
    if (done) return; done = true;
    if (save && input.value.trim() !== (m.participants || '')) {
      try { await post(`/api/meetings/${encodeURIComponent(m.id)}/meta`, { participants: input.value.trim() }); toast(t('doc.participants_saved')); }
      catch (e) { toast(t('common.save_failed', { msg: e.message }), 6000); }
    }
    if (state.current === m.id && !state.editing) renderMeeting();
  };
  input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.preventDefault(); finish(false); } };
  input.onblur = () => finish(true);
}

/* ---------- upload (multi + drag & drop), import, "cannot transcribe" help ---------- */
const isTextFile = name => (state.caps.textExts || []).includes(('.' + name.split('.').pop()).toLowerCase());
export async function uploadFiles(files) {
  const skipped = [];
  for (const f of files) {
    if (!state.caps.transcribe && !isTextFile(f.name)) { skipped.push(f.name); continue; }
    const fd = new FormData(); fd.append('file', f);
    toast(t('upload.uploading', { name: f.name }));
    try {
      const r = await api('/api/upload', { method: 'POST', body: fd });
      if (r.text) { toast(t('upload.text_done', { name: f.name })); await loadMeetings(); if (!state.current) openMeeting(r.meeting_id); }
      else { toast(t('upload.queued', { name: f.name })); watchJob(r.job, r.meeting_id); }
    } catch (err) { toast(t('upload.failed', { name: f.name, msg: err.message }), 6000); }
  }
  if (skipped.length) showAudioHelp(skipped);
}
export function showAudioHelp(files) {
  const p = state.caps.platform;
  $('audiohelp-body').textContent = t(p === 'windows' ? 'audiohelp.body_windows' : p === 'mac' ? 'audiohelp.body_mac' : 'audiohelp.body_linux');
  $('audiohelp-files').textContent = files && files.length ? t('audiohelp.files', { names: files.join(', ') }) : '';
  openDialog('audiohelp-dialog');
}

let impTab = 'path';
function setImpTab(tab) {
  impTab = tab;
  qsa('#imp-tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $('imp-path-pane').hidden = tab !== 'path';
  $('imp-paste-pane').hidden = tab !== 'paste';
}
export function openImport(tab) {
  if (tab) setImpTab(tab);
  openDialog('import-dialog');
  setTimeout(() => (impTab === 'path' ? $('imp-path') : $('imp-text')).focus(), 30);
}
async function runImport() {
  const btn = $('btn-import-go');
  if (impTab === 'path') {
    const path = $('imp-path').value.trim();
    if (!path) { toast(t('import.need_path')); return; }
    btn.disabled = true;
    try {
      const r = await post('/api/import', { path });
      const errs = r.imported.filter(it => it.error), jobs = r.imported.filter(it => it.job);
      let msg = t('import.done_count', { n: r.count - errs.length });
      if (errs.length) msg += t('import.done_failed', { n: errs.length, err: errs[0].error });
      else if (jobs.length) msg += t('import.done_queued');
      toast(msg, 7000);
      r.imported.forEach(it => { if (it.job) watchJob(it.job, it.meeting_id); });
      await loadMeetings();
      closeDialog('import-dialog'); $('imp-path').value = '';
      if (errs.some(it => it.audio)) showAudioHelp(errs.filter(it => it.audio).map(it => it.error.split(':')[0]));
    } catch (e) { toast(t('import.failed', { msg: e.message }), 8000); }
    btn.disabled = false;
  } else {
    const content = $('imp-text').value.trim();
    if (!content) { toast(t('import.need_content')); return; }
    btn.disabled = true;
    try {
      const r = await post('/api/import-text', { content, title: $('imp-title').value.trim(), date: $('imp-date').value || '' });
      toast(r.format && r.format !== 'paste' ? t('import.pasted_fmt', { fmt: r.format }) : t('import.pasted'));
      $('imp-text').value = ''; $('imp-title').value = ''; $('imp-date').value = '';
      closeDialog('import-dialog');
      await loadMeetings();
      openMeeting(r.meeting_id);
    } catch (e) { toast(t('import.failed', { msg: e.message }), 8000); }
    btn.disabled = false;
  }
}

/* ---------- recording ----------
   Default: the browser records (MediaRecorder) and sends 4-second chunks that the server appends to the
   meeting's audio file, so a closed tab or a crash keeps everything recorded so far. record_mode "ffmpeg"
   keeps the server-side recorder (macOS, e.g. BlackHole inputs). */
let recTimer = null;
const rec = { active: false, id: '', mr: null, stream: null, queue: [], seq: 0, pumping: false, failed: false,
  startedAt: 0, ctx: null, raf: 0, quietSince: 0, warnedQuiet: false, elsewhere: false };
const fmtRec = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
export const recActive = () => rec.active;

function recPanelHtml(m) {
  const mine = rec.active && rec.id === m.id;
  return mine
    ? `<div class="rec-panel" id="rec-panel"><span class="rec-dot"></span><div class="fill"><b id="rec-panel-time">${esc(fmtRec(state.recSeconds))}</b>
         <span class="muted small">${esc(t('record.panel_hint'))}</span><div class="level"><i></i></div></div>
         <button class="primary" id="btn-rec-stop">${esc(t('record.panel_stop'))}</button></div>`
    : `<div class="rec-panel"><span class="rec-dot"></span><div><b>${esc(t('record.elsewhere'))}</b><span class="muted small">${esc(t('record.panel_elsewhere'))}</span></div></div>`;
}
export function setRecUI() {
  const b = $('btn-record'); if (!b) return;
  b.disabled = rec.elsewhere && !state.recording;
  const pt = $('rec-panel-time'); if (pt) pt.textContent = fmtRec(state.recSeconds);
  const ind = $('rec-indicator');
  ind.hidden = !state.recording;
  $('rec-indicator-time').textContent = t('nav.recording', { time: fmtRec(state.recSeconds) });
  if (state.recording) { b.classList.add('recording'); b.textContent = t('record.stop', { time: fmtRec(state.recSeconds) }); }
  else if (rec.elsewhere) { b.classList.remove('recording'); b.textContent = t('record.elsewhere'); }
  else { b.classList.remove('recording'); b.textContent = t('library.record'); }
}
function startRecTimer() {
  clearInterval(recTimer);
  recTimer = setInterval(async () => {
    if (rec.active) { state.recSeconds = Math.floor((Date.now() - rec.startedAt) / 1000); setRecUI(); return; }
    try {
      const s = await api('/api/record/status');
      state.recSeconds = Math.floor(s.elapsed || 0);
      state.recording = !!s.active && s.mode === 'ffmpeg';
      setRecUI();
      if (!state.recording) clearInterval(recTimer);
    } catch {}
  }, 1000);
}
function onBeforeUnload(e) { if (rec.active) { e.preventDefault(); e.returnValue = ''; } }

async function startRecording() {
  if (state.caps.recordMode === 'ffmpeg') return startServerRecording();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { toast(t('record.no_browser_support'), 9000); return; }
  const btn = $('btn-record');
  btn.disabled = true; btn.textContent = t('record.opening_mic');
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    btn.disabled = false; setRecUI();
    toast(t(e && e.name === 'NotAllowedError' ? 'record.mic_denied' : 'record.mic_failed', { msg: e.message || e.name }), 12000);
    return;
  }
  btn.disabled = false;
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'].find(m => MediaRecorder.isTypeSupported(m)) || '';
  let r;
  try { r = await post('/api/record/browser/start', { title: '', mime: mime || 'audio/webm' }); }
  catch (e) { stream.getTracks().forEach(tr => tr.stop()); setRecUI(); toast(t('record.start_failed', { msg: e.message }), 9000); return; }
  Object.assign(rec, { active: true, id: r.meeting_id, stream, queue: [], seq: 0, failed: false, startedAt: Date.now(), quietSince: Date.now(), warnedQuiet: false });
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : undefined);
  rec.mr = mr;
  mr.ondataavailable = e => { if (e.data && e.data.size) { rec.queue.push(e.data); pumpChunks(); } };
  mr.start(4000);
  startLevelMeter(stream);
  window.addEventListener('beforeunload', onBeforeUnload);
  state.recording = true; state.recSeconds = 0; setRecUI(); startRecTimer();
  toast(t('record.started_browser'));
  await loadMeetings();
  if (!state.editing && !state.pending) openMeeting(rec.id);
}
async function pumpChunks() {
  if (rec.pumping) return;
  rec.pumping = true;
  while (rec.queue.length && !rec.failed) {
    const blob = rec.queue[0];
    let ok = false;
    for (let attempt = 0; attempt < 6 && !ok; attempt++) {
      try {
        const res = await fetch(`/api/record/browser/chunk?id=${encodeURIComponent(rec.id)}&seq=${rec.seq}`, {
          method: 'POST', body: blob, headers: { 'Content-Type': 'application/octet-stream', 'X-Coco-Lang': I18N.lang } });
        if (res.ok) ok = true;
        else if (res.status === 409) break;
        else await sleep(1000 * (attempt + 1));
      } catch { await sleep(1000 * (attempt + 1)); }
    }
    if (!ok) { rec.failed = true; toast(t('record.upload_failed'), 12000); break; }
    rec.queue.shift(); rec.seq++;
  }
  rec.pumping = false;
}
function startLevelMeter(stream) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    rec.ctx = new Ctx();
    const an = rec.ctx.createAnalyser(); an.fftSize = 512;
    rec.ctx.createMediaStreamSource(stream).connect(an);
    const buf = new Uint8Array(an.fftSize);
    const tick = () => {
      if (!rec.active) return;
      an.getByteTimeDomainData(buf);
      let sum = 0; for (const v of buf) { const x = (v - 128) / 128; sum += x * x; }
      const lvl = Math.min(1, Math.sqrt(sum / buf.length) * 6);
      const panel = $('rec-panel'); if (panel) panel.style.setProperty('--lvl', lvl.toFixed(2));
      if (lvl > 0.04) rec.quietSince = Date.now();
      else if (!rec.warnedQuiet && Date.now() - rec.quietSince > 20000) { rec.warnedQuiet = true; toast(t('record.quiet'), 10000); }
      rec.raf = requestAnimationFrame(tick);
    };
    tick();
  } catch {}
}
export async function stopBrowserRecording(quiet) {
  if (!rec.active) return;
  const mr = rec.mr;
  await new Promise(res => { mr.onstop = res; try { mr.stop(); } catch { res(); } });
  rec.stream.getTracks().forEach(tr => tr.stop());
  cancelAnimationFrame(rec.raf);
  try { rec.ctx && rec.ctx.close(); } catch {}
  for (let i = 0; i < 120 && (rec.queue.length || rec.pumping) && !rec.failed; i++) await sleep(250);
  rec.active = false;
  window.removeEventListener('beforeunload', onBeforeUnload);
  clearInterval(recTimer); state.recording = false; setRecUI();
  try {
    const r = await post('/api/record/browser/stop', { id: rec.id });
    if (!quiet) { toast(t('record.stopped')); watchJob(r.job, r.meeting_id); }
  } catch (e) { if (!quiet) toast(t('record.stop_failed', { msg: e.message }), 8000); }
}
export async function checkRecordingOnLoad() {
  let s;
  try { s = await api('/api/record/status'); } catch { return; }
  if (!s.active) return;
  if (s.mode === 'ffmpeg') { state.recording = true; state.recSeconds = Math.floor(s.elapsed || 0); startRecTimer(); return; }
  if (s.idle < 15) { rec.elsewhere = true; setRecUI(); watchElsewhere(); return; }
  const mins = Math.max(1, Math.round((s.elapsed - s.idle) / 60));
  const ans = await choose(t('record.recover_title'), t('record.recover_body', { min: mins, mb: (s.bytes / 1048576).toFixed(1) }),
    [['later', t('record.recover_later')], ['discard', t('record.recover_discard')], ['keep', t('record.recover_keep'), true]]);
  if (ans !== 'keep' && ans !== 'discard') return;
  try {
    const r = await post('/api/record/browser/stop', { id: s.meeting_id, discard: ans === 'discard' });
    if (ans === 'keep') { toast(t('record.stopped')); watchJob(r.job, r.meeting_id); } else loadMeetings();
  } catch (e) { toast(t('common.failed', { msg: e.message }), 8000); }
}
function watchElsewhere() {
  const timer = setInterval(async () => {
    try { const s = await api('/api/record/status'); if (!s.active || s.mode !== 'browser') { clearInterval(timer); rec.elsewhere = false; setRecUI(); loadMeetings(); } } catch {}
  }, 4000);
}
async function startServerRecording() {
  try { await post('/api/record/start', { title: '' }); state.recording = true; state.recSeconds = 0; setRecUI(); startRecTimer(); toast(t('record.started')); }
  catch (e) { toast(t('record.start_failed', { msg: e.message }), 9000); }
}
export async function stopRecording() {
  if (rec.active) return stopBrowserRecording(false);
  try {
    const r = await post('/api/record/stop');
    clearInterval(recTimer); state.recording = false; setRecUI();
    toast(t('record.stopped')); watchJob(r.job, r.meeting_id);
  } catch (e) { toast(t('record.stop_failed', { msg: e.message }), 8000); }
}

/* ---------- transcription jobs ---------- */
export function watchJob(jid, meetingId) {
  loadMeetings();
  const timer = setInterval(async () => {
    try {
      const j = await api('/api/jobs/' + jid);
      if (j.status === 'done') {
        clearInterval(timer); toast(t('job.done'));
        await loadMeetings();
        if (state.editing && state.current === meetingId) toast(t('job.done_editing'), 6000);
        else if (state.view === 'library' && (!state.current || state.current === meetingId)) openMeeting(meetingId);
      } else if (j.status === 'error') {
        clearInterval(timer); toast(t('job.failed', { msg: j.detail }), 12000); loadMeetings();
        if (state.current === meetingId) renderMeeting();
      } else if (state.current === meetingId && !state.editing) {
        const el = $('job-status'); if (el) el.textContent = j.detail || t('job.transcribing');
      }
    } catch { clearInterval(timer); }
  }, 3000);
}

/* ---------- batch download of the selected meetings ---------- */
function selectedItems() {
  const items = [];
  for (const id of state.selected) {
    const m = state.meetings.find(x => x.id === id);
    if (!m) continue;
    if (m.has_transcript) items.push({ type: 'transcript', id });
    (m.reports || []).forEach(name => items.push({ type: 'report', id, name }));
  }
  return items;
}
export async function postDownload(items) {
  if (!items.length) { toast(t('export.nothing')); return; }
  toast(t('export.packing'));
  try {
    const res = await fetch('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Coco-Lang': I18N.lang }, body: JSON.stringify({ items }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || res.statusText); }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const mm = cd.match(/filename\*=UTF-8''([^;]+)/);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = mm ? decodeURIComponent(mm[1]) : 'coco-export.zip';
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    toast(t('export.done'));
  } catch (e) { toast(t('export.failed', { msg: e.message }), 6000); }
}

/* ---------- wiring ---------- */
export function initLibrary() {
  $('btn-record').onclick = () => state.recording ? stopRecording() : startRecording();
  $('rec-indicator-stop').onclick = () => stopRecording();
  $('btn-upload').onclick = () => $('file-input').click();
  $('file-input').onchange = e => { uploadFiles([...e.target.files]); e.target.value = ''; };
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => { e.preventDefault(); if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]); });
  $('btn-import').onclick = () => openImport();
  qsa('#imp-tabs button').forEach(b => { b.onclick = () => setImpTab(b.dataset.tab); });
  setImpTab('path');
  $('btn-import-go').onclick = runImport;
  $('btn-audiohelp-paste').onclick = () => { closeDialog('audiohelp-dialog'); openImport('paste'); };
  $('btn-audiohelp-settings').onclick = () => { closeDialog('audiohelp-dialog'); setView('settings', { section: 'asr' }); };
  $('start-rec').onclick = () => $('btn-record').click();
  $('start-upload').onclick = () => $('file-input').click();
  $('start-paste').onclick = () => openImport('paste');

  $('search-box').addEventListener('input', e => {
    state.searchQuery = e.target.value.trim();
    clearTimeout(searchTimer);
    if (!state.searchQuery) { renderList._sig = null; state.highlight = ''; renderList(); updateSelBar(); return; }
    searchTimer = setTimeout(doSearch, 300);
  });
  $('filter-from').onchange = e => { state.filterFrom = e.target.value; renderList(); updateSelBar(); };
  $('filter-to').onchange = e => { state.filterTo = e.target.value; renderList(); updateSelBar(); };
  $('filter-clear').onclick = () => { state.filterFrom = state.filterTo = ''; $('filter-from').value = ''; $('filter-to').value = ''; renderList(); updateSelBar(); };
  $('sel-download').onclick = () => postDownload(selectedItems());
  $('sel-insight').onclick = () => setView('insight', { form: true, scope: 'sel' });
  $('sel-clear').onclick = () => { state.selected.clear(); renderList._sig = null; renderList(); updateSelBar(); };

  registerView('library', { show() { if (state.current) refreshIfStale(); } });
  on('recording-panel', () => { if (state.currentData && state.currentData.status === 'recording') renderMeeting(); });
}
