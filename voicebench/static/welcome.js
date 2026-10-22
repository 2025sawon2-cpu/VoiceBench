/* Saved samples only: opening this page never enqueues synthesis. */
const names = {'qwen3-tts':'Qwen Base · CustomVoice',zonos2:'ZONOS2',voxcpm2:'VoxCPM2','fish-speech':'Fish S2-Pro','chatterbox-v3':'Chatterbox V3','regression-tests':'앱 검사','korean-previews':'한국어 샘플','open-studio':'웹페이지 실행'};
const players = [];
let sequence = false;
const note = document.querySelector('#playback-note');
const allButton = document.querySelector('#play-all');
function textElement(tag, text, className) {
  const el = document.createElement(tag); el.textContent = text;
  if (className) el.className = className;
  return el;
}
async function playFrom(index) {
  players.forEach(player => player.pause());
  const player = players[index];
  if (!player) { sequence = false; allButton.textContent = '▶ 전체 샘플 다시 듣기'; return; }
  player.currentTime = 0;
  try {
    await player.play(); note.textContent = `${index + 1}/${players.length} · ${player.dataset.label}`;
    allButton.textContent = sequence ? '■ 재생 멈추기' : '▶ 전체 샘플 듣기';
  } catch {
    sequence = false; allButton.textContent = '▶ 전체 샘플 듣기';
    note.textContent = '브라우저가 자동 재생을 막았습니다. ‘전체 샘플 듣기’ 또는 아래 재생 버튼을 눌러 주세요.';
  }
}
allButton.addEventListener('click', () => {
  if (sequence) { sequence = false; players.forEach(p => p.pause()); allButton.textContent = '▶ 전체 샘플 듣기'; }
  else { sequence = true; playFrom(0); }
});
async function load() {
  try {
    const responses = await Promise.all([fetch('/api/setup'), fetch('/api/studio')]);
    // A server started before this update can still serve the new static guide.
    if ((!responses[0].ok && responses[0].status !== 404) || !responses[1].ok) throw new Error('서버 연결 또는 로그인 상태를 확인하세요.');
    const [setup, studio] = await Promise.all([
      responses[0].status === 404 ? Promise.resolve({status:'not_run',steps:[],samples:[]}) : responses[0].json(),
      responses[1].json()]);
    document.querySelector('#setup-heading').textContent = ({ready:'설치와 샘플 준비가 끝났어요',incomplete:'아직 준비가 필요한 항목이 있어요',running:'설치를 진행하고 있어요',not_run:'현재 PC의 저장 샘플입니다'})[setup.status] || '설치 상태를 확인해 주세요';
    if (setup.plan) {
      const p = setup.plan;
      document.querySelector('#hardware').textContent = `${p.gpu.name} · VRAM ${(p.gpu.memory_mb / 1024).toFixed(1)}GB · RAM ${p.ram_gb}GB · ZONOS2 ${p.zonos_quant.toUpperCase()}`;
      (p.warnings || []).forEach(w => document.querySelector('#warnings').append(textElement('li', w)));
    }
    (setup.steps || []).forEach(s => document.querySelector('#steps').append(textElement('span', `${s.status === 'ready' ? '✓' : s.status === 'failed' ? '!' : '…'} ${names[s.name] || s.name}`, 'step ' + s.status)));
    document.querySelector('#retry').hidden = setup.status !== 'incomplete';
    const results = new Map((setup.samples || []).map(s => [s.id, s]));
    const voices = (studio.voice_catalog || []).filter(v => v.kind === 'official' || v.kind === 'shared');
    voices.sort((a,b) => (a.id === 'qwen-sohee' ? -1 : b.id === 'qwen-sohee' ? 1 : a.model.localeCompare(b.model, 'ko')));
    voices.forEach(v => {
      const card = textElement('article', '', 'sample');
      card.append(textElement('h3', `${v.model} · ${v.name}`), textElement('p', v.kind === 'official' ? '공식 제공 목소리' : '공용 참조로 복제한 목소리', 'muted'));
      const result = results.get(v.id);
      if (v.preview_kind === 'generated' && v.preview_url && (!result || result.status === 'ready')) {
        const player = document.createElement('audio');
        player.controls = true; player.preload = 'none'; player.src = v.preview_url;
        player.dataset.label = `${v.model} · ${v.name}`;
        const index = players.length; players.push(player);
        player.addEventListener('play', () => players.forEach(p => { if (p !== player) p.pause(); }));
        player.addEventListener('ended', () => { if (sequence) playFrom(index + 1); });
        player.addEventListener('error', () => { sequence = false; allButton.textContent = '▶ 전체 샘플 듣기'; note.textContent = '샘플 파일을 읽지 못했습니다. 서버 연결과 설치 결과를 확인해 주세요.'; });
        const link = textElement('a', '이 목소리로 대본 읽기 →'); link.href = '/?voice=' + encodeURIComponent(v.id);
        card.append(player, link);
      } else card.append(textElement('p', result?.reason || '한국어 샘플 준비 필요', 'muted'));
      document.querySelector('#samples').append(card);
    });
    const missing = [...results.values()].filter(r => !voices.some(v => v.id === r.id));
    missing.forEach(r => document.querySelector('#samples').append(textElement('p', `${r.id}: ${r.reason || '준비 필요'}`, 'muted')));
    document.querySelector('#sample-count').textContent = `${players.length}개 샘플 재생 가능`;
    allButton.disabled = players.length === 0;
    if (players.length) { sequence = true; await playFrom(0); }
    else note.textContent = '아직 재생 가능한 한국어 샘플이 없습니다. 설치 결과를 확인해 주세요.';
  } catch (error) { document.querySelector('#setup-heading').textContent = '서버에 연결하지 못했어요'; note.textContent = error.message; }
}
load();
