/* Decode and trim locally. Only the chosen PCM clip is uploaded. */
const recording = {buffer:null, blob:null, url:null, revision:0, processing:false, transcribing:false, dirty:false};
function clipMessage(text, error=false) {
  $('#clip-state').textContent=text; $('#clip-state').classList.toggle('error',error);
}
function recordingButtons() {
  $('#voice-transcribe').disabled = !recording.blob || recording.dirty || recording.processing || recording.transcribing;
  $('#voice-submit').disabled = !recording.blob || recording.dirty || recording.processing || recording.transcribing;
  $('#clip-apply').disabled = !recording.buffer || recording.processing;
}
function pcmWav(samples, sampleRate=24000) {
  const bytes=new ArrayBuffer(44+samples.length*2), view=new DataView(bytes);
  const text=(offset,value)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));};
  text(0,'RIFF');view.setUint32(4,bytes.byteLength-8,true);text(8,'WAVE');text(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
  text(36,'data');view.setUint32(40,samples.length*2,true);
  for(let i=0;i<samples.length;i++){const s=Math.max(-1,Math.min(1,samples[i]));view.setInt16(44+i*2,Math.round(s*(s<0?32768:32767)),true);}
  return new Blob([bytes],{type:'audio/wav'});
}
function clearClip() {
  $('#clip-audio').pause();$('#clip-audio').removeAttribute('src');
  if(recording.url)URL.revokeObjectURL(recording.url);
  recording.url=null;recording.blob=null;
}
async function applyClip(autoplay=false) {
  if(!recording.buffer || recording.processing)return;
  const start=Number($('#clip-start').value),end=Number($('#clip-end').value);
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>recording.buffer.duration+0.001||end-start<3||end-start>30){
    recording.dirty=true;recordingButtons();return clipMessage('녹음 안에서 3~30초 길이의 구간을 선택해 주세요.',true);
  }
  const version=++recording.revision;
  recording.processing=true;recordingButtons();
  try {
    const context=new OfflineAudioContext(1,Math.round((end-start)*24000),24000);
    const source=context.createBufferSource();source.buffer=recording.buffer;source.connect(context.destination);source.start(0,start,end-start);
    const output=await context.startRendering();
    if(version!==recording.revision)return;
    clearClip();recording.blob=pcmWav(output.getChannelData(0));recording.url=URL.createObjectURL(recording.blob);
    $('#clip-audio').src=recording.url;recording.dirty=false;
    clipMessage(`${start.toFixed(1)}~${end.toFixed(1)}초 · ${(end-start).toFixed(1)}초 구간 적용됨 · WAV 자동 변환 완료. 녹음 대본도 이 구간과 맞는지 확인해 주세요.`);
    if(autoplay)await $('#clip-audio').play().catch(()=>{});
  } catch {clipMessage('구간을 변환하지 못했습니다. 다른 녹음 파일을 선택해 주세요.',true);}
  finally {if(version===recording.revision){recording.processing=false;recordingButtons();}}
}
$('#voice-file').onchange=async event=>{
  const file=event.target.files[0],version=++recording.revision;
  if(recording.buffer || recording.blob) $('#voice-transcript').value='';
  clearClip();recording.buffer=null;recording.processing=false;recording.dirty=false;
  $('#clip-editor').classList.add('hidden');recordingButtons();
  if(!file)return clipMessage('녹음 파일을 선택해 주세요.');
  if(file.size>30*1024*1024)return clipMessage('30MB 이하의 녹음을 선택해 주세요.',true);
  recording.processing=true;recordingButtons();clipMessage('녹음을 읽고 있습니다…');
  let context;
  try {
    context=new AudioContext();
    const decoded=await context.decodeAudioData(await file.arrayBuffer());
    if(version!==recording.revision)return;
    if(decoded.duration<3||decoded.duration>300)throw Error('3초~5분 길이의 녹음을 선택해 주세요.');
    recording.buffer=decoded;$('#clip-start').value=0;
    $('#clip-end').value=Math.floor(Math.min(decoded.duration,15)*10)/10;
    $('#clip-start').max=decoded.duration;$('#clip-end').max=decoded.duration;
    $('#clip-editor').classList.remove('hidden');recording.processing=false;
    await applyClip();
  } catch(error){if(version===recording.revision)clipMessage(error.message.includes('선택')?error.message:'이 브라우저에서 읽을 수 없는 녹음입니다. WAV 또는 MP3로 다시 선택해 주세요.',true);}
  finally{if(context)await context.close();if(version===recording.revision){recording.processing=false;recordingButtons();}}
};
for(const id of ['#clip-start','#clip-end'])$(id).oninput=()=>{
  recording.revision++;recording.dirty=true;recording.processing=false;$('#clip-audio').pause();recordingButtons();
  clipMessage('구간을 바꿨습니다. ‘구간 적용 · 미리듣기’를 눌러 확정해 주세요.');
};
$('#clip-apply').onclick=()=>applyClip(true);
$('#voice-transcribe').onclick=async()=>{
  if(recording.transcribing||recording.processing||recording.dirty||!recording.blob)return;
  if($('#voice-transcript').value.trim()&&!confirm('작성한 녹음 대본을 자동 인식 결과로 바꿀까요?'))return;
  const version=recording.revision,original=$('#voice-transcript').value;
  recording.transcribing=true;recordingButtons();$('#voice-transcribe').textContent='한국어 인식 중…';clipMessage('선택한 구간의 대본을 로컬에서 인식합니다. 직접 입력할 수도 있어요.');
  try {
    const result=await api('/api/voices/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:recording.blob,signal:AbortSignal.timeout(120000)});
    if(version!==recording.revision)return;
    if($('#voice-transcript').value!==original)return clipMessage('인식 중 수정한 대본을 유지했습니다. 필요하면 다시 인식해 주세요.');
    $('#voice-transcript').value=result.transcript;clipMessage('자동 인식 완료. 틀린 단어나 문장 끝을 확인한 뒤 등록해 주세요.');
  } catch(error){if(version===recording.revision)clipMessage(`자동 인식 실패: ${error.name==='TimeoutError'?'시간이 초과됐습니다.':error.message} 대본을 직접 입력해 등록할 수 있습니다.`,true);}
  finally{recording.transcribing=false;$('#voice-transcribe').textContent='녹음 대본 자동 인식';recordingButtons();}
};
$('#voice-form').onsubmit=async event=>{
  event.preventDefault();
  if(!$('#voice-consent').checked||!recording.blob||recording.dirty||recording.processing||recording.transcribing)return;
  recording.processing=true;recordingButtons();$('#voice-submit').textContent='등록 중…';
  $('#voice-file').disabled=true;$('#clip-start').disabled=true;$('#clip-end').disabled=true;
  try {
    await api('/api/voices',{method:'POST',headers:{'Content-Type':'audio/wav','X-Voice-Name':encodeURIComponent($('#voice-name').value),'X-Reference-Text':encodeURIComponent($('#voice-transcript').value)},body:recording.blob});
    $('#voice-form').reset();clearClip();recording.buffer=null;recording.revision++;
    $('#clip-editor').classList.add('hidden');clipMessage('목소리를 등록했습니다. 오른쪽 보관함에서 선택해 사용할 수 있어요.');
    await loadStudio();flash('목소리를 등록했습니다.');
  } catch(error){clipMessage(error.message,true);}
  finally{recording.processing=false;$('#voice-submit').textContent='목소리 등록';$('#voice-file').disabled=false;$('#clip-start').disabled=false;$('#clip-end').disabled=false;recordingButtons();}
};
recordingButtons();
