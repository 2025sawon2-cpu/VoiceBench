const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(blocked=false) {
 const nodes=new Map(),audio=[],requests=[];
 function element(tag='div') {return {tag,textContent:'',children:[],dataset:{},listeners:{},append(...items){this.children.push(...items)},addEventListener(name,fn){this.listeners[name]=fn},pause(){this.paused=true},async play(){if(blocked)throw Error('NotAllowedError');this.paused=false;this.listeners.play?.()},paused:true};}
 const document={querySelector(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},createElement(tag){const e=element(tag);if(tag==='audio')audio.push(e);return e}};
 const voices=['qwen-sohee','shared-fish-aiden'].map(id=>({id,model:'Qwen',name:id,kind:'official',preview_kind:'generated',preview_url:'/api/voice-catalog/'+id+'/preview'}));
 const context=vm.createContext({document,Map,encodeURIComponent,fetch:async url=>{requests.push(url);return{ok:true,json:async()=>url==='/api/setup'?{status:'incomplete',steps:[{name:'zonos2',status:'failed'}],samples:[]}:{voice_catalog:voices}}}});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../voicebench/static/welcome.js'),'utf8'),context);
 return {context,nodes,audio,requests};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('welcome uses saved audio only and autoplay denial exposes one-click playback',async()=>{
 const f=fixture(true);await tick();
 assert.deepEqual(f.requests,['/api/setup','/api/studio']);
 assert.equal(f.audio.length,2);
 assert.match(f.nodes.get('#playback-note').textContent,/자동 재생을 막았습니다/);
 assert.equal(f.nodes.get('#play-all').disabled,false);
 assert.equal(f.nodes.get('#retry').hidden,false);
});
test('welcome sequential playback advances and stop pauses every player',async()=>{
 const f=fixture();await tick();
 assert.equal(f.audio[0].paused,false);assert.equal(f.audio[1].paused,true);
 f.audio[0].listeners.ended();await tick();
 assert.equal(f.audio[0].paused,true);assert.equal(f.audio[1].paused,false);
 f.nodes.get('#play-all').listeners.click();
 assert.ok(f.audio.every(p=>p.paused));
});
