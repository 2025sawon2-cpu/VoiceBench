/* A voice is a speaker identity. An engine/preset is an advanced inference choice. */
const gallery = {category:'official', model:'', favorites:new Set(), favoritesOnly:false, controlsEngine:'', signature:'', playingId:null};
try { gallery.favorites = new Set(JSON.parse(localStorage.getItem('voicebench.favorites.v1') || '[]')); } catch {}
const voiceKinds = {official:'공식 기본 음성',shared:'공용 목소리',personal:'내 목소리',library:'내 보관함'};
const genderLabels = {male:'남성',female:'여성',personal:'나의 음성'};
function filteredVoices(voices, query, gender, model, favoritesOnly, favorites) {
  const q = query.trim().toLocaleLowerCase();
  return voices.filter(v => (!model || v.model===model) && (!gender || v.gender===gender) && (!favoritesOnly || favorites.has(v.id)) && `${v.name} ${v.description} ${v.native_language} ${v.model}`.toLocaleLowerCase().includes(q));
}
function renderVoiceGallery() {
  const voices = app.studio?.voice_catalog || [];
  if (!app.voiceId && !app.voiceManuallyCleared) app.voiceId = voices.find(v => v.id==='personal-zonos2')?.id;
  const models = [...new Set(voices.map(v => v.model))];
  const tabs = $('#voice-model-tabs');
  const tabsKey = JSON.stringify([models,gallery.model]);
  if (tabs.dataset.signature !== tabsKey) {
    tabs.dataset.signature = tabsKey;
    tabs.innerHTML = ['',...models].map(model => `<button type="button" class="model-tab ${model===gallery.model?'active':''}" data-model="${escapeHtml(model)}" aria-pressed="${model===gallery.model}">${escapeHtml(model || '모든 모델')}</button>`).join('');
    tabs.querySelectorAll('button').forEach(button => button.onclick = () => {gallery.model=button.dataset.model;renderVoiceGallery();});
  }
  const sorted = [...voices].sort((a,b) => (a.id==='personal-zonos2'?-1:({official:0,shared:1,personal:2,library:3}[a.kind]??4))-(b.id==='personal-zonos2'?-1:({official:0,shared:1,personal:2,library:3}[b.kind]??4)));
  const grouped = gallery.favoritesOnly ? sorted : sorted.filter(v => gallery.category==='official' ? v.kind==='official' : gallery.category==='personal' ? v.kind==='library' || v.id==='personal-zonos2' : v.kind==='shared' || v.kind==='personal');
  const visible = filteredVoices(grouped,$('#voice-search').value,$('#voice-gender').value,gallery.model,gallery.favoritesOnly,gallery.favorites);
  $('#gallery-count').textContent = `${visible.length}개 선택 항목`;
  $('#gallery-explanation').textContent = gallery.category==='personal' && !gallery.favoritesOnly ? '등록한 녹음과 기존 내 목소리입니다. 같은 목소리를 다른 모델로 들어보려면 모델별 비교를 선택하세요.' : gallery.category==='comparison' ? '같은 목소리를 여러 모델로 비교하는 조합입니다. 항목 수는 서로 다른 화자의 수가 아닙니다. 공용 목소리는 다른 모델의 음성을 참조해 복제했습니다.' : ['VoxCPM2','Fish','Chatterbox'].includes(gallery.model)
    ? '현재 연결한 모델에 공식 남녀 화자 목록이 없어, 공용 남녀 목소리와 내 목소리를 제공합니다. 한국어 샘플은 해당 모델로 생성한 결과이며, 원본 미리듣기는 참조 녹음입니다.'
    : '공식 기본 음성 · 공용 목소리 · 내 목소리를 구분해 표시합니다. 영어·중국어 기반 음색은 한국어 억양이 다를 수 있어요.';
  const signature = JSON.stringify([visible,app.voiceId,[...gallery.favorites]]);
  if (gallery.signature !== signature) {
    gallery.signature = signature;
    $('#voice-cards').innerHTML = visible.map(v => `<article class="voice-card ${v.id===app.voiceId?'selected':''} ${!v.available?'unavailable':''}"><div class="voice-card-top"><span class="voice-avatar ${v.gender}">${v.gender==='female'?'F':v.gender==='male'?'M':'나'}</span><span class="voice-model">${escapeHtml(v.model)}</span><button type="button" class="favorite-button" data-favorite="${v.id}" aria-label="${escapeHtml(v.name)} 즐겨찾기" aria-pressed="${gallery.favorites.has(v.id)}">${gallery.favorites.has(v.id)?'★':'☆'}</button></div><button type="button" class="voice-choose" data-choose="${v.id}" ${!v.available?'disabled':''} aria-pressed="${v.id===app.voiceId}"><strong>${escapeHtml(v.name)}</strong><span>${escapeHtml(v.description)}</span></button><div class="voice-tags"><span>${genderLabels[v.gender] || '내 목소리'}</span><span>${voiceKinds[v.kind]}</span><span>${escapeHtml(v.native_language)}</span></div><div class="voice-card-bottom"><button type="button" class="preview-button" data-preview="${v.id}" ${!v.preview_url?'disabled':''} aria-label="${escapeHtml(v.name)} 샘플 듣기">▶ ${v.preview_kind==='generated'?'한국어 샘플':v.preview_kind==='reference'?'원본 미리듣기':'샘플 준비 필요'}</button><small>${v.id===app.voiceId?'✓ 선택됨':''}</small></div></article>`).join('') || '<div class="empty-state gallery-empty">조건에 맞는 목소리가 없어요. 다른 모델이나 검색어를 선택해 주세요.</div>';
    $$('[data-choose]').forEach(button => button.onclick=()=>chooseGalleryVoice(button.dataset.choose,true));
    $$('[data-preview]').forEach(button => button.onclick=()=>playVoicePreview(button.dataset.preview));
    $$('[data-favorite]').forEach(button => button.onclick=()=>{
      const id=button.dataset.favorite; gallery.favorites.has(id)?gallery.favorites.delete(id):gallery.favorites.add(id);
      try {localStorage.setItem('voicebench.favorites.v1',JSON.stringify([...gallery.favorites]));} catch {}
      renderVoiceGallery();
    });
  }
  const chosen = voices.find(v=>v.id===app.voiceId);
  $('#selected-voice-description').textContent = chosen ? `${chosen.description} · ${voiceKinds[chosen.kind]}` : '고급 설정에서 선택한 모델의 기본 목소리';
  $('#selected-preview').disabled = !chosen?.preview_url;
  renderVoiceControls();
}
function chooseGalleryVoice(id, autoplay=true) {
  const voice=app.studio?.voice_catalog?.find(v=>v.id===id);
  if (!voice?.available) return flash('이 목소리의 파일 또는 모델이 준비되지 않았습니다.',true);
  app.voiceId=id; app.voiceManuallyCleared=false;
  $('#studio-engine').value=voice.engine_slug;
  $('#studio-voice').value=voice.voice_project_id ? String(voice.voice_project_id) : '';
  renderVoiceGallery(); renderStudioEngineState();
  $('#studio-text').dispatchEvent(new Event('input'));
  if (typeof closeVoicePicker === 'function') closeVoicePicker(true);
  if (autoplay) playVoicePreview(id);
}
async function playVoicePreview(id) {
  const voice=app.studio?.voice_catalog?.find(v=>v.id===id);
  if (!voice?.preview_url) return flash('저장된 미리듣기가 아직 없습니다.',true);
  const audio=$('#voice-audition');
  if (gallery.playingId===id && !audio.paused) {audio.pause(); return;}
  audio.pause(); gallery.playingId=id;
  audio.src=voice.preview_url;
  $('#preview-title').textContent=voice.name+' · '+voice.model;
  $('#preview-caption').textContent=voice.preview_kind==='generated'?'저장된 생성 샘플 · 고급 설정을 바꾸기 전 기본 음성':'원본 녹음 미리듣기 · 현재 모델의 생성 결과가 아닙니다';
  $('#preview-download').href=voice.preview_url;
  $('#preview-download').download=id+(voice.preview_format || '.wav');
  $('#preview-dock').classList.remove('hidden');
  try {await audio.play();} catch {flash('자동 재생을 시작하지 못했습니다. 아래 플레이어의 재생 버튼을 눌러 주세요.',true);}
}
function galleryEngineChanged() {
  const slug=$('#studio-engine').value;
  const current=app.studio?.voice_catalog?.find(v=>v.id===app.voiceId);
  const same=current?.engine_slug===slug || (current?.kind==='library' && slug!=='qwen3-tts-17b-custom-voice' && !app.studio.studio_engines.find(e=>e.slug===slug)?.fixed_voice);
  if (!same) {
    const next=app.studio?.voice_catalog?.find(v=>v.engine_slug===slug && (v.kind==='personal'||v.kind==='official'));
    app.voiceId=next?.id || null;app.voiceManuallyCleared=!next;
    $('#studio-voice').value='';
  }
  renderVoiceGallery();
}
function renderVoiceControls(force=false) {
  const engine=app.studio?.studio_engines?.find(e=>e.slug===$('#studio-engine').value);
  if (!engine || (!force && gallery.controlsEngine===engine.slug)) return;
  gallery.controlsEngine=engine.slug;
  $('#voice-custom-controls').innerHTML=(engine.custom_controls||[]).map(c=>`<label class="field">${escapeHtml(c.label)}<input data-generation="${c.key}" type="number" min="${c.minimum}" max="${c.maximum}" step="${c.step}" value="${c.default ?? ''}" placeholder="모델 기본값"></label>`).join('') || '<p class="support">이 어댑터는 추가 샘플링 설정을 제공하지 않습니다.</p>';
  $('#voice-instruction-field').classList.toggle('hidden',!engine.instruction_supported);
  $('#voice-instruction').value='';
}
function galleryRequestOptions() {
  const generation={};
  $$('[data-generation]').forEach(input=>{if(input.value!=='')generation[input.dataset.generation]=Number(input.value);});
  const engine=app.studio?.studio_engines?.find(e=>e.slug===$('#studio-engine').value);
  if (engine?.instruction_supported && $('#voice-instruction').value.trim()) generation.instruct=$('#voice-instruction').value.trim();
  return {...(app.voiceId ? {voice_id:app.voiceId} : $('#studio-voice').value ? {voice_project_id:Number($('#studio-voice').value)} : {}),generation};
}
function restoreGalleryRequest(item) {
  app.voiceId=item.voice_id || app.studio.voice_catalog?.find(v=>v.kind==='library' && v.voice_project_id===item.project_id)?.id || app.studio.voice_catalog?.find(v=>v.engine_slug===item.engine_slug && v.kind==='personal')?.id || null;
  app.voiceManuallyCleared=!app.voiceId;
  renderVoiceGallery();renderVoiceControls(true);
  $$('[data-generation]').forEach(input=>{if(item.generation?.[input.dataset.generation]!=null)input.value=item.generation[input.dataset.generation];});
  $('#voice-instruction').value=item.generation?.instruct || '';
}
$('#voice-search').oninput=renderVoiceGallery;
$('#voice-gender').onchange=renderVoiceGallery;
$('#voice-favorites').onclick=()=>{gallery.favoritesOnly=!gallery.favoritesOnly;$('#voice-favorites').setAttribute('aria-pressed',String(gallery.favoritesOnly));renderVoiceGallery();};
$('#selected-preview').onclick=()=>playVoicePreview(app.voiceId);
$('#preview-close').onclick=()=>{$('#voice-audition').pause();$('#preview-dock').classList.add('hidden');};
$('#voice-reset-settings').onclick=()=>{renderVoiceControls(true);$('#studio-seed').value=13;$('#studio-quality').value='long_form_hq';renderStudioEngineState();};
$('#voice-audition').onerror=()=>flash('샘플을 재생하지 못했습니다. 샘플 저장으로 파일을 확인할 수 있습니다.',true);
if(app.studio){renderVoiceGallery();renderStudioEngineState();}
