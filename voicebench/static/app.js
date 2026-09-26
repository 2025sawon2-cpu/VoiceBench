const app = { state: null, studio: null, project: null, runs: [], comparison: null, poller: null, studioPoller: null };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get('content-type') || '';
  const payload = type.includes('json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload.error || payload || `HTTP ${response.status}`);
  return payload;
}

function jsonOptions(payload) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
}

function flash(message, error = false) {
  const node = $('#flash');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.remove('hidden');
  clearTimeout(node.timer);
  node.timer = setTimeout(() => node.classList.add('hidden'), 3500);
}

async function loadState() {
  app.state = await api('/api/state');
  renderHardware(); renderProjects(); renderEngines();
  await loadStudio();
  if (!app.project && app.state.projects.length) await selectProject(app.state.projects[0].id);
}

async function loadStudio() {
  app.studio = await api('/api/studio');
  renderStudio();
}

function studioStatusLabel(status) {
  return ({queued:'대기 중',running:'생성 중',succeeded:'완료',failed:'실패',cancelled:'취소'})[status] || status;
}

function renderStudioEngineState() {
  const engine = app.studio?.studio_engines?.find(item => item.slug === $('#studio-engine').value);
  const available = app.studio?.available && Boolean(engine);
  const quality = engine?.slug.startsWith('qwen3-tts-') ? '자동 발음·파형 검사' : '파형 검사 · 발음 자동 재시도 없음';
  $('#studio-engine-state').textContent = available ? `${engine.name} · ${quality}` : '엔진 준비 필요';
  $('#studio-engine-state').classList.toggle('muted', !available);
  $('#studio-generate').disabled = !available;
}

function renderStudio() {
  const studio = app.studio;
  if (!studio) return;
  $('#studio-voice-name').textContent = studio.voice?.name || '레퍼런스 미등록';
  const engineSelect = $('#studio-engine');
  const selectedEngine = engineSelect.value;
  engineSelect.innerHTML = (studio.studio_engines || []).map(engine =>
    `<option value="${engine.slug}">${escapeHtml(engine.name)}</option>`
  ).join('');
  if ([...engineSelect.options].some(option => option.value === selectedEngine)) {
    engineSelect.value = selectedEngine;
  } else if ([...engineSelect.options].some(option => option.value === studio.default_engine_slug)) {
    engineSelect.value = studio.default_engine_slug;
  }
  renderStudioEngineState();

  const active = studio.requests.find(item => item.id === studio.processing_request_id)
    || studio.requests.find(item => item.status === 'queued');
  $('#studio-active').classList.toggle('hidden', !active);
  if (active) {
    const done = Number(active.completed_segments || 0);
    const total = Number(active.segment_count || 1);
    $('#studio-active-state').textContent = studioStatusLabel(active.status);
    $('#studio-progress-bar').style.width = `${Math.round(done / total * 100)}%`;
    const source = active.source === 'external_api' ? 'MCP/API' : '웹';
    const plan = active.speech_plan || {};
    const planLabel = plan.paragraph_count ? `${plan.paragraph_count}개 문단 낭독 계획 · ` : '';
    $('#studio-progress-label').textContent = active.status === 'cancelled'
      ? '취소됐습니다. 이미 GPU에서 시작된 마지막 조각만 정리 중입니다.'
      : active.status === 'queued'
        ? `${source} 요청이 대기 중입니다. ${planLabel}앞에 생성 조각 ${active.jobs_ahead || 0}개가 남아 있습니다.`
        : `${source} 요청을 생성·품질 검사 중입니다. ${planLabel}${total}개 조각 중 ${done}개 완료, 남은 대기 조각 ${active.queued_segments || 0}개.`;
  }

  const history = $('#studio-history');
  if (!studio.requests.length) {
    history.className = 'studio-history empty-state';
    history.textContent = '아직 생성한 음성이 없습니다.';
  } else {
    history.className = 'studio-history';
    history.innerHTML = studio.requests.map((item, index) => {
      const date = new Date(item.created_at).toLocaleString('ko-KR', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
      const progress = `${item.completed_segments || 0}/${item.segment_count || 0}조각`;
      const source = item.source === 'external_api' ? 'MCP/API' : '웹';
      const engine = item.engine_name || item.engine_slug || '엔진 정보 없음';
      const quality = item.quality_control || {};
      const fallbackSeeds = Object.entries(quality.selected_seed_counts || {})
        .filter(([seed]) => Number(seed) !== Number(item.seed))
        .map(([seed, count]) => `Seed ${seed} ${count}조각`);
      const seedLabel = fallbackSeeds.length
        ? `기본 Seed ${item.seed} · QA 대체 ${fallbackSeeds.join(', ')}`
        : `Seed ${item.seed}`;
      const qualityLabel = quality.checked_segments
        ? ` · 품질검사 ${quality.checked_segments}/${item.segment_count || 0}`
          + (quality.regenerated_segments ? ` · 재생성 ${quality.regenerated_segments}조각` : '')
          + (quality.warning_segments ? ` · 경고 ${quality.warning_segments}조각` : '')
        : '';
      const speechPlan = item.speech_plan || {};
      const roleCounts = speechPlan.role_counts || {};
      const expressiveUnits = Number(roleCounts.question || 0)
        + Number(roleCounts.transition || 0)
        + Number(roleCounts.conclusion || 0)
        + Number(roleCounts.exclamation || 0);
      const planSummary = speechPlan.paragraph_count
        ? ` · ${speechPlan.paragraph_count}문단/${speechPlan.segment_count}생성 구간`
          + (expressiveUnits ? ` · 질문·전환 등 ${expressiveUnits}구간` : '')
        : '';
      const continuity = item.continuity || {};
      const continuityLabel = continuity.requested_segments
        ? ` · 문맥 연결 적용 ${continuity.used_segments || 0}구간`
          + (continuity.fallback_segments ? ` · 원본 참조로 재시도 ${continuity.fallback_segments}구간` : '')
        : '';
      const audio = item.status === 'succeeded'
        ? `<audio controls preload="none" src="${item.audio_url}"></audio><a class="download-button" href="${item.audio_url}" download="my-voice-${item.id}.wav">WAV 저장</a>`
        : item.status === 'failed'
          ? `<span class="studio-error">${escapeHtml((item.error || '생성에 실패했습니다.').slice(0, 180))}</span>`
          : `<span class="tag ${item.status === 'queued' ? 'muted' : ''}">${studioStatusLabel(item.status)}</span>`;
      const cancel = ['queued','running'].includes(item.status)
        ? `<button class="ghost danger studio-cancel" data-request-id="${item.id}">취소</button>` : '';
      const queue = item.status === 'queued' ? ` · 앞 ${item.jobs_ahead || 0}개 조각` : '';
      const chapters = (item.chapters || []).length > 1
        ? `<details class="chapter-list"><summary>장문 HQ · ${item.chapters.length}개 장 보기</summary>${item.chapters.map(chapter => {
            const chapterAudio = chapter.status === 'succeeded'
              ? `<audio controls preload="none" src="${chapter.audio_url}"></audio><a class="chapter-download" href="${chapter.audio_url}" download="voice-${item.id}-chapter-${chapter.position}.wav">저장</a>`
              : `<span class="tag muted">${studioStatusLabel(chapter.status)} · ${chapter.completed_segments}/${chapter.segment_count}</span>`;
            return `<div class="chapter-item"><span><b>${chapter.position}장</b><small>${chapter.character_count.toLocaleString()}자</small></span><div>${chapterAudio}</div></div>`;
          }).join('')}</details>`
        : '';
      return `<div class="studio-result ${item.id === studio.processing_request_id ? 'is-processing' : ''}"><div class="studio-result-main"><span class="studio-result-index">#${item.id}</span><div class="studio-result-copy"><strong>${escapeHtml(item.original_text)}</strong><small><b class="source-badge">${source}</b> · 엔진 ${escapeHtml(engine)} · ${date} · ${seedLabel} · ${progress}${planSummary}${qualityLabel}${continuityLabel}${queue}${item.duration_seconds ? ` · ${Number(item.duration_seconds).toFixed(1)}초` : ''}</small>${chapters}</div></div><div class="studio-result-actions">${audio}${cancel}</div></div>`;
    }).join('');
    $$('.studio-cancel').forEach(button => button.onclick = async () => {
      const id = Number(button.dataset.requestId);
      if (!confirm(`요청 #${id}의 남은 생성 조각을 취소할까요?`)) return;
      await api(`/api/studio/${id}`, { method: 'DELETE' });
      flash('남은 생성 조각을 취소했습니다.');
      await loadStudio();
    });
  }
  clearInterval(app.studioPoller);
  if (studio.requests.some(item => ['queued','running'].includes(item.status))) {
    app.studioPoller = setInterval(() => loadStudio().catch(error => flash(error.message, true)), 1800);
  }
}

function renderHardware() {
  const gpu = app.state.system.gpu;
  $('#hardware-pill').textContent = gpu
    ? `${gpu.name} · ${gpu.memory_used_mb}/${gpu.memory_total_mb} MB · CC ${gpu.compute_capability}`
    : 'GPU 정보 없음';
}

function renderProjects() {
  const list = $('#project-list');
  if (!app.state.projects.length) { list.className = 'project-list empty-state'; list.textContent = '프로젝트가 없습니다.'; return; }
  list.className = 'project-list';
  list.innerHTML = app.state.projects.map(project => `<div class="project-item ${app.project?.id === project.id ? 'active' : ''}" data-project="${project.id}"><div><strong>${escapeHtml(project.name)}</strong><small>${escapeHtml(project.description || '설명 없음')}</small></div><span class="tag ${project.reference_path ? '' : 'muted'}">${project.reference_path ? '음성 등록' : '미등록'}</span></div>`).join('');
  $$('.project-item').forEach(node => node.onclick = () => selectProject(Number(node.dataset.project)));
}

async function selectProject(id) {
  app.project = await api(`/api/projects/${id}`);
  app.runs = await api(`/api/runs?project_id=${id}`);
  renderProjects(); renderProject(); renderRuns(); renderScores();
  maybePoll();
}

function renderProject() {
  const p = app.project;
  $('#reference-state').textContent = p.reference_path ? `등록 · ${p.reference_sha256.slice(0, 8)}` : '미등록';
  $('#reference-state').classList.toggle('muted', !p.reference_path);
  $('#reference-text').value = p.reference_text || '';
  $('#prompt-count').textContent = `${p.prompts.length} 문장`;
  $('#prompt-preview').innerHTML = p.prompts.length
    ? p.prompts.slice(0, 20).map(item => `<div class="prompt-chip"><span>${item.position.toString().padStart(2,'0')} · ${escapeHtml(item.category)}</span><p>${escapeHtml(item.normalized_text)}</p></div>`).join('')
    : '<div class="empty-state">기본 평가 대본을 추가하세요.</div>';
  $('#load-corpus-button').disabled = p.prompts.length >= 20;
  renderJobState();
}

function renderJobState() {
  const counts = Object.fromEntries((app.project?.jobs || []).map(row => [row.status, row.count]));
  const queued = counts.queued || 0, running = counts.running || 0, done = counts.succeeded || 0, failed = counts.failed || 0;
  const total = queued + running + done + failed;
  $('#job-state').textContent = total ? `완료 ${done} · 대기 ${queued + running} · 실패 ${failed}` : '대기';
  if (total) {
    $('#progress-wrap').classList.remove('hidden');
    $('#progress-bar').style.width = `${Math.round((done + failed) / total * 100)}%`;
    $('#progress-label').textContent = `${done + failed} / ${total} 작업 처리 · 실패 ${failed}`;
  } else $('#progress-wrap').classList.add('hidden');
}

function renderEngines() {
  const enabled = app.state.engines.filter(engine => engine.enabled);
  $('#engine-checkboxes').innerHTML = enabled.map(engine => `<label class="engine-option"><input type="checkbox" value="${engine.id}" ${engine.kind === 'mock' ? 'checked' : ''}><div><strong>${escapeHtml(engine.name)}</strong><small>${escapeHtml(engine.kind)} · ${escapeHtml(engine.version || 'version 미지정')}</small></div></label>`).join('');
  $('#engine-list').innerHTML = app.state.engines.map(engine => `<div class="engine-card"><strong>${escapeHtml(engine.name)}</strong><small>${escapeHtml(engine.slug)} · ${escapeHtml(engine.kind)} · ${escapeHtml(engine.version || 'version 미지정')}</small></div>`).join('');
}

function runPerformanceLabel(metrics) {
  const parts = [`전체 ${Number(metrics.wall_seconds || 0).toFixed(1)}s`];
  const measured = metrics.engine_performance;
  if (measured?.generate_seconds != null) parts.push(`합성 ${Number(measured.generate_seconds).toFixed(1)}s`);
  if (measured?.peak_allocated_mb != null) {
    parts.push(`모델 최대 할당 ${Number(measured.peak_allocated_mb).toFixed(0)}MB`);
  } else if (metrics.gpu?.peak_memory_used_mb != null) {
    parts.push(`GPU 전체 최대 ${Number(metrics.gpu.peak_memory_used_mb).toFixed(0)}MB`);
  }
  if (metrics.gpu?.peak_engine_delta_mb != null) parts.push(`작업 중 증가 ${Number(metrics.gpu.peak_engine_delta_mb).toFixed(0)}MB`);
  return parts.join(' · ');
}

function renderRuns() {
  const body = $('#runs-table');
  if (!app.runs.length) { body.innerHTML = '<tr><td colspan="8" class="empty-state">아직 결과가 없습니다.</td></tr>'; return; }
  body.innerHTML = app.runs.slice(0, 200).map(run => `<tr><td><code>${run.anonymous_code}</code></td><td>${run.position}. ${escapeHtml(run.original_text.slice(0, 55))}</td><td>${escapeHtml(run.engine_name)}</td><td>${run.seed}</td><td>${Number(run.duration_seconds || 0).toFixed(1)}s</td><td>${runPerformanceLabel(run.metrics)}</td><td><span class="tag ${run.metrics.hard_reject ? 'muted' : ''}">${run.metrics.hard_reject ? '검사 실패' : '통과'}</span></td><td><audio controls preload="none" src="/api/audio/${run.id}"></audio></td></tr>`).join('');
}

async function renderScores() {
  if (!app.project) return;
  const rows = await api(`/api/comparisons/summary?project_id=${app.project.id}`);
  $('#score-list').innerHTML = rows.length ? rows.map(row => `<div class="score-row"><div><strong>${escapeHtml(row.name)}</strong><small>${row.appearances}회 비교 참여</small></div><span class="tag">${row.wins}승</span></div>`).join('') : '평가가 아직 없습니다.';
}

function maybePoll() {
  clearInterval(app.poller);
  const active = (app.project.jobs || []).some(row => ['queued','running'].includes(row.status) && row.count > 0);
  if (active) app.poller = setInterval(() => refreshProject(false), 1500);
}

async function refreshProject(show = true) {
  if (!app.project) return;
  const id = app.project.id;
  app.project = await api(`/api/projects/${id}`);
  app.runs = await api(`/api/runs?project_id=${id}`);
  renderProject(); renderRuns(); maybePoll();
  if (show) flash('새로고침했습니다.');
}

async function loadComparison() {
  if (!app.project) { flash('먼저 프로젝트를 선택하세요.', true); return; }
  app.comparison = await api(`/api/comparisons/next?project_id=${app.project.id}`);
  if (!app.comparison) {
    $('#comparison-id').textContent = '비교 없음';
    $('#comparison-text').textContent = '동일 대본에서 생성된 결과가 두 개 이상 필요합니다.';
    $('#left-audio').removeAttribute('src'); $('#right-audio').removeAttribute('src');
    return;
  }
  $('#comparison-id').textContent = `PAIR ${app.comparison.id.toString().padStart(4,'0')}`;
  $('#comparison-text').textContent = app.comparison.prompt.normalized_text;
  $('#left-audio').src = app.comparison.left.audio_url;
  $('#right-audio').src = app.comparison.right.audio_url;
  $('#comparison-notes').value = '';
}

function switchView(name) {
  $$('.nav-item').forEach(node => node.classList.toggle('active', node.dataset.view === name));
  $$('.view').forEach(node => node.classList.toggle('active', node.id === `${name}-view`));
  $('#page-title').textContent = ({studio:'내 목소리 TTS',workspace:'벤치마크',review:'블라인드 비교',engines:'엔진 설정'})[name];
  if (name === 'review') { loadComparison(); renderScores(); }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
}

$$('.nav-item').forEach(node => node.onclick = () => switchView(node.dataset.view));
let studioPreviewTimer;
let studioPreviewController;
let studioPreviewVersion = 0;
$('#studio-engine').onchange = renderStudioEngineState;
$('#studio-text').oninput = event => {
  const text = event.target.value;
  const length = text.length;
  $('#studio-char-count').textContent = `${length.toLocaleString()}자`;
  clearTimeout(studioPreviewTimer);
  studioPreviewController?.abort();
  const version = ++studioPreviewVersion;
  $('#studio-plan-preview').classList.add('hidden');
  $('#studio-plan-segments').replaceChildren();
  $('#studio-segment-estimate').textContent = text.trim() ? '생성 구간 확인 중…' : '0구간';
  if (!text.trim()) return;
  studioPreviewTimer = setTimeout(async () => {
    studioPreviewController = new AbortController();
    try {
      const plan = await api('/api/studio/preview', {...jsonOptions({text}), signal: studioPreviewController.signal});
      if (version !== studioPreviewVersion) return;
      $('#studio-segment-estimate').textContent = plan.segment_count === 1
        ? '한 번에 생성 · 구간 연결 없음'
        : `${plan.segment_count}구간 생성 · ${plan.join_count}곳 연결`;
      const items = plan.segments.map(segment => {
        const item = document.createElement('li');
        item.textContent = segment.text;
        return item;
      });
      $('#studio-plan-segments').replaceChildren(...items);
      $('#studio-plan-preview').classList.remove('hidden');
    } catch (error) {
      if (error.name !== 'AbortError' && version === studioPreviewVersion) {
        $('#studio-segment-estimate').textContent = '생성 구간 확인 불가';
      }
    }
  }, 300);
};
$('#studio-form').onsubmit = async event => {
  event.preventDefault();
  const text = $('#studio-text').value.trim();
  if (!text) return flash('합성할 대본을 입력하세요.', true);
  $('#studio-generate').disabled = true;
  $('#studio-generate').textContent = '요청 중…';
  try {
    await api('/api/studio/synthesize', jsonOptions({
      text,
      seed:Number($('#studio-seed').value || 13),
      engine_slug:$('#studio-engine').value || app.studio?.default_engine_slug,
      quality_mode:'long_form_hq',
    }));
    $('#studio-text').value = '';
    $('#studio-text').dispatchEvent(new Event('input'));
    await loadStudio();
    flash('음성 생성을 시작했습니다.');
  } catch (error) { flash(error.message, true); }
  finally {
    $('#studio-generate').textContent = '음성 만들기';
    $('#studio-generate').disabled = !app.studio?.available;
  }
};
$('#studio-refresh').onclick = () => loadStudio().then(() => flash('생성 이력을 새로고침했습니다.')).catch(error => flash(error.message,true));
$('#new-project-button').onclick = () => $('#project-dialog').showModal();
$('#project-form').onsubmit = async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  try {
    const project = await api('/api/projects', jsonOptions({name: $('#project-name').value, description: $('#project-description').value}));
    $('#project-dialog').close(); $('#project-form').reset(); await loadState(); await selectProject(project.id); flash('프로젝트를 만들었습니다.');
  } catch (error) { flash(error.message, true); }
};
$('#reference-file').onchange = async event => {
  const file = event.target.files[0];
  if (!file || !app.project) return flash('프로젝트를 먼저 선택하세요.', true);
  try {
    app.project = await api(`/api/projects/${app.project.id}/reference`, {method:'POST', headers:{'Content-Type':file.type || 'application/octet-stream','X-Filename':encodeURIComponent(file.name),'X-Reference-Text':encodeURIComponent($('#reference-text').value)}, body:file});
    renderProject(); renderProjects(); flash('레퍼런스 음성을 저장했습니다.');
  } catch (error) { flash(error.message, true); }
};
$('#save-reference-text').onclick = async () => {
  if (!app.project) return flash('프로젝트를 먼저 선택하세요.', true);
  try {
    app.project = await api(`/api/projects/${app.project.id}/reference-text`, jsonOptions({reference_text:$('#reference-text').value}));
    renderProject(); flash('레퍼런스 대본을 저장했습니다.');
  } catch (error) { flash(error.message, true); }
};
$('#load-corpus-button').onclick = async () => {
  if (!app.project) return flash('프로젝트를 먼저 선택하세요.', true);
  try { app.project = await api(`/api/projects/${app.project.id}/corpus/default`, {method:'POST',headers:{'Content-Length':'0'}}); renderProject(); flash('한국어 평가 대본을 추가했습니다.'); } catch(error) { flash(error.message,true); }
};
$('#prompt-form').onsubmit = async event => {
  event.preventDefault(); if (!app.project) return flash('프로젝트를 먼저 선택하세요.', true);
  try { await api(`/api/projects/${app.project.id}/prompts`, jsonOptions({text:$('#prompt-text').value})); $('#prompt-text').value=''; await refreshProject(false); flash('대본을 추가했습니다.'); } catch(error) { flash(error.message,true); }
};
$('#start-button').onclick = async () => {
  if (!app.project) return flash('프로젝트를 먼저 선택하세요.', true);
  const engineIds = $$('#engine-checkboxes input:checked').map(node => Number(node.value));
  const seeds = $('#seeds-input').value.split(',').map(value => Number(value.trim())).filter(Number.isFinite);
  try { const result = await api('/api/benchmarks', jsonOptions({project_id:app.project.id,engine_ids:engineIds,seeds})); await refreshProject(false); flash(`${result.queued}개 작업을 큐에 추가했습니다.`); } catch(error) { flash(error.message,true); }
};
$('#refresh-button').onclick = () => refreshProject();
$$('.choice').forEach(node => node.onclick = async () => {
  if (!app.comparison) return;
  try { await api(`/api/comparisons/${app.comparison.id}`, jsonOptions({winner:node.dataset.winner,notes:$('#comparison-notes').value})); await loadComparison(); await renderScores(); } catch(error) { flash(error.message,true); }
});
$('#engine-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const command = JSON.parse($('#engine-command').value);
    await api('/api/engines', jsonOptions({slug:$('#engine-slug').value,name:$('#engine-name').value,kind:'command',version:$('#engine-version').value,config:{command,timeout_seconds:7200}}));
    $('#engine-form').reset(); await loadState(); flash('엔진 어댑터를 저장했습니다.');
  } catch(error) { flash(error.message,true); }
};

loadState().catch(error => flash(error.message, true));
