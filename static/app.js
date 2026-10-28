'use strict';
const $ = id => document.getElementById(id);
const stateNames = {running:'В работе',awaiting_check:'Ждёт проверки',needs_fragments:'Нужны фрагменты',checked:'Порог достигнут',needs_review:'Нужна редактура',error:'Требует внимания'};
let selected = null, current = null, config = null, copiedHash = null, editHash = null, pollPending = false;
let toastTimer;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,6500); }
async function api(path,body) {
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Request':'1'},body:JSON.stringify(body)});
  const result=await response.json(); if(!response.ok) throw new Error(result.error||'Ошибка запроса'); return result;
}
function node(tag,text,className) { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; }
const browserOption=node('option','Автоматически через бесплатный сайт ZeroGPT (Edge)');browserOption.value='browser';$('detector').insertBefore(browserOption,$('detector').options[1]);
const browserPrivacy=node('p','В этом режиме текст статьи передаётся в ZeroGPT через форму сайта. Нужен Microsoft Edge. При CAPTCHA, лимите или изменении сайта приложение предложит перенести результат вручную. Сохраняя этот режим, вы разрешаете такую передачу.','small muted');browserPrivacy.id='browser-privacy';browserPrivacy.hidden=true;$('detector').parentElement.after(browserPrivacy);
async function refreshStatus() {
  config=await api('/api/status');
  const ready=config.model_ready&&config.images_ready!==false;
  $('connection').textContent=ready?'● Локальные модели готовы':'● Моделям нужна настройка'; $('connection').classList.toggle('ready',ready);
  const problems=[];
  if(!config.model_ready) problems.push('Текстовая модель '+config.settings.model+' не найдена.');
  if(config.images_ready===false) problems.push('Модель картинок ещё загружается или не установлена.');
  $('readiness').textContent=problems.length?problems.join(' ')+' Запустите «Установить модели.cmd», затем обновите страницу.':config.settings.detector!=='manual'?'Проверка включена: текст статьи будет передан в ZeroGPT.':'';
  $('readiness').classList.toggle('info',!problems.length);
  $('generate').disabled=config.busy;
}
async function history() {
  const items=await api('/api/articles'); $('history').replaceChildren();
  if(!items.length) $('history').append(node('p','Пока здесь чистый лист.','small muted'));
  for(const item of items) { const b=node('button',undefined,'history-item'+(item.id===selected?' active':'')); b.append(node('strong',item.title),node('span',stateNames[item.state]||item.state)); b.onclick=()=>openArticle(item.id); $('history').append(b); }
}
function newTopic(){selected=null;current=null;copiedHash=null;$('compose').hidden=false;$('article-view').hidden=true;history();refreshStatus().catch(e=>toast(e.message));$('topic').focus();}
async function openArticle(id){selected=id;copiedHash=null;$('compose').hidden=true;$('article-view').hidden=false;await refreshArticle();await history();}
function picture(image) { const figure=node('figure'); const img=node('img'); img.src='/media/'+current.id+'/'+image.filename;img.alt=image.caption;img.loading='lazy';figure.append(img,node('figcaption',image.caption));return figure; }
function renderArticle(a){
  current=a; $('article-title').textContent=a.title;$('article-status').textContent=stateNames[a.state]||a.state;$('article-version').textContent=a.version?'Версия '+a.version:'';$('download').href='/api/articles/'+a.id+'/export';
  const body=$('article-body');body.replaceChildren();
  if(!a.blocks.length){body.append(node('p','Здесь появится статья. Можно оставить страницу открытой: результат сохраняется автоматически.','muted'));}
  else{body.innerHTML=a.html;if(a.images.length){body.prepend(picture(a.images[0]));if(a.images[1]){const second=picture(a.images[1]);const children=[...body.children].filter(c=>c.tagName!=='FIGURE');const target=children[Math.floor(children.length/2)];if(target)target.after(second);else body.append(second);}}}
  $('article-error').hidden=!a.error;$('article-error').textContent=a.error||'';
  $('progress').replaceChildren();const stages=['Анализ','Инструменты','Создание','Ревью'];stages.forEach((s,i)=>$('progress').append(node('div',(i+1)+'. '+s,'progress-step'+(i+1<a.stage?' done':'')+(i+1===a.stage?' current':''))));
  const last=a.events.at(-1);const progress=node('div',undefined,'progress-text');if(a.state==='running')progress.append(node('span',undefined,'spinner'));progress.append(document.createTextNode(last?last.message:'Начинаем работу…'));$('progress').append(progress);
  const check=a.check;const card=$('score-card');card.classList.toggle('high',!!check&&check.score>10);card.replaceChildren(node('span',check?check.score.toLocaleString('ru-RU')+'%':'—','score-value'),node('p',check?'Оценка сервиса · версия '+check.version:'Текущая версия ещё не проверена'));
  if(check)card.append(node('p',check.provider,'muted'));
  $('copy-text').disabled=!a.blocks.length;$('edit-open').disabled=!a.blocks.length||a.busy;$('submit-report').disabled=!a.blocks.length||a.busy;$('images-retry').disabled=!a.blocks.length||a.busy;$('recheck').disabled=!a.blocks.length||a.busy;
  $('retry').hidden=!!a.blocks.length||a.state!=='error';$('retry').disabled=a.busy;
  $('detector-actions').hidden=!a.blocks.length;$('recheck').hidden=config?.settings.detector==='manual';$('recheck').textContent=config?.settings.detector==='browser'?'Проверить на бесплатном сайте автоматически':'Проверить через подключённый API';
  $('notes').replaceChildren();const notes=[...(a.review.notes||[]),...(a.review.warnings||[]),...(a.plan?.fact_cautions||[])];if(a.images.length<a.brief.image_count)notes.unshift('Иллюстрации ещё не готовы.');if(!notes.length)notes.push(a.blocks.length?'Редактор не оставил замечаний. Проверьте факты перед публикацией.':'Редактор приступит после создания текста.');for(const note of notes)$('notes').append(node('li',String(note)));
  $('events').replaceChildren();for(const e of a.events)$('events').append(node('li',e.message));
  $('versions').replaceChildren();for(const v of [...a.versions].reverse()){const d=node('details');d.append(node('summary','Версия '+v.version+' · '+v.reason));const r=a.reports.filter(x=>x.version===v.version);for(const report of r)d.append(node('p',report.score+'% · '+report.provider));d.append(node('pre',v.title+'\n\n'+v.blocks.map(b=>b.markdown).join('\n\n')));$('versions').append(d);}
}
async function refreshArticle(){if(!selected)return;const id=selected;const a=await api('/api/articles/'+id);if(selected===id)renderArticle(a);}
async function tick(){if(pollPending)return;pollPending=true;try{if(selected){const old=current?.state;await refreshArticle();if(current?.state!==old){await history();await refreshStatus();}}else await refreshStatus();}catch(e){toast(e.message);}finally{pollPending=false;}}
$('new').onclick=newTopic;$('back').onclick=newTopic;
document.querySelectorAll('[data-topic]').forEach(b=>b.onclick=()=>{$('topic').value=b.dataset.topic;$('topic').focus();});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('brief-form').onsubmit=async e=>{e.preventDefault();$('generate').disabled=true;try{const response=await api('/api/articles',{topic:$('topic').value,audience:$('audience').value,length:Number($('length').value),format:$('format').value,image_count:Number($('image-count').value),instructions:$('instructions').value,materials:$('materials').value});await openArticle(response.id);}catch(error){toast(error.message);}finally{if(!selected)$('generate').disabled=false;}};
$('copy-text').onclick=async()=>{try{await navigator.clipboard.writeText(current.detector_text);copiedHash=current.hash;$('copy-info').textContent='Скопирована версия '+current.version+'. Проверяйте именно этот текст.';toast('Текст скопирован. Вставьте его в ZeroGPT.');}catch(e){toast('Буфер обмена недоступен. Скачайте ZIP и откройте detector-text.txt.');}};
$('report-form').onsubmit=async e=>{e.preventDefault();const hash=copiedHash||current.hash;const fragments=$('fragments').value.split(/\n\s*\n/).map(s=>s.trim()).filter(Boolean);$('submit-report').disabled=true;try{await api('/api/articles/'+selected+'/report',{score:Number($('score').value),fragments,hash});$('score').value='';$('fragments').value='';await refreshArticle();toast('Результат принят. Следите за текущим этапом.');}catch(error){toast(error.message);$('submit-report').disabled=false;}};
async function operation(action){try{await api('/api/articles/'+selected+'/'+action,{});await refreshArticle();}catch(e){toast(e.message);}}
$('recheck').onclick=()=>operation('recheck');$('images-retry').onclick=()=>operation('images');$('retry').onclick=()=>operation('retry');
$('edit-open').onclick=()=>{editHash=current.hash;$('edit-title').value=current.title;$('edit-markdown').value=current.blocks.map(b=>b.markdown).join('\n\n');$('edit-dialog').showModal();};
$('edit-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/articles/'+selected+'/edit',{title:$('edit-title').value,markdown:$('edit-markdown').value,hash:editHash});$('edit-dialog').close();copiedHash=null;await refreshArticle();await history();toast('Новая версия сохранена. Пройдите проверку заново.');}catch(error){toast(error.message);}};
function credentials(){$('credentials').hidden=$('detector').value!=='zerogpt';$('browser-privacy').hidden=$('detector').value!=='browser';}
$('detector').onchange=credentials;
$('settings-open').onclick=async()=>{try{await refreshStatus();const s=config.settings;$('model').value=s.model;$('models').replaceChildren(...config.models.map(m=>{const o=node('option');o.value=m;return o;}));$('ollama-url').value=s.ollama_url;$('image-provider').value=s.image_provider;$('sd-url').value=s.sd_url;$('detector').value=s.detector;$('max-revisions').value=s.max_revisions;$('zerogpt-token').value='';$('zerogpt-key').value='';$('clear-keys').checked=false;credentials();$('settings-dialog').showModal();}catch(e){toast(e.message);}};
$('settings-form').onsubmit=async e=>{e.preventDefault();try{const s={model:$('model').value,ollama_url:$('ollama-url').value,image_provider:$('image-provider').value,sd_url:$('sd-url').value,detector:$('detector').value,max_revisions:Number($('max-revisions').value)};if($('zerogpt-token').value||$('clear-keys').checked)s.zerogpt_token=$('zerogpt-token').value;if($('zerogpt-key').value||$('clear-keys').checked)s.zerogpt_key=$('zerogpt-key').value;await api('/api/settings',s);$('settings-dialog').close();await refreshStatus();toast('Настройки сохранены.');}catch(error){toast(error.message);}};
Promise.all([refreshStatus(),history()]).then(()=>{const id=new URLSearchParams(location.search).get('article');if(id&&/^[a-f0-9]{32}$/.test(id))return openArticle(id);}).catch(e=>toast(e.message));
setInterval(tick,3500);
