const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function fixture(){
 const nodes=new Map(),requests=[];
 const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',disabled:false,files:[],classList:{add(){},remove(){},toggle(){}},pause(){},removeAttribute(){}});return nodes.get(id);};
 const context=vm.createContext({$,Blob,DataView,ArrayBuffer,Number,Math,AbortSignal,URL:{revokeObjectURL(){}},confirm:()=>true,flash(){},loadStudio(){},api:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../voicebench/static/recording.js'),'utf8'),context);
 vm.runInContext('recording.blob=new Blob(["clip"]); recordingButtons();',context);
 return {context,$,requests};
}
test('converted WAV is mono PCM16 with a valid RIFF header and bounded samples',async()=>{
 const f=fixture(),blob=vm.runInContext('pcmWav([-2,-.5,0,.5,2])',f.context),view=new DataView(await blob.arrayBuffer());
 assert.equal(blob.type,'audio/wav');assert.equal(view.getUint16(22,true),1);assert.equal(view.getUint32(24,true),24000);
 assert.equal(view.getUint32(4,true)+8,blob.size);assert.equal(view.getUint32(40,true),10);
 assert.deepEqual([0,1,2,3,4].map(i=>view.getInt16(44+i*2,true)),[-32768,-16384,0,16384,32767]);
});
test('changing a trim disables registration until applied and ignores old transcription',async()=>{
 const f=fixture();const pending=f.$('#voice-transcribe').onclick();
 assert.equal(f.requests[0].url,'/api/voices/transcribe');assert.equal(f.$('#voice-submit').disabled,true);
 f.$('#clip-start').oninput();f.requests[0].resolve({transcript:'old clip'});await pending;
 assert.equal(f.$('#voice-transcript').value,'');assert.equal(f.$('#voice-submit').disabled,true);assert.equal(f.$('#voice-transcribe').disabled,true);
});
test('transcription never overwrites typing that began after the request',async()=>{
 const f=fixture();const pending=f.$('#voice-transcribe').onclick();
 f.$('#voice-transcript').value='I edited this';f.requests[0].resolve({transcript:'recognized'});await pending;
 assert.equal(f.$('#voice-transcript').value,'I edited this');assert.equal(f.$('#voice-submit').disabled,false);
});
test('ASR failure permits manual registration and does not submit another request',async()=>{
 const f=fixture();const pending=f.$('#voice-transcribe').onclick();f.requests[0].reject(Error('offline'));await pending;
 assert.match(f.$('#clip-state').textContent,/직접 입력/);assert.equal(f.$('#voice-submit').disabled,false);assert.equal(f.requests.length,1);
});
