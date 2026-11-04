/* coco · settings (AI channels, transcription, languages, about), first-run welcome, export, quit */
import { $, qsa, state, t, I18N, OUTPUT_LANGS, esc, api, post, toast, choose, openDialog, closeDialog,
  registerView, setView } from './core.js';
import { postDownload, recActive, stopBrowserRecording } from './library.js';

const TASK_KEYS = ['report', 'ask', 'track', 'prep', 'brief', 'weekly', 'memory', 'glossary', 'namefix', 'participants'];
const CLI_TYPES = ['claude-cli', 'codex-cli'];
const SECTIONS = ['ai', 'asr', 'lang', 'about'];
const CHANNELS = ['agy', 'gemini', 'openai', 'bailian', 'local'];
let section = 'ai';

/* ---------- capabilities, header status, banners ---------- */
export function applyCaps(cfg) {
  state.caps = { transcribe: cfg.transcribe || '', record: !!cfg.record, recordMode: cfg.record_mode || 'browser',
    platform: cfg.platform || 'mac', device: cfg.transcribe_device || '', textExts: cfg.text_exts || [], audioExts: cfg.audio_exts || [] };
  $('file-input').accept = ['audio/*', 'video/*'].concat(state.caps.audioExts, state.caps.textExts).join(',');
  const canRecord = state.caps.record && (state.caps.recordMode === 'ffmpeg' || !!(navigator.mediaDevices && window.MediaRecorder));
  $('btn-record').hidden = !canRecord;
  $('start-rec').hidden = !canRecord;
  if (!state.caps.transcribe) $('btn-upload').title = t('caps.upload_title_noengine');
  const ch = cfg.transcribe;
  const asr = $('asr-chip');
  asr.innerHTML = `${esc(t('nav.asr'))} <span class="v">${esc(ch ? t('asr.channel_' + ch) : t('nav.asr_none'))}</span>`;
  asr.classList.toggle('bad', !ch);
  asr.onclick = () => setView('settings', { section: 'asr' });
  const platform = state.caps.platform;
  $('imp-path').placeholder = platform === 'windows' ? 'C:\\Users\\you\\Desktop\\meetings\\' : platform === 'linux' ? '/home/you/meetings/' : '/Users/you/Desktop/meetings/';
  $('imp-path-tip').textContent = platform === 'windows' ? t('import.path_tip_win') : platform === 'mac' ? t('import.path_tip_mac') : '';
}

export const aiReady = cfg => !cfg.ai || cfg.ai.ready !== false;
export function applyAIState(cfg) {
  state.cfg = cfg;
  const ai = cfg.ai || {}, ready = aiReady(cfg);
  const chip = $('ai-chip');
  const label = (ai.primary && ai.primary.label) || '';
  chip.innerHTML = `${esc(t('nav.ai'))} <span class="v">${esc(ready ? label : t('header.ai_none'))}</span>`;
  chip.classList.toggle('bad', !ready);
  chip.onclick = () => ready ? setView('settings', { section: 'ai' }) : openWelcome();
  const b = $('ai-banner');
  if (ready || sessionStorage.getItem('coco-aibanner-dismissed')) { b.classList.remove('show'); return; }
  b.innerHTML = esc(t('aibanner.text')) + ` <button class="link" id="aib-setup">${esc(t('aibanner.setup'))}</button>`
    + ` <button class="link" id="aib-dismiss">${esc(t('aibanner.dismiss'))}</button>`;
  b.classList.add('show');
  $('aib-setup').onclick = openWelcome;
  $('aib-dismiss').onclick = () => { sessionStorage.setItem('coco-aibanner-dismissed', '1'); b.classList.remove('show'); };
}

export function setConn(down) {
  const b = $('conn-banner');
  if (down && !state.quitting) {
    b.textContent = t('conn.down', { hint: state.caps.platform === 'windows' ? t('conn.hint_win') : t('conn.hint_mac') });
    b.classList.add('show');
  } else b.classList.remove('show');
}

/* ---------- settings view ---------- */
function renderSections() {
  const list = $('set-sections');
  list.innerHTML = '';
  SECTIONS.forEach(s => {
    const div = document.createElement('div');
    div.className = 'item' + (s === section ? ' on' : '');
    div.innerHTML = `<div class="body"><div class="t">${esc(t('settings.sec_' + s))}</div><div class="s">${esc(t('settings.sec_' + s + '_desc'))}</div></div>`;
    div.onclick = () => { section = s; renderSections(); renderSection(); };
    list.appendChild(div);
  });
}

async function renderSection() {
  const box = $('settings-content');
  let cfg;
  try { cfg = await api('/api/config'); } catch (e) { box.innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; return; }
  state.cfg = cfg;
  if (section === 'ai') return renderAI(box, cfg);
  if (section === 'asr') return renderASR(box, cfg);
  if (section === 'lang') return renderLang(box, cfg);
  return renderAbout(box);
}

/* AI channels */
function presetGroups(presets) {
  const zh = I18N.lang.startsWith('zh');
  const rank = p => (p.region === 'cn' ? (zh ? 0 : 1) : p.region === 'intl' ? (zh ? 1 : 0) : 2);
  const groups = { subscription: [], api: [], local: [], custom: [] };
  Object.entries(presets).forEach(([k, p]) => { if (groups[p.group]) groups[p.group].push([k, p]); });
  groups.api.sort((a, b) => rank(a[1]) - rank(b[1]));
  return groups;
}
function guessPreset(p, presets) {
  if (p.preset && presets[p.preset]) return p.preset;
  const base = (p.base_url || '').replace(/\/+$/, '');
  if (p.type === 'claude-cli' && !base) return 'claude-cli';
  if (p.type === 'codex-cli') return 'codex-cli';
  const hit = Object.entries(presets).find(([, pr]) => pr.group !== 'hidden' && pr.group !== 'custom' && pr.type === p.type
    && (pr.base_url || '').replace(/\/+$/, '') === base);
  if (hit) return hit[0];
  return p.type === 'anthropic' ? 'custom-anthropic' : p.type === 'claude-cli' ? 'claude-cli' : 'custom-openai';
}
const PROF_HTML = `
  <b class="p-title"></b>
  <label class="fld p-servicelabel"></label><select class="full p-preset"></select>
  <div class="note p-status" hidden></div>
  <div class="grid2">
    <div class="p-keywrap"><label class="fld"><span class="p-keylabel"></span> <a class="p-keylink" target="_blank" rel="noopener"></a></label>
      <input type="password" class="p-key full" autocomplete="off"></div>
    <div><label class="fld p-modellabel"></label>
      <div class="combo"><input type="text" class="p-model"><button class="p-fetch" type="button"></button></div>
      <select class="full p-modelpick" hidden style="margin-top:6px"></select></div>
    <div class="p-reasonwrap"><label class="fld p-reasonlabel"></label>
      <select class="full p-reason"><option value=""></option><option>low</option><option>medium</option><option>high</option><option>xhigh</option><option>max</option></select></div>
  </div>
  <details class="adv"><summary class="p-advlabel"></summary>
    <div class="grid2">
      <div><label class="fld p-typelabel"></label><select class="full p-type">
        <option value="claude-cli"></option><option value="codex-cli"></option><option value="anthropic"></option><option value="openai"></option></select></div>
      <div><label class="fld p-baselabel"></label><input type="text" class="p-base full"></div>
      <div><label class="fld p-envlabel"></label><input type="text" class="p-keyenv full"></div>
      <div><label class="fld p-ctxlabel"></label><input type="number" class="p-ctx full" min="0" step="10000"></div>
      <div><label class="fld p-maxlabel"></label><input type="number" class="p-max full" min="256" step="1000" placeholder="16000"></div>
    </div>
  </details>
  <div class="row"><button class="p-test"></button><span class="test-out"></span></div>`;

const prof = name => document.querySelector(`.prof[data-prof="${name}"]`);
function buildProfile(name, p, ai) {
  const el = prof(name), presets = ai.presets || {};
  el.innerHTML = PROF_HTML;
  const q = c => el.querySelector(c);
  q('.p-title').textContent = t(name === 'primary' ? 'settings.profile_primary' : 'settings.profile_fast');
  q('.p-servicelabel').textContent = t('settings.service');
  q('.p-keylabel').textContent = t('settings.api_key');
  q('.p-modellabel').textContent = t('settings.model');
  q('.p-fetch').textContent = t('settings.fetch_models');
  q('.p-reasonlabel').textContent = t('settings.reasoning');
  q('.p-reason').options[0].textContent = t('settings.reasoning_default');
  q('.p-advlabel').textContent = t('settings.advanced');
  q('.p-typelabel').textContent = t('settings.type');
  ['claude-cli', 'codex-cli', 'anthropic', 'openai'].forEach((v, i) => { q('.p-type').options[i].textContent = t('settings.type_' + v.replace('-', '_')); });
  q('.p-baselabel').textContent = t('settings.base_url');
  q('.p-base').placeholder = t('settings.base_url_placeholder');
  q('.p-envlabel').textContent = t('settings.api_key_env');
  q('.p-ctxlabel').textContent = t('settings.max_context');
  q('.p-maxlabel').textContent = t('settings.max_tokens');
  q('.p-test').textContent = t('settings.test');
  const sel = q('.p-preset');
  Object.entries(presetGroups(presets)).forEach(([g, items]) => {
    if (!items.length) return;
    const og = document.createElement('optgroup'); og.label = t('settings.group_' + g);
    items.forEach(([k, pr]) => { const o = document.createElement('option'); o.value = k;
      o.textContent = g === 'subscription' ? t('settings.preset_' + k.replace(/-/g, '_')) : pr.label; og.appendChild(o); });
    sel.appendChild(og);
  });
  q('.p-type').value = p.type || 'claude-cli';
  q('.p-model').value = p.model || '';
  q('.p-base').value = p.base_url || '';
  q('.p-keyenv').value = p.api_key_env || '';
  q('.p-ctx').value = p.max_context_chars || '';
  q('.p-ctx').placeholder = '160000';
  q('.p-max').value = p.max_tokens && p.max_tokens !== 16000 ? p.max_tokens : '';
  q('.p-reason').value = p.reasoning || '';
  const key = q('.p-key');
  key.value = ''; key.dataset.dirty = '';
  key.placeholder = p.api_key_set ? t('settings.api_key_stored') : t('settings.api_key_placeholder');
  key.oninput = () => { key.dataset.dirty = '1'; };
  key.onchange = () => { if (key.value.trim()) fetchModels(name, true); };
  sel.value = guessPreset(p, presets);
  el.dataset.preset = sel.value;
  el.dataset.saved = sel.value; el.dataset.blocked = p.blocked || '';
  sel.onchange = () => {
    const pr = presets[sel.value]; if (!pr) return;
    el.dataset.preset = sel.value;
    q('.p-type').value = pr.type; q('.p-base').value = pr.base_url || '';
    q('.p-model').value = (name === 'fast' && pr.fast_model) ? pr.fast_model : (pr.model || '');
    q('.p-keyenv').value = pr.api_key_env || ''; q('.p-ctx').value = pr.max_context_chars || '';
    q('.p-reason').value = ''; q('.p-modelpick').hidden = true; q('.test-out').textContent = '';
    refreshProfile(name, ai);
    if (pr.group === 'custom') el.querySelector('details').open = true;
    if (pr.group === 'subscription' || pr.group === 'local') fetchModels(name, true);
  };
  q('.p-type').onchange = () => refreshProfile(name, ai);
  q('.p-fetch').onclick = () => fetchModels(name, false);
  q('.p-modelpick').onchange = e => { if (e.target.value !== '__') q('.p-model').value = e.target.value; };
  q('.p-test').onclick = () => testProfile(name);
  refreshProfile(name, ai);
}
function refreshProfile(name, ai) {
  const el = prof(name), q = c => el.querySelector(c);
  const pr = (ai.presets || {})[el.dataset.preset] || {};
  const type = q('.p-type').value;
  const cli = CLI_TYPES.includes(type) && !q('.p-base').value.trim();
  const local = pr.group === 'local';
  q('.p-keywrap').hidden = cli || local;
  q('.p-reasonwrap').hidden = !CLI_TYPES.includes(type);
  q('.p-model').placeholder = cli ? t('settings.model_placeholder') : (local ? t('settings.model_local_placeholder') : t('settings.model_api_placeholder'));
  const link = q('.p-keylink');
  if (pr.key_url && !cli) { link.href = pr.key_url; link.textContent = t(local ? 'settings.download_link' : 'settings.key_link'); link.hidden = false; }
  else link.hidden = true;
  const st = q('.p-status'), clis = ai.clis || {};
  st.hidden = true; st.className = 'note p-status';
  if (type === 'claude-cli' && cli) {
    st.hidden = false;
    if (clis.claude) { st.classList.add('ok'); st.textContent = t('settings.claude_ok'); }
    else { st.innerHTML = esc(t('settings.claude_missing')) + ` <button class="link">${esc(t('settings.show_steps'))}</button>`; st.querySelector('button').onclick = () => openCliGuide('claude'); }
  } else if (type === 'codex-cli') {
    st.hidden = false;
    if (clis.codex && clis.codex_login === 'chatgpt') { st.classList.add('ok'); st.textContent = t('settings.codex_ok'); }
    else if (clis.codex && clis.codex_login === 'apikey') { st.classList.add('ok'); st.textContent = t('settings.codex_apikey'); }
    else { st.innerHTML = esc(t(clis.codex ? 'settings.codex_login' : 'settings.codex_missing')) + ` <button class="link">${esc(t('settings.show_steps'))}</button>`; st.querySelector('button').onclick = () => openCliGuide('codex'); }
  } else if (local) { st.hidden = false; st.textContent = t('settings.local_hint'); }
  if (el.dataset.blocked && el.dataset.preset === el.dataset.saved) {  // the channel policy refuses the saved channel
    st.hidden = false; st.className = 'note p-status bad'; st.textContent = el.dataset.blocked;
  }
}
async function fetchModels(name, quiet) {
  const el = prof(name), q = c => el.querySelector(c);
  const b = q('.p-fetch'), pick = q('.p-modelpick');
  const label = b.textContent; b.disabled = true; b.textContent = '…';
  try {
    const r = await post('/api/settings/ai/models', { profile: name, inline: readProfile(name) });
    const models = r.models || [];
    if (!models.length) { if (!quiet) toast(t('settings.models_none')); pick.hidden = true; return; }
    pick.innerHTML = `<option value="__">${esc(t('settings.models_pick', { n: models.length }))}</option>`
      + models.map(m => `<option value="${esc(m.id)}" title="${esc(m.desc || '')}">${esc(m.name === m.id || !m.id ? (m.id || m.name) : m.name + ' · ' + m.id)}</option>`).join('');
    pick.hidden = false;
    const cur = q('.p-model').value;
    pick.value = models.some(m => m.id === cur) ? cur : '__';
    const pr = ((state.cfg.ai || {}).presets || {})[el.dataset.preset] || {};
    if (!cur && pr.group === 'local' && models[0]) { q('.p-model').value = models[0].id; pick.value = models[0].id; }
  } catch (e) { if (!quiet) toast(t('settings.models_failed', { msg: e.message }), 8000); pick.hidden = true; }
  finally { b.disabled = false; b.textContent = label; }
}
async function testProfile(name) {
  const el = prof(name), out = el.querySelector('.test-out'), b = el.querySelector('.p-test');
  b.disabled = true; out.className = 'test-out'; out.textContent = t('settings.testing');
  try {
    const r = await post('/api/settings/ai/test', { profile: name, inline: readProfile(name) });
    out.className = 'test-out ' + (r.ok ? 'ok' : 'bad');
    out.textContent = r.ok ? t('settings.test_ok', { model: r.model, ms: r.latency_ms, reply: r.reply }) : t('settings.test_failed', { msg: r.error });
    return r.ok;
  } catch (e) { out.className = 'test-out bad'; out.textContent = t('settings.test_failed', { msg: e.message }); return false; }
  finally { b.disabled = false; }
}
function readProfile(name) {
  const el = prof(name), q = c => el.querySelector(c), key = q('.p-key');
  return { type: q('.p-type').value, model: q('.p-model').value.trim(), base_url: q('.p-base').value.trim(),
    api_key: key.dataset.dirty ? key.value.trim() : null, api_key_env: q('.p-keyenv').value.trim(),
    max_context_chars: parseInt(q('.p-ctx').value || '0', 10) || 0, max_tokens: parseInt(q('.p-max').value || '0', 10) || 16000,
    extra_args: [], reasoning: q('.p-reason').value, preset: el.dataset.preset || '' };
}
function renderAI(box, cfg) {
  const ai = cfg.ai || {};
  const fastMode = ai.fast_configured ? 'custom' : (ai.fast_auto === false ? 'same' : 'auto');
  box.innerHTML = `<h2>${esc(t('settings.sec_ai'))}</h2><p class="lead">${esc(t('settings.ai_hint'))}</p>
    ${ai.edition_note ? `<p class="lead">${esc(ai.edition_note)}</p>` : ''}
    <div class="prof" data-prof="primary"></div>
    <div class="section-title">${esc(t('settings.fast_title'))}</div>
    <div class="form">${['auto', 'same', 'custom'].map(k => `<label class="radio"><input type="radio" name="fast-mode" value="${k}"${fastMode === k ? ' checked' : ''}>
      ${esc(t('settings.fast_' + k))}<span class="d">${esc(t('settings.fast_' + k + '_desc', { model: (ai.fast || {}).label || '' }))}</span></label>`).join('')}</div>
    <div class="prof" data-prof="fast"></div>
    <details class="adv"><summary>${esc(t('settings.tasks_title'))}</summary><p class="lead" style="margin-top:8px">${esc(t('settings.tasks_hint'))}</p><div class="tasks" id="task-map"></div></details>
    <div class="row"><button class="primary" id="btn-ai-save">${esc(t('common.save'))}</button></div>`;
  buildProfile('primary', ai.primary || {}, ai);
  buildProfile('fast', ai.fast || {}, ai);
  const syncFast = () => {
    const mode = document.querySelector('input[name="fast-mode"]:checked').value;
    prof('fast').hidden = mode !== 'custom';
    if (mode === 'custom' && !ai.fast_configured) { const sel = prof('fast').querySelector('.p-preset'); sel.value = prof('primary').dataset.preset || sel.value; sel.onchange(); }
  };
  qsa('input[name="fast-mode"]').forEach(r => { r.onchange = syncFast; });
  prof('fast').hidden = fastMode !== 'custom';
  const tm = $('task-map');
  TASK_KEYS.forEach(k => {
    const lab = document.createElement('span'); lab.textContent = t('settings.task_' + k); tm.appendChild(lab);
    const s = document.createElement('select'); s.dataset.task = k;
    [['primary', t('settings.profile_primary_short')], ['fast', t('settings.profile_fast_short')]].forEach(([v, l]) => { const o = document.createElement('option'); o.value = v; o.textContent = l; s.appendChild(o); });
    s.value = (ai.tasks || {})[k] || 'primary'; tm.appendChild(s);
  });
  $('btn-ai-save').onclick = async () => {
    const mode = document.querySelector('input[name="fast-mode"]:checked').value;
    const tasks = {}; qsa('#task-map select').forEach(s => { tasks[s.dataset.task] = s.value; });
    try {
      await post('/api/settings/ai', { primary: readProfile('primary'), fast: mode === 'custom' ? readProfile('fast') : null,
        fast_same_as_primary: mode !== 'custom', fast_auto: mode === 'auto', tasks });
      toast(t('settings.saved'));
      const c = await api('/api/config'); applyAIState(c); renderAI(box, c);
    } catch (e) { toast(t('common.save_failed', { msg: e.message }), 8000); }
  };
}

/* transcription channels */
function channelTag(name, c) {
  if (c.blocked) return ['bad', t('asr.tag_blocked')];
  if (name === 'agy') return c.ready ? ['ok', t('asr.tag_ready')] : c.found ? ['bad', t('asr.tag_login')] : ['', t('asr.tag_missing')];
  if (name === 'local') return c.ready ? ['ok', t('asr.tag_engine', { engine: c.device || c.engine })] : ['', t('asr.tag_no_engine')];
  return c.ready ? ['ok', t('asr.tag_ready')] : ['', t('asr.tag_need_key')];
}
async function renderASR(box, cfg) {
  let s;
  try { s = await api('/api/settings/asr'); } catch (e) { box.innerHTML = `<p class="bad-text">${esc(e.message)}</p>`; return; }
  const opt = (v, label, desc, tag) => `<label class="radio"><input type="radio" name="asr-ch" value="${v}"${s.setting === v ? ' checked' : ''}>
    <b>${esc(label)}</b>${tag ? `<span class="tag ${tag[0]}">${esc(tag[1])}</span>` : ''}<span class="d">${esc(desc)}</span></label>`;
  const langs = [['auto', t('header.lang_auto')], ['zh', '中文'], ['en', 'English'], ['fr', 'Français'], ['de', 'Deutsch'], ['es', 'Español'],
    ['it', 'Italiano'], ['pt', 'Português'], ['ja', '日本語'], ['ko', '한국어'], ['ru', 'Русский'], ['ar', 'العربية']];
  const shown = CHANNELS.filter(k => !(s.channels[k] || {}).blocked || s.setting === k);
  box.innerHTML = `<h2>${esc(t('settings.sec_asr'))}</h2><p class="lead">${esc(t('asr.lead'))}</p>
    ${s.edition_note ? `<p class="lead">${esc(s.edition_note)}</p>` : ''}
    <div class="form channels">
      ${opt('auto', t('asr.auto'), t('asr.auto_desc', { channel: s.active ? t('asr.channel_' + s.active) : t('nav.asr_none') }))}
      ${shown.map(k => opt(k, t('asr.channel_' + k), t('asr.desc_' + k), channelTag(k, s.channels[k]))).join('')}
      ${opt('none', t('asr.none'), t('asr.none_desc'))}
    </div>
    <div id="asr-panels"></div>
    <div class="section-title">${esc(t('asr.common'))}</div>
    <div class="form">
      <div class="grid2">
        <div><label class="fld">${esc(t('settings.transcribe_lang'))}</label><select id="asr-lang" class="full">${langs.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select></div>
        <div><label class="fld">${esc(t('settings.record_mode'))}</label><select id="asr-rec" class="full">
          <option value="auto">${esc(t('settings.record_auto'))}</option><option value="browser">${esc(t('settings.record_browser'))}</option><option value="ffmpeg">${esc(t('settings.record_ffmpeg'))}</option></select></div>
      </div>
      <label class="fld">${esc(t('asr.context'))}</label><input id="asr-context" class="full" placeholder="${esc(t('asr.context_placeholder'))}">
      <label class="radio" style="border:none"><input type="checkbox" id="asr-autofix"> ${esc(t('asr.autofix'))}<span class="d">${esc(t('asr.autofix_desc'))}</span></label>
      <div class="row"><button class="primary" id="btn-asr-save">${esc(t('common.save'))}</button></div>
    </div>`;
  $('asr-lang').value = s.language || 'auto';
  if ($('asr-lang').value !== (s.language || 'auto')) { const o = document.createElement('option'); o.value = s.language; o.textContent = s.language; $('asr-lang').appendChild(o); $('asr-lang').value = s.language; }
  $('asr-rec').value = cfg.record_mode_setting || 'auto';
  $('asr-context').value = s.context || '';
  $('asr-autofix').checked = s.glossary_autofix !== false;
  const panels = $('asr-panels');
  const field = (k, label, value, type = 'text', ph = '') => `<div><label class="fld">${esc(label)}</label><input data-k="${k}" type="${type}" class="full" value="${esc(value || '')}" placeholder="${esc(ph)}"></div>`;
  const panelHtml = k => {
    const c = s.channels[k];
    if (c.blocked) return `<div class="prof" data-ch="${k}"><b>${esc(t('asr.channel_' + k))}</b><div class="note bad">${esc(c.blocked)}</div></div>`;
    const test = `<div class="row"><button class="asr-test" data-ch="${k}">${esc(t('settings.test'))}</button><span class="test-out"></span></div>`;
    if (k === 'agy') return `<div class="prof" data-ch="agy"><b>${esc(t('asr.channel_agy'))}</b>
      <p class="lead" style="margin-top:6px">${esc(c.found ? (c.login ? t('asr.agy_ok') : t('asr.agy_login_hint')) : t('asr.agy_missing_hint'))}</p>
      <label class="fld">${esc(t('settings.model'))}</label><div class="combo" style="max-width:30em"><input data-k="model" value="${esc(c.model)}"><button class="asr-models">${esc(t('settings.fetch_models'))}</button></div>${test}</div>`;
    if (k === 'local') return `<div class="prof" data-ch="local"><b>${esc(t('asr.channel_local'))}</b>
      <p class="lead" style="margin-top:6px">${esc(c.engine ? t('asr.local_ok', { engine: c.engine, device: c.device }) : t('asr.local_missing'))}</p>
      <div class="grid2"><div><label class="fld">${esc(t('settings.whisper_model'))}</label><select data-k="whisper_model" class="full">
        <option value="turbo">${esc(t('header.model_turbo'))}</option><option value="large">${esc(t('header.model_large'))}</option><option value="small">${esc(t('header.model_small'))}</option></select></div></div></div>`;
    const keyPh = c.api_key_set ? t('settings.api_key_stored') : t('settings.api_key_placeholder');
    return `<div class="prof" data-ch="${k}"><b>${esc(t('asr.channel_' + k))}</b>
      <p class="lead" style="margin-top:6px">${esc(t('asr.cloud_note_' + k))}</p>
      <div class="grid2">${field('api_key', t('settings.api_key'), '', 'password', keyPh)}${field('model', t('settings.model'), c.model)}
      ${k !== 'bailian' ? field('base_url', t('settings.base_url'), c.base_url) : ''}${field('api_key_env', t('settings.api_key_env'), c.api_key_env)}</div>${test}</div>`;
  };
  const sync = () => {
    const v = document.querySelector('input[name="asr-ch"]:checked').value;
    const show = v === 'auto' ? (s.active || 'local') : v;
    panels.innerHTML = CHANNELS.includes(show) ? panelHtml(show) : '';
    const lm = panels.querySelector('[data-k="whisper_model"]'); if (lm) lm.value = s.whisper_model || 'turbo';
    const tb = panels.querySelector('.asr-test');
    if (tb) tb.onclick = async () => {
      const out = tb.nextElementSibling; out.className = 'test-out'; out.textContent = t('settings.testing');
      await saveASR(false);
      const r = await post('/api/settings/asr/test', { channel: tb.dataset.ch }).catch(e => ({ ok: false, detail: e.message }));
      out.className = 'test-out ' + (r.ok ? 'ok' : 'bad');
      out.textContent = r.ok ? t('asr.test_ok', { ms: r.ms || 0 }) : t('settings.test_failed', { msg: r.detail });
    };
    const mb = panels.querySelector('.asr-models');
    if (mb) mb.onclick = async () => {
      mb.disabled = true;
      const r = await api('/api/settings/asr/models?channel=agy').catch(() => ({ models: [] }));
      mb.disabled = false;
      if (!r.models.length) { toast(t('settings.models_none')); return; }
      const pick = await choose(t('settings.model'), '', [['', t('common.cancel')]].concat(r.models.map(m => [m, m])));
      if (pick) panels.querySelector('[data-k="model"]').value = pick;
    };
  };
  qsa('input[name="asr-ch"]').forEach(r => { r.onchange = sync; });
  sync();
  const saveASR = async (announce = true) => {
    const body = { channel: document.querySelector('input[name="asr-ch"]:checked').value, language: $('asr-lang').value,
      context: $('asr-context').value, glossary_autofix: $('asr-autofix').checked };
    const p = panels.querySelector('.prof');
    if (p && p.dataset.ch !== 'local') {
      const c = {};
      p.querySelectorAll('[data-k]').forEach(el => { if (el.dataset.k === 'api_key') { if (el.value.trim()) c.api_key = el.value.trim(); } else c[el.dataset.k] = el.value.trim(); });
      body[p.dataset.ch] = c;
    }
    try {
      await post('/api/settings/asr', body);
      const lm = panels.querySelector('[data-k="whisper_model"]');
      await post('/api/config', { record_mode: $('asr-rec').value, ...(lm ? { whisper_model: lm.value } : {}) });
      const c = await api('/api/config'); applyCaps(c);
      if (announce) { toast(t('settings.saved')); renderASR(box, c); }
    } catch (e) { toast(t('common.save_failed', { msg: e.message }), 8000); }
  };
  $('btn-asr-save').onclick = () => saveASR(true);
}

/* languages */
function renderLang(box, cfg) {
  box.innerHTML = `<h2>${esc(t('settings.sec_lang'))}</h2><div class="form">
    <div class="grid2"><div><label class="fld">${esc(t('settings.ui_lang'))}</label><select id="set-ui-lang" class="full"></select></div>
    <div><label class="fld">${esc(t('settings.output_lang'))}</label><select id="set-output-lang" class="full"></select></div></div>
    <p class="lead" style="margin-top:10px">${esc(t('settings.output_hint'))}</p>
    <div class="row"><button class="primary" id="btn-lang-save">${esc(t('common.save'))}</button></div></div>`;
  const ul = $('set-ui-lang');
  (cfg.available_languages || []).forEach(l => { const o = document.createElement('option'); o.value = l.code; o.textContent = l.name; ul.appendChild(o); });
  ul.value = I18N.lang;
  const ol = $('set-output-lang');
  [['ui', t('settings.output_ui')], ['source', t('settings.output_source')]].concat(OUTPUT_LANGS).forEach(([v, label]) => {
    const o = document.createElement('option'); o.value = v; o.textContent = label; ol.appendChild(o); });
  ol.value = cfg.output_language || 'ui';
  $('btn-lang-save').onclick = async () => {
    try {
      await post('/api/config', { output_language: ol.value, ui_language: ul.value });
      toast(t('settings.saved'));
      if (ul.value !== I18N.lang) { localStorage.setItem('coco-lang', ul.value); location.reload(); }
    } catch (e) { toast(t('common.save_failed', { msg: e.message }), 8000); }
  };
}

/* about */
async function renderAbout(box) {
  let a;
  try { a = await api('/api/about'); } catch (e) { box.textContent = e.message; return; }
  const row = (k, v, btn) => `<span class="k">${esc(k)}</span><code>${esc(v)}</code>${btn || '<span></span>'}`;
  box.innerHTML = `<h2>${esc(t('settings.sec_about'))}</h2><div class="kv">
    ${row(t('about.version'), a.version)}
    ${row(t('about.data'), a.root, `<button class="link" data-reveal="data">${esc(t('about.open'))}</button>`)}
    ${row(t('about.log'), a.log, `<button class="link" data-reveal="log">${esc(t('about.open'))}</button>`)}
    ${row(t('about.asr'), a.asr ? t('asr.channel_' + a.asr) : t('nav.asr_none'))}
    ${row(t('about.engine'), a.engine || t('about.engine_none'))}
    ${row(t('about.shortcut_state'), a.shortcut ? t('about.shortcut_yes') : t('about.shortcut_no'))}</div>
    <div class="row"><button id="btn-shortcut">${esc(t(a.shortcut ? 'about.shortcut_again' : 'about.shortcut'))}</button>
      <button id="btn-quit2">${esc(t('about.quit'))}</button></div>
    <p class="lead" style="margin-top:14px">${esc(t('about.hint'))}</p>`;
  qsa('[data-reveal]', box).forEach(b => { b.onclick = () => post('/api/reveal', { which: b.dataset.reveal }).catch(e => toast(e.message, 6000)); });
  $('btn-shortcut').onclick = async () => {
    try { const r = await post('/api/shortcut', {}); toast(t('about.shortcut_done', { path: (r.paths || [])[0] || '' }), 7000); renderAbout(box); }
    catch (e) { toast(t('common.failed', { msg: e.message }), 8000); }
  };
  $('btn-quit2').onclick = quitCoco;
}

/* ---------- export dialog ---------- */
let exportItems = [];
async function openExport() {
  $('export-list').innerHTML = `<p class="muted small" style="padding:16px 0">${esc(t('common.loading'))}</p>`;
  openDialog('export-dialog');
  let mm;
  try { mm = await api('/api/export/manifest'); } catch (e) { $('export-list').innerHTML = `<p class="bad-text">${esc(t('common.load_failed', { msg: e.message }))}</p>`; return; }
  const groups = [];
  if (mm.meetings.length) {
    const items = [];
    mm.meetings.forEach(m => m.files.forEach(f => items.push({ spec: { type: f.type, id: m.id, name: f.name }, label: `${m.title} · ${f.label}`, meta: m.date })));
    groups.push({ title: t('export.group_meetings'), items });
  }
  const grp = (arr, type, title) => { if ((arr || []).length) groups.push({ title, items: arr.map(b => ({ spec: { type, name: b.name }, label: b.label, meta: '' })) }); };
  grp(mm.briefs, 'brief', t('export.group_briefs'));
  grp(mm.weeklies, 'weekly', t('export.group_weeklies'));
  grp(mm.tracking, 'tracking', t('export.group_tracking'));
  grp(mm.preps, 'prep', t('export.group_preps'));
  const wrap = $('export-list'); wrap.innerHTML = ''; exportItems = [];
  if (!groups.length) wrap.innerHTML = `<p class="muted small" style="padding:16px 0">${esc(t('export.none'))}</p>`;
  let idx = 0;
  groups.forEach(g => {
    const gd = document.createElement('div');
    const head = document.createElement('label'); head.className = 'grp-head';
    head.innerHTML = `<input type="checkbox" class="grp-cb"> ${esc(g.title)} (${g.items.length})`;
    gd.appendChild(head);
    const cbs = [];
    g.items.forEach(it => {
      const i = idx++; exportItems.push(it.spec);
      const row = document.createElement('div'); row.className = 'pick';
      row.innerHTML = `<input type="checkbox" data-i="${i}" class="exp-cb"><label>${esc(it.label)}</label>${it.meta ? `<span class="muted">${esc(it.meta)}</span>` : ''}`;
      const cb = row.querySelector('.exp-cb'); cb.onchange = updateExportCount;
      row.querySelector('label').onclick = () => { cb.checked = !cb.checked; updateExportCount(); };
      cbs.push(cb); gd.appendChild(row);
    });
    head.querySelector('.grp-cb').onchange = e => { cbs.forEach(c => { c.checked = e.target.checked; }); updateExportCount(); };
    wrap.appendChild(gd);
  });
  $('export-all').checked = false;
  updateExportCount();
}
const checkedSpecs = () => qsa('#export-list .exp-cb').filter(c => c.checked).map(c => exportItems[+c.dataset.i]);
function updateExportCount() { const n = checkedSpecs().length; $('export-count').textContent = n ? t('export.count', { n }) : ''; }

/* ---------- quit ---------- */
export async function quitCoco() {
  const go = force => post('/api/shutdown', { force });
  try {
    let r = await go(false);
    if (!r.ok && r.busy) {
      const what = r.busy.map(b => t('quit.busy_' + b)).join(t('quit.and'));
      const ans = await choose(t('quit.confirm_title'), t('quit.confirm_body', { what }), [['cancel', t('common.cancel')], ['quit', t('quit.anyway'), true]]);
      if (ans !== 'quit') return;
      if (recActive()) await stopBrowserRecording(true);
      r = await go(true);
    } else if (!r.ok) return;
    state.quitting = true;
    $('quit-screen').classList.add('show');
  } catch (e) { toast(t('common.failed', { msg: e.message }), 8000); }
}

/* ---------- first-run welcome, CLI install guides ---------- */
export function openWelcome() {
  const clis = ((state.cfg || {}).ai || {}).clis || {};
  const badge = (ok, text) => `<span class="badge${ok ? ' ok' : ''}">${esc(text)}</span>`;
  const codexState = clis.codex && clis.codex_login ? [true, t('welcome.ready')] : clis.codex ? [false, t('welcome.needs_login')] : [false, t('welcome.not_installed')];
  const cards = [
    ['claude', t('welcome.claude_title'), t('welcome.claude_desc'), badge(!!clis.claude, clis.claude ? t('welcome.ready') : t('welcome.not_installed'))],
    clis.codex_allowed === false ? null : ['codex', t('welcome.chatgpt_title'), t('welcome.chatgpt_desc'), badge(...codexState)],
    ['api', t('welcome.api_title'), t('welcome.api_desc'), ''],
    ['local', t('welcome.local_title'), t('welcome.local_desc'), ''],
  ].filter(Boolean);
  $('welcome-choices').innerHTML = cards.map(([k, title, desc, b]) => `<button class="choice" data-k="${k}"><b>${esc(title)}</b><span>${esc(desc)}</span>${b}</button>`).join('');
  qsa('#welcome-choices .choice').forEach(c => { c.onclick = () => pickWelcome(c.dataset.k); });
  openDialog('welcome-dialog');
}
async function useSubscription(which) {
  const preset = which === 'codex' ? 'codex-cli' : 'claude-cli';
  const pr = ((state.cfg.ai || {}).presets || {})[preset] || {};
  toast(t('welcome.testing'), 20000);
  try {
    await post('/api/settings/ai', { primary: { type: pr.type, model: '', base_url: '', api_key: null, api_key_env: '', max_context_chars: 0,
      max_tokens: 16000, extra_args: [], reasoning: '', preset } });
    const r = await post('/api/settings/ai/test', { profile: 'primary' });
    applyAIState(await api('/api/config'));
    toast(r.ok ? t('welcome.done', { name: which === 'codex' ? 'ChatGPT' : 'Claude', ms: r.latency_ms }) : t('settings.test_failed', { msg: r.error }), r.ok ? 6000 : 10000);
  } catch (e) { toast(t('common.failed', { msg: e.message }), 8000); }
}
async function pickWelcome(k) {
  const clis = ((state.cfg || {}).ai || {}).clis || {};
  closeDialog('welcome-dialog');
  if (k === 'codex' || k === 'claude') {
    const ready = k === 'codex' ? (clis.codex && clis.codex_login) : clis.claude;
    if (ready) await useSubscription(k); else openCliGuide(k, true);
    return;
  }
  section = 'ai';
  setView('settings', { section: 'ai' });
  setTimeout(() => {
    const p = prof('primary'); if (!p) return;
    const sel = p.querySelector('.p-preset');
    if (k === 'local') sel.value = 'ollama';
    else { const first = presetGroups((state.cfg.ai || {}).presets || {}).api[0]; if (first) sel.value = first[0]; }
    sel.onchange();
    if (k === 'api') p.querySelector('.p-key').focus();
  }, 300);
}
let cliGuideFor = 'claude', cliGuideSave = false;
export function openCliGuide(which, saveWhenReady) {
  cliGuideFor = which; cliGuideSave = !!saveWhenReady;
  const plat = { windows: 'win', linux: 'linux' }[state.caps.platform] || 'mac';
  const clis = ((state.cfg || {}).ai || {}).clis || {};
  const loginOnly = which === 'codex' && clis.codex && !clis.codex_login;
  $('cli-title').textContent = t(`cliguide.${which}_title`);
  $('cli-intro').textContent = t(`cliguide.${which}_intro`);
  $('cli-steps').textContent = loginOnly ? t('cliguide.codex_login_steps') : t(`cliguide.${which}_steps_${plat}`);
  openDialog('cli-dialog');
}
async function recheckCli() {
  try {
    const cfg = await api('/api/config');
    state.cfg = cfg;
    const clis = (cfg.ai || {}).clis || {};
    const ok = cliGuideFor === 'codex' ? (clis.codex && clis.codex_login) : clis.claude;
    if (!ok) { toast(t(`cliguide.${cliGuideFor}_recheck_fail`), 9000); return; }
    closeDialog('cli-dialog');
    if (cliGuideSave || !aiReady(cfg)) await useSubscription(cliGuideFor);
    else { toast(t('cliguide.recheck_ok')); applyAIState(cfg); if (state.view === 'settings') renderSection(); }
  } catch { toast(t(`cliguide.${cliGuideFor}_recheck_fail`), 9000); }
}

export function initSettings() {
  $('nav-export').onclick = openExport;
  $('export-all').onchange = e => { qsa('#export-list .exp-cb, #export-list .grp-cb').forEach(c => { c.checked = e.target.checked; }); updateExportCount(); };
  $('btn-export-go').onclick = async () => { const specs = checkedSpecs(); if (!specs.length) { toast(t('export.pick')); return; } await postDownload(specs); closeDialog('export-dialog'); };
  $('btn-quit').onclick = quitCoco;
  $('btn-welcome-later').onclick = () => { sessionStorage.setItem('coco-welcome-later', '1'); closeDialog('welcome-dialog'); };
  $('btn-cli-recheck').onclick = recheckCli;
  registerView('settings', { show(o) { if (o.section) section = o.section; renderSections(); renderSection(); } });
}
