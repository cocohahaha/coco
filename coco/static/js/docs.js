/* coco · documents that are not one meeting: pre-meeting prep, cross-meeting insight, daily / weekly briefs */
import { $, qsa, state, t, esc, md, api, del, streamApi, toast, throttle, confirmBox, registerView, download } from './core.js';

const titleOf = (content, fallback) => {
  const first = (content || '').split('\n').find(l => l.startsWith('# ')) || '';
  const parts = first.slice(2).split(' · ');
  return parts.length >= 2 ? parts.slice(1, -1).join(' · ') || parts[1] : (fallback || first.slice(2));
};
const stampOf = name => (name.match(/^(\d{4}-\d{2}-\d{2})(?:-(\d{2})(\d{2}))?/) || []);
const when = name => { const m = stampOf(name); return m[1] ? m[1] + (m[2] ? ` ${m[2]}:${m[3]}` : '') : ''; };

function listItems(boxId, items, current, onPick, sub) {
  const list = $(boxId);
  if (!items.length) { list.innerHTML = `<div class="list-empty">${esc(t('docs.none'))}</div>`; return; }
  list.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'item' + (it.key === current ? ' on' : '');
    div.innerHTML = `<div class="body"><div class="t">${esc(it.title)}</div><div class="s">${esc(sub(it))}</div></div>`;
    div.onclick = () => onPick(it.key);
    list.appendChild(div);
  });
}

function docView(box, content, actions) {
  box.innerHTML = `<div class="toolbar" style="padding-left:0;padding-top:0;border-bottom:none" id="${box.id}-bar"></div><div class="md">${md(content)}</div>`;
  const bar = $(box.id + '-bar');
  actions.forEach(([label, fn, cls]) => { const b = document.createElement('button'); b.textContent = label; if (cls) b.className = cls; b.onclick = fn; bar.appendChild(b); });
}

function liveArea(box, title, headline) {
  box.innerHTML = `<h1>${esc(title)}</h1><div class="live-head">${esc(headline)}</div><div class="md cursor" id="${box.id}-live"></div>`;
  return throttle(text => { const el = $(box.id + '-live'); if (el) { el.innerHTML = md(text); box.scrollTop = box.scrollHeight; } }, 160);
}

/* ---------- pre-meeting prep ---------- */
const prep = { items: [], current: '', busy: false };
async function prepLoad() { prep.items = await api('/api/preps').catch(() => []); prepList(); }
function prepList() {
  listItems('prep-list', prep.items.map(p => ({ key: p.name, title: titleOf(p.content, p.name), name: p.name })),
    prep.current, k => { prep.current = k; prepShow(); prepList(); }, it => when(it.name));
}
function prepShow() {
  const box = $('prep-content');
  const p = prep.items.find(x => x.name === prep.current);
  if (!p) return prepForm();
  docView(box, p.content, [
    [t('common.download_md'), () => download('/api/download/prep/' + encodeURIComponent(p.name))],
    [t('common.delete'), async () => {
      if (!await confirmBox(t('common.delete'), t('prep.delete_confirm'), t('common.delete'))) return;
      try { await del('/api/preps/' + encodeURIComponent(p.name)); toast(t('prep.deleted')); prep.current = ''; await prepLoad(); prepForm(); }
      catch (e) { toast(t('common.delete_failed', { msg: e.message }), 6000); }
    }],
  ]);
}
function prepForm() {
  prep.current = ''; prepList();
  const box = $('prep-content');
  box.innerHTML = `<div class="form">
    <h1>${esc(t('prep.title'))}</h1>
    <p class="muted small" style="margin-top:6px">${esc(t('prep.hint'))}</p>
    <label class="fld">${esc(t('prep.topic'))}</label><input id="prep-topic" class="full" placeholder="${esc(t('prep.topic_placeholder'))}">
    <label class="fld">${esc(t('prep.people'))}</label><input id="prep-people" class="full" placeholder="${esc(t('prep.people_placeholder'))}">
    <label class="fld">${esc(t('prep.goal'))}</label><input id="prep-goal" class="full" placeholder="${esc(t('prep.goal_placeholder'))}">
    <label class="radio" style="border:none;padding-top:14px"><input type="checkbox" id="prep-web"> ${esc(t('prep.web'))}</label>
    <div class="row"><button class="primary" id="btn-prep-go">${esc(t('prep.go'))}</button></div></div>`;
  $('prep-topic').focus();
  $('btn-prep-go').onclick = prepRun;
}
async function prepRun() {
  if (prep.busy) { toast(t('prep.busy')); return; }
  const topic = $('prep-topic').value.trim();
  if (!topic) { toast(t('prep.need_topic')); return; }
  const body = { topic, people: $('prep-people').value.trim(), goal: $('prep-goal').value.trim(), web: $('prep-web').checked };
  prep.busy = true; $('prep-new').disabled = true;
  const paint = liveArea($('prep-content'), t('prep.view_title'), body.web ? t('prep.toast_web') : t('prep.toast'));
  let acc = '';
  try {
    const r = await streamApi('/api/stream/prep', body, txt => { acc += txt; paint(acc); });
    toast(t('prep.done'));
    prep.current = r.name; await prepLoad(); prepShow();
  } catch (e) { toast(e.message, 9000); prepForm(); }
  finally { prep.busy = false; $('prep-new').disabled = false; }
}

/* ---------- cross-meeting insight ---------- */
const ins = { items: [], current: '', busy: false };
async function insLoad() { ins.items = await api('/api/tracking').catch(() => []); insList(); }
function insList() {
  listItems('insight-list', ins.items.map(p => ({ key: p.name, title: titleOf(p.content, p.name), name: p.name })),
    ins.current, k => { ins.current = k; insShow(); insList(); }, it => when(it.name));
}
function insShow() {
  const p = ins.items.find(x => x.name === ins.current);
  if (!p) return insForm();
  docView($('insight-content'), p.content, [
    [t('common.download_md'), () => download('/api/download/tracking/' + encodeURIComponent(p.name))],
    [t('common.delete'), async () => {
      if (!await confirmBox(t('common.delete'), t('insight.delete_confirm'), t('common.delete'))) return;
      try { await del('/api/tracking/' + encodeURIComponent(p.name)); toast(t('insight.deleted')); ins.current = ''; await insLoad(); insForm(); }
      catch (e) { toast(t('common.delete_failed', { msg: e.message }), 6000); }
    }],
  ]);
}
async function insForm(scope) {
  ins.current = ''; insList();
  if (!state.trackModes) state.trackModes = await api('/api/track-modes').catch(() => []);
  const n = [...state.selected].filter(id => { const m = state.meetings.find(x => x.id === id); return m && m.has_transcript; }).length;
  const box = $('insight-content');
  box.innerHTML = `<div class="form">
    <h1>${esc(t('insight.title'))}</h1>
    <p class="muted small" style="margin-top:6px">${esc(t('insight.hint'))}</p>
    <div class="section-title">${esc(t('insight.mode'))}</div>
    <div>${state.trackModes.map((m, i) => `<label class="radio"><input type="radio" name="ins-mode" value="${esc(m.id)}"${i === 0 ? ' checked' : ''}>
      <b>${esc(m.name)}</b><span class="d">${esc(m.desc)}</span></label>`).join('')}</div>
    <label class="fld">${esc(t('insight.focus'))}</label><input id="insight-focus" class="full" placeholder="${esc(t('insight.focus_placeholder'))}">
    <div class="section-title">${esc(t('insight.scope'))}</div>
    <label class="radio"><input type="radio" name="ins-scope" value="all"${scope === 'sel' && n >= 2 ? '' : ' checked'}>${esc(t('insight.scope_all'))}</label>
    <label class="radio${n < 2 ? ' off' : ''}"><input type="radio" name="ins-scope" value="sel"${n < 2 ? ' disabled' : ''}${scope === 'sel' && n >= 2 ? ' checked' : ''}>
      ${esc(t('insight.scope_sel', { n }))}${n < 2 ? `<span class="d">${esc(t('insight.scope_sel_hint'))}</span>` : ''}</label>
    <div class="row"><button class="primary" id="btn-insight-go">${esc(t('insight.go'))}</button></div></div>`;
  $('btn-insight-go').onclick = insRun;
}
async function insRun() {
  if (ins.busy) return;
  const mode = document.querySelector('input[name="ins-mode"]:checked')?.value || 'track';
  const modeName = (state.trackModes.find(m => m.id === mode) || {}).name || mode;
  const scope = document.querySelector('input[name="ins-scope"]:checked')?.value || 'all';
  const focus = $('insight-focus').value.trim();
  const ids = scope === 'sel' ? [...state.selected] : [];
  ins.busy = true; $('insight-new').disabled = true;
  const scopeText = ids.length ? t('insight.scope_limited', { n: ids.length }) : t('insight.scope_all');
  const paint = liveArea($('insight-content'), t('insight.view_title', { mode: modeName, focus: focus || t('insight.global') }),
    t('insight.toast', { mode: modeName, scope: scopeText }));
  let acc = '';
  try {
    const r = await streamApi('/api/stream/track', { focus, mode, ids }, txt => { acc += txt; paint(acc); });
    ins.current = r.name; await insLoad(); insShow();
  } catch (e) { toast(e.message, 9000); insForm(); }
  finally { ins.busy = false; $('insight-new').disabled = false; }
}

/* ---------- daily / weekly briefs ---------- */
const br = { mode: 'day', days: [], weeks: [], current: '' };
async function brLoad() {
  if (br.mode === 'day') {
    const briefs = await api('/api/briefs').catch(() => []);
    const dates = new Map();
    briefs.forEach(b => dates.set(b.date, b.content));
    state.meetings.filter(m => m.has_transcript).forEach(m => { const d = m.date || m.created.slice(0, 10); if (!dates.has(d)) dates.set(d, null); });
    br.days = [...dates.keys()].sort().reverse().map(d => ({ key: d, content: dates.get(d) }));
  } else {
    br.weeks = (await api('/api/weeklies').catch(() => [])).map(w => ({ key: w.week, content: w.content, range: w.range }));
  }
  const items = br.mode === 'day' ? br.days : br.weeks;
  if (!items.find(x => x.key === br.current)) br.current = items.length ? items[0].key : '';
  brList(); brShow();
}
function brModes() {
  $('brief-mode').innerHTML = ['day', 'week'].map(m => `<button data-m="${m}" class="${br.mode === m ? 'on' : ''}">${esc(t('brief.' + m))}</button>`).join('');
  qsa('#brief-mode button').forEach(b => { b.onclick = () => { if (br.mode !== b.dataset.m) { br.mode = b.dataset.m; br.current = ''; brModes(); brLoad(); } }; });
}
function brList() {
  const items = br.mode === 'day' ? br.days : br.weeks;
  listItems('brief-list', items.map(x => ({ key: x.key, title: br.mode === 'day' ? x.key : `${x.key}`, x })),
    br.current, k => { br.current = k; brList(); brShow(); },
    it => (br.mode === 'week' ? it.x.range + ' · ' : '') + (it.x.content === null ? t('brief.not_generated_short') : t('brief.generated')));
}
function brShow() {
  const box = $('brief-content');
  const items = br.mode === 'day' ? br.days : br.weeks;
  const cur = items.find(x => x.key === br.current);
  if (!cur) { box.innerHTML = `<p class="muted">${esc(t('brief.none'))}</p>`; return; }
  const day = br.mode === 'day';
  const gen = async () => {
    const paint = liveArea(box, day ? t('brief.title') : t('weekly.title'),
      day ? t('brief.generating_toast', { date: cur.key }) : t('weekly.generating_toast', { week: cur.key }));
    let acc = '';
    try {
      await streamApi(day ? '/api/stream/brief' : '/api/stream/weekly', day ? { date: cur.key } : { date: cur.range.slice(0, 10) },
        txt => { acc += txt; paint(acc); });
      toast(day ? t('brief.done') : t('weekly.done'));
    } catch (e) { toast(e.message, 9000); }
    brLoad();
  };
  const actions = [[cur.content === null ? (day ? t('brief.generate') : t('weekly.generate')) : t('brief.regenerate'), gen, 'primary']];
  if (cur.content !== null) {
    actions.push([t('common.download_md'), () => download(day ? '/api/download/brief/' + cur.key : '/api/download/weekly/' + encodeURIComponent(cur.key))]);
    actions.push([t('common.delete'), async () => {
      if (!await confirmBox(t('common.delete'), day ? t('brief.delete_confirm', { date: cur.key }) : t('weekly.delete_confirm', { week: cur.key }), t('common.delete'))) return;
      try { await del(day ? '/api/briefs/' + cur.key : '/api/weeklies/' + encodeURIComponent(cur.key)); toast(day ? t('brief.deleted') : t('weekly.deleted')); brLoad(); }
      catch (e) { toast(t('common.delete_failed', { msg: e.message }), 6000); }
    }]);
  }
  docView(box, cur.content !== null ? cur.content : `# ${day ? cur.key : cur.key + ' (' + cur.range + ')'}\n\n${day ? t('brief.empty') : t('weekly.empty')}`, actions);
}

export function initDocs() {
  $('prep-new').onclick = prepForm;
  $('insight-new').onclick = () => insForm();
  registerView('prep', { show(o) { prepLoad().then(() => { if (o.name) { prep.current = o.name; prepShow(); prepList(); } else if (!prep.current && !prep.busy) prepForm(); }); } });
  registerView('insight', { show(o) { insLoad().then(() => { if (o.name) { ins.current = o.name; insShow(); insList(); } else if (o.form || (!ins.current && !ins.busy)) insForm(o.scope); }); } });
  registerView('briefs', {
    show(o) {
      if (o.mode) br.mode = o.mode;
      if (o.date) br.current = o.date;
      if (o.week) br.current = o.week;
      brModes(); brLoad();
    },
  });
}
