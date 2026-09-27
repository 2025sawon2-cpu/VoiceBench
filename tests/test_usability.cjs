const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
function fixture(){
 const nodes=new Map(),storage=new Map();
 function $(id){if(!nodes.has(id)){const classes=new Set();nodes.set(id,{value:'',textContent:'',dataset:{},content:id.startsWith('meta')?'old-version':'',classList:{toggle(c,on){on?classes.add(c):classes.delete(c)},contains(c){return classes.has(c)},add(c){classes.add(c)},remove(c){classes.delete(c)}},addEventListener(){},setAttribute(){},dispatchEvent(){},focus(){},append(){},close(){this.open=false},showModal(){this.open=true}});}return nodes.get(id);}
 const context=vm.createContext({document:{querySelector:$,querySelectorAll:()=>[],body:{append(){}}},window:{addEventListener(){}},Event,AbortController,AbortSignal,setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},localStorage:{setItem(){}},saveDraft(){},draftKey:'draft',galleryRequestOptions:()=>({generation:{}}),renderVoiceGallery(){},renderVoiceControls(){},gallery:{},location:{reload(){}}});
 let source=fs.readFileSync(path.join(__dirname,'../voicebench/static/app.js'),'utf8').trim().split('\n');source.pop();vm.runInContext(source.join('\n'),context);
 vm.runInContext("app.studio={studio_engines:[{slug:'zonos',name:'ZONOS2',available:true}],voice_catalog:[{id:'mine',name:'My voice',available:true}],voices:[],ui_version:'old-version'}; app.voiceId='mine';",context);
 $('#studio-engine').value='zonos';$('#studio-quality').value='long_form_hq';$('#studio-text').value='Keep my draft';
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../voicebench/static/usability.js'),'utf8'),context);
 return {context,$,storage};
}
test('offline state persists, disables synthesis and preserves the draft; reconnect reverses it',()=>{
 const f=fixture();vm.runInContext('updateConnection(false)',f.context);
 assert.equal(f.$('#studio-generate').disabled,true);assert.equal(f.$('#connection-banner').classList.contains('hidden'),false);
 assert.equal(f.$('#studio-text').value,'Keep my draft');
 vm.runInContext('updateConnection(true)',f.context);
 assert.equal(f.$('#studio-generate').disabled,false);assert.equal(f.$('#connection-banner').classList.contains('hidden'),true);
});
test('changed UI version shows an update, not a forced reload or discarded draft',()=>{
 const f=fixture();vm.runInContext("app.studio.ui_version='new-version';updateConnection(true)",f.context);
 assert.equal(f.$('#update-banner').classList.contains('hidden'),false);assert.equal(f.$('#studio-text').value,'Keep my draft');
});
test('offline form submission cannot enqueue a TTS job',async()=>{
 const f=fixture();vm.runInContext('updateConnection(false)',f.context);
 await f.$('#studio-form').onsubmit({preventDefault(){}});
 assert.equal(vm.runInContext('Boolean(app.submitting)',f.context),false);
});
