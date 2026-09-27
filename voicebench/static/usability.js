/* Keep browsing separate from the actual synthesis selection. */
let settingsRestored = false;
let reconnecting = false;
const sessionSettingsKey = 'voicebench.reload-settings.v1';
function saveStudioSettings() {
  try {
    localStorage.setItem(draftKey, $('#studio-text').value);
    sessionStorage.setItem(sessionSettingsKey, JSON.stringify({
      engine:$('#studio-engine').value, voice:app.voiceId,
      library:$('#studio-voice').value, seed:$('#studio-seed').value,
      quality:$('#studio-quality').value, ...galleryRequestOptions(),
    }));
    return true;
  } catch { return false; }
}
function restoreStudioSettings() {
  if (settingsRestored || !app.studio) return;
  settingsRestored = true;
  try {
    const saved = JSON.parse(sessionStorage.getItem(sessionSettingsKey) || 'null');
    if (!saved || !app.studio.studio_engines.some(e=>e.slug===saved.engine)) return;
    if (saved.voice && !app.studio.voice_catalog.some(v=>v.id===saved.voice && v.available)) return;
    $('#studio-engine').value = saved.engine;
    $('#studio-voice').value = saved.library || '';
    $('#studio-seed').value = saved.seed;
    $('#studio-quality').value = saved.quality;
    app.voiceId = saved.voice || null; app.voiceManuallyCleared = !saved.voice;
    renderVoiceGallery(); renderVoiceControls(true);
    $$('[data-generation]').forEach(input=>{if(saved.generation?.[input.dataset.generation]!=null)input.value=saved.generation[input.dataset.generation];});
    $('#voice-instruction').value = saved.generation?.instruct || '';
    renderStudioEngineState();
    $('#studio-text').dispatchEvent(new Event('input'));
  } catch { /* Old or unavailable browser storage must not block composition. */ }
}
function renderComposeSummary() {
  const name = $('#studio-voice-name').textContent;
  const engine = app.studio?.studio_engines.find(e=>e.slug===$('#studio-engine').value);
  const model = engine?.model_label || engine?.name?.split(' · ')[0] || '';
  $('#compose-selection').textContent = `${name}${model ? ' · '+model : ''}`;
  $('#picker-current').textContent = `현재 생성에 사용할 목소리: ${name}${model ? ' · '+model : ''}`;
  $('#compose-status').textContent = app.connected===false ? '서버 연결 후 생성할 수 있어요.' : !$('#studio-text').value.trim() ? '대본을 입력해 주세요.' : `${$('#studio-text').value.trim().length.toLocaleString()}자 · ${$('#studio-quality').value==='standard'?'기본 검사':'HQ 검사'} · 생성 버튼을 누르면 작업을 시작합니다.`;
}
function updateConnection(online) {
  app.connected = online;
  const status = $('#connection-status');
  status.textContent = online ? '● 서버 연결됨' : '● 서버 연결 끊김';
  status.dataset.state = online ? 'online' : 'offline';
  $('#connection-banner').classList.toggle('hidden', online);
  if (online) {
    restoreStudioSettings();
    const loaded = document.querySelector('meta[name="voicebench-ui-version"]')?.content;
    $('#update-banner').classList.toggle('hidden', !loaded || !app.studio?.ui_version || loaded===app.studio.ui_version);
  }
  renderStudioEngineState();
}
async function reconnectStudio() {
  if (reconnecting) return;
  reconnecting = true;
  $('#connection-retry').disabled = true;
  try { if (!app.state) await loadState(); else await loadStudio(); }
  catch { updateConnection(false); }
  finally { reconnecting=false; $('#connection-retry').disabled=false; }
}
function closeVoicePicker(toScript=false) {
  const picker = $('#voice-picker');
  if (!picker.open) return;
  picker.close();
  document.body.append($('#preview-dock'));
  $(toScript ? '#studio-text' : '#voice-picker-open').focus();
}
$('#voice-picker-open').onclick = () => {
  renderVoiceGallery(); renderComposeSummary();
  $('#voice-picker').append($('#preview-dock'));
  $('#voice-picker').showModal();
  $('#voice-search').focus();
};
$('#voice-picker-close').onclick = ()=>closeVoicePicker();
$('#voice-picker').addEventListener('cancel', event=>{event.preventDefault();closeVoicePicker();});
$('#picker-register').onclick = ()=>{closeVoicePicker();switchView('voices');$('#voice-name').focus();};
$$('[data-category]').forEach(button=>button.onclick=()=>{
  gallery.category=button.dataset.category; gallery.model=''; gallery.favoritesOnly=false;
  $('#voice-favorites').setAttribute('aria-pressed','false');
  $$('[data-category]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  renderVoiceGallery();
});
$('#connection-retry').onclick = reconnectStudio;
$('#update-reload').onclick = ()=>{
  if (!saveStudioSettings()) return flash('브라우저에 설정을 저장하지 못했습니다. 대본을 TXT로 먼저 저장해 주세요.',true);
  location.reload();
};
$('#studio-text').addEventListener('input', renderComposeSummary);
window.addEventListener('beforeunload', ()=>{if(app.studio)saveStudioSettings();});
// Initial connection failures also need a retry; normal polling starts after loadStudio.
setInterval(()=>{if(!app.studio && !reconnecting)reconnectStudio();},10000);
if (app.studio) updateConnection(app.connected!==false);
else if (app.connected===false) updateConnection(false);
