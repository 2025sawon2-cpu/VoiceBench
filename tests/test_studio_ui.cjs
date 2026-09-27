// Functional tests for the shipped input handler, with delayed HTTP responses.
// No browser, production server, GPU, or third-party packages are required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture() {
  const staticRoot = path.join(__dirname, '..', 'voicebench', 'static');
  const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8');
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]));
  const nodes = new Map();
  const timers = new Map();
  const requests = [];
  let nextTimer = 0;
  function element() {
    const classes = new Set();
    return {value: '', textContent: '', children: [],
      classList: {
        add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
        toggle(x, force) {
          const enabled = force === undefined ? !classes.has(x) : force;
          if (enabled) classes.add(x); else classes.delete(x);
          return enabled;
        },
      },
      dispatchEvent(event) { this["on" + event.type]?.({target: this}); },
      replaceChildren(...items) { this.children = items; },
    };
  }
  function node(selector) {
    assert.ok(selector.startsWith('#') && ids.has(selector.slice(1)), `Missing HTML element: ${selector}`);
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  }
  const context = vm.createContext({
    document: {querySelector: node, querySelectorAll: () => [], createElement: element},
    AbortController, AbortSignal, Event,
    setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => {
      requests.push({url, options, reject,
        reply: body => resolve({ok: true, headers: {get: () => 'application/json'}, json: async () => body}),
      });
    }),
  });
  const source = fs.readFileSync(path.join(staticRoot, 'app.js'), 'utf8');
  const startup = source.trim().split('\n').at(-1);
  assert.equal(source.split(startup).length, 2);
  vm.runInContext(source.replace(startup, ''), context);
  function input(text) { node('#studio-text').value = text; node('#studio-text').oninput({target: node('#studio-text')}); }
  function fireTimer() {
    assert.equal(timers.size, 1);
    const [id, fn] = [...timers.entries()][0];
    timers.delete(id);
    return fn();
  }
  return {context, node, input, fireTimer, requests, timers};
}

test('a multi-sentence single-generation preview shows no join and escaped text', async () => {
  const f = fixture();
  const text = '문장 하나입니다. <b>문장 둘입니다.</b>';
  f.input(text);
  const pending = f.fireTimer();
  assert.equal(f.requests[0].url, '/api/studio/preview');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {text});
  f.requests[0].reply({segment_count: 1, join_count: 0, segments: [{text}]});
  await pending;
  assert.equal(f.node('#studio-segment-estimate').textContent, '한 번에 생성 · 구간 연결 없음');
  assert.equal(f.node('#studio-plan-segments').children[0].textContent, text);
  assert.equal(f.node('#studio-plan-segments').children[0].innerHTML, undefined);
  assert.equal(f.node('#studio-plan-preview').classList.contains('hidden'), false);
});

test('an old response cannot overwrite the newest plan, even if abort is ignored', async () => {
  const f = fixture();
  f.input('old');
  const oldPending = f.fireTimer();
  f.input('new');
  assert.equal(f.requests[0].options.signal.aborted, true);
  const newPending = f.fireTimer();
  f.requests[1].reply({segment_count: 2, join_count: 1, segments: [{text: 'new one'}, {text: 'new two'}]});
  await newPending;
  f.requests[0].reply({segment_count: 1, join_count: 0, segments: [{text: 'old'}]});
  await oldPending;
  assert.equal(f.node('#studio-segment-estimate').textContent, '2구간 생성 · 1곳 연결');
  assert.equal(f.node('#studio-plan-segments').children[0].textContent, 'new one');
});

test('clearing input discards a late response; errors do not invent a segment count', async () => {
  const f = fixture();
  f.input('old');
  const oldPending = f.fireTimer();
  f.input('');
  f.requests[0].reply({segment_count: 1, join_count: 0, segments: [{text: 'old'}]});
  await oldPending;
  assert.equal(f.node('#studio-segment-estimate').textContent, '0구간');
  assert.equal(f.node('#studio-plan-preview').classList.contains('hidden'), true);
  assert.equal(f.timers.size, 0);
  f.input('new');
  const pending = f.fireTimer();
  f.requests[1].reject(new Error('offline'));
  await pending;
  assert.equal(f.node('#studio-segment-estimate').textContent, '생성 구간 확인 불가');
});

test('engine banner describes the selected comparison preset', () => {
  const f = fixture();
  vm.runInContext("app.studio = {available:true,studio_engines:[{slug:'default',name:'기본'},{slug:'comparison',name:'비교'}]}", f.context);
  f.node('#studio-engine').value = 'comparison';
  f.node('#studio-engine').onchange();
  assert.equal(f.node('#studio-engine-state').textContent, '비교 · 파형 검사 · 발음 자동 재시도 없음');
  assert.equal(f.node('#studio-generate').disabled, false);
});

test('switching between Qwen and Fish preserves model-specific quality guidance', () => {
  const f = fixture();
  vm.runInContext("app.studio = {available:true,studio_engines:[{slug:'qwen3-tts-17b-pascal-expressive',name:'Qwen'},{slug:'fish-s2-pro-experimental',name:'Fish'}]}", f.context);
  f.node('#studio-engine').value = 'qwen3-tts-17b-pascal-expressive';
  f.node('#studio-engine').onchange();
  assert.equal(f.node('#studio-engine-state').textContent, 'Qwen · 자동 발음·파형 검사');
  f.node('#studio-engine').value = 'fish-s2-pro-experimental';
  f.node('#studio-engine').onchange();
  assert.equal(f.node('#studio-engine-state').textContent, 'Fish · 파형 검사 · 발음 자동 재시도 없음');
  f.node('#studio-engine').value = 'missing';
  f.node('#studio-engine').onchange();
  assert.equal(f.node('#studio-generate').disabled, true);
});

test('changing the model refreshes boundaries and ignores the old preview', async () => {
  const f = fixture();
  vm.runInContext("app.studio = {available:true,studio_engines:[{slug:'zonos2-q8-experimental',name:'ZONOS2',voice_name:'Personal',quality_control_supported:true},{slug:'qwen3-tts-17b-pascal-expressive',name:'Qwen'}]}", f.context);
  f.node('#studio-engine').value = 'qwen3-tts-17b-pascal-expressive';
  f.input('테스트 대본');
  const old = f.fireTimer();
  f.node('#studio-engine').value = 'zonos2-q8-experimental';
  f.node('#studio-engine').onchange();
  assert.equal(f.node('#studio-voice-name').textContent, 'Personal');
  assert.equal(f.node('#studio-engine-state').textContent, 'ZONOS2 · 자동 발음·파형 검사');
  const latest = f.fireTimer();
  assert.equal(JSON.parse(f.requests[1].options.body).engine_slug, 'zonos2-q8-experimental');
  f.requests[1].reply({segment_count:1,join_count:0,segments:[{text:'테스트 대본'}]});
  await latest;
  f.requests[0].reply({segment_count:2,join_count:1,segments:[{text:'테스트'},{text:'대본'}]});
  await old;
  assert.equal(f.node('#studio-segment-estimate').textContent, '한 번에 생성 · 구간 연결 없음');
});

test('submission sends the selected voice and quality without deleting the draft; duplicate click is ignored', async () => {
  const f = fixture();
  vm.runInContext(`app.studio = {available:true,studio_engines:[{slug:'zonos2-q8-experimental',name:'ZONOS2',available:true}],voices:[{id:8,name:'New voice',available:true}]}; loadStudio = async () => {};`, f.context);
  f.node('#studio-text').value = 'Keep this draft.';
  f.node('#studio-engine').value = 'zonos2-q8-experimental';
  f.node('#studio-voice').value = '8';
  f.node('#studio-quality').value = 'standard';
  f.node('#studio-seed').value = '42';
  const pending = f.node('#studio-form').onsubmit({preventDefault(){}});
  await f.node('#studio-form').onsubmit({preventDefault(){}});
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].url, '/api/studio/synthesize');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {text:'Keep this draft.', seed:42, engine_slug:'zonos2-q8-experimental',quality_mode:'standard',voice_project_id:8});
  f.requests[0].reply({id:1}); await pending;
  assert.equal(f.node('#studio-text').value, 'Keep this draft.');
  assert.equal(f.node('#studio-generate').disabled, false);
});

test('default voice sends no override and uses HQ by default', async () => {
  const f = fixture();
  vm.runInContext(`app.studio = {available:true,studio_engines:[{slug:'zonos2-q8-experimental',name:'ZONOS2',available:true}],voices:[]}; loadStudio = async () => {};`, f.context);
  f.node('#studio-text').value = 'Default voice.';
  f.node('#studio-engine').value = 'zonos2-q8-experimental';
  const pending = f.node('#studio-form').onsubmit({preventDefault(){}});
  const payload = JSON.parse(f.requests[0].options.body);
  assert.equal(payload.voice_project_id, undefined);
  assert.equal(payload.seed, 13);
  assert.equal(payload.quality_mode, 'long_form_hq');
  f.requests[0].reply({id:1}); await pending;
});
