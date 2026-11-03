/* coco · start-up */
import { $, qsa, state, api, loadI18n, pickLang, initDialogs, setView, toast } from './core.js';
import { initLibrary, loadMeetings, checkRecordingOnLoad, setRecUI } from './library.js';
import { initChat, updateChatRef } from './chat.js';
import { initMemory } from './memory.js';
import { initDocs } from './docs.js';
import { initSettings, applyCaps, applyAIState, aiReady, setConn, openWelcome } from './settings.js';

async function bootstrap() {
  const cfg = await (await fetch('/api/config')).json();
  await loadI18n(pickLang(cfg));
  state.templates = await api('/api/templates');
  applyCaps(cfg);
  applyAIState(cfg);
  await loadMeetings();
  updateChatRef();
  await checkRecordingOnLoad();
  setRecUI();
  if (!aiReady(cfg) && !(cfg.ai || {}).configured && !sessionStorage.getItem('coco-welcome-later')) openWelcome();
}

(async function init() {
  $('brand-icon').src = document.querySelector('link[rel="icon"]').href;
  initDialogs();
  initLibrary();
  initChat();
  initMemory();
  initDocs();
  initSettings();
  qsa('.nav-item[data-view]').forEach(b => { b.onclick = () => setView(b.dataset.view); });
  while (true) {
    try { await bootstrap(); setConn(false); break; }
    catch (e) { console.error(e); setConn(true); await new Promise(r => setTimeout(r, 2000)); }
  }
  const view = new URLSearchParams(location.search).get('view');
  setView(['library', 'memory', 'prep', 'insight', 'briefs', 'settings'].includes(view) ? view : 'library');
  setInterval(async () => {
    if (state.quitting) return;
    try { await loadMeetings(); setConn(false); } catch { setConn(true); }
  }, 8000);
  window.cocoToast = toast;
})();
