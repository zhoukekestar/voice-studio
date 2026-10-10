'use strict';
const $ = (id) => document.getElementById(id);
const sample = '小熊有一盏小小的灯。\n\n每当夜晚来临，它就提着灯，走过安静的森林。\n\n“小兔子，你睡了吗？”小熊轻轻地问。\n\n“还没有，我在等星星。”小兔子说。\n\n于是，它们坐在一起，数着天上的星星。一颗，两颗，三颗……\n\n小熊忽然发现，最温暖的光，就在朋友的身旁。';
let models = [], selectedModel = 'voxcpm2', reference = null, activeJob = null, polling = false;
let recorder = null, recordingStream = null, recordingStart = 0, timer = null, animation = null, audioContext = null;
let busy = false, historyItems = [], draftTimer = null;
let serviceOnline = false, healthChecking = false, pendingUpload = null;
const offlineMessage = '本地服务未连接。请双击“启动声音工坊.command”，页面会自动重新连接。';

function setServiceOnline(online) {
  serviceOnline=online;
  $('service-label').textContent=online?'本地服务已连接 · 免费生成':'本地服务未连接';
  $('service-badge').classList.toggle('offline',!online);
  updateReady();
}

async function api(url, options = {}) {
  let response;
  try { response = await fetch(url, {...options, signal:AbortSignal.timeout(60000)}); }
  catch { setServiceOnline(false); throw new Error(offlineMessage); }
  setServiceOnline(true);
  let data;
  try { data = await response.json(); } catch { throw new Error('本地服务没有返回有效数据，请检查运行终端。'); }
  if (!response.ok) {
    const message = Array.isArray(data.detail) ? data.detail.map(x => `${x.loc.at(-1)}: ${x.msg}`).join('；') : data.detail;
    const error = new Error(message || `操作失败（${response.status}）`);
    error.status=response.status;throw error;
  }
  return data;
}
const jsonPost = (data) => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
function notice(message) { $('notice-text').textContent = message; $('notice').hidden = false; }
$('dismiss-notice').onclick = () => { $('notice').hidden = true; };
const escapeHTML = (text) => String(text ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
function duration(seconds) { const n = Math.max(0, Math.round(seconds || 0)); return `${String(Math.floor(n / 60)).padStart(2,'0')}:${String(n % 60).padStart(2,'0')}`; }
function modelName(key) { return models.find(m => m.id === key)?.name || key; }

function updateReady() {
  const installed = models.find(m => m.id === selectedModel)?.installed;
  const isRecording = recorder?.state === 'recording';
  $('generate-button').disabled = !serviceOnline || busy || isRecording || !!pendingUpload || !reference || !$('transcript').value.trim() || !$('story-text').value.trim() || !installed;
  $('generate-button').querySelector('span').textContent=busy?'正在处理…':'生成故事音频';
  $('audio-file').disabled = !serviceOnline || busy || isRecording;
  $('reset-reference').disabled = !serviceOnline || busy || isRecording;
  $('record-button').disabled = busy || (!serviceOnline && !isRecording);
  $('retry-upload').disabled = !serviceOnline || busy || isRecording;
  $('pending-upload').hidden = !pendingUpload;
  $('generate-hint').textContent = !serviceOnline ? '本地服务未连接，请先启动声音工坊' : busy ? '正在处理，请稍等片刻' : isRecording ? '录音完成后再生成' : pendingUpload ? '刚才的录音尚未保存，请重新保存录音' : !reference ? '先录下你的声音，再开始创作' : !$('transcript').value.trim() ? '请填写录音里实际说的话' : !installed ? '先下载所选声音模型' : !$('story-text').value.trim() ? '写下一段想朗读的文字' : '准备好了，开始讲故事吧';
  $('clone-mode').disabled = selectedModel !== 'voxcpm2' || busy;
  document.querySelectorAll('.model-install').forEach(button => { button.disabled = !serviceOnline || busy || isRecording; });
}

function renderModels() {
  $('model-list').innerHTML = models.map(m => `<label class="model-option ${m.id === selectedModel ? 'selected' : ''}"><input type="radio" name="model" value="${m.id}" ${m.id === selectedModel ? 'checked' : ''}><div class="model-top"><span class="radio-dot"></span><span class="model-name">${m.name}</span><span class="model-tag">${m.tag}</span></div><p class="model-variant">${m.variant}</p><p class="model-description">${m.description}</p><div class="model-bottom"><span>约 ${m.size_gb} GB · 本机生成</span>${m.installed ? '<span class="installed-label">✓ 已就绪</span>' : `<button type="button" class="model-install" data-model="${m.id}">下载模型 ↓</button>`}</div></label>`).join('');
  document.querySelectorAll('input[name=model]').forEach(input => input.onchange = () => { selectedModel = input.value; renderModels(); saveDraft(); });
  document.querySelectorAll('.model-install').forEach(button => button.onclick = async event => {
    event.preventDefault();
    try {
      selectedModel = button.dataset.model;
      const job = await api('/api/models/install', jsonPost({model:selectedModel}));
      renderModels(); startJob(job);
    } catch (error) { notice(error.message); }
  });
  updateReady();
}

async function refreshStatus(status = null) {
  status ||= await api('/api/status');
  models = status.models;
  const {chip, memory_gb, free_disk_gb} = status.hardware;
  $('hardware').textContent = `${chip} · ${memory_gb} GB 内存 · ${free_disk_gb} GB 可用`;
  renderModels();
  if (!status.ffmpeg) notice('没有找到 FFmpeg，请先安装后再录音和导出。');
}

function idleWave() {
  const canvas = $('waveform'), ctx = canvas.getContext('2d');
  canvas.width = Math.round(canvas.clientWidth * devicePixelRatio); canvas.height = 72 * devicePixelRatio;
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const scale = devicePixelRatio, center = canvas.height / 2, bars = 46, step = canvas.width / bars;
  ctx.fillStyle = '#bac7a7';
  for(let i=0;i<bars;i++) {
    const height = (5 + Math.abs(Math.sin(i * 1.7)) * 17 * Math.sin(Math.PI * i / bars)) * scale;
    ctx.beginPath(); ctx.roundRect(i*step,center-height/2,3*scale,height,2*scale); ctx.fill();
  }
}

async function visualize(source, live = false) {
  if (animation) cancelAnimationFrame(animation);
  if (audioContext) { await audioContext.close().catch(()=>{}); audioContext = null; }
  audioContext = new (window.AudioContext || window.webkitAudioContext)();
  const canvas = $('waveform'), ctx = canvas.getContext('2d');
  canvas.width = Math.round(canvas.clientWidth * devicePixelRatio); canvas.height = 72 * devicePixelRatio;
  const bars = 46, step = canvas.width / bars, scale = devicePixelRatio;
  function paint(values, color) {
    ctx.clearRect(0,0,canvas.width,canvas.height); ctx.fillStyle=color;
    values.forEach((value,i) => { const height=Math.max(3,Math.min(60,value*60))*scale; ctx.beginPath();ctx.roundRect(i*step,(canvas.height-height)/2,3*scale,height,2*scale);ctx.fill(); });
  }
  if(live) {
    const analyser = audioContext.createAnalyser(); analyser.fftSize=256;
    audioContext.createMediaStreamSource(source).connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    function draw() { analyser.getByteFrequencyData(data); paint(Array.from({length:bars},(_,i)=>data[i+2]/255),'#b77b6e'); animation=requestAnimationFrame(draw); }
    draw();
  } else {
    try {
      const buffer = await audioContext.decodeAudioData(await source.arrayBuffer());
      const data = buffer.getChannelData(0), chunk=Math.max(1,Math.floor(data.length/bars));
      const values=Array.from({length:bars},(_,i)=> { let max=0;for(let j=i*chunk;j<Math.min((i+1)*chunk,data.length);j+=20)max=Math.max(max,Math.abs(data[j]));return max; });
      const peak=Math.max(...values,.02); paint(values.map(x=>x/peak*.85),'#8ca678');
    } catch { idleWave(); }
    await audioContext.close().catch(()=>{}); audioContext=null;
  }
}

async function uploadAudio(blob, filename, fromRecording) {
  pendingUpload={blob,filename,fromRecording};
  busy = true; updateReady(); $('record-status').textContent='正在整理录音…';
  try {
    const form = new FormData(); form.append('file',blob,filename);
    const next = await api('/api/references',{method:'POST',body:form});
    pendingUpload=null;
    const previous = reference;
    reference=next;
    if(!fromRecording) $('transcript').value='';
    showReference(); await visualize(blob); saveDraft();
    if(previous && previous.id!==next.id) await api(`/api/references/${previous.id}`,{method:'DELETE'}).catch(()=>{});
    if(reference.warnings.length) notice(reference.warnings.join(' '));
  } catch(error) {
    if(serviceOnline)pendingUpload=null;
    notice(error.message);
    $('record-status').textContent=pendingUpload?'录音保留在当前页面，等待重新保存':'重新录制，或上传一段清晰音频';
  }
  finally { busy=false;updateReady(); }
}
$('retry-upload').onclick=async()=>{if(pendingUpload){const {blob,filename,fromRecording}=pendingUpload;await uploadAudio(blob,filename,fromRecording);}};

function showReference() {
  $('reference-preview').hidden=!reference;
  $('reference-player').src=reference?.audio_url || '';
  $('reference-name').textContent=reference?.name || '';
  $('reference-info').textContent=reference ? `${reference.duration} 秒 · 单声道 · ${reference.sample_rate/1000} kHz · 请确认下面的原文与录音一致` : '';
  $('record-status').textContent=reference ? '声音已就绪，可以继续生成' : '一段声音，一个熟悉的你';
  $('record-timer').textContent=reference ? duration(reference.duration) : '00:00';
}

async function startRecording() {
  if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { notice('当前浏览器不支持录音。请用 Safari / Chrome 打开本地地址，或上传已有音频。');return; }
  try {
    $('record-button').disabled=true;
    recordingStream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
    const formats=['audio/webm;codecs=opus','audio/mp4','audio/webm','audio/ogg;codecs=opus'];
    const mimeType=formats.find(type=>MediaRecorder.isTypeSupported(type));
    recorder=new MediaRecorder(recordingStream,mimeType ? {mimeType} : {});
    const chunks=[];
    recorder.ondataavailable=event=>{if(event.data.size)chunks.push(event.data);};
    recorder.onstop=async()=>{
      clearInterval(timer); if(animation)cancelAnimationFrame(animation);
      recordingStream.getTracks().forEach(track=>track.stop());
      if(audioContext){await audioContext.close().catch(()=>{});audioContext=null;}
      $('record-panel').classList.remove('recording');
      $('record-button').querySelector('span').textContent='重新录音';
      const blob=new Blob(chunks,{type:recorder.mimeType});
      const ext=recorder.mimeType.includes('mp4')?'m4a':recorder.mimeType.includes('ogg')?'ogg':'webm';
      recorder=null;
      await uploadAudio(blob,`我的录音.${ext}`,true);
    };
    recorder.onerror=event=>{notice(`录音失败：${event.error?.message || '请重新尝试'}`);stopRecording();};
    recorder.start();recordingStart=Date.now();
    $('record-panel').classList.add('recording');
    $('record-button').querySelector('span').textContent='结束录音';
    $('record-status').textContent='正在录音，按平时讲故事的语气朗读';
    timer=setInterval(()=>{const seconds=(Date.now()-recordingStart)/1000;$('record-timer').textContent=duration(seconds);if(seconds>=44)stopRecording();},200);
    await visualize(recordingStream,true);updateReady();
  } catch(error) {
    recordingStream?.getTracks().forEach(track=>track.stop());
    notice(error.name==='NotAllowedError'?'麦克风权限未开启。请允许浏览器使用麦克风，也可以上传手机录音。':`无法开始录音：${error.message}`);
    updateReady();
  }
}
function stopRecording(){if(recorder?.state==='recording')recorder.stop();}
$('record-button').onclick=()=>recorder?.state==='recording'?stopRecording():startRecording();
$('audio-file').onchange=async()=>{const file=$('audio-file').files[0];if(file)await uploadAudio(file,file.name,false);$('audio-file').value='';};
$('reset-reference').onclick=async()=>{
  try{if(reference)await api(`/api/references/${reference.id}`,{method:'DELETE'});reference=null;showReference();idleWave();updateReady();saveDraft();}
  catch(error){notice(error.message);}
};

function startJob(job) {
  activeJob=job.id;busy=true;updateReady();
  $('notice').hidden=true;$('empty-output').hidden=true;$('finished-output').hidden=true;$('job-progress').hidden=false;
  $('job-message').textContent=job.message || '任务准备中…';$('elapsed-time').textContent='0 秒';$('job-detail').textContent='正在准备';$('progress-fill').style.width='3%';
  $('output-section').scrollIntoView({behavior:'smooth',block:'nearest'});
  $('job-log').textContent='';$('log-details').open=false;
  pollJob();
}
async function pollJob() {
  if(polling)return;polling=true;
  const id=activeJob;
  try {
    const job=await api(`/api/jobs/${id}`);
    $('job-message').textContent=job.message;
    $('elapsed-time').textContent=`${Math.round(job.elapsed || 0)} 秒`;
    $('job-detail').textContent=job.kind==='install'?'首次下载需要联网；完成后可以离线生成。':job.phase==='generating'?`${modelName(job.model)} · ${job.segment} / ${job.total_segments} 段`:'首次载入模型会稍慢一些';
    $('progress-fill').style.width=job.phase==='generating'?`${10+job.segment/job.total_segments*80}%`:job.phase==='exporting'?'95%':'7%';
    $('job-log').textContent=job.log || '';
    $('log-details').hidden=job.kind!=='install' && job.status!=='failed';
    if(['completed','failed','cancelled'].includes(job.status)) {
      busy=false;activeJob=null;$('job-progress').hidden=true;
      if(job.status==='completed' && job.kind==='generate')showOutput(job);
      else {$('empty-output').hidden=false;if(job.status==='failed')notice(`任务未完成：${job.message}`);else if(job.kind==='install'&&job.status==='completed')notice(`${modelName(job.model)} 已准备好，可以开始生成。`);}
      await refreshStatus();await refreshHistory();updateReady();
    }
  } catch(error) {notice(`连接本地服务失败，将自动重试。${error.message}`);}
  finally {polling=false;if(activeJob===id)setTimeout(pollJob,1500);}
}
$('cancel-button').onclick=async()=>{try{if(activeJob)await api(`/api/jobs/${activeJob}/cancel`,{method:'POST'});}catch(error){notice(error.message);}};
$('generate-button').onclick=async()=>{
  if($('generate-button').disabled)return;
  busy=true;updateReady();
  try {
    const job=await api('/api/generate',jsonPost({model:selectedModel,reference_id:reference.id,transcript:$('transcript').value,text:$('story-text').value,title:$('story-title').value.trim()||'我的故事',clone_mode:$('clone-mode').value,speed:Number($('speed').value),pause_ms:Number($('pause-ms').value),seed:Number($('seed').value),steps:Number($('steps').value)}));
    saveDraft();startJob(job);
  } catch(error){busy=false;updateReady();notice(error.message);}
};
function stats(job) {return `<span>音频时长 <b>${duration(job.duration)}</b></span><span>生成用时 <b>${job.elapsed} 秒</b></span><span>采样率 <b>${job.sample_rate/1000} kHz</b></span><span>RTF <b>${job.rtf}</b> <span title="生成用时除以音频时长，越小越快">ⓘ</span></span>`;}
function showOutput(job) {
  $('empty-output').hidden=true;$('finished-output').hidden=false;
  $('result-model').textContent=`${modelName(job.model)} · 本地生成`;$('result-title').textContent=job.title;
  $('output-player').src=job.mp3_url;$('download-mp3').href=job.mp3_url;$('download-wav').href=job.wav_url;
  $('result-stats').innerHTML=stats(job);
}
async function refreshHistory() {
  historyItems=await api('/api/jobs');
  const creations=historyItems.filter(job=>job.kind==='generate');
  $('history-count').textContent=creations.filter(job=>job.status==='completed').length;
  $('history-list').innerHTML=creations.length?creations.map(job=>`<article class="card history-item"><div class="result-heading"><div><p class="eyebrow">${escapeHTML(modelName(job.model))} · ${new Date(job.created_at*1000).toLocaleString('zh-CN')}</p><h3>${escapeHTML(job.title)}</h3></div>${job.status==='completed'?`<div class="download-links"><a class="primary-button" href="${job.mp3_url}" download>下载 MP3 ↓</a><a class="secondary-button" href="${job.wav_url}" download>WAV ↓</a></div>`:`<span class="small-tag">${escapeHTML(job.message)}</span>`}</div>${job.status==='completed'?`<audio src="${job.mp3_url}" controls preload="none"></audio><div class="result-stats">${stats(job)}</div>`:''}<div class="history-actions"><span class="field-note">${job.status==='completed'?'已保存在本机':'可以返回创作台重新生成'}</span><button class="text-button" data-delete="${job.id}">删除记录</button></div></article>`).join(''):'<div class="card history-empty">还没有故事音频。录下你的声音，开始第一个故事吧。</div>';
  document.querySelectorAll('[data-delete]').forEach(button=>button.onclick=async()=>{if(!confirm('删除这条记录及其生成的音频？此操作不会删除你的参考录音。'))return;try{await api(`/api/jobs/${button.dataset.delete}`,{method:'DELETE'});await refreshHistory();}catch(error){notice(error.message);}});
}
function switchView(view){
  const isStudio=view==='studio';$('studio-view').hidden=!isStudio;$('history-view').hidden=isStudio;
  $('studio-tab').classList.toggle('active',isStudio);$('history-tab').classList.toggle('active',!isStudio);
  $('studio-tab').setAttribute('aria-selected',String(isStudio));$('history-tab').setAttribute('aria-selected',String(!isStudio));
  if(!isStudio)refreshHistory().catch(error=>notice(error.message));
}
$('studio-tab').onclick=()=>switchView('studio');$('history-tab').onclick=()=>switchView('history');
$('sample-button').onclick=()=>{if($('story-text').value.trim()&&!confirm('用示例故事替换现在的文字？'))return;$('story-text').value=sample;$('story-title').value='小熊的灯';editorChanged();};
function editorChanged(){ $('character-count').textContent=$('story-text').value.length;updateReady();clearTimeout(draftTimer);draftTimer=setTimeout(saveDraft,300); }
['story-text','transcript','story-title'].forEach(id=>$(id).addEventListener('input',editorChanged));
$('speed').oninput=()=>{$('speed-value').textContent=`${Number($('speed').value).toFixed(2)}×`;saveDraft();};
function saveDraft(){try{localStorage.setItem('story-voice-draft',JSON.stringify({text:$('story-text').value,title:$('story-title').value,transcript:$('transcript').value,model:selectedModel,reference,speed:$('speed').value}));}catch{}}
window.addEventListener('beforeunload',event=>{saveDraft();if(pendingUpload){event.preventDefault();event.returnValue='';return;}recordingStream?.getTracks().forEach(track=>track.stop());});
window.addEventListener('resize',()=>{if(recorder?.state!=='recording')idleWave();});

async function init(){
  idleWave();
  try {
    const draft=JSON.parse(localStorage.getItem('story-voice-draft')||'null');
    if(draft){$('story-text').value=draft.text||'';$('story-title').value=draft.title||'';$('transcript').value=draft.transcript||'';selectedModel=draft.model||selectedModel;$('speed').value=draft.speed||1;$('speed-value').textContent=`${Number($('speed').value).toFixed(2)}×`;reference=draft.reference||null;}
    await refreshStatus();
    if(reference){try{reference=await api(`/api/references/${reference.id}`);showReference();}catch(error){if(error.status===404){reference=null;saveDraft();}else throw error;}}
    await refreshHistory();editorChanged();
    const running=historyItems.find(job=>['running','pending'].includes(job.status));
    if(running)startJob(running);
    else {const last=historyItems.find(job=>job.kind==='generate'&&job.status==='completed');if(last)showOutput(last);}
  } catch(error){notice(`本地服务连接失败：${error.message}`);}
}
async function checkService(){
  if(healthChecking)return;healthChecking=true;
  const wasOnline=serviceOnline;
  try {
    const status=await api('/api/status');
    if(!wasOnline){
      await refreshStatus(status);
      if(reference){try{reference=await api(`/api/references/${reference.id}`);if(!pendingUpload&&recorder?.state!=='recording')showReference();}catch(error){if(error.status===404){reference=null;showReference();saveDraft();}else throw error;}}
      await refreshHistory();
      const running=historyItems.find(job=>['running','pending'].includes(job.status));
      if(running&&!activeJob)startJob(running);
      else if(!activeJob){const last=historyItems.find(job=>job.kind==='generate'&&job.status==='completed');if(last)showOutput(last);}
      if($('notice-text').textContent.includes(offlineMessage))$('notice').hidden=true;
      updateReady();
    }
  } catch(error){if(wasOnline)notice(error.message);}
  finally{healthChecking=false;}
}
init();
setInterval(checkService,10000);
window.addEventListener('online',checkService);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkService();});
