/* coco · questions about one meeting or the whole library (streamed) */
import { $, state, t, esc, md, api, post, streamApi, throttle, toast, on } from './core.js';

export function setChatOpen(open) {
  state.chatOpen = open;
  localStorage.setItem('coco-chat', open ? '1' : '0');
  $('chat').hidden = !open || !['library', 'memory'].includes(state.view);
  const b = $('btn-ask-toggle'); if (b) b.textContent = open ? t('doc.ask_hide') : t('doc.ask');
  updateChatRef();
}

export function askAbout(text, all = true) {
  setChatOpen(true);
  $('chat-all').checked = all;
  $('chat-input').value = text;
  updateChatRef();
  $('chat-input').focus();
}

export function updateChatRef() {
  const all = $('chat-all').checked;
  const noTarget = !all && !state.current;
  $('chat-input').disabled = noTarget;
  $('btn-send').disabled = noTarget;
  $('chat-ref').textContent = all ? t('chat.ref_all')
    : (state.current && state.currentData) ? t('chat.ref', { title: state.currentData.title }) : t('chat.ref_none');
  $('chat-input').placeholder = all ? t('chat.placeholder_all') : (noTarget ? t('chat.placeholder_none') : t('chat.placeholder'));
}

async function send() {
  const q = $('chat-input').value.trim();
  if (!q) return;
  $('chat-input').value = '';
  const all = $('chat-all').checked;
  const log = $('chat-log');
  const ref = all ? t('chat.ref_all') : (state.currentData ? state.currentData.title : '');
  const stamp = new Date().toTimeString().slice(0, 5);
  state.chat.push({ role: 'user', text: q, ref, time: stamp });
  log.insertAdjacentHTML('beforeend', `<div class="bubble user">${esc(q)}</div>`);
  const tip = document.createElement('div');
  tip.className = 'bubble coco thinking';
  tip.textContent = all ? t('chat.thinking_all') : t('chat.thinking');
  log.appendChild(tip); log.scrollTop = log.scrollHeight;
  let acc = '';
  const paint = throttle(() => { tip.className = 'bubble coco md'; tip.innerHTML = md(acc); log.scrollTop = log.scrollHeight; }, 120);
  try {
    const r = await streamApi('/api/stream/ask', { question: q, ids: !all && state.current ? [state.current] : [], all }, txt => { acc += txt; paint(); });
    tip.className = 'bubble coco md'; tip.innerHTML = md(r.answer || acc);
    state.chat.push({ role: 'coco', text: r.answer || acc, ref, time: stamp });
  } catch (e) { tip.className = 'bubble coco'; tip.textContent = t('chat.error', { msg: e.message }); }
  log.scrollTop = log.scrollHeight;
}

function chatMarkdown() {
  const now = new Date();
  const lines = [`# ${t('chat.md_title')} · ${now.toISOString().slice(0, 10)} ${now.toTimeString().slice(0, 5)}`, ''];
  for (const m of state.chat) {
    if (m.role === 'user') lines.push(`## ${t('chat.md_question')} ${m.time}${m.ref ? ' · ' + m.ref : ''}`, '', m.text, '');
    else lines.push('**coco**', '', m.text, '', '---', '');
  }
  return lines.join('\n').trim() + '\n';
}

async function exportChat() {
  if (!state.chat.length) { toast(t('chat.export_empty')); return; }
  const content = chatMarkdown();
  const now = new Date();
  const stamp = now.toISOString().slice(0, 10).replace(/-/g, '') + '-' + now.toTimeString().slice(0, 5).replace(':', '');
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }));
  const a = document.createElement('a'); a.href = url; a.download = `coco-chat-${stamp}.md`; document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  if (state.current) {
    try {
      await post(`/api/meetings/${encodeURIComponent(state.current)}/chat`, { content });
      toast(t('chat.saved_to_meeting', { title: state.currentData ? state.currentData.title : state.current }), 6000);
    } catch (e) { toast(t('chat.exported') + ' · ' + t('common.save_failed', { msg: e.message }), 6000); }
  } else toast(t('chat.exported'));
}

export function initChat() {
  $('chat-all').onchange = updateChatRef;
  $('btn-send').onclick = send;
  $('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  $('btn-chat-export').onclick = exportChat;
  $('btn-chat-close').onclick = () => setChatOpen(false);
  $('btn-ask-toggle').onclick = () => setChatOpen(!state.chatOpen);
  on('meeting', updateChatRef);
  on('view', () => { $('chat').hidden = !state.chatOpen || !['library', 'memory'].includes(state.view); });
  const saved = localStorage.getItem('coco-chat');
  setChatOpen(saved === null ? window.innerWidth >= 1360 : saved === '1');
}
