const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function fixture(){
 const nodes=new Map(),calls=[],inputs=[];
 function $(id){if(!nodes.has(id))nodes.set(id,{value:'',dataset:{},classList:{remove(){},add(){},toggle(){}},setAttribute(){},querySelectorAll(){return[]},dispatchEvent(){},paused:true,pause(){this.paused=true},async play(){this.paused=false;calls.push(this.src)}});return nodes.get(id)}
 const voices=[{id:'qwen-sohee',name:'소희 · 따뜻한 목소리',model:'Qwen',gender:'female',native_language:'한국어',kind:'official',description:'따뜻한 여성',available:true,engine_slug:'custom',preview_kind:'generated',preview_url:'/api/voice-catalog/qwen-sohee/preview'}, {id:'zonos-americanmale',name:'아메리칸 · 남성',model:'ZONOS2',gender:'male',native_language:'영어',kind:'official',description:'남성',available:true,engine_slug:'zonos',preview_kind:'reference',preview_format:'.mp3',preview_url:'/api/voice-catalog/zonos-americanmale/preview'}];
 const app={studio:{voice_catalog:voices,studio_engines:[{slug:'custom',instruction_supported:true},{slug:'zonos'}]}};
 const context=vm.createContext({app,$,$$:()=>inputs,Event,localStorage:{getItem(){return null},setItem(){}},escapeHtml:s=>s,flash(){},renderStudioEngineState(){},fetch(){throw Error('Preview must not create HTTP synthesis requests')}});
 vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../voicebench/static/gallery.js'),'utf8'),context);
 return {context,app,$,calls,inputs,voices};
}
test('choosing a voice immediately plays its cached sample, without generation or a script',async()=>{
 const f=fixture();vm.runInContext("chooseGalleryVoice('qwen-sohee')",f.context);
 assert.equal(f.app.voiceId,'qwen-sohee');assert.equal(f.$('#studio-engine').value,'custom');
 assert.deepEqual(f.calls,['/api/voice-catalog/qwen-sohee/preview']);
 assert.equal(f.$('#studio-text').value,'');
 await vm.runInContext("playVoicePreview('qwen-sohee')",f.context);
 assert.equal(f.$('#voice-audition').paused,true);assert.equal(f.calls.length,1);
 await vm.runInContext("playVoicePreview('zonos-americanmale')",f.context);
 assert.equal(f.$('#preview-download').download,'zonos-americanmale.mp3');
 assert.match(f.$('#preview-caption').textContent,/원본 녹음/);
});
test('gender, descriptive search, model and favorites compose',()=>{
 const f=fixture();
 assert.equal(vm.runInContext("filteredVoices(app.studio.voice_catalog,'따뜻','female','Qwen',false,new Set()).length",f.context),1);
 assert.equal(vm.runInContext("filteredVoices(app.studio.voice_catalog,'','male','Qwen',false,new Set()).length",f.context),0);
 assert.equal(vm.runInContext("filteredVoices(app.studio.voice_catalog,'','','',true,new Set(['zonos-americanmale']))[0].id",f.context),'zonos-americanmale');
});
test('advanced controls persist in requests even on a legacy preset without a catalog voice',()=>{
 const f=fixture();f.app.voiceId=null;f.$('#studio-engine').value='custom';f.$('#voice-instruction').value=' Calm. ';
 f.inputs.push({dataset:{generation:'temperature'},value:'0.85'});
 const options=vm.runInContext('galleryRequestOptions()',f.context);
 assert.equal(options.voice_id,undefined);assert.equal(options.generation.temperature,.85);assert.equal(options.generation.instruct,'Calm.');
 f.app.voiceId='qwen-sohee';assert.equal(vm.runInContext('galleryRequestOptions().voice_id',f.context),'qwen-sohee');
});
test('history restores a legacy library voice and request-specific settings',()=>{
 const f=fixture();f.voices.push({id:'library-9',kind:'library',voice_project_id:9,model:'ZONOS2',engine_slug:'zonos',name:'Mine',description:'Mine',native_language:'한국어',gender:'personal'});
 f.$('#studio-engine').value='zonos';f.inputs.push({dataset:{generation:'temperature'},value:'.7'});
 vm.runInContext("restoreGalleryRequest({project_id:9,engine_slug:'zonos',generation:{temperature:.95}})",f.context);
 assert.equal(f.app.voiceId,'library-9');assert.equal(f.inputs[0].value,.95);
});
