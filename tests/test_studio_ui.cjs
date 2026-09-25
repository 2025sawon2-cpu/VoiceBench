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
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
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
    AbortController,
    setTimeout: fn => { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => {
      requests.push({url, options, reject,
        reply: body => resolve({ok: true, headers: {get: () => 'application/json'}, json: async () => body}),
      });
    }),
  });
  const source = fs.readFileSync(path.join(staticRoot, 'app.js'), 'utf8');
  const startup = 'loadState().catch(error => flash(error.message, true));';
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
  assert.equal(f.node('#studio-engine-state').textContent, '비교 · 자동 내용 검사');
});
