/* Standalone workspace interactions; no remote assets or client dependencies. */
const draftKey = 'voicebench.studio.draft.v1';
function saveDraft() {
  try { localStorage.setItem(draftKey, $('#studio-text').value); $('#draft-state').textContent = '이 브라우저에 저장됨'; }
  catch { $('#draft-state').textContent = '자동 저장 불가 · TXT로 저장하세요'; }
}
function setScript(text) { $('#studio-text').value = text; $('#studio-text').dispatchEvent(new Event('input')); saveDraft(); }
function renderWorkspace() {
  const studio = app.studio;
  if (!studio) return;
  const engine = studio.studio_engines.find(e => e.slug === $('#studio-engine').value);
  const picker = $('#studio-voice');
  const selected = picker.value;
  picker.innerHTML = '<option value="">모델 기본 목소리</option>' + (studio.voices || []).filter(v => v.available).map(v => `<option value="${v.id}">${escapeHtml(v.name)}</option>`).join('');
  picker.disabled = !!engine?.fixed_voice;
  if (!engine?.fixed_voice && [...picker.options].some(v => v.value === selected)) picker.value = selected;
  const signature = JSON.stringify(studio.voices || []);
  const library = $('#voice-library');
  if (library.dataset.signature !== signature) {
    library.dataset.signature = signature;
    library.innerHTML = `<div class="voice-tile"><strong>내 목소리 · 기본</strong><p>기존에 선택한 본인 녹음으로 복제합니다.</p><button class="ghost" data-voice="">이 목소리 사용</button></div>` + (studio.voices || []).map(v => `<div class="voice-tile"><strong>${escapeHtml(v.name)}</strong><p>${escapeHtml(v.transcript)}</p>${v.available ? `<audio controls preload="none" src="${v.audio_url}"></audio><button class="ghost" data-voice="${v.id}">이 목소리 사용</button>` : '<p>원본 파일을 찾을 수 없습니다.</p>'}</div>`).join('');
    $$('[data-voice]').forEach(button => button.onclick = () => {
      $('#studio-engine').value = studio.default_engine_slug;
      renderWorkspace(); picker.value = button.dataset.voice; renderStudioEngineState();
      if (typeof chooseGalleryVoice === 'function') chooseGalleryVoice(button.dataset.voice ? 'library-'+button.dataset.voice : 'personal-zonos2', false);
      $('#studio-text').dispatchEvent(new Event('input')); switchView('studio');
    });
  }
  const latest = studio.requests.find(r => r.status === 'succeeded');
  const latestNode = $('#studio-latest');
  if (latest && latestNode.dataset.id !== String(latest.id)) {
    latestNode.dataset.id = String(latest.id);
    latestNode.innerHTML = `<p><b>#${latest.id}</b> · ${escapeHtml(latest.original_text.slice(0, 95))}<br><small>${escapeHtml(latest.voice_name || "기본 목소리")} · ${escapeHtml(latest.engine_name.split(" · ")[0])} · ${Number(latest.duration_seconds).toFixed(1)}초</small></p><audio controls preload="none" src="${latest.audio_url}"></audio><a class="download-button" href="${latest.audio_url}" download="voice-${latest.id}.wav">WAV 저장</a>`;
  }
}
function reuseStudioRequest(id) {
  const item = app.studio.requests.find(r => r.id === id);
  if (!item) return;
  if (item.voice_id && !app.studio.voice_catalog?.some(v => v.id===item.voice_id && v.available)) return flash('이 기록의 목소리는 현재 사용할 수 없습니다.', true);
  const originalVoice = (app.studio.voices || []).find(v => v.id === item.project_id);
  if (originalVoice && !originalVoice.available) return flash('이 기록의 원본 목소리 파일을 찾을 수 없습니다.', true);
  if ($('#studio-text').value.trim() && $('#studio-text').value !== item.original_text && !confirm('작성 중인 대본을 이 기록으로 바꿀까요?')) return;
  if (![...$('#studio-engine').options].some(o => o.value === item.engine_slug)) return flash('이 기록의 모델은 현재 사용할 수 없습니다.', true);
  $('#studio-engine').value = item.engine_slug;
  $('#studio-seed').value = item.seed;
  $('#studio-quality').value = item.quality_mode || 'long_form_hq';
  renderWorkspace();
  $('#studio-voice').value = (app.studio.voices || []).some(v => v.id === item.project_id) ? String(item.project_id) : '';
  if (typeof restoreGalleryRequest === 'function') restoreGalleryRequest(item);
  setScript(item.original_text); renderStudioEngineState(); switchView('studio');
  flash('대본과 설정을 불러왔습니다. 음성 만들기를 누르면 새로 생성합니다.');
}
$('#studio-voice').onchange = renderStudioEngineState;
$('#studio-quality').onchange = renderStudioEngineState;
$('#history-search').oninput = () => renderStudio();
$('#history-status').onchange = () => renderStudio();
$$('[data-go]').forEach(button => button.onclick = () => switchView(button.dataset.go));
$('#studio-text').addEventListener('input', saveDraft);
$('#script-example').onclick = () => {
  if ($('#studio-text').value.trim() && !confirm('작성 중인 대본을 예시로 바꿀까요?')) return;
  setScript('안녕하세요. 오늘은 일상 속 작은 발견을 이야기해 보려고 합니다. 익숙한 길에서도 잠시 걸음을 늦추면, 미처 보지 못했던 새로운 풍경이 눈에 들어오죠. 여러분은 오늘 어떤 순간을 기억하고 싶으신가요?');
};
$('#script-clear').onclick = () => { if (!$('#studio-text').value || confirm('작성 중인 대본을 비울까요?')) setScript(''); };
$('#script-file').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 1024 * 1024) throw new Error('대본 파일은 1MB 이하로 선택하세요.');
    if ($('#studio-text').value.trim() && !confirm('작성 중인 대본을 파일 내용으로 바꿀까요?')) return;
    setScript(await file.text()); flash('대본을 불러왔습니다.');
  } catch (error) { flash(error.message, true); }
  finally { event.target.value = ''; }
};
$('#script-save').onclick = () => {
  const url = URL.createObjectURL(new Blob([$('#studio-text').value], {type:'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'voicebench-script.txt'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$('#voice-form').onsubmit = async event => {
  event.preventDefault();
  if (!$('#voice-consent').checked) return;
  const file = $('#voice-file').files[0];
  if (!file) return;
  const button = $('#voice-submit'); button.disabled = true; button.textContent = '등록 중…';
  try {
    if (file.size > 12 * 1024 * 1024) throw new Error('음성 파일은 12MB 이하로 선택하세요.');
    await api('/api/voices', {method:'POST', headers:{'Content-Type':'audio/wav', 'X-Voice-Name':encodeURIComponent($('#voice-name').value), 'X-Reference-Text':encodeURIComponent($('#voice-transcript').value)}, body:file});
    $('#voice-form').reset(); await loadStudio(); flash('목소리를 등록했습니다.');
  } catch (error) { flash(error.message, true); }
  finally { button.disabled = false; button.textContent = '목소리 등록'; }
};
// Never overlap playback when comparing takes.
document.addEventListener('play', event => { if (event.target.tagName === 'AUDIO') $$('audio').forEach(a => { if (a !== event.target) a.pause(); }); }, true);
try { const draft = localStorage.getItem(draftKey); if (draft) setScript(draft); } catch { /* Private browsing may disable storage. */ }

if (app.studio) renderWorkspace();
